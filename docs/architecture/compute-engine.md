# FundLens — Compute Engine

Status: Draft v1.0 · Part of [technical-spec.md](technical-spec.md) · Data model: [data-model.md](data-model.md)

This is the deterministic core of FundLens: lot accounting, return metrics, tax, benchmarking,
and the recommendation rubric. Everything here is a pure function over `(raw_transaction,
nav_history, benchmark_history, assumption)` — no side effects, no LLM calls. This purity is what
makes the golden-file test suite meaningful and what the AI layer's determinism boundary depends
on (PRD FR-5.2).

## 1. Lot Accounting (FIFO)

Implemented in `src/fundlens/compute/lots.py` (pure, no I/O) + `compute/service.py`
(DB-aware grouping/persistence). Validated against a real 1,578-transaction, 26-folio,
57-scheme MFCentral CAS spanning 1980–2026 — several design decisions below were corrected
against what that real data actually showed, not assumed.

**Input:** `raw_transaction` rows for one `(folio, scheme_key)`, sorted by `(txn_date, sequence)`
— `sequence` (data-model.md §2) is the stable secondary key that breaks same-day ties, since
`txn_date` alone doesn't disambiguate multiple same-day transactions, which real data has.

**Algorithm:** classic FIFO queue of open lots, branching purely on the *sign* of `units` —
verified live that casparser already gives correctly signed units (positive=inflow,
negative=outflow) across every observed transaction type without exception, so no
`txn_type`-based sign flip is needed or performed:
- **units > 0** (`PURCHASE`, `PURCHASE_SIP`, `DIVIDEND_REINVEST`, `SWITCH_IN`, `SEGREGATION`,
  and presumably `SWITCH_IN_MERGER`/`GIFT_IN` by the same pattern) → push a new lot. Cost per
  unit prefers `nav` (the row's own per-unit price), falls back to `amount/units` if `nav` is
  absent, else zero with `zero_cost_flag` set.
- **units < 0** (`REDEMPTION`, `SWITCH_OUT`, `REVERSAL`, presumably `SWITCH_OUT_MERGER`/`GIFT_OUT`)
  → consume from the oldest open lot(s) first (FIFO); split a lot if the outflow doesn't consume
  it fully. Each consumed slice's realized gain = `(sale_price_per_unit − lot.cost_per_unit) ×
  units_consumed`, using the same nav→amount→zero price fallback. Consuming more than the open
  lots hold raises `InsufficientLotsError` — `compute/lots.py` itself is unchanged and still
  raises this (its pure contract, well-tested); `compute/service.py::rebuild_lots_for_folio`
  is what changed (see **gap-fill lots** below).

**Gap-fill lots** (`compute/service.py`, added alongside the dashboard-charts/valuation-history
batch — a real bug fix, not a new feature: Dezerv-only imports into an empty database were
silently missing funds entirely). CAS has a native opening-balance signal (`Scheme.open`,
data-model.md §2's `coverage_gap`); Dezerv's "BANK BOOK" export has no such concept at all — it's
a plain buy/sell ledger for whatever date window was requested, so an account that predates the
window's start routinely has a scheme whose *first recorded* transaction is a Sell. Previously,
`InsufficientLotsError` on ANY transaction aborted `build_lots` for the WHOLE scheme, discarding
every lot for it — including lots from later, perfectly clean transactions that came after the
deficit point, since `build_lots` processes a scheme's full history as one call. The fund would
vanish from holdings entirely, not just misprice the gap. Now, `rebuild_lots_for_folio` catches
`InsufficientLotsError`, synthesizes a lot sized to exactly the reported shortfall (algebraically
exact, never guessed) dated at the scheme's earliest known transaction, `zero_cost_flag=True` —
the same "flag an unknown cost rather than fabricate one" instinct already used for
`SEGREGATION`'s unpriced rows — and retries, bounded by the scheme's own transaction count (each
retry can resolve at most one new deficit point, so this is a hard, provable bound, not a
heuristic cap). A gap-fill lot has `source_txn_id=None` — there's no real transaction to point
to, so `Lot.source_txn_id`/`RealizedGain.source_txn_id` were relaxed from `NOT NULL` to nullable
(migration `192e08f1fbf1`) rather than inventing a fake `raw_transaction` row, which would have
blurred that table's "real ingested data only" invariant. Reported via `RebuildSummary.gap_filled`
(surfaced in both the CAS/CSV and Dezerv import UIs, alongside the pre-existing `errors` count) —
informational, not hidden, since the affected lot's cost basis (and, unavoidably, its holding
period — no date signal exists to do better) is an assumption, not a fact.
- **units is `None` or 0** (`STT_TAX`, `STAMP_DUTY_TAX`, `TDS_TAX`, `DIVIDEND_PAYOUT`, `MISC`,
  `UNKNOWN`) → skipped entirely; these never touch the lot ledger.

**`SEGREGATION` — corrected against real data.** The original design here assumed segregation
(side-pocketing) reduces the original scheme's units pro-rata while a new segregated scheme
inherits a pro-rata cost basis. Real data shows otherwise: `SEGREGATION` rows are **always
positive** (the new side-pocket scheme's inflow) with **no corresponding transaction in the
original scheme at all** — units held in the original scheme don't change, only its valuation
does (via NAV), consistent with how Indian MF side-pocketing actually works. `SEGREGATION` rows
also carry **no `amount` or `nav`** (0/5 in the validation set) — a structural event, not a
priced one — so the allocator opens a zero-cost, `zero_cost_flag`-marked lot for it. Indian tax
rules require carrying over the *original* lot's cost basis pro-rata to the segregated units;
implementing that carryover is the tax engine's job (P1.2), not this allocator's — flagged, not
guessed.

**`REVERSAL` needs no special-casing.** Despite the name suggesting something unusual, real data
shows it carries a normal negative `units` value plus a real `amount`/`nav` (4/4 in the
validation set) — it's priced and consumes lots exactly like a redemption.

**Output:** `lot` and `realized_gain` rows (data-model.md §3), rebuilt wholesale per folio on
every call (delete-then-regenerate, never patched in place) — `compute/service.py`'s
`rebuild_lots_for_folio` / `rebuild_all_lots`.

## 2. XIRR

Implemented in `compute/xirr.py` (pure solver) + `compute/metrics.py` (cash-flow construction,
DB-aware). Validated live against the same real 56-position, 1980–2026 portfolio used to validate
§1, including a real market-data refresh (P0.2) to backfill NAV for every held scheme.

**Cash flow construction** (FR-3.1): for a given entity (fund / folio / portfolio — category/goal/
platform levels are P1+):
- `PURCHASE`, `PURCHASE_SIP`, `SWITCH_IN`, `SWITCH_IN_MERGER` → negative flow (money out of the
  investor's pocket) at `txn_date`.
- `REDEMPTION`, `SWITCH_OUT`, `SWITCH_OUT_MERGER`, `DIVIDEND_PAYOUT`, `REVERSAL` → positive flow
  (money back to the investor) at `txn_date`.
- Excluded — no net external cash movement: `DIVIDEND_REINVEST` (dividend income reinvested
  internally, not fresh cash), `SEGREGATION` (carries no amount at all, §1), tax lines, `MISC`,
  `UNKNOWN`. `GIFT_IN`/`GIFT_OUT` are also excluded, conservatively — no real data exercised
  this path to confirm whether they carry a genuine amount.
- Terminal flow: `units_remaining * latest_nav` as a positive flow at the NAV's own as-of date,
  for still-held positions only (fully-exited positions get no terminal flow — see below).

**`amount_paise` sign — corrected against real data.** Originally assumed to be an unsigned
magnitude, used as-is for inflow-classified transactions. Real data proved `amount_paise` is
*signed to match `units`* (negative for `REDEMPTION`/`SWITCH_OUT`/`REVERSAL`) — a convention
`compute/lots.py` never needed to check since it prices lots from `nav`, not `amount`. Left
unguarded, this made every redemption/switch-out a *negative* cash flow instead of positive: on
the validation portfolio, only 13/56 positions (the still-held ones, which never touch an
inflow-classified row) produced a valid XIRR — every fully-exited fund silently read as
"insufficient_flow_variation" despite its units correctly reaching zero in the FIFO ledger. Fixed
by normalizing to `abs(amount_paise)` before applying the sign the classification above dictates.
Re-verified: 54/56 positions now produce a valid XIRR; the remaining 2 have legitimate reasons
(below), not bugs.

**Portfolio-wide XIRR** is every held position's classified cash flows concatenated, with no
special inter-fund netting: a `SWITCH_OUT` from fund A and its matching `SWITCH_IN` to fund B are
equal and opposite on the same date, so they algebraically cancel in the XIRR objective function
for any rate once aggregated. Verified with a closed-form cross-check (a switch pair plus a
purchase and terminal value reduces to exactly the same rate as if the switch had never happened).

**Solver** (`compute/xirr.py`): `scipy.optimize.brentq` bracketed on `[-0.9999, 10.0]` annual
rate, falling back to `scipy.optimize.newton` seeded at 10% (not the bracket's raw midpoint —
~4.5 is a poor real-world seed) if `brentq` fails to bracket a root. Every result is checked with
`math.isfinite()` before being trusted: numpy silently returns NaN (with a warning, not an
exception) for a negative base raised to a fractional exponent, which Newton's iteration can
wander into for pathological flow patterns — left unguarded, a NaN could slip through as if it
were a valid rate. `_objective` raises explicitly when `1 + rate ≤ 0` so both solvers treat that
region as out-of-domain rather than silently computing NaN.

**Edge cases (PRD FR-3.1):**
- **Fully exited fund:** no terminal flow (0 units remaining); this is "realized XIRR" and is
  labeled as such (`is_fully_exited`), not conflated with a still-held fund's XIRR.
- **< 1 year of history:** XIRR is still computed (`less_than_one_year` flag set); the "annualized
  and noisy" framing is the UI/API consumer's job, not the solver's.
- **Degenerate flows** (all-positive, all-negative, a single flow, or all-same-date): return
  `null` with a reason code (`insufficient_flow_variation` or `same_date_flows`) rather than
  raising, so a portfolio-wide computation doesn't fail because one position is degenerate. The
  real portfolio's one genuinely all-positive case is a `SEGREGATION`-originated position with no
  purchase leg at all — there's no investment cash flow to compute a return against, so `null` is
  the *correct* answer, not a gap.

### 2.1 CAGR, absolute return, realized/unrealized P&L (FR-3.2)

Also `compute/metrics.py`, reusing the same cash-flow classification and terminal-value logic as
XIRR rather than a separate computation:
- **Invested** = sum of outflow-classified transaction amounts. **Received** = sum of
  inflow-classified transaction amounts (gross proceeds already redeemed — distinct from
  `realized_gain`, which is a *gain* figure from the FIFO lot ledger, not gross proceeds).
- **Absolute return %** = `(received + current_value − invested) / invested × 100`.
- **CAGR** = a simplified since-inception point-to-point figure —
  `((received + current_value) / invested) ^ (365 / days_since_first_investment) − 1` — distinct
  from true money-weighted XIRR and from NAV-based rolling CAGR (fund performance independent of
  the investor's own cash-flow timing, FR-3.3/P1). Labeled accordingly, not conflated with either.
- **Unrealized P&L** = `current_value − cost_basis_of_open_lots` (the open-lot cost basis comes
  directly from `compute/holdings.py`, §data-model.md `lot` table).
- **Realized P&L** = summed straight from the `realized_gain` table (§1), not recomputed.

## 3. Tax Engine

**Status: §3.1–3.4 shipped (§3.1–3.3 in P0.5, §3.2's grandfathering and §3.4 in P1.2). §3.5 is
P1, not yet built** — noted inline below so this doc doesn't drift back into describing
aspirational behavior as current, which happened in an earlier draft of this section.

### 3.1 Classification (`fundlens/tax/asset_class.py`)
`classify_category(category_raw) -> TaxAssetClass` (`EQUITY` / `DEBT` / `NEEDS_REVIEW`) runs
**substring matching against the actual live AMFI NAVAll category-header taxonomy**, not a
config-driven equity-allocation percentage — AMFI's feed exposes only the SEBI category string
(e.g. `"Open Ended Schemes(Equity Scheme - Large Cap Fund)"`), never a numeric equity-allocation
%, so the originally-planned "≥65%/<65% hybrid test" (below) isn't derivable from this data source
and was dropped in favor of an honest `NEEDS_REVIEW` bucket for categories that are genuinely
tax-ambiguous without allocation data (Balanced Hybrid, Multi Asset, Dynamic Asset Allocation,
Equity Savings, domestic Fund-of-Funds, Gold/Silver ETF, Solution Oriented, Life Cycle, and bare
legacy "Growth" labels). Classification result is persisted per `amfi_code` in `scheme_metadata`,
refreshed on every AMFI feed pull (`market_data/service.py::_upsert_scheme_metadata`), scoped to
currently-held schemes only (not the full ~16k-scheme universe).

Real-data check (13 open holdings across the user's actual CAS): 8 equity, 1 debt, 4
needs_review — 0 missing metadata.

### 3.2 STCG/LTCG rules (`fundlens/tax/classification.py`)
`classify_gain(asset_class, purchase_date, holding_period_days, assumptions) -> GainClassification`,
per lot at redemption, thresholds read from the assumption registry (§3.4a) rather than code
constants:
- **Equity**: LTCG if holding period > `equity_ltcg_threshold_days` (365 by default), else STCG.
  This determines the *classification* only — the *gain amount* an equity LTCG line reports uses
  a grandfathering-adjusted cost basis when the lot predates the cutoff (§3.2a).
- **Debt, purchased on/after `debt_specified_fund_cutoff`** (2023-04-01 by default —
  "specified mutual fund" under the Finance Act 2023 redefinition): always STCG-equivalent — the
  LTCG concept doesn't exist for these regardless of holding period.
- **Debt, purchased before the cutoff**: old regime — LTCG if holding period >
  `debt_ltcg_threshold_days` (1095 by default), else STCG (grandfathered; this is the case the
  PRD explicitly calls out).
- **`NEEDS_REVIEW` asset class, or purchase predates scheme_metadata coverage**: `GainType.UNCLASSIFIED`
  — never guessed.

Actual tax rates and slab logic are out of scope for this classification step; it labels
STCG/LTCG/UNCLASSIFIED only.

### 3.2a Equity LTCG grandfathering (shipped P1.2 — Section 112A 4th/5th proviso)
For equity LTCG lines whose lot was purchased before `equity_grandfather_cutoff` (2018-01-31 by
default), the cost of acquisition used to compute the *gain amount* (not the STCG/LTCG
classification itself) is the higher of (a) actual cost and (b) the lower of (i) the scheme's FMV
as of the cutoff date and (ii) the sale price — reading FMV straight from `nav_history`, which
P0.2 already backfills a scheme's *full* history into, not just from its first purchase, so no new
data source was needed. If a lot predates the cutoff but `nav_history` has no row on/before it (a
backfill gap), grandfathering is **not silently skipped in the tax-favorable direction** — the
line is flagged `grandfathering_note="grandfathering_data_unavailable"` and falls back to actual
cost, the conservative (higher-taxable-gain) direction when the true FMV can't be determined.
Real-data check: 44 and 29 lines (FY2023-24 and FY2024-25 respectively) were actually
grandfathering-eligible in the user's real portfolio, and FMV was available for all of them — 0
`grandfathering_data_unavailable` flags.

### 3.3 Capital gains report (FR-10.2 — `compute/capital_gains.py`, `reports/capital_gains_csv.py`)
`get_capital_gains_report(session, fy_label)` joins `RealizedGain` (filtered to `sale_date` within
the FY) with `RawTransaction` (scheme identity, PAN) and `SchemeMetadata` (tax classification),
scheme- and lot-wise. `gain_paise` is **derived** as `sale_value_paise − cost_of_acquisition_paise`
in the report layer rather than trusted verbatim from the FIFO ledger's independently-rounded
`realized_gain_paise`, guaranteeing `sale − cost == gain` holds exactly for every line (verified
against 3 real financial years of the user's actual redemption history). Exposed via
`GET /reports/capital-gains` (JSON) and `GET /reports/capital-gains.csv` (Schedule-112A-adjacent
CSV export, now including PAN, grandfathering, and exemption columns), both scoped by `?fy=`.

### 3.4 Exemption tracking (shipped P1.2)
`ltcg_exemption_used_this_fy` is computed across all equity-LTCG lines realized in the FY, **per
PAN** (PRD §12.2 — tax math never crosses PAN boundaries; the sale transaction's own `pan` field
scopes each line), ordered by `sale_date`, consuming the `equity_ltcg_exemption_paise_per_fy`
headroom (₹1.25L by default) first-realized-first-consumed. A line with a gain but no PAN on its
transaction can't be scoped to any PAN's headroom — flagged
`exemption_note="no_pan_on_transaction"` and treated as fully taxable (the conservative direction)
rather than guessed into some PAN's bucket; losses (`gain_paise <= 0`) never consume headroom.
Real-data check: per-PAN headroom was never exceeded across all 3 FYs checked, and
`taxable_gain_paise == gain_paise - ltcg_exemption_consumed_paise` held for every line. Feeds the
capital-gains report (§3.3); the harvesting action-plan generator (FR-6.3) is still unbuilt.

### 3.4a Assumption registry (PRD §5.7, shipped P1.2 — `fundlens/assumptions/`)
Tax thresholds and the exemption limit are versioned, editable config (`AssumptionVersion` +
`Assumption` tables, data-model.md), not code constants. Exactly one version is `is_current` at a
time; `assumptions/service.py::create_version()` clones the current version's values, applies
overrides, and flips the current flag — it never mutates an existing version, so a report generated
under one version stays reproducible after the user edits an assumption later (the
`report.assumption_version_id` field this was built to support, data-model.md `report`). Exposed
via `GET /assumptions/current`, `GET /assumptions/versions`, `POST /assumptions`. Only the values
the tax engine actually reads are seeded (`equity_ltcg_threshold_days`,
`debt_ltcg_threshold_days`, `debt_specified_fund_cutoff`, `equity_grandfather_cutoff`,
`equity_ltcg_exemption_paise_per_fy`) — inflation % and expected-return assumptions the PRD also
names belong to goal planning (P2+) and aren't added until something reads them.

### 3.5 SELL friction statement (FR-6.2, shipped P1.3 — `tax/friction.py`)
For a currently-held position, `compute_tax_friction(session, folio, scheme_key)` computes the
real rupee tax cost of selling **today**: per open lot (not per position — a position built from
multiple purchases can have both STCG- and LTCG-eligible lots simultaneously), it replays the same
classification (§3.2) and grandfathering (§3.2a, `tax/grandfathering.py` — factored out as a
shared module once this became its second caller) logic the realized-gains report uses, applied to
a hypothetical sale instead of a past one. Exemption headroom is netted against what the same
PAN has *already* consumed this FY (queries the current FY's own capital-gains report), so the
friction statement never double-counts headroom another already-realized sale already used.

**Two real data gaps, both handled by flagging rather than guessing**:
- **Debt tax rate**: debt gains are taxed at the investor's marginal income-tax slab, which
  nothing in this app captures (unlike equity's fixed, publicly-known STCG/LTCG rates, seeded into
  the assumption registry as `equity_stcg_rate_pct`/`equity_ltcg_rate_pct`). Requires
  `debt_marginal_tax_rate_pct` to be set via `POST /assumptions` first — deliberately **not** in
  the seeded defaults (unlike every other assumption key), since defaulting a tax-rate guess to
  any number would present it as the user's real rate. Until set, `computable=False,
  reason="debt_marginal_tax_rate_not_configured"`.
- **Exit load**: no free data source exists for exit-load schedules (same category of gap as
  expense ratio, §6). `exit_load_modeled` is always `False`; the breakeven figure is tax-only,
  never silently presented as tax-plus-exit-load. Real-data check: `exit_load_modeled` was `False`
  for all 13 of the user's real holdings, as designed.

Old-regime debt LTCG (pre-2023 purchases, >36mo) is taxed identically to debt STCG here
(`debt_marginal_tax_rate_pct`) rather than modeling indexation, which would need historical Cost
Inflation Index data this app doesn't have — a documented simplification, not a hidden one.

This structured output is what the recommendation engine (§5) consumes — schema-enforced
(`tax_impact` non-null on every `SELL` verdict, §5) rather than merely UI-enforced. An AI
narrative layer (PRD FR-5.1/5.2) that would also consume this is not yet built.

## 4. Benchmark Simulation Engine

**Shipped in P1.1** (`fundlens/benchmark/`, `compute/benchmark.py`). **"What if these cash flows
had gone into the benchmark instead?"** (FR-4.1): on each purchase, the real rupee amount buys
benchmark units at that date's benchmark value; on each redemption that reduces the fund's own
units, the *same fraction* of the fund's currently-held units being liquidated is applied to the
parallel benchmark-units position, and that fraction's benchmark value — not the fund's real
redemption proceeds — becomes the benchmark-side cash flow. A still-held position gets a terminal
flow from its remaining benchmark units at the latest available price. Both series then go through
the same XIRR solver (`compute/xirr.py`), giving a cash-flow-timing-controlled "fund XIRR vs
benchmark XIRR, alpha = the difference" — the entire point of the Zerodha-Coin-style comparison.

**A real-data-caught bug, corrected before shipping**: an earlier version reused the fund's real
redemption rupee amount as the benchmark-side flow too (reasoning: "same cash flows, same
amounts"). Live-verified against the user's real 56-position portfolio, this made `benchmark_xirr`
trivially equal `fund_xirr` for every fully-exited position (no terminal flow to differ on) —
alpha landed at exactly 0.0 for two-thirds of exited positions and was never once negative, a
statistically impossible pattern for independent real return series. The fund's real exit proceeds
reflect the *fund's* performance, not what the benchmark would have been worth; reusing them
silently erases the comparison for most of a typical portfolio's history. Fixed by tracking
fund-units-held in parallel and liquidating the matching *fraction* of benchmark units instead —
re-verified against the same real portfolio: alpha sign breakdown went from
`{positive: 8, negative: 0, zero: 16}` (impossible) to `{positive: 18, negative: 6, zero: 0}`
(plausible). `DIVIDEND_PAYOUT` (real-data-verified: never carries a `units` value — a cash-only
distribution) has no benchmark-side equivalent under the Growth-option proxies used (see below)
and is excluded from the benchmark timeline entirely, while remaining in the fund's own XIRR
unchanged.

**No free true-TRI source exists** (PRD §10 risk, live-verified P1.1: NSE/niftyindices.com return
403/503 to unauthenticated requests — the same bot-hostile class of dependency the PRD explicitly
avoids for Groww/MFCentral scraping). Every `BenchmarkId` is therefore backed by a real index
fund/ETF's own NAV (`benchmark/proxy_config.py`), chosen empirically for longest available history
via mfapi.in, always flagged `proxy=true` with a `proxy_note` naming the actual fund — this is the
PRD's own documented fallback, used as the primary (only working) data source, not a last resort.

- **Portfolio-level** (FR-4.2): same simulation against a single reference index, default Nifty 50
  TRI (PRD §12.3), user-overridable via `?benchmark_id=` on `GET /benchmarks/comparison/portfolio`.
  Per-PAN scoping and a household roll-up are not built — the app has no multi-PAN concept yet.
- **Category-correct** (FR-4.3): default benchmark selection (`benchmark/category_mapping.py`)
  keyed off `scheme_metadata.category_raw` — Large Cap/other equity → Nifty 50 TRI, Mid Cap →
  Midcap 150 TRI, Small Cap → Smallcap 250 TRI, Large & Mid Cap → Nifty 50 TRI (spans both, no
  single-cap index fits). Debt and `NEEDS_REVIEW` categories return no benchmark (`reason:
  no_category_benchmark`) rather than comparing against an equity index, which would misattribute
  category-driven return differences as fund skill. No per-fund override yet.
- **Index-fund counterfactual** (FR-4.4): already what this module computes — every `BenchmarkId`
  *is* a real index fund/ETF's NAV, not a theoretical TRI value, so no separate counterfactual
  computation exists on top of the above. **Category-appropriate debt indices** (also FR-1.4-named)
  remain unbuilt — no debt `BenchmarkId` exists; debt/hybrid schemes get no benchmark comparison.

**Equity blended benchmark** (`build_blended_series`/
`compute_portfolio_equity_blended_benchmark_comparison` in `compute/benchmark.py`): a single
reference index is less defensible than one matching the portfolio's actual cap-size mix, so this
composites Nifty50/Midcap150/Smallcap250 TRI weighted by the equity slice's actual large/mid/small
value split (`compute/allocation.py::compute_portfolio_valuation`'s cap breakdown, reused as-is —
no separate valuation logic). **Combines each component's own daily percentage return, weighted by
allocation, then compounds into a synthetic series from a base value of 1000** — never averages
raw index levels across components, since they're on unrelated scales. Coverage starts on the
*latest* first-return date among all non-zero-weighted components (an intersection, not a union):
Nifty50's proxy history goes back to 2013, but Midcap150/Smallcap250 proxies only to 2019-09-11
(`benchmark/proxy_config.py`), so a large+mid blend's series can't validly start before the later
of the two — never silently reweighted or backward-extrapolated to paper over the gap. Deliberately
**equity-only**: no debt/gold `BenchmarkId` exists (same gap noted above), so a "portfolio-wide"
blend would have to either ignore that weight or fabricate a benchmark for it — both wrong. Scoped
to equity positions' own cash flows (`tax_asset_class == EQUITY`), reported as `xirr`/
`benchmark_xirr`/`alpha_pct` alongside the large/mid/small weights actually used, so the blend is
legible rather than a black box. Mirrors the existing single-benchmark comparison's asymmetric
cash-flow inclusion (all fund-side positions included; benchmark-side restricted to whatever the
blended series' coverage start date actually reaches) rather than inventing a stricter symmetric
rule.

**Manual TRI CSV import** (`benchmark/tri_csv_import.py`, `benchmark/service.py::
upsert_manual_tri_rows`, `POST /benchmarks/import-tri`): every `benchmark_history` row is currently
a proxy (no free true-TRI source exists, per above) — this is the escape hatch for upgrading
specific dates to real data the user has downloaded and reformatted by hand. Defines FundLens's own
simple two-column format (`date`, `value`) rather than attempting to parse any vendor's native
export shape — no real sample of NSE Indices' actual CSV was ever obtained (live-investigated: its
historical-data page is an ASP.NET WebForms app with no stable discoverable public API), so
guessing that format sight-unseen was rejected in favor of a format this app controls. Unlike the
proxy auto-refresh (`_insert_new_benchmark_rows`, insert-if-absent — never overwrites), this
function **overwrites**: a row for a date already present is replaced (`value`, `proxy=False`,
`proxy_note=None`, fresh `fetched_at`), since real data should supersede the proxy default for the
dates it covers. No separate upload-provenance table: `benchmark_history`'s own composite primary
key (`benchmark_id`, `nav_date`) already is the dedupe key. A manually-imported real row is
permanently protected from later being clobbered back to proxy, since the auto-refresh path only
ever inserts absent dates.

## 5. Recommendation Rubric Engine

**Shipped in P1.3** (`compute/verdict.py`), **narrowed from the original 5-component design to
4** — agreed before building, not discovered partway through. Real data only supports 4 of the 5
originally-specified components:

| Component | Original design | What shipped | Weight (default, tunable) |
|---|---|---|---|
| Rolling alpha | mean(rolling-window fund XIRR − benchmark XIRR) via §4 | scheme-level 3yr/5yr **true rolling** NAV CAGR vs benchmark CAGR (`compute/rolling_returns.py`, upgraded P3/FR-3.3) — see below for why | 30 |
| Consistency | % of rolling windows where fund beat benchmark | genuine win-rate across every monthly-sampled 3yr/5yr window (P3 upgrade) | 20 |
| Category fit | actual-vs-target allocation drift contribution (§7) | unchanged — needs P1.4's drift, which is why P1.4 was built first | 15 |
| Cost | expense ratio percentile vs category peers; direct-vs-regular flag | **narrowed to the binary direct-vs-regular flag alone** (§6) — inherits cost's original weight | 20 |
| Overlap/redundancy | pairwise holding overlap vs rest of portfolio (FR-5.3) | **omitted entirely** — needs monthly AMC portfolio disclosures, a data source this app doesn't integrate | — |

**Why NAV-based CAGR, not XIRR, for rolling alpha**: this is scheme-level performance (how has
the fund itself done over the trailing window), not investor-position performance (which would
conflate the fund's skill with the specific investor's entry-timing luck) — the same number a
fund fact sheet reports as "3Y/5Y returns."

**Rolling methodology, shipped P3 (FR-3.3)**: `compute/rolling_returns.py::compute_rolling_returns`
now samples end dates at ~monthly cadence (`dateutil.relativedelta(months=1)`, not
`timedelta(days=30)`, to avoid calendar drift over decades) from the earliest date at least
`years` after the scheme's first NAV to the latest available NAV date, computing fund/benchmark
CAGR at every sample — a genuine rolling series, not the single point-to-point snapshot per
window-length this module shipped with in P1.3. `consistency_pct` is now a real win-rate across
however many windows were sampled, not a 2-point proxy. Each scheme's full NAV/benchmark series is
bulk-fetched once and bisected per sample (same template as §9's `_nav_series_by_scheme` /
compute/benchmark.py's synthetic value source), so even 20 years of daily NAV data (~240 samples
per window length) is cheap.

Also ships **1yr** as a third window length (`window_years=(1, 3, 5)` by default) — but
**1yr is informational only, on the fund detail page, never fed into this rubric**
(`compute/verdict.py` calls `compute_rolling_returns(..., window_years=(3, 5))` explicitly). The
rubric's weights were designed around exactly two rolling components (3yr+5yr sharing the 30%
"rolling_alpha" slot); folding in a noisier, short-horizon window would mean redesigning that
weight structure for a signal poorly suited to a long-horizon hold/sell judgment. Judgment call,
confirmed with the user given real verdict-shifting consequences (see next paragraph) — documented
here rather than silently decided.

**Real-portfolio impact, verified at ship time**: this methodology upgrade — better numbers behind
the *same* two components, same weights, same renormalization — was checked by diffing
`GET /verdicts` on the real portfolio before and after: 19 of 33 held funds had `overall_score`
shift (methodology precision changing, exactly as expected), but **zero verdicts changed HOLD ↔
SELL**, and the lowest post-change score (46.2) stayed well clear of the 35% sell threshold. Not a
guarantee this will always be true after future assumption/weight edits — just the honest,
checked answer for this specific ship.

Weights (and the SELL threshold) live in the `assumption` table (`assumptions/service.py::RubricWeights`),
not code — user-tunable and versioned per PRD §5.7 — and are **renormalized per-verdict** across
whichever components are actually computable for that specific fund, so a fund held <3yr (both
rolling components unavailable) gets a confidence-capped verdict from the remaining components,
never a score silently computed as if the missing ones were zero. `confidence_pct` is exactly
"how many of the 4 components were computable," which is what makes the <3yr case self-explanatory
without special-casing it.

**Verdict space is `HOLD`/`SELL` only, not `HOLD`/`SELL`/`SWITCH`**: a `SWITCH` verdict needs a
specific alternative fund to switch to, which requires the Alternatives Engine (FR-9.1, §8) — not
built. The reasoning string and "what would change this verdict" counterfactual are both
template-generated from the component scores (never an LLM call — this stays in the deterministic
layer so a verdict is reproducible without network access).

Any `SELL` verdict is required (FR-6.2) to attach the §3.5 tax-friction statement — enforced by
construction, not just validated after the fact: `tax_impact` is `None` for every `HOLD` and a
*computable* `TaxFrictionStatement` for every `SELL`. If the rubric score alone would indicate
SELL but the tax-friction statement reports `computable=False` (e.g. a debt fund with no
configured marginal tax rate, §3.5), the verdict is **downgraded to `HOLD`** with `blocked_reason`
set — recommending an action whose real-world cost can't be quantified would violate the same
flag-don't-guess discipline used everywhere else in this app, just applied to the action itself
rather than a data field. Real-data check: 0/13 of the user's real verdicts needed this downgrade
(none scored low enough to reach the SELL threshold), and the schema invariant (`tax_impact`
non-null iff non-`HOLD`) held for all 13.

## 6. Direct-vs-Regular Audit

**Shipped in P1.3, narrower than originally planned** (`compute/plan_audit.py`,
`GET /audit/direct-vs-regular`). Flagged by parsing the scheme name — AMFI scheme names reliably
embed "Direct Plan"/"Regular Plan" (real-data-verified against the live AMFI feed), not from a
`scheme_metadata.plan_type` column, which doesn't exist (scheme_metadata's real shape is
documented in data-model.md — no plan-type field was ever added, since the original design's own
premise, a stored plan_type, was never populated).

**No ₹/year commission-drag figure**: the original design estimated commission drag from the
expense-ratio delta between the held regular plan and its direct-plan twin. No free expense-ratio
data source exists — live-checked during P1.1/P1.3: mfapi.in's scheme metadata has no TER field,
and AMFI's TER disclosures are scattered per-AMC filings, not a unified free feed. This ships as a
binary flag only (`direct` / `regular` / `unknown`), which is what feeds the rubric's cost
component (§5) at weight 20. Real-data check: all 13 of the user's real open holdings resolved to
`direct` — none flagged `regular` or `unknown` in this portfolio.

## 7. Allocation & Drift

**Shipped in P1.4** (`fundlens/allocation/`, `compute/allocation.py`). Actual allocation is
**current market value**, not cost basis — priced from each currently-open holding's latest
available NAV (compute/metrics.py `latest_nav`), grouped by `SchemeMetadata.tax_asset_class`:
EQUITY / DEBT, with everything else (`NEEDS_REVIEW`-classified schemes, or schemes entirely
missing `scheme_metadata`) bucketed as "other" and reported separately — the PRD's persona table
(§3) only defines an equity:debt target ratio, so "other" has no target to drift against and isn't
silently folded into either side. A holding with no NAV data is excluded from the percentage
totals and counted separately (`value_unavailable_paise`), never substituted with cost basis or
zero, either of which would misstate the drift numbers.

`equity_drift_pct` / `debt_drift_pct` = actual% − target%, compared against the active
`risk_profile`'s target bands (persona presets below, or explicit values for `custom`). Equity
holdings get a further cap-size breakdown (large/mid/small/large-and-mid/other, via the same
`tax/asset_class.py::equity_cap_size` the P1.1 benchmark engine uses for category-default
selection) as **informational detail only** — no persona in the PRD defines large/mid/small
sub-targets, so this breakdown is reported, never drift-scored against an invented target, unlike
the originally-planned design here which implied a large/mid/small target existed.

Persona presets (PRD §3, `allocation/service.py::PERSONA_TARGETS`): Conservative 30:70,
Balanced 60:40, Aggressive 80:20, Custom user-defined. The risk profile itself is a **single,
app-wide singleton** today, not per-PAN as data-model.md originally planned — same reasoning as
the P1.1 benchmark engine's portfolio-level comparison (§4): the app has no multi-PAN concept
anywhere else yet, so a partial per-PAN implementation here would be inconsistent with the rest of
the app. Real-data check: actual/target percentages summed to exactly 100% and the equity cap
breakdown reconciled exactly to the equity total on the user's real 13-holding portfolio.

FR-7.2's "prefer directing new SIP money over selling" (a rebalancing suggestion that solves for
the *next N months' SIP allocation* that closes the drift gap without generating a sell) is **not
yet built**.

## 8. Alternatives Engine (FR-9.1)

For any `SELL`/`SWITCH` verdict, candidates are pulled from `scheme_metadata` filtered to the
same category, ranked by the same rolling-consistency and cost components used in §5 (reused, not
reimplemented), with an explicit tie-breaking bias toward index funds when
`scheme_metadata.category` is flagged index-replicable. "Simplification mode" (FR-9.2, P2) is a
downstream reduction over this same ranking: greedily pick a minimal fund set that covers the
portfolio's category exposure within the risk profile's bands, and diff its blended expense ratio
against the current portfolio's AUM-weighted expense ratio for the ₹/year savings figure.

## 9. Historical Portfolio Valuation

**Shipped alongside the dashboard-charts-round-2 batch** (`compute/valuation_history.py`).
Every other compute module answers "what's true *now*" — this is the first that answers "what
was true *then*": a **weekly** time series of portfolio value and cumulative invested capital
from the portfolio's first-ever transaction to today, powering the Portfolio Value vs Invested
line chart and (derived client-side from the same series, no second endpoint) the Drawdown chart.

Scoped to every position ever held (`compute/metrics.py::get_all_positions`), not just
currently-open ones — a valid historical point still needs to reflect what was held back then,
even for positions since fully exited. Per position, a two-pointer merge walks that position's
`RawTransaction`s (sorted `(txn_date, sequence)`, same tiebreaker as FIFO lots and the §4
benchmark simulation) against the sorted report dates once — O(transactions + report dates), not
their product — accumulating a running `units_held` and pricing it via a per-scheme NAV series
pre-fetched once and bisected per report date (the same in-memory bisect template §4's synthetic
blended-benchmark series already established, generalized from "one series" to "one series per
held scheme").

**`invested_paise` is gross, monotonically non-decreasing** — cumulative purchase-family cash
flow only (`metrics.py::CASH_OUTFLOW_TYPES`), never netted against redemptions. This is a
deliberate consistency choice, not an oversight: `PortfolioMetrics.invested_paise` (§ the
Dashboard's "Details" card) already means gross invested everywhere else in this app; a
net-of-redemptions series here would silently mean two different things by the same field name
in the same dashboard. A report date where a position has units held but no NAV reaches back that
far excludes just that position's value at that one point and increments
`positions_missing_nav` — mirrors `allocation.py::compute_portfolio_valuation`'s
`unavailable_paise` precedent (flag and exclude, never crash, never substitute).

**AMC allocation** (`GET /portfolio/breakdown?level=amc`) reuses `compute_category_breakdown`'s
exact grouping/XIRR-blending shape (§ extracted into a shared `_compute_grouped_breakdown` helper
that both now call) rather than a new bespoke aggregation — the only difference is the label
function (`h.amc` directly vs. a Groww-category lookup), so AMC concentration gets XIRR-per-AMC
for free. Deliberately lives in `compute/metrics.py` next to `compute_category_breakdown`, not in
`compute/holdings.py` (where the pre-existing, still-untouched, cost-basis-only
`get_holdings_by_amc` lives) — `holdings.py` is lower-level and already imported *by*
`metrics.py`, so pulling `metrics.py`'s NAV-pricing logic the other way would create a circular
import; `metrics.py` already owns both `latest_nav` and the grouped-breakdown pattern this needed.

**Per-fund category labels** (`HoldingMetricsResponse.category_broad`/`category_specific`,
`GET /portfolio/metrics`) reuse the same Groww-then-AMFI resolution as `compute_category_breakdown`
via two shared helpers (`_category_lookup_maps`, `_resolve_category_label`) and a new
`fund_category_labels(session, holdings)` that batch-resolves both levels per `(folio,
scheme_key)` in one pass — not a second, independent lookup. Added so the Dashboard's Asset/
Category/AMC breakdown charts can drill down to `/funds?category=<label>` /
`/funds?amc=<label>` and have the Funds page filter on the exact same label the chart showed,
rather than two code paths that could silently drift. Scoped to currently-open holdings only,
same limitation as `compute_category_breakdown` (§9 above) — a fully-exited position has no
`open_holding` to read an `amfi_code` from, so it reports `"Uncategorized"` rather than guessing.

## 10. Goal Planning (PRD FR-8.1/8.2, shipped P2.4 — `compute/goal.py`, `goals/`)

`Goal` is persisted CRUD (name, target amount, target date — like `RiskProfile`, current state
not a time series). The **projection** built on top is compute-on-demand and never persisted,
same philosophy as §7's allocation drift.

**Tagging**: there is no `Pan` table anywhere in this schema (`pan` is a free-text column on
`raw_transaction`/holdings, per §4's reasoning), so "a goal's corpus spans PANs" (PRD §12.1) is
implemented by tagging individual `(folio, scheme_key)` holdings — `HoldingView`'s own natural
key — via a `goal_holding` link table, not anything keyed to a PAN entity that doesn't exist. A
holding can be tagged to more than one goal; no attempt is made to prevent the same rupee being
counted toward two different goals' corpora, since each goal's math is independent and a real
holding can genuinely serve more than one purpose.

**Expected return** is blended from the *existing* `RiskProfile` singleton's
`target_equity_pct`/`target_debt_pct` against two new assumption-registry keys,
`expected_equity_return_pct` and `expected_debt_return_pct`:
`blended_return = equity_pct/100 * equity_return + debt_pct/100 * debt_return`. This is a
judgment call (the PRD's `data-model.md` sketch named "expected-return-by-risk-profile" without
specifying the exact shape) — reusing the app's one existing allocation-target concept instead of
inventing a parallel per-persona return table, and it handles `Persona.CUSTOM` for free (no named
preset, but always has explicit equity/debt targets). Projection requires a `RiskProfile` to
exist — 404s otherwise, identical precedent to `GET /allocation/drift`.

**Projected corpus** (FR-8.1) = pure lump-sum growth of currently-tagged holdings' present market
value at the blended return, to the target date — "where today's tagged money is headed if
nothing more is added." Unpriceable tagged holdings (no NAV on file) are excluded and flagged
separately (`current_corpus_unavailable_paise`), same "flag, don't fabricate" pattern as
`compute_portfolio_valuation`'s `unavailable_paise`.

**Required monthly SIP** (FR-8.2) = the payment needed, on top of that, to close the gap between
projected corpus and the inflation-adjusted target (`target_amount_paise` is entered in today's
rupees, inflated to the target date via a new `inflation_pct` assumption key before comparing).
Solved with the standard future-value-of-an-ordinary-annuity formula, monthly compounding
(`i = r/1200`, `n = years*12`): `P = gap / (((1+i)^n - 1) / i)`, or `gap/n` when `i == 0`. No
"current monthly contribution" field exists on `Goal` — keeps the two outputs exactly as
PRD-named, with no extra state for the user to keep in sync.

**±2% sensitivity table**: required SIP recomputed at `blended_return - 2`, `blended_return`,
`blended_return + 2` — three rows, per the PRD's literal wording. Year-fraction uses
`(target_date - today).days / 365.0`, the same convention already used by `compute/xirr.py`,
`compute/rolling_returns.py`, and `compute/metrics.py`'s short-horizon check — not a new one.

Money stays in `Decimal` throughout this module's growth math (not `float`), matching
`money.py`'s convention; only converted to integer paise at the boundary via the module's own
`_round_paise` (ROUND_HALF_UP), not `money.py::to_paise_strict` (which expects a rupee amount,
not an already-paise-denominated `Decimal`).

## 11. Yearly Report (PRD FR-10.1, shipped P2.5 — `compute/yearly_report.py`)

FY-aligned (Apr-Mar) assembly of opening/closing value, XIRR (this year + since inception),
benchmark alpha, best/worst funds, allocation drift, estimated fees, and realized gains. FY
boundaries reuse `compute/capital_gains.py::financial_year_bounds` (already public, already
exactly this shape) — not reimplemented. `report_end = min(fy_end, today)`, so a report for the
FY currently in progress reports through today rather than into the future.

**The synthetic-boundary-flow technique**: almost every existing engine
(`compute_portfolio_metrics`, `compute_portfolio_benchmark_comparison`, `compute_allocation_drift`)
is hardcoded to "as of today," not an arbitrary past date. Rather than modifying those, this module
prices the portfolio at exactly `[fy_start, report_end]` via a new
`compute/valuation_history.py::compute_portfolio_value_at_dates` (the historical-valuation
module's own per-position NAV-bisection engine, generalized from an internally-generated weekly
cadence to an arbitrary explicit date list — `compute_portfolio_value_history` is now a thin
wrapper over it), then treats the opening value as a synthetic outflow at the period start and the
closing value as a synthetic inflow at the period end, alongside the position's *real*
transactions in between (`build_position_cash_flows(session, filtered_txns, open_holding=None)` —
passing `None` suppresses that function's own today-priced terminal flow, since it isn't wanted
here). The combined flow list goes straight into the existing `compute_xirr` solver — the same
engine every other XIRR figure in this app uses. Since-inception XIRR *as of report_end* reuses
the identical technique with no `fy_start` floor on the transaction filter and only the closing
synthetic flow. Best/worst funds (top/bottom 3, fewer if fewer than 6 positions were active) apply
the *same* helper per `(folio, scheme_key)` instead of pooled, rather than a separate simpler
"return %" heuristic.

**Benchmark comparison is deliberately "since inception, as of report_end," not a true FY-only
sub-period simulation** (judgment call): `compute/benchmark.py::simulate_benchmark_cash_flows`
(promoted from private to public for this second call site) must replay a position's *full*
transaction history to correctly track its partial-redemption fraction-sold state — floor-filtering
to `fy_start` would silently miscompute that fraction for any position that already existed before
the FY. Reused unmodified with the transaction list truncated only at `report_end`, giving "alpha
since I started investing, as of this FY's end" — well-defined and still useful, instead of a
fragile new simulation surface bolted onto a function three other features already depend on.

**Allocation drift** reuses `compute_allocation_drift` exactly as shipped for §7 — current (today),
not reconstructed as of `report_end`. A backward-looking report's most actionable drift figure is
"where do I stand right now"; a historical one would also need `scheme_metadata` classifications
this app doesn't track over time. Omitted entirely (not fabricated) if no risk profile is set, same
precedent as `GET /allocation/drift`'s 404.

**Estimated fees**: `sum(current_value_paise * expense_ratio_pct / 100)` over currently-open
holdings with a known `GrowwFundMetadata.expense_ratio`, holdings missing it excluded and counted
separately — same "flag, don't fabricate" shape as `ai/snapshot.py`'s fee-drag calc, deliberately
not shared code across the `ai/` and `reports/` packages (small enough to duplicate for two
different consumers).

**Realized gains** reuses `get_capital_gains_report(session, fy_label)` wholesale, zero new code —
the one section that's genuinely pure assembly, no synthetic flows or new date-bounding needed.

**Rendering**: Jinja2 (inline `Template` string, not a separate template file — avoids any wheel
packaging-inclusion risk for a single-template, local-only app) → HTML → WeasyPrint → PDF, split
into `render_yearly_report_html` (pure string templating, testable with zero native dependencies)
and `render_yearly_report_pdf` (the only thing that needs Pango/cairo, imported lazily inside the
function so importing the module itself never requires WeasyPrint's native libs to be loadable).
WeasyPrint needs Pango/cairo installed via Homebrew (`brew install pango`) and
`DYLD_FALLBACK_LIBRARY_PATH` pointed at Homebrew's lib dir on macOS — the latter is silently
stripped from any process launched through `nohup`, which is why `scripts/start.sh` backgrounds
the backend via plain `&` + `disown` instead (see that script's own comment).

## 12. Risk-Adjusted Metrics (PRD FR-3.4, shipped P3 — `compute/risk_metrics.py`)

Sharpe ratio, Sortino ratio, and max drawdown for a held scheme's own NAV, for trailing 1/3/5yr
windows (a single window per length, ending today — like a fund fact sheet's "3Y Sharpe," not the
rolling multi-sample series §"Rolling Returns" (below) computes). Purely informational on the fund
detail page; never fed into the verdict rubric (§5).

**"vs category peers" reinterpreted as "vs category benchmark"** (judgment call, confirmed with
the user): this app only ingests NAV history for held schemes
(`market_data/service.py::refresh_all_held_schemes`), not for other funds in the same category, so
literal peer-fund comparison isn't possible — the same underlying data gap as the blocked P2.2
fund overlap. Every other "vs category" comparison already shipped here substitutes the category
benchmark index for literal peers (Rolling Returns' consistency check, the verdict rubric's rolling
alpha), so this follows that established precedent, using `BenchmarkHistory` data the app already
has.

**Methodology**: Sharpe/Sortino computed from ~monthly-sampled returns within the window (same
`relativedelta(months=1)` cadence Rolling Returns established) —
`Sharpe = (mean(excess_return) * 12) / (stdev(excess_return) * sqrt(12))`,
`excess_return = monthly_return - risk_free_rate_pct/100/12`; Sortino replaces the denominator
with annualized downside deviation (`sqrt(mean(min(0, excess_return)^2)) * sqrt(12)`), returning
`None` rather than a fabricated/infinite value when there are no negative excess-return periods to
compute a downside deviation from. Max drawdown is a single-pass peak-to-trough calculation over
the window's *daily* series, not monthly-sampled — drawdown is path-dependent, so monthly sampling
would understate real intra-month declines, and an O(n) pass is cheap regardless of resolution.
Computed identically for the fund and the category benchmark so the frontend can show them side by
side.

**New assumption key**: `risk_free_rate_pct` (default `7`, roughly a 10yr G-Sec/FD proxy,
`assumptions/service.py`) for Sharpe's excess-return numerator.

**Bug found and fixed while shipping this**: `assumptions/service.py::get_current_values` did a
raw dict lookup with no fallback, so any `AssumptionKey` added *after* a database's "Default"
version was first seeded (true for every real, pre-existing local DB, including this app's own)
raised `KeyError` the moment something tried to read it — a latent bug since P2.4's goal-planning
keys, only surfaced now because this phase was the first to actually hit it against the real local
DB during manual verification. Fixed by having `get_current_values` merge `DEFAULT_ASSUMPTIONS`
underneath whatever's actually stored for the current version (stored values always win; nothing
persisted or mutated) — deliberately **not** applied in `get_values(session, version_id)` for an
arbitrary version, since a past version legitimately not having a key that didn't exist yet is
correct, and backfilling there would misrepresent what that version actually pinned, breaking the
reproducibility guarantee versions exist for.

## 13. Glide-Path Suggestions (PRD FR-7.3, shipped P3 — `compute/glide_path.py`)

Advisory suggestion to gradually shift target allocation from equity-heavy toward debt-heavy as
the investor nears their goal horizon — standard glide-path practice. Extends `GET
/allocation/drift` (`api/routers/allocation.py`), not a new endpoint, following the same
"extend the existing aggregating endpoint" precedent as `GET /funds/{amfi_code}` (twice) and
`GET /reports/yearly.json`. Never mutates `RiskProfile` automatically — advisory only, like every
other suggestion in this app (verdicts, alternatives); the user acts on it via the existing
`POST /risk-profile` if they choose to.

**"Years remaining" is derived, not stored**: `RiskProfile` (`db/models.py:369-391`) has no
birthdate or fixed target date — only `horizon_years` (set once at onboarding) and `updated_at`.
`years_remaining = horizon_years - (years since updated_at)`. Judgment call, confirmed with the
user: uses only fields that already exist, no new required onboarding input. Disclosed limitation:
this only counts down correctly if the user doesn't touch other `RiskProfile` fields without also
updating `horizon_years` — there's no fixed target date to anchor against instead (`Goal` entities
from P2.4 are per-goal, not the portfolio-wide horizon this feature is about).

**Formula** (documented judgment call, no canonical PRD number available — same "documented, not
asserted as canonical" treatment as `compute/allocation.py::compute_category_fit_score`'s
scaling): `years_remaining >= 15` → suggest no change (glide-down hasn't started yet);
`years_remaining <= 0` → suggest a 20% equity floor; in between, linear interpolation between the
current target and the floor. A resulting `<2pp` drift is suppressed as "on track," not surfaced
as an actionable suggestion, to avoid nagging over noise. Explicitly never suggests *more* equity
than the current target — guards an edge case where an already-below-floor custom profile would
otherwise have the interpolation pull it back up toward the floor as the horizon approaches,
backwards for a glide path.

**Still-missing gap, not solved here**: there is no risk-profile *settings* UI anywhere in this
app — `POST /risk-profile` remains API-only, set once at onboarding (P1.4) with no dedicated
frontend form since. This feature surfaces the suggestion on the existing Dashboard allocation-
drift card; acting on it (or setting a profile in the first place) still requires calling the API
directly. A real, pre-existing gap, not introduced or silently worked around by this phase.

## 14. Monte Carlo Goal-Probability Simulation (PRD FR-8.3, shipped P3 — `compute/monte_carlo.py`)

A probability lens on top of Goal Planning's (P2.4, §10) deterministic projection, which assumes
returns exactly equal the blended expected rate every year — this simulates 5,000 random
monthly-return paths instead and reports the fraction that actually reach the (inflation-adjusted)
target. Two scenarios, directly extending Goal Planning's two headline numbers rather than
duplicating just one: **no further contribution** (a probability lens on `projected_corpus_paise`)
and **with the required SIP** (`required_monthly_sip_paise`, already computed deterministically —
shows how that calculation's "exactly the expected return every year" assumption holds up under
real volatility). Both scenarios reuse the *same already-computed* `blended_expected_return_pct`/
`current_corpus_paise`/`inflated_target_paise`/`required_monthly_sip_paise` from
`compute_goal_projection` rather than re-deriving them — one source of truth for "what's the
blended return," matching the "reuse, don't duplicate" precedent from every prior P2/P3 phase.

**Volatility blending, new assumption keys**: `equity_volatility_pct` (default 18) and
`debt_volatility_pct` (default 3), blended the *same simple weighted-average way the expected
return already is* (`compute/goal.py:96-99`) — not a covariance-based portfolio-variance formula,
which would need an equity-debt correlation assumption this app doesn't have. Documented
simplification, not asserted as canonical, same treatment as every other blending/scoring judgment
call in this codebase.

**Money is float, not Decimal, in this module** — a deliberate departure from `money.py`'s
paise-as-Decimal convention, which this codebase reserves for exact accounting of real transaction
amounts. A Monte Carlo simulation is inherently statistical (matches how XIRR, rolling-return CAGR,
and Sharpe/Sortino are already float, not Decimal, throughout this codebase); Decimal precision
here would be a false sense of accuracy.

**Geometric monthly-equivalent rate, not `annual/12`**: the simulation steps monthly (numpy,
vectorized — `numpy` added as an explicit dependency, already present transitively via `scipy`,
used directly here for the first time), so it needs a monthly mean return. Using `annual_rate/12`
directly (a common simplification) introduces a small systematic upward bias vs. the stated annual
rate once compounded over 12 months. Using `(1+annual_rate)^(1/12) - 1` instead means 12 months of
compounding reproduces *exactly* the stated annual rate — which is also what makes the module's
zero-volatility test gate a real correctness proof rather than an approximate one (see below).

**Test-gate approach**: Monte Carlo results are inherently statistical, not exactly
hand-computable to one precise value the way prior phases' formulas were — the honest equivalent
here is a **zero-volatility collapse test**: with both volatility assumptions at 0, every simulated
path is deterministic, so the simulated final corpus must land on *exactly* the same value an
independently-written monthly-compounding calculation produces (for a clean, integer-years
fixture, this also matches `compute/goal.py`'s own annual-compounding formula exactly). This is a
real proof the compounding math is correct, not a "looks statistically plausible" check. A
zero-volatility fixture also collapses all three output percentiles to a single value, and success
probability to a clean 100%/0% depending on which side of the target the deterministic figure
lands on. Nonzero-volatility tests are directional-only (higher volatility widens the
percentile-10-to-90 spread; an easy/already-funded goal has high success probability, a wildly
ambitious one low) — appropriate coverage for a statistical method, not a precision claim.

**Separate endpoint, not folded into `GET /goals/{id}/projection`** — a deliberate deviation from
the "extend the aggregating endpoint" precedent every prior P2/P3 phase followed (funds detail
×2, allocation drift, yearly report): those were all cheap enough to compute unconditionally on
every read of an already-existing endpoint. 5,000 vectorized simulations is a meaningfully
different cost tier, and bundling it in would tax every plain projection fetch. Matches the
precedent of `POST /ai/analyze` being its own explicitly-triggered endpoint for the same reason —
`GET /goals/{goal_id}/monte-carlo`, button-triggered on the frontend, not auto-fetched on page
load.

## 15. Zerodha Tradebook Importer (shipped Phase 4b — `ingestion/zerodha_*.py`)

Third broker/platform import path alongside CAS/CSV and Dezerv, built against a real Console
"Tradebook" export filtered to the MF segment — the only format doubt this session ever had to
resolve from live data rather than documentation, since neither Zerodha's docs nor web search gave
a verified column schema.

**ISIN-exact matching, not fuzzy** — the real structural difference from Dezerv
(`ingestion/dezerv_matching.py`'s Jaccard-scored name matching). This format carries ISIN directly
per row, so `zerodha_matching.py::resolve_isin` is a plain lookup against AMFI's own
`isin_growth`/`isin_div_reinvestment` fields, not a similarity score — real-data-verified this
matters: the same fund's `symbol` text is *not* stable over time in this export (e.g. "MIRAE ASSET
FLEXI CAP FUND - DIRECT PLAN" early in the file, "MIRAE ASSET FLEXI CAP FUND DIRECT PLAN - GROWTH"
later, same ISIN both times), so grouping/matching by ISIN rather than symbol text is required, not
just simpler.

**No NAV back-computation needed** — unlike Dezerv (a rupee amount only, units back-computed from
NAV-on-date via `tax/grandfathering.py::fmv_per_unit_on_or_before`), this format gives `quantity`
and `price` directly per row. `amount_paise = money.py::to_paise_strict(quantity * price)`, and the
resulting `TransactionRecord.nav` is simply that row's own `price` — the transaction's real
per-unit price, same convention the CAS and CSV-escape-hatch paths already use.

**Constant synthetic folio `"ZERODHA"`** — documented judgment call, not a guarantee. This export
format has no account/folio column at all (unlike Dezerv's `Custodian Account`); a Zerodha
tradebook download is inherently scoped to one account per file (Zerodha doesn't support
multi-account exports), so every row from one file genuinely belongs to the same account. A user
with more than one Zerodha account would have both land under the same folio — accepted as the
simplest correct design given the format's real constraints, rather than fragile filename-based
client-ID parsing (users rename downloaded files).

**Cross-source duplicate detection and AMC-name resolution extracted to
`ingestion/broker_import_shared.py`** — this is now the second real caller of logic that started
Dezerv-specific (`existing_cross_source_transaction`, `resolve_amc_names`), matching this session's
established "extract on second caller" precedent (e.g. §9's category-label sharing). Dezerv's
tests were re-run unmodified after the extraction to confirm it's behavior-preserving.

**`trade_id` as a real ordering key** — unlike Dezerv (no per-row identifier at all, so same-day
identical-looking transactions need an arbitrary insertion-order tiebreak), this format's
`trade_id` is genuinely unique per row (real-data-verified: no repeats across 172 real rows).
Records are processed in `(isin, txn_date, trade_id)` order, so sequence/ordinal assignment is
deterministic and re-derivable from the source data itself, not just stable-by-accident.

## 16. Retirement Projection (shipped — `compute/retirement.py`)

A year-by-year simulation of the **whole portfolio** through two phases — accumulation
(`current_age` → `retirement_age`) then decumulation (`retirement_age` → `life_expectancy`) —
answering "is the current investment rate enough to fund retirement, and until what age does the
corpus last?"

**Why this is a separate engine from §10 Goal Planning**, rather than another Goal type. Goal
planning evaluates a *closed-form* future value at a single point in time. Retirement is
inherently *sequential*: each year's closing balance is the next year's opening balance, and a
withdrawal permanently reduces the base every later year compounds on. No closed form expresses
that, so the two are genuinely different computations. They do share their return assumption via
the extracted `compute/goal.py::blended_expected_return_pct` (target-allocation-weighted), so the
two engines can never disagree about what rate a given profile implies — the same
"extract on second caller" move §9 and §15 already made.

**Inputs live on the existing `RiskProfile` singleton** (new nullable columns, migration
`7d1eea46c88e`) rather than a new table — it's already the app's "about you" record. All
nullable, and the projection returns `reason="retirement_profile_incomplete"` with every
numeric field `None` rather than projecting from guessed inputs, matching §13 glide-path's
`"no_horizon_set"` precedent. Two further refusal cases are reported the same way rather than
producing nonsense: current age at/past retirement age, and life expectancy not after retirement
age. **`POST /retirement/profile` deliberately does not touch persona/target allocation, and
`POST /risk-profile` does not touch the retirement fields** — the two forms write to the same row,
so folding them into one setter would let saving either one silently wipe the other's inputs.

**Starting corpus is the real portfolio**, read from `compute/metrics.py::compute_portfolio_metrics`
— not a manually re-entered number that would immediately go stale against actual holdings.

**Ordering convention within each year**: returns accrue on the *opening* balance only, then the
year's investment is added (or withdrawal taken). Contributions are therefore effectively
end-of-year (ordinary annuity), matching `compute/goal.py::_required_monthly_sip_paise`'s own
convention — the conservative reading, since crediting a full year's return to money invested
during that year would overstate the corpus.

**Today's money vs. the nominal retirement-year figure** are reported as two distinct numbers
(`monthly_withdrawal_today_paise` / `first_year_monthly_withdrawal_paise`): the user enters what
they'd need per month *today*, and the engine inflates it over `years_to_retirement`. Conflating
the two is the single most misleading thing a retirement projection can do, so both are surfaced.

**Required corpus** is the present value at the post-retirement return of the whole
inflation-escalating withdrawal stream — the growing-annuity PV,
`W/(r−g) · (1 − ((1+g)/(1+r))^n)`. That closed form divides by zero when `r == g`, which is a
perfectly plausible input (post-retirement return set equal to inflation), so that case is
branched explicitly to its correct finite value `n·W/(1+r)` rather than crashing. Both branches
are cross-checked in tests against an independently-written discounting loop.

**Depletion** clamps the corpus at zero, records `depletion_age`, and stops projecting — a
negative corpus isn't a meaningful figure to display, and every later year would just repeat
"still zero". `surplus_or_shortfall` is the difference of the *rounded* projected and required
figures (not the rounding of their difference) so the three numbers shown together reconcile
exactly on screen, the same requirement the Funds page's headline stats already hold to.

**Deliberately deferred**, each its own follow-up phase: conservative/base/optimistic scenarios,
reverse planning ("what SIP do I need?"), interactive what-if, Monte Carlo sequence-of-returns
risk on the retirement corpus, and the healthcare / emergency-reserve / tax / terminal-corpus
layers. The projection is pre-tax, and the UI says so alongside every assumption it used.

## 17. Lump-Sum Life Events in the Retirement Projection (shipped — `compute/retirement.py`)

Education, marriage, a house — one-off future costs that hit the same corpus retirement draws
from. Rather than inventing a second "event" entity, these are the existing `Goal` rows (§10)
read a second way: a goal is a dated, today's-rupees amount, which is exactly what an event is.

**Why counting goals here isn't double-counting**: `GoalHolding` tagging *labels* holdings, it
doesn't move money into a separate pot, and this projection's starting corpus is the entire
portfolio — tagged holdings included. Ignoring goals would be the choice that overstates what's
actually available for retirement.

**But a goal can opt out** (`Goal.include_in_retirement_projection`, default true, migration
`8a41c02b7f13`). Found immediately against real data: a goal literally named "Retirement"
duplicates the projection's own income stream, so drawing it as a lump sum *as well* double-counts
— on the real portfolio it swung readiness from 259% to 100%. The flag also covers goals funded
from outside the portfolio. This is the "funding source" idea from the source spec, in its
minimum honest form.

**Per-goal inflation** (`Goal.inflation_pct_override`, migration `67373d8ea0e9`): education and
healthcare inflate faster than the general rate, so a goal may override the global
`inflation_pct`. Setting it to **0** is how a fixed nominal cost (a loan payoff) is expressed —
deliberately *not* a separate `is_inflation_adjusted` boolean, which would be a second way to say
the same thing. Because `None` means "use the global rate" and is therefore a meaningful stored
value, `update_goal` needs an explicit `UNSET` sentinel to tell "clear the override" apart from
"don't touch it" — a plain `None` default can't express both.

**Whole-year bucketing**: an event's age is `current_age + floor(years until target_date)`, and
it's inflated over exactly that many years. So an event shown in the age-55 row is inflated by
precisely the number of rows between now and that row — the ledger stays internally consistent,
rather than mixing fractional-year inflation into an integer-year table.

**Events are claims, and claims can exceed the corpus.** A lump sum is taken after the year's
growth and contribution, capped at what's actually there: the excess is reported as
`lump_sum_shortfall_paise` rather than modelled as borrowing, and the corpus floors at zero.
**Post-retirement events also raise the required corpus** (each discounted at the post-retirement
return back to the retirement year) — they're claims on the same corpus as the income stream, so
counting only the income stream would understate what's needed. Pre-retirement events instead
reduce the *projected* corpus, via the simulation itself, and leave the required figure alone.
Events falling after life expectancy, or whose target date has already passed, are reported with
an `excluded_reason` rather than silently dropped.

## 18. Retirement Scenario Analysis (shipped — `compute/retirement.py`)

The same projection run under three sets of assumptions, so the output is a *range* rather than a
single number presented as though it were a forecast — the source spec's stated core principle
("the most important feature should ultimately be scenario-based planning").

**Two knobs, not three.** The spec describes the conservative case as lower returns *and* higher
inflation *and* higher withdrawals. But withdrawals here are *derived* from inflation: the
retirement-year nominal figure is today's money inflated to it (§16), and it escalates at
inflation every year after. Raising inflation therefore already raises withdrawals — a separate
withdrawal knob would double-apply the same pessimism.

**Symmetric deltas around the user's own base**, via two editable assumption keys
(`scenario_return_delta_pct` default 2, `scenario_inflation_delta_pct` default 1 — roughly the
spread the spec's own 9/10/12% worked example implies). Conservative subtracts the return delta
and adds the inflation delta; optimistic does the reverse. Deltas rather than six independently
configurable rates so "how pessimistic is the pessimistic case" stays one legible number, and so
the base case is *by construction* identical to what the projection endpoint already shows
(asserted in tests — the two must never drift).

**The inflation delta shifts per-goal overrides too.** "What if inflation runs hotter than
expected" should make an explicit 8% education assumption 9%, not leave it pinned while
everything around it moves.

**The portfolio is priced once** and passed into all three runs via
`compute_retirement_projection`'s `starting_corpus_paise` parameter — it's the one genuinely
expensive input (a full pass over every transaction) and is identical across scenarios, so
pricing it per scenario would triple the cost for no difference in result.

## 19. Reverse Planning (shipped — `compute/retirement.py`)

The spec names this a primary output: not just "how much will I have?" but **"how much do I need
to invest?"**. Solves the accumulation identity for the contribution instead of evaluating it:

```
corpus_at_retirement = C0·(1+r)^n + P0·A − Σ event·(1+r)^(years it compounds after)
        ⇒      P0     = (required − C0·(1+r)^n + Σ FV(events)) / A
```

where `A` is the growing-annuity future-value factor for a contribution stepping up at the user's
own annual increase — `((1+r)^n − (1+g)^n)/(r−g)`, with the **`r == g` singularity branched** to
`n·(1+r)^(n−1)`. That case is ordinary, not pathological: a 10% step-up alongside a 10% expected
return hits it exactly, and the unbranched form would divide by zero on a perfectly normal plan.
(Same shape of branch the withdrawal-side growing annuity needed in §16.)

**This is the forward loop's own arithmetic, rearranged** — which is precisely why the tests don't
trust the algebra on its own. The correctness gate is a **round-trip**: solve for the
contribution, feed it back through the *forward simulation*, and assert the corpus at retirement
lands on the required figure to within a rupee. Any wrong term — a mis-stated compounding
exponent, a dropped event, the singularity branch — fails to close. It's run three ways: plain,
with pre-retirement lump-sum events draining the corpus, and at `r == g`.

**An over-funded plan reports zero, not a negative contribution.** When the existing corpus alone
clears the requirement the solve goes negative, which isn't a meaningful instruction; it's floored
at zero and `additional_annual_investment_paise` likewise floors at the gap above what's already
planned. (This also made the first round-trip fixture meaningless — a starting corpus that already
covered the target produced the correct answer 0, proving nothing about the algebra, so the
fixtures deliberately start well short.)

## 20. What-If Exploration (shipped — `GET /retirement/projection` query params)

The spec's §25: *"what happens if I retire two years later?"*, *"…if I increase my SIP by
₹25,000/month?"*, *"…if returns are only 8%?"*. No new engine — the projection already
parameterizes every lever (§16) and scenarios already added return/inflation deltas (§18), so
this is the same computation over an adjusted input.

**The overrides must never persist**, and that's enforced structurally rather than by discipline.
`allocation/service.py::detached_profile_with` builds an **unattached** `RiskProfile` copy;
mutating the session-attached instance instead would be silently destructive, because SQLAlchemy
autoflushes pending changes before the next query — a read-only "what if I retired at 62" request
would quietly rewrite the saved plan. A test asserts the stored profile is byte-for-byte unchanged
after a request that overrides three fields at once.

Deltas (`return_delta_pct`) are passed straight through to the same parameters scenario analysis
uses, so "shift returns down 3%" and "the conservative scenario" are the same code path with
different numbers — they can't disagree.

Real-portfolio sanity check, all four from one saved plan and leaving it untouched: retiring at 65
lifts readiness 52% → 83% and pushes depletion 71 → 81; +₹25k/month SIP moves it to 55%; a 3%
worse return drops it to 29% with the corpus gone by 67.

## 21. Retirement Sequence-of-Returns Risk (shipped — `compute/monte_carlo.py`)

The deterministic projection (§16) earns the *average* return every single year. Real markets
deliver the same average **in an order**, and order matters enormously once you are withdrawing: a
poor first decade of retirement sells units at depressed prices, permanently shrinking the base the
later good years compound on. Two paths with identical mean returns can end solvent or bankrupt.
Averaging hides precisely the risk a retiree cares about, so `run_retirement_monte_carlo` puts a
probability next to the point estimate — 5,000 random orderings of the same accumulate-then-
decumulate path, asking how many survive to life expectancy.

Real-portfolio demonstration, and the clearest argument for the feature: a ₹1L/month plan the
deterministic projection scores at **259% readiness, lasting to 85** survives only **89.3%** of
orderings — about one run in nine depletes, typically at 81. The point estimate isn't wrong; it
just isn't a probability.

**Annual steps, deliberately.** §16 steps annually, so this does too. Monthly steps would be
finer-grained but could no longer collapse *exactly* onto the deterministic result — and that
collapse is the correctness gate. The vectorized simulation and the year loop are two
implementations of the same ordering (return on opening balance → contribution or withdrawal →
lump sums → floor at zero), so **at zero volatility every simulated path must reproduce the
deterministic terminal corpus and depletion age exactly**. Nothing else stops the two drifting
apart as either changes.

That gate immediately earned its keep: the first run missed by ~700 paise on ₹86 Cr, which turned
out not to be float noise but the *caller* reconstructing the annual withdrawal as
`round(monthly) × 12` — losing up to 12 paise that then compounded across 25 retirement years. The
projection now exposes `first_year_annual_withdrawal_paise` exactly, so the endpoint can't
reintroduce the same error.

**Returns are stochastic; inflation is not.** Sequence-of-returns is the dominant, best-evidenced
retirement risk, and equity/debt volatility are already grounded assumptions here (blended by
target allocation, §14). Inflation variability (also named in the spec) would need a volatility
figure this app has no basis for, so it is deferred rather than invented — the same treatment
`debt_marginal_tax_rate_pct` gets.

A depleted corpus is **absorbing**: returns on zero are zero and further withdrawals clamp back to
zero, so a path that fails stays failed rather than mathematically recovering.

Its own endpoint (`GET /retirement/monte-carlo`, button-triggered) rather than folded into the
projection — 5,000 paths is a different cost tier from a few dozen rows of arithmetic, matching the
goal Monte Carlo and `POST /ai/analyze` precedent.

## 22. Reserve, Healthcare and Terminal Corpus (shipped — `compute/retirement.py`)

Three inputs from the source spec that each change the retirement math in a specific way, added
together because they interact. All nullable, and **inert when unset** — an existing plan projects
identically until they're filled in (asserted in tests).

**Emergency reserve** (spec §12). Real money in the portfolio, but explicitly *not* available to
fund retirement, so the projection compounds `portfolio − reserve` and reports both figures. A
reserve larger than the portfolio floors the investable corpus at zero rather than going negative.

**Healthcare** (spec §13) is a **second withdrawal stream with its own escalation**, not a bigger
general withdrawal. Blending it into the general figure would understate it badly in later years,
since healthcare compounds faster: over a 25-year retirement, 10% vs 6% is a ~2.5× divergence.
Entered in today's rupees, inflated to retirement, then escalating at `healthcare_inflation_pct`
(default 10, the spec's own figure) while the general withdrawal escalates at general inflation.
It adds its own growing-annuity term to the required corpus — the same formula, its own rate.

**Terminal / inheritance corpus** (spec §23). Money the user wants left behind can't also be
spent, so its present value is added to the corpus needed at retirement. Entered in **today's**
rupees and inflated to life expectancy, matching how the monthly requirement is handled: "leave
₹1 Cr" means ₹1 Cr of today's purchasing power, not a nominal figure worth a fraction of that by
then. Depletion still means *hitting zero* — the terminal target is reported separately via
`terminal_corpus_target_met` rather than redefining what failure means, which would have made
"depleted" ambiguous.

Real-portfolio effect, and why they belong in one phase: individually each looks modest
(readiness 259% → 242% for a ₹25L reserve, → 168% for ₹3L/yr healthcare, → 166% for a ₹2 Cr
bequest), but **together they take readiness from 259% to 115%**. Modelling any one alone would
leave a materially over-optimistic picture.

The Monte Carlo simulation (§21) carries the healthcare stream too, with its own escalation —
otherwise the zero-volatility collapse against the deterministic engine would no longer hold, and
that gate is what keeps the two implementations honest.

**Tax remains deferred** (spec §14), which the spec itself sanctions for a first version: every
figure here is pre-tax, and the UI says so.

## 23. Holding Source Attribution (shipped — `compute/metrics.py::position_sources`)

Backs the Funds page's three tabs (Active funds / By broker / Personal). Each `(folio,
scheme_key)` position is attributed to whichever import supplied its transactions, derived from
the originating `raw_cas_upload.parser_version` rather than pattern-matching the folio string,
which has no stable format across sources.

A CAS is parsed by casparser, whose `parser_version` is casparser's own release number — there's
no fixed value to match on, so anything that isn't a *known* importer
(`dezerv-csv-v1`, `zerodha-tradebook-csv-v1`, `csv-import-v1`) is treated as CAS-sourced.

**The honest limit of "Direct".** This attributes a position by *where its data came from*, which
is not the same as where the fund was bought: a CAS comes from the RTAs (CAMS/KFintech) and
therefore lists holdings bought through platforms too. Real-data-verified — 4 funds are held under
both an AMC folio and a platform folio. So `SOURCE_DIRECT` means "held under an AMC folio", not
"never bought via a broker", and the Personal tab says exactly that rather than overclaiming.

Real data also confirms **no position mixes sources** (folios differ per platform), so attribution
is unambiguous in practice; a position that somehow did is reported as `Mixed` rather than
silently attributed to whichever row happened to win.

All three tabs scope to **currently-held positions only**. On the real portfolio that matters more
than it sounds: 44 of 86 positions are fully exited, and the page previously listed them
interleaved with live holdings. Exited funds keep their realised history on the fund detail page.
