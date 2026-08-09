# FundLens — Data Model

Status: Draft v1.0 · Part of [technical-spec.md](technical-spec.md)

## 1. Design Principles

1. **Raw layer is immutable.** Nothing in `raw_*` tables is ever updated in place, only appended
   or superseded by a new ingestion batch. This is what makes the pipeline replayable (PRD §5.8).
2. **Everything derivable is a view or a cached, invalidatable table**, keyed by a content hash
   of its inputs — never hand-maintained state that can drift from source.
3. **PAN is the tax boundary** (PRD §12.2). Every folio belongs to exactly one PAN. Cross-PAN
   aggregation exists only in read-only rollup views, never in a table that feeds tax math.
4. **Money and units are stored as fixed-point (integer paise / integer micro-units)**, not
   floats, to keep golden-file XIRR/tax parity exact.

## 2. Core Entities (raw / immutable layer)

### `raw_cas_upload`
One row per CAS file ingested. Never deleted; superseded uploads are marked, not removed.

| column | type | notes |
|---|---|---|
| id | uuid pk | |
| file_hash | text | sha256 of the decrypted PDF bytes, for dedupe of re-uploads |
| uploaded_at | timestamp | |
| statement_period_start / end | date | parsed from CAS header |
| parser_version | text | `casparser` version pinned at parse time |
| status | enum | `parsed` \| `parse_failed` \| `superseded` |

### `raw_transaction`
The atomic unit of truth. Natural key enforces the PRD's idempotent-merge rule (FR-1.2).

| column | type | notes |
|---|---|---|
| id | uuid pk | |
| upload_id | fk → raw_cas_upload | provenance |
| pan | text | masked in all LLM-facing paths |
| folio | text | |
| amc | text | |
| scheme_amfi_code | text | |
| scheme_isin | text | nullable — not all CAS rows carry ISIN |
| txn_type | enum | purchase, sip, redemption, switch_in, switch_out, dividend_payout, dividend_reinvest, segregation |
| txn_date | date | |
| units | numeric(20,4) | signed: positive for inflow-to-folio, negative for outflow |
| nav | numeric(12,4) | |
| amount | integer | paise |
| stamp_duty | integer | paise, nullable |
| stt | integer | paise, nullable |
| natural_key | text, unique | `sha256(folio, scheme_amfi_code, txn_date, txn_type, amount, units)` — the FR-1.2 dedupe key |

Re-upload merge algorithm: parse new CAS → compute `natural_key` for every row → `INSERT ... ON
CONFLICT (natural_key) DO NOTHING`. This is intentionally a set-union, not a diff/replace, so a
corrupted re-upload can never silently delete history — deletions only happen via explicit user
action on a specific transaction, logged separately.

### `nav_history`
| column | type | notes |
|---|---|---|
| scheme_amfi_code | text | |
| nav_date | date | |
| nav | numeric(12,4) | |
| source | enum | `amfi_navall` \| `mfapi` \| `manual` |
| fetched_at | timestamp | |

Primary key `(scheme_amfi_code, nav_date)`. Append-only; a later fetch for the same date
overwrites only if the prior value was flagged provisional (same-day NAV before EOD publish).

### `benchmark_history`
Same shape as `nav_history` but keyed by benchmark id (`NIFTY50_TRI`, `BSE100_TRI`,
`NIFTY_MIDCAP150_TRI`, `NIFTY_SMALLCAP250_TRI`, category debt indices). Includes a `proxy` boolean
— set true when a TRI series is unavailable and an index-fund NAV is substituted per the PRD §10
mitigation, with the substitution documented in `benchmark_history.proxy_note`.

### `scheme_metadata`
Category (SEBI classification), expense ratio (time-series, since it changes), AUM, fund manager,
AMC-declared benchmark, plan type (`direct` \| `regular`). Sourced from AMFI/AMC disclosures,
refreshed on the same cadence as NAV.

