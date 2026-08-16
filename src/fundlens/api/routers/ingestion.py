"""FR-1.1/1.2/1.3: upload a CAS PDF or an escape-hatch CSV of transactions."""

import tempfile
from pathlib import Path

from casparser.exceptions import ParserException
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from fundlens.api.deps import get_session
from fundlens.ingestion import service
from fundlens.ingestion.cas_parser import UnsupportedCASTypeError
from fundlens.ingestion.csv_import import CSVValidationError

router = APIRouter(prefix="/ingest", tags=["ingestion"])


class CoverageGapResponse(BaseModel):
    folio: str
    scheme_name: str
    opening_balance_units: str
    message: str


class IngestResponse(BaseModel):
    upload_id: str
    inserted_count: int
    duplicate_count: int
    coverage_gaps: list[CoverageGapResponse]


def _to_response(result: service.IngestResult) -> IngestResponse:
    return IngestResponse(
        upload_id=result.upload_id,
        inserted_count=result.inserted_count,
        duplicate_count=result.duplicate_count,
        coverage_gaps=[
            CoverageGapResponse(
                folio=gap.folio,
                scheme_name=gap.scheme_name,
                opening_balance_units=str(gap.opening_balance_units),
                message=gap.message,
            )
            for gap in result.coverage_gaps
        ],
    )


@router.post("/cas", response_model=IngestResponse)
async def ingest_cas(
    file: UploadFile = File(...),
    password: str = Form(...),
    session: Session = Depends(get_session),
) -> IngestResponse:
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(await file.read())
        tmp.flush()
        try:
            result = service.ingest_cas_pdf(
                session, Path(tmp.name), password, source_filename=file.filename
            )
        except UnsupportedCASTypeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ParserException as exc:
            raise HTTPException(status_code=422, detail=f"Failed to parse CAS: {exc}") from exc

    return _to_response(result)


@router.post("/csv", response_model=IngestResponse)
async def ingest_csv(
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> IngestResponse:
    content = (await file.read()).decode("utf-8")
    try:
        result = service.ingest_transactions_csv(session, content, source_filename=file.filename)
    except CSVValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return _to_response(result)
