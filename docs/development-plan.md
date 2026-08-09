# FundLens — Phase-Wise Development Plan

Status: Draft v1.0 · Companion to [architecture/technical-spec.md](architecture/technical-spec.md)

This expands the PRD's roadmap (§8) into engineering-level tasks, exit criteria, and test gates.
Phase boundaries and FR mappings follow the PRD exactly; each phase adds the concrete build order
and the specific test/verification gate that must pass before the next phase starts.

Estimates assume one senior full-stack-capable engineer working solo (matches the PRD's personal
tool context); adjust proportionally for a team.

## Phase 0a — Foundations (3–4 days, precedes the PRD's "P0")

Not in the PRD roadmap explicitly, but everything else depends on it existing first.

1. Repo scaffolding: Python package layout (`ingestion/`, `compute/`, `api/`, `reports/`,
   `frontend/`), `pyproject.toml`, pytest config, pre-commit (ruff, mypy).
2. SQLite + SQLCipher wiring: DB init, migration tool (`alembic` against SQLite), Keychain
   integration for the encryption key.
3. FastAPI skeleton: health check, OpenAPI docs, CORS for local React dev server.
4. React + Vite skeleton: routing shell, API client codegen from OpenAPI.
5. CI: GitHub Actions (or equivalent) running lint + pytest on every push, since accuracy
   regressions (PRD §10) must be caught automatically, not by memory.

**Exit criteria:** `uvicorn` serves an empty API + a React shell that round-trips one API call;
CI green on a trivial commit.

## Phase P0 — Truth Engine (4–6 weeks)

**PRD scope:** FR-1.1/1.2/1.4, FR-2.1/2.2, FR-3.1/3.2, FR-10.2
**Outcome:** trusted XIRR + full history + capital gains, reconciled against the source CAS.

### P0.1 — Ingestion (≈1.5 wk)
- `casparser` integration: password-protected CAS decrypt + parse, mapped into
  `raw_cas_upload`/`raw_transaction` ([data-model.md](architecture/data-model.md) §2).
- Natural-key dedupe on re-upload (FR-1.2); gap detection (CAS date range vs known folio start).
- CSV import escape hatch (FR-1.3b) with the same natural-key path, so it can never bypass dedupe.
- **Test gate:** golden CAS fixture files (at least 2 real, anonymized CAS vintages) parse to
  exact expected transaction counts and sums; re-upload of an overlapping CAS produces zero
  duplicate rows.

### P0.2 — Market data (≈0.5 wk)
- AMFI NAVAll / mfapi.in fetcher into `nav_history`; scheduled daily refresh via APScheduler.
- `data_freshness` tracking table + stale-data banner contract (technical-spec.md §7).
- **Test gate:** fetch failure (mocked 500/timeout) degrades to a visible stale banner, doesn't
  crash the app or silently serve old data as fresh.

### P0.3 — Lot accounting + portfolio views (≈1 wk)
- FIFO lot allocator (compute-engine.md §1), including segregation/side-pocketing handling.
- Views: by PAN, fund, AMC, category, platform/folio (FR-2.2 subset; goal/tax-status views land
  in P1/P2 once goals and the tax engine exist).
- **Test gate:** `hypothesis`-generated transaction sequences never produce negative
  `units_remaining`; cost-basis sum across all lots for a folio equals invested amount minus
  realized-lot cost.

### P0.4 — XIRR + core metrics (≈1 wk)
- XIRR solver (compute-engine.md §2) at fund/folio/portfolio level; CAGR, absolute return,
  realized/unrealized P&L (FR-3.2).
- Named edge cases handled explicitly: fully-exited fund, <1yr fund, degenerate flows.
- **Test gate (accuracy NFR):** golden-file suite — XIRR within 0.01% of Excel's `XIRR()` on the
  same cash flows, across ≥10 hand-verified fund fixtures including every named edge case.

### P0.5 — Capital gains report (≈1 wk)
- Minimal tax classification (equity/debt LTCG/STCG only — full grandfathering/exemption
  machinery is P1) sufficient to produce scheme-wise gains.
