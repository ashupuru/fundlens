import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

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

# A Sell with no prior Buy in this (empty) database, followed by a clean
# later Buy -- a tradebook export only covers a date window, so an account
# that predates the window's start routinely has this shape on a
# first-ever import.
_SELL_THEN_BUY_ROWS = (
    "Acme Focused Fund - Direct Plan,INF000A00001,2023-06-14,BSE,MF,,sell,false,"
    "133.333300,150.000000,2001,2001,2023-06-14T00:00:00\n"
    "Acme Focused Fund - Direct Plan,INF000A00001,2023-06-20,BSE,MF,,buy,false,"
    "333.333300,150.000000,2002,2002,2023-06-20T00:00:00\n"
)

_NAVALL_TEXT = (
    "999901;INF000A00001;INF000A00002;Acme Focused Fund-Direct Plan-Growth;150.0000;14-Jun-2023\n"
)

_MFAPI_PAYLOAD = {
    "meta": {"fund_house": "Acme Mutual Fund"},
    "data": [{"date": "14-06-2023", "nav": "150.0000"}],
}


@pytest.fixture(autouse=True)
def _patch_market_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(zerodha_service, "fetch_navall_text", lambda: _NAVALL_TEXT)
    monkeypatch.setattr(broker_import_shared, "fetch_scheme_history", lambda code: _MFAPI_PAYLOAD)
    monkeypatch.setattr(
        market_data_service, "fetch_scheme_history", lambda code: _MFAPI_PAYLOAD
    )


def test_preview_endpoint_returns_proposed_mapping(client: TestClient) -> None:
    content = _content(_MATCHED_ROW)

    response = client.post(
        "/ingest/zerodha/preview", files={"file": ("tradebook-TEST-MF.csv", content, "text/csv")}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total_mf_rows"] == 1
    assert len(body["schemes"]) == 1
    scheme = body["schemes"][0]
    assert scheme["isin"] == "INF000A00001"
    assert scheme["matched_amfi_code"] == "999901"
    assert scheme["reason"] is None


def test_preview_endpoint_rejects_malformed_file(client: TestClient) -> None:
    response = client.post(
        "/ingest/zerodha/preview",
        files={"file": ("not_zerodha.csv", "a,b,c\n1,2,3\n", "text/csv")},
    )

    assert response.status_code == 422


def test_commit_endpoint_inserts_transactions(client: TestClient) -> None:
    content = _content(_MATCHED_ROW)
    mapping = json.dumps({"INF000A00001": "999901"})

    response = client.post(
        "/ingest/zerodha/commit",
        files={"file": ("tradebook-TEST-MF.csv", content, "text/csv")},
        data={"mapping": mapping},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["inserted_count"] == 1
    assert body["skipped_count"] == 0
    assert body["skipped_reasons"] == {}
    assert body["cross_source_duplicate_count"] == 0
    assert body["cross_source_duplicates"] == {}


def test_commit_endpoint_rejects_invalid_mapping_json(client: TestClient) -> None:
    content = _content(_MATCHED_ROW)

    response = client.post(
        "/ingest/zerodha/commit",
        files={"file": ("tradebook-TEST-MF.csv", content, "text/csv")},
        data={"mapping": "not json"},
    )

    assert response.status_code == 422


def test_commit_endpoint_reports_skipped_rows_when_mapped_to_null(client: TestClient) -> None:
    content = _content(_MATCHED_ROW)
    mapping = json.dumps({"INF000A00001": None})

    response = client.post(
        "/ingest/zerodha/commit",
        files={"file": ("tradebook-TEST-MF.csv", content, "text/csv")},
        data={"mapping": mapping},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["inserted_count"] == 0
    assert body["skipped_count"] == 1


def test_commit_then_rebuild_shows_fund_even_when_first_txn_is_a_sell(
    client: TestClient,
) -> None:
    """Same gap-fill regression shape as the Dezerv importer
    (tests/test_dezerv_api.py) -- the underlying lot-accounting fix
    (compute/service.py::rebuild_lots_for_folio) is shared by every
    importer, so any source whose export window starts mid-holding must
    show the fund, not silently lose it."""
    content = _content(_SELL_THEN_BUY_ROWS)
    mapping = json.dumps({"INF000A00001": "999901"})

    commit_response = client.post(
        "/ingest/zerodha/commit",
        files={"file": ("tradebook-TEST-MF.csv", content, "text/csv")},
        data={"mapping": mapping},
    )
    assert commit_response.status_code == 200
    assert commit_response.json()["inserted_count"] == 2

    rebuild_response = client.post("/portfolio/rebuild-lots")
    assert rebuild_response.status_code == 200
    rebuild_body = rebuild_response.json()
    assert rebuild_body["errors"] == []
    assert rebuild_body["lots_created"] == 2  # the gap-fill lot + the later clean Buy's lot

    holdings = client.get("/portfolio/holdings").json()
    assert len(holdings) == 1
    assert holdings[0]["amfi_code"] == "999901"
    assert Decimal(holdings[0]["units_remaining"]) > 0
