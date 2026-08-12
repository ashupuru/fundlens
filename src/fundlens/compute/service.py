"""Rebuilds lots and realized gains from raw_transaction (compute-engine.md §1).

Derived state, rebuilt wholesale — never patched. Rebuilding a folio deletes
its existing lot/realized_gain rows and regenerates them fresh, per
data-model.md's "everything derivable is a view or a cached, invalidatable
table" principle. Safe to call repeatedly (e.g. after every ingestion).
"""

from dataclasses import dataclass, replace

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from fundlens.compute.lots import build_lots
from fundlens.compute.records import InsufficientLotsError, LotInputTxn
from fundlens.db.models import Lot, RawTransaction, RealizedGain
from fundlens.scheme_identity import scheme_key


@dataclass(frozen=True)
class SchemeRebuildError:
    folio: str
    scheme_key: str
    message: str


@dataclass(frozen=True)
class RebuildSummary:
    folios_processed: int
    schemes_processed: int
    lots_created: int
    realized_gains_created: int
    errors: list[SchemeRebuildError]


def get_all_folios(session: Session) -> set[str]:
    return set(session.scalars(select(RawTransaction.folio).distinct()).all())


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
        try:
            result = build_lots(inputs)
        except InsufficientLotsError as exc:
            errors.append(SchemeRebuildError(folio=folio, scheme_key=key, message=str(exc)))
            continue

        for lot in result.lots:
            session.add(
                Lot(
                    folio=folio,
                    scheme_key=key,
                    open_date=lot.open_date,
                    units_remaining=lot.units_remaining,
                    cost_per_unit=lot.cost_per_unit,
                    source_txn_id=lot.source_txn_id,
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
                    source_txn_id=rg.source_txn_id,
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
    )


def rebuild_all_lots(session: Session) -> RebuildSummary:
    total = RebuildSummary(
        folios_processed=0,
        schemes_processed=0,
        lots_created=0,
        realized_gains_created=0,
        errors=[],
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
        )
    return total
