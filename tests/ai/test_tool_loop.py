"""The agentic loop.

Mocking follows the precedent in test_claude_client.py: monkeypatch the
`anthropic.Anthropic` module attribute with a fake whose `messages.create`
returns scripted responses, so the loop's own control flow is what's under
test, not the SDK's.
"""

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

import anthropic
import pytest
from sqlalchemy.orm import Session

from fundlens.ai.snapshot import ConcentrationStats, PortfolioMetricsSnapshot
from fundlens.ai.tool_loop import MAX_ITERATIONS, run_ask_loop
from fundlens.ai.tools import Tool, registry
from fundlens.ai.tools.registry import ToolError, object_schema


@dataclass
class _TextBlock:
    text: str
    type: str = "text"


@dataclass
class _ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


@dataclass
class _Response:
    content: list[Any]


class _FakeMessages:
    def __init__(self, responses: list[_Response]) -> None:
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _Response:
        # Snapshot `messages`: the loop appends to the same list across
        # turns, so recording the reference would make every assertion
        # about an earlier turn silently read the final state instead.
        self.calls.append({**kwargs, "messages": list(kwargs.get("messages", []))})
        # Repeat the last script entry once exhausted, so a test can script
        # "always asks for a tool" without listing MAX_ITERATIONS copies.
        index = min(len(self.calls) - 1, len(self._responses) - 1)
        return self._responses[index]


class _FakeClient:
    def __init__(self, messages: _FakeMessages) -> None:
        self.messages = messages


@pytest.fixture
def snapshot() -> PortfolioMetricsSnapshot:
    return PortfolioMetricsSnapshot(
        as_of_date=date(2026, 8, 20),
        portfolio_xirr=14.2,
        portfolio_xirr_reason=None,
        benchmark_xirr=11.0,
        benchmark_name="NIFTY 50 TRI",
        alpha_pct=3.2,
        allocation=None,
        funds=[],
        overlap_pairs=[],
        fee_drag_annual_rupees=None,
        fee_drag_funds_excluded=0,
        concentration=ConcentrationStats(
            top_1_pct=40.0, top_3_pct=80.0, top_5_pct=95.0, hhi=2200.0
        ),
        style_drift_flags=[],
    )


def _answer(summary: str = "Here is the answer.") -> _Response:
    return _Response(
        content=[
            _TextBlock(
                json.dumps(
                    {
                        "answer_summary": summary,
                        "statements": [
                            {
                                "topic": "concentration",
                                "text": "Your readiness is 41.3%.",
                                "cited_metric_paths": ["tool_calls[0].result.readiness_pct"],
                            }
                        ],
                    }
                )
            )
        ]
    )


_STUB_TOOLS = registry(
    [
        Tool(
            name="stub_projection",
            description="A deterministic stub.",
            input_schema=object_schema({"retirement_age": {"type": "integer"}}),
            fn=lambda session, retirement_age=58: {"readiness_pct": 41.3, "age": retirement_age},
        ),
        Tool(
            name="always_fails",
            description="Always raises.",
            input_schema=object_schema({}),
            fn=lambda session: (_ for _ in ()).throw(ToolError("no risk profile set")),
        ),
    ]
)


def _patch(monkeypatch: pytest.MonkeyPatch, responses: list[_Response]) -> _FakeMessages:
    messages = _FakeMessages(responses)
    monkeypatch.setattr(anthropic, "Anthropic", lambda api_key: _FakeClient(messages))
    return messages


