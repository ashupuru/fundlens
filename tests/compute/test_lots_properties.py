"""Property-based tests for the FIFO lot allocator (dev plan P0.3 test gate):
hypothesis-generated transaction sequences must never produce negative
units_remaining, and cost-basis must be conserved — the sum of what's still
open plus what's been realized must equal everything ever invested.
"""

from datetime import date, timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from fundlens.compute.lots import build_lots
from fundlens.compute.records import LotInputTxn


@st.composite
def _non_overselling_sequences(draw: st.DrawFn) -> list[LotInputTxn]:
    """Buys and sells for one scheme, in date order, where a sell never
    exceeds the running balance — the InsufficientLotsError path (a real
    data-quality signal, not an allocator bug) is covered separately in
    test_lots.py, not here."""
    n = draw(st.integers(min_value=1, max_value=25))
    start = date(2015, 1, 1)
    txns: list[LotInputTxn] = []
    balance = Decimal("0")

    for i in range(n):
        txn_date = start + timedelta(days=i * 3)
        nav = draw(st.decimals(min_value=Decimal("1"), max_value=Decimal("1000"), places=2))
        do_buy = balance == 0 or draw(st.booleans())

        if do_buy:
            units = draw(st.decimals(min_value=Decimal("0.1"), max_value=Decimal("500"), places=4))
            balance += units
            txn_type = "PURCHASE"
        else:
            units = draw(st.decimals(min_value=Decimal("0.0001"), max_value=balance, places=4))
            balance -= units
            units = -units
            txn_type = "REDEMPTION"

        txns.append(
            LotInputTxn(
                id=f"t{i}",
                txn_date=txn_date,
                txn_type=txn_type,
                units=units,
                nav=nav,
                amount_paise=None,
            )
        )

    return txns


@given(_non_overselling_sequences())
@settings(max_examples=200)
def test_units_remaining_never_negative(transactions: list[LotInputTxn]) -> None:
    result = build_lots(transactions)
    assert all(lot.units_remaining >= 0 for lot in result.lots)


@given(_non_overselling_sequences())
@settings(max_examples=200)
def test_cost_basis_is_conserved(transactions: list[LotInputTxn]) -> None:
    """invested == still-open cost basis + realized (consumed) cost basis."""
    result = build_lots(transactions)

    total_invested = sum(
        (t.units * t.nav for t in transactions if t.units is not None and t.units > 0),
        start=Decimal("0"),
    )
    cost_remaining = sum(
        (lot.units_remaining * lot.cost_per_unit for lot in result.lots),
        start=Decimal("0"),
    )
    cost_consumed = sum(
        (rg.units_consumed * rg.cost_per_unit for rg in result.realized_gains),
        start=Decimal("0"),
    )

    assert total_invested == cost_remaining + cost_consumed


@given(_non_overselling_sequences())
@settings(max_examples=200)
def test_total_units_bought_equals_units_remaining_plus_units_sold(
    transactions: list[LotInputTxn],
) -> None:
    """A units-level analogue of the cost-basis conservation check — every
    unit bought is accounted for as either still held or sold, never lost."""
    result = build_lots(transactions)

    total_bought = sum(
        (t.units for t in transactions if t.units is not None and t.units > 0),
        start=Decimal("0"),
    )
    units_remaining = sum((lot.units_remaining for lot in result.lots), start=Decimal("0"))
    units_sold = sum((rg.units_consumed for rg in result.realized_gains), start=Decimal("0"))

    assert total_bought == units_remaining + units_sold
