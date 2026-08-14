# FundLens — Technical Specification

Status: Draft v1.0 · Companion to `docs/MF_Analyzer_PRD.pdf` · See also [data-model.md](data-model.md), [compute-engine.md](compute-engine.md), [ai-layer.md](ai-layer.md), [ADR-0001](../decisions/adr-0001-python-fastapi-sqlite-stack.md)

## 1. Purpose & Scope

This spec translates the FundLens PRD into an implementable system design for a single-user,
local-first mutual fund portfolio analyzer. It covers architecture, module boundaries, data
model, algorithms, security, and non-functional design responses. Feature IDs (FR-x.x) refer to
the PRD; every module below is traceable back to one or more FR entries.

Out of scope here (unchanged from PRD §2): transaction execution, non-MF assets, multi-tenant
SaaS, SEBI-regulated advisory framing.

## 2. System Context & Constraints

- **Single user, single machine.** Runs on macOS (Mac Mini/MacBook), no server to operate.
- **Privacy-first (NFR, PRD §6, §5.5).** All financial data — transactions, PAN, folio numbers —
  stays on disk, encrypted at rest. The only network calls are: (a) AMFI/mfapi.in NAV fetch,
  (b) benchmark TRI data fetch, (c) optional Claude API calls carrying only anonymized,
  aggregated metrics.
- **Source of truth is file-based.** Raw CAS PDFs are immutable inputs (PRD §5.8). All derived
  state (transactions, lots, XIRR, verdicts) must be fully reconstructable by replaying raw CAS
  files + NAV history through the compute engine. This makes the system event-sourced at the
  ingestion boundary, not merely "a database with some tables."
- **Accuracy is the product.** NFR: XIRR within 0.01% of Excel's XIRR; capital gains reconcile
  with CAMS/KFintech statements within rounding. Every number must be drillable to source
  transactions (PRD §5.6, "Explainability everywhere").

## 3. Architecture Overview

```
                         ┌─────────────────────────────────────────────┐
                         │              CLI / Local Web UI              │
                         │         (React SPA, served by API)           │
                         └───────────────────┬───────────────────────────┘
                                              │ HTTP (localhost only)
                         ┌───────────────────▼───────────────────────────┐
                         │                FastAPI app                    │
                         │  routers: ingest / portfolio / metrics /      │
                         │  verdicts / goals / reports / assumptions     │
                         └───────┬───────────────────────┬───────────────┘
                                 │                        │
                 ┌───────────────▼──────────┐   ┌─────────▼─────────────┐
                 │      Ingestion module      │   │   Compute engine       │
                 │  casparser wrapper, CSV     │   │  lots (FIFO), XIRR,   │
                 │  import, dedupe/merge,      │   │  tax engine, drift,   │
                 │  gap detection              │   │  rubric/verdicts      │
                 └───────────────┬──────────┘   └─────────┬─────────────┘
                                 │                         │
                 ┌───────────────▼─────────────────────────▼─────────────┐
                 │                    SQLite (SQLCipher)                  │
                 │  raw_transactions (immutable) · nav_history ·          │
                 │  benchmark_history · derived views · assumptions       │
                 └───────────────┬─────────────────────────┬─────────────┘
                                 │                         │
                 ┌───────────────▼──────────┐   ┌─────────▼─────────────┐
                 │   Market data fetchers    │   │   AI analysis layer    │
                 │  AMFI NAVAll / mfapi.in,  │   │  Claude API, structured│
                 │  TRI index cache          │   │  JSON in / narrative   │
                 │  (scheduled job)          │   │  out, validator        │
                 └───────────────────────────┘   └────────────────────────┘
                                                              │
                                                  ┌───────────▼────────────┐
                                                  │     Report generator     │
                                                  │  HTML→PDF, CSV export    │
                                                  └───────────────────────────┘
```

All arrows within the dashed local boundary are in-process or localhost calls. Only the market
data fetchers and the AI analysis layer cross the machine boundary, and both are called with
sanitized payloads (§7.3).

## 4. Component Breakdown

