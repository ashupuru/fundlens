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

from fundlens.allocation.service import get_risk_profile, set_retirement_profile
from fundlens.api.deps import get_session
from fundlens.compute.retirement import RetirementProjection
from fundlens.db.models import RiskProfile
from fundlens.money import to_paise_strict
from fundlens.planning import service as planning

router = APIRouter(prefix="/retirement", tags=["retirement"])


class RetirementProfileRequest(BaseModel):
    current_age: int | None = None
    retirement_age: int | None = None
    life_expectancy: int | None = None
    annual_investment_rupees: str | None = None
    annual_investment_step_up_pct: str | None = None
    monthly_withdrawal_today_rupees: str | None = None
    post_retirement_return_pct: str | None = None
    emergency_reserve_rupees: str | None = None
    annual_healthcare_today_rupees: str | None = None
    desired_terminal_corpus_today_rupees: str | None = None


class RetirementProfileResponse(BaseModel):
    current_age: int | None
    retirement_age: int | None
    life_expectancy: int | None
    annual_investment_paise: int | None
    annual_investment_step_up_pct: str | None
    monthly_withdrawal_today_paise: int | None
    post_retirement_return_pct: str | None
    emergency_reserve_paise: int | None
    annual_healthcare_today_paise: int | None
    desired_terminal_corpus_today_paise: int | None


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
        emergency_reserve_paise=profile.emergency_reserve_paise,
        annual_healthcare_today_paise=profile.annual_healthcare_today_paise,
        desired_terminal_corpus_today_paise=profile.desired_terminal_corpus_today_paise,
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
            emergency_reserve_paise=(
                to_paise_strict(Decimal(request.emergency_reserve_rupees))
                if request.emergency_reserve_rupees
                else None
            ),
            annual_healthcare_today_paise=(
                to_paise_strict(Decimal(request.annual_healthcare_today_rupees))
                if request.annual_healthcare_today_rupees
                else None
            ),
            desired_terminal_corpus_today_paise=(
                to_paise_strict(Decimal(request.desired_terminal_corpus_today_rupees))
                if request.desired_terminal_corpus_today_rupees
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
    healthcare_paise: int
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
    total_portfolio_paise: int | None
    emergency_reserve_paise: int | None
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
    first_year_annual_withdrawal_paise: int | None
    depletion_age: int | None
    terminal_corpus_paise: int | None
    total_lump_sum_paise: int | None
    total_lump_sum_shortfall_paise: int | None
    first_year_annual_healthcare_paise: int | None
    healthcare_inflation_pct: str | None
    desired_terminal_corpus_paise: int | None
    terminal_corpus_target_met: bool | None
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
        total_portfolio_paise=p.total_portfolio_paise,
        emergency_reserve_paise=p.emergency_reserve_paise,
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
        first_year_annual_withdrawal_paise=p.first_year_annual_withdrawal_paise,
        depletion_age=p.depletion_age,
        terminal_corpus_paise=p.terminal_corpus_paise,
        total_lump_sum_paise=p.total_lump_sum_paise,
        total_lump_sum_shortfall_paise=p.total_lump_sum_shortfall_paise,
        first_year_annual_healthcare_paise=p.first_year_annual_healthcare_paise,
        healthcare_inflation_pct=(
            str(p.healthcare_inflation_pct) if p.healthcare_inflation_pct is not None else None
        ),
        desired_terminal_corpus_paise=p.desired_terminal_corpus_paise,
        terminal_corpus_target_met=p.terminal_corpus_target_met,
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
                healthcare_paise=y.healthcare_paise,
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
    try:
        investment_paise = (
            to_paise_strict(Decimal(annual_investment_rupees))
            if annual_investment_rupees is not None
            else None
        )
        withdrawal_paise = (
            to_paise_strict(Decimal(monthly_withdrawal_today_rupees))
            if monthly_withdrawal_today_rupees is not None
            else None
        )
        returns_delta = Decimal(return_delta_pct) if return_delta_pct is not None else Decimal(0)
        inflation_delta = (
            Decimal(inflation_delta_pct) if inflation_delta_pct is not None else Decimal(0)
        )
    except InvalidOperation as exc:
        raise HTTPException(status_code=422, detail=f"Invalid numeric override: {exc}") from exc

    try:
        projection = planning.project_retirement(
            session,
            retirement_age=retirement_age,
            annual_investment_paise=investment_paise,
            monthly_withdrawal_today_paise=withdrawal_paise,
            return_delta_pct=returns_delta,
            inflation_delta_pct=inflation_delta,
        )
    except planning.PlanningUnavailable as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _to_response(projection)


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
    try:
        scenarios = planning.retirement_scenarios(session)
    except planning.PlanningUnavailable as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [
        RetirementScenarioResponse(
            key=s.key,
            label=s.label,
            return_delta_pct=str(s.return_delta_pct),
            inflation_delta_pct=str(s.inflation_delta_pct),
            projection=_to_response(s.projection),
        )
        for s in scenarios
    ]


class RetirementMonteCarloResponse(BaseModel):
    simulations_run: int
    success_probability_pct: float
    median_depletion_age: int | None
    percentile_10_terminal_corpus_paise: int
    percentile_50_terminal_corpus_paise: int
    percentile_90_terminal_corpus_paise: int
    volatility_pct: str


@router.get("/monte-carlo", response_model=RetirementMonteCarloResponse)
def get_retirement_monte_carlo(
    session: Session = Depends(get_session),
) -> RetirementMonteCarloResponse:
    """Its own endpoint rather than folded into the projection: 5,000
    simulated paths is a different cost tier from the projection's few dozen
    rows of arithmetic, and the frontend triggers it explicitly (same
    precedent as the goal Monte Carlo and POST /ai/analyze)."""
    try:
        simulation = planning.retirement_monte_carlo(session)
    except planning.PlanningUnavailable as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    result, volatility_pct = simulation.result, simulation.volatility_pct
    return RetirementMonteCarloResponse(
        simulations_run=result.simulations_run,
        success_probability_pct=result.success_probability_pct,
        median_depletion_age=result.median_depletion_age,
        percentile_10_terminal_corpus_paise=result.percentile_10_terminal_corpus_paise,
        percentile_50_terminal_corpus_paise=result.percentile_50_terminal_corpus_paise,
        percentile_90_terminal_corpus_paise=result.percentile_90_terminal_corpus_paise,
        volatility_pct=str(volatility_pct),
    )
