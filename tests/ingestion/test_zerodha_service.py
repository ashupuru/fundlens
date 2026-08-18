from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.db.models import RawCASUpload, RawTransaction, TransactionType, UploadStatus
from fundlens.ingestion import broker_import_shared, zerodha_service
from fundlens.market_data import service as market_data_service

_HEADER = (
    "symbol,isin,trade_date,exchange,segment,series,trade_type,auction,quantity,price,"
    "trade_id,order_id,order_execution_time"
)


def _content(rows: str) -> str:
    return _HEADER + "\n" + rows


_MATCHED_ROW = (
    "Acme Focused Fund - Direct Plan,INF000A00001,2023-06-14,BSE,MF,,buy,false,"
    "666.666700,150.000000,1001,1001,2023-06-14T00:00:00\n"
)
_UNMATCHED_ROW = (
    "Totally Unknown Fund - Direct Plan,INF999Z99999,2023-06-15,BSE,MF,,buy,false,"
    "100.000000,50.000000,1002,1002,2023-06-15T00:00:00\n"
)
_UNRECOGNIZED_TYPE_ROW = (
    "Acme Focused Fund - Direct Plan,INF000A00001,2023-06-20,BSE,MF,,switch,false,"
    "1.000000,150.000000,1003,1003,2023-06-20T00:00:00\n"
)

_NAVALL_TEXT = (
    "999901;INF000A00001;INF000A00002;Acme Focused Fund-Direct Plan-Growth;150.0000;14-Jun-2023\n"
)