- CSV/PDF export in schedule-112A shape (FR-10.2).
- **Test gate:** total gains reconcile with the CAS's own capital-gains statement (where present)
  within rounding, on every fixture portfolio.

### P0.6 — Data quality dashboard, minimal (≈0.5 wk)
- Folios found, date-range coverage, unmatched schemes, failed XIRR computations surfaced in the
  UI — pulled forward from PRD §5.4/P1 because trust in P0's numbers depends on visible
  provenance from day one, not after judgment features ship.

**Phase exit criteria (blocking, do not proceed to P1 until all pass):**
- 100% of transactions from each fixture CAS reconciled to the CAS's own summary page (PRD §9).
- XIRR parity ±0.01% vs Excel on the full fixture set.
- Full recompute of a 60-fund/15-yr/5,000-txn synthetic portfolio completes in <30s (perf NFR);
  incremental single-day NAV update in <5s.

## Phase P1 — Judgment (4 weeks)

**PRD scope:** FR-4 (benchmarks), FR-6.1/6.2 (verdicts + tax), FR-7.1, Direct/Regular audit
**Outcome:** "Hold or sell, and what it costs me."

### P1.1 — Benchmark data + simulation engine (≈1 wk)
- Source and cache TRI series for Nifty 50, BSE 100, Midcap 150, Smallcap 250, category debt
  indices; implement the index-fund-NAV-as-TRI-proxy fallback (PRD §10 risk) with the
  `proxy`/`proxy_note` fields from the data model.
- Benchmark simulation engine (compute-engine.md §4): identical-cash-flow XIRR against benchmark,
  per fund and portfolio-wide, category-correct default with override (FR-4.3), index-fund
  counterfactual (FR-4.4).
- **Test gate:** benchmark XIRR computed on a fixture with known-by-hand cash flows matches a
  spreadsheet cross-check; category-default benchmark selection correct for every
  `scheme_metadata.category` value in the fixture set.

### P1.2 — Full tax engine (≈1 wk)
- Versioned assumption-driven rules (compute-engine.md §3): grandfathering, post-2023 debt rules,
  LTCG exemption tracking across the FY per PAN.
- Assumption registry CRUD (PRD §5.7) — needed now because the tax engine is the first consumer.
- **Test gate:** capital-gains figures from P0.5 recomputed through the full engine match the
  CAMS/KFintech capital-gains statement within rounding, including at least one fixture crossing
  the pre/post-2023 debt-fund boundary and one exercising LTCG-exemption carry-forward within a
  FY.

### P1.3 — Recommendation rubric + verdicts (≈1 wk)
- Scored rubric engine (compute-engine.md §5): rolling alpha, consistency, cost, overlap,
  category fit — weights in the assumption registry.
- Direct-vs-Regular audit (§5.3/compute-engine.md §6) wired into the cost component.
- Every SELL/SWITCH verdict required to carry the tax-friction statement (FR-6.2) —
  schema-enforced, not just UI-enforced.
