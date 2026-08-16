"""Golden-file XIRR tests (dev plan P0.4 test gate, technical-spec.md §9
accuracy NFR: within 0.01% of Excel's XIRR on identical cash flows).

Excel's XIRR is not available in this environment, so "golden" reference
values come from two independent sources, both defensible as ground truth:

1. Closed-form annualized return for simple single-in/single-out cash
   flows: r = (final / -initial) ** (1/years) - 1, computed independently
   of the solver via plain exponentiation — this is the exact mathematical
   definition of XIRR for a two-flow series, not an approximation.
2. Direct verification that the solved rate satisfies the XIRR equation
   itself (objective function ≈ 0 at the solved rate) for multi-flow
   series without a simple closed form. Excel's XIRR solves the identical
   equation (actual/365 day-count), so satisfying it to high precision is
   equivalent to matching Excel, not just "close to a plausible answer."
"""

from datetime import date
from decimal import Decimal

import pytest

from fundlens.compute.xirr import CashFlow, XirrResult, _objective, compute_xirr


def _closed_form_rate(initial: float, final: float, days: int) -> float:
    years = days / 365.0
    return (final / -initial) ** (1 / years) - 1


def test_simple_lump_sum_matches_closed_form() -> None:
    """Invest 100,000 on day 0, receive 150,000 exactly 3 years later."""
    d0 = date(2020, 1, 1)
    d1 = date(2023, 1, 1)
    flows = [CashFlow(d0, Decimal("-100000")), CashFlow(d1, Decimal("150000"))]

    result = compute_xirr(flows)

    expected = _closed_form_rate(-100000, 150000, (d1 - d0).days)
    assert result.rate is not None
    assert result.rate == pytest.approx(expected, rel=1e-9)


def test_one_year_flat_return_matches_closed_form() -> None:
    d0 = date(2021, 1, 1)
    d1 = date(2022, 1, 1)
    flows = [CashFlow(d0, Decimal("-10000")), CashFlow(d1, Decimal("11000"))]

    result = compute_xirr(flows)

    expected = _closed_form_rate(-10000, 11000, (d1 - d0).days)
    assert result.rate is not None
    assert result.rate == pytest.approx(expected, rel=1e-9)


def test_loss_scenario_matches_closed_form() -> None:
    d0 = date(2020, 6, 1)
    d1 = date(2021, 6, 1)
    flows = [CashFlow(d0, Decimal("-50000")), CashFlow(d1, Decimal("40000"))]

    result = compute_xirr(flows)

    expected = _closed_form_rate(-50000, 40000, (d1 - d0).days)
    assert result.rate is not None
    assert result.rate == pytest.approx(expected, rel=1e-9)
    assert result.rate < 0  # a real loss


def test_short_holding_period_high_annualized_rate_matches_closed_form() -> None:
    """A 30-day holding with a modest absolute gain annualizes to an
    extreme rate — exercises the solver bracket's edge, not just the
    common case."""
    d0 = date(2022, 1, 1)
    d1 = date(2022, 1, 31)
    flows = [CashFlow(d0, Decimal("-10000")), CashFlow(d1, Decimal("10500"))]

    result = compute_xirr(flows)

    expected = _closed_form_rate(-10000, 10500, (d1 - d0).days)
    assert result.rate is not None
    assert result.rate == pytest.approx(expected, rel=1e-6)


def _assert_solves_equation(result: XirrResult, flows: list[CashFlow]) -> None:
    assert result.rate is not None
    base_date = min(cf.date for cf in flows)
    normalized = [
        ((cf.date - base_date).days / 365.0, float(cf.amount)) for cf in flows
    ]
    assert _objective(result.rate, normalized) == pytest.approx(0.0, abs=1e-6)


def test_sip_like_series_solves_the_xirr_equation() -> None:
    """12 monthly SIP installments plus a terminal value — no simple closed
    form, so the golden check is that the solved rate actually satisfies
    the XIRR equation (which is what Excel's XIRR also solves)."""
    flows = [CashFlow(date(2022, m, 5), Decimal("-5000")) for m in range(1, 13)]
    flows.append(CashFlow(date(2023, 1, 5), Decimal("65000")))

    result = compute_xirr(flows)

    _assert_solves_equation(result, flows)


