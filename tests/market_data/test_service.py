import uuid
from datetime import date
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.db.models import (
    DataFreshness,
    FetchStatus,
    NavHistory,
    NavSource,
    RawCASUpload,
    RawTransaction,
    SchemeMetadata,
    TransactionType,
    UploadStatus,
)
from fundlens.market_data import service
from fundlens.tax.asset_class import TaxAssetClass

NAVALL_TEXT = (
    "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;"
    "Scheme Name;Net Asset Value;Date\n"
    "\n"
    "Open Ended Schemes(Debt Scheme - Banking and PSU Fund)\n"
    "\n"
    "119551;INF209KA12Z1;INF209KA13Z9;Held Fund;107.1766;11-Aug-2026\n"
    "\n"
    "Open Ended Schemes(Equity Scheme - Large Cap Fund)\n"
    "\n"
    "999999;-;-;Not Held Fund;250.0000;11-Aug-2026\n"
)

MFAPI_PAYLOAD = {
    "meta": {"scheme_code": 119551},
    "data": [
        {"date": "11-08-2026", "nav": "107.17660"},
        {"date": "10-08-2026", "nav": "107.19580"},
    ],
}


def _add_held_transaction(session: Session, amfi_code: str = "119551") -> None:
    unique = uuid.uuid4().hex
    upload = RawCASUpload(
        file_hash=f"hash-{unique}", parser_version="test", status=UploadStatus.PARSED
    )
    session.add(upload)
    session.flush()
    session.add(
        RawTransaction(
            upload_id=upload.id,
            folio="F1",
            amc="Test AMC",
            scheme_name="Held Fund",
            amfi_code=amfi_code,
            txn_type=TransactionType.PURCHASE,
            txn_date=date(2021, 4, 15),
            natural_key=f"key-{unique}",
        )
    )
    session.commit()


def test_get_held_amfi_codes_returns_distinct_non_null_codes(session: Session) -> None:
    _add_held_transaction(session, "119551")
    _add_held_transaction(session, "119551")  # duplicate scheme, different txn
    _add_held_transaction(session, "222222")

    codes = service.get_held_amfi_codes(session)

    assert codes == {"119551", "222222"}


def test_backfill_scheme_via_mfapi_inserts_rows_and_records_freshness(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "fetch_scheme_history", lambda code: MFAPI_PAYLOAD)

    inserted = service.backfill_scheme_via_mfapi(session, "119551")

    assert inserted == 2
    assert len(session.scalars(select(NavHistory)).all()) == 2
    freshness = session.get(DataFreshness, NavSource.MFAPI)
    assert freshness is not None
    assert freshness.status == FetchStatus.OK
    assert freshness.rows_updated == 2
    assert freshness.last_success_at is not None


