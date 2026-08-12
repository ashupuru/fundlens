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

**Status: §3.1–3.3 shipped in P0.5. §3.4–3.5 are P1, not yet built** — noted inline below so this
doc doesn't drift back into describing aspirational behavior as current, which happened in an
earlier draft of this section.

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
`classify_gain(asset_class, purchase_date, holding_period_days) -> GainClassification`, per lot at
redemption:
- **Equity**: LTCG if holding period > 365 days, else STCG. (Exemption-headroom math, i.e. the
  ₹1.25L/FY LTCG threshold, is **not yet implemented** — see §3.4.)
- **Debt, purchased on/after 2023-04-01** ("specified mutual fund" under the Finance Act 2023
  redefinition): always STCG-equivalent — the LTCG concept doesn't exist for these regardless of
  holding period.
- **Debt, purchased before 2023-04-01**: old regime — LTCG if holding period > 3×365 days, else
  STCG (grandfathered; this is the case the PRD explicitly calls out).
- **`NEEDS_REVIEW` asset class, or purchase predates scheme_metadata coverage**: `GainType.UNCLASSIFIED`
  — never guessed.

Actual tax rates and slab logic are out of scope for this classification step; it labels
STCG/LTCG/UNCLASSIFIED only.

### 3.3 Capital gains report (FR-10.2 — `compute/capital_gains.py`, `reports/capital_gains_csv.py`)
`get_capital_gains_report(session, fy_label)` joins `RealizedGain` (filtered to `sale_date` within
the FY) with `RawTransaction` (scheme identity) and `SchemeMetadata` (tax classification), scheme-
and lot-wise. `gain_paise` is **derived** as `sale_value_paise − cost_of_acquisition_paise` in the
report layer rather than trusted verbatim from the FIFO ledger's independently-rounded
`realized_gain_paise`, guaranteeing `sale − cost == gain` holds exactly for every line (verified
against 3 real financial years of the user's actual redemption history — held for all 384 lines
across FY2023-24/2024-25/2025-26). Exposed via `GET /reports/capital-gains` (JSON) and
`GET /reports/capital-gains.csv` (Schedule-112A-adjacent CSV export), both scoped by `?fy=`.

### 3.4 Exemption tracking — **not yet built (P1)**
Planned: `ltcg_exemption_used_this_fy` computed across all lots realized in the FY, per PAN
(PRD §12.2 — tax math never crosses PAN boundaries), ordered by `sale_date`, consuming the ₹1.25L
headroom first-realized-first-consumed. Would feed both the capital-gains report (§3.3) and the
harvesting action-plan generator (FR-6.3).

### 3.5 SELL friction statement (FR-6.2) — **not yet built (P1)**
For any proposed sell/switch, the tax engine computes:
```
tax_impact = Σ per-lot (STCG or LTCG tax owed, net of remaining exemption headroom)
exit_load = per scheme's exit-load schedule, if within the load period
net_of_tax_breakeven = (tax_impact + exit_load) / proposed_switch_amount
                        → expressed as "new fund must outperform by X%/yr over N years to recoup"
```
This structured output is what the recommendation engine (§5) and AI layer consume — the
breakeven number itself is compute-engine output, never LLM-generated (PRD FR-5.2).

## 4. Benchmark Simulation Engine

**"What if these cash flows had gone into the benchmark instead?"** (FR-4.1): take the exact same
signed, dated cash-flow list used for a fund's XIRR, replace the terminal-flow logic with "buy
benchmark units at each purchase date's benchmark level, redeem at each redemption date's level,"
and run the same XIRR solver. This guarantees the comparison is apples-to-apples on timing, which
is the entire point of the Zerodha-Coin-style comparison — a naive CAGR-vs-CAGR comparison would
not control for cash flow timing and would misattribute alpha caused by contribution timing.

- **Portfolio-level** (FR-4.2): same simulation against the user's chosen reference index
  (default Nifty 50 TRI, PRD §12.3), computed per PAN, plus a display-only household rollup.
- **Category-correct** (FR-4.3): default benchmark selection keyed off `scheme_metadata.category`
  (e.g. midcap fund → Nifty Midcap 150 TRI), user-overridable per fund.
- **Index-fund counterfactual** (FR-4.4): same simulation but against a specific index fund's
  actual NAV series (fee-inclusive) rather than the raw TRI, so the comparison is fund-vs-fund,
  not fund-vs-theoretical-index.

## 5. Recommendation Rubric Engine

**Design goal (PRD FR-6.1, §5.6): transparent and tunable, not a black-box score.** Each fund
gets a `verdict` (§data-model.md) built from independently-computed, independently-displayed
component scores:

| Component | Computation | Weight (default, tunable) |
|---|---|---|
| 3yr/5yr rolling alpha | mean(rolling-window fund XIRR − benchmark XIRR) via §4's engine | 30% |
| Consistency | % of rolling windows where fund beat benchmark | 20% |
| Cost | expense ratio percentile vs category peers; direct-vs-regular flag (§6) | 20% |
| Overlap/redundancy | pairwise holding overlap vs rest of portfolio (FR-5.3) | 15% |
| Category fit | actual-vs-target allocation drift contribution (§7) | 15% |

Weights live in the `assumption` table, not code, so they're user-tunable and versioned per PRD
§5.7. The verdict output is deliberately not just `HOLD`/`SELL`/`SWITCH` — it carries the
component scores, a generated reasoning string built from a template over those scores (not an
LLM call — this stays in the deterministic layer so a verdict is reproducible without network
access), a confidence measure (based on data completeness — e.g. a fund held < 3yr has capped
confidence since 3yr/5yr components are unavailable), and a "what would change this verdict"
counterfactual (e.g. "would flip to HOLD if expense ratio dropped below 1.1% via a direct-plan
switch").

Any `SELL`/`SWITCH` verdict is required (FR-6.2) to attach the §3.5 tax-friction statement before
it can be surfaced in the UI — enforced by the verdict schema making `tax_impact` non-nullable
when `verdict_type != HOLD`.

## 6. Direct-vs-Regular Audit

Flagged at the scheme level (`scheme_metadata.plan_type == regular`). Commission drag is
estimated as the expense-ratio delta between the held regular plan and its direct-plan twin
(matched by AMC + scheme family), applied to the fund's average AUM-weighted holding value over
the holding period, expressed in ₹/year (PRD §5.3). This is a P0 requirement precisely because
it's mechanical and high-value — no rubric judgment involved, just a lookup and a multiplication.

## 7. Allocation & Drift

Actual allocation computed by summing current lot value grouped by
`scheme_metadata.category`-derived buckets (equity/debt/gold/international, and
large/mid/small within equity). Compared against the active `risk_profile`'s target bands;
drift = actual% − target%. FR-7.2's "prefer directing new SIP money over selling" is implemented
as a rebalancing suggestion that solves for the *next N months' SIP allocation* that closes the
drift gap without generating a sell (a small LP/greedy allocation, not a rubric score).

## 8. Alternatives Engine (FR-9.1)

For any `SELL`/`SWITCH` verdict, candidates are pulled from `scheme_metadata` filtered to the
same category, ranked by the same rolling-consistency and cost components used in §5 (reused, not
reimplemented), with an explicit tie-breaking bias toward index funds when
`scheme_metadata.category` is flagged index-replicable. "Simplification mode" (FR-9.2, P2) is a
downstream reduction over this same ranking: greedily pick a minimal fund set that covers the
portfolio's category exposure within the risk profile's bands, and diff its blended expense ratio
against the current portfolio's AUM-weighted expense ratio for the ₹/year savings figure.
