"""PRD FR-8.1/8.2, phase P2.4: goal CRUD, holding tagging, and projections."""

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from fundlens.allocation.service import get_risk_profile
from fundlens.api.deps import get_session
from fundlens.assumptions.service import get_current_goal_assumptions
from fundlens.compute.goal import GoalProjection, compute_goal_projection
from fundlens.db.models import Goal
from fundlens.goals import service
from fundlens.money import to_paise_strict

router = APIRouter(tags=["goals"])


class GoalResponse(BaseModel):
    id: str
    name: str
    target_amount_paise: int
    target_date: date
    created_at: str
    updated_at: str


def _to_response(goal: Goal) -> GoalResponse:
    return GoalResponse(
        id=goal.id,
        name=goal.name,
        target_amount_paise=goal.target_amount_paise,
        target_date=goal.target_date,
        created_at=goal.created_at.isoformat(),
        updated_at=goal.updated_at.isoformat(),
    )


class CreateGoalRequest(BaseModel):
    name: str
    target_amount_rupees: str
    target_date: date


class UpdateGoalRequest(BaseModel):
    name: str | None = None
    target_amount_rupees: str | None = None
    target_date: date | None = None


def _get_goal_or_404(session: Session, goal_id: str) -> Goal:
    goal = service.get_goal(session, goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail=f"No goal with id '{goal_id}'")
    return goal


@router.post("/goals", response_model=GoalResponse)
def create_goal(
    request: CreateGoalRequest, session: Session = Depends(get_session)
) -> GoalResponse:
    try:
        goal = service.create_goal(
            session,
            name=request.name,
            target_amount_paise=to_paise_strict(Decimal(request.target_amount_rupees)),
            target_date=request.target_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _to_response(goal)


@router.get("/goals", response_model=list[GoalResponse])
def list_goals(session: Session = Depends(get_session)) -> list[GoalResponse]:
    return [_to_response(g) for g in service.list_goals(session)]


@router.get("/goals/{goal_id}", response_model=GoalResponse)
def get_goal(goal_id: str, session: Session = Depends(get_session)) -> GoalResponse:
    return _to_response(_get_goal_or_404(session, goal_id))


@router.patch("/goals/{goal_id}", response_model=GoalResponse)
def update_goal(
    goal_id: str, request: UpdateGoalRequest, session: Session = Depends(get_session)
) -> GoalResponse:
    goal = _get_goal_or_404(session, goal_id)
    try:
        goal = service.update_goal(
            session,
            goal,
            name=request.name,
            target_amount_paise=(
                to_paise_strict(Decimal(request.target_amount_rupees))
                if request.target_amount_rupees is not None
                else None
            ),
            target_date=request.target_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _to_response(goal)


@router.delete("/goals/{goal_id}", status_code=204)
def delete_goal(goal_id: str, session: Session = Depends(get_session)) -> None:
    goal = _get_goal_or_404(session, goal_id)
    service.delete_goal(session, goal)


class GoalHoldingRef(BaseModel):
    folio: str
    scheme_key: str


class GoalHoldingsResponse(BaseModel):
    holdings: list[GoalHoldingRef]


@router.put("/goals/{goal_id}/holdings", response_model=GoalHoldingsResponse)
def set_goal_holdings(
    goal_id: str, request: list[GoalHoldingRef], session: Session = Depends(get_session)
) -> GoalHoldingsResponse:
    _get_goal_or_404(session, goal_id)
    rows = service.set_goal_holdings(
        session, goal_id, [(h.folio, h.scheme_key) for h in request]
    )
    return GoalHoldingsResponse(
        holdings=[GoalHoldingRef(folio=r.folio, scheme_key=r.scheme_key) for r in rows]
    )


@router.get("/goals/{goal_id}/holdings", response_model=GoalHoldingsResponse)
def get_goal_holdings(
    goal_id: str, session: Session = Depends(get_session)
) -> GoalHoldingsResponse:
    _get_goal_or_404(session, goal_id)
    rows = service.get_goal_holdings(session, goal_id)
    return GoalHoldingsResponse(
        holdings=[GoalHoldingRef(folio=r.folio, scheme_key=r.scheme_key) for r in rows]
    )


class SensitivityPointResponse(BaseModel):
    return_pct: str
    required_monthly_sip_paise: int


class GoalProjectionResponse(BaseModel):
    goal_id: str
    years_to_target: float
    current_corpus_paise: int
    current_corpus_unavailable_paise: int
    inflated_target_paise: int
    blended_expected_return_pct: str
    projected_corpus_paise: int
    surplus_or_shortfall_paise: int
    required_monthly_sip_paise: int
    sensitivity: list[SensitivityPointResponse]


def _projection_to_response(p: GoalProjection) -> GoalProjectionResponse:
    return GoalProjectionResponse(
        goal_id=p.goal_id,
        years_to_target=p.years_to_target,
        current_corpus_paise=p.current_corpus_paise,
        current_corpus_unavailable_paise=p.current_corpus_unavailable_paise,
        inflated_target_paise=p.inflated_target_paise,
        blended_expected_return_pct=str(p.blended_expected_return_pct),
        projected_corpus_paise=p.projected_corpus_paise,
        surplus_or_shortfall_paise=p.surplus_or_shortfall_paise,
        required_monthly_sip_paise=p.required_monthly_sip_paise,
        sensitivity=[
            SensitivityPointResponse(
                return_pct=str(s.return_pct),
                required_monthly_sip_paise=s.required_monthly_sip_paise,
            )
            for s in p.sensitivity
        ],
    )


@router.get("/goals/{goal_id}/projection", response_model=GoalProjectionResponse)
def get_goal_projection(
    goal_id: str, session: Session = Depends(get_session)
) -> GoalProjectionResponse:
    goal = _get_goal_or_404(session, goal_id)
    profile = get_risk_profile(session)
    if profile is None:
        raise HTTPException(
            status_code=404,
            detail="No risk profile set — complete onboarding first (POST /risk-profile)",
        )
    tagged = {(h.folio, h.scheme_key) for h in service.get_goal_holdings(session, goal_id)}
    assumptions = get_current_goal_assumptions(session)
    projection = compute_goal_projection(session, goal, tagged, profile, assumptions)
    return _projection_to_response(projection)
