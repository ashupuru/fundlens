"""Claude API integration for the portfolio health narrative
(docs/architecture/ai-layer.md). Bare fetch-style function, same shape as
market_data/mfapi_client.py: this module makes the call and can raise;
service.py wraps it in try/except and degrades to "unavailable" rather than
crashing the caller, matching every other external-API integration here.

Two distinct statement kinds in the response, matching two different trust
boundaries:
- `GroundedStatement` -- a numeric claim. Must trace to the snapshot;
  determinism-validated by ai/validator.py.
- `NewsContextItem` -- a sourced web claim (via the web_search tool).
  Explicitly allowed to reference the outside world; never run through the
  numeric validator. Rendered as a visually distinct "external context" card
  so it's never confused with a snapshot-grounded claim (ai-layer.md §3).
"""

import json
from typing import Literal

import anthropic
from pydantic import BaseModel, ConfigDict

from fundlens.ai.snapshot import PortfolioMetricsSnapshot

MODEL = "claude-sonnet-5"

_SYSTEM_PROMPT = """\
You are FundLens's portfolio analysis assistant. You interpret numbers, you never calculate \
them. Every numeric claim you make about the user's portfolio must come from the attached \
PortfolioMetricsSnapshot JSON and nothing else.

For each grounded statement:
- Set "cited_metric_paths" to the exact dotted/bracket paths into the snapshot that support it \
  (e.g. "allocation.equity_drift_pct", "funds[2].expense_ratio", "concentration.hhi").
- The number you cite must appear verbatim (to a reasonable rounding) in "text".
- Cover concentration, overlap, style drift, and fee drag where the snapshot has data for them \
  -- if a field is an empty list or null, say so plainly rather than inventing a number.

Separately, you may use the web_search tool to find current news or context about specific \
funds in the portfolio (fund house changes, manager departures, category headwinds). Put these \
in "news_context", never in "statements" -- they are not numeric claims and are not validated \
against the snapshot. Every news_context item must carry a real "source_url" from a web_search \
result. Do not guess or fabricate a source_url.

Never state a number that does not appear in the snapshot JSON."""


class GroundedStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: Literal["concentration", "overlap", "style_drift", "fee_drag"]
    text: str
    cited_metric_paths: list[str]


class NewsContextItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fund_name: str
    text: str
    source_url: str | None


class NarrativeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statements: list[GroundedStatement]
    news_context: list[NewsContextItem]


def _build_user_message(snapshot: PortfolioMetricsSnapshot, prior_summary: str | None) -> str:
    parts = [f"PortfolioMetricsSnapshot:\n{snapshot.model_dump_json()}"]
    if prior_summary:
        parts.append(
            f"\nSummary of the previous analysis, for context on what's changed:\n{prior_summary}"
        )
    return "\n".join(parts)


def call_claude(
    api_key: str, snapshot: PortfolioMetricsSnapshot, prior_summary: str | None = None
) -> NarrativeResponse:
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        thinking={"type": "adaptive"},
        output_config={
            "effort": "medium",
            "format": {"type": "json_schema", "schema": NarrativeResponse.model_json_schema()},
        },
        tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 3}],
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_user_message(snapshot, prior_summary)}],
    )

    text_blocks = [block.text for block in response.content if block.type == "text"]
    if not text_blocks:
        raise ValueError("Claude response contained no text content")

    parsed = json.loads(text_blocks[-1])
    return NarrativeResponse.model_validate(parsed)
