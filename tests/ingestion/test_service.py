from decimal import Decimal
from pathlib import Path

import pytest
from casparser.exceptions import IncorrectPasswordError
from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.db.models import CoverageGapRecord, RawCASUpload, RawTransaction, UploadStatus
from fundlens.ingestion import service
from tests.ingestion.factories import make_cas_data, make_folio, make_scheme


@pytest.fixture
def fake_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "cas.pdf"
    path.write_bytes(b"fake encrypted pdf bytes")
    return path


def test_ingest_cas_pdf_persists_upload_and_transactions(
    session: Session, fake_pdf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "parse_cas_pdf", lambda path, password: make_cas_data())

    result = service.ingest_cas_pdf(session, fake_pdf, password="ABCDE1234F")

    assert result.inserted_count == 1
    assert result.duplicate_count == 0
    upload = session.get(RawCASUpload, result.upload_id)
    assert upload is not None
    assert upload.status == UploadStatus.PARSED
    assert len(session.scalars(select(RawTransaction)).all()) == 1


def test_reingesting_byte_identical_file_is_a_fast_noop(
    session: Session, fake_pdf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"count": 0}

    def fake_parse(path: Path, password: str) -> object:
        calls["count"] += 1
        return make_cas_data()

    monkeypatch.setattr(service, "parse_cas_pdf", fake_parse)

    first = service.ingest_cas_pdf(session, fake_pdf, password="ABCDE1234F")
    second = service.ingest_cas_pdf(session, fake_pdf, password="ABCDE1234F")

    assert calls["count"] == 1  # second call never re-parses
    assert second.upload_id == first.upload_id
    assert second.inserted_count == 0
    assert len(session.scalars(select(RawTransaction)).all()) == 1


def test_failed_parse_records_parse_failed_status_and_reraises(
    session: Session, fake_pdf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def raise_incorrect_password(path: Path, password: str) -> object:
        raise IncorrectPasswordError("bad password")

    monkeypatch.setattr(service, "parse_cas_pdf", raise_incorrect_password)

    with pytest.raises(IncorrectPasswordError):
        service.ingest_cas_pdf(session, fake_pdf, password="WRONG")

    uploads = session.scalars(select(RawCASUpload)).all()
    assert len(uploads) == 1
    assert uploads[0].status == UploadStatus.PARSE_FAILED


def test_coverage_gaps_are_surfaced_on_result(
    session: Session, fake_pdf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cas = make_cas_data(
        folios=[make_folio(schemes=[make_scheme(open_balance=Decimal("10"))])]
    )
    monkeypatch.setattr(service, "parse_cas_pdf", lambda path, password: cas)

    result = service.ingest_cas_pdf(session, fake_pdf, password="ABCDE1234F")

    assert len(result.coverage_gaps) == 1


def test_coverage_gaps_are_persisted_not_only_returned_transiently(
    session: Session, fake_pdf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cas = make_cas_data(
        folios=[make_folio(schemes=[make_scheme(open_balance=Decimal("10"))])]
    )
    monkeypatch.setattr(service, "parse_cas_pdf", lambda path, password: cas)

    result = service.ingest_cas_pdf(session, fake_pdf, password="ABCDE1234F")

    persisted = session.scalars(select(CoverageGapRecord)).all()
    assert len(persisted) == 1
    assert persisted[0].upload_id == result.upload_id


def test_ingest_transactions_csv_persists_upload_and_transaction(session: Session) -> None:
    csv_content = (
        "folio,amc,scheme_name,txn_type,txn_date,amount,units,isin\n"
        "F1,Test AMC,Feeder Fund,PURCHASE,2021-04-15,5000.00,100.5,INF999\n"
    )

    result = service.ingest_transactions_csv(session, csv_content)

    assert result.inserted_count == 1
    upload = session.get(RawCASUpload, result.upload_id)
    assert upload is not None
    assert upload.parser_version == "csv-import-v1"


def test_reingesting_byte_identical_csv_is_a_fast_noop(session: Session) -> None:
    csv_content = (
        "folio,amc,scheme_name,txn_type,txn_date,amount,units,isin\n"
        "F1,Test AMC,Feeder Fund,PURCHASE,2021-04-15,5000.00,100.5,INF999\n"
    )

    first = service.ingest_transactions_csv(session, csv_content)
    second = service.ingest_transactions_csv(session, csv_content)

    assert second.upload_id == first.upload_id
    assert second.inserted_count == 0
    assert second.duplicate_count == 0
    assert len(session.scalars(select(RawTransaction)).all()) == 1
