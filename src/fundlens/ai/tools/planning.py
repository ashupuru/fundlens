"""Retirement and goal planning tools.

These are the tools that justify the whole tool-calling layer: their
answers depend on inputs the *user* chooses at question time ("what if I
retire at 52?"), so no precomputed snapshot can ever hold them. Everything
routes through `planning/service.py`, the same resolvers the HTTP routes
use -- including its detached-profile rule, so a what-if never mutates the
stored plan.
"""

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from fundlens.ai.tools.registry import Tool, ToolError, object_schema
from fundlens.ai.tools.serialize import compact, money, number
from fundlens.compute.retirement import RetirementProjection
from fundlens.goals import service as goals_service
from fundlens.planning import service as planning

_AGE = {"type": "integer", "description": "Age in whole years"}
_RUPEES = {"type": "number", "description": "Amount in rupees (not paise)"}

_WHAT_IF_PROPERTIES: dict[str, Any] = {
    "retirement_age": {**_AGE, "description": "Retire at this age instead of the planned one"},
    "annual_investment_rupees": {
        **_RUPEES,
        "description": "Invest this much per year instead of the planned amount",
    },
    "monthly_withdrawal_today_rupees": {
        **_RUPEES,
        "description": "Spend this much per month in retirement, in today's purchasing power",
    },
}


def _to_paise(rupees: float | None) -> int | None:
    if rupees is None:
        return None
    return int(Decimal(str(rupees)) * 100)


def _projection_result(p: RetirementProjection) -> dict[str, Any]:
    if p.reason is not None:
        raise ToolError(p.reason)
    return compact(
        {
            "current_age": p.current_age,
            "retirement_age": p.retirement_age,
            "life_expectancy": p.life_expectancy,
            "years_to_retirement": p.years_to_retirement,
            "starting_corpus": money(p.starting_corpus_paise),
            "emergency_reserve": money(p.emergency_reserve_paise),
            "corpus_at_retirement": money(p.corpus_at_retirement_paise),
            "required_corpus_at_retirement": money(p.required_corpus_at_retirement_paise),
            "surplus_or_shortfall": money(p.surplus_or_shortfall_paise),
            "readiness_pct": number(p.readiness_pct),
            "monthly_withdrawal_today": money(p.monthly_withdrawal_today_paise),
            "first_year_monthly_withdrawal": money(p.first_year_monthly_withdrawal_paise),
            "first_year_annual_healthcare": money(p.first_year_annual_healthcare_paise),
            # None means the corpus survives to life expectancy -- said in
            # words because an absent key reads as missing data, not as good
            # news.
            "depletion_age": p.depletion_age if p.depletion_age is not None else "never depletes",
            "terminal_corpus": money(p.terminal_corpus_paise),
            "desired_terminal_corpus": money(p.desired_terminal_corpus_paise),
            "terminal_corpus_target_met": p.terminal_corpus_target_met,
            "blended_return_pct": number(p.blended_return_pct),
            "post_retirement_return_pct": number(p.post_retirement_return_pct),
            "inflation_pct": number(p.inflation_pct),
            "healthcare_inflation_pct": number(p.healthcare_inflation_pct),
            # Reverse planning.
            "current_annual_investment": money(p.current_annual_investment_paise),
            "required_annual_investment": money(p.required_annual_investment_paise),
            "required_monthly_investment": money(p.required_monthly_investment_paise),
            "additional_annual_investment": money(p.additional_annual_investment_paise),
            "planned_events": [
                compact(
                    {
                        "name": e.name,
                        "age": e.age,
                        "today_cost": money(e.today_cost_paise),
                        "inflated_cost": money(e.inflated_cost_paise),
                        "excluded_reason": e.excluded_reason,
                    }
                )
                for e in p.events
            ],
        }
    )
    # `p.years` is deliberately omitted: 40-60 rows of 9 figures each would
    # add several hundred numbers to the evidence, and every one of them
    # widens the validator's foreign-number net (ai-layer.md §2). The
    # year-by-year table is available over HTTP for a human to read;
    # depletion age and terminal corpus are the summary figures a narrative
    # actually needs.


def _retirement_projection(
    session: Session,
    retirement_age: int | None = None,
    annual_investment_rupees: float | None = None,
    monthly_withdrawal_today_rupees: float | None = None,
) -> dict[str, Any]:
    try:
        projection = planning.project_retirement(
            session,
            retirement_age=retirement_age,
            annual_investment_paise=_to_paise(annual_investment_rupees),
            monthly_withdrawal_today_paise=_to_paise(monthly_withdrawal_today_rupees),
        )
    except planning.PlanningUnavailable as exc:
        raise ToolError(str(exc)) from exc
    return _projection_result(projection)


def _retirement_scenarios(session: Session) -> dict[str, Any]:
    try:
        scenarios = planning.retirement_scenarios(session)
    except planning.PlanningUnavailable as exc:
        raise ToolError(str(exc)) from exc
    return {
        "scenarios": [
            {
                "key": s.key,
                "label": s.label,
                "return_delta_pct": number(s.return_delta_pct),
                "inflation_delta_pct": number(s.inflation_delta_pct),
                "projection": _projection_result(s.projection),
            }
            for s in scenarios
        ]
    }


