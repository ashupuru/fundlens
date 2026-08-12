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

**Cash flow construction** (FR-3.1): for a given entity (fund / folio / category / goal /
platform / portfolio):
- Every `purchase`/`sip`/`switch_in` → negative flow at `txn_date`.
- Every `redemption`/`dividend_payout`/`switch_out` → positive flow at `txn_date`.
- Terminal flow: `units_remaining * latest_nav` as a positive flow at `as_of_date` (today, or the
  report's as-of date).

**Solver:** `scipy.optimize.brentq` bracketed on `[-0.9999, 10.0]` annual rate, falling back to
Newton's method (`scipy.optimize.newton`) seeded from the bracket's midpoint if `brentq` fails to
bracket a root (can happen with unusual flow sign patterns). This two-tier approach is chosen
deliberately over a single Newton solve because Newton alone can diverge on flow patterns common
in real portfolios (large late lump sums, long fund histories) — this is exactly the kind of bug
class the 0.01%-of-Excel accuracy NFR is designed to catch.

**Edge cases (PRD FR-3.1):**
- **Fully exited fund:** terminal flow is 0 (no `units_remaining`); this is "realized XIRR" and
  is labeled as such in the UI, not conflated with a still-held fund's XIRR.
- **< 1 year of history:** XIRR is still computed but the UI labels it "annualized — based on
  < 1yr of data, treat as noisy" and shows absolute return alongside it as the primary number.
- **Degenerate flows** (all-positive, all-negative, or a single flow): solver has no root:
  return `null` with a reason code (`insufficient_flow_variation`) rather than raising, so a
  portfolio-wide computation doesn't fail because one fund is degenerate.

## 3. Tax Engine

Modeled as a **versioned, config-driven ruleset**, not hardcoded branches, per PRD §5.1 and the
§10 risk "tax rules change (budget cycles)."

### 3.1 Classification
Per lot, at redemption: `asset_class` (equity / debt / hybrid — derived from `scheme_metadata`
SEBI category) × `holding_period` determines STCG vs LTCG, per the `assumption` version active on
`sale_date` (not today's rules applied retroactively — historical redemptions use the rules in
force when they happened).

### 3.2 Rules covered at P0/P1
- Equity-oriented (≥65% equity): LTCG (>12mo) taxed above ₹1.25L exemption/FY; STCG (≤12mo) flat
  rate.
- Debt-oriented: post-April-2023 rules — all gains taxed at slab rate regardless of holding
  period, no indexation, no LTCG concept (config flag `debt_indexation_eligible` scoped per
  purchase date, since pre-2023 purchases retain the old regime — this is the grandfathering
  case the PRD explicitly calls out).
- Hybrid: classified by the ≥65%/<65% equity-allocation test at the scheme level.

### 3.3 Exemption tracking
`ltcg_exemption_used_this_fy` is computed across **all lots realized in the FY, per PAN**
(PRD §12.2 — tax math never crosses PAN boundaries), ordered by `sale_date`, consuming the ₹1.25L
headroom on a first-realized-first-consumed basis. This feeds both the capital-gains report
(FR-10.2) and the harvesting action-plan generator (FR-6.3).

### 3.4 SELL friction statement (FR-6.2)
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

Any `SELL`/`SWITCH` verdict is required (FR-6.2) to attach the §3.4 tax-friction statement before
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
