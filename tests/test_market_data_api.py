import pytest
from fastapi.testclient import TestClient

from fundlens.api.routers import market_data
from fundlens.market_data.groww_service import GrowwRefreshSummary


def test_refresh_endpoint_with_no_held_schemes_is_a_clean_noop(client: TestClient) -> None:
    response = client.post("/market-data/refresh")

    # No transactions ingested yet in this test's DB, so nothing to refresh —
    # this must not error just because there's nothing to do.
    assert response.status_code == 200
    body = response.json()
    assert body["backfilled_schemes"] == 0
    assert body["backfilled_rows"] == 0


def test_refresh_groww_endpoint_returns_summary(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        market_data,
        "refresh_groww_metadata",
        lambda session: GrowwRefreshSummary(rows_upserted=42, ok=True, error=None),
    )

    response = client.post("/market-data/refresh-groww")

    assert response.status_code == 200
    body = response.json()
    assert body["rows_upserted"] == 42
    assert body["ok"] is True
    assert body["error"] is None


def test_refresh_groww_endpoint_surfaces_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        market_data,
        "refresh_groww_metadata",
        lambda session: GrowwRefreshSummary(rows_upserted=0, ok=False, error="network down"),
    )

    response = client.post("/market-data/refresh-groww")

    assert response.status_code == 200  # never raises, degrades in the body
    body = response.json()
    assert body["ok"] is False
    assert body["error"] == "network down"
