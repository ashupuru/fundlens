"""Zerodha Tradebook (MF segment) CSV import: same two-step
preview-then-confirm flow as `/ingest/dezerv` (`ingestion/zerodha_service.py`
has the full rationale) -- even though this format's ISIN-based matching is
exact rather than fuzzy, a wrong scheme mapping would still silently
corrupt cost-basis/tax numbers, so nothing is written until the user
confirms the proposed mapping."""

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from fundlens.api.deps import get_session
from fundlens.ingestion.zerodha_csv import ZerodhaFormatError
from fundlens.ingestion.zerodha_service import commit_zerodha_csv, preview_zerodha_csv

router = APIRouter(prefix="/ingest/zerodha", tags=["ingestion"])


class ZerodhaSchemePreviewResponse(BaseModel):
    isin: str
    symbol: str
    txn_count: int
    matched_amfi_code: str | None
    matched_scheme_name: str | None
    reason: str | None


class ZerodhaPreviewResponse(BaseModel):
    schemes: list[ZerodhaSchemePreviewResponse]
    unrecognized_trade_types: list[str]
    rows_missing_isin: int
    total_mf_rows: int
    date_range_start: str | None
    date_range_end: str | None


@router.post("/preview", response_model=ZerodhaPreviewResponse)
async def preview(file: UploadFile = File(...)) -> ZerodhaPreviewResponse:
    content = (await file.read()).decode("utf-8")
    try:
        result = preview_zerodha_csv(content)
    except ZerodhaFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return ZerodhaPreviewResponse(
        schemes=[
            ZerodhaSchemePreviewResponse(
                isin=s.isin,
                symbol=s.symbol,
                txn_count=s.txn_count,
                matched_amfi_code=s.matched_amfi_code,
                matched_scheme_name=s.matched_scheme_name,
                reason=s.reason,
            )
            for s in result.schemes
        ],
        unrecognized_trade_types=result.unrecognized_trade_types,
        rows_missing_isin=result.rows_missing_isin,
        total_mf_rows=result.total_mf_rows,
        date_range_start=result.date_range[0].isoformat() if result.date_range else None,
        date_range_end=result.date_range[1].isoformat() if result.date_range else None,
    )


class ZerodhaCommitResponse(BaseModel):
    upload_id: str
    inserted_count: int
    duplicate_count: int
    skipped_count: int
    skipped_reasons: dict[str, int]
    cross_source_duplicate_count: int
    cross_source_duplicates: dict[str, int]


@router.post("/commit", response_model=ZerodhaCommitResponse)
async def commit(
    file: UploadFile = File(...),
    mapping: str = Form(...),
    session: Session = Depends(get_session),
) -> ZerodhaCommitResponse:
    content = (await file.read()).decode("utf-8")
    try:
        mapping_dict: dict[str, str | None] = json.loads(mapping)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid mapping JSON: {exc}") from exc

    try:
        result = commit_zerodha_csv(
            session, content, mapping_dict, source_filename=file.filename
        )
    except ZerodhaFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return ZerodhaCommitResponse(
        upload_id=result.upload_id,
        inserted_count=result.inserted_count,
        duplicate_count=result.duplicate_count,
        skipped_count=result.skipped_count,
        skipped_reasons=result.skipped_reasons,
        cross_source_duplicate_count=result.cross_source_duplicate_count,
        cross_source_duplicates=result.cross_source_duplicates,
    )
