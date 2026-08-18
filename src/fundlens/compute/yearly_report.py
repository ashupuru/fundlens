"""Yearly Report (PRD FR-10.1, phase P2.5, compute-engine.md §11): FY-aligned
(Apr-Mar) assembly of opening/closing value, XIRR (this year + since
inception), benchmark alpha, best/worst funds, allocation drift, estimated
fees, and realized gains -- built almost entirely from existing engines.

The "this year's XIRR" and per-fund best/worst figures both need a fund's
value at an arbitrary past date, which none of compute_portfolio_metrics /
compute_portfolio_benchmark_comparison support (all "as of today" only).
Rather than modifying those, this module uses a synthetic-boundary-flow
technique: treat the opening value as an outflow at the period start and
the closing value as an inflow at the period end, alongside the position's
real transactions in between, and feed the combined list straight into the
existing compute_xirr solver -- the same engine every other XIRR figure in
this app uses, just with two extra synthetic flows.

Benchmark comparison is deliberately scoped to "since inception, as of
report_end," not a true FY-only sub-period simulation -- see
_since_inception_benchmark_xirr's docstring for why.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.allocation.service import get_risk_profile
from fundlens.compute.allocation import AllocationDrift, compute_allocation_drift
from fundlens.compute.benchmark import (
    BenchmarkId,
    real_value_source,
    simulate_benchmark_cash_flows,
)
from fundlens.compute.capital_gains import (
    CapitalGainsReport,
    financial_year_bounds,
    get_capital_gains_report,
)
from fundlens.compute.holdings import get_current_holdings
from fundlens.compute.metrics import (
    build_position_cash_flows,
    get_all_positions,
    latest_nav,
    positions_with_transactions,
)
from fundlens.compute.valuation_history import compute_portfolio_value_at_dates
from fundlens.compute.xirr import CashFlow, XirrResult, compute_xirr
from fundlens.db.models import GrowwFundMetadata, NavHistory, RawTransaction
from fundlens.money import to_paise_strict

_DEFAULT_BENCHMARK = BenchmarkId.NIFTY50_TRI
_BEST_WORST_COUNT = 3


@dataclass(frozen=True)
class FundPerformance:
    folio: str
    scheme_key: str
    scheme_name: str
    amc: str
    xirr: float | None
    xirr_reason: str | None


@dataclass(frozen=True)
class YearlyReport:
    financial_year: str
    fy_start: date
    report_end: date  # min(fy_end, today) -- may be mid-year for the current FY
    opening_value_paise: int
    closing_value_paise: int
    portfolio_xirr_this_year: float | None
    portfolio_xirr_this_year_reason: str | None
    portfolio_xirr_since_inception: float | None
    portfolio_xirr_since_inception_reason: str | None
    benchmark_id: BenchmarkId
    benchmark_xirr_since_inception: float | None
    benchmark_xirr_since_inception_reason: str | None
    alpha_pct_since_inception: float | None
    best_funds: list[FundPerformance]
    worst_funds: list[FundPerformance]
    allocation_drift: AllocationDrift | None
    estimated_annual_fee_paise: int
    fee_funds_excluded: int
    capital_gains: CapitalGainsReport


def _sub_period_xirr(
    session: Session,
    transactions: list[RawTransaction],
    opening_value_paise: int,
    closing_value_paise: int,
    period_start: date,
    period_end: date,
) -> XirrResult:
    """Synthetic-boundary-flow sub-period XIRR: `open_holding=None` makes
    build_position_cash_flows treat the position as fully exited, so it
    contributes zero cash flows of its own (no terminal today-priced flow)
    -- only the real transaction flows within (period_start, period_end]
    plus the two synthetic boundary flows added here."""
    filtered = [t for t in transactions if period_start < t.txn_date <= period_end]
    real_flows = build_position_cash_flows(session, filtered, open_holding=None).cash_flows

    flows = [
        CashFlow(date=period_start, amount=Decimal(-opening_value_paise) / 100),
        *real_flows,
        CashFlow(date=period_end, amount=Decimal(closing_value_paise) / 100),
    ]
    return compute_xirr(flows)


def _since_inception_xirr(
    session: Session,
    transactions: list[RawTransaction],
    closing_value_paise: int,
    report_end: date,
) -> XirrResult:
    filtered = [t for t in transactions if t.txn_date <= report_end]
    real_flows = build_position_cash_flows(session, filtered, open_holding=None).cash_flows
    flows = [*real_flows, CashFlow(date=report_end, amount=Decimal(closing_value_paise) / 100)]
    return compute_xirr(flows)


def _since_inception_benchmark_xirr(
    session: Session,
    txns_by_position: dict[tuple[str, str], list[RawTransaction]],
    report_end: date,
    benchmark_id: BenchmarkId,
) -> XirrResult:
    """Deliberately "since inception, as of report_end," not a true FY-only
    sub-period simulation: simulate_benchmark_cash_flows must replay a
    position's FULL history to correctly track partial-redemption fractions
    (see its own docstring) -- floor-filtering to fy_start would silently
    miscompute that fraction for any position that predates the FY. Bounded
    only at the end (`txn_date <= report_end`), giving a well-defined
    "alpha since I started investing, as of this FY's end" figure instead.
    """
    value_source = real_value_source(session, benchmark_id)
    all_flows: list[CashFlow] = []
    any_available = False
    for txns in txns_by_position.values():
        filtered = [t for t in txns if t.txn_date <= report_end]
        flows, reason = simulate_benchmark_cash_flows(filtered, None, value_source)
        if reason is None:
            any_available = True
            all_flows.extend(flows)
    if not any_available:
        return XirrResult(rate=None, reason="benchmark_history_unavailable")
    return compute_xirr(all_flows)


def _position_value_at_date(
    session: Session, transactions: list[RawTransaction], as_of_date: date
) -> int | None:
    """Value of a SINGLE position's units-held-as-of-date, priced at the
    NAV on or before that date -- the per-fund analog of
    compute_portfolio_value_at_dates, needed for best/worst ranking (a
    portfolio-wide call would price every position, wasteful for a
    single-position lookup)."""
    amfi_code = next((t.amfi_code for t in transactions if t.amfi_code), None)
    if amfi_code is None:
        return None

    units_held = Decimal("0")
    for txn in sorted(transactions, key=lambda t: (t.txn_date, t.sequence)):
        if txn.txn_date > as_of_date:
            break
        units_held += txn.units or Decimal("0")

    if units_held <= 0:
        return 0

    nav = session.execute(
        select(NavHistory.nav)
        .where(NavHistory.amfi_code == amfi_code, NavHistory.nav_date <= as_of_date)
        .order_by(NavHistory.nav_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    if nav is None:
        return None
    return to_paise_strict(units_held * nav)


def _fund_performance_for_period(
    session: Session,
    txns_by_position: dict[tuple[str, str], list[RawTransaction]],
    period_start: date,
    period_end: date,
) -> list[FundPerformance]:
    results: list[FundPerformance] = []
    for (folio, key), txns in txns_by_position.items():
        if not any(t.txn_date <= period_end for t in txns):
            continue  # nothing to report: position didn't exist yet by period_end

        opening = _position_value_at_date(session, txns, period_start)
        closing = _position_value_at_date(session, txns, period_end)
        xirr: float | None
        reason: str | None
        if opening is None or closing is None:
            xirr, reason = None, "nav_unavailable"
        else:
            result = _sub_period_xirr(session, txns, opening, closing, period_start, period_end)
            xirr, reason = result.rate, result.reason

        results.append(
            FundPerformance(
                folio=folio,
                scheme_key=key,
                scheme_name=txns[0].scheme_name if txns else key,
                amc=txns[0].amc if txns else "",
                xirr=xirr,
                xirr_reason=reason,
            )
        )
    return results


def _estimate_annual_fee(session: Session) -> tuple[int, int]:
    """sum(current_value_paise * expense_ratio_pct / 100) over currently
    open holdings with a known GrowwFundMetadata.expense_ratio -- same
    "flag, don't fabricate" shape as ai/snapshot.py's fee-drag calc (not
    shared code: small enough to duplicate for two different consumers)."""
    holdings = get_current_holdings(session)
    amfi_codes = {h.amfi_code for h in holdings if h.amfi_code}
    metadata_by_code = {
        m.amfi_code: m
        for m in session.scalars(
            select(GrowwFundMetadata).where(GrowwFundMetadata.amfi_code.in_(amfi_codes))
        )
    }

    fee_paise = Decimal(0)
    excluded = 0
    for h in holdings:
        metadata = metadata_by_code.get(h.amfi_code) if h.amfi_code else None
        nav_info = latest_nav(session, h.amfi_code) if h.amfi_code else None
        if metadata is None or metadata.expense_ratio is None or nav_info is None:
            excluded += 1
            continue
        nav, _as_of = nav_info
        value_paise = to_paise_strict(h.units_remaining * nav)
        fee_paise += Decimal(value_paise) * metadata.expense_ratio / 100

    fee_paise_rounded = int(fee_paise.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return fee_paise_rounded, excluded


def compute_yearly_report(session: Session, fy_label: str) -> YearlyReport:
    fy_start, fy_end = financial_year_bounds(fy_label)
    report_end = min(fy_end, date.today())

    value_points = compute_portfolio_value_at_dates(session, [fy_start, report_end])
    opening_value_paise = value_points[0].value_paise
    closing_value_paise = value_points[1].value_paise

    positions = get_all_positions(session)
    txns_by_position = positions_with_transactions(session, positions)
    all_transactions = [t for txns in txns_by_position.values() for t in txns]

    this_year = _sub_period_xirr(
        session, all_transactions, opening_value_paise, closing_value_paise, fy_start, report_end
    )
    since_inception = _since_inception_xirr(
        session, all_transactions, closing_value_paise, report_end
    )
    benchmark_result = _since_inception_benchmark_xirr(
        session, txns_by_position, report_end, _DEFAULT_BENCHMARK
    )
    alpha_pct = (
        (since_inception.rate - benchmark_result.rate) * 100
        if since_inception.rate is not None and benchmark_result.rate is not None
        else None
    )

    fund_performance = _fund_performance_for_period(session, txns_by_position, fy_start, report_end)
    ranked = sorted(
        (f for f in fund_performance if f.xirr is not None),
        key=lambda f: cast(float, f.xirr),
        reverse=True,
    )
    n = min(_BEST_WORST_COUNT, len(ranked) // 2)
    best_funds = ranked[:n]
    worst_funds = list(reversed(ranked[-n:])) if n > 0 else []

    profile = get_risk_profile(session)
    allocation_drift = compute_allocation_drift(session, profile) if profile is not None else None

    fee_paise, fee_excluded = _estimate_annual_fee(session)

    capital_gains = get_capital_gains_report(session, fy_label)

    return YearlyReport(
        financial_year=fy_label,
        fy_start=fy_start,
        report_end=report_end,
        opening_value_paise=opening_value_paise,
        closing_value_paise=closing_value_paise,
        portfolio_xirr_this_year=this_year.rate,
        portfolio_xirr_this_year_reason=this_year.reason,
        portfolio_xirr_since_inception=since_inception.rate,
        portfolio_xirr_since_inception_reason=since_inception.reason,
        benchmark_id=_DEFAULT_BENCHMARK,
        benchmark_xirr_since_inception=benchmark_result.rate,
        benchmark_xirr_since_inception_reason=benchmark_result.reason,
        alpha_pct_since_inception=alpha_pct,
        best_funds=best_funds,
        worst_funds=worst_funds,
        allocation_drift=allocation_drift,
        estimated_annual_fee_paise=fee_paise,
        fee_funds_excluded=fee_excluded,
        capital_gains=capital_gains,
    )
