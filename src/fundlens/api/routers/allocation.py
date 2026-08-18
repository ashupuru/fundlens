"""PRD §3/FR-7.1: risk profile onboarding + actual-vs-target allocation drift."""

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from fundlens.allocation.service import get_risk_profile, set_risk_profile
from fundlens.api.deps import get_session
from fundlens.compute.allocation import compute_allocation_drift
from fundlens.compute.glide_path import compute_glide_path_suggestion
from fundlens.db.models import Persona, RiskProfile

router = APIRouter(tags=["allocation"])


class RiskProfileResponse(BaseModel):
    persona: str
    target_equity_pct: str
    target_debt_pct: str
    horizon_years: int | None
    drawdown_tolerance: str | None
    income_stability: str | None
    has_dependents: bool | None
    updated_at: str


def _to_response(profile: RiskProfile) -> RiskProfileResponse:
    return RiskProfileResponse(
        persona=profile.persona.value,
        target_equity_pct=str(profile.target_equity_pct),
        target_debt_pct=str(profile.target_debt_pct),
        horizon_years=profile.horizon_years,
        drawdown_tolerance=profile.drawdown_tolerance,
        income_stability=profile.income_stability,
        has_dependents=profile.has_dependents,
        updated_at=profile.updated_at.isoformat(),
    )


@router.get("/risk-profile", response_model=RiskProfileResponse | None)
def risk_profile(session: Session = Depends(get_session)) -> RiskProfileResponse | None:
    profile = get_risk_profile(session)
    return _to_response(profile) if profile is not None else None


class SetRiskProfileRequest(BaseModel):
    persona: str
    target_equity_pct: str | None = None
    target_debt_pct: str | None = None
    horizon_years: int | None = None
    drawdown_tolerance: str | None = None
    income_stability: str | None = None
    has_dependents: bool | None = None


@router.post("/risk-profile", response_model=RiskProfileResponse)
def set_profile(
    request: SetRiskProfileRequest, session: Session = Depends(get_session)
) -> RiskProfileResponse:
    try:
        persona = Persona(request.persona)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"Unknown persona '{request.persona}'"
        ) from exc

    target_equity = (
        Decimal(request.target_equity_pct) if request.target_equity_pct is not None else None
    )
    target_debt = (
        Decimal(request.target_debt_pct) if request.target_debt_pct is not None else None
    )

    try:
        profile = set_risk_profile(
            session,
            persona=persona,
            target_equity_pct=target_equity,
            target_debt_pct=target_debt,
            horizon_years=request.horizon_years,
            drawdown_tolerance=request.drawdown_tolerance,
            income_stability=request.income_stability,
            has_dependents=request.has_dependents,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _to_response(profile)


class EquityCapBreakdownResponse(BaseModel):
    large_paise: int
    mid_paise: int
    small_paise: int
    large_and_mid_paise: int
    other_paise: int


class GlidePathSuggestionResponse(BaseModel):
    years_remaining: float | None
    current_target_equity_pct: float
    current_target_debt_pct: float
    suggested_target_equity_pct: float | None
    suggested_target_debt_pct: float | None
    drift_pp: float | None
    reason: str | None


class AllocationDriftResponse(BaseModel):
    persona: str
    target_equity_pct: float
    target_debt_pct: float
    actual_equity_pct: float | None
    actual_debt_pct: float | None
    actual_other_pct: float | None
    equity_drift_pct: float | None
    debt_drift_pct: float | None
    priced_portfolio_value_paise: int
    equity_value_paise: int
    debt_value_paise: int
    other_value_paise: int
    value_unavailable_paise: int
    equity_cap_breakdown: EquityCapBreakdownResponse
    schemes_missing_metadata: int
    glide_path: GlidePathSuggestionResponse


@router.get("/allocation/drift", response_model=AllocationDriftResponse)
def drift(session: Session = Depends(get_session)) -> AllocationDriftResponse:
    profile = get_risk_profile(session)
    if profile is None:
        raise HTTPException(
            status_code=404,
            detail="No risk profile set — complete onboarding first (POST /risk-profile)",
        )

    d = compute_allocation_drift(session, profile)
    g = compute_glide_path_suggestion(profile)
    return AllocationDriftResponse(
        persona=d.persona.value,
        target_equity_pct=d.target_equity_pct,
        target_debt_pct=d.target_debt_pct,
        actual_equity_pct=d.actual_equity_pct,
        actual_debt_pct=d.actual_debt_pct,
        actual_other_pct=d.actual_other_pct,
        equity_drift_pct=d.equity_drift_pct,
        debt_drift_pct=d.debt_drift_pct,
        priced_portfolio_value_paise=d.priced_portfolio_value_paise,
        equity_value_paise=d.equity_value_paise,
        debt_value_paise=d.debt_value_paise,
        other_value_paise=d.other_value_paise,
        value_unavailable_paise=d.value_unavailable_paise,
        equity_cap_breakdown=EquityCapBreakdownResponse(
            large_paise=d.equity_cap_breakdown.large_paise,
            mid_paise=d.equity_cap_breakdown.mid_paise,
            small_paise=d.equity_cap_breakdown.small_paise,
            large_and_mid_paise=d.equity_cap_breakdown.large_and_mid_paise,
            other_paise=d.equity_cap_breakdown.other_paise,
        ),
        schemes_missing_metadata=d.schemes_missing_metadata,
        glide_path=GlidePathSuggestionResponse(
            years_remaining=g.years_remaining,
            current_target_equity_pct=g.current_target_equity_pct,
            current_target_debt_pct=g.current_target_debt_pct,
            suggested_target_equity_pct=g.suggested_target_equity_pct,
            suggested_target_debt_pct=g.suggested_target_debt_pct,
            drift_pp=g.drift_pp,
            reason=g.reason,
        ),
    )
