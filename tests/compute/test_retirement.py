"""compute_retirement_projection tests.

The correctness gate here is hand-computed year-by-year arithmetic, worked
out independently of the module (not by calling into it), plus degenerate
cases whose answer is provable by inspection: zero return/inflation must
give exactly linear growth, and the growing-annuity PV is cross-checked
against an independently-written summation loop.
"""

from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from fundlens.assumptions.service import GoalAssumptions
from fundlens.compute.retirement import (
    ACCUMULATION,
    EXCLUDED_BEYOND_LIFE_EXPECTANCY,
    REASON_ALREADY_RETIRED,
    REASON_INCOMPLETE,
    REASON_INVALID_AGES,
    RETIREMENT,
    compute_retirement_projection,
    required_corpus_for_withdrawals,
)
from fundlens.db.models import Persona, RiskProfile


def _profile(**overrides: object) -> RiskProfile:
    defaults: dict[str, object] = {
        "id": 1,
        "persona": Persona.CUSTOM,
        "target_equity_pct": Decimal("100"),
        "target_debt_pct": Decimal("0"),
        "current_age": 50,
        "retirement_age": 60,
        "life_expectancy": 85,
        "annual_investment_paise": 10_000_000,  # Rs 1,00,000
        "annual_investment_step_up_pct": Decimal("0"),
        "monthly_withdrawal_today_paise": 10_000_000,  # Rs 1,00,000/month
        "post_retirement_return_pct": Decimal("8"),
    }
    defaults.update(overrides)
    return RiskProfile(**defaults)  # type: ignore[arg-type]


def _assumptions(
    inflation: str = "7", equity: str = "10", debt: str = "7"
) -> GoalAssumptions:
    return GoalAssumptions(
        inflation_pct=Decimal(inflation),
        expected_equity_return_pct=Decimal(equity),
        expected_debt_return_pct=Decimal(debt),
    )


def _seed_corpus(session: Session, monkeypatch: pytest.MonkeyPatch, paise: int) -> None:
    """The engine reads the real portfolio value via compute_portfolio_metrics;
    these tests are about the projection arithmetic, not portfolio pricing,
    so the starting corpus is pinned directly."""
    from fundlens.compute import retirement as retirement_module
    from fundlens.compute.metrics import PortfolioMetrics

    monkeypatch.setattr(
        retirement_module,
        "compute_portfolio_metrics",
        lambda _session: PortfolioMetrics(
            xirr=None,
            xirr_reason=None,
            invested_paise=paise,
            received_paise=0,
            current_value_paise=paise,
            positions_missing_nav=0,
        ),
    )


