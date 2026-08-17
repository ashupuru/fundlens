"""FR-2.1/2.2/3.1/3.2: rebuild FIFO lots, view current holdings, and return
metrics (XIRR, CAGR, absolute return, realized/unrealized P&L)."""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from fundlens.api.deps import get_session
from fundlens.compute import metrics, service
from fundlens.compute.holdings import get_current_holdings, get_holdings_by_amc
from fundlens.compute.valuation_history import compute_portfolio_value_history

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


class SchemeRebuildErrorResponse(BaseModel):
    folio: str
    scheme_key: str
    message: str


class SchemeGapFilledResponse(BaseModel):
    folio: str
    scheme_key: str
    units_assumed: str
    message: str


class RebuildResponse(BaseModel):
    folios_processed: int
    schemes_processed: int
    lots_created: int
    realized_gains_created: int
    errors: list[SchemeRebuildErrorResponse]
    gap_filled: list[SchemeGapFilledResponse]


class HoldingResponse(BaseModel):
    folio: str
    scheme_key: str
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
        gap_filled=[
            SchemeGapFilledResponse(
                folio=g.folio,
                scheme_key=g.scheme_key,
                units_assumed=str(g.units_assumed),
                message=g.message,
            )
            for g in summary.gap_filled
        ],
    )


@router.get("/holdings", response_model=list[HoldingResponse])
def holdings(session: Session = Depends(get_session)) -> list[HoldingResponse]:
    return [
        HoldingResponse(
            folio=h.folio,
            scheme_key=h.scheme_key,
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


class HoldingMetricsResponse(BaseModel):
    folio: str
    scheme_name: str
    amc: str
    invested_paise: int
    received_paise: int
    current_value_paise: int | None
    nav_as_of: str | None
    cost_basis_paise: int
    realized_gain_paise: int
    unrealized_gain_paise: int | None
    absolute_return_pct: float | None
    cagr_pct: float | None
    xirr: float | None
    xirr_reason: str | None
    is_fully_exited: bool
    less_than_one_year: bool


class PortfolioMetricsResponse(BaseModel):
    xirr: float | None
    xirr_reason: str | None
    invested_paise: int
    received_paise: int
    current_value_paise: int
    positions_missing_nav: int


@router.get("/metrics", response_model=list[HoldingMetricsResponse])
def holding_metrics(session: Session = Depends(get_session)) -> list[HoldingMetricsResponse]:
    return [
        HoldingMetricsResponse(
            folio=m.folio,
            scheme_name=m.scheme_name,
            amc=m.amc,
            invested_paise=m.invested_paise,
            received_paise=m.received_paise,
            current_value_paise=m.current_value_paise,
            nav_as_of=m.nav_as_of.isoformat() if m.nav_as_of else None,
            cost_basis_paise=m.cost_basis_paise,
            realized_gain_paise=m.realized_gain_paise,
            unrealized_gain_paise=m.unrealized_gain_paise,
            absolute_return_pct=m.absolute_return_pct,
            cagr_pct=m.cagr_pct,
            xirr=m.xirr,
            xirr_reason=m.xirr_reason,
            is_fully_exited=m.is_fully_exited,
            less_than_one_year=m.less_than_one_year,
        )
        for m in metrics.get_all_holding_metrics(session)
    ]


@router.get("/metrics/portfolio", response_model=PortfolioMetricsResponse)
def portfolio_metrics(session: Session = Depends(get_session)) -> PortfolioMetricsResponse:
    m = metrics.compute_portfolio_metrics(session)
    return PortfolioMetricsResponse(
        xirr=m.xirr,
        xirr_reason=m.xirr_reason,
        invested_paise=m.invested_paise,
        received_paise=m.received_paise,
        current_value_paise=m.current_value_paise,
        positions_missing_nav=m.positions_missing_nav,
    )


class CategoryBreakdownItemResponse(BaseModel):
    label: str
    current_value_paise: int
    allocation_pct: float
    xirr: float | None
    xirr_reason: str | None
    fund_count: int


@router.get("/breakdown", response_model=list[CategoryBreakdownItemResponse])
def breakdown(
    level: Literal["category", "sub_category", "amc"] = "category",
    session: Session = Depends(get_session),
) -> list[CategoryBreakdownItemResponse]:
    groups = (
        metrics.compute_amc_breakdown(session)
        if level == "amc"
        else metrics.compute_category_breakdown(session, level)
    )
    return [
        CategoryBreakdownItemResponse(
            label=g.label,
            current_value_paise=g.current_value_paise,
            allocation_pct=g.allocation_pct,
            xirr=g.xirr,
            xirr_reason=g.xirr_reason,
            fund_count=g.fund_count,
        )
        for g in groups
    ]


class ValuePointResponse(BaseModel):
    as_of_date: str
    value_paise: int
    invested_paise: int
    positions_missing_nav: int


@router.get("/value-history", response_model=list[ValuePointResponse])
def value_history(session: Session = Depends(get_session)) -> list[ValuePointResponse]:
    return [
        ValuePointResponse(
            as_of_date=p.as_of_date.isoformat(),
            value_paise=p.value_paise,
            invested_paise=p.invested_paise,
            positions_missing_nav=p.positions_missing_nav,
        )
        for p in compute_portfolio_value_history(session)
    ]
