"""compute_risk_adjusted_metrics tests. test_sharpe_sortino_hand_computed
is the explicit cross-check gate: Sharpe/Sortino/max-drawdown are computed
independently (plain Python + stdlib statistics, not calling the module
under test) for a 13-point monthly NAV fixture and asserted within a tight
tolerance, matching the convention used for goal planning / yearly report
/ rolling returns.
"""

import statistics
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from fundlens.compute.risk_metrics import compute_risk_adjusted_metrics
from fundlens.db.models import BenchmarkHistory, BenchmarkId, NavHistory, NavSource
from fundlens.tax.asset_class import TaxAssetClass

_TOLERANCE = 1e-6
_RISK_FREE_PCT = Decimal("7")

# 13 monthly points, 2020-01-01 -> 2021-01-01 (exactly a 1yr window).
_MONTHLY_DATES = [
    date(2020, 1, 1), date(2020, 2, 1), date(2020, 3, 1), date(2020, 4, 1),
    date(2020, 5, 1), date(2020, 6, 1), date(2020, 7, 1), date(2020, 8, 1),
    date(2020, 9, 1), date(2020, 10, 1), date(2020, 11, 1), date(2020, 12, 1),
    date(2021, 1, 1),
]
_MONTHLY_NAVS = [100, 105, 100, 110, 105, 115, 110, 120, 115, 125, 120, 130, 125]


def _add_nav(session: Session, amfi_code: str, nav_date: date, nav: str) -> None:
    session.add(
        NavHistory(
            amfi_code=amfi_code, nav_date=nav_date, nav=Decimal(nav), source=NavSource.MANUAL
        )
    )


def _add_benchmark(session: Session, benchmark_id: BenchmarkId, nav_date: date, value: str) -> None:
    session.add(
        BenchmarkHistory(
            benchmark_id=benchmark_id, nav_date=nav_date, value=Decimal(value), proxy=True,
            proxy_note="test",
        )
    )


def _seed_monthly_fixture(session: Session, amfi_code: str = "111") -> None:
    for d, nav in zip(_MONTHLY_DATES, _MONTHLY_NAVS, strict=True):
        _add_nav(session, amfi_code, d, str(nav))
    session.commit()


def test_no_nav_at_all_is_flagged(session: Session) -> None:
    windows = compute_risk_adjusted_metrics(
        session, "999", "Open Ended Schemes(Equity Scheme - Large Cap Fund)", TaxAssetClass.EQUITY,
        _RISK_FREE_PCT, window_years=(1,),
    )
    assert windows[0].fund is None
    assert windows[0].reason == "nav_unavailable"


def test_insufficient_history_when_scheme_too_young(session: Session) -> None:
    _add_nav(session, "111", date(2020, 1, 1), "100")
    _add_nav(session, "111", date(2020, 6, 1), "110")
    session.commit()

    windows = compute_risk_adjusted_metrics(
        session, "111", "Open Ended Schemes(Equity Scheme - Large Cap Fund)", TaxAssetClass.EQUITY,
        _RISK_FREE_PCT, window_years=(1,),
    )

    assert windows[0].fund is None
    assert windows[0].reason == "insufficient_history"


def test_debt_fund_has_no_category_benchmark(session: Session) -> None:
    _seed_monthly_fixture(session)

    windows = compute_risk_adjusted_metrics(
        session, "111", "Open Ended Schemes(Debt Scheme - Liquid Fund)", TaxAssetClass.DEBT,
        _RISK_FREE_PCT, window_years=(1,),
    )

    window = windows[0]
    assert window.fund is not None  # fund's own stats are still real
    assert window.benchmark is None
    assert window.reason == "no_category_benchmark"


def test_sharpe_sortino_hand_computed(session: Session) -> None:
    _seed_monthly_fixture(session)

    windows = compute_risk_adjusted_metrics(
        session, "111", "Open Ended Schemes(Equity Scheme - Large Cap Fund)", TaxAssetClass.EQUITY,
        _RISK_FREE_PCT, window_years=(1,),
    )
    fund = windows[0].fund
    assert fund is not None

    returns = [(b - a) / a for a, b in zip(_MONTHLY_NAVS, _MONTHLY_NAVS[1:], strict=False)]
    monthly_rf = float(_RISK_FREE_PCT) / 100 / 12
    excess = [r - monthly_rf for r in returns]
    mean_excess = statistics.fmean(excess)
    stdev = statistics.stdev(excess)
    expected_sharpe = (mean_excess * 12) / (stdev * 12**0.5)

    downside = [min(0.0, e) ** 2 for e in excess]
    downside_dev = statistics.fmean(downside) ** 0.5
    expected_sortino = (mean_excess * 12) / (downside_dev * 12**0.5)

    peak = _MONTHLY_NAVS[0]
    worst = 0.0
    for v in _MONTHLY_NAVS:
        peak = max(peak, v)
        worst = min(worst, (v - peak) / peak)
    expected_max_drawdown = worst * 100

    assert fund.sharpe is not None and abs(fund.sharpe - expected_sharpe) < _TOLERANCE
    assert fund.sortino is not None and abs(fund.sortino - expected_sortino) < _TOLERANCE
    assert fund.max_drawdown_pct is not None
    assert abs(fund.max_drawdown_pct - expected_max_drawdown) < _TOLERANCE


def test_max_drawdown_obvious_peak_trough_partial_recovery(session: Session) -> None:
    """NAV: 100 -> 200 (peak) -> 100 (50% drawdown from peak) -> 150
    (partial recovery, doesn't create a worse drawdown). Expected max
    drawdown: exactly -50%."""
    dates = [
        date(2020, 1, 1), date(2020, 4, 1), date(2020, 7, 1), date(2020, 10, 1), date(2021, 1, 1)
    ]
    navs = ["100", "200", "100", "150", "160"]
    for d, nav in zip(dates, navs, strict=True):
        _add_nav(session, "111", d, nav)
    session.commit()

    windows = compute_risk_adjusted_metrics(
        session, "111", "Open Ended Schemes(Equity Scheme - Large Cap Fund)", TaxAssetClass.EQUITY,
        _RISK_FREE_PCT, window_years=(1,),
    )

    fund = windows[0].fund
    assert fund is not None
    assert fund.max_drawdown_pct is not None
    assert abs(fund.max_drawdown_pct - (-50.0)) < _TOLERANCE


def test_benchmark_stats_computed_when_available(session: Session) -> None:
    _seed_monthly_fixture(session)
    for d, nav in zip(_MONTHLY_DATES, _MONTHLY_NAVS, strict=True):
        _add_benchmark(session, BenchmarkId.NIFTY50_TRI, d, str(nav - 10))
    session.commit()

    windows = compute_risk_adjusted_metrics(
        session, "111", "Open Ended Schemes(Equity Scheme - Large Cap Fund)", TaxAssetClass.EQUITY,
        _RISK_FREE_PCT, window_years=(1,),
    )

    window = windows[0]
    assert window.benchmark is not None
    assert window.reason is None