| Module | Responsibility | Key FRs |
|---|---|---|
| Ingestion | Decrypt + parse CAS PDFs via `casparser`; CSV import escape hatch; idempotent merge; gap detection | FR-1.1–1.3, FR-1.5 |
| Market data | Fetch/cache daily NAVs, TRI benchmark series, scheme metadata | FR-1.4 |
| Portfolio & lots | Reconstruct per-folio lot ledger (FIFO), running units, cost basis, current value | FR-2.1–2.4 |
| Metrics engine | XIRR, CAGR, absolute/realized/unrealized return, rolling returns, risk-adjusted stats | FR-3.x |
| Benchmark engine | Cash-flow-identical benchmark simulation, alpha, index-fund counterfactual | FR-4.x |
| Tax engine | STCG/LTCG classification per lot, grandfathering, exemption tracking, harvesting | §5.1, FR-6.2, FR-6.3 |
| Recommendation engine | Scored rubric, Hold/Sell/Switch verdicts, alternatives, simplification mode | FR-6.x, FR-9.x |
| Allocation & goals | Actual vs target allocation, drift, glide path, goal projection, Monte Carlo | FR-7.x, FR-8.x |
| AI analysis layer | Structured-metrics-in → narrative-out, with a determinism validator | FR-5.x |
| Reports | Yearly report, ITR capital-gains export, snapshot, digests | FR-10.x |
| Data quality dashboard | Ingestion coverage, unmatched schemes, failed computations | §5.4 |

Each module is a Python package with its own unit test suite; only `Portfolio & lots`,
`Metrics engine`, `Benchmark engine`, and `Tax engine` are pure functions over the SQLite-derived
dataset (no I/O), which is what makes the golden-file accuracy tests (§8) tractable.

## 5. Tech Stack

Decision recorded in [ADR-0001](../decisions/adr-0001-python-fastapi-sqlite-stack.md): this
project uses **Python** end-to-end rather than the org-default TypeScript/Fastify/Postgres stack.
Summary:

- **Backend:** Python 3.12, FastAPI, Pydantic v2 for schema validation at every module boundary.
- **Data processing:** pandas / duckdb for bulk transaction and NAV-series work; `scipy.optimize`
  (Brent/Newton) for XIRR — not a hand-rolled solver, to avoid convergence bugs.
- **Storage:** SQLite with SQLCipher (encryption at rest). WAL mode for concurrent read during
  background NAV refresh.
- **CAS parsing:** `casparser` (pinned version, PRD §10 risk).
- **Frontend:** React + TypeScript SPA (Vite), served as static assets by FastAPI. TypeScript
  is used here — it is the right tool for the UI layer and doesn't conflict with the backend
  language choice.
- **Reports:** Jinja2 → HTML → PDF via WeasyPrint (pure-Python, no headless-Chrome dependency).
- **Scheduler:** APScheduler in-process (daily NAV refresh, monthly CAS-refresh reminder) — no
  external queue needed at single-user scale.
- **AI layer:** Anthropic Python SDK (`anthropic`), Claude API.
- **Testing:** pytest, golden-file fixtures for XIRR/tax parity, `hypothesis` for property tests
  on the FIFO lot allocator.

## 6. Storage Capacity: Will SQLite Actually Hold This?

Worth checking explicitly rather than assuming, since "SQLite" and "financial analytics" can
sound like a mismatch. Sizing against the PRD's actual data shapes:

| Data set | Rough volume | Notes |
|---|---|---|
| Held-portfolio transactions | ≤ 5,000 rows | the NFR's own benchmark shape |
| Held-portfolio NAV history | ~60 schemes × ~5,500 daily rows ≈ 330K rows | 15-year window |
| Full AMFI universe NAV cache (for the alternatives/watchlist engine to rank funds not held) | ~10,000 scheme codes × ~10yr daily ≈ 36M rows, ~1.5–2GB | worst-case, only needed once P2 alternatives/watchlist features are built |
| Benchmark/TRI series | low thousands of rows | few indices × decades |

Even the worst-case full-universe NAV cache is two orders of magnitude below where SQLite starts
to strain (SQLite is routinely used at billions-of-rows / hundreds-of-GB scale for read-heavy
workloads). The workload here is also exactly SQLite's strength, not a weak point: single writer,
many reads, no concurrent multi-client access — this is a desktop analytical tool for one person,
not a multi-tenant server. WAL mode gives non-blocking reads during the daily NAV-refresh write.

Where SQLite would stop being sufficient: sustained concurrent writers (a genuinely multi-user
deployment) — which is precisely the scenario [ADR-0001](../decisions/adr-0001-python-fastapi-sqlite-stack.md)
already flags as a revisit trigger, not a v1 concern. If a specific analytical query over the
full-universe NAV cache ever proves slow despite indexing, the escape hatch is letting duckdb
`ATTACH` the SQLite file directly for that query (already planned for the compute engine, §5) —
not a database migration.

## 7. Non-Functional Requirements → Design Response