_MFAPI_PAYLOAD = {
    "meta": {"fund_house": "Acme Mutual Fund"},
    "data": [{"date": "14-06-2023", "nav": "150.0000"}],
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
    """A different `folio` (AMC folio, not Zerodha's constant synthetic
    "ZERODHA" folio) is the whole point -- see broker_import_shared.py's
    cross-source-duplicate docstring."""
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
    monkeypatch.setattr(zerodha_service, "fetch_navall_text", lambda: _NAVALL_TEXT)
    monkeypatch.setattr(broker_import_shared, "fetch_scheme_history", lambda code: _MFAPI_PAYLOAD)
    monkeypatch.setattr(
        market_data_service, "fetch_scheme_history", lambda code: _MFAPI_PAYLOAD
    )


def test_preview_groups_by_isin_and_reports_matched_and_unmatched() -> None:
    content = _content(_MATCHED_ROW + _UNMATCHED_ROW)
    preview = zerodha_service.preview_zerodha_csv(content)

    by_isin = {s.isin: s for s in preview.schemes}
    assert by_isin["INF000A00001"].matched_amfi_code == "999901"
    assert by_isin["INF999Z99999"].matched_amfi_code is None
    assert by_isin["INF999Z99999"].reason == "no_candidates"
    assert preview.total_mf_rows == 2


def test_preview_reports_unrecognized_trade_types() -> None:
    content = _content(_MATCHED_ROW + _UNRECOGNIZED_TYPE_ROW)
    preview = zerodha_service.preview_zerodha_csv(content)

    assert preview.unrecognized_trade_types == ["switch"]
    assert preview.total_mf_rows == 1


def test_commit_inserts_transaction_with_row_quantity_and_price_directly(session: Session) -> None:
    content = _content(_MATCHED_ROW)
    mapping = {"INF000A00001": "999901"}

    result = zerodha_service.commit_zerodha_csv(
        session, content, mapping, source_filename="test.csv"
    )

    assert result.inserted_count == 1
    assert result.duplicate_count == 0
    assert result.skipped_count == 0

    upload = session.get(RawCASUpload, result.upload_id)
    assert upload is not None
    assert upload.parser_version == "zerodha-tradebook-csv-v1"

    txn = session.scalars(select(RawTransaction)).one()
    assert txn.folio == "ZERODHA"
    assert txn.amfi_code == "999901"
    assert txn.amc == "Acme Mutual Fund"
    assert txn.isin == "INF000A00001"
    assert txn.pan is None
    # Given directly by the row, not back-computed from NAV history.
    assert txn.units == Decimal("666.6667")
    assert txn.nav == Decimal("150.000000")
    # 666.6667 * 150 = 100,000.005 -> paise, round-half-up.
    assert txn.amount_paise == 10_000_001


def test_commit_skips_row_with_no_mapping(session: Session) -> None:
    content = _content(_MATCHED_ROW)
    mapping: dict[str, str | None] = {"INF000A00001": None}

    result = zerodha_service.commit_zerodha_csv(session, content, mapping)

    assert result.inserted_count == 0
    assert result.skipped_count == 1
    assert result.skipped_reasons == {"INF000A00001": 1}
    assert session.scalars(select(RawTransaction)).all() == []


def test_recommitting_same_file_and_mapping_is_idempotent(session: Session) -> None:
    content = _content(_MATCHED_ROW)
    mapping = {"INF000A00001": "999901"}

    first = zerodha_service.commit_zerodha_csv(session, content, mapping)
    second = zerodha_service.commit_zerodha_csv(session, content, mapping)

    assert second.upload_id == first.upload_id
    assert second.inserted_count == 0
    assert len(session.scalars(select(RawTransaction)).all()) == 1


def test_recommitting_with_corrected_mapping_is_treated_as_new_upload(session: Session) -> None:
    content = _content(_MATCHED_ROW)

    first = zerodha_service.commit_zerodha_csv(session, content, {"INF000A00001": None})
    assert first.inserted_count == 0

    second = zerodha_service.commit_zerodha_csv(session, content, {"INF000A00001": "999901"})
    assert second.upload_id != first.upload_id
    assert second.inserted_count == 1


def test_commit_skips_row_matching_an_existing_cross_source_transaction(session: Session) -> None:
    # Same scheme/date/type/amount as _MATCHED_ROW, but a CAS-style folio
    # instead of Zerodha's constant "ZERODHA" folio.
    _seed_cas_transaction(
        session,
        folio="AMC-FOLIO-000123",
        amfi_code="999901",
        txn_date=date(2023, 6, 14),
        txn_type=TransactionType.PURCHASE,
        amount_paise=10_000_001,
        natural_key="preexisting-cas-key",
    )

    content = _content(_MATCHED_ROW)
    mapping = {"INF000A00001": "999901"}

    result = zerodha_service.commit_zerodha_csv(session, content, mapping)

    assert result.inserted_count == 0
    assert result.cross_source_duplicate_count == 1
    assert result.cross_source_duplicates == {"INF000A00001": 1}
    assert result.skipped_count == 0
    assert len(session.scalars(select(RawTransaction)).all()) == 1


def test_commit_still_inserts_a_genuinely_new_transaction_for_the_same_scheme(
    session: Session,
) -> None:
    _seed_cas_transaction(
        session,
        folio="AMC-FOLIO-000123",
        amfi_code="999901",
        txn_date=date(2023, 5, 1),
        txn_type=TransactionType.PURCHASE,
        amount_paise=10_000_001,
        natural_key="preexisting-unrelated-key",
    )

    content = _content(_MATCHED_ROW)
    mapping = {"INF000A00001": "999901"}

    result = zerodha_service.commit_zerodha_csv(session, content, mapping)

    assert result.inserted_count == 1
    assert result.cross_source_duplicate_count == 0
    assert len(session.scalars(select(RawTransaction)).all()) == 2


def test_duplicate_looking_same_day_rows_both_inserted_distinguished_by_trade_id(
    session: Session,
) -> None:
    # Two real, separate fills on the same day/scheme/qty/price -- only
    # trade_id differs. Both must be inserted, not deduped against each
    # other by the natural key.
    content = _content(
        "Acme Focused Fund - Direct Plan,INF000A00001,2023-06-14,BSE,MF,,buy,false,"
        "10.000000,150.000000,2001,2001,2023-06-14T00:00:00\n"
        "Acme Focused Fund - Direct Plan,INF000A00001,2023-06-14,BSE,MF,,buy,false,"
        "10.000000,150.000000,2002,2002,2023-06-14T00:00:00\n"
    )
    mapping = {"INF000A00001": "999901"}

    result = zerodha_service.commit_zerodha_csv(session, content, mapping)

    assert result.inserted_count == 2
    assert len(session.scalars(select(RawTransaction)).all()) == 2