def test_answers_without_calling_any_tool(
    session: Session, snapshot: PortfolioMetricsSnapshot, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch(monkeypatch, [_answer()])

    outcome = run_ask_loop(session, "key", "How am I doing?", snapshot, tools=_STUB_TOOLS)

    assert outcome.tool_calls == []
    assert outcome.hit_iteration_cap is False
    assert outcome.response.answer_summary == "Here is the answer."


def test_executes_a_tool_call_and_records_it(
    session: Session, snapshot: PortfolioMetricsSnapshot, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch(
        monkeypatch,
        [
            _Response(
                content=[
                    _ToolUseBlock(id="t1", name="stub_projection", input={"retirement_age": 52})
                ]
            ),
            _answer(),
        ],
    )

    outcome = run_ask_loop(session, "key", "Can I retire at 52?", snapshot, tools=_STUB_TOOLS)

    assert len(outcome.tool_calls) == 1
    record = outcome.tool_calls[0]
    assert record.tool == "stub_projection"
    assert record.arguments == {"retirement_age": 52}
    assert record.result == {"readiness_pct": 41.3, "age": 52}
    assert record.error is None


def test_tool_arguments_and_result_both_reach_the_ledger(
    session: Session, snapshot: PortfolioMetricsSnapshot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ledger is what the validator resolves citation paths against, so
    an argument the model wrote about must be citable too."""
    _patch(
        monkeypatch,
        [
            _Response(
                content=[
                    _ToolUseBlock(id="t1", name="stub_projection", input={"retirement_age": 52})
                ]
            ),
            _answer(),
        ],
    )

    outcome = run_ask_loop(session, "key", "q", snapshot, tools=_STUB_TOOLS)

    assert outcome.tool_calls[0].as_evidence() == {
        "tool": "stub_projection",
        "arguments": {"retirement_age": 52},
        "result": {"readiness_pct": 41.3, "age": 52},
    }


def test_a_failing_tool_is_reported_back_to_the_model_not_raised(
    session: Session, snapshot: PortfolioMetricsSnapshot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model that guessed a bad argument should get the chance to correct
    itself rather than failing the whole question."""
    messages = _patch(
        monkeypatch,
        [
            _Response(content=[_ToolUseBlock(id="t1", name="always_fails", input={})]),
            _answer(),
        ],
    )

    outcome = run_ask_loop(session, "key", "q", snapshot, tools=_STUB_TOOLS)

    assert outcome.tool_calls[0].error == "no risk profile set"
    assert outcome.tool_calls[0].result is None
    # A failed call contributes no citable result, only the attempt.
    assert "result" not in outcome.tool_calls[0].as_evidence()
    # The error went back as a tool_result the model could react to.
    follow_up = messages.calls[1]["messages"][-1]["content"][0]
    assert follow_up["is_error"] is True
    assert follow_up["tool_use_id"] == "t1"


def test_unknown_tool_name_is_an_error_result_not_a_crash(
    session: Session, snapshot: PortfolioMetricsSnapshot, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch(
        monkeypatch,
        [
            _Response(content=[_ToolUseBlock(id="t1", name="not_a_tool", input={})]),
            _answer(),
        ],
    )

    outcome = run_ask_loop(session, "key", "q", snapshot, tools=_STUB_TOOLS)

    assert outcome.tool_calls[0].error is not None
    assert "Unknown tool" in outcome.tool_calls[0].error


def test_iteration_cap_terminates_a_model_that_never_stops_calling_tools(
    session: Session, snapshot: PortfolioMetricsSnapshot, monkeypatch: pytest.MonkeyPatch
) -> None:
    messages = _patch(
        monkeypatch,
        [_Response(content=[_ToolUseBlock(id="t1", name="stub_projection", input={})])],
    )

    outcome = run_ask_loop(
        session, "key", "q", snapshot, tools=_STUB_TOOLS, max_iterations=3
    )

    assert outcome.hit_iteration_cap is True
    assert outcome.response.statements == []
    assert len(messages.calls) == 3
    assert len(outcome.tool_calls) == 3


def test_several_tool_calls_in_one_turn_are_all_executed(
    session: Session, snapshot: PortfolioMetricsSnapshot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Comparative questions ("52 vs 58") are answered by calling the same
    tool twice, which the model may do in a single turn."""
    _patch(
        monkeypatch,
        [
            _Response(
                content=[
                    _ToolUseBlock(id="a", name="stub_projection", input={"retirement_age": 52}),
                    _ToolUseBlock(id="b", name="stub_projection", input={"retirement_age": 58}),
                ]
            ),
            _answer(),
        ],
    )

    outcome = run_ask_loop(session, "key", "52 or 58?", snapshot, tools=_STUB_TOOLS)

    assert [c.arguments["retirement_age"] for c in outcome.tool_calls] == [52, 58]


def test_request_shape_carries_the_tools_and_the_question(
    session: Session, snapshot: PortfolioMetricsSnapshot, monkeypatch: pytest.MonkeyPatch
) -> None:
    messages = _patch(monkeypatch, [_answer()])

    run_ask_loop(session, "key", "Can I retire at 52?", snapshot, tools=_STUB_TOOLS)

    kwargs = messages.calls[0]
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["output_config"]["format"]["type"] == "json_schema"
    assert {t["name"] for t in kwargs["tools"]} == {"stub_projection", "always_fails"}
    assert "Can I retire at 52?" in kwargs["messages"][0]["content"]


def test_missing_text_block_raises(
    session: Session, snapshot: PortfolioMetricsSnapshot, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch(monkeypatch, [_Response(content=[])])

    with pytest.raises(ValueError, match="no text content"):
        run_ask_loop(session, "key", "q", snapshot, tools=_STUB_TOOLS)


def test_default_iteration_cap_is_bounded() -> None:
    assert 1 < MAX_ITERATIONS <= 12


def test_each_tool_result_states_its_own_citation_index(
    session: Session, snapshot: PortfolioMetricsSnapshot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression on a real failure: asked to compare funds, the model
    chained five calls then cited tool_calls[0] for figures that were at
    tool_calls[1] -- indexing as though the discovery call didn't count, so
    every statement was rejected despite correct numbers. The server assigns
    the index, so the server states it."""
    messages = _patch(
        monkeypatch,
        [
            _Response(
                content=[
                    _ToolUseBlock(id="a", name="stub_projection", input={"retirement_age": 52}),
                    _ToolUseBlock(id="b", name="stub_projection", input={"retirement_age": 58}),
                ]
            ),
            _answer(),
        ],
    )

    run_ask_loop(session, "key", "52 or 58?", snapshot, tools=_STUB_TOOLS)

    blocks = messages.calls[1]["messages"][-1]["content"]
    first, second = json.loads(blocks[0]["content"]), json.loads(blocks[1]["content"])
    assert first["cite_as"] == "tool_calls[0]"
    assert second["cite_as"] == "tool_calls[1]"
    # The wrapper mirrors the evidence layout, so the prefix the model reads
    # back concatenates into a path that actually resolves.
    assert first["result"] == {"readiness_pct": 41.3, "age": 52}


def test_indices_keep_counting_across_turns(
    session: Session, snapshot: PortfolioMetricsSnapshot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The off-by-one appeared across turns, which is where counting is
    hardest for the model and where the ledger must stay authoritative."""
    messages = _patch(
        monkeypatch,
        [
            _Response(content=[_ToolUseBlock(id="a", name="stub_projection", input={})]),
            _Response(content=[_ToolUseBlock(id="b", name="stub_projection", input={})]),
            _answer(),
        ],
    )

    run_ask_loop(session, "key", "q", snapshot, tools=_STUB_TOOLS)

    turn_one = json.loads(messages.calls[1]["messages"][-1]["content"][0]["content"])
    turn_two = json.loads(messages.calls[2]["messages"][-1]["content"][0]["content"])
    assert turn_one["cite_as"] == "tool_calls[0]"
    assert turn_two["cite_as"] == "tool_calls[1]"


def test_a_failed_call_still_consumes_its_index(
    session: Session, snapshot: PortfolioMetricsSnapshot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure occupies a ledger slot, so the next success must not reuse
    it -- otherwise the stated index would drift from the real one."""
    messages = _patch(
        monkeypatch,
        [
            _Response(
                content=[
                    _ToolUseBlock(id="a", name="always_fails", input={}),
                    _ToolUseBlock(id="b", name="stub_projection", input={}),
                ]
            ),
            _answer(),
        ],
    )

    run_ask_loop(session, "key", "q", snapshot, tools=_STUB_TOOLS)

    blocks = messages.calls[1]["messages"][-1]["content"]
    assert blocks[0]["is_error"] is True
    assert json.loads(blocks[1]["content"])["cite_as"] == "tool_calls[1]"