- **Test gate:** verdict output schema validated (non-null tax_impact on non-HOLD verdicts);
  manual review of verdicts on the fixture portfolio against expert judgment (qualitative gate,
  since there's no ground truth for "correct" verdicts).

### P1.4 — Allocation & drift (≈0.5 wk)
- Risk profile onboarding questionnaire + persona presets (PRD §3); actual-vs-target allocation
  and drift % (FR-7.1).
- **Test gate:** drift % matches hand-computed allocation on the fixture portfolio for all four
  personas (conservative/balanced/aggressive/custom).

### P1.5 — UI: verdicts + benchmark dashboards (≈0.5 wk)
- Fund-level and portfolio-level benchmark comparison views, verdict cards with drill-down to
  rubric scores, direct-vs-regular flags surfaced in the fund list.

**Phase exit criteria:** every held fund shows a verdict with reasoning, confidence, and tax
impact where applicable; portfolio XIRR-vs-benchmark alpha visible at the top level; XIRR parity
with Zerodha Coin on any funds also held there, ±0.05% (PRD §9).

## Phase P2 — Intelligence (4 weeks)

**PRD scope:** FR-5 (AI layer), FR-9 (alternatives), FR-8 (goals), FR-10.1 (yearly report)
**Outcome:** full advisor experience — this is the PRD's stated MVP-plus target
(MVP itself is end of P1 per PRD §8).

### P2.1 — AI analysis layer (≈1.5 wk)
- `PortfolioMetricsSnapshot` serializer (ai-layer.md §1), Claude API integration with structured
  output, determinism validator (ai-layer.md §2).
- Portfolio health narrative + per-fund commentary (FR-5.1); trigger on monthly CAS refresh +
  on-demand (PRD §12.4).
- **Test gate:** adversarial test — feed the validator a deliberately-hallucinated response
  containing a plausible-but-absent number; confirm it's rejected and logged, not rendered.
  Confirm zero PII (folio/PAN/platform) present in any outbound API payload via a payload-schema
  assertion in tests, not manual inspection.

### P2.2 — Fund overlap analysis (≈0.5 wk)
- Ingest monthly AMC portfolio disclosures (new data source); pairwise holding-overlap %
  (FR-5.3), feeding both the rubric's overlap component (already stubbed in P1.3) and the AI
  narrative.

### P2.3 — Alternatives engine (≈0.5 wk)
- Same-category candidate ranking (compute-engine.md §8) attached to every SELL/SWITCH verdict
  (FR-9.1).

### P2.4 — Goal planning (≈1 wk)
- Goal CRUD spanning PANs (MF-only corpus, PRD §12.1); projected corpus, required SIP, ±2%
  sensitivity table (FR-8.1/8.2).
- **Test gate:** required-SIP calculation cross-checked against a standard future-value-of-annuity
  formula by hand for 3 fixture goals.

### P2.5 — Yearly report (≈0.5 wk)
- FY-aligned (Apr–Mar) report generator (FR-10.1): opening/closing value, XIRR (year +
  since-inception), benchmark comparison, best/worst funds, drift, estimated fees, realized
  gains — reuses every engine built in P0/P1, this phase is assembly + rendering (WeasyPrint),
  not new computation.

**Phase exit criteria:** a fresh CAS upload reaches full portfolio insight (dashboards + at least
one AI narrative + at least one goal projection, if a goal is defined) in under 5 minutes
end-to-end (PRD §9 success metric).

## Phase P3 — Polish (scope-flexible, PRD leaves duration open)

**PRD scope:** rolling returns, overlap % refinements, Monte Carlo, digests, watchlist
**Outcome:** depth & retention — explicitly the least time-boxed phase; sequence by user demand
rather than committing fixed weeks upfront.

Suggested order (highest leverage first, per PRD's own P2/P1 tags within this bucket):
1. Rolling 1/3/5-yr returns (FR-3.3) — upgrades P1's point-in-time alpha to a consistency view,
   directly strengthens the rubric engine already built.
2. Risk-adjusted metrics: Sharpe, Sortino, max drawdown vs category peers (FR-3.4).
3. Age/horizon glide-path suggestions (FR-7.3).
4. Monte Carlo goal-probability simulation (FR-8.3).
5. Simplification mode (FR-9.2).
6. On-demand snapshot report + monthly one-pager (FR-10.3), email/notification digest (FR-10.4).
7. Watchlist (FR-9.4 area, tracking funds not owned).

## Cross-Phase Concerns

- **CAS-vintage regression fixtures** (technical-spec.md §9) should be collected incrementally
  starting in P0.1 — every new CAS format encountered during real usage becomes a permanent
  fixture, since `casparser` upgrades are the PRD's top-flagged risk (§10).
- **Assumption registry** versioning (introduced in P1.2) must be treated as append-only from
  the start — changing a tax rate in place would silently invalidate the reproducibility
  guarantee (PRD §5.7) for every report generated before the change.
- **No phase ships a SELL-capable verdict without its tax-friction statement** — this is a
  schema-level constraint from P1.3 onward, not a per-feature checklist item, so it can't
  regress silently in P2/P3 work.
