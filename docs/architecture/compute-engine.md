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
  lots hold raises `InsufficientLotsError` (surfaced as a per-scheme error on the rebuild
  summary, not a crash) — the expected signal when ingested history doesn't cover a scheme's
  full past (see gap detection, data-model.md §2).
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
| Rolling alpha | mean(rolling-window fund XIRR − benchmark XIRR) via §4 | scheme-level 3yr/5yr point-to-point NAV CAGR vs benchmark CAGR (`compute/rolling_returns.py`) — see below for why | 30 |
| Consistency | % of rolling windows where fund beat benchmark | fraction of the (at most 2) available 3yr/5yr windows beaten | 20 |
| Category fit | actual-vs-target allocation drift contribution (§7) | unchanged — needs P1.4's drift, which is why P1.4 was built first | 15 |
| Cost | expense ratio percentile vs category peers; direct-vs-regular flag | **narrowed to the binary direct-vs-regular flag alone** (§6) — inherits cost's original weight | 20 |
| Overlap/redundancy | pairwise holding overlap vs rest of portfolio (FR-5.3) | **omitted entirely** — needs monthly AMC portfolio disclosures, a data source this app doesn't integrate | — |

**Why NAV-based CAGR, not XIRR, for rolling alpha**: this is scheme-level performance (how has
the fund itself done over the trailing window), not investor-position performance (which would
conflate the fund's skill with the specific investor's entry-timing luck) — the same number a
fund fact sheet reports as "3Y/5Y returns." A true moving-window methodology (many overlapping
windows, not one snapshot per length) is deferred; this computes a single window ending today for
each of 3yr/5yr, so "consistency" is a simplified 2-point proxy, not a real rolling win-rate.

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
