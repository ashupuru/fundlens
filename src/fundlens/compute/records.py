"""DB-independent records for the lot allocator (compute-engine.md §1).

Kept separate from ORM models so build_lots() is a pure function testable
without a DB — the entire point of the compute engine being pure per
technical-spec.md §4.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class LotInputTxn:
    """One raw_transaction row, reduced to exactly what FIFO lot math needs.

    `units` must already be signed (positive = inflow, negative = outflow) —
    verified against real casparser output that this holds across every
    observed transaction type, so the allocator branches on sign alone and
    never needs to know the original txn_type to decide direction.
    """

    id: str
    txn_date: date
    txn_type: str  # kept for lot provenance/labeling only, not control flow
    units: Decimal | None
    nav: Decimal | None
    amount_paise: int | None


@dataclass
class Lot:
    open_date: date
    units_remaining: Decimal
    cost_per_unit: Decimal
    # None for a gap-fill lot -- an assumed pre-existing position inferred
    # from an InsufficientLotsError, with no real transaction to point to.
    source_txn_id: str | None
    source_txn_type: str
    close_date: date | None = None
    zero_cost_flag: bool = False  # e.g. SEGREGATION rows with no priced amount


@dataclass(frozen=True)
class RealizedGainRecord:
    source_txn_id: str | None  # the lot (partially) consumed; None if it was a gap-fill lot
    open_date: date
    sale_txn_id: str
    sale_date: date
    units_consumed: Decimal
    cost_per_unit: Decimal
    sale_price_per_unit: Decimal
    realized_gain_paise: int
    holding_period_days: int


@dataclass(frozen=True)
class LotBuildResult:
    lots: list[Lot]
    realized_gains: list[RealizedGainRecord]


class InsufficientLotsError(Exception):
    """Raised when an outflow transaction consumes more units than the open
    lots hold — e.g. the ingested history doesn't cover a scheme's full
    history (see coverage-gap detection, data-model.md §2). Callers should
    surface this as a data-quality flag, not crash the whole computation.
    """

    def __init__(self, txn_id: str, shortfall: Decimal) -> None:
        self.txn_id = txn_id
        self.shortfall = shortfall
        super().__init__(
            f"Transaction {txn_id} consumes {shortfall} more units than open lots hold — "
            "likely incomplete transaction history for this scheme."
        )