def test_accumulation_matches_hand_computed_year_by_year(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Start Rs 10,00,000; 10% return; Rs 1,00,000 invested at end of each
    # year; 3 accumulation years (age 57 -> 59, retiring at 60).
    #
    # Hand-computed, returns on the OPENING balance only:
    #   yr1: open 10,00,000 + return 1,00,000 + inv 1,00,000 = 12,00,000
    #   yr2: open 12,00,000 + return 1,20,000 + inv 1,00,000 = 14,20,000
    #   yr3: open 14,20,000 + return 1,42,000 + inv 1,00,000 = 16,62,000
    _seed_corpus(session, monkeypatch, 100_000_000)
    profile = _profile(current_age=57, retirement_age=60, annual_investment_paise=10_000_000)

    result = compute_retirement_projection(session, profile, _assumptions())

    accumulation = [y for y in result.years if y.phase == ACCUMULATION]
    assert len(accumulation) == 3
    assert [y.closing_corpus_paise for y in accumulation] == [
        120_000_000,
        142_000_000,
        166_200_000,
    ]
    assert [y.return_paise for y in accumulation] == [10_000_000, 12_000_000, 14_200_000]
    assert result.corpus_at_retirement_paise == 166_200_000
    assert result.years_to_retirement == 3


def test_step_up_increases_each_year_s_investment(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Rs 1,00,000 stepped up 10%/yr -> 1,00,000 / 1,10,000 / 1,21,000.
    _seed_corpus(session, monkeypatch, 0)
    profile = _profile(
        current_age=57,
        retirement_age=60,
        annual_investment_paise=10_000_000,
        annual_investment_step_up_pct=Decimal("10"),
    )

    result = compute_retirement_projection(session, profile, _assumptions())

    assert [y.investment_paise for y in result.years if y.phase == ACCUMULATION] == [
        10_000_000,
        11_000_000,
        12_100_000,
    ]


def test_zero_return_and_zero_inflation_gives_exactly_linear_growth(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With no return and no step-up, the corpus can only grow by the
    # investment: start + n * investment, exactly.
    _seed_corpus(session, monkeypatch, 50_000_000)
    profile = _profile(
        current_age=55,
        retirement_age=60,
        annual_investment_paise=10_000_000,
        # Rs 10,000/month -> Rs 1.2L/yr, comfortably below the Rs 10L corpus,
        # so the decline below is genuinely linear rather than an immediate
        # depletion-and-clamp.
        monthly_withdrawal_today_paise=1_000_000,
        post_retirement_return_pct=Decimal("0"),
    )

    result = compute_retirement_projection(
        session, profile, _assumptions(inflation="0", equity="0", debt="0")
    )

    assert result.corpus_at_retirement_paise == 50_000_000 + 5 * 10_000_000

    # ...and with zero inflation the withdrawal never escalates, so the
    # corpus falls by exactly the same amount every retirement year.
    retirement_years = [y for y in result.years if y.phase == RETIREMENT]
    annual_withdrawal = 1_000_000 * 12
    assert {y.withdrawal_paise for y in retirement_years} == {annual_withdrawal}
    for year in retirement_years[:-1]:  # last year may clamp at depletion
        assert year.opening_corpus_paise - year.closing_corpus_paise == annual_withdrawal


def test_withdrawal_escalates_at_inflation_from_the_retirement_year_figure(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_corpus(session, monkeypatch, 10_000_000_000)
    profile = _profile(current_age=59, retirement_age=60, life_expectancy=64)

    result = compute_retirement_projection(session, profile, _assumptions(inflation="7"))

    retirement_years = [y for y in result.years if y.phase == RETIREMENT]
    first_annual = Decimal(retirement_years[0].withdrawal_paise)
    for n, year in enumerate(retirement_years):
        expected = first_annual * Decimal("1.07") ** n
        # Round-trip through int paise; allow the rounding of one paisa per year.
        assert abs(Decimal(year.withdrawal_paise) - expected) <= n + 1


def test_first_year_withdrawal_is_todays_money_inflated_to_retirement(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Rs 1,00,000/month today, 7% inflation, retiring in 10 years:
    # 1,00,000 * 1.07^10 = Rs 1,96,715.14 -- the spec's own worked example.
    _seed_corpus(session, monkeypatch, 10_000_000_000)
    profile = _profile(
        current_age=50, retirement_age=60, monthly_withdrawal_today_paise=10_000_000
    )

    result = compute_retirement_projection(session, profile, _assumptions(inflation="7"))

    assert result.monthly_withdrawal_today_paise == 10_000_000
    expected_nominal = Decimal("10000000") * Decimal("1.07") ** 10
    assert result.first_year_monthly_withdrawal_paise is not None
    assert abs(result.first_year_monthly_withdrawal_paise - expected_nominal) < 100


def test_depletion_detected_and_projection_stops_there(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Tiny corpus, large withdrawal -> must run out almost immediately, and
    # must never report a negative corpus.
    _seed_corpus(session, monkeypatch, 10_000_000)
    profile = _profile(
        current_age=59,
        retirement_age=60,
        life_expectancy=85,
        annual_investment_paise=0,
        monthly_withdrawal_today_paise=10_000_000,
    )

    result = compute_retirement_projection(session, profile, _assumptions())

    assert result.depletion_age is not None
    assert result.depletion_age < 85
    assert result.terminal_corpus_paise == 0
    assert all(y.closing_corpus_paise >= 0 for y in result.years)
    # Stopped at depletion rather than projecting pointless all-zero years.
    assert result.years[-1].age == result.depletion_age


def test_well_funded_corpus_survives_with_no_depletion_age(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_corpus(session, monkeypatch, 100_000_000_000)  # Rs 10 Cr
    profile = _profile(
        current_age=59, retirement_age=60, life_expectancy=85,
        monthly_withdrawal_today_paise=10_000_000,
    )

    result = compute_retirement_projection(session, profile, _assumptions())

    assert result.depletion_age is None
    assert result.terminal_corpus_paise is not None
    assert result.terminal_corpus_paise > 0
    assert result.years[-1].age == 84  # through the year before life expectancy


def test_required_corpus_matches_independent_summation() -> None:
    """Growing-annuity PV cross-checked against a plainly-written
    year-by-year discounting loop -- deliberately not the closed form the
    module uses."""
    first_year = Decimal("1200000")
    return_pct = Decimal("8")
    inflation_pct = Decimal("7")
    n = 25

    expected = Decimal(0)
    withdrawal = first_year
    for year in range(1, n + 1):
        expected += withdrawal / (1 + return_pct / 100) ** year
        withdrawal *= 1 + inflation_pct / 100

    actual = required_corpus_for_withdrawals(first_year, return_pct, inflation_pct, n)

    assert abs(actual - expected) < Decimal("0.01")


def test_required_corpus_handles_return_equal_to_inflation_singularity() -> None:
    """r == g makes the closed form divide by zero. With returns exactly
    matching inflation every withdrawal has present value W/(1+r), so the
    total must be exactly n * W/(1+r) -- provable by inspection, and a
    perfectly plausible user input."""
    first_year = Decimal("1200000")
    rate = Decimal("7")
    n = 25

    actual = required_corpus_for_withdrawals(first_year, rate, rate, n)

    expected = n * first_year / (1 + rate / 100)
    assert abs(actual - expected) < Decimal("0.01")


def test_required_corpus_is_zero_for_no_retirement_years() -> None:
    assert required_corpus_for_withdrawals(Decimal("1200000"), Decimal("8"), Decimal("7"), 0) == 0


def test_readiness_and_surplus_derive_from_projected_vs_required(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_corpus(session, monkeypatch, 100_000_000_000)
    profile = _profile(current_age=59, retirement_age=60)

    result = compute_retirement_projection(session, profile, _assumptions())

    assert result.corpus_at_retirement_paise is not None
    assert result.required_corpus_at_retirement_paise is not None
    assert result.surplus_or_shortfall_paise == (
        result.corpus_at_retirement_paise - result.required_corpus_at_retirement_paise
    )
    assert result.readiness_pct is not None
    expected_readiness = (
        result.corpus_at_retirement_paise / result.required_corpus_at_retirement_paise * 100
    )
    assert abs(result.readiness_pct - expected_readiness) < 0.01


def test_post_retirement_return_falls_back_to_blended_when_unset(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_corpus(session, monkeypatch, 10_000_000_000)
    profile = _profile(post_retirement_return_pct=None)

    result = compute_retirement_projection(session, profile, _assumptions(equity="10"))

    assert result.blended_return_pct == Decimal("10")
    assert result.post_retirement_return_pct == Decimal("10")


def test_blended_return_uses_target_allocation(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 60/40 with equity 10% and debt 5% -> 0.6*10 + 0.4*5 = 8%.
    _seed_corpus(session, monkeypatch, 10_000_000_000)
    profile = _profile(target_equity_pct=Decimal("60"), target_debt_pct=Decimal("40"))

    result = compute_retirement_projection(
        session, profile, _assumptions(equity="10", debt="5")
    )

    assert result.blended_return_pct == Decimal("8.0")


@pytest.mark.parametrize(
    "missing_field",
    [
        "current_age",
        "retirement_age",
        "life_expectancy",
        "annual_investment_paise",
        "monthly_withdrawal_today_paise",
    ],
)
def test_incomplete_profile_reports_reason_and_no_fabricated_numbers(
    session: Session, monkeypatch: pytest.MonkeyPatch, missing_field: str
) -> None:
    _seed_corpus(session, monkeypatch, 10_000_000_000)
    profile = _profile(**{missing_field: None})

    result = compute_retirement_projection(session, profile, _assumptions())

    assert result.reason == REASON_INCOMPLETE
    assert result.years == []
    assert result.corpus_at_retirement_paise is None
    assert result.readiness_pct is None


def _add_goal(
    session: Session,
    name: str,
    rupees: int,
    years_from_now: int,
    inflation_override: Decimal | None = None,
    include_in_retirement: bool = True,
) -> None:
    from datetime import date, timedelta

    from fundlens.db.models import Goal

    session.add(
        Goal(
            name=name,
            target_amount_paise=rupees * 100,
            # +2 days of slack so integer-year flooring lands on the intended
            # year regardless of leap days.
            target_date=date.today() + timedelta(days=int(years_from_now * 365) + 2),
            inflation_pct_override=inflation_override,
            include_in_retirement_projection=include_in_retirement,
        )
    )
    session.commit()


def test_goal_opted_out_is_not_drawn_from_the_corpus(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case this exists for: a goal literally tracking "retirement"
    duplicates the projection's own income stream, so counting it as a
    lump-sum spend too would double-count."""
    _seed_corpus(session, monkeypatch, 10_000_000_000)
    _add_goal(
        session, "Retirement", rupees=30_000_000, years_from_now=4,
        include_in_retirement=False,
    )
    profile = _profile(current_age=51, retirement_age=60)

    result = compute_retirement_projection(session, profile, _assumptions())

    assert result.events == []
    assert result.total_lump_sum_paise == 0
    assert all(y.lump_sum_paise == 0 for y in result.years)


def test_goal_becomes_a_lump_sum_withdrawal_at_the_right_age(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Rs 10,00,000 goal 3 years out, 0% inflation everywhere -> lands at age
    # 53 (current 50) at exactly its today-cost, and the corpus that year
    # drops by exactly that amount beyond the usual investment.
    _seed_corpus(session, monkeypatch, 100_000_000)
    _add_goal(session, "Education", rupees=1_000_000, years_from_now=3)
    profile = _profile(
        current_age=50, retirement_age=60, annual_investment_paise=0,
        monthly_withdrawal_today_paise=1_000_000,
    )

    result = compute_retirement_projection(
        session, profile, _assumptions(inflation="0", equity="0", debt="0")
    )

    event = result.events[0]
    assert event.name == "Education"
    assert event.age == 53
    assert event.inflated_cost_paise == 100_000_000  # 0% inflation -> unchanged
    assert event.excluded_reason is None

    by_age = {y.age: y for y in result.years}
    assert by_age[53].lump_sum_paise == 100_000_000
    assert by_age[52].lump_sum_paise == 0
    # No return, no investment -> the corpus falls by exactly the event.
    assert by_age[53].closing_corpus_paise == by_age[53].opening_corpus_paise - 100_000_000


def test_goal_cost_inflates_at_its_own_override_rate(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Rs 25L education 4 years out at 8% (vs the 7% global rate):
    # 25,00,000 * 1.08^4 = Rs 34,012,24.6... -> hand-computed below.
    _seed_corpus(session, monkeypatch, 10_000_000_000)
    _add_goal(
        session, "Education", rupees=2_500_000, years_from_now=4,
        inflation_override=Decimal("8"),
    )
    profile = _profile(current_age=51, retirement_age=60)

    result = compute_retirement_projection(session, profile, _assumptions(inflation="7"))

    event = result.events[0]
    assert event.inflation_pct_used == Decimal("8")
    expected = Decimal("250000000") * Decimal("1.08") ** 4
    assert event.inflated_cost_paise is not None
    assert abs(event.inflated_cost_paise - expected) < 1


def test_goal_without_override_uses_the_global_inflation_rate(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_corpus(session, monkeypatch, 10_000_000_000)
    _add_goal(session, "Marriage", rupees=3_000_000, years_from_now=5)
    profile = _profile(current_age=51, retirement_age=60)

    result = compute_retirement_projection(session, profile, _assumptions(inflation="7"))

    event = result.events[0]
    assert event.inflation_pct_used == Decimal("7")
    expected = Decimal("300000000") * Decimal("1.07") ** 5
    assert event.inflated_cost_paise is not None
    assert abs(event.inflated_cost_paise - expected) < 1


def test_zero_override_means_a_fixed_nominal_cost(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_corpus(session, monkeypatch, 10_000_000_000)
    _add_goal(
        session, "Loan payoff", rupees=1_000_000, years_from_now=5,
        inflation_override=Decimal("0"),
    )
    profile = _profile(current_age=51, retirement_age=60)

    result = compute_retirement_projection(session, profile, _assumptions(inflation="7"))

    assert result.events[0].inflated_cost_paise == 100_000_000  # unchanged by inflation


def test_post_retirement_event_raises_the_required_corpus(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A life event after retirement is a claim on the same corpus as the
    income stream, so it must raise the required figure -- not just show up
    in the year table."""
    _seed_corpus(session, monkeypatch, 10_000_000_000)
    profile = _profile(current_age=51, retirement_age=60, life_expectancy=85)

    without = compute_retirement_projection(session, profile, _assumptions())

    _add_goal(session, "Late-life travel", rupees=5_000_000, years_from_now=15)  # age 66
    with_event = compute_retirement_projection(session, profile, _assumptions())

    assert with_event.required_corpus_at_retirement_paise is not None
    assert without.required_corpus_at_retirement_paise is not None
    assert (
        with_event.required_corpus_at_retirement_paise
        > without.required_corpus_at_retirement_paise
    )


def test_pre_retirement_event_lowers_corpus_at_retirement_not_required_corpus(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_corpus(session, monkeypatch, 10_000_000_000)
    profile = _profile(current_age=51, retirement_age=60, life_expectancy=85)

    without = compute_retirement_projection(session, profile, _assumptions())

    _add_goal(session, "Education", rupees=2_500_000, years_from_now=4)  # age 55
    with_event = compute_retirement_projection(session, profile, _assumptions())

    assert with_event.corpus_at_retirement_paise is not None
    assert without.corpus_at_retirement_paise is not None
    assert with_event.corpus_at_retirement_paise < without.corpus_at_retirement_paise
    # A pre-retirement event isn't funded from the retirement corpus, so the
    # required figure is untouched.
    assert (
        with_event.required_corpus_at_retirement_paise
        == without.required_corpus_at_retirement_paise
    )


def test_event_larger_than_the_corpus_is_reported_as_a_shortfall_never_negative(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_corpus(session, monkeypatch, 10_000_000)  # Rs 1L
    _add_goal(session, "Impossible house", rupees=50_000_000, years_from_now=2)  # Rs 5 Cr
    profile = _profile(
        current_age=50, retirement_age=60, annual_investment_paise=0,
        monthly_withdrawal_today_paise=1_000_000,
    )

    result = compute_retirement_projection(
        session, profile, _assumptions(inflation="0", equity="0", debt="0")
    )

    by_age = {y.age: y for y in result.years}
    assert by_age[52].lump_sum_shortfall_paise > 0
    assert by_age[52].closing_corpus_paise == 0
    assert all(y.closing_corpus_paise >= 0 for y in result.years)
    assert result.total_lump_sum_shortfall_paise is not None
    assert result.total_lump_sum_shortfall_paise > 0


def test_goal_beyond_life_expectancy_is_excluded_with_a_reason(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_corpus(session, monkeypatch, 10_000_000_000)
    _add_goal(session, "Way out there", rupees=1_000_000, years_from_now=40)
    profile = _profile(current_age=51, retirement_age=60, life_expectancy=85)

    result = compute_retirement_projection(session, profile, _assumptions())

    event = result.events[0]
    assert event.excluded_reason == EXCLUDED_BEYOND_LIFE_EXPECTANCY
    assert event.inflated_cost_paise is None
    assert all(y.lump_sum_paise == 0 for y in result.years)


def test_multiple_events_in_one_year_are_summed(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_corpus(session, monkeypatch, 10_000_000_000)
    _add_goal(session, "A", rupees=1_000_000, years_from_now=3, inflation_override=Decimal("0"))
    _add_goal(session, "B", rupees=2_000_000, years_from_now=3, inflation_override=Decimal("0"))
    profile = _profile(current_age=50, retirement_age=60)

    result = compute_retirement_projection(session, profile, _assumptions())

    by_age = {y.age: y for y in result.years}
    assert by_age[53].lump_sum_paise == 300_000_000  # Rs 10L + Rs 20L
    assert result.total_lump_sum_paise == 300_000_000


def test_current_age_at_or_past_retirement_age_is_reported_not_projected(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_corpus(session, monkeypatch, 10_000_000_000)
    profile = _profile(current_age=62, retirement_age=60)

    result = compute_retirement_projection(session, profile, _assumptions())

    assert result.reason == REASON_ALREADY_RETIRED
    assert result.years == []


def test_life_expectancy_not_after_retirement_is_reported_not_projected(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_corpus(session, monkeypatch, 10_000_000_000)
    profile = _profile(retirement_age=60, life_expectancy=58)

    result = compute_retirement_projection(session, profile, _assumptions())

    assert result.reason == REASON_INVALID_AGES
    assert result.years == []


def _scenarios(
    session: Session, profile: RiskProfile, assumptions: GoalAssumptions
) -> dict[str, object]:
    from fundlens.compute.retirement import compute_retirement_scenarios

    return {
        s.key: s
        for s in compute_retirement_scenarios(
            session, profile, assumptions, Decimal("2"), Decimal("1")
        )
    }


def test_scenarios_apply_deltas_symmetrically_around_base(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Base blended return 10%, inflation 7%. With +/-2 return and +/-1
    # inflation: conservative 8%/8%, base 10%/7%, optimistic 12%/6%.
    _seed_corpus(session, monkeypatch, 10_000_000_000)
    profile = _profile(current_age=51, retirement_age=60, post_retirement_return_pct=None)

    by_key = _scenarios(session, profile, _assumptions(inflation="7", equity="10", debt="7"))

    cons = by_key["conservative"].projection  # type: ignore[attr-defined]
    base = by_key["base"].projection  # type: ignore[attr-defined]
    opt = by_key["optimistic"].projection  # type: ignore[attr-defined]

    assert (cons.blended_return_pct, cons.inflation_pct) == (Decimal("8"), Decimal("8"))
    assert (base.blended_return_pct, base.inflation_pct) == (Decimal("10"), Decimal("7"))
    assert (opt.blended_return_pct, opt.inflation_pct) == (Decimal("12"), Decimal("6"))


def test_base_scenario_is_identical_to_the_plain_projection(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The base case must not drift from what the Retirement page's own
    projection endpoint shows -- same inputs, same engine, zero deltas."""
    _seed_corpus(session, monkeypatch, 10_000_000_000)
    profile = _profile(current_age=51, retirement_age=60)
    assumptions = _assumptions()

    plain = compute_retirement_projection(session, profile, assumptions)
    base = _scenarios(session, profile, assumptions)["base"].projection  # type: ignore[attr-defined]

    assert base.corpus_at_retirement_paise == plain.corpus_at_retirement_paise
    assert base.required_corpus_at_retirement_paise == plain.required_corpus_at_retirement_paise
    assert base.depletion_age == plain.depletion_age


def test_conservative_is_worse_and_optimistic_better_on_every_headline(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_corpus(session, monkeypatch, 10_000_000_000)
    profile = _profile(current_age=51, retirement_age=60, post_retirement_return_pct=None)

    by_key = _scenarios(session, profile, _assumptions())
    cons = by_key["conservative"].projection  # type: ignore[attr-defined]
    base = by_key["base"].projection  # type: ignore[attr-defined]
    opt = by_key["optimistic"].projection  # type: ignore[attr-defined]

    # Lower returns -> smaller corpus; higher inflation -> bigger requirement.
    assert cons.corpus_at_retirement_paise < base.corpus_at_retirement_paise
    assert base.corpus_at_retirement_paise < opt.corpus_at_retirement_paise
    assert cons.required_corpus_at_retirement_paise > base.required_corpus_at_retirement_paise
    assert base.required_corpus_at_retirement_paise > opt.required_corpus_at_retirement_paise
    assert cons.readiness_pct < base.readiness_pct < opt.readiness_pct


def test_scenario_inflation_delta_also_shifts_a_per_goal_override(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An explicit 8% education override becomes 9% in the conservative case
    # and 7% in the optimistic one -- it moves with the scenario rather than
    # staying pinned while everything around it changes.
    _seed_corpus(session, monkeypatch, 10_000_000_000)
    _add_goal(
        session, "Education", rupees=2_500_000, years_from_now=4,
        inflation_override=Decimal("8"),
    )
    profile = _profile(current_age=51, retirement_age=60)

    by_key = _scenarios(session, profile, _assumptions())

    assert by_key["conservative"].projection.events[0].inflation_pct_used == Decimal("9")  # type: ignore[attr-defined]
    assert by_key["base"].projection.events[0].inflation_pct_used == Decimal("8")  # type: ignore[attr-defined]
    assert by_key["optimistic"].projection.events[0].inflation_pct_used == Decimal("7")  # type: ignore[attr-defined]


def test_scenarios_return_incomplete_reason_without_a_retirement_plan(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_corpus(session, monkeypatch, 10_000_000_000)
    profile = _profile(current_age=None)

    by_key = _scenarios(session, profile, _assumptions())

    assert all(
        s.projection.reason == REASON_INCOMPLETE  # type: ignore[attr-defined]
        for s in by_key.values()
    )


def _with_investment(profile: RiskProfile, annual_paise: int) -> RiskProfile:
    fields = {
        c.name: getattr(profile, c.name)
        for c in profile.__table__.columns  # type: ignore[attr-defined]
    }
    fields["annual_investment_paise"] = annual_paise
    return RiskProfile(**fields)


def test_required_investment_round_trips_through_the_forward_simulation(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The strongest available check on the reverse algebra: solve for the
    contribution, feed it back into the *forward* loop, and the corpus at
    retirement must land on the required figure. If the rearrangement were
    wrong in any term, this wouldn't close."""
    # Rs 50L: deliberately well short of the requirement, so the solved
    # contribution is genuinely positive and the round-trip has teeth. (A
    # corpus that already covers the requirement makes the answer 0, which
    # proves nothing about the algebra.)
    _seed_corpus(session, monkeypatch, 500_000_000)
    profile = _profile(
        current_age=51,
        retirement_age=60,
        annual_investment_paise=60_000_000,
        annual_investment_step_up_pct=Decimal("10"),
    )
    assumptions = _assumptions()

    solved = compute_retirement_projection(session, profile, assumptions)
    assert solved.required_annual_investment_paise is not None

    replayed = compute_retirement_projection(
        session, _with_investment(profile, solved.required_annual_investment_paise), assumptions
    )

    assert replayed.corpus_at_retirement_paise is not None
    assert replayed.required_corpus_at_retirement_paise is not None
    # Within a rupee, i.e. pure paise rounding of the solved contribution.
    assert (
        abs(replayed.corpus_at_retirement_paise - replayed.required_corpus_at_retirement_paise)
        < 100
    )


def test_required_investment_round_trips_with_lump_sum_events(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same round-trip, but with pre-retirement events draining the corpus --
    the solve has to fund those on top of the target."""
    _seed_corpus(session, monkeypatch, 500_000_000)
    _add_goal(session, "Education", rupees=2_500_000, years_from_now=4)
    _add_goal(session, "Marriage", rupees=3_000_000, years_from_now=7)
    profile = _profile(
        current_age=51, retirement_age=60, annual_investment_step_up_pct=Decimal("10")
    )
    assumptions = _assumptions()

    solved = compute_retirement_projection(session, profile, assumptions)
    assert solved.required_annual_investment_paise is not None

    replayed = compute_retirement_projection(
        session, _with_investment(profile, solved.required_annual_investment_paise), assumptions
    )

    assert replayed.total_lump_sum_shortfall_paise == 0  # every event funded
    assert (
        abs(
            (replayed.corpus_at_retirement_paise or 0)
            - (replayed.required_corpus_at_retirement_paise or 0)
        )
        < 100
    )


def test_required_investment_round_trips_when_step_up_equals_return(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """r == g is the annuity-factor singularity, and an entirely ordinary
    plan (a 10% step-up alongside a 10% return)."""
    _seed_corpus(session, monkeypatch, 500_000_000)
    profile = _profile(
        current_age=51, retirement_age=60, annual_investment_step_up_pct=Decimal("10")
    )
    assumptions = _assumptions(equity="10", debt="10")  # blended 10% == step-up

    solved = compute_retirement_projection(session, profile, assumptions)
    assert solved.required_annual_investment_paise is not None

    replayed = compute_retirement_projection(
        session, _with_investment(profile, solved.required_annual_investment_paise), assumptions
    )

    assert (
        abs(
            (replayed.corpus_at_retirement_paise or 0)
            - (replayed.required_corpus_at_retirement_paise or 0)
        )
        < 100
    )


def test_already_funded_plan_needs_no_contribution(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A corpus far beyond what the income stream needs -> the solve would go
    # negative; report zero rather than a negative "contribution".
    _seed_corpus(session, monkeypatch, 500_000_000_000)  # Rs 50 Cr
    profile = _profile(current_age=51, retirement_age=60)

    result = compute_retirement_projection(session, profile, _assumptions())

    assert result.required_annual_investment_paise == 0
    assert result.additional_annual_investment_paise == 0


def test_additional_investment_is_the_gap_above_what_is_already_planned(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_corpus(session, monkeypatch, 100_000_000)  # small corpus -> real gap
    profile = _profile(
        current_age=51, retirement_age=60, annual_investment_paise=60_000_000,
        annual_investment_step_up_pct=Decimal("0"),
    )

    result = compute_retirement_projection(session, profile, _assumptions())

    assert result.required_annual_investment_paise is not None
    assert result.required_annual_investment_paise > 60_000_000
    assert result.additional_annual_investment_paise == (
        result.required_annual_investment_paise - 60_000_000
    )
    assert result.required_monthly_investment_paise == round(
        result.required_annual_investment_paise / 12
    )


def test_emergency_reserve_is_carved_out_of_the_investable_corpus(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_corpus(session, monkeypatch, 10_000_000_000)  # Rs 1 Cr
    profile = _profile(current_age=59, retirement_age=60, emergency_reserve_paise=2_000_000_000)

    result = compute_retirement_projection(session, profile, _assumptions())

    assert result.total_portfolio_paise == 10_000_000_000
    assert result.emergency_reserve_paise == 2_000_000_000
    assert result.starting_corpus_paise == 8_000_000_000  # Rs 80 L invested
    assert result.years[0].opening_corpus_paise == 8_000_000_000


def test_reserve_larger_than_the_portfolio_floors_at_zero(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_corpus(session, monkeypatch, 100_000_000)
    profile = _profile(current_age=59, retirement_age=60, emergency_reserve_paise=500_000_000)

    result = compute_retirement_projection(session, profile, _assumptions())

    assert result.starting_corpus_paise == 0  # not negative


def test_healthcare_escalates_at_its_own_rate_not_general_inflation(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Rs 1L/yr today at 10% healthcare inflation, retiring in 1 year:
    # first retirement year = 100000 * 1.10 = Rs 1,10,000, then +10%/yr
    # while the general withdrawal escalates at only 7%.
    _seed_corpus(session, monkeypatch, 100_000_000_000)
    profile = _profile(
        current_age=59, retirement_age=60, life_expectancy=64,
        annual_healthcare_today_paise=10_000_000,
    )

    result = compute_retirement_projection(
        session, profile, _assumptions(inflation="7"), healthcare_inflation_pct=Decimal("10")
    )

    assert result.healthcare_inflation_pct == Decimal("10")
    assert result.first_year_annual_healthcare_paise == 11_000_000  # 1L * 1.10

    retirement_years = [y for y in result.years if y.phase == RETIREMENT]
    for n, year in enumerate(retirement_years):
        expected = Decimal("11000000") * Decimal("1.10") ** n
        assert abs(Decimal(year.healthcare_paise) - expected) <= n + 1

    # ...and it really is a separate stream from the general withdrawal.
    assert retirement_years[0].withdrawal_paise != retirement_years[0].healthcare_paise


def test_healthcare_raises_the_required_corpus(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_corpus(session, monkeypatch, 100_000_000_000)
    plain = compute_retirement_projection(
        session, _profile(current_age=51, retirement_age=60), _assumptions()
    )
    with_healthcare = compute_retirement_projection(
        session,
        _profile(current_age=51, retirement_age=60, annual_healthcare_today_paise=20_000_000),
        _assumptions(),
        healthcare_inflation_pct=Decimal("10"),
    )

    assert (
        with_healthcare.required_corpus_at_retirement_paise
        > plain.required_corpus_at_retirement_paise
    )


def test_desired_terminal_corpus_raises_the_requirement_and_is_reported_inflated(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Rs 1 Cr in today's money, 34 years to life expectancy at 7%:
    # 1 Cr * 1.07^34 -- reported as the nominal figure actually needed then.
    _seed_corpus(session, monkeypatch, 100_000_000_000)
    plain = compute_retirement_projection(
        session, _profile(current_age=51, retirement_age=60), _assumptions()
    )
    with_terminal = compute_retirement_projection(
        session,
        _profile(
            current_age=51, retirement_age=60,
            desired_terminal_corpus_today_paise=1_000_000_000,
        ),
        _assumptions(inflation="7"),
    )

    expected = Decimal("1000000000") * Decimal("1.07") ** 34
    assert with_terminal.desired_terminal_corpus_paise is not None
    assert abs(with_terminal.desired_terminal_corpus_paise - expected) < 100
    assert (
        with_terminal.required_corpus_at_retirement_paise
        > plain.required_corpus_at_retirement_paise
    )


def test_terminal_corpus_target_met_flag_tracks_the_final_balance(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_corpus(session, monkeypatch, 500_000_000_000)  # Rs 50 Cr -- plenty left over
    rich = compute_retirement_projection(
        session,
        _profile(
            current_age=59, retirement_age=60,
            desired_terminal_corpus_today_paise=1_000_000_000,
        ),
        _assumptions(),
    )
    assert rich.terminal_corpus_target_met is True

    _seed_corpus(session, monkeypatch, 100_000_000)  # depletes
    poor = compute_retirement_projection(
        session,
        _profile(
            current_age=59, retirement_age=60, annual_investment_paise=0,
            desired_terminal_corpus_today_paise=1_000_000_000,
        ),
        _assumptions(),
    )
    assert poor.terminal_corpus_target_met is False


def test_new_inputs_are_inert_when_unset(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An existing plan that predates these fields must project identically."""
    _seed_corpus(session, monkeypatch, 10_000_000_000)
    profile = _profile(current_age=51, retirement_age=60)

    result = compute_retirement_projection(session, profile, _assumptions())

    assert result.emergency_reserve_paise == 0
    assert result.starting_corpus_paise == result.total_portfolio_paise
    assert result.first_year_annual_healthcare_paise == 0
    assert result.desired_terminal_corpus_paise == 0
    assert all(y.healthcare_paise == 0 for y in result.years)
