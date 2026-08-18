"""True rolling trailing returns (PRD FR-3.3, phase P3, compute-engine.md
§5): monthly-sampled 1/3/5yr trailing CAGR for a scheme's own NAV, vs the
same window on its category-default benchmark, at every sample point --
not a single point-to-point calculation.

Upgrades the originally-shipped (P1.3) 2-point proxy documented in this
module's own prior version: a single 3yr and single 5yr window ending
today. That was a deliberate, documented simplification pending "monthly
rolling snapshots computed... across many overlapping windows" -- this is
that upgrade. Sample end dates step by calendar month
(`dateutil.relativedelta`, not `timedelta(days=30)`, to avoid drift over
decades of sampling) from the earliest date at least `years` after the
scheme's first NAV, to the latest available NAV date. Each scheme's full
NAV/benchmark series is bulk-fetched once and bisected per sample point
(same template as compute/valuation_history.py's `_nav_series_by_scheme` /
compute/benchmark.py's synthetic value source) -- daily NAV data over 20
years is ~240 samples per window length, cheap once bulk-fetched, not
240 individual queries.

This is scheme-level, NAV-based trailing CAGR -- not investor-position
XIRR. It measures the *fund's* own performance, independent of when the
specific investor happened to buy in, matching how standard fund fact
sheets report "3Y/5Y returns," and the more relevant number for a
hold/sell decision about the fund itself.

1yr is informational only (fund detail page) -- deliberately never fed
into the rubric (compute/verdict.py), which was designed around exactly
two components (3yr+5yr) sharing one "rolling_alpha" weight; folding in a
noisier, short-horizon window would need redesigning that weight
structure for a signal less suited to a long-horizon hold/sell judgment.
See verdict.py's own docstring and compute-engine.md §5 for the rubric
side of this decision.
"""

import bisect
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.benchmark.category_mapping import default_benchmark_for_category
from fundlens.db.models import BenchmarkHistory, BenchmarkId, NavHistory
from fundlens.tax.asset_class import TaxAssetClass

_DEFAULT_WINDOW_YEARS = (1, 3, 5)


@dataclass(frozen=True)
class RollingWindowSample:
    as_of_date: date
    fund_cagr_pct: float
    benchmark_cagr_pct: float | None
    alpha_pct: float | None
    beat_benchmark: bool | None


@dataclass(frozen=True)
class RollingReturnWindow:
    years: int
    samples: list[RollingWindowSample]
    mean_alpha_pct: float | None
    consistency_pct: float | None  # % of samples where beat_benchmark is True
    reason: str | None  # set only when samples is empty


@dataclass(frozen=True)
class RollingReturnSummary:
    """Pools mean_alpha_pct/consistency_pct across whichever window lengths
    were passed in -- used by compute/verdict.py to combine 3yr+5yr into
    the single rolling_alpha/consistency rubric components, exactly as the
    prior 2-point-proxy version did (only the per-window number quality
    changed, not this pooling logic)."""

    windows: list[RollingReturnWindow]
    mean_alpha_pct: float | None
    consistency_pct: float | None
    windows_available: int
    windows_total: int


_Series = tuple[list[date], list[Decimal]]


def _nav_series(session: Session, amfi_code: str) -> _Series:
    rows = session.execute(
        select(NavHistory.nav_date, NavHistory.nav)
        .where(NavHistory.amfi_code == amfi_code)
        .order_by(NavHistory.nav_date)
    ).all()
    return [r.nav_date for r in rows], [r.nav for r in rows]


def _benchmark_series(session: Session, benchmark_id: BenchmarkId) -> _Series:
    rows = session.execute(
        select(BenchmarkHistory.nav_date, BenchmarkHistory.value)
        .where(BenchmarkHistory.benchmark_id == benchmark_id)
        .order_by(BenchmarkHistory.nav_date)
    ).all()
    return [r.nav_date for r in rows], [r.value for r in rows]


def _on_or_before(series: _Series, on_date: date) -> Decimal | None:
    dates, values = series
    idx = bisect.bisect_right(dates, on_date) - 1
    return values[idx] if idx >= 0 else None


def _cagr_pct(start_value: Decimal, end_value: Decimal, days: int) -> float | None:
    if start_value <= 0 or days <= 0:
        return None
    years = days / 365.0
    return ((float(end_value) / float(start_value)) ** (1 / years) - 1) * 100


