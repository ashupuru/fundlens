from fastapi.testclient import TestClient


def test_verdicts_empty_when_no_holdings(client: TestClient) -> None:
    response = client.get("/verdicts")
    assert response.status_code == 200
    assert response.json() == []


def test_direct_vs_regular_audit_empty_when_no_holdings(client: TestClient) -> None:
    response = client.get("/audit/direct-vs-regular")
    assert response.status_code == 200
    assert response.json() == []


def test_verdicts_after_ingest_returns_hold_with_schema_fields(client: TestClient) -> None:
    csv_content = (
        "folio,amc,scheme_name,txn_type,txn_date,amount,units,isin,amfi_code\n"
        "F1,Test AMC,Regular Feeder Fund,PURCHASE,2022-04-15,5000.00,100,INF999,119551\n"
    )
    client.post("/ingest/csv", files={"file": ("t.csv", csv_content, "text/csv")})
    client.post("/portfolio/rebuild-lots")

    response = client.get("/verdicts")
    assert response.status_code == 200
    body = response.json()

    assert len(body) == 1
    verdict = body[0]
    assert verdict["scheme_name"] == "Regular Feeder Fund"
    # No scheme_metadata/NAV ever fetched in this test's DB, so nothing is
    # computable -- must default to the safe HOLD state, never SELL.
    assert verdict["verdict_type"] == "hold"
    assert verdict["tax_impact"] is None
    assert set(verdict["omitted_components"]) == {"overlap_redundancy", "cost_expense_ratio"}
    assert len(verdict["components"]) == 4


def test_direct_vs_regular_audit_flags_regular_plan(client: TestClient) -> None:
    csv_content = (
        "folio,amc,scheme_name,txn_type,txn_date,amount,units,isin,amfi_code\n"
        "F1,Test AMC,Regular Feeder Fund,PURCHASE,2022-04-15,5000.00,100,INF999,119551\n"
    )
    client.post("/ingest/csv", files={"file": ("t.csv", csv_content, "text/csv")})
    client.post("/portfolio/rebuild-lots")

    response = client.get("/audit/direct-vs-regular")
    body = response.json()

    assert len(body) == 1
    assert body[0]["plan_type"] == "regular"
