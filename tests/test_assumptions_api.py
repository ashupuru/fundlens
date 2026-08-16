from fastapi.testclient import TestClient


def test_current_assumption_version_seeds_defaults(client: TestClient) -> None:
    response = client.get("/assumptions/current")

    assert response.status_code == 200
    body = response.json()
    assert body["is_current"] is True
    assert body["values"]["equity_ltcg_threshold_days"] == "365"
    assert body["values"]["equity_ltcg_exemption_paise_per_fy"] == "12500000"


def test_create_version_overrides_and_becomes_current(client: TestClient) -> None:
    response = client.post(
        "/assumptions",
        json={
            "label": "Higher exemption",
            "overrides": {"equity_ltcg_exemption_paise_per_fy": "20000000"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["is_current"] is True
    assert body["values"]["equity_ltcg_exemption_paise_per_fy"] == "20000000"

    current = client.get("/assumptions/current").json()
    assert current["id"] == body["id"]


def test_create_version_rejects_unknown_key(client: TestClient) -> None:
    response = client.post(
        "/assumptions", json={"label": "bad", "overrides": {"not_a_real_key": "1"}}
    )
    assert response.status_code == 422


def test_versions_lists_history_newest_first(client: TestClient) -> None:
    client.get("/assumptions/current")  # seed default version
    client.post("/assumptions", json={"label": "v2", "overrides": {}})

    response = client.get("/assumptions/versions")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert body[0]["label"] == "v2"
    assert body[1]["label"] == "Default"