def _sample_end_dates(first_eligible_end: date, latest_date: date) -> list[date]:
    dates: list[date] = []
    d = first_eligible_end
    while d < latest_date:
        dates.append(d)
        d += relativedelta(months=1)
    dates.append(latest_date)
    return dates


def _compute_window(
    fund_series: _Series,
    benchmark_series: _Series | None,
    benchmark_reason: str | None,
    years: int,
) -> RollingReturnWindow:
    fund_dates, _ = fund_series
    if not fund_dates:
        return RollingReturnWindow(years, [], None, None, "nav_unavailable")

    first_nav_date, latest_nav_date = fund_dates[0], fund_dates[-1]
    first_eligible_end = first_nav_date + relativedelta(years=years)
    if first_eligible_end > latest_nav_date:
        return RollingReturnWindow(years, [], None, None, "insufficient_history")

    samples: list[RollingWindowSample] = []
    for end_date in _sample_end_dates(first_eligible_end, latest_nav_date):
        start_date = end_date - relativedelta(years=years)
        start_nav = _on_or_before(fund_series, start_date)
        end_nav = _on_or_before(fund_series, end_date)
        if start_nav is None or end_nav is None:
            continue
        fund_cagr = _cagr_pct(start_nav, end_nav, (end_date - start_date).days)
        if fund_cagr is None:
            continue

        benchmark_cagr: float | None = None
        if benchmark_series is not None:
            start_bm = _on_or_before(benchmark_series, start_date)
            end_bm = _on_or_before(benchmark_series, end_date)
            if start_bm is not None and end_bm is not None:
                benchmark_cagr = _cagr_pct(start_bm, end_bm, (end_date - start_date).days)

        alpha = fund_cagr - benchmark_cagr if benchmark_cagr is not None else None
        beat = alpha > 0 if alpha is not None else None
        samples.append(RollingWindowSample(end_date, fund_cagr, benchmark_cagr, alpha, beat))

    if not samples:
        empty_reason = benchmark_reason or "insufficient_history"
        return RollingReturnWindow(years, [], None, None, empty_reason)

    alphas = [s.alpha_pct for s in samples if s.alpha_pct is not None]
    mean_alpha = sum(alphas) / len(alphas) if alphas else None
    beat_count = sum(1 for s in samples if s.beat_benchmark)
    with_alpha = [s for s in samples if s.alpha_pct is not None]
    consistency = (beat_count / len(with_alpha) * 100) if with_alpha else None
    final_reason: str | None = benchmark_reason if mean_alpha is None else None

    return RollingReturnWindow(years, samples, mean_alpha, consistency, final_reason)


def compute_rolling_returns(
    session: Session,
    amfi_code: str,
    category_raw: str,
    tax_asset_class: TaxAssetClass,
    window_years: tuple[int, ...] = _DEFAULT_WINDOW_YEARS,
) -> list[RollingReturnWindow]:
    fund_series = _nav_series(session, amfi_code)

    benchmark_id = default_benchmark_for_category(category_raw, tax_asset_class)
    benchmark_series: _Series | None = None
    benchmark_reason: str | None = None
    if benchmark_id is None:
        benchmark_reason = "no_category_benchmark"
    else:
        candidate = _benchmark_series(session, benchmark_id)
        if candidate[0]:
            benchmark_series = candidate
        else:
            benchmark_reason = "benchmark_history_unavailable"

    return [
        _compute_window(fund_series, benchmark_series, benchmark_reason, years)
        for years in window_years
    ]


def summarize_for_rubric(windows: list[RollingReturnWindow]) -> RollingReturnSummary:
    usable = [w for w in windows if w.mean_alpha_pct is not None]
    mean_alpha = (
        sum(w.mean_alpha_pct for w in usable if w.mean_alpha_pct is not None) / len(usable)
        if usable
        else None
    )
    consistencies = [w.consistency_pct for w in usable if w.consistency_pct is not None]
    consistency = sum(consistencies) / len(consistencies) if consistencies else None
    return RollingReturnSummary(
        windows=windows,
        mean_alpha_pct=mean_alpha,
        consistency_pct=consistency,
        windows_available=len(usable),
        windows_total=len(windows),
    )
