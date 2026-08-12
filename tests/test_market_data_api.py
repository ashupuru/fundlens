from fastapi.testclient import TestClient


def test_refresh_endpoint_with_no_held_schemes_is_a_clean_noop(client: TestClient) -> None:
    response = client.post("/market-data/refresh")

    # No transactions ingested yet in this test's DB, so nothing to refresh —
    # this must not error just because there's nothing to do.
    assert response.status_code == 200
    body = response.json()
    assert body["backfilled_schemes"] == 0
    assert body["backfilled_rows"] == 0
