# FundLens — AI Analysis Layer

Status: **Shipped, on-demand only** (phase P2.1: `src/fundlens/ai/`) · Part of
[technical-spec.md](technical-spec.md)

Implements PRD FR-5.x. The governing rule (FR-5.2, restated as the single most important
constraint in this module): **the LLM interprets, it never calculates.** Every number in an LLM
narrative must trace back to a number the compute engine already produced.

**As shipped, vs. this doc's original sketch:**
- Model is **Claude Sonnet 5** (cost-conscious choice for a personal app), not left unspecified.
- Trigger is **on-demand only** (`POST /ai/analyze`) — §4's "auto-run on each successful monthly
  CAS refresh" is deliberately not wired up yet, to avoid automatic spending; a natural later
  addition once real cost/quality is visible.
- `PortfolioMetricsSnapshot.funds` gained two fields beyond the original sketch,
  `aum_crores`/`groww_rating`, both already available from the same `GrowwFundMetadata` join
  `expense_ratio`/`plan_type` already needed — effectively free once that join exists.
- **News/context enrichment (FR-5.4)** — originally scoped here as "P2" future work — shipped in
  this same pass, using Claude's server-side `web_search` tool (no RAG/embeddings pipeline;
  considered and rejected as overkill for a single-user app checking a few dozen funds). Modeled
  as a `NewsContextItem` (fund name, text, `source_url`) alongside `GroundedStatement` in the
  response schema — structurally separate from the validated statements, per §3's requirement
  that this content type never be confused with a snapshot-grounded claim.
- "Memory" (what changed since last time) is the `ai_analysis` table's own history: each call
  looks up the most recent prior row and summarizes its accepted statements into the prompt — no
  separate memory store.