| NFR | Target | Design response |
|---|---|---|
| Accuracy | XIRR within 0.01% of Excel | `scipy.optimize.brentq` on the XIRR objective with a bracketed search + Newton polish; golden-file regression suite (§9) diffs against Excel-computed XIRR fixtures on every commit |
| Performance | Full recompute: 60 funds / 15 yrs / 5,000 txns in < 30s; incremental NAV update < 5s | Vectorized pandas/duckdb over transactions instead of row-by-row Python loops; lot ledger and XIRR cash-flow arrays built once per fund, memoized by a content hash of (transactions, NAV series) so incremental NAV-only updates skip lot re-derivation entirely |
| Reliability | Graceful degradation on feed failure, never silent | Every fetcher records a `data_freshness` row (source, last_success_at, status); UI renders a stale-data banner keyed off this table rather than trusting the last cached value implicitly |
| Security | Encrypted local DB; PDF passwords never persisted; PII excluded from LLM calls | SQLCipher-encrypted DB keyed from an OS-keychain-stored key; CAS password held only in-memory for the duration of a single parse call; LLM payload builder is an explicit allowlist serializer (§8.3), not a redaction filter |
| Portability | macOS, no cloud dependency except NAV feeds + optional LLM | Single `pip install` + `uvicorn` entrypoint; SQLite file defaults to `fundlens.db` in the backend's working directory (override via `FUNDLENS_DB_PATH`); app functions fully offline except ingestion/refresh/AI actions, each independently disable-able |

## 8. Security & Privacy Architecture

### 8.1 At-rest encryption
SQLite DB opened via SQLCipher with a key derived from a passphrase stored in the macOS Keychain
(`Keychain Services` via `keyring` package). No plaintext financial data touches disk outside
this file. Report exports (PDF/CSV) are written to a user-chosen location and are explicitly the
user's responsibility once exported — this boundary is called out in the UI.

### 8.2 CAS password handling
CAS PDF passwords (typically PAN-derived) are supplied per-upload, used in-memory to decrypt via
`casparser`, and discarded immediately after parsing. Never written to disk, logs, or the DB. This
satisfies PRD §5.5 ("No account credentials ever stored").

### 8.3 LLM payload boundary
The AI analysis layer never receives PDFs, raw transactions, folio numbers, or PAN. It receives a
`PortfolioMetricsSnapshot` Pydantic model (defined in [ai-layer.md](ai-layer.md)) built by an
explicit serializer function — an allowlist, not a blocklist, so a new field added to the DB
schema does not leak to the LLM by default. Fund/scheme names are retained (needed for useful
narrative) but folio numbers, PAN, and platform identity are masked or omitted. This is a
stricter reading of PRD §5.5/§6 than "mask PII" — the serializer's input type and the LLM
client's input type are structurally different, so there is no code path by which raw rows can
reach the API.

### 8.4 Determinism boundary
See [ai-layer.md](ai-layer.md) §2 for the validator that rejects any LLM output containing a
number not traceable to the input snapshot (PRD FR-5.2, §10 risk table).

## 9. Testing Strategy

- **Golden-file accuracy suite:** hand-verified (folio, transactions) → (XIRR, CAGR, capital
  gains) fixtures, including the PRD's named edge cases — fully-exited funds, funds < 1 year old,
  switch-in/out pairs, dividend reinvestment, segregated portfolios. Run on every change to the
  compute engine.
- **CAS-vintage regression tests:** one fixture PDF per CAS format vintage encountered, re-parsed
  on every `casparser` version bump (PRD §10 risk: "CAS format changes break parsing").
  Deferred until real vintage samples are collected; tracked as a P0 exit-criterion gap.
- **Property-based tests:** `hypothesis`-generated transaction sequences checked for FIFO
  lot-allocator invariants (units never go negative, cost basis sums reconcile).
  Deferred to P0 hardening once the lot allocator's core shape is stable — writing property
  tests against a bulk vectorized implementation is a POC v0 activity.
- **Reconciliation tests:** total invested amount and capital gains, summed from derived state,
  must match the CAS's own summary page and the CAMS/KFintech capital-gains statement within
  rounding (NFR, PRD §9 success metric).
- **API contract tests:** Pydantic schemas double as OpenAPI contracts; snapshot-tested so the
  frontend never silently breaks on a backend field rename.

## 10. Deployment & Runtime Model

Single-machine, single-process deployment: `uvicorn` serves both the API and the built React
static bundle. No Docker requirement for v1 (adds friction on a personal Mac Mini setup with no
deployment target beyond "runs on my machine"); a `Dockerfile` may be added later purely for
reproducible dev environments, not runtime necessity. Background jobs (NAV refresh, monthly CAS
reminder) run via APScheduler inside the same process — acceptable at single-user scale and
avoids standing up Redis/Celery infrastructure the PRD's NFRs don't call for.

Config (API keys, DB passphrase reference, feature flags) lives in a local `.env`, consistent
with the existing `.env.example` in the repo root.
