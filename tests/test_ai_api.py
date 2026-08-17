import pytest
from fastapi.testclient import TestClient

from fundlens.ai import service
from fundlens.ai.claude_client import GroundedStatement, NarrativeResponse
from fundlens.settings import Settings


def test_latest_returns_404_when_never_run(client: TestClient) -> None:
    response = client.get("/ai/latest")
    assert response.status_code == 404


def test_analyze_no_api_key_configured(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "get_settings", lambda: Settings(anthropic_api_key=None))

    response = client.post("/ai/analyze")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["error_message"] == "ANTHROPIC_API_KEY is not configured"
    assert body["accepted"] == []


def test_analyze_then_latest_round_trip(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "get_settings", lambda: Settings(anthropic_api_key="fake-key"))
    response_stub = NarrativeResponse(
        statements=[
            GroundedStatement(
                topic="concentration", text="No holdings yet.", cited_metric_paths=[]
            )
        ],
        news_context=[],
    )
    monkeypatch.setattr(service, "call_claude", lambda *a, **kw: response_stub)

    analyze_response = client.post("/ai/analyze")
    assert analyze_response.status_code == 200
    analyzed = analyze_response.json()
    # The one statement cites no path -- rejected by the determinism validator.
    assert analyzed["status"] == "all_rejected"
    assert analyzed["accepted"] == []

    latest_response = client.get("/ai/latest")
    assert latest_response.status_code == 200
    latest = latest_response.json()
    assert latest["id"] == analyzed["id"]
    assert latest["status"] == "all_rejected"