- Per-fund qualitative commentary (§3's *other* output type — un-sourced claims like "the fund
  manager left") is still **not built**; only the sourced, `web_search`-backed version above is.

## 1. Input Contract: `PortfolioMetricsSnapshot`

A Pydantic model, built by an explicit allowlist serializer (not a generic ORM dump) from the
compute engine's output. Roughly:

```python
class PortfolioMetricsSnapshot(BaseModel):
    as_of_date: date
    portfolio_xirr: float
    benchmark_xirr: float
    benchmark_name: str
    alpha: float
    allocation: AllocationBreakdown          # % by category, vs target bands + drift
    funds: list[FundMetrics]                 # per-fund: name, category, xirr, alpha,
                                              #   expense_ratio, plan_type, verdict, verdict_score
    overlap_pairs: list[OverlapPair]          # fund_a, fund_b, overlap_pct
    fee_drag_annual_rupees: float
    concentration: ConcentrationStats         # HHI or top-N% by value
    style_drift_flags: list[str]
```

No `folio`, no `PAN`, no `AMC` account identifiers, no raw transactions. Fund/scheme *names* are
included deliberately — the PRD wants "per-fund qualitative commentary" (FR-5.1), which requires
naming the fund; this is a judgment call that fund names are not the sensitive surface here,
platform/account identity is. If the user later wants fund names withheld too, add an
`anonymize_fund_names` flag that swaps names for stable pseudonyms (`Fund A`, `Fund B`) in the
serializer — not a redaction pass on the LLM output.

## 2. Determinism Validator

Every LLM response is parsed into a structured output (Claude's tool-use / structured-output
mode, not free text parsing) with a required `cited_metric_path` per claim, e.g.:

```json
{
  "statements": [
    {
      "text": "Your large-cap allocation has drifted 8pp above target, mostly from Fund X.",
      "cited_metric_paths": ["allocation.large_cap.drift_pct", "funds[2].name"]
    }
  ]
}
```

The validator (plain Python, no LLM involved) walks `cited_metric_paths` against the actual
`PortfolioMetricsSnapshot` that was sent, and:
- Rejects the statement if a cited path doesn't resolve, or resolves to a value that doesn't
  appear (as a number, rounded consistently) anywhere in `statement.text`.
- Rejects the whole response if it contains a number-shaped token not present in the input
  snapshot (regex-scan of the free text as a second, blunter safety net beyond the citation
  check).
- On rejection: the statement is dropped from the rendered narrative and logged, not silently
  fixed — silently "correcting" LLM output would defeat the point of the boundary. A rejected
  response never blocks the deterministic dashboard; it only means that particular narrative
  paragraph doesn't render this cycle.

This is the concrete implementation of PRD §10's risk mitigation: *"validator rejects any number
not present in input JSON."*

## 3. Outputs

- **Portfolio health narrative** (FR-5.1): concentration, overlap, style drift, fee drag —
  one paragraph each, each independently validated per §2.
- **Per-fund qualitative commentary** (FR-5.1): manager changes, category headwinds,
  consistency — this is the one output type allowed to include information *not* in the
  snapshot (e.g. "the fund manager left in March 2026"), because it's explicitly qualitative/
  external, not a number claim. It is visually distinguished in the UI from the
  metric-grounded narrative (different card style, "external context" label) so the user never
  confuses "the compute engine said X" with "the model said it read Y somewhere."
- **Fund overlap analysis** (FR-5.3, P1): pairwise holding-overlap % is itself compute-engine
  output (from monthly AMC portfolio disclosures, a new ingestion source, not an LLM
  computation) — the AI layer only narrates it, it doesn't compute the overlap percentage.
- **News/context enrichment** (FR-5.4, P2): web-search-grounded, sources shown inline; treated
  as advisory color, explicitly not fed back into the rubric engine (§compute-engine.md §5),
  keeping the deterministic rubric free of unverifiable inputs.

## 4. Trigger Cadence (PRD §12.4)

**Superseded in part by §6**, which adds a second entry point. Both: auto-run on each successful monthly CAS refresh (produces the monthly narrative,
cached until the next refresh), and on-demand re-analysis (e.g. after the user tweaks an
assumption or reviews a verdict). On-demand calls reuse the same snapshot serializer and
validator — there is exactly one code path into the LLM, never a "quick unstructured call"
shortcut, so the determinism boundary can't be bypassed by a different trigger.

## 5. Failure Handling

If the Claude API is unreachable or returns an invalid/fully-rejected response, the dashboard
renders normally with the narrative section showing "AI analysis unavailable" rather than
blocking — consistent with the NFR that the deterministic core must never depend on the LLM
being reachable (PRD: LLM is explicitly "optional" in the portability NFR, §6).

## 6. Tool Calling — POST /ai/ask

Shipped after §1-§5, and the reason §4's "exactly one code path into the LLM" now reads as two:
the health narrative (`ai/service.py`) and a user-typed question (`ai/ask_service.py`). Both go
through the same determinism validator; neither can bypass it.

**Why.** §1's snapshot is built once and fixed, so the LLM could only ever comment on what was
precomputed. Any question whose answer depends on an input the *user* picks — "can I retire at
52?", "what if I pause SIPs for two years?" — was unanswerable by construction, even though
`compute/retirement.py` and `compute/monte_carlo.py` could answer it. The tools close that gap
without moving the boundary: **the model chooses which computation to run and with what
arguments; the engine performs every computation.** No number in an answer originates in the
model.

**The loop** (`ai/tool_loop.py`). Claude receives the snapshot plus the tool schemas and calls
whatever it needs, up to `MAX_ITERATIONS` (8). Every call is recorded to a ledger. A tool that
fails returns its error to the model as an `is_error` tool result rather than raising — a model
that guessed a goal_id that doesn't exist should get the chance to call `list_goals` and correct
itself. Hitting the iteration cap is reported as its own failure state, never as a partial
answer.

Hand-written rather than `client.beta.messages.tool_runner`: recording the ledger, capping
iterations and validating arguments before dispatch is most of what this loop does, which leaves
the runner little to own, and it would add a beta API surface next to a plain blocking
`messages.create`. Worth reconsidering if the loop grows much.

**The tools** (`ai/tools/`). Two groups: planning (retirement projection, scenarios and Monte
Carlo, all accepting what-if overrides; goal projection and Monte Carlo; `list_goals`) and
per-fund analytics (`list_holdings`, fund metrics, rolling returns, risk metrics, verdict, costs).
The discovery tools exist so the model never guesses an `amfi_code` or `goal_id`.

Argument resolution routes through `planning/service.py`, shared with the HTTP routes rather than
reimplemented — including its rule that what-if overrides apply to a *detached* profile copy, so
asking about retiring at 62 can never quietly rewrite the saved plan.

**Read-only is enforced by construction**: the registry contains only reading functions, and a
test greps each registered callable for any mutating one. The failure mode justifies the
paranoia — an LLM able to write to the ledger it reasons about could corrupt real financial
history while sounding confident about it.

**Money is emitted in rupees, lakh and crore at once** (`ai/tools/serialize.py`). The validator
matches a cited value against the number *as written in the prose*, and nobody writes a corpus in
paise or in full rupees; exposing a single unit would have made almost every naturally-worded
sentence uncitable, which in practice would have meant loosening the check.

### Evidence, and one honest caveat

`validator.build_evidence` returns the snapshot at the root plus the tool ledger under
`tool_calls[i]`. Snapshot paths are therefore unchanged — nothing that validated under §2
validates differently now. Tool arguments are citable alongside results, deliberately, so "at 52
you fall short" can cite the 52 it was computed with.

**`scan_for_foreign_numbers` weakens as the evidence grows.** With a dozen tool results in scope,
most number-shaped tokens will land within tolerance of *something*, so §2's blunter safety net
degrades toward a coarse backstop. This is a real reduction in what that particular check buys
and is recorded rather than glossed. The per-statement citation check is unaffected: it verifies
that each cited path resolves *and* that its value appears in the text, which is a per-path test
that additional evidence cannot dilute. That check was always the primary gate and remains it.

### Two things real usage changed

Both were found by asking real questions against the real portfolio, not by review.

**Tool results state their own citation index.** Asked which fund had the worst risk-adjusted
return, the model chained five calls and then cited `tool_calls[0]...` for figures that were
actually at `tool_calls[1]` — consistently off by one, indexing as though the `list_holdings`
discovery call didn't count. Every statement was rejected even though the numbers were correct.
Tracking your own position in a server-assigned ledger across multiple turns is a counting task a
model has no reliable way to do, so each tool result is now returned wrapped as
`{"cite_as": "tool_calls[2]", "result": {...}}`. The wrapper mirrors the evidence layout exactly,
so the prefix the model reads back concatenates into a path that resolves. Re-running the same
question afterwards produced correctly-cited, validated statements.

**`answer_summary` is scanned, and flagged rather than suppressed.** The summary carries no
citations by construction, yet it is the most prominent text on the page and is dense with
figures — leaving it unchecked put the least-verified prose in the most-read place. It now goes
through `scan_for_foreign_numbers`. Blanking it on any hit was tried first and was wrong: the
scan over-rejects by design, and the first real summary it caught was flagged for the number 50
in a benchmark name, which would have deleted an otherwise sound paragraph over a false positive.
So unverified figures are *named* and the summary renders visually distinct — the same treatment
§3 already gives `news_context` for unvalidated content. The validated statements remain the
authoritative part. The scan is recomputed from the persisted evidence when reading history, so
it can never drift from the text it describes.

### Persistence

One `ai_question` row per ask, holding the question, the tool ledger, the exact evidence, and
both accepted and rejected statements — so a rendered answer can be re-verified later rather than
merely trusted. Kept separate from `ai_analysis`: the shapes differ (a tool ledger vs a web-news
list), and `ai_analysis`'s "summarize the previous row into the next prompt" memory rule must not
start pulling in unrelated ad-hoc questions.

The tool chain is surfaced in the UI (expandable), unlike `rejected` — which computations ran with
which arguments is exactly what makes a tool-derived answer checkable instead of magic.
