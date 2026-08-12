"""Orchestrates a full ingestion: hash, parse, record the upload, merge transactions."""

import hashlib
from dataclasses import dataclass
from pathlib import Path

import casparser
from casparser.exceptions import ParserException
from sqlalchemy.orm import Session

from fundlens.db.models import CoverageGapRecord, RawCASUpload, UploadStatus
from fundlens.ingestion.cas_parser import parse_cas_pdf
from fundlens.ingestion.csv_import import parse_transactions_csv
from fundlens.ingestion.mapping import cas_data_to_parsed
from fundlens.ingestion.merge import merge_transactions
from fundlens.ingestion.records import CoverageGap


@dataclass(frozen=True)
class IngestResult:
    upload_id: str
    inserted_count: int
    duplicate_count: int
    coverage_gaps: list[CoverageGap]


def _find_existing_parsed_upload(session: Session, file_hash: str) -> RawCASUpload | None:
    return (
        session.query(RawCASUpload)
        .filter_by(file_hash=file_hash, status=UploadStatus.PARSED)
        .one_or_none()
    )


def ingest_cas_pdf(
    session: Session, path: Path, password: str, source_filename: str | None = None
) -> IngestResult:
    file_hash = hashlib.sha256(path.read_bytes()).hexdigest()

    existing = _find_existing_parsed_upload(session, file_hash)
    if existing is not None:
        # Byte-identical file already ingested — skip re-parsing entirely.
        return IngestResult(
            upload_id=existing.id, inserted_count=0, duplicate_count=0, coverage_gaps=[]
        )

    try:
        cas_data = parse_cas_pdf(path, password)
    except ParserException:
        session.add(
            RawCASUpload(
                file_hash=file_hash,
                source_filename=source_filename,
                parser_version=casparser.__version__,
                status=UploadStatus.PARSE_FAILED,
            )
        )
        session.commit()
        raise

    parsed = cas_data_to_parsed(cas_data)

    upload = RawCASUpload(
        file_hash=file_hash,
        source_filename=source_filename,
        statement_period_start=parsed.statement_period_start,
        statement_period_end=parsed.statement_period_end,
        parser_version=parsed.parser_version,
        status=UploadStatus.PARSED,
    )
    session.add(upload)
    session.flush()  # assigns upload.id before it's used as a FK below

    merge_result = merge_transactions(session, upload.id, parsed.transactions)
    for gap in parsed.coverage_gaps:
        session.add(
            CoverageGapRecord(
                upload_id=upload.id,
                folio=gap.folio,
                scheme_name=gap.scheme_name,
                opening_balance_units=gap.opening_balance_units,
                message=gap.message,
            )
        )
    session.commit()

    return IngestResult(
        upload_id=upload.id,
        inserted_count=merge_result.inserted_count,
        duplicate_count=merge_result.duplicate_count,
        coverage_gaps=parsed.coverage_gaps,
    )


def ingest_transactions_csv(
    session: Session, content: str, source_filename: str | None = None
) -> IngestResult:
    file_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    existing = _find_existing_parsed_upload(session, file_hash)
    if existing is not None:
        # Byte-identical CSV content already ingested — its transactions are
        # necessarily already merged too, since parsing is deterministic.
        return IngestResult(
            upload_id=existing.id, inserted_count=0, duplicate_count=0, coverage_gaps=[]
        )

    records = parse_transactions_csv(content)

    upload = RawCASUpload(
        file_hash=file_hash,
        source_filename=source_filename,
        parser_version="csv-import-v1",
        status=UploadStatus.PARSED,
    )
    session.add(upload)
    session.flush()

    merge_result = merge_transactions(session, upload.id, records)
    session.commit()

    return IngestResult(
        upload_id=upload.id,
        inserted_count=merge_result.inserted_count,
        duplicate_count=merge_result.duplicate_count,
        coverage_gaps=[],
    )
