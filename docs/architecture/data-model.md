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
| file_hash | text | sha256 of the uploaded PDF's raw bytes, for dedupe of byte-identical re-uploads |
| source_filename | text, nullable | as supplied by the user, for display only |
| uploaded_at | timestamp | |
| statement_period_start / end | date | parsed from `CASData.statement_period` |
| parser_version | text | `casparser` version pinned at parse time |
| status | enum | `parsed` \| `parse_failed` \| `superseded` |

### `raw_transaction`
The atomic unit of truth. Natural key enforces the PRD's idempotent-merge rule (FR-1.2).

**Corrected against `casparser`'s actual output** (v1.3.0, inspected directly rather than assumed):
STT, stamp duty, and TDS are **not** fields on a purchase row — `casparser` emits them as their
own transaction rows (`TransactionType.STT_TAX` / `STAMP_DUTY_TAX` / `TDS_TAX`), so the earlier
`stamp_duty`/`stt` columns below were wrong and are dropped in favor of storing every row
uniformly, typed by `txn_type`.

| column | type | notes |
|---|---|---|
| id | uuid pk | |
| upload_id | fk → raw_cas_upload | provenance |
| pan | text, nullable | from `Folio.PAN`; masked in all LLM-facing paths |
| folio | text | |
| amc | text | |
| scheme_name | text | `Scheme.scheme`, the raw name as printed on the CAS |
| isin | text, nullable | not all CAS rows carry ISIN |
| amfi_code | text, nullable | `Scheme.amfi`; backfilled via `scheme_identity` (§2) when absent |
| txn_type | enum | full `casparser.types.TransactionType` set: `PURCHASE`, `PURCHASE_SIP`, `REDEMPTION`, `DIVIDEND_PAYOUT`, `DIVIDEND_REINVEST`, `SWITCH_IN`, `SWITCH_IN_MERGER`, `SWITCH_OUT`, `SWITCH_OUT_MERGER`, `STT_TAX`, `STAMP_DUTY_TAX`, `TDS_TAX`, `SEGREGATION`, `GIFT_IN`, `GIFT_OUT`, `MISC`, `UNKNOWN`, `REVERSAL` |
| txn_date | date | |
| sequence | integer | position within this scheme's transaction list, in the stable order casparser/the CSV importer emits them (0-indexed, per folio+scheme). Added in P0.3: FIFO lot ordering needs a same-day tiebreaker, and no existing field could serve that role — `balance_after` moves in opposite directions for inflows vs outflows, so it can't order a mixed same-day sequence |
| description | text, nullable | free-text line description from the PDF, kept for audit/debugging and SIP detection (FR-2.3) |
| units | numeric(20,4), nullable | signed: positive for inflow-to-folio, negative for outflow; null for pure-cash rows like tax lines |
| nav | numeric(12,4), nullable | |
| amount_paise | integer, nullable | fixed-point; `casparser` gives `Decimal`, converted on ingest |
| balance_after | numeric(20,4), nullable | `casparser`'s running unit balance after this row — a free cross-check against our own FIFO ledger (compute-engine.md §1) |
| dividend_rate | numeric(12,4), nullable | only present on dividend rows |
| gift_folio | text, nullable | only present on `GIFT_IN`/`GIFT_OUT` rows |
| natural_key | text, unique | `sha256(folio, scheme_identifier, txn_date, txn_type, amount_paise, units, balance_after, ordinal)` — the FR-1.2 dedupe key. See below; `scheme_identifier` is `isin` if present else `amfi_code` else `scheme_name` |

**Natural key — corrected against real data.** The first six fields (through `units`) looked
sufficient on synthetic fixtures but proved incomplete against a real 1,578-transaction MFCentral
CAS: 95 genuinely distinct purchase/switch transactions collided because two same-day,
same-amount, same-unit rows are indistinguishable on those fields alone. Two more fields were
added:
- `balance_after` — casparser's running unit balance after the row. Distinct transactions
  necessarily leave the ledger at different cumulative balances; this alone resolved 100% of the
  real-data collisions among unit-bearing rows. It's null for zero-unit rows (tax lines), so it
  can't help there.
