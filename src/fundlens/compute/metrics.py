"""Return metrics per (folio, scheme) and portfolio-wide (PRD FR-3.1/3.2):
XIRR, CAGR, absolute return, invested vs current value, realized vs
unrealized P&L.

Cash-flow classification (FR-3.1: "purchases negative, redemptions/dividend
payouts positive, current value as terminal flow") needs a per-transaction-
type decision beyond a blanket sign rule, because not every unit-moving
transaction is a *cash* flow:

- Outflow from investor's pocket: PURCHASE, PURCHASE_SIP, SWITCH_IN,
  SWITCH_IN_MERGER.
- Inflow back to investor: REDEMPTION, SWITCH_OUT, SWITCH_OUT_MERGER,
  DIVIDEND_PAYOUT, REVERSAL (real-data-verified in compute/lots.py to carry
  a real amount/nav despite the name).
- Excluded — no net *external* cash movement: DIVIDEND_REINVEST (dividend
  income reinvested internally, not fresh cash — including it as an outflow
  would overstate invested capital), SEGREGATION (structural, carries no
  amount at all — real-data-verified in compute/lots.py), tax lines, MISC,
  UNKNOWN. GIFT_IN/GIFT_OUT are also excluded here as a conservative,
  *unverified* choice — no real data exercised this path; revisit if it
  turns out they carry a genuine amount.

**`amount_paise` sign — corrected against real data.** `compute/lots.py`
verified that `units` is signed (positive=inflow, negative=outflow); this
module initially assumed `amount_paise` was an unsigned magnitude, using it
as-is for inflow-classified types. Real CAS data proved that wrong:
`amount_paise` is *also* signed to match `units` (negative for
REDEMPTION/SWITCH_OUT/REVERSAL, positive for the PURCHASE family) — a
convention never explicitly checked before, since `compute/lots.py` prices
lots from `nav`, not `amount`, so this never surfaced there. Left unguarded,
every redemption/switch-out became a *negative* cash flow instead of
positive, which on a real 56-position portfolio meant only 13/56 positions
(the still-held ones, which never touch an inflow-classified transaction)
produced a valid XIRR — every fully-exited fund (43/56) silently failed as
"insufficient_flow_variation" despite the FIFO ledger (nav-based) closing
them out correctly. Fixed by normalizing to `abs(amount_paise)` before
applying the sign our own classification dictates, rather than trusting the
stored sign to already mean what we need. Re-verified against the same real
portfolio: 54/56 positions now produce a valid XIRR; the remaining 2 have
legitimate reasons (one all-same-date flows, one a SEGREGATION-originated
position with no purchase leg at all, so no investment cash flow exists to
compute a return against).

Portfolio-wide XIRR (FR-3.1: fund/folio/category/goal/platform/portfolio
levels) is just every position's classified cash flows concatenated: a
SWITCH_OUT from fund A and its matching SWITCH_IN to fund B are equal and
opposite on (about) the same date, so they net to ~zero automatically once
aggregated — no special inter-fund netting logic needed.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.compute.holdings import HoldingView, get_current_holdings
from fundlens.compute.xirr import CashFlow, compute_xirr
from fundlens.db.models import (
    GrowwFundMetadata,
    NavHistory,
    RawTransaction,
    RealizedGain,
    TransactionType,
)
from fundlens.money import to_paise_strict
from fundlens.scheme_identity import scheme_key

CASH_OUTFLOW_TYPES = {
    TransactionType.PURCHASE,
    TransactionType.PURCHASE_SIP,
    TransactionType.SWITCH_IN,
    TransactionType.SWITCH_IN_MERGER,
}
CASH_INFLOW_TYPES = {
    TransactionType.REDEMPTION,
    TransactionType.SWITCH_OUT,
    TransactionType.SWITCH_OUT_MERGER,
    TransactionType.DIVIDEND_PAYOUT,
    TransactionType.REVERSAL,
}


@dataclass(frozen=True)
class HoldingMetrics:
    folio: str
    scheme_key: str
    scheme_name: str
    amc: str
    invested_paise: int
    received_paise: int  # gross proceeds already redeemed, excluding current value
    current_value_paise: int | None  # None only if still held and NAV unavailable
    nav_as_of: date | None
    cost_basis_paise: int  # cost basis of currently-open lots (0 if fully exited)
    realized_gain_paise: int  # from the FIFO lot ledger — gain, not gross proceeds
    unrealized_gain_paise: int | None
    absolute_return_pct: float | None
    cagr_pct: float | None
    xirr: float | None
    xirr_reason: str | None
    is_fully_exited: bool
    less_than_one_year: bool


@dataclass(frozen=True)
class PortfolioMetrics:
    xirr: float | None
    xirr_reason: str | None
    invested_paise: int
    received_paise: int
    current_value_paise: int
    positions_missing_nav: int  # portfolio total understates value if > 0


@dataclass(frozen=True)
class PositionCashFlows:
    cash_flows: list[CashFlow]
    invested_paise: int
    received_paise: int
    current_value_paise: int | None
    nav_as_of: date | None
    xirr_reason: str | None  # set only when a still-held position lacks NAV


def get_all_positions(session: Session) -> set[tuple[str, str]]:
    """Every (folio, scheme_key) ever held, including fully-exited ones —
    get_current_holdings() only covers still-open positions, but a fully
    exited fund still has a "realized XIRR" (PRD FR-3.1 named edge case)."""
    txns = session.scalars(select(RawTransaction)).all()
    return {(t.folio, scheme_key(t.isin, t.amfi_code, t.scheme_name)) for t in txns}


def latest_nav(session: Session, amfi_code: str) -> tuple[Decimal, date] | None:
    row = session.execute(
        select(NavHistory.nav, NavHistory.nav_date)
        .where(NavHistory.amfi_code == amfi_code)
        .order_by(NavHistory.nav_date.desc())
        .limit(1)
    ).first()
    return (row.nav, row.nav_date) if row is not None else None


def _realized_gain_paise(session: Session, folio: str, key: str) -> int:
    total = session.execute(
        select(RealizedGain.realized_gain_paise).where(
            RealizedGain.folio == folio, RealizedGain.scheme_key == key
        )
    ).scalars()
    return sum(total, 0)


def build_position_cash_flows(
    session: Session,
    transactions: list[RawTransaction],
    open_holding: HoldingView | None,
) -> PositionCashFlows:
    cash_flows: list[CashFlow] = []
    invested_paise = 0
    received_paise = 0
    for txn in transactions:
        if txn.amount_paise is None:
            continue
        # amount_paise is signed to match units (real-data-verified: negative
        # for REDEMPTION/SWITCH_OUT/REVERSAL, positive for PURCHASE-family) —
        # normalize to a magnitude first, then apply the sign our own
        # classification dictates, rather than trusting the stored sign to
        # already mean "inflow"/"outflow" the way we need it to.
        magnitude = abs(txn.amount_paise)
        if txn.txn_type in CASH_OUTFLOW_TYPES:
            cash_flows.append(CashFlow(date=txn.txn_date, amount=Decimal(-magnitude) / 100))
            invested_paise += magnitude
        elif txn.txn_type in CASH_INFLOW_TYPES:
            cash_flows.append(CashFlow(date=txn.txn_date, amount=Decimal(magnitude) / 100))
            received_paise += magnitude

    units_remaining = open_holding.units_remaining if open_holding else Decimal("0")
    is_fully_exited = units_remaining == 0

    current_value_paise: int | None
    nav_as_of: date | None = None
    xirr_reason: str | None = None

    if is_fully_exited:
        current_value_paise = 0
    else:
        amfi_code = open_holding.amfi_code if open_holding else None
        latest = latest_nav(session, amfi_code) if amfi_code else None
        if latest is not None:
            nav, nav_as_of = latest
            current_value_paise = to_paise_strict(units_remaining * nav)
            cash_flows.append(CashFlow(date=nav_as_of, amount=Decimal(current_value_paise) / 100))
        else:
            current_value_paise = None
            xirr_reason = "nav_unavailable"

    return PositionCashFlows(
        cash_flows=cash_flows,
        invested_paise=invested_paise,
        received_paise=received_paise,
        current_value_paise=current_value_paise,
        nav_as_of=nav_as_of,
        xirr_reason=xirr_reason,
    )


def positions_with_transactions(
    session: Session, positions: set[tuple[str, str]]
) -> dict[tuple[str, str], list[RawTransaction]]:
    folios = {folio for folio, _ in positions}
    all_txns = session.scalars(
        select(RawTransaction).where(RawTransaction.folio.in_(folios))
    ).all()
    by_position: dict[tuple[str, str], list[RawTransaction]] = {}
    for txn in all_txns:
        key = scheme_key(txn.isin, txn.amfi_code, txn.scheme_name)
        by_position.setdefault((txn.folio, key), []).append(txn)
    return by_position


def compute_holding_metrics(
    session: Session,
    folio: str,
    key: str,
    transactions: list[RawTransaction],
    open_holding: HoldingView | None,
) -> HoldingMetrics:
    scheme_name = transactions[0].scheme_name if transactions else key
    amc = transactions[0].amc if transactions else ""

    pos = build_position_cash_flows(session, transactions, open_holding)

    if pos.xirr_reason is None:
        xirr_result = compute_xirr(pos.cash_flows)
        xirr = xirr_result.rate
        xirr_reason = xirr_result.reason
    else:
        xirr = None
        xirr_reason = pos.xirr_reason

    cost_basis_paise = open_holding.cost_basis_paise if open_holding else 0
    is_fully_exited = (open_holding.units_remaining if open_holding else Decimal("0")) == 0
    realized_gain_paise = _realized_gain_paise(session, folio, key)
    unrealized_gain_paise = (
        pos.current_value_paise - cost_basis_paise if pos.current_value_paise is not None else None
    )

    total_value_paise = pos.received_paise + (pos.current_value_paise or 0)
    if pos.current_value_paise is None:
        # Still held, NAV unavailable — total_value_paise silently omits the
        # current value, so a return computed from it would misrepresent
        # "unknown" as "worthless" (e.g. read as -100%). None, not a number.
        absolute_return_pct = None
    elif pos.invested_paise > 0:
        absolute_return_pct = (total_value_paise - pos.invested_paise) / pos.invested_paise * 100
    else:
        absolute_return_pct = None

    outflow_dates = [t.txn_date for t in transactions if t.txn_type in CASH_OUTFLOW_TYPES]
    first_investment_date = min(outflow_dates) if outflow_dates else None
    less_than_one_year = bool(
        first_investment_date and (date.today() - first_investment_date).days < 365
    )

    cagr_pct: float | None = None
    if first_investment_date and pos.invested_paise > 0 and pos.current_value_paise is not None:
        as_of = pos.nav_as_of if pos.nav_as_of else date.today()
        days = (as_of - first_investment_date).days
        if days > 0 and total_value_paise > 0:
            years = days / 365.0
            cagr_pct = ((total_value_paise / pos.invested_paise) ** (1 / years) - 1) * 100

    return HoldingMetrics(
        folio=folio,
        scheme_key=key,
        scheme_name=scheme_name,
        amc=amc,
        invested_paise=pos.invested_paise,
        received_paise=pos.received_paise,
        current_value_paise=pos.current_value_paise,
        nav_as_of=pos.nav_as_of,
        cost_basis_paise=cost_basis_paise,
        realized_gain_paise=realized_gain_paise,
        unrealized_gain_paise=unrealized_gain_paise,
        absolute_return_pct=absolute_return_pct,
        cagr_pct=cagr_pct,
        xirr=xirr,
        xirr_reason=xirr_reason,
        is_fully_exited=is_fully_exited,
        less_than_one_year=less_than_one_year,
    )


def get_all_holding_metrics(session: Session) -> list[HoldingMetrics]:
    positions = get_all_positions(session)
    if not positions:
        return []

    open_holdings = {(h.folio, h.scheme_key): h for h in get_current_holdings(session)}
    txns_by_position = positions_with_transactions(session, positions)

    return [
        compute_holding_metrics(
            session,
            folio=folio,
            key=key,
            transactions=txns_by_position.get((folio, key), []),
            open_holding=open_holdings.get((folio, key)),
        )
        for folio, key in positions
    ]


def compute_portfolio_metrics(session: Session) -> PortfolioMetrics:
    positions = get_all_positions(session)
    if not positions:
        return PortfolioMetrics(
            xirr=None,
            xirr_reason="insufficient_flow_variation",
            invested_paise=0,
            received_paise=0,
            current_value_paise=0,
            positions_missing_nav=0,
        )

    open_holdings = {(h.folio, h.scheme_key): h for h in get_current_holdings(session)}
    txns_by_position = positions_with_transactions(session, positions)

    all_cash_flows: list[CashFlow] = []
    invested_total = 0
    received_total = 0
    current_value_total = 0
    missing_nav_count = 0

    for folio, key in positions:
        pos = build_position_cash_flows(
            session, txns_by_position.get((folio, key), []), open_holdings.get((folio, key))
        )
        all_cash_flows.extend(pos.cash_flows)
        invested_total += pos.invested_paise
        received_total += pos.received_paise
        if pos.current_value_paise is not None:
            current_value_total += pos.current_value_paise
        if pos.xirr_reason == "nav_unavailable":
            missing_nav_count += 1

    xirr_result = compute_xirr(all_cash_flows)

    return PortfolioMetrics(
        xirr=xirr_result.rate,
        xirr_reason=xirr_result.reason,
        invested_paise=invested_total,
        received_paise=received_total,
        current_value_paise=current_value_total,
        positions_missing_nav=missing_nav_count,
    )


def compute_fund_metrics(session: Session, amfi_code: str) -> PortfolioMetrics | None:
    """Aggregates every (folio, scheme_key) position for one fund across
    every folio that holds or has held it — the fund detail page's
    "your position" summary. Same concatenate-cash-flows-then-
    compute_xirr-once pattern as compute_portfolio_metrics above, just
    filtered to one fund instead of the whole portfolio (not new math).

    Returns None if the user has never held this fund, so the caller can
    render "you don't hold this fund" instead of an all-zero metrics
    block — a fund reached via the "similar funds" list on someone else's
    detail page is the expected case this covers.
    """
    all_positions = get_all_positions(session)
    open_holdings = {(h.folio, h.scheme_key): h for h in get_current_holdings(session)}
    txns_by_position = positions_with_transactions(session, all_positions)

    fund_positions: set[tuple[str, str]] = set()
    for folio, key in all_positions:
        holding = open_holdings.get((folio, key))
        if holding is not None:
            if holding.amfi_code == amfi_code:
                fund_positions.add((folio, key))
            continue
        # Fully-exited position: no open holding to read amfi_code from,
        # fall back to its own transactions (mirrors get_all_positions'
        # rationale for including exited positions at all).
        if any(t.amfi_code == amfi_code for t in txns_by_position.get((folio, key), [])):
            fund_positions.add((folio, key))

    if not fund_positions:
        return None

    all_cash_flows: list[CashFlow] = []
    invested_total = 0
    received_total = 0
    current_value_total = 0
    missing_nav_count = 0

    for folio, key in fund_positions:
        pos = build_position_cash_flows(
            session, txns_by_position.get((folio, key), []), open_holdings.get((folio, key))
        )
        all_cash_flows.extend(pos.cash_flows)
        invested_total += pos.invested_paise
        received_total += pos.received_paise
        if pos.current_value_paise is not None:
            current_value_total += pos.current_value_paise
        if pos.xirr_reason == "nav_unavailable":
            missing_nav_count += 1

    xirr_result = compute_xirr(all_cash_flows)

    return PortfolioMetrics(
        xirr=xirr_result.rate,
        xirr_reason=xirr_result.reason,
        invested_paise=invested_total,
        received_paise=received_total,
        current_value_paise=current_value_total,
        positions_missing_nav=missing_nav_count,
    )


@dataclass(frozen=True)
class CategoryGroupMetrics:
    label: str
    current_value_paise: int
    allocation_pct: float
    xirr: float | None
    xirr_reason: str | None
    fund_count: int


def compute_category_breakdown(
    session: Session, level: Literal["category", "sub_category"]
) -> list[CategoryGroupMetrics]:
    """Groups currently-open positions by Groww's fund category or
    sub-category (market_data/groww_service.py's groww_fund_metadata),
    blending each group's cash flows into one XIRR the same way
    compute_portfolio_metrics/compute_fund_metrics do (concatenate, call
    compute_xirr once) -- a new grouping key over the same pattern, not
    new math.

    Scoped to currently-open positions only (get_current_holdings()),
    deliberately excluding fully-exited ones -- same reasoning as the
    Funds-page consistency fix: allocation % is inherently a snapshot of
    what's held *now*, so blending in an exited position's historical
    cash flows would conflate "how is my current Equity slice doing"
    with "how did that Equity fund I already sold do".

    A position whose scheme has no amfi_code, or an amfi_code Groww's
    screener doesn't cover, lands in "Uncategorized" -- real-data-verified
    coverage gap (data-model.md's groww_fund_metadata section), never
    dropped silently.
    """
    holdings = get_current_holdings(session)
    if not holdings:
        return []

    amfi_codes = {h.amfi_code for h in holdings if h.amfi_code}
    metadata_by_code = {
        m.amfi_code: m
        for m in session.scalars(
            select(GrowwFundMetadata).where(GrowwFundMetadata.amfi_code.in_(amfi_codes))
        )
    }

    positions = {(h.folio, h.scheme_key) for h in holdings}
    open_holdings = {(h.folio, h.scheme_key): h for h in holdings}
    txns_by_position = positions_with_transactions(session, positions)

    members_by_label: dict[str, list[tuple[str, str]]] = {}
    for h in holdings:
        metadata = metadata_by_code.get(h.amfi_code) if h.amfi_code else None
        label = getattr(metadata, level) if metadata is not None else None
        members_by_label.setdefault(label or "Uncategorized", []).append((h.folio, h.scheme_key))

    value_by_label: dict[str, int] = {}
    xirr_by_label: dict[str, tuple[float | None, str | None]] = {}
    for label, members in members_by_label.items():
        cash_flows: list[CashFlow] = []
        value_paise = 0
        for folio, key in members:
            pos = build_position_cash_flows(
                session, txns_by_position.get((folio, key), []), open_holdings.get((folio, key))
            )
            cash_flows.extend(pos.cash_flows)
            if pos.current_value_paise is not None:
                value_paise += pos.current_value_paise
        xirr_result = compute_xirr(cash_flows)
        value_by_label[label] = value_paise
        xirr_by_label[label] = (xirr_result.rate, xirr_result.reason)

    total_value = sum(value_by_label.values())

    results = [
        CategoryGroupMetrics(
            label=label,
            current_value_paise=value_by_label[label],
            allocation_pct=(
                value_by_label[label] / total_value * 100 if total_value > 0 else 0.0
            ),
            xirr=xirr_by_label[label][0],
            xirr_reason=xirr_by_label[label][1],
            fund_count=len({key for _, key in members}),
        )
        for label, members in members_by_label.items()
    ]

    return sorted(results, key=lambda r: r.current_value_paise, reverse=True)
