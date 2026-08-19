"""Retirement projection (compute-engine.md §16): the retirement-plan
inputs and the year-by-year accumulation/decumulation projection derived
from them.

The projection is a single aggregating GET rather than a separately
triggered call (the treatment Monte Carlo needed): it's a few dozen rows of
arithmetic over one already-computed portfolio value, with no per-year
database access.
"""

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from fundlens.allocation.service import (
    detached_profile_with,
    get_risk_profile,
    set_retirement_profile,
)
from fundlens.api.deps import get_session
from fundlens.assumptions.service import (
    get_current_goal_assumptions,
    get_current_scenario_assumptions,
)
from fundlens.compute.retirement import (
    RetirementProjection,
    compute_retirement_projection,
    compute_retirement_scenarios,
)
from fundlens.db.models import RiskProfile
from fundlens.money import to_paise_strict

router = APIRouter(prefix="/retirement", tags=["retirement"])


class RetirementProfileRequest(BaseModel):
    current_age: int | None = None
    retirement_age: int | None = None
    life_expectancy: int | None = None
    annual_investment_rupees: str | None = None
    annual_investment_step_up_pct: str | None = None
    monthly_withdrawal_today_rupees: str | None = None
    post_retirement_return_pct: str | None = None


class RetirementProfileResponse(BaseModel):
    current_age: int | None
    retirement_age: int | None
    life_expectancy: int | None
    annual_investment_paise: int | None
    annual_investment_step_up_pct: str | None
    monthly_withdrawal_today_paise: int | None
    post_retirement_return_pct: str | None


def _profile_response(profile: RiskProfile) -> RetirementProfileResponse:
    # `is not None`, not truthiness: Decimal("0") is falsy, and a deliberate
    # 0% step-up / 0% post-retirement return is a real, meaningful input that
    # must not be silently reported back as "unset".
    return RetirementProfileResponse(
        current_age=profile.current_age,
        retirement_age=profile.retirement_age,
        life_expectancy=profile.life_expectancy,
        annual_investment_paise=profile.annual_investment_paise,
        annual_investment_step_up_pct=(
            str(profile.annual_investment_step_up_pct)
            if profile.annual_investment_step_up_pct is not None
            else None
        ),
        monthly_withdrawal_today_paise=profile.monthly_withdrawal_today_paise,
        post_retirement_return_pct=(
            str(profile.post_retirement_return_pct)
            if profile.post_retirement_return_pct is not None
            else None
        ),
    )


@router.get("/profile", response_model=RetirementProfileResponse)
def get_retirement_profile(
    session: Session = Depends(get_session),
) -> RetirementProfileResponse:
    profile = get_risk_profile(session)
    if profile is None:
        raise HTTPException(
            status_code=404,
            detail="No risk profile set — complete onboarding first (POST /risk-profile)",
        )
    return _profile_response(profile)


