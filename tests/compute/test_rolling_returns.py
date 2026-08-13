from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from fundlens.compute.rolling_returns import (
    compute_trailing_windows,
    summarize_rolling_returns,
)
from fundlens.db.models import BenchmarkHistory, BenchmarkId, NavHistory, NavSource
from fundlens.tax.asset_class import TaxAssetClass


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


def test_fund_cagr_exact_doubling_over_3_years(session: Session) -> None:
    today = date.today()
    three_years_ago = today - timedelta(days=1095)
    _add_nav(session, "111", three_years_ago, "100")
    _add_nav(session, "111", today, "200")
    session.commit()

    windows = compute_trailing_windows(
        session, "111", "Open Ended Schemes(Equity Scheme - Large Cap Fund)", TaxAssetClass.EQUITY
    )

    window_3y = next(w for w in windows if w.years == 3)
    expected = ((200 / 100) ** (1 / 3) - 1) * 100
    assert window_3y.fund_cagr_pct is not None
    assert abs(window_3y.fund_cagr_pct - expected) < 1e-9


def test_no_nav_at_all_is_flagged(session: Session) -> None:
    windows = compute_trailing_windows(
        session, "999", "Open Ended Schemes(Equity Scheme - Large Cap Fund)", TaxAssetClass.EQUITY
    )
    assert all(w.fund_cagr_pct is None and w.reason == "nav_unavailable" for w in windows)


def test_insufficient_history_when_scheme_too_young(session: Session) -> None:
    today = date.today()
    _add_nav(session, "111", today - timedelta(days=100), "100")
    _add_nav(session, "111", today, "110")
    session.commit()

    windows = compute_trailing_windows(
        session, "111", "Open Ended Schemes(Equity Scheme - Large Cap Fund)", TaxAssetClass.EQUITY
    )

    assert all(w.fund_cagr_pct is None and w.reason == "insufficient_history" for w in windows)


def test_debt_fund_has_no_category_benchmark(session: Session) -> None:
    today = date.today()
    _add_nav(session, "111", today - timedelta(days=1095), "100")
    _add_nav(session, "111", today, "130")
    session.commit()

    windows = compute_trailing_windows(
        session, "111", "Open Ended Schemes(Debt Scheme - Liquid Fund)", TaxAssetClass.DEBT
    )

    window_3y = next(w for w in windows if w.years == 3)
    assert window_3y.fund_cagr_pct is not None  # fund's own CAGR is still real
    assert window_3y.benchmark_cagr_pct is None
    assert window_3y.reason == "no_category_benchmark"
    assert window_3y.alpha_pct is None


def test_alpha_and_beat_benchmark_computed_when_both_available(session: Session) -> None:
    today = date.today()
    start = today - timedelta(days=1095)
    _add_nav(session, "111", start, "100")
    _add_nav(session, "111", today, "200")  # fund doubles
    _add_benchmark(session, BenchmarkId.NIFTY50_TRI, start, "100")
    _add_benchmark(session, BenchmarkId.NIFTY50_TRI, today, "150")  # benchmark up 50%
    session.commit()

    windows = compute_trailing_windows(
        session, "111", "Open Ended Schemes(Equity Scheme - Large Cap Fund)", TaxAssetClass.EQUITY
    )

    window_3y = next(w for w in windows if w.years == 3)
    assert window_3y.alpha_pct is not None
    assert window_3y.alpha_pct > 0  # fund beat benchmark
    assert window_3y.beat_benchmark is True


def test_summarize_computes_mean_alpha_and_consistency(session: Session) -> None:
    today = date.today()
    # One consistent NAV/benchmark history spanning 5 years: fund and
    # benchmark both start lower 5yr ago, both end at today's value.
    _add_nav(session, "111", today - timedelta(days=365 * 5), "80")
    _add_nav(session, "111", today - timedelta(days=365 * 3), "100")
    _add_nav(session, "111", today, "200")
    _add_benchmark(session, BenchmarkId.NIFTY50_TRI, today - timedelta(days=365 * 5), "90")
    _add_benchmark(session, BenchmarkId.NIFTY50_TRI, today - timedelta(days=365 * 3), "100")
    _add_benchmark(session, BenchmarkId.NIFTY50_TRI, today, "150")
    session.commit()

    windows = compute_trailing_windows(
        session, "111", "Open Ended Schemes(Equity Scheme - Large Cap Fund)", TaxAssetClass.EQUITY
    )
    summary = summarize_rolling_returns(windows)

    assert summary.windows_total == 2
    assert summary.windows_available == 2
    assert summary.mean_alpha_pct is not None
    assert summary.consistency_pct == 100.0  # fund beat benchmark in both windows