- `ordinal` — for rows tying on every field including `balance_after` (chiefly STT/stamp-duty/TDS
  tax lines, which carry neither units nor a balance), the Nth occurrence of an identical tuple
  within its scheme's transaction list, in the stable order `casparser` emits them. Deterministic
  and stable across re-uploads of overlapping data, since historical entries don't reorder
  between CAS pulls.

**Gap detection (FR-1.2):** `casparser`'s `Scheme.open` gives the opening unit balance at the
start of the statement period. A nonzero `open` is a precise, built-in signal that the CAS does
not cover that scheme's full history — units existed before the statement period that this
document has no transaction-level detail for. Surfaced per (folio, scheme) as part of the
ingestion API response, and persisted to `coverage_gap` (below) so the data quality dashboard
(P0.6) can show it after the fact, not only in the immediate upload response.

### `coverage_gap`
| column | type | notes |
|---|---|---|
| id | uuid, pk | |
| upload_id | fk → raw_cas_upload | which upload detected this gap |
| folio | text, indexed | |
| scheme_name | text | |
| opening_balance_units | numeric(20,4) | the CAS's own reported opening balance |
| message | text | human-readable explanation |
| detected_at | timestamp | |

One row per (upload, folio, scheme) gap, written at ingest time (`ingestion/service.py`), never
patched — a later, fuller-history upload doesn't retroactively delete an earlier gap record; the
dashboard shows every gap ever detected, since the underlying incomplete-history problem doesn't
resolve itself just because a different upload's statement period happens to start later. CSV
import never produces gap rows (no `Scheme.open` equivalent in the CSV format).

Re-upload merge algorithm: parse new CAS → compute `natural_key` for every row → `INSERT ... ON
CONFLICT (natural_key) DO NOTHING`. This is intentionally a set-union, not a diff/replace, so a
corrupted re-upload can never silently delete history — deletions only happen via explicit user
action on a specific transaction, logged separately.

### `nav_history`
| column | type | notes |
|---|---|---|
| amfi_code | text | matches `raw_transaction.amfi_code` naming |
| nav_date | date | |
| nav | numeric(12,4) | |
| source | enum | `amfi_navall` \| `mfapi` \| `manual` |
| fetched_at | timestamp | |

Primary key `(amfi_code, nav_date)`. Append-only, insert-if-absent — implemented this way in
P0.2: a later fetch never overwrites an existing `(amfi_code, nav_date)` row, from either source.
Verified live against both real feeds this doesn't cause gaps: AMFI NAVAll and mfapi.in agreed
exactly on the current day's NAV in testing, so the "insert only if absent" rule naturally
converges. The originally-planned "overwrite if the prior value was flagged provisional (same-day
NAV before EOD publish)" refinement is deferred — not needed until same-day pre-EOD fetches are
actually attempted, which P0.2 doesn't do (AMFI refresh runs once daily, not intraday).

### `data_freshness`
One row per `NavSource`, upserted on every fetch attempt — never a fetch history log, just current
status. Backs the "stale-data banner, never silent" NFR (technical-spec.md §7): the UI reads this
table rather than trusting the last cached NAV as implicitly current.

| column | type | notes |
|---|---|---|
| source | enum, pk | `amfi_navall` \| `mfapi` \| `manual` |
| last_attempt_at | timestamp | updated on every attempt, success or failure |
| last_success_at | timestamp, nullable | only updated on success — the banner's real signal |
| status | enum | `ok` \| `failed` |
| error_message | text, nullable | set on failure, surfaced by the data quality dashboard (P0.6) |
| rows_updated | integer, nullable | rows inserted on the most recent attempt |