def _retirement_monte_carlo(
    session: Session,
    retirement_age: int | None = None,
    annual_investment_rupees: float | None = None,
    monthly_withdrawal_today_rupees: float | None = None,
) -> dict[str, Any]:
    try:
        simulation = planning.retirement_monte_carlo(
            session,
            retirement_age=retirement_age,
            annual_investment_paise=_to_paise(annual_investment_rupees),
            monthly_withdrawal_today_paise=_to_paise(monthly_withdrawal_today_rupees),
        )
    except planning.PlanningUnavailable as exc:
        raise ToolError(str(exc)) from exc
    r = simulation.result
    return compact(
        {
            "simulations_run": r.simulations_run,
            "success_probability_pct": number(r.success_probability_pct),
            "median_depletion_age": (
                r.median_depletion_age
                if r.median_depletion_age is not None
                else "never depletes in the median path"
            ),
            "percentile_10_terminal_corpus": money(r.percentile_10_terminal_corpus_paise),
            "percentile_50_terminal_corpus": money(r.percentile_50_terminal_corpus_paise),
            "percentile_90_terminal_corpus": money(r.percentile_90_terminal_corpus_paise),
            "volatility_pct": number(simulation.volatility_pct),
        }
    )


def _list_goals(session: Session) -> dict[str, Any]:
    return {
        "goals": [
            {
                "goal_id": g.id,
                "name": g.name,
                "target": money(g.target_amount_paise),
                "target_date": g.target_date.isoformat(),
            }
            for g in goals_service.list_goals(session)
        ]
    }


def _goal_projection(session: Session, goal_id: str) -> dict[str, Any]:
    try:
        resolved = planning.project_goal(session, goal_id)
    except planning.PlanningUnavailable as exc:
        raise ToolError(str(exc)) from exc
    p = resolved.projection
    return compact(
        {
            "goal_id": p.goal_id,
            "goal_name": resolved.goal.name,
            "years_to_target": number(p.years_to_target),
            "current_corpus": money(p.current_corpus_paise),
            "current_corpus_unavailable": money(p.current_corpus_unavailable_paise),
            "inflated_target": money(p.inflated_target_paise),
            "projected_corpus": money(p.projected_corpus_paise),
            "surplus_or_shortfall": money(p.surplus_or_shortfall_paise),
            "required_monthly_sip": money(p.required_monthly_sip_paise),
            "blended_expected_return_pct": number(p.blended_expected_return_pct),
        }
    )


def _goal_monte_carlo(session: Session, goal_id: str) -> dict[str, Any]:
    try:
        r = planning.goal_monte_carlo(session, goal_id)
    except planning.PlanningUnavailable as exc:
        raise ToolError(str(exc)) from exc
    return {
        "goal_id": r.goal_id,
        "simulations_run": r.simulations_run,
        "success_probability_no_contribution_pct": number(
            r.success_probability_no_contribution_pct
        ),
        "success_probability_with_required_sip_pct": number(
            r.success_probability_with_required_sip_pct
        ),
        "percentile_10_corpus": money(r.percentile_10_corpus_paise),
        "percentile_50_corpus": money(r.percentile_50_corpus_paise),
        "percentile_90_corpus": money(r.percentile_90_corpus_paise),
    }


PLANNING_TOOLS = [
    Tool(
        name="compute_retirement_projection",
        description=(
            "Year-by-year retirement projection from the user's saved plan: corpus at "
            "retirement, required corpus, surplus/shortfall, readiness, depletion age, and "
            "reverse planning (what annual investment would close the gap). Pass any "
            "what-if argument to explore an alternative without changing the saved plan."
        ),
        input_schema=object_schema(_WHAT_IF_PROPERTIES),
        fn=_retirement_projection,
    ),
    Tool(
        name="run_retirement_monte_carlo",
        description=(
            "Monte Carlo simulation of the retirement plan under return volatility. Returns "
            "the probability the corpus survives to life expectancy and the 10th/50th/90th "
            "percentile terminal corpus. Use when the question is about risk or confidence "
            "rather than a single projected path. Accepts the same what-if arguments."
        ),
        input_schema=object_schema(_WHAT_IF_PROPERTIES),
        fn=_retirement_monte_carlo,
    ),
    Tool(
        name="compute_retirement_scenarios",
        description=(
            "The retirement projection recomputed under conservative, base and optimistic "
            "return/inflation assumptions."
        ),
        input_schema=object_schema({}),
        fn=_retirement_scenarios,
    ),
    Tool(
        name="list_goals",
        description=(
            "Every saved financial goal with its id, target amount and target date. Call "
            "this first to find the goal_id needed by the goal tools."
        ),
        input_schema=object_schema({}),
        fn=_list_goals,
    ),
    Tool(
        name="compute_goal_projection",
        description=(
            "Projection for one goal: inflation-adjusted target, corpus projected by the "
            "target date, surplus or shortfall, and the monthly SIP required to close it."
        ),
        input_schema=object_schema(
            {"goal_id": {"type": "string", "description": "Goal id from list_goals"}},
            required=["goal_id"],
        ),
        fn=_goal_projection,
    ),
    Tool(
        name="run_goal_monte_carlo",
        description=(
            "Monte Carlo simulation for one goal: probability of reaching the target with "
            "no further contribution and with the required SIP, plus percentile outcomes."
        ),
        input_schema=object_schema(
            {"goal_id": {"type": "string", "description": "Goal id from list_goals"}},
            required=["goal_id"],
        ),
        fn=_goal_monte_carlo,
    ),
]
