"""Argument resolution for the retirement and goal projections.

Extracted from `api/routers/retirement.py` and `api/routers/goals.py` on
their second caller: the AI tool layer (`ai/tools/planning.py`) needs the
same "resolve the stored profile, apply what-if overrides to a detached
copy, pull the current assumption version, then compute" sequence those
routers were carrying inline. Reimplementing it there would have been two
copies of the same business logic drifting apart -- and the what-if
detachment in particular is a correctness rule, not a convenience:
exploring "what if I retired at 62" must never quietly become "I retire
at 62".

Raises `PlanningUnavailable` rather than `HTTPException` so the same
functions serve an HTTP route and a tool call; each caller renders the
failure in its own idiom.
"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from fundlens.allocation.service import detached_profile_with, get_risk_profile
from fundlens.assumptions.service import (
    get_current_goal_assumptions,
    get_current_healthcare_assumptions,
    get_current_monte_carlo_assumptions,
    get_current_scenario_assumptions,
)
from fundlens.compute.goal import GoalProjection, compute_goal_projection
from fundlens.compute.monte_carlo import (
    MonteCarloResult,
    RetirementMonteCarloResult,
    run_monte_carlo,
    run_retirement_monte_carlo,
)
from fundlens.compute.retirement import (
    RetirementProjection,
    RetirementScenario,
    compute_retirement_projection,
    compute_retirement_scenarios,
)
from fundlens.db.models import Goal, RiskProfile
from fundlens.goals import service as goals_service

_NO_PROFILE = "No risk profile set — complete onboarding first (POST /risk-profile)"


class PlanningUnavailable(Exception):
    """A projection can't be computed from the data that exists yet — no
    risk profile, no such goal, an incomplete retirement plan. Carries the
    user-facing reason; never a bug."""


def _require_profile(session: Session) -> RiskProfile:
    profile = get_risk_profile(session)
    if profile is None:
        raise PlanningUnavailable(_NO_PROFILE)
    return profile


def project_retirement(
    session: Session,
    *,
    retirement_age: int | None = None,
    annual_investment_paise: int | None = None,
    monthly_withdrawal_today_paise: int | None = None,
    return_delta_pct: Decimal = Decimal(0),
    inflation_delta_pct: Decimal = Decimal(0),
) -> RetirementProjection:
    """What-if overrides are applied to a **detached** copy of the stored
    plan and never written back (spec §25). Omit them all for the saved
    plan as-is."""
    profile = _require_profile(session)

    overrides: dict[str, object] = {}
    if retirement_age is not None:
        overrides["retirement_age"] = retirement_age
    if annual_investment_paise is not None:
        overrides["annual_investment_paise"] = annual_investment_paise
    if monthly_withdrawal_today_paise is not None:
        overrides["monthly_withdrawal_today_paise"] = monthly_withdrawal_today_paise
    if overrides:
        profile = detached_profile_with(session, profile, **overrides)

    return compute_retirement_projection(
        session,
        profile,
        get_current_goal_assumptions(session),
        return_delta_pct=return_delta_pct,
        inflation_delta_pct=inflation_delta_pct,
        healthcare_inflation_pct=get_current_healthcare_assumptions(
            session
        ).healthcare_inflation_pct,
    )


def retirement_scenarios(session: Session) -> list[RetirementScenario]:
    profile = _require_profile(session)
    scenario_assumptions = get_current_scenario_assumptions(session)
    return compute_retirement_scenarios(
        session,
        profile,
        get_current_goal_assumptions(session),
        scenario_assumptions.return_delta_pct,
        scenario_assumptions.inflation_delta_pct,
    )


@dataclass(frozen=True)
class RetirementSimulation:
    result: RetirementMonteCarloResult
    volatility_pct: Decimal


def retirement_monte_carlo(
    session: Session,
    *,
    retirement_age: int | None = None,
    annual_investment_paise: int | None = None,
    monthly_withdrawal_today_paise: int | None = None,
) -> RetirementSimulation:
    profile = _require_profile(session)
    projection = project_retirement(
        session,
        retirement_age=retirement_age,
        annual_investment_paise=annual_investment_paise,
        monthly_withdrawal_today_paise=monthly_withdrawal_today_paise,
    )
    if projection.reason is not None:
        raise PlanningUnavailable(projection.reason)

    mc_assumptions = get_current_monte_carlo_assumptions(session)
    # Blended the same weighted-average way the expected return is
    # (compute-engine.md §14) -- one volatility for the whole portfolio.
    volatility_pct = (
        profile.target_equity_pct / 100 * mc_assumptions.equity_volatility_pct
        + profile.target_debt_pct / 100 * mc_assumptions.debt_volatility_pct
    )

    lump_sum_by_age = {
        e.age: e.inflated_cost_paise
        for e in projection.events
        if e.excluded_reason is None and e.age is not None and e.inflated_cost_paise is not None
    }

    result = run_retirement_monte_carlo(
        starting_corpus_paise=projection.starting_corpus_paise or 0,
        annual_investment_paise=(
            annual_investment_paise
            if annual_investment_paise is not None
            else profile.annual_investment_paise or 0
        ),
        step_up_pct=profile.annual_investment_step_up_pct or Decimal(0),
        current_age=projection.current_age or 0,
        retirement_age=projection.retirement_age or 0,
        life_expectancy=projection.life_expectancy or 0,
        first_year_withdrawal_paise=projection.first_year_annual_withdrawal_paise or 0,
        first_year_healthcare_paise=projection.first_year_annual_healthcare_paise or 0,
        healthcare_inflation_pct=projection.healthcare_inflation_pct,
        lump_sum_by_age=lump_sum_by_age,
        pre_return_pct=projection.blended_return_pct or Decimal(0),
        post_return_pct=projection.post_retirement_return_pct or Decimal(0),
        volatility_pct=volatility_pct,
        inflation_pct=projection.inflation_pct or Decimal(0),
    )
    return RetirementSimulation(result=result, volatility_pct=volatility_pct)


@dataclass(frozen=True)
class ResolvedGoalProjection:
    goal: Goal
    profile: RiskProfile
    projection: GoalProjection


def project_goal(session: Session, goal_id: str) -> ResolvedGoalProjection:
    goal = goals_service.get_goal(session, goal_id)
    if goal is None:
        raise PlanningUnavailable(f"No goal with id {goal_id!r}")
    profile = _require_profile(session)
    tagged = {(h.folio, h.scheme_key) for h in goals_service.get_goal_holdings(session, goal_id)}
    projection = compute_goal_projection(
        session, goal, tagged, profile, get_current_goal_assumptions(session)
    )
    return ResolvedGoalProjection(goal=goal, profile=profile, projection=projection)


def goal_monte_carlo(session: Session, goal_id: str) -> MonteCarloResult:
    resolved = project_goal(session, goal_id)
    mc_assumptions = get_current_monte_carlo_assumptions(session)
    return run_monte_carlo(
        goal_id=resolved.goal.id,
        current_corpus_paise=resolved.projection.current_corpus_paise,
        inflated_target_paise=resolved.projection.inflated_target_paise,
        required_monthly_sip_paise=resolved.projection.required_monthly_sip_paise,
        blended_return_pct=resolved.projection.blended_expected_return_pct,
        years=resolved.projection.years_to_target,
        equity_volatility_pct=mc_assumptions.equity_volatility_pct,
        debt_volatility_pct=mc_assumptions.debt_volatility_pct,
        target_equity_pct=resolved.profile.target_equity_pct,
        target_debt_pct=resolved.profile.target_debt_pct,
    )