### `benchmark_history`
**Shipped in P1.1.** Same shape as `nav_history` but keyed by `benchmark_id` (PK, with
`nav_date`): `NIFTY50_TRI`, `BSE100_TRI`, `NIFTY_MIDCAP150_TRI`, `NIFTY_SMALLCAP250_TRI`. Category
debt indices (also PRD FR-1.4-named) are **not built** — no debt `BenchmarkId` exists.

`proxy` is **always `true` today**, not merely a fallback flag: PRD §10 named true TRI data as
hard to source freely, and P1.1 live-verified this — NSE/niftyindices.com return 403/503 to
unauthenticated requests. Every row's `value` is therefore a real index fund or ETF's own NAV,
chosen per benchmark for longest available history via mfapi.in (`benchmark/proxy_config.py` has
the exact scheme mapping and rationale), backfilled the same way P0.2 backfills any held scheme's
history (one `mfapi.in` call per benchmark, not the AMFI NAVAll bulk file). `proxy_note` names the
actual fund standing in, e.g. "UTI Nifty 50 Index Fund (Direct, Growth) NAV — no free TRI source
available" — never presented as true TRI without this note attached.

### `scheme_metadata`
**Shipped in P0.5, narrower than originally planned**: `amfi_code` (PK), `category_raw` (the SEBI
category string exactly as AMFI publishes it, e.g.
`"Open Ended Schemes(Equity Scheme - Large Cap Fund)"`), `tax_asset_class` (`equity` \| `debt` \|
`needs_review`, derived from `category_raw` — see compute-engine.md §3.1), `fetched_at`. Sourced
from the AMFI NAVAll feed's category section headers (parsed alongside the NAV rows in the same
fetch, no extra network call — `market_data/amfi_client.py::parse_navall_categories`), refreshed
on the same cadence as NAV, scoped to currently-held schemes only. Upserted (current state, not a
time series).

Expense ratio, AUM, fund manager, AMC-declared benchmark, and plan type (`direct` \| `regular`)
are **not sourced yet** — AMFI's NAVAll feed doesn't carry them; these need a separate AMC-
disclosure or RTA data source and are deferred to a later phase.

### `scheme_identity` (missing-ISIN handling) — **not yet built**
Some CAS rows carry only an AMFI scheme code, no ISIN. The original plan was a deterministic
backfill table: AMFI's own NAVAll master data publishes, for every scheme code, both the
Growth-plan ISIN and the IDCW (dividend) ISIN alongside the canonical scheme name — independently
of any CAS — so a `scheme_identity` table (`amfi_code → isin_growth, isin_idcw, scheme_name, amc`)
could backfill any `raw_transaction` row missing ISIN by joining on `amfi_code`, with a
`superseded_name` history for AMC renames.

**None of that exists yet.** `fundlens/scheme_identity.py` today is only a `scheme_key(isin,
amfi_code, scheme_name)` helper — prefer ISIN, then AMFI code, then raw name — used to group
transactions into "one scheme" consistently across dedup and lot accounting; it does no backfill.
Currently, the P0.6 data quality dashboard's "unmatched schemes" view is the actual, honest
mitigation in place today: any currently-held scheme still missing ISIN or AMFI code is flagged
for the user to see, not silently guessed at or auto-resolved. Real-data check: 0/13 of the
user's open holdings are currently unmatched, so this hasn't blocked anything in practice, but the
backfill mechanism itself remains a future enhancement, not shipped functionality.

## 3. Derived Layer

