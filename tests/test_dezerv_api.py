import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

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

# A Sell with no prior Buy in this (empty) database, followed by a clean
# later Buy -- the Dezerv "BANK BOOK" export only covers a date window, so
# an account that predates the window's start routinely has this shape on
# a first-ever import. The scheme ends with a real, still-held position
# from the second row.
_SELL_THEN_BUY_ROWS = (
    "1,Test Investor,ICICI-1,ICICI Bank,Sell,14/06/2023,14/06/2023,MFDEMAT,ABC,"
    'Acme Focused Direct-G,"20,000.00",0.00,0.00,0.00,"20,000.00",NSDL-TEST-1,,,1\n'
    "1,Test Investor,ICICI-1,ICICI Bank,Buy,20/06/2023,20/06/2023,MFDEMAT,ABC,"
    'Acme Focused Direct-G,"-50,000.00",0.00,0.00,0.00,"30,000.00",NSDL-TEST-1,,,1\n'
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
    monkeypatch.setattr(dezerv_service, "fetch_navall_text", lambda: _NAVALL_TEXT)
    monkeypatch.setattr(broker_import_shared, "fetch_scheme_history", lambda code: _MFAPI_PAYLOAD)
    monkeypatch.setattr(
        market_data_service, "fetch_scheme_history", lambda code: _MFAPI_PAYLOAD
    )


def test_preview_endpoint_returns_proposed_mapping(client: TestClient) -> None:
    content = _content(_MATCHED_ROW)

    response = client.post(
        "/ingest/dezerv/preview", files={"file": ("dezerv.csv", content, "text/csv")}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total_mfdemat_rows"] == 1
    assert len(body["schemes"]) == 1
    scheme = body["schemes"][0]
    assert scheme["security_name"] == "Acme Focused Direct-G"
    assert scheme["matched_amfi_code"] == "999901"
    assert scheme["match_score"] == 1.0


def test_preview_endpoint_rejects_malformed_file(client: TestClient) -> None:
    response = client.post(
        "/ingest/dezerv/preview",
        files={"file": ("not_dezerv.csv", "a,b,c\n1,2,3\n", "text/csv")},
    )

    assert response.status_code == 422


def test_commit_endpoint_inserts_transactions(client: TestClient) -> None:
    content = _content(_MATCHED_ROW)
    mapping = json.dumps({"Acme Focused Direct-G": "999901"})

    response = client.post(
        "/ingest/dezerv/commit",
        files={"file": ("dezerv.csv", content, "text/csv")},
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
        "/ingest/dezerv/commit",
        files={"file": ("dezerv.csv", content, "text/csv")},
        data={"mapping": "not json"},
    )

    assert response.status_code == 422


def test_commit_endpoint_reports_skipped_rows_when_mapped_to_null(client: TestClient) -> None:
    content = _content(_MATCHED_ROW)
    mapping = json.dumps({"Acme Focused Direct-G": None})

    response = client.post(
        "/ingest/dezerv/commit",
        files={"file": ("dezerv.csv", content, "text/csv")},
        data={"mapping": mapping},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["inserted_count"] == 0
    assert body["skipped_count"] == 1


def test_commit_then_rebuild_shows_fund_even_when_first_txn_is_a_sell(
    client: TestClient,
) -> None:
    """Regression test: on an empty database, a Dezerv import whose first
    recorded transaction for a scheme is a Sell (the export's date window
    started mid-holding) used to insert both transactions fine but silently
    drop that scheme's lots entirely after rebuild-lots -- InsufficientLotsError
    on the first row aborted lot-building before the second row's clean Buy
    was ever processed, so even that later, genuinely valid purchase never
    surfaced as a holding. "The fund is not added" from the user's
    perspective. It should now show up, priced, with the assumed
    pre-history gap flagged rather than the whole scheme being lost."""
    content = _content(_SELL_THEN_BUY_ROWS)
    mapping = json.dumps({"Acme Focused Direct-G": "999901"})

    commit_response = client.post(
        "/ingest/dezerv/commit",
        files={"file": ("dezerv.csv", content, "text/csv")},
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
