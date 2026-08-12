"""FR-2.1/2.2: rebuild FIFO lots and view current holdings by folio/scheme/AMC."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from fundlens.api.deps import get_session
from fundlens.compute import service
from fundlens.compute.holdings import get_current_holdings, get_holdings_by_amc

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


class SchemeRebuildErrorResponse(BaseModel):
    folio: str
    scheme_key: str
    message: str


class RebuildResponse(BaseModel):
    folios_processed: int
    schemes_processed: int
    lots_created: int
    realized_gains_created: int
    errors: list[SchemeRebuildErrorResponse]


class HoldingResponse(BaseModel):
    folio: str
    scheme_name: str
    amc: str
    isin: str | None
    amfi_code: str | None
    units_remaining: str
    cost_basis_paise: int
    average_cost_per_unit: str


class AmcHoldingResponse(BaseModel):
    amc: str
    scheme_count: int
    cost_basis_paise: int


@router.post("/rebuild-lots", response_model=RebuildResponse)
def rebuild_lots(session: Session = Depends(get_session)) -> RebuildResponse:
    summary = service.rebuild_all_lots(session)
    return RebuildResponse(
        folios_processed=summary.folios_processed,
        schemes_processed=summary.schemes_processed,
        lots_created=summary.lots_created,
        realized_gains_created=summary.realized_gains_created,
        errors=[
            SchemeRebuildErrorResponse(folio=e.folio, scheme_key=e.scheme_key, message=e.message)
            for e in summary.errors
        ],
    )


@router.get("/holdings", response_model=list[HoldingResponse])
def holdings(session: Session = Depends(get_session)) -> list[HoldingResponse]:
    return [
        HoldingResponse(
            folio=h.folio,
            scheme_name=h.scheme_name,
            amc=h.amc,
            isin=h.isin,
            amfi_code=h.amfi_code,
            units_remaining=str(h.units_remaining),
            cost_basis_paise=h.cost_basis_paise,
            average_cost_per_unit=str(h.average_cost_per_unit),
        )
        for h in get_current_holdings(session)
    ]


@router.get("/holdings/by-amc", response_model=list[AmcHoldingResponse])
def holdings_by_amc(session: Session = Depends(get_session)) -> list[AmcHoldingResponse]:
    return [
        AmcHoldingResponse(
            amc=row.amc, scheme_count=row.scheme_count, cost_basis_paise=row.cost_basis_paise
        )
        for row in get_holdings_by_amc(session)
    ]
