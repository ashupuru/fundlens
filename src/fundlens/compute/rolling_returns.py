"""Rolling trailing return (compute-engine.md §5 rubric components 1-2:
"rolling alpha" and "consistency"): 3yr/5yr point-to-point CAGR for a
scheme's own NAV, vs the same window on its category-default benchmark
(P1.1 benchmark/category_mapping.py).

**Deliberately simplified from the originally-envisioned "mean(rolling-
window fund XIRR - benchmark XIRR)"**, not a silent shortcut:
- This is scheme-level, NAV-based trailing CAGR — not investor-position
  XIRR. It measures the *fund's* own performance, independent of when the
  specific investor happened to buy in, which is what standard fund fact
  sheets report as "3Y/5Y returns" and is the more relevant number for a
  hold/sell decision about the fund itself (XIRR would conflate fund skill
  with the investor's personal entry-timing luck).
- A true moving-window methodology needs monthly rolling snapshots
  computed and stored over time, or expensive on-the-fly recomputation
  across many overlapping windows. This computes a single window ending
  today for each of 3yr/5yr, not a moving sequence — deferred, documented
  here rather than silently degraded.

"Consistency" is therefore also a simplified 2-point proxy: what fraction
of the available windows (0, 1, or 2, since only 3yr and 5yr are
attempted) beat the benchmark — not a true rolling win-rate percentage.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.benchmark.category_mapping import default_benchmark_for_category
from fundlens.db.models import BenchmarkHistory, BenchmarkId, NavHistory
from fundlens.tax.asset_class import TaxAssetClass

_WINDOW_YEARS = (3, 5)


@dataclass(frozen=True)
class TrailingWindow:
    years: int
    fund_cagr_pct: float | None
    benchmark_cagr_pct: float | None
    alpha_pct: float | None
    beat_benchmark: bool | None
    reason: str | None  # set whenever fund_cagr_pct or benchmark_cagr_pct is None


@dataclass(frozen=True)
class RollingReturnSummary:
    windows: list[TrailingWindow]
    mean_alpha_pct: float | None
    consistency_pct: float | None
    windows_available: int
    windows_total: int


def _nav_on_or_before(session: Session, amfi_code: str, on_date: date) -> Decimal | None:
    return session.execute(
        select(NavHistory.nav)
        .where(NavHistory.amfi_code == amfi_code, NavHistory.nav_date <= on_date)
        .order_by(NavHistory.nav_date.desc())
        .limit(1)
    ).scalar_one_or_none()


def _latest_nav_with_date(session: Session, amfi_code: str) -> tuple[Decimal, date] | None:
    row = session.execute(
        select(NavHistory.nav, NavHistory.nav_date)
        .where(NavHistory.amfi_code == amfi_code)
        .order_by(NavHistory.nav_date.desc())
        .limit(1)
    ).first()
    return (row.nav, row.nav_date) if row is not None else None


def _benchmark_on_or_before(
    session: Session, benchmark_id: BenchmarkId, on_date: date
) -> Decimal | None:
    return session.execute(
        select(BenchmarkHistory.value)
        .where(BenchmarkHistory.benchmark_id == benchmark_id, BenchmarkHistory.nav_date <= on_date)
        .order_by(BenchmarkHistory.nav_date.desc())
        .limit(1)
    ).scalar_one_or_none()


def _latest_benchmark_with_date(
    session: Session, benchmark_id: BenchmarkId
) -> tuple[Decimal, date] | None:
    row = session.execute(
        select(BenchmarkHistory.value, BenchmarkHistory.nav_date)
        .where(BenchmarkHistory.benchmark_id == benchmark_id)
        .order_by(BenchmarkHistory.nav_date.desc())
        .limit(1)
    ).first()
    return (row.value, row.nav_date) if row is not None else None


def _cagr_pct(start_value: Decimal, end_value: Decimal, days: int) -> float | None:
    if start_value <= 0 or days <= 0:
        return None
    years = days / 365.0
    return ((float(end_value) / float(start_value)) ** (1 / years) - 1) * 100


def compute_trailing_windows(
    session: Session, amfi_code: str, category_raw: str, tax_asset_class: TaxAssetClass
) -> list[TrailingWindow]:
    latest_nav = _latest_nav_with_date(session, amfi_code)
    benchmark_id = default_benchmark_for_category(category_raw, tax_asset_class)

    windows: list[TrailingWindow] = []
    for years in _WINDOW_YEARS:
        if latest_nav is None:
            windows.append(TrailingWindow(years, None, None, None, None, "nav_unavailable"))
            continue

        end_nav, end_date = latest_nav
        start_date = end_date - timedelta(days=365 * years)
        start_nav = _nav_on_or_before(session, amfi_code, start_date)
        if start_nav is None:
            windows.append(TrailingWindow(years, None, None, None, None, "insufficient_history"))
            continue

        actual_days = (end_date - start_date).days
        fund_cagr = _cagr_pct(start_nav, end_nav, actual_days)

        benchmark_cagr: float | None = None
        reason: str | None = None
        if benchmark_id is None:
            reason = "no_category_benchmark"
        else:
            latest_bm = _latest_benchmark_with_date(session, benchmark_id)
            if latest_bm is None:
                reason = "benchmark_history_unavailable"
            else:
                end_bm, bm_end_date = latest_bm
                start_bm = _benchmark_on_or_before(session, benchmark_id, start_date)
                if start_bm is None:
                    reason = "benchmark_history_unavailable"
                else:
                    bm_days = (bm_end_date - start_date).days
                    benchmark_cagr = _cagr_pct(start_bm, end_bm, bm_days)

        alpha = (
            fund_cagr - benchmark_cagr
            if fund_cagr is not None and benchmark_cagr is not None
            else None
        )
        beat = alpha > 0 if alpha is not None else None
        windows.append(TrailingWindow(years, fund_cagr, benchmark_cagr, alpha, beat, reason))

    return windows


def summarize_rolling_returns(windows: list[TrailingWindow]) -> RollingReturnSummary:
    usable = [w for w in windows if w.alpha_pct is not None]
    alphas = [w.alpha_pct for w in usable if w.alpha_pct is not None]
    mean_alpha = sum(alphas) / len(alphas) if alphas else None
    beat_count = sum(1 for w in usable if w.beat_benchmark)
    consistency = (beat_count / len(usable) * 100) if usable else None
    return RollingReturnSummary(
        windows=windows,
        mean_alpha_pct=mean_alpha,
        consistency_pct=consistency,
        windows_available=len(usable),
        windows_total=len(windows),
    )
