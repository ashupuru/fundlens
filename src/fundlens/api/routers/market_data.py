"""FR-1.4: trigger a NAV refresh for every scheme referenced by ingested
transactions; also fronts the Groww fund-metadata refresh (AUM/rating/
returns), a separate best-effort third-party source (market_data/
groww_service.py)."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from fundlens.api.deps import get_session
from fundlens.market_data.groww_service import refresh_groww_metadata
from fundlens.market_data.service import refresh_all_held_schemes

router = APIRouter(prefix="/market-data", tags=["market-data"])


class RefreshResponse(BaseModel):
    backfilled_schemes: int
    backfilled_rows: int
    amfi_refresh_rows: int
    amfi_refresh_ok: bool
    amfi_error: str | None


@router.post("/refresh", response_model=RefreshResponse)
def refresh(session: Session = Depends(get_session)) -> RefreshResponse:
    summary = refresh_all_held_schemes(session)
    return RefreshResponse(
        backfilled_schemes=summary.backfilled_schemes,
        backfilled_rows=summary.backfilled_rows,
        amfi_refresh_rows=summary.amfi_refresh_rows,
        amfi_refresh_ok=summary.amfi_refresh_ok,
        amfi_error=summary.amfi_error,
    )


class GrowwRefreshResponse(BaseModel):
    rows_upserted: int
    ok: bool
    error: str | None


@router.post("/refresh-groww", response_model=GrowwRefreshResponse)
def refresh_groww(session: Session = Depends(get_session)) -> GrowwRefreshResponse:
    summary = refresh_groww_metadata(session)
    return GrowwRefreshResponse(
        rows_upserted=summary.rows_upserted, ok=summary.ok, error=summary.error
    )
