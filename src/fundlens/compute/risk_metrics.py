"""Risk-adjusted metrics (PRD FR-3.4, phase P3, compute-engine.md §12):
Sharpe ratio, Sortino ratio, and max drawdown for a scheme's own NAV,
compared against its category benchmark, for trailing 1/3/5yr windows.

"vs category peers" (the PRD's literal wording) is reinterpreted as "vs
category benchmark": this app only ingests NAV history for held schemes
(market_data/service.py::refresh_all_held_schemes), not for other funds in
the same category, so literal peer-fund comparison isn't possible -- the
same underlying data gap as the blocked P2.2 fund overlap. Every other
"vs category" comparison already shipped here substitutes the benchmark
for literal peers (rolling_returns.py's consistency check, the verdict
rubric's rolling alpha), so this follows that established precedent.

A single trailing window per length (ending today), not a rolling
multi-sample series like rolling_returns.py -- Sharpe/Sortino/max-drawdown
are conventionally reported as "3Y Sharpe," a single number for the
period, not a win-rate across many overlapping windows.

Sharpe/Sortino are computed from ~monthly-sampled returns (same
`relativedelta(months=1)` cadence rolling_returns.py established, and the
conventional period for these ratios); max drawdown from the window's
*daily* series (path-dependent -- monthly sampling would understate real
intra-month declines, and a single O(n) pass is cheap regardless of
resolution). Purely informational (fund detail page) -- never fed into
the verdict rubric, unlike rolling returns.
"""

import bisect
import statistics
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.benchmark.category_mapping import default_benchmark_for_category
from fundlens.db.models import BenchmarkHistory, BenchmarkId, NavHistory
from fundlens.tax.asset_class import TaxAssetClass

_WINDOW_YEARS = (1, 3, 5)
_PERIODS_PER_YEAR = 12


@dataclass(frozen=True)
class SeriesRiskStats:
    sharpe: float | None
    sortino: float | None
    max_drawdown_pct: float | None


@dataclass(frozen=True)
class RiskAdjustedWindow:
    years: int
    fund: SeriesRiskStats | None
    benchmark: SeriesRiskStats | None
    reason: str | None  # set only when fund is None


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


def _window_slice(series: _Series, start: date, end: date) -> _Series:
    dates, values = series
    lo = bisect.bisect_left(dates, start)
    hi = bisect.bisect_right(dates, end)
    return dates[lo:hi], values[lo:hi]


def _monthly_returns(series: _Series, start: date, end: date) -> list[float]:
    points: list[Decimal] = []
    d = start
    while d < end:
        v = _on_or_before(series, d)
        if v is not None:
            points.append(v)
        d += relativedelta(months=1)
    v_end = _on_or_before(series, end)
    if v_end is not None:
        points.append(v_end)

    returns: list[float] = []
    for prev, cur in zip(points, points[1:], strict=False):
        if prev > 0:
            returns.append(float((cur - prev) / prev))
    return returns


def _max_drawdown_pct(series: _Series, start: date, end: date) -> float | None:
    _, values = _window_slice(series, start, end)
    if not values:
        return None
    peak = values[0]
    worst = Decimal(0)
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            drawdown = (v - peak) / peak
            worst = min(worst, drawdown)
    return float(worst) * 100


def _series_risk_stats(
    series: _Series, start: date, end: date, risk_free_rate_pct: Decimal
) -> SeriesRiskStats | None:
    returns = _monthly_returns(series, start, end)
    if len(returns) < 2:
        return None

    monthly_rf = float(risk_free_rate_pct) / 100 / _PERIODS_PER_YEAR
    excess = [r - monthly_rf for r in returns]
    mean_excess = statistics.fmean(excess)
    stdev = statistics.stdev(excess)

    annualized_stdev = stdev * _PERIODS_PER_YEAR**0.5
    sharpe = (mean_excess * _PERIODS_PER_YEAR) / annualized_stdev if annualized_stdev > 0 else None

    downside = [min(0.0, e) ** 2 for e in excess]
    downside_dev = statistics.fmean(downside) ** 0.5
    annualized_downside_dev = downside_dev * _PERIODS_PER_YEAR**0.5
    sortino = (
        (mean_excess * _PERIODS_PER_YEAR) / annualized_downside_dev
        if annualized_downside_dev > 0
        else None
    )

    max_dd = _max_drawdown_pct(series, start, end)

    return SeriesRiskStats(sharpe=sharpe, sortino=sortino, max_drawdown_pct=max_dd)


def _compute_window(
    fund_series: _Series,
    benchmark_series: _Series | None,
    benchmark_reason: str | None,
    years: int,
    risk_free_rate_pct: Decimal,
) -> RiskAdjustedWindow:
    fund_dates, _ = fund_series
    if not fund_dates:
        return RiskAdjustedWindow(years, None, None, "nav_unavailable")

    end = fund_dates[-1]
    start = end - relativedelta(years=years)
    if fund_dates[0] > start:
        return RiskAdjustedWindow(years, None, None, "insufficient_history")

    fund_stats = _series_risk_stats(fund_series, start, end, risk_free_rate_pct)
    if fund_stats is None:
        return RiskAdjustedWindow(years, None, None, "insufficient_history")

    benchmark_stats = (
        _series_risk_stats(benchmark_series, start, end, risk_free_rate_pct)
        if benchmark_series is not None
        else None
    )

    return RiskAdjustedWindow(
        years, fund_stats, benchmark_stats, None if benchmark_stats else benchmark_reason
    )


def compute_risk_adjusted_metrics(
    session: Session,
    amfi_code: str,
    category_raw: str,
    tax_asset_class: TaxAssetClass,
    risk_free_rate_pct: Decimal,
    window_years: tuple[int, ...] = _WINDOW_YEARS,
) -> list[RiskAdjustedWindow]:
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
        _compute_window(fund_series, benchmark_series, benchmark_reason, years, risk_free_rate_pct)
        for years in window_years
    ]
