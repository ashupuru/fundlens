"""Benchmark simulation engine (compute-engine.md §4, FR-4.1/4.2/4.3):
"what if these exact cash flows had gone into the benchmark instead?"

Per position: on each purchase-type flow, the real rupee amount invested
buys benchmark units at that date's benchmark value (real amount either
way — money invested is money invested, regardless of destination
instrument). On each redemption-type flow that reduces the fund's own
units (REDEMPTION/SWITCH_OUT/SWITCH_OUT_MERGER/REVERSAL — DIVIDEND_PAYOUT
is excluded, see below), the SAME FRACTION of the fund's currently-held
units being liquidated is applied to the parallel benchmark-units position,
and *that* fraction's benchmark value becomes the benchmark-side cash flow
— not the fund's real redemption proceeds. A still-held position gets a
terminal flow valued from its remaining benchmark units at the latest
available price, mirroring how the fund side values an open position from
its latest NAV.

**Why not just reuse the fund's real redemption amount** (an earlier,
wrong version of this module did exactly that): live-verified against the
user's real 56-position portfolio, it produced alpha exactly 0.0 for two
thirds of fully-exited positions and never a single negative alpha —
because for any fully-exited position (no terminal flow), reusing the real
redemption amount makes the benchmark cash-flow list byte-identical to the
fund's own, so benchmark_xirr trivially equals fund_xirr by construction,
not by comparison. The fund's real exit proceeds reflect the FUND's
performance, not what the benchmark would have been worth — using them on
the benchmark side silently erases the entire comparison for every
fully-exited position, which is most of a typical portfolio's history.

DIVIDEND_PAYOUT has no unit reduction (real-data-verified: `units` is
always None on these rows) and all four benchmark proxies are Growth-option
funds that don't distribute cash — there's no clean benchmark-side
equivalent, so these flows are included in the fund's own XIRR (unchanged)
but excluded entirely from the benchmark timeline, rather than guessed at.

Transactions are processed in `(txn_date, sequence)` order — same
same-day tiebreaker as FIFO lot accounting (db/models.py `sequence`
docstring) — because the liquidated-fraction calculation needs to know
units held *before* each redemption, which is meaningless unprocessed
out of chronological order.

FR-4.4 ("index fund counterfactual" — compare against a real index fund's
fee-inclusive NAV, not a raw index) is effectively already what this module
computes: every benchmark_id here IS a real index fund/ETF's NAV (see
benchmark/proxy_config.py's proxy rationale), not a theoretical TRI value.
No separate counterfactual computation is needed on top of this.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.benchmark.category_mapping import default_benchmark_for_category
from fundlens.benchmark.proxy_config import PROXY_NOTE_BY_BENCHMARK
from fundlens.compute.holdings import HoldingView, get_current_holdings
from fundlens.compute.metrics import (
    CASH_INFLOW_TYPES,
    CASH_OUTFLOW_TYPES,
    compute_portfolio_metrics,
    get_all_holding_metrics,
    get_all_positions,
    positions_with_transactions,
)
from fundlens.compute.xirr import CashFlow, compute_xirr
from fundlens.db.models import BenchmarkHistory, BenchmarkId, RawTransaction, SchemeMetadata


@dataclass(frozen=True)
class BenchmarkComparison:
    folio: str
    scheme_key: str
    scheme_name: str
    amc: str
    benchmark_id: BenchmarkId | None
    proxy_note: str | None
    fund_xirr: float | None
    fund_xirr_reason: str | None
    benchmark_xirr: float | None
    benchmark_xirr_reason: str | None
    alpha_pct: float | None


def _benchmark_value_on_or_before(
    session: Session, benchmark_id: BenchmarkId, on_date: date
) -> Decimal | None:
    return session.execute(
        select(BenchmarkHistory.value)
        .where(BenchmarkHistory.benchmark_id == benchmark_id, BenchmarkHistory.nav_date <= on_date)
        .order_by(BenchmarkHistory.nav_date.desc())
        .limit(1)
    ).scalar_one_or_none()


def _latest_benchmark_value(
    session: Session, benchmark_id: BenchmarkId
) -> tuple[Decimal, date] | None:
    row = session.execute(
        select(BenchmarkHistory.value, BenchmarkHistory.nav_date)
        .where(BenchmarkHistory.benchmark_id == benchmark_id)
        .order_by(BenchmarkHistory.nav_date.desc())
        .limit(1)
    ).first()
    return (row.value, row.nav_date) if row is not None else None


def _simulate_benchmark_cash_flows(
    session: Session,
    transactions: list[RawTransaction],
    open_holding: HoldingView | None,
    benchmark_id: BenchmarkId,
) -> tuple[list[CashFlow], str | None]:
    benchmark_units = Decimal("0")
    fund_units_held = Decimal("0")
    cash_flows: list[CashFlow] = []

    ordered = sorted(transactions, key=lambda t: (t.txn_date, t.sequence))
    for txn in ordered:
        if txn.amount_paise is None:
            continue
        if txn.txn_type not in CASH_OUTFLOW_TYPES and txn.txn_type not in CASH_INFLOW_TYPES:
            continue

        value = _benchmark_value_on_or_before(session, benchmark_id, txn.txn_date)
        if value is None or value == 0:
            return [], "benchmark_history_unavailable"

        if txn.txn_type in CASH_OUTFLOW_TYPES:
            magnitude = abs(txn.amount_paise)
            amount = Decimal(magnitude) / 100
            benchmark_units += amount / value
            cash_flows.append(CashFlow(date=txn.txn_date, amount=-amount))
            if txn.units:
                fund_units_held += abs(txn.units)
        elif txn.units and fund_units_held > 0:
            # Liquidate the SAME FRACTION of the parallel benchmark position
            # as was actually liquidated from the fund — not the fund's real
            # proceeds, which reflect the fund's own performance, not the
            # benchmark's (see module docstring for why this matters).
            fraction_sold = min(abs(txn.units) / fund_units_held, Decimal("1"))
            redemption_amount = fraction_sold * benchmark_units * value
            benchmark_units -= fraction_sold * benchmark_units
            fund_units_held -= abs(txn.units)
            cash_flows.append(CashFlow(date=txn.txn_date, amount=redemption_amount))
        # else: a cash-only distribution (DIVIDEND_PAYOUT — no units field)
        # or a redemption preceding any tracked purchase (data anomaly) has
        # no benchmark-side equivalent under Growth-option proxies; skipped
        # on this side only, the fund's own XIRR is unaffected.

    units_remaining = open_holding.units_remaining if open_holding else Decimal("0")
    if units_remaining != 0:
        latest = _latest_benchmark_value(session, benchmark_id)
        if latest is None:
            return [], "benchmark_history_unavailable"
        value, as_of = latest
        cash_flows.append(CashFlow(date=as_of, amount=benchmark_units * value))

    return cash_flows, None


def compute_position_benchmark_comparison(
    session: Session,
    folio: str,
    key: str,
    transactions: list[RawTransaction],
    open_holding: HoldingView | None,
    fund_xirr: float | None,
    fund_xirr_reason: str | None,
    override_benchmark_id: BenchmarkId | None = None,
) -> BenchmarkComparison:
    scheme_name = transactions[0].scheme_name if transactions else key
    amc = transactions[0].amc if transactions else ""
    amfi_code = transactions[0].amfi_code if transactions else None

    benchmark_id = override_benchmark_id
    if benchmark_id is None and amfi_code:
        metadata = session.get(SchemeMetadata, amfi_code)
        if metadata is not None:
            benchmark_id = default_benchmark_for_category(
                metadata.category_raw, metadata.tax_asset_class
            )

    if benchmark_id is None:
        return BenchmarkComparison(
            folio=folio,
            scheme_key=key,
            scheme_name=scheme_name,
            amc=amc,
            benchmark_id=None,
            proxy_note=None,
            fund_xirr=fund_xirr,
            fund_xirr_reason=fund_xirr_reason,
            benchmark_xirr=None,
            benchmark_xirr_reason="no_category_benchmark",
            alpha_pct=None,
        )

    benchmark_flows, reason = _simulate_benchmark_cash_flows(
        session, transactions, open_holding, benchmark_id
    )
    if reason is None:
        benchmark_result = compute_xirr(benchmark_flows)
        benchmark_xirr = benchmark_result.rate
        benchmark_xirr_reason = benchmark_result.reason
    else:
        benchmark_xirr = None
        benchmark_xirr_reason = reason

    alpha_pct = (
        (fund_xirr - benchmark_xirr) * 100
        if fund_xirr is not None and benchmark_xirr is not None
        else None
    )

    return BenchmarkComparison(
        folio=folio,
        scheme_key=key,
        scheme_name=scheme_name,
        amc=amc,
        benchmark_id=benchmark_id,
        proxy_note=PROXY_NOTE_BY_BENCHMARK[benchmark_id],
        fund_xirr=fund_xirr,
        fund_xirr_reason=fund_xirr_reason,
        benchmark_xirr=benchmark_xirr,
        benchmark_xirr_reason=benchmark_xirr_reason,
        alpha_pct=alpha_pct,
    )


def get_all_benchmark_comparisons(session: Session) -> list[BenchmarkComparison]:
    positions = get_all_positions(session)
    if not positions:
        return []

    metrics_by_position = {(m.folio, m.scheme_key): m for m in get_all_holding_metrics(session)}
    open_holdings = {(h.folio, h.scheme_key): h for h in get_current_holdings(session)}
    txns_by_position = positions_with_transactions(session, positions)

    results = []
    for folio, key in positions:
        m = metrics_by_position.get((folio, key))
        results.append(
            compute_position_benchmark_comparison(
                session,
                folio=folio,
                key=key,
                transactions=txns_by_position.get((folio, key), []),
                open_holding=open_holdings.get((folio, key)),
                fund_xirr=m.xirr if m else None,
                fund_xirr_reason=m.xirr_reason if m else None,
            )
        )
    return results


@dataclass(frozen=True)
class PortfolioBenchmarkComparison:
    benchmark_id: BenchmarkId
    proxy_note: str
    portfolio_xirr: float | None
    portfolio_xirr_reason: str | None
    benchmark_xirr: float | None
    benchmark_xirr_reason: str | None
    alpha_pct: float | None


def compute_portfolio_benchmark_comparison(
    session: Session, benchmark_id: BenchmarkId = BenchmarkId.NIFTY50_TRI
) -> PortfolioBenchmarkComparison:
    """FR-4.2: portfolio-level, against a single reference index (default
    Nifty 50 TRI per PRD §12.3), user-overridable via `benchmark_id`."""
    positions = get_all_positions(session)
    portfolio_metrics = compute_portfolio_metrics(session)

    if not positions:
        return PortfolioBenchmarkComparison(
            benchmark_id=benchmark_id,
            proxy_note=PROXY_NOTE_BY_BENCHMARK[benchmark_id],
            portfolio_xirr=portfolio_metrics.xirr,
            portfolio_xirr_reason=portfolio_metrics.xirr_reason,
            benchmark_xirr=None,
            benchmark_xirr_reason="insufficient_flow_variation",
            alpha_pct=None,
        )

    open_holdings = {(h.folio, h.scheme_key): h for h in get_current_holdings(session)}
    txns_by_position = positions_with_transactions(session, positions)

    all_benchmark_flows: list[CashFlow] = []
    unavailable = False
    for folio, key in positions:
        flows, reason = _simulate_benchmark_cash_flows(
            session,
            txns_by_position.get((folio, key), []),
            open_holdings.get((folio, key)),
            benchmark_id,
        )
        if reason is not None:
            unavailable = True
            continue
        all_benchmark_flows.extend(flows)

    benchmark_xirr: float | None
    benchmark_xirr_reason: str | None
    if unavailable and not all_benchmark_flows:
        benchmark_xirr = None
        benchmark_xirr_reason = "benchmark_history_unavailable"
    else:
        result = compute_xirr(all_benchmark_flows)
        benchmark_xirr = result.rate
        benchmark_xirr_reason = result.reason

    alpha_pct = (
        (portfolio_metrics.xirr - benchmark_xirr) * 100
        if portfolio_metrics.xirr is not None and benchmark_xirr is not None
        else None
    )

    return PortfolioBenchmarkComparison(
        benchmark_id=benchmark_id,
        proxy_note=PROXY_NOTE_BY_BENCHMARK[benchmark_id],
        portfolio_xirr=portfolio_metrics.xirr,
        portfolio_xirr_reason=portfolio_metrics.xirr_reason,
        benchmark_xirr=benchmark_xirr,
        benchmark_xirr_reason=benchmark_xirr_reason,
        alpha_pct=alpha_pct,
    )
