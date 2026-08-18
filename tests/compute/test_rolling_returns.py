"""compute_rolling_returns tests. test_mean_alpha_and_consistency_hand_computed
is the explicit cross-check gate: an independently-written CAGR formula
(not calling into the module under test) computes the expected
mean_alpha_pct/consistency_pct for a 2-sample fixture, matching the
"hand-computed, independent formula" convention used for goal planning's
required-SIP test and the yearly report's XIRR tests.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from fundlens.compute.rolling_returns import compute_rolling_returns, summarize_for_rubric
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


def test_no_nav_at_all_is_flagged(session: Session) -> None:
    windows = compute_rolling_returns(
        session, "999", "Open Ended Schemes(Equity Scheme - Large Cap Fund)", TaxAssetClass.EQUITY,
        window_years=(1,),
    )
    assert windows[0].samples == []
    assert windows[0].reason == "nav_unavailable"


def test_insufficient_history_when_scheme_too_young(session: Session) -> None:
    today = date.today()
    _add_nav(session, "111", today, "100")
    session.commit()

    windows = compute_rolling_returns(
        session, "111", "Open Ended Schemes(Equity Scheme - Large Cap Fund)", TaxAssetClass.EQUITY,
        window_years=(1,),
    )

    assert windows[0].samples == []
    assert windows[0].reason == "insufficient_history"


def test_debt_fund_has_no_category_benchmark(session: Session) -> None:
    _add_nav(session, "111", date(2020, 1, 1), "100")
    _add_nav(session, "111", date(2021, 6, 1), "130")
    session.commit()

    windows = compute_rolling_returns(
        session, "111", "Open Ended Schemes(Debt Scheme - Liquid Fund)", TaxAssetClass.DEBT,
        window_years=(1,),
    )

    window = windows[0]
    assert window.samples != []  # fund's own CAGR is still real
    assert all(s.benchmark_cagr_pct is None and s.alpha_pct is None for s in window.samples)
    assert window.mean_alpha_pct is None
    assert window.reason == "no_category_benchmark"


def test_mean_alpha_and_consistency_hand_computed(session: Session) -> None:
    """1yr window, exactly 2 monthly-sampled end dates: 2021-01-01 (fund
    beats benchmark) and 2021-02-01 (fund loses to benchmark). Expected
    figures computed independently below, not via the module under test.
    """
    _add_nav(session, "111", date(2020, 1, 1), "100")
    _add_nav(session, "111", date(2020, 2, 1), "100")
    _add_nav(session, "111", date(2021, 1, 1), "120")
    _add_nav(session, "111", date(2021, 2, 1), "90")
    _add_benchmark(session, BenchmarkId.NIFTY50_TRI, date(2020, 1, 1), "100")
    _add_benchmark(session, BenchmarkId.NIFTY50_TRI, date(2020, 2, 1), "100")
    _add_benchmark(session, BenchmarkId.NIFTY50_TRI, date(2021, 1, 1), "110")
    _add_benchmark(session, BenchmarkId.NIFTY50_TRI, date(2021, 2, 1), "100")
    session.commit()

    windows = compute_rolling_returns(
        session, "111", "Open Ended Schemes(Equity Scheme - Large Cap Fund)", TaxAssetClass.EQUITY,
        window_years=(1,),
    )
    window = windows[0]

    assert [s.as_of_date for s in window.samples] == [date(2021, 1, 1), date(2021, 2, 1)]

    def _cagr(start_v: float, end_v: float, start_d: date, end_d: date) -> float:
        years = (end_d - start_d).days / 365.0
        return ((end_v / start_v) ** (1 / years) - 1) * 100

    alpha1 = _cagr(100, 120, date(2020, 1, 1), date(2021, 1, 1)) - _cagr(
        100, 110, date(2020, 1, 1), date(2021, 1, 1)
    )
    alpha2 = _cagr(100, 90, date(2020, 2, 1), date(2021, 2, 1)) - _cagr(
        100, 100, date(2020, 2, 1), date(2021, 2, 1)
    )
    expected_mean_alpha = (alpha1 + alpha2) / 2
    expected_consistency = 50.0  # beat in window 1, lost in window 2

    assert window.samples[0].beat_benchmark is True
    assert window.samples[1].beat_benchmark is False
    assert window.mean_alpha_pct is not None
    assert abs(window.mean_alpha_pct - expected_mean_alpha) < 1e-6
    assert window.consistency_pct == expected_consistency


def test_sample_count_scales_with_history_length(session: Session) -> None:
    """First NAV 2018-01-01, latest 2021-01-01: for a 1yr window, the
    *eligible* sampling range is [2019-01-01, 2021-01-01] (the first year
    can't complete a trailing 1yr window yet) -- 2 years at ~monthly
    cadence, roughly 25 samples. Range-checked, not an exact brittle count
    (leap years / month lengths make the precise figure implementation-
    sensitive)."""
    _add_nav(session, "111", date(2018, 1, 1), "100")
    _add_nav(session, "111", date(2021, 1, 1), "150")
    session.commit()

    windows = compute_rolling_returns(
        session, "111", "Open Ended Schemes(Equity Scheme - Large Cap Fund)", TaxAssetClass.EQUITY,
        window_years=(1,),
    )

    assert 20 <= len(windows[0].samples) <= 30


def test_summarize_for_rubric_pools_3yr_and_5yr_only(session: Session) -> None:
    _add_nav(session, "111", date(2015, 1, 1), "100")
    _add_nav(session, "111", date(2024, 1, 1), "300")
    session.commit()

    windows = compute_rolling_returns(
        session, "111", "Open Ended Schemes(Equity Scheme - Large Cap Fund)", TaxAssetClass.EQUITY,
        window_years=(3, 5),
    )
    summary = summarize_for_rubric(windows)

    assert summary.windows_total == 2
    assert {w.years for w in summary.windows} == {3, 5}
