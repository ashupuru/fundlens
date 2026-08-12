"""Current-holdings views over the persisted lot ledger (PRD FR-2.2 subset).

By folio+scheme and by AMC. By-category is deliberately not included yet —
it needs SEBI scheme classification, which isn't ingested anywhere yet (AMFI
NAVAll gives NAV and name, not category); that's a future scheme_metadata
phase, not silently faked here.
"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.db.models import Lot, RawTransaction
from fundlens.money import to_paise_strict
from fundlens.scheme_identity import scheme_key


@dataclass(frozen=True)
class HoldingView:
    folio: str
    scheme_key: str
    scheme_name: str
    amc: str
    pan: str | None
    isin: str | None
    amfi_code: str | None
    units_remaining: Decimal
    cost_basis_paise: int
    average_cost_per_unit: Decimal


def get_current_holdings(session: Session) -> list[HoldingView]:
    lots = session.scalars(select(Lot).where(Lot.units_remaining > 0)).all()
    if not lots:
        return []

    folios = {lot.folio for lot in lots}
    txns = session.scalars(
        select(RawTransaction).where(RawTransaction.folio.in_(folios))
    ).all()
    scheme_info: dict[tuple[str, str], RawTransaction] = {}
    for txn in txns:
        key = scheme_key(txn.isin, txn.amfi_code, txn.scheme_name)
        scheme_info.setdefault((txn.folio, key), txn)

    grouped: dict[tuple[str, str], list[Lot]] = {}
    for lot in lots:
        grouped.setdefault((lot.folio, lot.scheme_key), []).append(lot)

    holdings: list[HoldingView] = []
    for (folio, key), scheme_lots in grouped.items():
        units_remaining = sum((lt.units_remaining for lt in scheme_lots), start=Decimal("0"))
        cost_basis = sum(
            (lt.units_remaining * lt.cost_per_unit for lt in scheme_lots), start=Decimal("0")
        )
        average_cost_per_unit = (
            cost_basis / units_remaining if units_remaining != 0 else Decimal("0")
        )
        info = scheme_info.get((folio, key))

        holdings.append(
            HoldingView(
                folio=folio,
                scheme_key=key,
                scheme_name=info.scheme_name if info else key,
                amc=info.amc if info else "",
                pan=info.pan if info else None,
                isin=info.isin if info else None,
                amfi_code=info.amfi_code if info else None,
                units_remaining=units_remaining,
                cost_basis_paise=to_paise_strict(cost_basis),
                average_cost_per_unit=average_cost_per_unit,
            )
        )

    return holdings


@dataclass(frozen=True)
class AmcHoldingView:
    amc: str
    scheme_count: int
    cost_basis_paise: int


def get_holdings_by_amc(session: Session) -> list[AmcHoldingView]:
    holdings = get_current_holdings(session)

    by_amc: dict[str, list[HoldingView]] = {}
    for h in holdings:
        by_amc.setdefault(h.amc, []).append(h)

    return [
        AmcHoldingView(
            amc=amc,
            scheme_count=len(rows),
            cost_basis_paise=sum(r.cost_basis_paise for r in rows),
        )
        for amc, rows in by_amc.items()
    ]
