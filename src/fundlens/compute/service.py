"""Rebuilds lots and realized gains from raw_transaction (compute-engine.md §1).

Derived state, rebuilt wholesale — never patched. Rebuilding a folio deletes
its existing lot/realized_gain rows and regenerates them fresh, per
data-model.md's "everything derivable is a view or a cached, invalidatable
table" principle. Safe to call repeatedly (e.g. after every ingestion).

**Gap-fill lots.** Some import formats have no concept of an opening
balance at all (Dezerv's "BANK BOOK" export is a plain buy/sell ledger for
whatever date window was requested — unlike a CAS, which reports
`Scheme.open` when its own statement period starts mid-holding, see
data-model.md's coverage_gap section). When a scheme's outflow transactions
would consume more units than its recorded inflows provide,
`InsufficientLotsError` names the exact shortfall (compute/lots.py) —
previously this discarded the whole scheme's lots (silently dropping it
from holdings, "the fund isn't there"), which is what an import starting
mid-holding hits on essentially every first-time Dezerv-only import into an
empty database. Instead, retry with a synthetic opening lot sized to
exactly the reported shortfall, `zero_cost_flag=True` (same "flag, don't
fabricate" instinct already used for SEGREGATION's unpriced rows) and
`source_txn_id=None` (there is no real transaction to point to — Lot/
RealizedGain's FK was relaxed to nullable for exactly this). Bounded by the
scheme's own transaction count, since each retry can resolve at most one
new deficit point.
"""

from dataclasses import dataclass, replace
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from fundlens.compute.lots import build_lots
from fundlens.compute.records import InsufficientLotsError, LotInputTxn
from fundlens.db.models import Lot, RawTransaction, RealizedGain
from fundlens.scheme_identity import scheme_key

# Sentinel LotInputTxn.id for a synthesized gap-fill lot -- never a real
# RawTransaction.id (those are uuids), translated to source_txn_id=None at
# persistence time below. Kept as a string, not None, all the way through
# compute/lots.py's pure math so that module needs no changes at all.
_GAP_FILL_TXN_ID = "__gap_fill__"


@dataclass(frozen=True)
class SchemeRebuildError:
    folio: str
    scheme_key: str
    message: str


@dataclass(frozen=True)
class SchemeGapFilled:
    folio: str
    scheme_key: str
    units_assumed: Decimal
    message: str


@dataclass(frozen=True)
class RebuildSummary:
    folios_processed: int
    schemes_processed: int
    lots_created: int
    realized_gains_created: int
    errors: list[SchemeRebuildError]
    gap_filled: list[SchemeGapFilled]


def get_all_folios(session: Session) -> set[str]:
    """Union of every folio with transactions AND every folio with existing
    derived state (Lot/RealizedGain) -- not just the former. A folio whose
    transactions were all removed (e.g. deleting a PAN's data) still has
    stale lot/realized_gain rows until rebuild_lots_for_folio runs for it,
    but that only happens for folios this function returns; missing the
    lot/realized_gain-only case would leave those rows orphaned forever,
    since nothing else ever revisits a folio with zero transactions."""
    txn_folios = set(session.scalars(select(RawTransaction.folio).distinct()).all())
    lot_folios = set(session.scalars(select(Lot.folio).distinct()).all())
    gain_folios = set(session.scalars(select(RealizedGain.folio).distinct()).all())
    return txn_folios | lot_folios | gain_folios


