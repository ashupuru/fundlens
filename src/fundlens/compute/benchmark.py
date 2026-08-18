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

import bisect
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.benchmark.category_mapping import default_benchmark_for_category
from fundlens.benchmark.proxy_config import PROXY_NOTE_BY_BENCHMARK
from fundlens.compute.allocation import compute_portfolio_valuation
from fundlens.compute.holdings import HoldingView, get_current_holdings
from fundlens.compute.metrics import (
    CASH_INFLOW_TYPES,
    CASH_OUTFLOW_TYPES,
    build_position_cash_flows,
    compute_portfolio_metrics,
    get_all_holding_metrics,
    get_all_positions,
    positions_with_transactions,
)
from fundlens.compute.xirr import CashFlow, compute_xirr
from fundlens.db.models import BenchmarkHistory, BenchmarkId, RawTransaction, SchemeMetadata
from fundlens.tax.asset_class import TaxAssetClass


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


@dataclass(frozen=True)
class BenchmarkValueSource:
    """Abstracts "what was this benchmark worth on/before date X" away from
    where the values come from — a real BenchmarkHistory row per
    benchmark_id (real_value_source, unchanged behavior from before this
    was extracted), or a synthetic in-memory blended series
    (_synthetic_value_source, build_blended_series below). Everything in
    simulate_benchmark_cash_flows is indifferent to which."""

    value_on_or_before: Callable[[date], Decimal | None]
    latest_value: Callable[[], tuple[Decimal, date] | None]


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


def real_value_source(session: Session, benchmark_id: BenchmarkId) -> BenchmarkValueSource:
    return BenchmarkValueSource(
        value_on_or_before=lambda on_date: _benchmark_value_on_or_before(
            session, benchmark_id, on_date
        ),
        latest_value=lambda: _latest_benchmark_value(session, benchmark_id),
    )


def _synthetic_value_source(series: list[tuple[date, Decimal]]) -> BenchmarkValueSource:
    """series must be sorted ascending by date (build_blended_series's
    output already is)."""
    dates = [d for d, _ in series]

    def value_on_or_before(on_date: date) -> Decimal | None:
        idx = bisect.bisect_right(dates, on_date) - 1
        return series[idx][1] if idx >= 0 else None

    def latest_value() -> tuple[Decimal, date] | None:
        return (series[-1][1], series[-1][0]) if series else None

    return BenchmarkValueSource(value_on_or_before=value_on_or_before, latest_value=latest_value)


