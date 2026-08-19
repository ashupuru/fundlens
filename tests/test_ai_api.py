import pytest
from fastapi.testclient import TestClient

from fundlens.ai import ask_service, service
from fundlens.ai.claude_client import GroundedStatement, NarrativeResponse
from fundlens.ai.tool_loop import AskOutcome, AskResponse, ToolCallRecord
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


# --- POST /ai/ask --------------------------------------------------------------


def test_ask_no_api_key_configured(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Degrades to a reported reason rather than a 500 -- a portfolio tool
    that errors out is worse than one that says what it couldn't do."""
    monkeypatch.setattr(
        ask_service, "get_settings", lambda: Settings(anthropic_api_key=None)
    )

    response = client.post("/ai/ask", json={"question": "Can I retire at 52?"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unavailable"
    assert "FUNDLENS_ANTHROPIC_API_KEY" in body["error_message"]
    assert body["statements"] == []


def test_ask_rejects_an_empty_question(client: TestClient) -> None:
    assert client.post("/ai/ask", json={"question": "   "}).status_code == 422


def test_ask_surfaces_the_tool_chain_and_validated_statements(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ask_service, "get_settings", lambda: Settings(anthropic_api_key="fake-key")
    )
    record = ToolCallRecord(
        tool="compute_retirement_projection",
        arguments={"retirement_age": 52},
        result={"readiness_pct": 41.3},
    )
    outcome = AskOutcome(
        response=AskResponse(
            answer_summary="Retiring at 52 falls short.",
            statements=[
                GroundedStatement(
                    topic="concentration",
                    text="At 52 you reach 41.3% of the corpus you need.",
                    cited_metric_paths=["tool_calls[0].result.readiness_pct"],
                ),
                GroundedStatement(
                    topic="concentration",
                    text="You would reach 88% instead.",
                    cited_metric_paths=["tool_calls[0].result.readiness_pct"],
                ),
            ],
        ),
        tool_calls=[record],
    )
    monkeypatch.setattr(ask_service, "run_ask_loop", lambda *a, **kw: outcome)

    body = client.post("/ai/ask", json={"question": "Can I retire at 52?"}).json()

    assert body["status"] == "ok"
    assert body["answer_summary"] == "Retiring at 52 falls short."
    # The grounded statement survives; the one citing a real path but stating
    # a fabricated number does not.
    assert [s["text"] for s in body["statements"]] == [
        "At 52 you reach 41.3% of the corpus you need."
    ]
    assert body["tool_calls"] == [
        {
            "tool": "compute_retirement_projection",
            "arguments": {"retirement_age": 52},
            "result": {"readiness_pct": 41.3},
            "error": None,
        }
    ]

    history = client.get("/ai/questions").json()
    assert len(history) == 1
    assert history[0]["question"] == "Can I retire at 52?"
    assert history[0]["id"] == body["id"]


def test_ask_reports_hitting_the_tool_call_limit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ask_service, "get_settings", lambda: Settings(anthropic_api_key="fake-key")
    )
    monkeypatch.setattr(
        ask_service,
        "run_ask_loop",
        lambda *a, **kw: AskOutcome(
            response=AskResponse(answer_summary="Stopped.", statements=[]),
            tool_calls=[],
            hit_iteration_cap=True,
        ),
    )

    body = client.post("/ai/ask", json={"question": "loop forever"}).json()

    assert body["status"] == "unavailable"
    assert "tool-call limit" in body["error_message"]


def test_ask_flags_a_summary_figure_that_does_not_trace_to_evidence(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The summary carries no citations by construction and is the most
    prominent text on the page, so it gets the blunt scan. Reported rather
    than suppressed -- that scan over-rejects, and deleting the whole
    summary over a false positive would cost more than it protects."""
    monkeypatch.setattr(
        ask_service, "get_settings", lambda: Settings(anthropic_api_key="fake-key")
    )
    monkeypatch.setattr(
        ask_service,
        "run_ask_loop",
        lambda *a, **kw: AskOutcome(
            response=AskResponse(
                answer_summary="Your corpus will reach 987654 crore by then.",
                statements=[],
            ),
            tool_calls=[],
        ),
    )

    body = client.post("/ai/ask", json={"question": "how much?"}).json()

    # Kept, so the user sees it -- but flagged, so they know which figure
    # isn't backed by a calculation.
    assert body["answer_summary"] == "Your corpus will reach 987654 crore by then."
    assert body["summary_unverified_figures"] == ["987654"]


def test_ask_keeps_a_summary_whose_figures_all_trace_to_evidence(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ask_service, "get_settings", lambda: Settings(anthropic_api_key="fake-key")
    )
    monkeypatch.setattr(
        ask_service,
        "run_ask_loop",
        lambda *a, **kw: AskOutcome(
            response=AskResponse(answer_summary="Readiness sits at 41.3%.", statements=[]),
            tool_calls=[
                ToolCallRecord(
                    tool="compute_retirement_projection",
                    arguments={},
                    result={"readiness_pct": 41.3},
                )
            ],
        ),
    )

    body = client.post("/ai/ask", json={"question": "how ready am i?"}).json()

    assert body["answer_summary"] == "Readiness sits at 41.3%."
    assert body["summary_unverified_figures"] == []

    # And it survives a round trip through history, where the scan is
    # recomputed from the persisted evidence rather than stored.
    assert client.get("/ai/questions").json()[0]["summary_unverified_figures"] == []