def rebuild_lots_for_folio(session: Session, folio: str) -> RebuildSummary:
    transactions = session.scalars(
        select(RawTransaction)
        .where(RawTransaction.folio == folio)
        .order_by(RawTransaction.txn_date, RawTransaction.sequence)
    ).all()

    by_scheme: dict[str, list[RawTransaction]] = {}
    for txn in transactions:
        key = scheme_key(txn.isin, txn.amfi_code, txn.scheme_name)
        by_scheme.setdefault(key, []).append(txn)

    # Rebuild wholesale: clear this folio's existing derived state first.
    session.execute(delete(Lot).where(Lot.folio == folio))
    session.execute(delete(RealizedGain).where(RealizedGain.folio == folio))

    lots_created = 0
    gains_created = 0
    errors: list[SchemeRebuildError] = []
    gap_filled: list[SchemeGapFilled] = []

    for key, scheme_txns in by_scheme.items():
        inputs = [
            LotInputTxn(
                id=t.id,
                txn_date=t.txn_date,
                txn_type=t.txn_type.value,
                units=t.units,
                nav=t.nav,
                amount_paise=t.amount_paise,
            )
            for t in scheme_txns
        ]

        # Bounded by transaction count: each retry can resolve at most one
        # new deficit point, so this can never loop more times than there
        # are transactions to find deficits at.
        result = None
        for _attempt in range(len(scheme_txns) + 1):
            try:
                result = build_lots(inputs)
                break
            except InsufficientLotsError as exc:
                gap_lot = LotInputTxn(
                    id=_GAP_FILL_TXN_ID,
                    txn_date=inputs[0].txn_date,
                    txn_type="PURCHASE",
                    units=exc.shortfall,
                    nav=None,
                    amount_paise=None,
                )
                inputs = [gap_lot, *inputs]
                gap_filled.append(
                    SchemeGapFilled(
                        folio=folio,
                        scheme_key=key,
                        units_assumed=exc.shortfall,
                        message=(
                            f"Assumed {exc.shortfall} units already held before this scheme's "
                            "earliest recorded transaction (cost basis unknown, flagged "
                            "zero-cost) -- this import doesn't cover the scheme's full history."
                        ),
                    )
                )

        if result is None:
            errors.append(
                SchemeRebuildError(
                    folio=folio,
                    scheme_key=key,
                    message="Repeated gap-fill retries were still insufficient — unexpected.",
                )
            )
            continue

        for lot in result.lots:
            session.add(
                Lot(
                    folio=folio,
                    scheme_key=key,
                    open_date=lot.open_date,
                    units_remaining=lot.units_remaining,
                    cost_per_unit=lot.cost_per_unit,
                    source_txn_id=(
                        None if lot.source_txn_id == _GAP_FILL_TXN_ID else lot.source_txn_id
                    ),
                    source_txn_type=lot.source_txn_type,
                    close_date=lot.close_date,
                    zero_cost_flag=lot.zero_cost_flag,
                )
            )
            lots_created += 1

        for rg in result.realized_gains:
            session.add(
                RealizedGain(
                    folio=folio,
                    scheme_key=key,
                    source_txn_id=(
                        None if rg.source_txn_id == _GAP_FILL_TXN_ID else rg.source_txn_id
                    ),
                    open_date=rg.open_date,
                    sale_txn_id=rg.sale_txn_id,
                    sale_date=rg.sale_date,
                    units_consumed=rg.units_consumed,
                    cost_per_unit=rg.cost_per_unit,
                    sale_price_per_unit=rg.sale_price_per_unit,
                    realized_gain_paise=rg.realized_gain_paise,
                    holding_period_days=rg.holding_period_days,
                )
            )
            gains_created += 1

    session.commit()

    return RebuildSummary(
        folios_processed=1,
        schemes_processed=len(by_scheme),
        lots_created=lots_created,
        realized_gains_created=gains_created,
        errors=errors,
        gap_filled=gap_filled,
    )


def rebuild_all_lots(session: Session) -> RebuildSummary:
    total = RebuildSummary(
        folios_processed=0,
        schemes_processed=0,
        lots_created=0,
        realized_gains_created=0,
        errors=[],
        gap_filled=[],
    )
    for folio in get_all_folios(session):
        summary = rebuild_lots_for_folio(session, folio)
        total = replace(
            total,
            folios_processed=total.folios_processed + 1,
            schemes_processed=total.schemes_processed + summary.schemes_processed,
            lots_created=total.lots_created + summary.lots_created,
            realized_gains_created=total.realized_gains_created + summary.realized_gains_created,
            errors=total.errors + summary.errors,
            gap_filled=total.gap_filled + summary.gap_filled,
        )
    return total
