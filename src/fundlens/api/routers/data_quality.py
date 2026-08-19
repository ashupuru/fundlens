"""PRD §5.4/P1 data quality dashboard, pulled forward to P0.6: folios found,
historical-coverage gaps, unresolved scheme identities, and failed XIRR
computations, so trust in the portfolio numbers has visible provenance."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from fundlens.api.deps import get_session
from fundlens.compute.data_quality import (
    get_coverage_gaps,
    get_cross_source_duplicates,
    get_data_freshness,
    get_failed_xirr_positions,
    get_folio_summary,
    get_unmatched_schemes,
)

router = APIRouter(prefix="/data-quality", tags=["data-quality"])


class FolioSummaryResponse(BaseModel):
    folio: str
    amc: str
    pan: str | None
    scheme_count: int
    txn_count: int
    first_txn_date: str
    last_txn_date: str


class CoverageGapResponse(BaseModel):
    folio: str
    scheme_name: str
    opening_balance_units: str
    message: str
    source_filename: str | None
    detected_at: str


class UnmatchedSchemeResponse(BaseModel):
    folio: str
    scheme_name: str
    amc: str
    isin: str | None
    amfi_code: str | None


class FailedXirrPositionResponse(BaseModel):
    folio: str
    scheme_name: str
    amc: str
    xirr_reason: str | None
    is_fully_exited: bool
    less_than_one_year: bool


class DataFreshnessResponse(BaseModel):
    source: str
    last_attempt_at: str
    last_success_at: str | None
    status: str
    error_message: str | None
    rows_updated: int | None


class DuplicateSideResponse(BaseModel):
    folio: str
    source: str
    amount_paise: int | None


class CrossSourceDuplicateResponse(BaseModel):
    scheme_name: str
    txn_date: str
    direction: str
    units: str
    rows: list[DuplicateSideResponse]
    resolvable: bool


class DataQualitySummaryResponse(BaseModel):
    folios: list[FolioSummaryResponse]
    coverage_gaps: list[CoverageGapResponse]
    unmatched_schemes: list[UnmatchedSchemeResponse]
    failed_xirr_positions: list[FailedXirrPositionResponse]
    data_freshness: list[DataFreshnessResponse]
    cross_source_duplicates: list[CrossSourceDuplicateResponse]


@router.get("/summary", response_model=DataQualitySummaryResponse)
def summary(session: Session = Depends(get_session)) -> DataQualitySummaryResponse:
    return DataQualitySummaryResponse(
        folios=[
            FolioSummaryResponse(
                folio=f.folio,
                amc=f.amc,
                pan=f.pan,
                scheme_count=f.scheme_count,
                txn_count=f.txn_count,
                first_txn_date=f.first_txn_date.isoformat(),
                last_txn_date=f.last_txn_date.isoformat(),
            )
            for f in get_folio_summary(session)
        ],
        coverage_gaps=[
            CoverageGapResponse(
                folio=g.folio,
                scheme_name=g.scheme_name,
                opening_balance_units=str(g.opening_balance_units),
                message=g.message,
                source_filename=g.source_filename,
                detected_at=g.detected_at.isoformat(),
            )
            for g in get_coverage_gaps(session)
        ],
        unmatched_schemes=[
            UnmatchedSchemeResponse(
                folio=h.folio,
                scheme_name=h.scheme_name,
                amc=h.amc,
                isin=h.isin,
                amfi_code=h.amfi_code,
            )
            for h in get_unmatched_schemes(session)
        ],
        failed_xirr_positions=[
            FailedXirrPositionResponse(
                folio=m.folio,
                scheme_name=m.scheme_name,
                amc=m.amc,
                xirr_reason=m.xirr_reason,
                is_fully_exited=m.is_fully_exited,
                less_than_one_year=m.less_than_one_year,
            )
            for m in get_failed_xirr_positions(session)
        ],
        cross_source_duplicates=[
            CrossSourceDuplicateResponse(
                scheme_name=d.scheme_name,
                txn_date=d.txn_date.isoformat(),
                direction=d.direction,
                units=str(d.units),
                rows=[
                    DuplicateSideResponse(
                        folio=r.folio, source=r.source, amount_paise=r.amount_paise
                    )
                    for r in d.rows
                ],
                resolvable=d.removable is not None,
            )
            for d in get_cross_source_duplicates(session)
        ],
        data_freshness=[
            DataFreshnessResponse(
                source=fr.source,
                last_attempt_at=fr.last_attempt_at.isoformat(),
                last_success_at=fr.last_success_at.isoformat() if fr.last_success_at else None,
                status=fr.status,
                error_message=fr.error_message,
                rows_updated=fr.rows_updated,
            )
            for fr in get_data_freshness(session)
        ],
    )