def test_backfill_scheme_via_mfapi_failure_degrades_gracefully(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def raise_error(code: str) -> dict:
        raise httpx.ConnectError("connection failed")

    monkeypatch.setattr(service, "fetch_scheme_history", raise_error)

    inserted = service.backfill_scheme_via_mfapi(session, "119551")

    assert inserted == 0
    assert session.scalars(select(NavHistory)).all() == []
    freshness = session.get(DataFreshness, NavSource.MFAPI)
    assert freshness is not None
    assert freshness.status == FetchStatus.FAILED
    assert freshness.last_success_at is None
    assert "connection failed" in freshness.error_message


def test_refresh_latest_via_amfi_only_inserts_held_schemes(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "fetch_navall_text", lambda: NAVALL_TEXT)

    inserted, error = service.refresh_latest_via_amfi(session, {"119551"})

    assert error is None
    assert inserted == 1
    rows = session.scalars(select(NavHistory)).all()
    assert len(rows) == 1
    assert rows[0].amfi_code == "119551"


def test_refresh_latest_via_amfi_skips_fetch_entirely_when_nothing_held(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No held schemes means nothing to look up in the (large) AMFI feed —
    must not make a network call at all, since that would make every 'empty
    portfolio' request slow and network-dependent for no reason."""

    def fail_if_called() -> str:
        raise AssertionError("fetch_navall_text should not be called with no held schemes")

    monkeypatch.setattr(service, "fetch_navall_text", fail_if_called)

    inserted, error = service.refresh_latest_via_amfi(session, set())

    assert inserted == 0
    assert error is None


def test_refresh_latest_via_amfi_failure_leaves_existing_data_untouched(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session.add(
        NavHistory(
            amfi_code="119551",
            nav_date=date(2026, 8, 1),
            nav=Decimal("100"),
            source=NavSource.MFAPI,
        )
    )
    session.commit()

    def raise_error() -> str:
        raise httpx.HTTPStatusError("500", request=None, response=None)  # type: ignore[arg-type]

    monkeypatch.setattr(service, "fetch_navall_text", raise_error)

    inserted, error = service.refresh_latest_via_amfi(session, {"119551"})

    assert inserted == 0
    assert error is not None
    # existing data survives untouched
    assert len(session.scalars(select(NavHistory)).all()) == 1
    freshness = session.get(DataFreshness, NavSource.AMFI_NAVALL)
    assert freshness is not None
    assert freshness.status == FetchStatus.FAILED


def test_reinserting_same_nav_row_is_idempotent(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "fetch_navall_text", lambda: NAVALL_TEXT)

    first = service.refresh_latest_via_amfi(session, {"119551"})
    second = service.refresh_latest_via_amfi(session, {"119551"})

    assert first[0] == 1
    assert second[0] == 0  # same (code, date) already present
    assert len(session.scalars(select(NavHistory)).all()) == 1


def test_refresh_all_held_schemes_backfills_only_schemes_without_history(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _add_held_transaction(session, "119551")
    # Pre-existing history for 119551 means it should NOT be backfilled again.
    session.add(
        NavHistory(
            amfi_code="119551",
            nav_date=date(2026, 8, 1),
            nav=Decimal("100"),
            source=NavSource.MFAPI,
        )
    )
    session.commit()

    backfill_calls: list[str] = []

    def fake_scheme_history(code: str) -> dict:
        backfill_calls.append(code)
        return MFAPI_PAYLOAD

    monkeypatch.setattr(service, "fetch_scheme_history", fake_scheme_history)
    monkeypatch.setattr(service, "fetch_navall_text", lambda: NAVALL_TEXT)

    summary = service.refresh_all_held_schemes(session)

    assert backfill_calls == []  # already had history, so no backfill triggered
    assert summary.backfilled_schemes == 0
    assert summary.amfi_refresh_ok is True


def test_refresh_all_held_schemes_backfills_new_scheme_then_refreshes(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _add_held_transaction(session, "119551")  # no existing NAV history yet

    monkeypatch.setattr(service, "fetch_scheme_history", lambda code: MFAPI_PAYLOAD)
    monkeypatch.setattr(service, "fetch_navall_text", lambda: NAVALL_TEXT)

    summary = service.refresh_all_held_schemes(session)

    assert summary.backfilled_schemes == 1
    assert summary.backfilled_rows == 2
    assert summary.amfi_refresh_ok is True


def test_refresh_persists_scheme_metadata_for_held_schemes(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "fetch_navall_text", lambda: NAVALL_TEXT)

    service.refresh_latest_via_amfi(session, {"119551"})

    metadata = session.get(SchemeMetadata, "119551")
    assert metadata is not None
    assert metadata.category_raw == "Open Ended Schemes(Debt Scheme - Banking and PSU Fund)"
    assert metadata.tax_asset_class == TaxAssetClass.DEBT


def test_refresh_only_persists_metadata_for_held_schemes_not_every_scheme(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "fetch_navall_text", lambda: NAVALL_TEXT)

    service.refresh_latest_via_amfi(session, {"119551"})

    # "999999" (Not Held Fund) is in the feed but not in held_amfi_codes.
    assert session.get(SchemeMetadata, "999999") is None


def test_refresh_updates_existing_scheme_metadata_not_duplicates(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "fetch_navall_text", lambda: NAVALL_TEXT)

    service.refresh_latest_via_amfi(session, {"119551"})
    service.refresh_latest_via_amfi(session, {"119551"})  # re-run

    assert len(session.scalars(select(SchemeMetadata)).all()) == 1
