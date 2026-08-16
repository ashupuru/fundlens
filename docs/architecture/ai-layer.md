# FundLens — AI Analysis Layer

Status: Draft v1.0 · Part of [technical-spec.md](technical-spec.md)

Implements PRD FR-5.x. The governing rule (FR-5.2, restated as the single most important
constraint in this module): **the LLM interprets, it never calculates.** Every number in an LLM
narrative must trace back to a number the compute engine already produced.

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

Both: auto-run on each successful monthly CAS refresh (produces the monthly narrative,
cached until the next refresh), and on-demand re-analysis (e.g. after the user tweaks an
assumption or reviews a verdict). On-demand calls reuse the same snapshot serializer and
validator — there is exactly one code path into the LLM, never a "quick unstructured call"
shortcut, so the determinism boundary can't be bypassed by a different trigger.

## 5. Failure Handling

If the Claude API is unreachable or returns an invalid/fully-rejected response, the dashboard
renders normally with the narrative section showing "AI analysis unavailable" rather than
blocking — consistent with the NFR that the deterministic core must never depend on the LLM
being reachable (PRD: LLM is explicitly "optional" in the portability NFR, §6).