### `lot`
One row per FIFO cost-basis lot, derived by replaying `raw_transaction` per (folio, scheme) in
`(txn_date, sequence)` order — see `raw_transaction.sequence` below; date alone doesn't
disambiguate multiple same-day transactions, which real data has. Rebuilt, not updated — a
change anywhere in a folio's transaction history invalidates and regenerates every lot for that
folio (compute/service.py deletes and regenerates a folio's lots wholesale).

| column | type | notes |
|---|---|---|
| folio | text | |
| scheme_key | text | matches `scheme_identity`'s ISIN → AMFI → name fallback |
| open_date | date | |
| units_remaining | numeric(20,4) | reduced by FIFO consumption on redemption/switch-out |
| cost_per_unit | numeric(20,4) | prefers `nav`, falls back to `amount/units`, else 0 (flagged) |
| source_txn_id | fk → raw_transaction | the purchase/SIP/reinvest/switch-in that opened this lot |
| source_txn_type | enum | the `raw_transaction.txn_type` that opened this lot, kept for audit/SIP-detection |
| close_date | date, nullable | set when fully consumed |
| zero_cost_flag | boolean | true when neither `nav` nor `amount` was available to cost the lot — real-data-verified to occur only for `SEGREGATION` rows (side-pocket creation carries no priced amount). Indian tax rules require carrying over the original lot's cost basis pro-rata for these; that refinement is the tax engine's job (P1.2), not this table's |

**FIFO allocator design (implemented in compute/lots.py), corrected against a real 1,578-row
CAS:** the allocator branches purely on the *sign* of `units` — verified live that casparser
already gives correctly signed units (positive=inflow, negative=outflow) across every observed
transaction type without exception, including `SEGREGATION` (always +) and `REVERSAL` (always −,
and carries a real `amount`/`nav` despite the name, so it needs no special-casing beyond the sign
check). No `txn_type`-based sign flip is performed or needed.

### `realized_gain`
One row per FIFO consumption slice — a lot split across several outflows, or one outflow
draining several lots, each produce their own row here. Feeds the tax engine (P1.2) directly.

| column | type | notes |
|---|---|---|
| folio | text | |
| scheme_key | text | |
| source_txn_id | fk → raw_transaction | the lot's opening transaction |
| open_date | date | |
| sale_txn_id | fk → raw_transaction | the outflow transaction that consumed this slice |
| sale_date | date | |
| units_consumed | numeric(20,4) | |
| cost_per_unit | numeric(20,4) | copied from the lot at consumption time |
| sale_price_per_unit | numeric(20,4) | the outflow's own price-per-unit (same nav/amount fallback as lot costing) |
| realized_gain_paise | integer | `(sale_price_per_unit − cost_per_unit) × units_consumed`, fixed-point |
| holding_period_days | integer | `sale_date − open_date`, for the tax engine's STCG/LTCG threshold check |

### `derived_xirr_cache`
Memoization table: key = `sha256(entity_type, entity_id, txn_set_hash, nav_asof_date)`. Avoids
recomputing XIRR for every fund on every page load; invalidated automatically when the hash
changes (new transaction or new NAV), which is what delivers the <5s incremental-update NFR.

### `risk_profile` — **shipped P1.4, single app-wide singleton, not per-PAN**
Real shape (`db/models.py`): fixed `id=1` row, `persona` (`conservative` \| `balanced` \|
`aggressive` \| `custom`), `target_equity_pct`/`target_debt_pct` (persona presets, or explicit
values for `custom`), and onboarding questionnaire answers (`horizon_years`,
`drawdown_tolerance`, `income_stability`, `has_dependents`). No `gold`/`international` bands or
large/mid/small sub-targets — the PRD's persona table (§3) only ever defines an equity:debt ratio,
so only that axis has a real target; equity's cap-size mix is reported (compute-engine.md §7) but
never drift-scored against a value nothing in the PRD actually specifies.

**Originally planned as one-per-PAN** (PRD §12.2's PAN-boundary consistency) but shipped as a
single singleton instead: the app has no multi-PAN concept anywhere else yet (same call made for
the P1.1 benchmark engine's portfolio-level comparison, compute-engine.md §4) — a partial
per-PAN `risk_profile` would be inconsistent with every other PAN-touching feature in the app
still being PAN-agnostic. Revisit together whenever multi-PAN support actually gets built.

### `goal`
Name, target amount, inflation assumption (fk → `assumption`), target date, tagged holdings
(many-to-many across PANs — corpus math is MF-only and cross-PAN per PRD §12.1/§8.1).

### `assumption` / `assumption_version` — **infrastructure shipped P1.2, narrower than planned**
Real shape (`db/models.py`): `assumption_version` (`id`, `label`, `created_at`, `is_current` —
exactly one row `True` at a time) and `assumption` (`version_id, key` composite PK, `value` text,
typed at the read site). `assumptions/service.py::create_version()` clones the current version's
values, applies overrides, and flips `is_current` — never mutates an existing version, so two
versions stay independently reproducible.

Only the keys something actually reads are seeded (P1.2 added the tax engine's; P1.3 added the
rubric's): `equity_ltcg_threshold_days`, `debt_ltcg_threshold_days`, `debt_specified_fund_cutoff`,
`equity_grandfather_cutoff`, `equity_ltcg_exemption_paise_per_fy`, `equity_stcg_rate_pct`,
`equity_ltcg_rate_pct`, `rubric_weight_alpha`, `rubric_weight_consistency`,
`rubric_weight_category_fit`, `rubric_weight_direct_regular`, `rubric_sell_threshold_pct`.
`debt_marginal_tax_rate_pct` is a **valid key with no seeded default** — debt gains are taxed at
the investor's income-tax slab, which this app has no way to know, so it must be set explicitly
via `POST /assumptions` before any debt-fund tax-friction statement is computable (compute-engine.md
§3.5), never defaulted to a guessed slab. Inflation % and expected-return-by-risk-profile — PRD
§5.7-named — belong to goal planning (P2) and aren't added until something reads them; an untyped,
unread assumption key is dead config. The `report`/`goal` tables below that would reference
`assumption_version_id` are themselves not yet built — today only the compute layer reads
`get_current_tax_assumptions()`/`get_current_rubric_tax_rates()`/`get_current_rubric_weights()`
directly, there's no persisted report row pinning a version yet.

### `verdict` — **compute-on-demand, not a persisted table**
`compute/verdict.py::Verdict` is a plain dataclass returned by `GET /verdicts`, matching every
other derived-on-read view in this app (P1.1's benchmark comparison, P0.5's capital gains report)
rather than the originally-planned persisted table with its own `generated_at` and
`assumption_version_id` FK. Real fields: `verdict_type` (`hold`/`sell` — no `switch`, see
compute-engine.md §5), `overall_score`, `confidence_pct`, `components` (each with its own
`score`/`weight`/`reason`, independently inspectable), `reasoning`, `counterfactual`, `tax_impact`
(non-null only for `sell`), `blocked_reason`, `omitted_components`. Historical verdict tracking
("what did we recommend last month") would need the persisted-table design — deferred until
something needs to compare a verdict against its own past self, consistent with this app's general
bias toward deriving state fresh rather than caching it prematurely.

## 4. Entity Relationship Summary

```
PAN 1──* Folio 1──* raw_transaction *──1 Scheme *──1 AMC
                 │
                 └──* Lot (derived, FIFO)

Scheme *──1 scheme_metadata
Scheme *──1 scheme_identity
Scheme *──* nav_history
Benchmark *──* benchmark_history
NavSource 1──1 data_freshness

risk_profile (app-wide singleton — not PAN-scoped, see its own section)
PAN *──* Goal *──* Folio (via tagged holdings)

Verdict *──1 Scheme, *──1 PAN, *──1 assumption (version)
Report *──1 assumption (version), *──1 PAN (or household rollup)
```

## 5. Status

Both items previously open here are resolved (2026-08-09): risk profile is per-PAN (§3), and
missing-ISIN rows are resolved deterministically via `scheme_identity` (§2), not guessed.
