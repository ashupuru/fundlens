from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.db.models import RawCASUpload, RawTransaction
from fundlens.ingestion import dezerv_service
from fundlens.market_data import service as market_data_service

_HEADER = (
    "Code,Name,Bank Account,Bank Name,Transaction Description,Tran Date,Set Date,"
    "Tran Account,Symbol Code,Security,Buy/Sell Amount,Income,Expenses,Dep/With,"
    "Balance,Custodian Account,Tran Ref.,Desc/Notes,Account Code "
)


def _content(rows: str) -> str:
    preamble = (
        "Dezerv Investments Private Limited,,,,,,,,,,,,,,,,,,\n"
        '"Unit No. 301, Trade Centre,\n'
        "Bandra East,\n"
        'Mumbai - 400051",,,,,,,,,,,,,,,,,,\n'
        "BANK BOOK,,,,,,,,,,,,,,,,,,\n"
        "From 14/06/2023 To 12/08/2026,,,,,,,,,,,,,,,,,,\n"
        "dezerv. Equity Revival Strategy,,,,,,,,,,,,,,,,,,\n"
    )
    return preamble + _HEADER + "\n" + rows


_MATCHED_ROW = (
    "1,Test Investor,ICICI-1,ICICI Bank,Buy,14/06/2023,14/06/2023,MFDEMAT,ABC,"
    'Acme Focused Direct-G,"-100,000.00",0.00,0.00,0.00,"900,000.00",NSDL-TEST-1,,,1\n'
)
_UNMATCHED_ROW = (
    "1,Test Investor,ICICI-1,ICICI Bank,Buy,15/06/2023,15/06/2023,MFDEMAT,XYZ,"
    'Totally Unknown Scheme Direct-G,"-50,000.00",0.00,0.00,0.00,"850,000.00",NSDL-TEST-1,,,1\n'
)
_UNRECOGNIZED_DESC_ROW = (
    "1,Test Investor,ICICI-1,ICICI Bank,Dividend,20/06/2023,20/06/2023,MFDEMAT,ABC,"
    'Acme Focused Direct-G,100.00,0.00,0.00,0.00,"900,100.00",NSDL-TEST-1,,,1\n'
)

_NAVALL_TEXT = (
    "999901;INF000A00001;INF000A00002;Acme Focused Fund-Direct Plan-Growth;150.0000;14-Jun-2023\n"
)

_MFAPI_PAYLOAD = {
    "meta": {"fund_house": "Acme Mutual Fund"},
    "data": [
        {"date": "14-06-2023", "nav": "150.0000"},
        {"date": "01-06-2023", "nav": "148.0000"},
    ],
}


@pytest.fixture(autouse=True)
def _patch_market_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dezerv_service, "fetch_navall_text", lambda: _NAVALL_TEXT)
    monkeypatch.setattr(dezerv_service, "fetch_scheme_history", lambda code: _MFAPI_PAYLOAD)
    monkeypatch.setattr(
        market_data_service, "fetch_scheme_history", lambda code: _MFAPI_PAYLOAD
    )


def test_preview_reports_matched_and_unmatched_schemes() -> None:
    content = _content(_MATCHED_ROW + _UNMATCHED_ROW)
    preview = dezerv_service.preview_dezerv_csv(content)

    by_name = {s.security_name: s for s in preview.schemes}
    assert by_name["Acme Focused Direct-G"].matched is not None
    assert by_name["Acme Focused Direct-G"].matched.amfi_code == "999901"
    assert by_name["Totally Unknown Scheme Direct-G"].matched is None
    assert by_name["Totally Unknown Scheme Direct-G"].reason == "no_candidates"
    assert preview.total_mfdemat_rows == 2


def test_preview_reports_unrecognized_transaction_descriptions() -> None:
    content = _content(_MATCHED_ROW + _UNRECOGNIZED_DESC_ROW)
    preview = dezerv_service.preview_dezerv_csv(content)

    assert preview.unrecognized_transaction_descriptions == ["Dividend"]
    assert preview.total_mfdemat_rows == 1  # the unrecognized-description row isn't a txn row


def test_commit_inserts_transaction_with_nav_based_units(session: Session) -> None:
    content = _content(_MATCHED_ROW)
    mapping = {"Acme Focused Direct-G": "999901"}

    result = dezerv_service.commit_dezerv_csv(session, content, mapping, source_filename="test.csv")

    assert result.inserted_count == 1
    assert result.duplicate_count == 0
    assert result.skipped_count == 0

    upload = session.get(RawCASUpload, result.upload_id)
    assert upload is not None
    assert upload.parser_version == "dezerv-csv-v1"

    txn = session.scalars(select(RawTransaction)).one()
    assert txn.folio == "NSDL-TEST-1"
    assert txn.amfi_code == "999901"
    assert txn.amc == "Acme Mutual Fund"
    assert txn.pan is None
    assert txn.units == Decimal("666.6667")  # 100,000 / 150 NAV, rounded to the column's scale
    assert txn.amount_paise == 10_000_000


def test_commit_skips_row_with_no_mapping(session: Session) -> None:
    content = _content(_MATCHED_ROW)
    mapping: dict[str, str | None] = {"Acme Focused Direct-G": None}

    result = dezerv_service.commit_dezerv_csv(session, content, mapping)

    assert result.inserted_count == 0
    assert result.skipped_count == 1
    assert result.skipped_reasons == {"Acme Focused Direct-G": 1}
    assert session.scalars(select(RawTransaction)).all() == []


def test_commit_skips_row_with_no_nav_on_or_before(session: Session) -> None:
    row = (
        "1,Test Investor,ICICI-1,ICICI Bank,Buy,01/01/2020,01/01/2020,MFDEMAT,ABC,"
        'Acme Focused Direct-G,"-100,000.00",0.00,0.00,0.00,"900,000.00",NSDL-TEST-1,,,1\n'
    )
    content = _content(row)
    mapping = {"Acme Focused Direct-G": "999901"}

    result = dezerv_service.commit_dezerv_csv(session, content, mapping)

    assert result.inserted_count == 0
    assert result.skipped_count == 1
    assert session.scalars(select(RawTransaction)).all() == []


def test_recommitting_same_file_and_mapping_is_idempotent(session: Session) -> None:
    content = _content(_MATCHED_ROW)
    mapping = {"Acme Focused Direct-G": "999901"}

    first = dezerv_service.commit_dezerv_csv(session, content, mapping)
    second = dezerv_service.commit_dezerv_csv(session, content, mapping)

    assert second.upload_id == first.upload_id
    assert second.inserted_count == 0
    assert len(session.scalars(select(RawTransaction)).all()) == 1


def test_recommitting_with_corrected_mapping_is_treated_as_new_upload(session: Session) -> None:
    content = _content(_MATCHED_ROW)

    first = dezerv_service.commit_dezerv_csv(session, content, {"Acme Focused Direct-G": None})
    assert first.inserted_count == 0

    second = dezerv_service.commit_dezerv_csv(
        session, content, {"Acme Focused Direct-G": "999901"}
    )
    assert second.upload_id != first.upload_id
    assert second.inserted_count == 1
