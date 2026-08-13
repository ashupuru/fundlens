from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.db.models import DataFreshness, FetchStatus, GrowwFundMetadata, NavSource
from fundlens.market_data import groww_service

PAYLOAD = {
    "content": [
        {
            "scheme_code": "119788",
            "scheme_name": "SBI Gold Direct Plan Growth",
            "fund_house": "SBI Mutual Fund",
            "category": "Commodities",
            "sub_category": "Gold",
            "plan_type": "Direct",
            "aum": 15811.8654,
            "groww_rating": 4,
            "risk_rating": 5,
            "expense_ratio": "0.24",
            "fund_manager": "Raviprakash Sharma",
            "exit_load": "Exit load of 1% if redeemed within 15 days.",
            "return1y": 52.6,
            "return3y": 36.04,
            "return5y": 25.6,
            "return10y": 16.25,
            "sip_return1y": 27.29,
            "sip_return3y": 40.92,
            "sip_return5y": 32.28,
            "launch_date": "2013-03-12",
        },
    ],
    "total_results": 1,
}

# Real-data-verified edge case: Groww's own screener carries duplicate
# scheme_codes within a single fetch (e.g. an old vs current fund name for
# the same underlying scheme) -- two records, same amfi_code.
PAYLOAD_WITH_DUPLICATE_CODE = {
    "content": [
        {"scheme_code": "150642", "scheme_name": "Old Fund Name Direct Growth", "aum": 100.0},
        {"scheme_code": "150642", "scheme_name": "Current Fund Name Direct Growth", "aum": 105.0},
    ],
    "total_results": 2,
}


def test_refresh_upserts_new_rows(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(groww_service, "fetch_groww_screener", lambda: PAYLOAD)

    summary = groww_service.refresh_groww_metadata(session)

    assert summary.ok is True
    assert summary.rows_upserted == 1
    assert summary.error is None

    row = session.get(GrowwFundMetadata, "119788")
    assert row is not None
    assert row.scheme_name == "SBI Gold Direct Plan Growth"
    assert row.aum == Decimal("15811.8654")
    assert row.groww_rating == 4

    freshness = session.get(DataFreshness, NavSource.GROWW_METADATA)
    assert freshness is not None
    assert freshness.status == FetchStatus.OK
    assert freshness.rows_updated == 1
    assert freshness.last_success_at is not None


def test_refresh_is_idempotent_and_updates_existing_row(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(groww_service, "fetch_groww_screener", lambda: PAYLOAD)
    groww_service.refresh_groww_metadata(session)

    updated_payload = {
        "content": [{**PAYLOAD["content"][0], "aum": 16000.0, "groww_rating": 5}],
        "total_results": 1,
    }
    monkeypatch.setattr(groww_service, "fetch_groww_screener", lambda: updated_payload)
    summary = groww_service.refresh_groww_metadata(session)

    assert summary.rows_upserted == 1
    rows = session.scalars(select(GrowwFundMetadata)).all()
    assert len(rows) == 1  # updated in place, not a second row
    assert rows[0].aum == Decimal("16000.0")
    assert rows[0].groww_rating == 5


def test_refresh_deduplicates_repeated_scheme_code_within_one_fetch(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        groww_service, "fetch_groww_screener", lambda: PAYLOAD_WITH_DUPLICATE_CODE
    )

    summary = groww_service.refresh_groww_metadata(session)

    assert summary.ok is True
    assert summary.rows_upserted == 1
    rows = session.scalars(select(GrowwFundMetadata)).all()
    assert len(rows) == 1
    # Last record in the batch wins.
    assert rows[0].scheme_name == "Current Fund Name Direct Growth"


def test_refresh_failure_degrades_gracefully(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def raise_error() -> dict:
        raise httpx.ConnectError("connection failed")

    monkeypatch.setattr(groww_service, "fetch_groww_screener", raise_error)

    summary = groww_service.refresh_groww_metadata(session)

    assert summary.ok is False
    assert summary.rows_upserted == 0
    assert summary.error is not None

    freshness = session.get(DataFreshness, NavSource.GROWW_METADATA)
    assert freshness is not None
    assert freshness.status == FetchStatus.FAILED
    assert freshness.last_success_at is None


def test_refresh_malformed_payload_degrades_gracefully(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(groww_service, "fetch_groww_screener", lambda: {"unexpected": "shape"})

    summary = groww_service.refresh_groww_metadata(session)

    assert summary.ok is False
    assert summary.rows_upserted == 0
    freshness = session.get(DataFreshness, NavSource.GROWW_METADATA)
    assert freshness is not None
    assert freshness.status == FetchStatus.FAILED