@router.post("/profile", response_model=RetirementProfileResponse)
def post_retirement_profile(
    request: RetirementProfileRequest, session: Session = Depends(get_session)
) -> RetirementProfileResponse:
    try:
        profile = set_retirement_profile(
            session,
            current_age=request.current_age,
            retirement_age=request.retirement_age,
            life_expectancy=request.life_expectancy,
            annual_investment_paise=(
                to_paise_strict(Decimal(request.annual_investment_rupees))
                if request.annual_investment_rupees
                else None
            ),
            annual_investment_step_up_pct=(
                Decimal(request.annual_investment_step_up_pct)
                if request.annual_investment_step_up_pct
                else None
            ),
            monthly_withdrawal_today_paise=(
                to_paise_strict(Decimal(request.monthly_withdrawal_today_rupees))
                if request.monthly_withdrawal_today_rupees
                else None
            ),
            post_retirement_return_pct=(
                Decimal(request.post_retirement_return_pct)
                if request.post_retirement_return_pct
                else None
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _profile_response(profile)


class RetirementYearResponse(BaseModel):
    age: int
    phase: str
    opening_corpus_paise: int
    investment_paise: int
    return_paise: int
    withdrawal_paise: int
    lump_sum_paise: int
    lump_sum_shortfall_paise: int
    closing_corpus_paise: int


class PlannedEventResponse(BaseModel):
    goal_id: str
    name: str
    target_date: str
    age: int | None
    today_cost_paise: int
    inflated_cost_paise: int | None
    inflation_pct_used: str | None
    excluded_reason: str | None


class RetirementProjectionResponse(BaseModel):
    reason: str | None
    current_age: int | None
    retirement_age: int | None
    life_expectancy: int | None
    years_to_retirement: int | None
    starting_corpus_paise: int | None
    blended_return_pct: str | None
    post_retirement_return_pct: str | None
    inflation_pct: str | None
    corpus_at_retirement_paise: int | None
    required_corpus_at_retirement_paise: int | None
    surplus_or_shortfall_paise: int | None
    readiness_pct: float | None
    monthly_withdrawal_today_paise: int | None
    first_year_monthly_withdrawal_paise: int | None
    depletion_age: int | None
    terminal_corpus_paise: int | None
    total_lump_sum_paise: int | None
    total_lump_sum_shortfall_paise: int | None
    current_annual_investment_paise: int | None
    required_annual_investment_paise: int | None
    required_monthly_investment_paise: int | None
    additional_annual_investment_paise: int | None
    events: list[PlannedEventResponse]
    years: list[RetirementYearResponse]


def _to_response(p: RetirementProjection) -> RetirementProjectionResponse:
    return RetirementProjectionResponse(
        reason=p.reason,
        current_age=p.current_age,
        retirement_age=p.retirement_age,
        life_expectancy=p.life_expectancy,
        years_to_retirement=p.years_to_retirement,
        starting_corpus_paise=p.starting_corpus_paise,
        blended_return_pct=str(p.blended_return_pct) if p.blended_return_pct is not None else None,
        post_retirement_return_pct=(
            str(p.post_retirement_return_pct)
            if p.post_retirement_return_pct is not None
            else None
        ),
        inflation_pct=str(p.inflation_pct) if p.inflation_pct is not None else None,
        corpus_at_retirement_paise=p.corpus_at_retirement_paise,
        required_corpus_at_retirement_paise=p.required_corpus_at_retirement_paise,
        surplus_or_shortfall_paise=p.surplus_or_shortfall_paise,
        readiness_pct=p.readiness_pct,
        monthly_withdrawal_today_paise=p.monthly_withdrawal_today_paise,
        first_year_monthly_withdrawal_paise=p.first_year_monthly_withdrawal_paise,
        depletion_age=p.depletion_age,
        terminal_corpus_paise=p.terminal_corpus_paise,
        total_lump_sum_paise=p.total_lump_sum_paise,
        total_lump_sum_shortfall_paise=p.total_lump_sum_shortfall_paise,
        current_annual_investment_paise=p.current_annual_investment_paise,
        required_annual_investment_paise=p.required_annual_investment_paise,
        required_monthly_investment_paise=p.required_monthly_investment_paise,
        additional_annual_investment_paise=p.additional_annual_investment_paise,
        events=[
            PlannedEventResponse(
                goal_id=e.goal_id,
                name=e.name,
                target_date=e.target_date.isoformat(),
                age=e.age,
                today_cost_paise=e.today_cost_paise,
                inflated_cost_paise=e.inflated_cost_paise,
                inflation_pct_used=(
                    str(e.inflation_pct_used) if e.inflation_pct_used is not None else None
                ),
                excluded_reason=e.excluded_reason,
            )
            for e in p.events
        ],
        years=[
            RetirementYearResponse(
                age=y.age,
                phase=y.phase,
                opening_corpus_paise=y.opening_corpus_paise,
                investment_paise=y.investment_paise,
                return_paise=y.return_paise,
                withdrawal_paise=y.withdrawal_paise,
                lump_sum_paise=y.lump_sum_paise,
                lump_sum_shortfall_paise=y.lump_sum_shortfall_paise,
                closing_corpus_paise=y.closing_corpus_paise,
            )
            for y in p.years
        ],
    )


@router.get("/projection", response_model=RetirementProjectionResponse)
def get_retirement_projection(
    session: Session = Depends(get_session),
    retirement_age: int | None = None,
    annual_investment_rupees: str | None = None,
    monthly_withdrawal_today_rupees: str | None = None,
    return_delta_pct: str | None = None,
    inflation_delta_pct: str | None = None,
) -> RetirementProjectionResponse:
    """What-if parameters (spec §25) are applied to a **detached** copy of the
    stored plan and never written back -- exploring "what if I retired at 62"
    must not quietly become "I retire at 62". Omit them all for the saved
    plan as-is.
    """
    profile = get_risk_profile(session)
    if profile is None:
        raise HTTPException(
            status_code=404,
            detail="No risk profile set — complete onboarding first (POST /risk-profile)",
        )

    overrides: dict[str, object] = {}
    try:
        if retirement_age is not None:
            overrides["retirement_age"] = retirement_age
        if annual_investment_rupees is not None:
            overrides["annual_investment_paise"] = to_paise_strict(
                Decimal(annual_investment_rupees)
            )
        if monthly_withdrawal_today_rupees is not None:
            overrides["monthly_withdrawal_today_paise"] = to_paise_strict(
                Decimal(monthly_withdrawal_today_rupees)
            )
        deltas = (
            Decimal(return_delta_pct) if return_delta_pct is not None else Decimal(0),
            Decimal(inflation_delta_pct) if inflation_delta_pct is not None else Decimal(0),
        )
    except InvalidOperation as exc:
        raise HTTPException(status_code=422, detail=f"Invalid numeric override: {exc}") from exc

    if overrides:
        profile = detached_profile_with(session, profile, **overrides)

    assumptions = get_current_goal_assumptions(session)
    return _to_response(
        compute_retirement_projection(
            session,
            profile,
            assumptions,
            return_delta_pct=deltas[0],
            inflation_delta_pct=deltas[1],
        )
    )


class RetirementScenarioResponse(BaseModel):
    key: str
    label: str
    return_delta_pct: str
    inflation_delta_pct: str
    projection: RetirementProjectionResponse


@router.get("/scenarios", response_model=list[RetirementScenarioResponse])
def get_retirement_scenarios(
    session: Session = Depends(get_session),
) -> list[RetirementScenarioResponse]:
    profile = get_risk_profile(session)
    if profile is None:
        raise HTTPException(
            status_code=404,
            detail="No risk profile set — complete onboarding first (POST /risk-profile)",
        )
    assumptions = get_current_goal_assumptions(session)
    scenario_assumptions = get_current_scenario_assumptions(session)
    return [
        RetirementScenarioResponse(
            key=s.key,
            label=s.label,
            return_delta_pct=str(s.return_delta_pct),
            inflation_delta_pct=str(s.inflation_delta_pct),
            projection=_to_response(s.projection),
        )
        for s in compute_retirement_scenarios(
            session,
            profile,
            assumptions,
            scenario_assumptions.return_delta_pct,
            scenario_assumptions.inflation_delta_pct,
        )
    ]
