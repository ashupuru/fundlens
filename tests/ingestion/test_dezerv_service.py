from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.db.models import RawCASUpload, RawTransaction, TransactionType, UploadStatus
from fundlens.ingestion import broker_import_shared, dezerv_service
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


def _seed_cas_transaction(
    session: Session,
    *,
    folio: str,
    amfi_code: str,
    txn_date: date,
    txn_type: TransactionType,
    amount_paise: int,
    natural_key: str,
) -> None:
    """Simulates a transaction already present from a prior CAS/CSV import --
    a different `folio` (AMC folio, not the Dezerv demat/custodian account)
    is the whole point, since that's what makes the natural-key dedupe miss
    it (see dezerv_service.py's cross-source-duplicate docstring)."""
    upload = RawCASUpload(file_hash=natural_key, parser_version="1.3.0", status=UploadStatus.PARSED)
    session.add(upload)
    session.flush()
    session.add(
        RawTransaction(
            upload_id=upload.id,
            folio=folio,
            amc="Acme Mutual Fund",
            scheme_name="Acme Focused Fund-Direct Plan-Growth",
            isin="INF000A00001",
            amfi_code=amfi_code,
            txn_type=txn_type,
            txn_date=txn_date,
            units=Decimal("666.6667"),
            amount_paise=amount_paise,
            natural_key=natural_key,
        )
    )
    session.commit()


@pytest.fixture(autouse=True)
def _patch_market_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dezerv_service, "fetch_navall_text", lambda: _NAVALL_TEXT)
    monkeypatch.setattr(broker_import_shared, "fetch_scheme_history", lambda code: _MFAPI_PAYLOAD)
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


def test_commit_skips_row_matching_an_existing_cross_source_transaction(session: Session) -> None:
    # Same scheme/date/type/amount as _MATCHED_ROW, but a different folio --
    # the AMC folio a CAS import would have used, not Dezerv's demat account.
    _seed_cas_transaction(
        session,
        folio="AMC-FOLIO-000123",
        amfi_code="999901",
        txn_date=date(2023, 6, 14),
        txn_type=TransactionType.PURCHASE,
        amount_paise=10_000_000,
        natural_key="preexisting-cas-key",
    )

    content = _content(_MATCHED_ROW)
    mapping = {"Acme Focused Direct-G": "999901"}

    result = dezerv_service.commit_dezerv_csv(session, content, mapping)

    assert result.inserted_count == 0
    assert result.cross_source_duplicate_count == 1
    assert result.cross_source_duplicates == {"Acme Focused Direct-G": 1}
    assert result.skipped_count == 0  # distinct from the no-mapping/no-NAV skip reasons
    # the pre-existing CAS row is untouched, and no second row was added for it
    assert len(session.scalars(select(RawTransaction)).all()) == 1


def test_commit_matches_sip_tagged_cas_transaction_as_buy_equivalent(session: Session) -> None:
    # CAS often tags recurring installments PURCHASE_SIP while Dezerv only
    # distinguishes Buy/Sell -- the two must still be recognized as the same
    # real transaction.
    _seed_cas_transaction(
        session,
        folio="AMC-FOLIO-000123",
        amfi_code="999901",
        txn_date=date(2023, 6, 14),
        txn_type=TransactionType.PURCHASE_SIP,
        amount_paise=10_000_000,
        natural_key="preexisting-sip-key",
    )

    content = _content(_MATCHED_ROW)
    mapping = {"Acme Focused Direct-G": "999901"}

    result = dezerv_service.commit_dezerv_csv(session, content, mapping)

    assert result.inserted_count == 0
    assert result.cross_source_duplicate_count == 1


def test_commit_still_inserts_a_genuinely_new_transaction_for_the_same_scheme(
    session: Session,
) -> None:
    # A pre-existing transaction for the same scheme but a different date
    # must not block a genuinely new Dezerv transaction from being inserted.
    _seed_cas_transaction(
        session,
        folio="AMC-FOLIO-000123",
        amfi_code="999901",
        txn_date=date(2023, 5, 1),
        txn_type=TransactionType.PURCHASE,
        amount_paise=10_000_000,
        natural_key="preexisting-unrelated-key",
    )

    content = _content(_MATCHED_ROW)
    mapping = {"Acme Focused Direct-G": "999901"}

    result = dezerv_service.commit_dezerv_csv(session, content, mapping)

    assert result.inserted_count == 1
    assert result.cross_source_duplicate_count == 0
    assert len(session.scalars(select(RawTransaction)).all()) == 2
