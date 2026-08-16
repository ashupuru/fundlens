"""PRD §5.7: assumption registry CRUD — tax thresholds and the LTCG
exemption limit as versioned, editable config."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from fundlens.api.deps import get_session
from fundlens.assumptions.service import (
    create_version,
    get_current_version,
    get_values,
    list_versions,
)
from fundlens.db.models import AssumptionVersion

router = APIRouter(prefix="/assumptions", tags=["assumptions"])


class AssumptionVersionResponse(BaseModel):
    id: str
    label: str
    created_at: str
    is_current: bool
    values: dict[str, str]


def _to_response(session: Session, version: AssumptionVersion) -> AssumptionVersionResponse:
    return AssumptionVersionResponse(
        id=version.id,
        label=version.label,
        created_at=version.created_at.isoformat(),
        is_current=version.is_current,
        values=get_values(session, version.id),
    )


@router.get("/current", response_model=AssumptionVersionResponse)
def current(session: Session = Depends(get_session)) -> AssumptionVersionResponse:
    return _to_response(session, get_current_version(session))


@router.get("/versions", response_model=list[AssumptionVersionResponse])
def versions(session: Session = Depends(get_session)) -> list[AssumptionVersionResponse]:
    return [_to_response(session, v) for v in list_versions(session)]


class CreateAssumptionVersionRequest(BaseModel):
    label: str
    overrides: dict[str, str] = {}


@router.post("", response_model=AssumptionVersionResponse)
def create(
    request: CreateAssumptionVersionRequest, session: Session = Depends(get_session)
) -> AssumptionVersionResponse:
    try:
        new_version = create_version(session, request.label, request.overrides)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _to_response(session, new_version)
