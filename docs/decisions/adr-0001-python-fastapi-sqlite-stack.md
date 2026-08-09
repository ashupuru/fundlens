# ADR-0001: Python/FastAPI/SQLite stack, overriding the org-default TypeScript/Fastify/Postgres stack

Status: Accepted · Date: 2026-08-09

## Context

The user's global instructions (`~/.claude/CLAUDE.md`) and this project's `CLAUDE.md` specify a
default stack: TypeScript, Fastify, React, PostgreSQL, Redis, pgvector.

The FundLens PRD (`docs/MF_Analyzer_PRD.pdf`, §7 "Proposed Architecture") independently arrives at
a different stack: Python (`casparser`, pandas/duckdb, scipy for XIRR) + SQLite, FastAPI +
React, with an explicit non-goal of any cloud dependency (§2, §6 Portability NFR: "no cloud
dependency except NAV feeds and optional LLM").

Three considerations drove the conflict:

1. **CAS parsing.** CAMS/KFintech detailed Consolidated Account Statements are password-protected
   PDFs in a registrar-specific format. `casparser` is the maintained, community-trusted Python
   library for this; there is no equivalent-maturity TypeScript library. Re-implementing PDF
   decryption + registrar-format parsing from scratch (rather than reusing `casparser`) was
   evaluated and rejected as unjustified effort/risk for a single-user tool — this exact format
   is what the PRD's own risk table (§10) flags as the most likely source of breakage, and a
   from-scratch implementation would carry that risk with none of the existing library's
   community-maintenance mitigation.
2. **Privacy/portability NFR.** The product is explicitly local-first, single-user, running on a
   Mac Mini/MacBook with no server to operate. SQLite (with SQLCipher for at-rest encryption)
   fits this directly; Postgres/Redis are designed for concurrent multi-client access this
   product will never have, and would add operational surface (a database server to run and
   secure) with no corresponding benefit at this scale.
3. **Numerical/data tooling.** XIRR solving, lot-level FIFO cost basis, and rolling-return
   computation over pandas/duckdb are well-trodden in Python's numerical ecosystem
   (`scipy.optimize`, pandas). The equivalent TypeScript ecosystem is thinner for this specific
   class of financial time-series math.

## Decision

Build FundLens as a single Python codebase: FastAPI backend, SQLite (SQLCipher-encrypted)
storage, `casparser` for ingestion, pandas/duckdb + scipy for the compute engine, WeasyPrint for
report rendering. React + TypeScript is retained for the frontend SPA only — that boundary is
the right place for TypeScript regardless of backend language, and doesn't reintroduce the
conflict.

This is a **per-project exception** to the user's global stack rules, made explicitly and
recorded here rather than silently deviating. The user confirmed this choice directly when asked
(2026-08-09) rather than defaulting to the TS/Fastify/Postgres stack or forcing a
Python-sidecar hybrid.

## Consequences

- **Gained:** direct reuse of `casparser` (mitigates the PRD's top-listed technical risk),
  simpler single-language codebase for the backend + compute engine, zero-ops local deployment
  (no Postgres/Redis to install, configure, or secure on the user's Mac Mini), numerical tooling
  well-suited to the XIRR/tax/rolling-return workload.
- **Given up:** consistency with the org-wide TypeScript/Fastify/Postgres convention used
  elsewhere; pgvector is unavailable, so any future feature needing vector similarity (e.g.
  semantic search over fund commentary, embedding-based overlap detection beyond the
  holdings-based approach in FR-5.3) would need a different solution — e.g. an in-process
  vector index (`sqlite-vec`, `faiss`) rather than pgvector, or a future migration to Postgres
  if/when the product outgrows single-user local deployment (e.g. the multi-user path
  explicitly deferred in PRD §2/§11).
- **Revisit triggers:** if FundLens ever moves toward the multi-tenant/SaaS direction the PRD
  explicitly rules out for v1 (§2, §11 compliance gate), this decision should be re-opened —
  that's precisely the scenario the org's default stack (Postgres, Redis, multi-client-safe
  Fastify) is suited for, and SQLite's single-writer model would become a real constraint.

## Alternatives Considered

- **TS/Fastify primary + Python sidecar for CAS parsing only.** Rejected by the user in favor of
  the simpler single-language option; would have kept Postgres/Redis/pgvector but added an
  inter-process boundary (subprocess or local RPC) purely to reach `casparser`.
- **Pure TypeScript, re-implement CAS parsing from scratch.** Rejected — re-deriving a
  format-fragile, registrar-specific parser that already exists and is maintained in Python was
  judged not worth the effort/risk for a single-user analytical tool.