def simulate_benchmark_cash_flows(
    transactions: list[RawTransaction],
    open_holding: HoldingView | None,
    value_source: BenchmarkValueSource,
) -> tuple[list[CashFlow], str | None]:
    """Public (also used by compute/yearly_report.py for its "since
    inception, as of report_end" benchmark comparison). Requires the FULL
    transaction history for a position, not a truncated window: it replays
    `transactions` from scratch to build up `fund_units_held`, which the
    partial-redemption fraction-sold math depends on being correct from
    inception — passing only a date-windowed slice would silently
    miscompute that fraction for any position that already existed before
    the window started. Callers needing a bounded-end (but not
    bounded-start) view should filter `transactions` to `txn_date <=
    as_of_date` only, never floor by a start date.
    """
    benchmark_units = Decimal("0")
    fund_units_held = Decimal("0")
    cash_flows: list[CashFlow] = []

    ordered = sorted(transactions, key=lambda t: (t.txn_date, t.sequence))
    for txn in ordered:
        if txn.amount_paise is None:
            continue
        if txn.txn_type not in CASH_OUTFLOW_TYPES and txn.txn_type not in CASH_INFLOW_TYPES:
            continue

        value = value_source.value_on_or_before(txn.txn_date)
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
        latest = value_source.latest_value()
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

    benchmark_flows, reason = simulate_benchmark_cash_flows(
        transactions, open_holding, real_value_source(session, benchmark_id)
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
    value_source = real_value_source(session, benchmark_id)

    all_benchmark_flows: list[CashFlow] = []
    unavailable = False
    for folio, key in positions:
        flows, reason = simulate_benchmark_cash_flows(
            txns_by_position.get((folio, key), []),
            open_holdings.get((folio, key)),
            value_source,
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


def _fetch_benchmark_series(
    session: Session, benchmark_id: BenchmarkId
) -> list[tuple[date, Decimal]]:
    rows = session.execute(
        select(BenchmarkHistory.nav_date, BenchmarkHistory.value)
        .where(BenchmarkHistory.benchmark_id == benchmark_id)
        .order_by(BenchmarkHistory.nav_date)
    ).all()
    return [(r.nav_date, r.value) for r in rows]


def _daily_returns(series: list[tuple[date, Decimal]]) -> dict[date, Decimal]:
    """(date -> return) for every date after the series' first, skipping any
    zero-value point (can't divide by it -- a genuine data anomaly, not
    something to guess a return for)."""
    returns: dict[date, Decimal] = {}
    for i in range(1, len(series)):
        prev_value = series[i - 1][1]
        cur_date, cur_value = series[i]
        if prev_value == 0:
            continue
        returns[cur_date] = cur_value / prev_value - 1
    return returns


@dataclass(frozen=True)
class BlendedSeries:
    values: list[tuple[date, Decimal]]  # sorted ascending by date
    start_date: date | None  # None only if there's no usable overlap at all
    excluded_components: list[BenchmarkId]  # zero/absent-weight components


def build_blended_series(
    session: Session, weights: dict[BenchmarkId, Decimal]
) -> BlendedSeries:
    """Combines each weighted benchmark's *daily returns* (never raw index
    levels — averaging levels across differently-scaled indices is
    dimensionally wrong) into one synthetic series, compounded from a base
    value of 1000. Starts on the first date *every* weighted component has
    a computable return (intersection, not union) -- real data-availability
    mismatch, confirmed live: the Nifty50 proxy's history goes back to
    2013, but the Midcap150/Smallcap250 proxies only to 2019-09-11
    (benchmark/proxy_config.py). A component with zero (or no) weight is
    dropped from the blend and reported via `excluded_components`, never
    silently included at some assumed weight.
    """
    active_weights = {bid: w for bid, w in weights.items() if w > 0}
    excluded = [bid for bid in weights if bid not in active_weights]

    if not active_weights:
        return BlendedSeries(values=[], start_date=None, excluded_components=excluded)

    returns_by_component = {
        bid: _daily_returns(_fetch_benchmark_series(session, bid)) for bid in active_weights
    }

    common_dates: set[date] | None = None
    for bid in active_weights:
        dates = set(returns_by_component[bid].keys())
        common_dates = dates if common_dates is None else common_dates & dates
    sorted_dates = sorted(common_dates or set())

    if not sorted_dates:
        return BlendedSeries(values=[], start_date=None, excluded_components=excluded)

    total_weight = sum(active_weights.values())
    values: list[tuple[date, Decimal]] = []
    level = Decimal("1000")
    for d in sorted_dates:
        blended_return = sum(
            (active_weights[bid] / total_weight) * returns_by_component[bid][d]
            for bid in active_weights
        )
        level = level * (1 + blended_return)
        values.append((d, level))

    return BlendedSeries(values=values, start_date=sorted_dates[0], excluded_components=excluded)


@dataclass(frozen=True)
class PortfolioEquityBlendedComparison:
    xirr: float | None
    xirr_reason: str | None
    benchmark_xirr: float | None
    benchmark_xirr_reason: str | None
    alpha_pct: float | None
    large_weight_pct: float
    mid_weight_pct: float
    small_weight_pct: float
    blended_series_start_date: date | None
    note: str


_EQUITY_BLEND_NOTE = (
    "Blended equity benchmark: your portfolio's actual large/mid/small-cap weighting "
    "(from currently-open equity holdings), compounded from each Nifty index proxy's daily "
    "returns -- not a raw average of index levels. Equity-only: debt/gold holdings are excluded "
    "from both sides, since no benchmark exists for them yet. A position with cash flows before "
    "the blended series' coverage start date is entirely excluded from the benchmark side "
    "(never partially reconstructed), same as the single-benchmark comparison above."
)


def compute_portfolio_equity_blended_benchmark_comparison(
    session: Session,
) -> PortfolioEquityBlendedComparison:
    """FR-4.2 extension: the doc-recommended "blend matching the portfolio's
    real mix" alternative to always comparing against a flat Nifty50 TRI.
    Weights come from compute_portfolio_valuation's cap-size breakdown
    (currently-open holdings only -- allocation is inherently a snapshot,
    same reasoning as compute_category_breakdown); LARGE/LARGE_AND_MID/OTHER
    all fall back to Nifty50, mirroring
    benchmark/category_mapping.py::default_benchmark_for_category's own
    fallback (no crisper single-cap index fits those buckets).
    """
    valuation = compute_portfolio_valuation(session)
    cap = valuation.equity_cap_breakdown
    large_paise = cap.large_paise + cap.large_and_mid_paise + cap.other_paise
    mid_paise = cap.mid_paise
    small_paise = cap.small_paise
    total_equity_paise = large_paise + mid_paise + small_paise

    if total_equity_paise == 0:
        return PortfolioEquityBlendedComparison(
            xirr=None,
            xirr_reason="no_equity_holdings",
            benchmark_xirr=None,
            benchmark_xirr_reason="no_equity_holdings",
            alpha_pct=None,
            large_weight_pct=0.0,
            mid_weight_pct=0.0,
            small_weight_pct=0.0,
            blended_series_start_date=None,
            note=_EQUITY_BLEND_NOTE,
        )

    large_weight_pct = large_paise / total_equity_paise * 100
    mid_weight_pct = mid_paise / total_equity_paise * 100
    small_weight_pct = small_paise / total_equity_paise * 100

    blended = build_blended_series(
        session,
        {
            BenchmarkId.NIFTY50_TRI: Decimal(large_paise),
            BenchmarkId.NIFTY_MIDCAP150_TRI: Decimal(mid_paise),
            BenchmarkId.NIFTY_SMALLCAP250_TRI: Decimal(small_paise),
        },
    )

    positions = get_all_positions(session)
    open_holdings = {(h.folio, h.scheme_key): h for h in get_current_holdings(session)}
    txns_by_position = positions_with_transactions(session, positions)

    amfi_code_by_position: dict[tuple[str, str], str | None] = {}
    for folio, key in positions:
        holding = open_holdings.get((folio, key))
        amfi_code = holding.amfi_code if holding else None
        if amfi_code is None:
            txns = txns_by_position.get((folio, key), [])
            amfi_code = txns[0].amfi_code if txns else None
        amfi_code_by_position[(folio, key)] = amfi_code

    amfi_codes = {c for c in amfi_code_by_position.values() if c}
    metadata_by_code = {
        m.amfi_code: m
        for m in session.scalars(
            select(SchemeMetadata).where(SchemeMetadata.amfi_code.in_(amfi_codes))
        )
    }
    equity_positions = {
        pos
        for pos, amfi_code in amfi_code_by_position.items()
        if amfi_code
        and metadata_by_code.get(amfi_code) is not None
        and metadata_by_code[amfi_code].tax_asset_class == TaxAssetClass.EQUITY
    }

    if blended.start_date is None:
        fund_cash_flows: list[CashFlow] = []
        for folio, key in equity_positions:
            pos = build_position_cash_flows(
                session, txns_by_position.get((folio, key), []), open_holdings.get((folio, key))
            )
            fund_cash_flows.extend(pos.cash_flows)
        fund_result = compute_xirr(fund_cash_flows)
        return PortfolioEquityBlendedComparison(
            xirr=fund_result.rate,
            xirr_reason=fund_result.reason,
            benchmark_xirr=None,
            benchmark_xirr_reason="blended_series_unavailable",
            alpha_pct=None,
            large_weight_pct=large_weight_pct,
            mid_weight_pct=mid_weight_pct,
            small_weight_pct=small_weight_pct,
            blended_series_start_date=None,
            note=_EQUITY_BLEND_NOTE,
        )

    value_source = _synthetic_value_source(blended.values)
    fund_cash_flows = []
    benchmark_cash_flows: list[CashFlow] = []
    any_unavailable = False
    for folio, key in equity_positions:
        txns = txns_by_position.get((folio, key), [])
        holding = open_holdings.get((folio, key))
        pos = build_position_cash_flows(session, txns, holding)
        fund_cash_flows.extend(pos.cash_flows)

        flows, reason = simulate_benchmark_cash_flows(txns, holding, value_source)
        if reason is not None:
            any_unavailable = True
            continue
        benchmark_cash_flows.extend(flows)

    fund_result = compute_xirr(fund_cash_flows)

    benchmark_xirr: float | None
    benchmark_xirr_reason: str | None
    if any_unavailable and not benchmark_cash_flows:
        benchmark_xirr = None
        benchmark_xirr_reason = "benchmark_history_unavailable"
    else:
        benchmark_result = compute_xirr(benchmark_cash_flows)
        benchmark_xirr = benchmark_result.rate
        benchmark_xirr_reason = benchmark_result.reason

    alpha_pct = (
        (fund_result.rate - benchmark_xirr) * 100
        if fund_result.rate is not None and benchmark_xirr is not None
        else None
    )

    return PortfolioEquityBlendedComparison(
        xirr=fund_result.rate,
        xirr_reason=fund_result.reason,
        benchmark_xirr=benchmark_xirr,
        benchmark_xirr_reason=benchmark_xirr_reason,
        alpha_pct=alpha_pct,
        large_weight_pct=large_weight_pct,
        mid_weight_pct=mid_weight_pct,
        small_weight_pct=small_weight_pct,
        blended_series_start_date=blended.start_date,
        note=_EQUITY_BLEND_NOTE,
    )
