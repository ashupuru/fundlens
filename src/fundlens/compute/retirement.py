"""Retirement projection (compute-engine.md §16): a year-by-year
accumulation-then-decumulation simulation of the whole portfolio, from the
user's current age through life expectancy.

**Why this is a separate engine from `compute/goal.py`.** Goal planning
answers "will this lump sum be there by this date?" with a closed-form
future-value formula evaluated at one point in time. Retirement needs a
*sequential* simulation: each year's closing balance is the next year's
opening balance, and a withdrawal permanently reduces the base everything
after it compounds on. A closed form can't express that, so this is a
genuinely different computation rather than a parameterization of the
existing one. The two engines do share their return assumption, via
`goal.py::blended_expected_return_pct`, so they can never disagree about
what rate a given profile implies.

**Ordering convention within each year** (spec §17): returns accrue on the
*opening* balance only, then the year's investment is added / withdrawal
taken. That makes contributions effectively end-of-year (ordinary annuity),
matching `compute/goal.py::_required_monthly_sip_paise`'s own convention --
the conservative choice, since assuming a contribution earns a full year's
return in the year it's made would overstate the corpus.

**Money stays Decimal** through the growth math, converted to int paise only
at the boundary -- matching `compute/goal.py` (and unlike
`compute/monte_carlo.py`, whose float is reserved for genuinely statistical
output).

Deliberately out of scope for this phase, each planned as its own follow-up:
lump-sum life events against the same corpus (existing `Goal` rows feed in
as withdrawals), conservative/base/optimistic scenarios, reverse planning
("what SIP do I need?"), interactive what-if, Monte Carlo sequence-of-returns
risk, and the healthcare/emergency/tax/terminal-corpus layers.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from fundlens.assumptions.service import GoalAssumptions
from fundlens.compute.goal import blended_expected_return_pct
from fundlens.compute.metrics import compute_portfolio_metrics
from fundlens.db.models import Goal, RiskProfile
from fundlens.goals.service import list_goals

_MONTHS_PER_YEAR = 12
_DAYS_PER_YEAR = 365.0

EXCLUDED_DATE_PASSED = "target_date_already_passed"
EXCLUDED_BEYOND_LIFE_EXPECTANCY = "beyond_life_expectancy"

ACCUMULATION = "accumulation"
RETIREMENT = "retirement"

REASON_INCOMPLETE = "retirement_profile_incomplete"
REASON_ALREADY_RETIRED = "current_age_at_or_past_retirement_age"
REASON_INVALID_AGES = "life_expectancy_not_after_retirement_age"


def _round_paise(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class RetirementYear:
    age: int
    phase: str  # ACCUMULATION | RETIREMENT
    opening_corpus_paise: int
    investment_paise: int  # 0 once retired
    return_paise: int
    withdrawal_paise: int  # retirement income; 0 until retired
    lump_sum_paise: int  # planned life events landing in this year
    lump_sum_shortfall_paise: int  # events the corpus couldn't cover
    closing_corpus_paise: int


@dataclass(frozen=True)
class PlannedEvent:
    """One `Goal` read as a future claim on the retirement corpus."""

    goal_id: str
    name: str
    target_date: date
    age: int | None  # None when excluded
    today_cost_paise: int
    inflated_cost_paise: int | None  # None when excluded
    inflation_pct_used: Decimal | None
    excluded_reason: str | None


@dataclass(frozen=True)
class RetirementProjection:
    # When `reason` is set, every projected field below is None/empty --
    # an incomplete or contradictory profile produces no numbers at all
    # rather than numbers derived from guessed inputs.
    reason: str | None
    current_age: int | None
    retirement_age: int | None
    life_expectancy: int | None
    years_to_retirement: int | None
    starting_corpus_paise: int | None
    blended_return_pct: Decimal | None
    post_retirement_return_pct: Decimal | None
    inflation_pct: Decimal | None
    corpus_at_retirement_paise: int | None
    required_corpus_at_retirement_paise: int | None
    surplus_or_shortfall_paise: int | None
    readiness_pct: float | None
    monthly_withdrawal_today_paise: int | None  # today's purchasing power
    first_year_monthly_withdrawal_paise: int | None  # nominal, at retirement
    depletion_age: int | None  # None => corpus survives to life expectancy
    terminal_corpus_paise: int | None
    total_lump_sum_paise: int | None
    total_lump_sum_shortfall_paise: int | None
    events: list[PlannedEvent]
    years: list[RetirementYear]


def _incomplete(reason: str) -> RetirementProjection:
    return RetirementProjection(
        reason=reason,
        current_age=None,
        retirement_age=None,
        life_expectancy=None,
        years_to_retirement=None,
        starting_corpus_paise=None,
        blended_return_pct=None,
        post_retirement_return_pct=None,
        inflation_pct=None,
        corpus_at_retirement_paise=None,
        required_corpus_at_retirement_paise=None,
        surplus_or_shortfall_paise=None,
        readiness_pct=None,
        monthly_withdrawal_today_paise=None,
        first_year_monthly_withdrawal_paise=None,
        depletion_age=None,
        terminal_corpus_paise=None,
        total_lump_sum_paise=None,
        total_lump_sum_shortfall_paise=None,
        events=[],
        years=[],
    )


def _plan_events(
    goals: list[Goal],
    current_age: int,
    life_expectancy: int,
    default_inflation_pct: Decimal,
    today: date,
) -> list[PlannedEvent]:
    """Maps each `Goal` onto the age it lands at and inflates its cost to
    that year.

    **Goals are claims on the whole portfolio by default**, because that is
    what they are: `GoalHolding` tagging labels holdings, it doesn't
    segregate money into a separate pot, and this projection's starting
    corpus is the *entire* portfolio (including any tagged holdings). So
    counting a goal here is not double-counting -- ignoring goals entirely
    would be the choice that overstates what's available for retirement.
    A goal can opt out via `include_in_retirement_projection` when it is
    already represented elsewhere (a goal tracking "retirement" itself
    duplicates the income stream modelled below) or funded from outside.

    Ages are whole years from today so an event shown in the age-55 row is
    inflated by exactly the number of rows between now and that row -- the
    table stays internally consistent, rather than mixing fractional-year
    inflation into an integer-year ledger.
    """
    events: list[PlannedEvent] = []
    for goal in goals:
        if not goal.include_in_retirement_projection:
            continue
        inflation_pct = (
            goal.inflation_pct_override
            if goal.inflation_pct_override is not None
            else default_inflation_pct
        )
        years_until = int((goal.target_date - today).days // _DAYS_PER_YEAR)

        if years_until < 0:
            events.append(
                PlannedEvent(
                    goal_id=goal.id,
                    name=goal.name,
                    target_date=goal.target_date,
                    age=None,
                    today_cost_paise=goal.target_amount_paise,
                    inflated_cost_paise=None,
                    inflation_pct_used=None,
                    excluded_reason=EXCLUDED_DATE_PASSED,
                )
            )
            continue

        age = current_age + years_until
        if age >= life_expectancy:
            events.append(
                PlannedEvent(
                    goal_id=goal.id,
                    name=goal.name,
                    target_date=goal.target_date,
                    age=age,
                    today_cost_paise=goal.target_amount_paise,
                    inflated_cost_paise=None,
                    inflation_pct_used=None,
                    excluded_reason=EXCLUDED_BEYOND_LIFE_EXPECTANCY,
                )
            )
            continue

        inflated = Decimal(goal.target_amount_paise) * (1 + inflation_pct / 100) ** years_until
        events.append(
            PlannedEvent(
                goal_id=goal.id,
                name=goal.name,
                target_date=goal.target_date,
                age=age,
                today_cost_paise=goal.target_amount_paise,
                inflated_cost_paise=_round_paise(inflated),
                inflation_pct_used=inflation_pct,
                excluded_reason=None,
            )
        )
    return events


def required_corpus_for_withdrawals(
    first_year_withdrawal_paise: Decimal,
    return_pct: Decimal,
    inflation_pct: Decimal,
    n_years: int,
) -> Decimal:
    """Present value, at `return_pct`, of `n_years` of annual withdrawals
    starting at `first_year_withdrawal_paise` and escalating at
    `inflation_pct` -- the standard growing-annuity PV, i.e. the corpus
    needed *at retirement* to fund the whole withdrawal stream (spec §18's
    "Required Retirement Corpus").

    The closed form PV = W/(r-g) * (1 - ((1+g)/(1+r))^n) divides by zero
    when r == g, so that case is branched explicitly: with returns exactly
    matching inflation every withdrawal has the same present value
    W/(1+r), so the total is simply n * W/(1+r). Without this branch a
    perfectly plausible input (post-retirement return set equal to
    inflation) would crash rather than produce its correct, finite answer.
    """
    if n_years <= 0:
        return Decimal(0)

    r = return_pct / 100
    g = inflation_pct / 100

    if r == g:
        return n_years * first_year_withdrawal_paise / (1 + r)

    ratio = (1 + g) / (1 + r)
    return first_year_withdrawal_paise / (r - g) * (1 - ratio**n_years)


def compute_retirement_projection(
    session: Session,
    profile: RiskProfile,
    assumptions: GoalAssumptions,
) -> RetirementProjection:
    required_fields = (
        profile.current_age,
        profile.retirement_age,
        profile.life_expectancy,
        profile.annual_investment_paise,
        profile.monthly_withdrawal_today_paise,
    )
    if any(f is None for f in required_fields):
        return _incomplete(REASON_INCOMPLETE)

    current_age = profile.current_age
    retirement_age = profile.retirement_age
    life_expectancy = profile.life_expectancy
    assert current_age is not None and retirement_age is not None
    assert life_expectancy is not None
    assert profile.annual_investment_paise is not None
    assert profile.monthly_withdrawal_today_paise is not None

    if current_age >= retirement_age:
        return _incomplete(REASON_ALREADY_RETIRED)
    if life_expectancy <= retirement_age:
        return _incomplete(REASON_INVALID_AGES)

    inflation_pct = assumptions.inflation_pct
    pre_return_pct = blended_expected_return_pct(profile, assumptions)
    post_return_pct = (
        profile.post_retirement_return_pct
        if profile.post_retirement_return_pct is not None
        else pre_return_pct
    )
    step_up_pct = (
        profile.annual_investment_step_up_pct
        if profile.annual_investment_step_up_pct is not None
        else Decimal(0)
    )

    starting_corpus_paise = compute_portfolio_metrics(session).current_value_paise
    years_to_retirement = retirement_age - current_age

    events = _plan_events(
        list_goals(session), current_age, life_expectancy, inflation_pct, date.today()
    )
    lump_sum_by_age: dict[int, Decimal] = {}
    for event in events:
        if event.excluded_reason is None and event.age is not None:
            assert event.inflated_cost_paise is not None
            lump_sum_by_age[event.age] = lump_sum_by_age.get(
                event.age, Decimal(0)
            ) + Decimal(event.inflated_cost_paise)

    total_shortfall = Decimal(0)

    corpus = Decimal(starting_corpus_paise)
    investment = Decimal(profile.annual_investment_paise)
    years: list[RetirementYear] = []

    for age in range(current_age, retirement_age):
        opening = corpus
        year_return = opening * pre_return_pct / 100
        available = opening + year_return + investment

        # Lump sums come out after the year's growth and contribution, and
        # can't take the corpus below zero: an event bigger than what's
        # there is reported as a shortfall rather than silently modelled as
        # borrowing.
        lump_sum = lump_sum_by_age.get(age, Decimal(0))
        funded = min(lump_sum, max(available, Decimal(0)))
        shortfall = lump_sum - funded
        total_shortfall += shortfall
        closing = available - funded

        years.append(
            RetirementYear(
                age=age,
                phase=ACCUMULATION,
                opening_corpus_paise=_round_paise(opening),
                investment_paise=_round_paise(investment),
                return_paise=_round_paise(year_return),
                withdrawal_paise=0,
                lump_sum_paise=_round_paise(funded),
                lump_sum_shortfall_paise=_round_paise(shortfall),
                closing_corpus_paise=_round_paise(closing),
            )
        )
        corpus = closing
        investment = investment * (1 + step_up_pct / 100)

    corpus_at_retirement = corpus

    # Today's monthly requirement, inflated to the retirement year (spec §8):
    # the nominal rupee amount actually needed then to buy what
    # monthly_withdrawal_today_paise buys now.
    annual_withdrawal = (
        Decimal(profile.monthly_withdrawal_today_paise)
        * _MONTHS_PER_YEAR
        * (1 + inflation_pct / 100) ** years_to_retirement
    )
    first_year_monthly_withdrawal = annual_withdrawal / _MONTHS_PER_YEAR

    retirement_years = life_expectancy - retirement_age
    # Required corpus must cover the income stream *and* any life events
    # falling after retirement -- both are claims on the same corpus, so
    # counting only the income stream would understate what's needed.
    required_corpus = required_corpus_for_withdrawals(
        annual_withdrawal, post_return_pct, inflation_pct, retirement_years
    )
    for event_age, amount in lump_sum_by_age.items():
        if event_age >= retirement_age:
            required_corpus += amount / (1 + post_return_pct / 100) ** (
                event_age - retirement_age
            )

    depletion_age: int | None = None
    withdrawal = annual_withdrawal
    for age in range(retirement_age, life_expectancy):
        opening = corpus
        year_return = opening * post_return_pct / 100
        after_income = opening + year_return - withdrawal

        lump_sum = lump_sum_by_age.get(age, Decimal(0))
        funded = min(lump_sum, max(after_income, Decimal(0)))
        shortfall = lump_sum - funded
        total_shortfall += shortfall
        closing = after_income - funded

        depleted = closing < 0
        if depleted:
            # A negative corpus isn't a meaningful figure to display -- clamp
            # to zero, record where it ran out, and stop: every later year
            # would just be "still zero".
            closing = Decimal(0)
            depletion_age = age

        years.append(
            RetirementYear(
                age=age,
                phase=RETIREMENT,
                opening_corpus_paise=_round_paise(opening),
                investment_paise=0,
                return_paise=_round_paise(year_return),
                withdrawal_paise=_round_paise(withdrawal),
                lump_sum_paise=_round_paise(funded),
                lump_sum_shortfall_paise=_round_paise(shortfall),
                closing_corpus_paise=_round_paise(closing),
            )
        )
        corpus = closing
        if depleted:
            break
        withdrawal = withdrawal * (1 + inflation_pct / 100)

    # Surplus is the difference of the *rounded* figures, not the rounding of
    # the difference: these three numbers are shown together, and a reader
    # subtracting the two displayed corpus values must get exactly the
    # displayed surplus (same on-screen reconciliation requirement the Funds
    # page's headline stats already hold themselves to).
    corpus_at_retirement_paise = _round_paise(corpus_at_retirement)
    required_corpus_paise = _round_paise(required_corpus)
    surplus_paise = corpus_at_retirement_paise - required_corpus_paise
    readiness = (
        float(corpus_at_retirement / required_corpus * 100) if required_corpus > 0 else None
    )

    return RetirementProjection(
        reason=None,
        current_age=current_age,
        retirement_age=retirement_age,
        life_expectancy=life_expectancy,
        years_to_retirement=years_to_retirement,
        starting_corpus_paise=starting_corpus_paise,
        blended_return_pct=pre_return_pct,
        post_retirement_return_pct=post_return_pct,
        inflation_pct=inflation_pct,
        corpus_at_retirement_paise=corpus_at_retirement_paise,
        required_corpus_at_retirement_paise=required_corpus_paise,
        surplus_or_shortfall_paise=surplus_paise,
        readiness_pct=readiness,
        monthly_withdrawal_today_paise=profile.monthly_withdrawal_today_paise,
        first_year_monthly_withdrawal_paise=_round_paise(first_year_monthly_withdrawal),
        depletion_age=depletion_age,
        terminal_corpus_paise=_round_paise(corpus),
        total_lump_sum_paise=_round_paise(sum(lump_sum_by_age.values(), Decimal(0))),
        total_lump_sum_shortfall_paise=_round_paise(total_shortfall),
        events=events,
        years=years,
    )
