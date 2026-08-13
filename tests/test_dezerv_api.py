import json

import pytest
from fastapi.testclient import TestClient

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
    monkeypatch.setattr(dezerv_service, "fetch_scheme_history", lambda code: _MFAPI_PAYLOAD)
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
    assert body["skipped_reasons"] == {"Acme Focused Direct-G": 1}