### `scheme_identity` (resolved: missing-ISIN handling)
Some CAS rows carry only an AMFI scheme code, no ISIN. Resolution is **deterministic, not
heuristic**: AMFI's own NAVAll master data publishes, for every scheme code, both the Growth-plan
ISIN and the IDCW (dividend) ISIN alongside the canonical scheme name — independently of any CAS.
The market-data fetcher ingests this into `scheme_identity` (`amfi_code → isin_growth, isin_idcw,
scheme_name, amc`) on the same refresh cadence as NAV. Ingestion backfills any `raw_transaction`
row missing ISIN by joining on `amfi_code` against this table — no fuzzy name matching, and no
guessing. AMC scheme renames are handled by keying on `amfi_code` (stable) rather than name
(not stable); `scheme_identity` keeps a `superseded_name` history so historic reports still render
the name that was current at the time.

The only residual gap is a CAS row missing *both* ISIN and a resolvable AMFI code — rare, and
would indicate a malformed or very old CAS entry. Those rows surface in the data quality
dashboard (technical-spec.md §4) for manual correction rather than being silently guessed at.

## 3. Derived Layer

### `lot`
One row per FIFO cost-basis lot, derived by replaying `raw_transaction` per (folio, scheme) in
date order. Rebuilt, not updated — a change anywhere in a folio's transaction history invalidates
and regenerates every lot for that folio.

| column | type | notes |
|---|---|---|
| folio | text | |
| scheme_amfi_code | text | |
| open_date | date | |
| units_remaining | numeric(20,4) | reduced by FIFO consumption on redemption/switch-out |
| cost_per_unit | numeric(20,4) | |
| source_txn_id | fk → raw_transaction | the purchase/SIP/reinvest/switch-in that opened this lot |
| close_date | date, nullable | set when fully consumed |

### `derived_xirr_cache`
Memoization table: key = `sha256(entity_type, entity_id, txn_set_hash, nav_asof_date)`. Avoids
recomputing XIRR for every fund on every page load; invalidated automatically when the hash
changes (new transaction or new NAV), which is what delivers the <5s incremental-update NFR.

### `risk_profile`
Persona (`conservative` \| `balanced` \| `aggressive` \| `custom`), target allocation bands
(equity/debt/gold/international; large/mid/small within equity), onboarding questionnaire
answers. **Resolved: one risk profile per PAN**, consistent with PAN being the tax and portfolio
boundary everywhere else in this model (PRD §12.2) — allocation drift, goal feasibility, and
recommendation tone can legitimately differ across a household's PANs (e.g. a parent's PAN nearer
retirement vs a working-age spouse's). Onboarding offers a "copy to all PANs" convenience action,
but each PAN's profile is independently editable afterward. The household rollup view (§12.2)
remains display-only and does not imply a shared profile.

### `goal`
Name, target amount, inflation assumption (fk → `assumption`), target date, tagged holdings
(many-to-many across PANs — corpus math is MF-only and cross-PAN per PRD §12.1/§8.1).

### `assumption`
Named, versioned config values: inflation %, expected-return-by-risk-profile, LTCG exemption
limit, STCG/LTCG rates and holding-period thresholds, exit-load schedules. Every report
(`report.assumption_version_id`) pins the assumption set it was generated with, so two reports
generated months apart remain individually reproducible and comparable (PRD §5.7).

### `verdict`
Per-fund Hold/Sell/Switch output: rubric component scores (each traceable to its metric), overall
verdict, confidence, reasoning text, "what would change this verdict," generated_at, and the
`assumption_version_id` + metric snapshot hash it was computed from — so a verdict is reproducible
even after assumptions change later.

## 4. Entity Relationship Summary

```
PAN 1──* Folio 1──* raw_transaction *──1 Scheme *──1 AMC
                 │
                 └──* Lot (derived, FIFO)

Scheme *──1 scheme_metadata
Scheme *──1 scheme_identity
Scheme *──* nav_history
Benchmark *──* benchmark_history

PAN 1──1 risk_profile
PAN *──* Goal *──* Folio (via tagged holdings)

Verdict *──1 Scheme, *──1 PAN, *──1 assumption (version)
Report *──1 assumption (version), *──1 PAN (or household rollup)
```

## 5. Status

Both items previously open here are resolved (2026-08-09): risk profile is per-PAN (§3), and
missing-ISIN rows are resolved deterministically via `scheme_identity` (§2), not guessed.
