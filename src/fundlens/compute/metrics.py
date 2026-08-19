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

from collections.abc import Callable
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
    RawCASUpload,
    RawTransaction,
    RealizedGain,
    SchemeMetadata,
    TransactionType,
)
from fundlens.money import to_paise_strict
from fundlens.scheme_identity import scheme_key
from fundlens.tax.asset_class import parse_category_raw

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


# How a position's transactions got into the app. Derived from the
# originating upload's parser_version rather than guessed from the folio
# string, which has no stable format across sources.
SOURCE_DIRECT = "Direct / AMC folio"
SOURCE_DEZERV = "Dezerv"
SOURCE_ZERODHA = "Zerodha"
SOURCE_CSV = "Manual CSV"
SOURCE_UNKNOWN = "Unknown"
SOURCE_MIXED = "Mixed"

_PARSER_VERSION_SOURCES = {
    "dezerv-csv-v1": SOURCE_DEZERV,
    "zerodha-tradebook-csv-v1": SOURCE_ZERODHA,
    "csv-import-v1": SOURCE_CSV,
}

# Platform imports; everything else (a CAS parsed by casparser, whose
# parser_version is casparser's own version string) is an AMC folio.
BROKER_SOURCES = frozenset({SOURCE_DEZERV, SOURCE_ZERODHA})


def _source_for_parser_version(parser_version: str) -> str:
    """A CAS is parsed by casparser, whose parser_version is its own release
    number (e.g. "1.3.0") -- there's no fixed value to match, so anything not
    a known importer is treated as CAS-sourced.

    Note this attributes a position by *where its data came from*, which is
    not the same as where the fund was bought: a CAS comes from the RTAs
    (CAMS/KFintech) and therefore lists holdings bought through platforms too.
    Real data shows 4 funds held under both an AMC folio and a platform folio.
    So SOURCE_DIRECT means "held under an AMC folio", not "never bought via a
    broker" -- the UI says so rather than overclaiming.
    """
    return _PARSER_VERSION_SOURCES.get(parser_version, SOURCE_DIRECT)


def position_sources(session: Session) -> dict[tuple[str, str], str]:
    """Maps each (folio, scheme_key) to the source that supplied its
    transactions. Real-data-verified that no position mixes sources (folios
    differ per platform), but a position that somehow did is reported as
    SOURCE_MIXED rather than silently attributed to whichever row won."""
    rows = session.execute(
        select(
            RawTransaction.folio,
            RawTransaction.isin,
            RawTransaction.amfi_code,
            RawTransaction.scheme_name,
            RawCASUpload.parser_version,
        ).join(RawCASUpload, RawCASUpload.id == RawTransaction.upload_id)
    ).all()

    by_position: dict[tuple[str, str], set[str]] = {}
    for folio, isin, amfi_code, scheme_name, parser_version in rows:
        key = (folio, scheme_key(isin, amfi_code, scheme_name))
        by_position.setdefault(key, set()).add(_source_for_parser_version(parser_version))

    return {
        key: (sources.pop() if len(sources) == 1 else SOURCE_MIXED)
        for key, sources in by_position.items()
    }


