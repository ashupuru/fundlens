# FundLens

**Personal mutual fund portfolio analyzer** — local-first, privacy-first, accuracy-obsessed.

FundLens turns your CAMS/KFintech Consolidated Account Statement (CAS) into a trusted view of performance, tax, benchmarks, and planning. Everything runs on your machine. Financial data stays on disk; the only network calls are NAV/benchmark feeds and an optional Claude API for narrative insights.

## Why it exists

Most portfolio apps optimize for engagement. FundLens optimizes for **numbers you can trust**:

- **XIRR within 0.01% of Excel** on the same cash flows
- Capital gains that reconcile with registrar statements (within rounding)
- Every figure drillable back to source transactions
- Full recomputation from immutable CAS uploads + NAV history

## Features

| Area | What you get |
|------|----------------|
| **Ingestion** | Password-protected CAS PDF via `casparser`; CSV escape hatch; Zerodha & Dezerv imports; idempotent re-upload |
| **Portfolio** | FIFO lot ledger, cost basis, current value by fund / AMC / category / folio |
| **Metrics** | XIRR, CAGR, absolute / realized / unrealized P&L, edge-case handling (exited funds, short holding periods) |
| **Benchmarks** | Cash-flow-identical index simulation, alpha, category-aware defaults |
| **Tax** | STCG/LTCG classification, grandfathering, assumption-driven rules, capital-gains exports |
| **Judgment** | Hold / sell / switch style verdicts grounded in portfolio metrics |
| **Planning** | Allocation drift, goals, retirement projections |
| **AI (optional)** | Structured metrics in → narrative out (Anthropic); app works fully offline without a key |
| **Reports** | Yearly report (HTML/PDF), data-quality dashboard |

## Architecture

```
React SPA (Vite)  ──HTTP localhost──▶  FastAPI
                                         │
                    ┌────────────────────┼────────────────────┐
                    ▼                    ▼                    ▼
               Ingestion            Compute engine       Market data
            (CAS / CSV / brokers)   (lots, XIRR, tax,    (AMFI / mfapi,
                                    benchmarks, …)       benchmarks)
                                         │
                                         ▼
                              SQLite (encrypted at rest)
```

Single-user, single-machine. No Postgres, no Redis, no multi-tenant SaaS surface.

Stack choice is documented in [ADR-0001](docs/decisions/adr-0001-python-fastapi-sqlite-stack.md) (Python/FastAPI/SQLite over the org TypeScript default, primarily for `casparser` and local-first ops).

## Tech stack

| Layer | Stack |
|-------|--------|
| Backend | Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy, Alembic |
| Compute | NumPy, SciPy (XIRR), FIFO lot accounting, tax & benchmark engines |
| Storage | SQLite (+ keyring for encryption key) |
| Frontend | React 19, TypeScript, Vite, Recharts |
| Reports | Jinja2 → WeasyPrint PDF |
| AI | Anthropic Python SDK (optional) |
| Tooling | `uv`, `pnpm`, pytest, ruff, mypy, oxlint |

## Prerequisites

- **macOS** (primary target; CI runs on `macos-latest`)
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- [pnpm](https://pnpm.io/) 11+ and Node 22+
- Homebrew **pango** (for WeasyPrint PDF rendering): `brew install pango`

## Quick start

```bash
# 1. Clone and enter the repo
git clone <repo-url> fundlens && cd fundlens

# 2. Python deps (includes dev extras: pytest, ruff, mypy)
uv sync --extra dev

# 3. Frontend deps
cd frontend && pnpm install && cd ..

# 4. Optional: local env
cp .env.example .env
# Set FUNDLENS_ANTHROPIC_API_KEY only if you want the Ask / AI features

# 5. Start backend + frontend
./scripts/start.sh
```

| Service | URL |
|---------|-----|
| UI | http://localhost:5173 |
| API | http://localhost:8000 |
| OpenAPI docs | http://localhost:8000/docs |

Helper scripts:

```bash
./scripts/status.sh    # health check
./scripts/stop.sh      # stop both services
./scripts/rebuild.sh   # stop, re-sync deps, clear runtime state, restart
./scripts/backup-db.sh # backup the SQLite DB
```

### Manual start (without scripts)

```bash
# Backend
uv run uvicorn fundlens.api.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend && pnpm run dev
```

### Configuration

Variables use the `FUNDLENS_` prefix (see `.env.example`):

| Variable | Purpose |
|----------|---------|
| `FUNDLENS_DB_PATH` | SQLite file path (default: `./fundlens.db`) |
| `FUNDLENS_CORS_ALLOW_ORIGINS` | Allowed CORS origins (default includes `http://localhost:5173`) |
| `FUNDLENS_ANTHROPIC_API_KEY` | Optional; required only for AI analysis |

## Development

```bash
# Lint / typecheck / test (backend)
uv run ruff check .
uv run mypy
uv run pytest -q

# Frontend
cd frontend
pnpm run lint
pnpm run build          # tsc + vite build
pnpm run gen:api-types  # regenerate OpenAPI → src/api/schema.d.ts
```

CI (`.github/workflows/ci.yml`) runs the same gates on every push/PR to `main`.

## Project layout

```
fundlens/
├── src/fundlens/          # Backend package
│   ├── api/               # FastAPI routers
│   ├── ingestion/         # CAS / broker imports
│   ├── compute/           # Lots, XIRR, tax, benchmarks, retirement…
│   ├── market_data/       # NAV & TRI fetchers
│   ├── db/                # Models & migrations
│   ├── ai/                # Optional Claude layer
│   ├── reports/           # HTML/PDF reports
│   └── …                  # goals, allocation, tax, assumptions
├── frontend/              # React + Vite SPA
├── tests/                 # pytest suite (golden files + property tests)
├── docs/                  # Specs, architecture, ADRs, OpenAPI
└── scripts/               # Dev lifecycle helpers
```

## Documentation

| Doc | Contents |
|-----|----------|
| [Technical spec](docs/architecture/technical-spec.md) | System design, NFRs, module map |
| [Data model](docs/architecture/data-model.md) | Tables and invariants |
| [Compute engine](docs/architecture/compute-engine.md) | FIFO, XIRR, tax, benchmarks |
| [AI layer](docs/architecture/ai-layer.md) | Prompting, anonymization, validation |
| [Development plan](docs/development-plan.md) | Phase-wise roadmap & test gates |
| [ADR-0001](docs/decisions/adr-0001-python-fastapi-sqlite-stack.md) | Stack decision |
| [PRD](docs/MF_Analyzer_PRD.pdf) | Product requirements |

## Privacy & trust

- Data lives in a local SQLite file; encryption key is managed via the system keyring (with a local fallback for dev).
- Raw CAS PDFs are treated as immutable source of truth; derived state is recomputable.
- Optional AI calls send **anonymized, aggregated metrics** — not folio numbers or PAN.
- Out of scope: trade execution, non-MF assets, multi-tenant SaaS, SEBI-regulated advice.

## License

Private / personal project. All rights reserved unless otherwise noted.
