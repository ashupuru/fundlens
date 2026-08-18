"""Historical portfolio valuation: portfolio value AND cumulative invested
capital as of PAST dates, not just "now" -- compute/allocation.py,
compute/metrics.py and compute/benchmark.py are all current-state or
benchmark-simulation only, so this is the first time-series module. Powers
the Portfolio Value vs Invested Amount and Drawdown charts (the latter
derived client-side from this module's value_paise series).

Weekly points from the portfolio's first transaction to today. Scoped to
every position ever held (compute/metrics.py::get_all_positions), not just
currently-open ones -- a valid historical series must still reflect what was
held at each past date, including positions since fully exited.
"""

import bisect
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fundlens.compute.metrics import (
    CASH_OUTFLOW_TYPES,
    get_all_positions,
    positions_with_transactions,
)
from fundlens.db.models import NavHistory, RawTransaction
from fundlens.money import to_paise_strict


@dataclass(frozen=True)
class PortfolioValuePoint:
    as_of_date: date
    value_paise: int
    invested_paise: int
    positions_missing_nav: int


def _first_transaction_date(session: Session) -> date | None:
    return session.execute(select(func.min(RawTransaction.txn_date))).scalar_one_or_none()


def _weekly_report_dates(first_date: date, today: date) -> list[date]:
    dates: list[date] = []
    d = first_date
    while d < today:
        dates.append(d)
        d += timedelta(days=7)
    dates.append(today)
    return dates


def _nav_series_by_scheme(
    session: Session, amfi_codes: set[str]
) -> dict[str, tuple[list[date], list[Decimal]]]:
    """Bulk-fetches each scheme's full NAV history once into a sorted,
    bisect-ready (dates, navs) pair -- same template as
    compute/benchmark.py::_synthetic_value_source, to avoid an
    as-of-lookup-per-report-date-per-position N+1."""
    if not amfi_codes:
        return {}

    rows = session.execute(
        select(NavHistory.amfi_code, NavHistory.nav_date, NavHistory.nav)
        .where(NavHistory.amfi_code.in_(amfi_codes))
        .order_by(NavHistory.amfi_code, NavHistory.nav_date)
    ).all()

    series: dict[str, tuple[list[date], list[Decimal]]] = {}
    for amfi_code, nav_date, nav in rows:
        dates, navs = series.setdefault(amfi_code, ([], []))
        dates.append(nav_date)
        navs.append(nav)
    return series


def _nav_on_or_before(
    series: tuple[list[date], list[Decimal]] | None, on_date: date
) -> Decimal | None:
    if series is None:
        return None
    dates, navs = series
    idx = bisect.bisect_right(dates, on_date) - 1
    return navs[idx] if idx >= 0 else None


def compute_portfolio_value_history(session: Session) -> list[PortfolioValuePoint]:
    first_date = _first_transaction_date(session)
    if first_date is None:
        return []

    report_dates = _weekly_report_dates(first_date, date.today())
    return compute_portfolio_value_at_dates(session, report_dates)


def compute_portfolio_value_at_dates(
    session: Session, report_dates: list[date]
) -> list[PortfolioValuePoint]:
    """The per-position NAV-bisection engine behind
    compute_portfolio_value_history, generalized to an arbitrary explicit
    list of report dates rather than an internally-generated weekly
    cadence -- reused as-is by compute/yearly_report.py to price the
    portfolio at exactly [fy_start, report_end], two points instead of a
    weekly series."""
    positions = get_all_positions(session)
    txns_by_position = positions_with_transactions(session, positions)

    amfi_codes = {
        amfi_code
        for txns in txns_by_position.values()
        if (amfi_code := next((t.amfi_code for t in txns if t.amfi_code), None)) is not None
    }
    nav_series = _nav_series_by_scheme(session, amfi_codes)

    n = len(report_dates)
    value_paise = [0] * n
    invested_paise = [0] * n
    missing_nav = [0] * n

    for txns in txns_by_position.values():
        amfi_code = next((t.amfi_code for t in txns if t.amfi_code), None)
        series = nav_series.get(amfi_code) if amfi_code else None
        sorted_txns = sorted(txns, key=lambda t: (t.txn_date, t.sequence))

        units_held = Decimal("0")
        invested_running = 0
        txn_idx = 0
        txn_count = len(sorted_txns)

        for i, report_date in enumerate(report_dates):
            while txn_idx < txn_count and sorted_txns[txn_idx].txn_date <= report_date:
                txn = sorted_txns[txn_idx]
                units_held += txn.units or Decimal("0")
                if txn.txn_type in CASH_OUTFLOW_TYPES and txn.amount_paise is not None:
                    invested_running += abs(txn.amount_paise)
                txn_idx += 1

            invested_paise[i] += invested_running

            if units_held > 0:
                nav = _nav_on_or_before(series, report_date)
                if nav is not None:
                    value_paise[i] += to_paise_strict(units_held * nav)
                else:
                    missing_nav[i] += 1

    return [
        PortfolioValuePoint(
            as_of_date=report_dates[i],
            value_paise=value_paise[i],
            invested_paise=invested_paise[i],
            positions_missing_nav=missing_nav[i],
        )
        for i in range(n)
    ]