@dataclass(frozen=True)
class HoldingMetrics:
    folio: str
    scheme_key: str
    scheme_name: str
    amc: str
    source: str  # where this position's transactions were imported from
    category_broad: str
    category_specific: str
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
    category_broad: str = "Uncategorized",
    category_specific: str = "Uncategorized",
    source: str = SOURCE_UNKNOWN,
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
        source=source,
        category_broad=category_broad,
        category_specific=category_specific,
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

    current_holdings = get_current_holdings(session)
    open_holdings = {(h.folio, h.scheme_key): h for h in current_holdings}
    txns_by_position = positions_with_transactions(session, positions)
    # Fully-exited positions have no open_holding to read an amfi_code from
    # (same limitation compute_category_breakdown documents), so they fall
    # through to the "Uncategorized" default below rather than being looked
    # up here.
    category_labels = fund_category_labels(session, current_holdings)
    sources = position_sources(session)

    return [
        compute_holding_metrics(
            session,
            folio=folio,
            key=key,
            transactions=txns_by_position.get((folio, key), []),
            open_holding=open_holdings.get((folio, key)),
            category_broad=category_labels.get((folio, key), ("Uncategorized", "Uncategorized"))[
                0
            ],
            category_specific=category_labels.get(
                (folio, key), ("Uncategorized", "Uncategorized")
            )[1],
            source=sources.get((folio, key), SOURCE_UNKNOWN),
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


def _compute_grouped_breakdown(
    session: Session,
    holdings: list[HoldingView],
    label_for: Callable[[HoldingView], str],
) -> list[CategoryGroupMetrics]:
    """Shared shape behind compute_category_breakdown and
    compute_amc_breakdown: bucket currently-open positions by whatever
    label_for extracts, blend each bucket's cash flows into one XIRR the
    same way compute_portfolio_metrics/compute_fund_metrics do (concatenate,
    call compute_xirr once), and report each bucket's current value as a
    percentage of the whole. The grouping key is the only thing that
    differs between callers -- not new math."""
    positions = {(h.folio, h.scheme_key) for h in holdings}
    open_holdings = {(h.folio, h.scheme_key): h for h in holdings}
    txns_by_position = positions_with_transactions(session, positions)

    members_by_label: dict[str, list[tuple[str, str]]] = {}
    for h in holdings:
        members_by_label.setdefault(label_for(h), []).append((h.folio, h.scheme_key))

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


def _category_lookup_maps(
    session: Session, amfi_codes: set[str]
) -> tuple[dict[str, GrowwFundMetadata], dict[str, SchemeMetadata]]:
    groww_by_code = {
        m.amfi_code: m
        for m in session.scalars(
            select(GrowwFundMetadata).where(GrowwFundMetadata.amfi_code.in_(amfi_codes))
        )
    }
    amfi_by_code = {
        m.amfi_code: m
        for m in session.scalars(
            select(SchemeMetadata).where(SchemeMetadata.amfi_code.in_(amfi_codes))
        )
    }
    return groww_by_code, amfi_by_code


def _resolve_category_label(
    amfi_code: str | None,
    groww_by_code: dict[str, GrowwFundMetadata],
    amfi_by_code: dict[str, SchemeMetadata],
    level: Literal["category", "sub_category"],
) -> str:
    if amfi_code is None:
        return "Uncategorized"

    groww = groww_by_code.get(amfi_code)
    groww_label = getattr(groww, level) if groww is not None else None
    if groww_label:
        return groww_label

    amfi_meta = amfi_by_code.get(amfi_code)
    if amfi_meta is not None:
        broad, specific = parse_category_raw(amfi_meta.category_raw)
        return broad if level == "category" else specific

    return "Uncategorized"


def fund_category_labels(
    session: Session, holdings: list[HoldingView]
) -> dict[tuple[str, str], tuple[str, str]]:
    """Per-fund (category_broad, category_specific) labels for currently-open
    holdings, keyed by (folio, scheme_key) -- same Groww-then-AMFI
    resolution as compute_category_breakdown's label_for (via the shared
    helpers above), reused here so a fund's row on the Funds page always
    carries the same label a breakdown chart's drill-down link points at."""
    amfi_codes = {h.amfi_code for h in holdings if h.amfi_code}
    groww_by_code, amfi_by_code = _category_lookup_maps(session, amfi_codes)
    return {
        (h.folio, h.scheme_key): (
            _resolve_category_label(h.amfi_code, groww_by_code, amfi_by_code, "category"),
            _resolve_category_label(h.amfi_code, groww_by_code, amfi_by_code, "sub_category"),
        )
        for h in holdings
    }


def compute_category_breakdown(
    session: Session, level: Literal["category", "sub_category"]
) -> list[CategoryGroupMetrics]:
    """Groups currently-open positions by fund category or sub-category,
    preferring Groww's listing (market_data/groww_service.py's
    groww_fund_metadata) and falling back to AMFI's own category feed
    (scheme_metadata.category_raw, parsed via
    tax/asset_class.py::parse_category_raw) when Groww has no row for a
    scheme.

    Scoped to currently-open positions only (get_current_holdings()),
    deliberately excluding fully-exited ones -- same reasoning as the
    Funds-page consistency fix: allocation % is inherently a snapshot of
    what's held *now*, so blending in an exited position's historical
    cash flows would conflate "how is my current Equity slice doing"
    with "how did that Equity fund I already sold do".

    **Why the fallback**: Groww's screener is direct-plan-only, so any
    Regular-plan holding has no groww_fund_metadata row regardless of how
    well-known the fund is -- real-data-verified on a real portfolio where
    27 of 35 held funds (all Regular plan) had zero Groww coverage despite
    every one of them having an AMFI scheme_metadata row (AMFI's category
    feed covers every scheme, not just Groww-listed ones). Falling back to
    it turns a mostly-"Uncategorized" chart into a fully-labeled one using
    data this app already ingests for every scheme, not a new source.

    A position whose scheme has no amfi_code, or an amfi_code covered by
    neither source, lands in "Uncategorized" -- never dropped silently.
    """
    holdings = get_current_holdings(session)
    if not holdings:
        return []

    amfi_codes = {h.amfi_code for h in holdings if h.amfi_code}
    groww_by_code, amfi_by_code = _category_lookup_maps(session, amfi_codes)

    def label_for(h: HoldingView) -> str:
        return _resolve_category_label(h.amfi_code, groww_by_code, amfi_by_code, level)

    return _compute_grouped_breakdown(session, holdings, label_for)


def compute_amc_breakdown(session: Session) -> list[CategoryGroupMetrics]:
    """Same shape as compute_category_breakdown, grouped by AMC (fund
    house) instead of Groww category -- every holding always has an amc
    (RawTransaction.amc is non-nullable), so no "Uncategorized" bucket is
    needed here."""
    holdings = get_current_holdings(session)
    if not holdings:
        return []

    return _compute_grouped_breakdown(session, holdings, label_for=lambda h: h.amc)