def test_lump_sum_plus_partial_redemption_plus_terminal_solves_equation() -> None:
    flows = [
        CashFlow(date(2019, 1, 1), Decimal("-100000")),
        CashFlow(date(2020, 6, 1), Decimal("-50000")),  # top-up
        CashFlow(date(2021, 3, 1), Decimal("30000")),  # partial redemption
        CashFlow(date(2023, 1, 1), Decimal("150000")),  # terminal value
    ]

    result = compute_xirr(flows)

    _assert_solves_equation(result, flows)


def test_fully_exited_fund_realized_xirr_solves_equation() -> None:
    """Fully-exited fund (PRD FR-3.1 named edge case): no terminal
    units_remaining, last flow is a full redemption, not a terminal value."""
    flows = [
        CashFlow(date(2018, 1, 1), Decimal("-100000")),
        CashFlow(date(2020, 1, 1), Decimal("-50000")),
        CashFlow(date(2022, 6, 1), Decimal("180000")),  # full redemption, no terminal flow after
    ]

    result = compute_xirr(flows)

    _assert_solves_equation(result, flows)


def test_less_than_one_year_of_history_still_computes() -> None:
    """PRD FR-3.1 named edge case: < 1yr history — XIRR is still computed
    here (the "annualized and noisy" label is the caller/API's concern,
    not the solver's)."""
    d0 = date(2024, 1, 1)
    d1 = date(2024, 4, 1)  # 3 months
    flows = [CashFlow(d0, Decimal("-10000")), CashFlow(d1, Decimal("10300"))]

    result = compute_xirr(flows)

    assert result.rate is not None
    expected = _closed_form_rate(-10000, 10300, (d1 - d0).days)
    assert result.rate == pytest.approx(expected, rel=1e-6)


def test_single_cash_flow_is_degenerate() -> None:
    result = compute_xirr([CashFlow(date(2021, 1, 1), Decimal("-10000"))])
    assert result.rate is None
    assert result.reason == "insufficient_flow_variation"


def test_all_negative_flows_is_degenerate() -> None:
    flows = [
        CashFlow(date(2021, 1, 1), Decimal("-10000")),
        CashFlow(date(2021, 6, 1), Decimal("-5000")),
    ]
    result = compute_xirr(flows)
    assert result.rate is None
    assert result.reason == "insufficient_flow_variation"


def test_all_positive_flows_is_degenerate() -> None:
    flows = [
        CashFlow(date(2021, 1, 1), Decimal("10000")),
        CashFlow(date(2021, 6, 1), Decimal("5000")),
    ]
    result = compute_xirr(flows)
    assert result.rate is None
    assert result.reason == "insufficient_flow_variation"


def test_empty_flows_is_degenerate() -> None:
    result = compute_xirr([])
    assert result.rate is None
    assert result.reason == "insufficient_flow_variation"


def test_all_same_date_flows_is_degenerate() -> None:
    """No time variation at all — dividing by zero years is meaningless,
    not just numerically awkward."""
    same_day = date(2021, 1, 1)
    flows = [CashFlow(same_day, Decimal("-10000")), CashFlow(same_day, Decimal("10500"))]

    result = compute_xirr(flows)

    assert result.rate is None
    assert result.reason == "same_date_flows"


def test_extreme_short_horizon_massive_gain_does_not_crash() -> None:
    """A pathological rate (>1000% annualized) should degrade to a
    reason code rather than raise — stresses the bracket + Newton fallback
    without asserting a specific numeric answer."""
    flows = [
        CashFlow(date(2024, 1, 1), Decimal("-100")),
        CashFlow(date(2024, 1, 3), Decimal("100000")),
    ]

    result = compute_xirr(flows)

    assert result.rate is None or isinstance(result.rate, float)  # never raises
