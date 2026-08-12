"""Pure FIFO lot accounting (compute-engine.md §1).

No I/O, no DB — a pure function over an already-scoped, already-sorted list
of transactions for exactly one (folio, scheme). Grouping/sorting/persisting
is the caller's job (compute/service.py); this module is only the math, so
it stays trivially unit- and property-testable.

Branches purely on the sign of `units`, verified against a real 1,578-row
MFCentral CAS to hold across every observed transaction type without
exception — including edge cases:
- PURCHASE / PURCHASE_SIP / DIVIDEND_REINVEST / SWITCH_IN / SEGREGATION: +units
- REDEMPTION / SWITCH_OUT / REVERSAL: -units
No txn_type-based sign flip is needed or performed.

Price-per-unit fallback (used both to cost a new lot and to price an
outflow for realized-gain purposes): prefer `nav` (the per-unit price
casparser gives directly); fall back to `amount / units` if nav is absent
but amount is present; fall back to zero, flagged, if neither is present.
The only real-data case observed needing that last fallback is SEGREGATION
(0/5 real rows carried an amount or nav) — a structural side-pocket event,
not a priced transaction. Indian tax rules require carrying over the
original lot's cost basis pro-rata for these; that refinement belongs to
the tax engine (P1.2), not here — `zero_cost_flag` marks the lot so nothing
downstream mistakes a zero cost basis for a real one.
"""

from collections import deque
from decimal import Decimal

from fundlens.compute.records import (
    InsufficientLotsError,
    Lot,
    LotBuildResult,
    LotInputTxn,
    RealizedGainRecord,
)
from fundlens.money import to_paise_strict


def _price_per_unit(
    nav: Decimal | None, amount_paise: int | None, units: Decimal
) -> tuple[Decimal, bool]:
    """Returns (price_per_unit, is_zero_cost_fallback)."""
    if nav is not None:
        return nav, False
    if amount_paise is not None and units != 0:
        return (Decimal(amount_paise) / 100) / abs(units), False
    return Decimal("0"), True


def build_lots(transactions: list[LotInputTxn]) -> LotBuildResult:
    open_lots: deque[Lot] = deque()
    all_lots: list[Lot] = []
    realized_gains: list[RealizedGainRecord] = []

    for txn in transactions:
        if txn.units is None or txn.units == 0:
            continue  # tax lines, dividend payouts, etc. never touch the lot ledger

        if txn.units > 0:
            price, is_zero_cost = _price_per_unit(txn.nav, txn.amount_paise, txn.units)
            lot = Lot(
                open_date=txn.txn_date,
                units_remaining=txn.units,
                cost_per_unit=price,
                source_txn_id=txn.id,
                source_txn_type=txn.txn_type,
                zero_cost_flag=is_zero_cost,
            )
            open_lots.append(lot)
            all_lots.append(lot)
            continue

        remaining_to_consume = -txn.units  # units is negative; work with a positive magnitude
        sale_price, _ = _price_per_unit(txn.nav, txn.amount_paise, txn.units)

        while remaining_to_consume > 0:
            if not open_lots:
                raise InsufficientLotsError(txn.id, remaining_to_consume)

            oldest = open_lots[0]
            consumed = min(oldest.units_remaining, remaining_to_consume)

            realized_gain = (sale_price - oldest.cost_per_unit) * consumed
            realized_gains.append(
                RealizedGainRecord(
                    source_txn_id=oldest.source_txn_id,
                    open_date=oldest.open_date,
                    sale_txn_id=txn.id,
                    sale_date=txn.txn_date,
                    units_consumed=consumed,
                    cost_per_unit=oldest.cost_per_unit,
                    sale_price_per_unit=sale_price,
                    realized_gain_paise=to_paise_strict(realized_gain),
                    holding_period_days=(txn.txn_date - oldest.open_date).days,
                )
            )

            oldest.units_remaining -= consumed
            remaining_to_consume -= consumed
            if oldest.units_remaining == 0:
                oldest.close_date = txn.txn_date
                open_lots.popleft()

    return LotBuildResult(lots=all_lots, realized_gains=realized_gains)
