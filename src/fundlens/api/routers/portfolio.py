"""FR-2.1/2.2/3.1/3.2: rebuild FIFO lots, view current holdings, and return
metrics (XIRR, CAGR, absolute return, realized/unrealized P&L)."""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from fundlens.api.deps import get_session
from fundlens.compute import metrics, service
from fundlens.compute.data_quality import get_cross_source_duplicates
from fundlens.compute.holdings import get_current_holdings, get_holdings_by_amc
from fundlens.compute.valuation_history import compute_portfolio_value_history
from fundlens.db.models import HoldingSourceOverride, RawTransaction
from fundlens.scheme_identity import scheme_key

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
    source: str
    source_is_override: bool
    category_broad: str
    category_specific: str
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
            source=m.source,
            source_is_override=m.source_is_override,
            category_broad=m.category_broad,
            category_specific=m.category_specific,
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


class HoldingSourceRequest(BaseModel):
    folio: str
    scheme_key: str
    # None clears the override, falling back to the derived attribution --
    # absence of a row is the "no override" state, so there's no sentinel.
    source: str | None = None


class HoldingSourceResponse(BaseModel):
    folio: str
    scheme_key: str
    source: str
    source_is_override: bool


@router.put("/holding-source", response_model=HoldingSourceResponse)
def set_holding_source(
    request: HoldingSourceRequest, session: Session = Depends(get_session)
) -> HoldingSourceResponse:
    """Overrides one position's derived source, or clears the override when
    `source` is null.

    Body rather than path parameters: a folio is free-form text that really
    does contain slashes and spaces in real CAS data (e.g. "22629257 / 07"),
    so putting it in the URL would need escaping every caller could get wrong.
    """
    label = (request.source or "").strip()
    existing = session.get(HoldingSourceOverride, (request.folio, request.scheme_key))

    if not label:
        if existing is not None:
            session.delete(existing)
            session.commit()
    elif existing is not None:
        existing.source = label
        session.commit()
    else:
        session.add(
            HoldingSourceOverride(
                folio=request.folio, scheme_key=request.scheme_key, source=label
            )
        )
        session.commit()

    effective = metrics.position_sources(session).get(
        (request.folio, request.scheme_key), (metrics.SOURCE_UNKNOWN, False)
    )
    return HoldingSourceResponse(
        folio=request.folio,
        scheme_key=request.scheme_key,
        source=effective[0],
        source_is_override=effective[1],
    )


class KnownSourcesResponse(BaseModel):
    sources: list[str]


@router.get("/holding-sources", response_model=KnownSourcesResponse)
def known_holding_sources(session: Session = Depends(get_session)) -> KnownSourcesResponse:
    """Every source currently in use, for the edit control's suggestions --
    the built-in labels plus anything the user has typed, so a custom broker
    only has to be spelled once."""
    in_use = {source for source, _ in metrics.position_sources(session).values()}
    return KnownSourcesResponse(
        sources=sorted(in_use | set(metrics.SELECTABLE_SOURCES)),
    )


class DuplicateSideResponse(BaseModel):
    txn_id: str
    folio: str
    source: str
    amount_paise: int | None


class DuplicateResponse(BaseModel):
    amfi_code: str
    scheme_name: str
    txn_date: str
    direction: str
    units: str
    rows: list[DuplicateSideResponse]
    removable_txn_id: str | None
    keep_folio: str | None


class DuplicatePreviewResponse(BaseModel):
    duplicates: list[DuplicateResponse]
    removable_count: int
    removable_amount_paise: int


def _duplicate_responses(session: Session) -> list[DuplicateResponse]:
    return [
        DuplicateResponse(
            amfi_code=d.amfi_code,
            scheme_name=d.scheme_name,
            txn_date=d.txn_date.isoformat(),
            direction=d.direction,
            units=str(d.units),
            rows=[
                DuplicateSideResponse(
                    txn_id=r.txn_id, folio=r.folio, source=r.source,
                    amount_paise=r.amount_paise,
                )
                for r in d.rows
            ],
            removable_txn_id=d.removable.txn_id if d.removable else None,
            keep_folio=d.keep.folio if d.keep else None,
        )
        for d in get_cross_source_duplicates(session)
    ]


@router.get("/duplicates/preview", response_model=DuplicatePreviewResponse)
def preview_duplicates(session: Session = Depends(get_session)) -> DuplicatePreviewResponse:
    """What a cleanup would remove. Writes nothing -- same preview-then-commit
    shape the broker importers use, because the resolve step deletes real
    financial records."""
    duplicates = _duplicate_responses(session)
    removable = [d for d in duplicates if d.removable_txn_id is not None]
    return DuplicatePreviewResponse(
        duplicates=duplicates,
        removable_count=len(removable),
        removable_amount_paise=sum(
            abs(next(r.amount_paise or 0 for r in d.rows if r.txn_id == d.removable_txn_id))
            for d in removable
        ),
    )


class DuplicateResolveResponse(BaseModel):
    deleted_count: int
    deleted_amount_paise: int
    source_overrides_written: int
    rebuild: RebuildResponse
    deleted: list[DuplicateResponse]


@router.post("/duplicates/resolve", response_model=DuplicateResolveResponse)
def resolve_duplicates(session: Session = Depends(get_session)) -> DuplicateResolveResponse:
    """Deletes the broker-side copy of each provable duplicate, keeping the
    CAS record, then rebuilds lots.

    Broker attribution would otherwise be lost -- a fund bought through
    Zerodha whose only Zerodha rows were duplicates would silently become
    "Direct / AMC folio". So for each surviving position a
    `HoldingSourceOverride` is written naming the broker, which is exactly
    what that table exists for.
    """
    duplicates = get_cross_source_duplicates(session)
    resolvable = [d for d in duplicates if d.removable is not None and d.keep is not None]

    deleted_amount = 0
    overrides: dict[tuple[str, str], str] = {}
    for dup in resolvable:
        assert dup.removable is not None and dup.keep is not None
        txn = session.get(RawTransaction, dup.removable.txn_id)
        if txn is None:
            continue
        deleted_amount += abs(txn.amount_paise or 0)
        overrides[(dup.keep.folio, scheme_key(txn.isin, txn.amfi_code, txn.scheme_name))] = (
            dup.removable.source
        )
        session.delete(txn)

    for (folio, key), source in overrides.items():
        # Never overwrite an override the user set themselves: an automated
        # cleanup correcting its own bookkeeping has no business silently
        # relabelling a position someone has already judged.
        if session.get(HoldingSourceOverride, (folio, key)) is None:
            session.add(
                HoldingSourceOverride(folio=folio, scheme_key=key, source=source)
            )
    session.commit()

    return DuplicateResolveResponse(
        deleted_count=len(resolvable),
        deleted_amount_paise=deleted_amount,
        source_overrides_written=len(overrides),
        rebuild=rebuild_lots(session),
        deleted=[
            DuplicateResponse(
                amfi_code=d.amfi_code,
                scheme_name=d.scheme_name,
                txn_date=d.txn_date.isoformat(),
                direction=d.direction,
                units=str(d.units),
                rows=[
                    DuplicateSideResponse(
                        txn_id=r.txn_id, folio=r.folio, source=r.source,
                        amount_paise=r.amount_paise,
                    )
                    for r in d.rows
                ],
                removable_txn_id=d.removable.txn_id if d.removable else None,
                keep_folio=d.keep.folio if d.keep else None,
            )
            for d in resolvable
        ],
    )
