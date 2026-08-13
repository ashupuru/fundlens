"""Dezerv "BANK BOOK" CSV import: a two-step preview-then-confirm flow
(ingestion/dezerv_service.py has the full rationale) rather than a single
upload, since this format's scheme names need fuzzy-matching against AMFI's
real feed and a wrong auto-match would silently corrupt cost-basis/tax
numbers."""

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from fundlens.api.deps import get_session
from fundlens.ingestion.dezerv_csv import DezervFormatError
from fundlens.ingestion.dezerv_service import commit_dezerv_csv, preview_dezerv_csv

router = APIRouter(prefix="/ingest/dezerv", tags=["ingestion"])


class MatchCandidateResponse(BaseModel):
    amfi_code: str
    scheme_name: str
    isin: str | None
    score: float


class DezervSchemePreviewResponse(BaseModel):
    security_name: str
    symbol_code: str
    txn_count: int
    matched_amfi_code: str | None
    matched_scheme_name: str | None
    matched_isin: str | None
    match_score: float | None
    reason: str | None
    suggestions: list[MatchCandidateResponse]


class DezervPreviewResponse(BaseModel):
    schemes: list[DezervSchemePreviewResponse]
    unrecognized_transaction_descriptions: list[str]
    total_mfdemat_rows: int
    date_range_start: str | None
    date_range_end: str | None


@router.post("/preview", response_model=DezervPreviewResponse)
async def preview(file: UploadFile = File(...)) -> DezervPreviewResponse:
    content = (await file.read()).decode("utf-8")
    try:
        result = preview_dezerv_csv(content)
    except DezervFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return DezervPreviewResponse(
        schemes=[
            DezervSchemePreviewResponse(
                security_name=s.security_name,
                symbol_code=s.symbol_code,
                txn_count=s.txn_count,
                matched_amfi_code=s.matched.amfi_code if s.matched else None,
                matched_scheme_name=s.matched.scheme_name if s.matched else None,
                matched_isin=s.matched.isin if s.matched else None,
                match_score=s.matched.score if s.matched else None,
                reason=s.reason,
                suggestions=[
                    MatchCandidateResponse(
                        amfi_code=c.amfi_code, scheme_name=c.scheme_name, isin=c.isin,
                        score=c.score,
                    )
                    for c in s.suggestions
                ],
            )
            for s in result.schemes
        ],
        unrecognized_transaction_descriptions=result.unrecognized_transaction_descriptions,
        total_mfdemat_rows=result.total_mfdemat_rows,
        date_range_start=result.date_range[0].isoformat() if result.date_range else None,
        date_range_end=result.date_range[1].isoformat() if result.date_range else None,
    )


class DezervCommitResponse(BaseModel):
    upload_id: str
    inserted_count: int
    duplicate_count: int
    skipped_count: int
    skipped_reasons: dict[str, int]


@router.post("/commit", response_model=DezervCommitResponse)
async def commit(
    file: UploadFile = File(...),
    mapping: str = Form(...),
    session: Session = Depends(get_session),
) -> DezervCommitResponse:
    content = (await file.read()).decode("utf-8")
    try:
        mapping_dict: dict[str, str | None] = json.loads(mapping)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid mapping JSON: {exc}") from exc

    try:
        result = commit_dezerv_csv(
            session, content, mapping_dict, source_filename=file.filename
        )
    except DezervFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return DezervCommitResponse(
        upload_id=result.upload_id,
        inserted_count=result.inserted_count,
        duplicate_count=result.duplicate_count,
        skipped_count=result.skipped_count,
        skipped_reasons=result.skipped_reasons,
    )
