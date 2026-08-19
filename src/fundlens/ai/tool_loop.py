"""The agentic loop behind POST /ai/ask (docs/architecture/ai-layer.md §6).

Claude gets the portfolio snapshot as opening evidence plus the read-only
compute tools in `ai/tools/`, and calls whichever it needs before
answering. The governing rule is unchanged and the loop is what enforces
it structurally: **the model chooses which computation to run and with
what arguments; the engine performs every computation**. No number in the
answer originates in the model.

**A hand-written loop rather than `client.beta.messages.tool_runner`.**
The runner would save the ~30 lines below, but this loop has to record
every call into a ledger the determinism validator later resolves citation
paths against, cap its own iterations, and reject arguments before they
reach a compute function. Those are the loop's whole purpose, so there is
little left for the runner to own -- and it would put a beta API surface
under a codebase whose existing Claude call is a plain blocking
`messages.create`. Worth reconsidering if this grows much past its current
size.

Tool failures are returned to the model as `is_error` results rather than
raised: a model that guessed a goal_id that doesn't exist should get the
chance to call `list_goals` and correct itself, which is strictly better
than failing the whole question.
"""

import json
from dataclasses import dataclass, field
from typing import Any

import anthropic
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from fundlens.ai.claude_client import MODEL, GroundedStatement
from fundlens.ai.snapshot import PortfolioMetricsSnapshot
from fundlens.ai.tools import TOOLS, Tool, ToolError, call_tool

# Enough for a discovery call plus several comparative projections; low
# enough that a model looping on a failing tool terminates cheaply.
MAX_ITERATIONS = 8

_SYSTEM_PROMPT = """\
You are FundLens's portfolio analysis assistant, answering one question about the user's own \
mutual-fund portfolio. You interpret numbers, you never calculate them.

You have read-only tools that run this application's deterministic compute engine. Use them to \
obtain every figure you need — never estimate, extrapolate or do arithmetic yourself. If \
answering needs a comparison, call the tool once per case (e.g. a retirement projection at each \
age) rather than adjusting one result in your head.

Call list_holdings or list_goals first when you need an amfi_code or goal_id. Never guess one.

Answer in "statements". For each statement:
- Set "cited_metric_paths" to the exact paths supporting it. Paths address the portfolio \
snapshot at the root (e.g. "concentration.hhi", "funds[2].expense_ratio") or a tool call by \
index. Every tool result tells you its own prefix in "cite_as" — use exactly that prefix, and \
do not count the calls yourself. If a result arrives as {"cite_as": "tool_calls[2]", "result": \
{"readiness_pct": 41.3}}, the path is "tool_calls[2].result.readiness_pct", and that call's \
inputs are at "tool_calls[2].arguments".
- Every number you write must appear in the cited evidence, to a reasonable rounding. Money is \
given in rupees, lakh and crore at once — cite the unit you actually wrote.
- A statement whose numbers you cannot cite will be discarded, so do not guess.

If the tools cannot answer the question, say so plainly in "answer_summary" and return no \
statements rather than inventing figures."""


class AskResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_summary: str
    statements: list[GroundedStatement]


@dataclass
class ToolCallRecord:
    tool: str
    arguments: dict[str, Any]
    result: Any = None
    error: str | None = None

    def as_evidence(self) -> dict[str, Any]:
        """Only successful calls contribute citable numbers; a failed call
        records its arguments so the transcript stays honest about what was
        attempted."""
        return {
            "tool": self.tool,
            "arguments": self.arguments,
            **({"error": self.error} if self.error is not None else {"result": self.result}),
        }


@dataclass
class AskOutcome:
    response: AskResponse
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    hit_iteration_cap: bool = False


def _tool_result_block(block_id: str, record: ToolCallRecord, index: int) -> dict[str, Any]:
    """Each result states its own citation prefix.

    Real usage showed why this is necessary: asked which fund had the worst
    risk-adjusted return, the model chained five calls and then cited
    `tool_calls[0]...` for figures that were actually at `tool_calls[1]`,
    consistently off by one -- it was indexing as though the `list_holdings`
    discovery call didn't count. Every statement was rejected, despite the
    numbers themselves being correct. Asking a model to track its own
    position in a ledger the server assigns, across multiple turns, is a
    counting task it has no reliable way to do; the server knows the index,
    so the server states it. The wrapper mirrors the evidence layout exactly
    (`tool_calls[i].result.<field>`) so the path the model reads back is the
    path that resolves.
    """
    if record.error is not None:
        return {
            "type": "tool_result",
            "tool_use_id": block_id,
            "content": record.error,
            "is_error": True,
        }
    return {
        "type": "tool_result",
        "tool_use_id": block_id,
        "content": json.dumps(
            {"cite_as": f"tool_calls[{index}]", "result": record.result}, default=str
        ),
    }


def _parse_answer(content: list[Any]) -> AskResponse:
    text_blocks = [block.text for block in content if block.type == "text"]
    if not text_blocks:
        raise ValueError("Claude response contained no text content")
    return AskResponse.model_validate(json.loads(text_blocks[-1]))


def run_ask_loop(
    session: Session,
    api_key: str,
    question: str,
    snapshot: PortfolioMetricsSnapshot,
    tools: dict[str, Tool] | None = None,
    max_iterations: int = MAX_ITERATIONS,
) -> AskOutcome:
    available = TOOLS if tools is None else tools
    client = anthropic.Anthropic(api_key=api_key)

    messages: list[Any] = [
        {
            "role": "user",
            "content": (
                f"PortfolioMetricsSnapshot:\n{snapshot.model_dump_json()}\n\n"
                f"Question:\n{question}"
            ),
        }
    ]
    records: list[ToolCallRecord] = []
    api_tools: list[Any] = [t.to_api_tool() for t in available.values()]

    for iteration in range(max_iterations):
        response = client.messages.create(
            model=MODEL,
            max_tokens=8192,
            thinking={"type": "adaptive"},
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": AskResponse.model_json_schema()},
            },
            tools=api_tools,
            system=_SYSTEM_PROMPT,
            messages=messages,
        )

        tool_uses = [block for block in response.content if block.type == "tool_use"]
        if not tool_uses:
            return AskOutcome(response=_parse_answer(response.content), tool_calls=records)

        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in tool_uses:
            arguments = dict(block.input or {})
            record = ToolCallRecord(tool=block.name, arguments=arguments)
            try:
                record.result = call_tool(available, session, block.name, arguments)
            except ToolError as exc:
                record.error = str(exc)
            records.append(record)
            results.append(_tool_result_block(block.id, record, len(records) - 1))
        messages.append({"role": "user", "content": results})

        if iteration == max_iterations - 1:
            # Out of turns with tool calls still pending. Report what was
            # gathered rather than a partial answer the model never wrote --
            # the caller surfaces this as its own failure state.
            return AskOutcome(
                response=AskResponse(
                    answer_summary=(
                        "Stopped after the maximum number of tool calls without reaching an "
                        "answer."
                    ),
                    statements=[],
                ),
                tool_calls=records,
                hit_iteration_cap=True,
            )

    raise AssertionError("unreachable: the loop returns from inside")
