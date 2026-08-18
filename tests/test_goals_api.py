from datetime import date, timedelta

from fastapi.testclient import TestClient


def test_get_unknown_goal_returns_404(client: TestClient) -> None:
    response = client.get("/goals/not-a-real-id")
    assert response.status_code == 404


def test_create_goal_rejects_past_target_date(client: TestClient) -> None:
    response = client.post(
        "/goals",
        json={
            "name": "Too late",
            "target_amount_rupees": "100000",
            "target_date": str(date.today() - timedelta(days=1)),
        },
    )
    assert response.status_code == 422


def test_create_list_update_delete_goal(client: TestClient) -> None:
    target_date = str(date.today() + timedelta(days=3650))
    create_response = client.post(
        "/goals",
        json={"name": "Retirement", "target_amount_rupees": "5000000", "target_date": target_date},
    )
    assert create_response.status_code == 200
    goal = create_response.json()
    assert goal["target_amount_paise"] == 500_000_000

    list_response = client.get("/goals")
    assert len(list_response.json()) == 1

    update_response = client.patch(f"/goals/{goal['id']}", json={"name": "Early Retirement"})
    assert update_response.json()["name"] == "Early Retirement"

    delete_response = client.delete(f"/goals/{goal['id']}")
    assert delete_response.status_code == 204
    assert client.get(f"/goals/{goal['id']}").status_code == 404


def test_projection_without_risk_profile_returns_404(client: TestClient) -> None:
    target_date = str(date.today() + timedelta(days=365))
    goal = client.post(
        "/goals",
        json={"name": "Goal", "target_amount_rupees": "100000", "target_date": target_date},
    ).json()

    response = client.get(f"/goals/{goal['id']}/projection")

    assert response.status_code == 404
    assert "risk profile" in response.json()["detail"].lower()


def test_monte_carlo_without_risk_profile_returns_404(client: TestClient) -> None:
    target_date = str(date.today() + timedelta(days=365))
    goal = client.post(
        "/goals",
        json={"name": "Goal", "target_amount_rupees": "100000", "target_date": target_date},
    ).json()

    response = client.get(f"/goals/{goal['id']}/monte-carlo")

    assert response.status_code == 404
    assert "risk profile" in response.json()["detail"].lower()


def test_monte_carlo_unknown_goal_returns_404(client: TestClient) -> None:
    client.post("/risk-profile", json={"persona": "aggressive"})
    response = client.get("/goals/not-a-real-id/monte-carlo")
    assert response.status_code == 404


def test_monte_carlo_shape(client: TestClient) -> None:
    client.post("/risk-profile", json={"persona": "aggressive"})
    target_date = str(date.today() + timedelta(days=3650))
    goal = client.post(
        "/goals",
        json={"name": "Goal", "target_amount_rupees": "1000000", "target_date": target_date},
    ).json()

    response = client.get(f"/goals/{goal['id']}/monte-carlo")

    assert response.status_code == 200
    body = response.json()
    assert body["goal_id"] == goal["id"]
    assert body["simulations_run"] == 5000
    assert 0.0 <= body["success_probability_no_contribution_pct"] <= 100.0
    assert 0.0 <= body["success_probability_with_required_sip_pct"] <= 100.0
    assert (
        body["percentile_10_corpus_paise"]
        <= body["percentile_50_corpus_paise"]
        <= body["percentile_90_corpus_paise"]
    )


def test_tag_holdings_and_fetch_projection(client: TestClient) -> None:
    client.post("/risk-profile", json={"persona": "aggressive"})
    csv_content = (
        "folio,amc,scheme_name,txn_type,txn_date,amount,units,isin,amfi_code\n"
        "F1,Test AMC,Feeder Fund,PURCHASE,2022-04-15,5000.00,100,INF999,119551\n"
    )
    client.post("/ingest/csv", files={"file": ("t.csv", csv_content, "text/csv")})
    client.post("/portfolio/rebuild-lots")
    holdings = client.get("/portfolio/holdings").json()
    assert len(holdings) == 1
    scheme_key = holdings[0]["scheme_key"]

    target_date = str(date.today() + timedelta(days=3650))
    goal = client.post(
        "/goals",
        json={"name": "Goal", "target_amount_rupees": "1000000", "target_date": target_date},
    ).json()

    tag_response = client.put(
        f"/goals/{goal['id']}/holdings", json=[{"folio": "F1", "scheme_key": scheme_key}]
    )
    assert tag_response.status_code == 200
    assert tag_response.json()["holdings"] == [{"folio": "F1", "scheme_key": scheme_key}]

    projection_response = client.get(f"/goals/{goal['id']}/projection")

    assert projection_response.status_code == 200
    body = projection_response.json()
    # No NAV was ever fetched for this holding in this test's DB, so it
    # can't be priced -- flagged as unavailable, not silently valued at 0.
    assert body["current_corpus_paise"] == 0
    assert body["current_corpus_unavailable_paise"] > 0
    assert len(body["sensitivity"]) == 3
