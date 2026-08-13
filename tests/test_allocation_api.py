from fastapi.testclient import TestClient


def test_risk_profile_none_before_onboarding(client: TestClient) -> None:
    response = client.get("/risk-profile")
    assert response.status_code == 200
    assert response.json() is None


def test_set_risk_profile_conservative(client: TestClient) -> None:
    response = client.post("/risk-profile", json={"persona": "conservative"})
    assert response.status_code == 200
    body = response.json()
    assert body["persona"] == "conservative"
    assert body["target_equity_pct"] == "30.00"
    assert body["target_debt_pct"] == "70.00"


def test_set_risk_profile_custom_requires_targets(client: TestClient) -> None:
    response = client.post("/risk-profile", json={"persona": "custom"})
    assert response.status_code == 422


def test_set_risk_profile_unknown_persona_rejected(client: TestClient) -> None:
    response = client.post("/risk-profile", json={"persona": "not_a_persona"})
    assert response.status_code == 422


def test_allocation_drift_without_onboarding_returns_404(client: TestClient) -> None:
    response = client.get("/allocation/drift")
    assert response.status_code == 404


def test_allocation_drift_after_onboarding_and_ingest(client: TestClient) -> None:
    client.post("/risk-profile", json={"persona": "balanced"})
    csv_content = (
        "folio,amc,scheme_name,txn_type,txn_date,amount,units,isin,amfi_code\n"
        "F1,Test AMC,Feeder Fund,PURCHASE,2022-04-15,5000.00,100,INF999,119551\n"
    )
    client.post("/ingest/csv", files={"file": ("t.csv", csv_content, "text/csv")})
    client.post("/portfolio/rebuild-lots")

    response = client.get("/allocation/drift")

    assert response.status_code == 200
    body = response.json()
    assert body["persona"] == "balanced"
    assert body["target_equity_pct"] == 60.0
    # No scheme_metadata/NAV ever fetched in this test's DB, so the holding
    # can't be priced -- flagged, not silently zeroed.
    assert body["priced_portfolio_value_paise"] == 0
    assert body["value_unavailable_paise"] > 0
    assert body["actual_equity_pct"] is None
