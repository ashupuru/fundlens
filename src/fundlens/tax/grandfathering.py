"""Equity LTCG grandfathering (Section 112A 4th/5th proviso): for
equity-oriented units purchased before the assumption registry's
`equity_grandfather_cutoff` (2018-01-31 by default), the cost of
acquisition used for the *gain amount* is the higher of (a) actual cost
and (b) the lower of (i) FMV as of the cutoff date and (ii) sale price —
never the classification (STCG vs LTCG) itself, only the cost basis the
gain is computed from.

FMV is read from `nav_history`, which P0.2 already backfills a scheme's
*full* history into, not just from its first purchase, so no new data
source is needed. If a scheme predates the cutoff but has no NAV on/before
it (backfill gap), grandfathering is **not silently skipped in the
direction that understates tax** — flagged `note="grandfathering_data_unavailable"`
and actual cost is used, the conservative (higher-taxable-gain) fallback
when the true FMV can't be determined.

Shared by compute/capital_gains.py (realized gains) and
tax/friction.py (hypothetical gain on a currently-held position) — both
need the identical cost-basis adjustment.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.db.models import NavHistory


@dataclass(frozen=True)
class Grandfathering:
    cost_per_unit: Decimal
    applied: bool
    note: str | None


def fmv_per_unit_on_or_before(session: Session, amfi_code: str, on_date: date) -> Decimal | None:
    return session.execute(
        select(NavHistory.nav)
        .where(NavHistory.amfi_code == amfi_code, NavHistory.nav_date <= on_date)
        .order_by(NavHistory.nav_date.desc())
        .limit(1)
    ).scalar_one_or_none()


def apply_equity_grandfathering(
    session: Session,
    amfi_code: str | None,
    purchase_date: date,
    original_cost_per_unit: Decimal,
    sale_price_per_unit: Decimal,
    cutoff: date,
) -> Grandfathering:
    if purchase_date >= cutoff:
        return Grandfathering(original_cost_per_unit, applied=False, note=None)
    if amfi_code is None:
        return Grandfathering(
            original_cost_per_unit, applied=False, note="grandfathering_data_unavailable"
        )

    fmv = fmv_per_unit_on_or_before(session, amfi_code, cutoff)
    if fmv is None:
        return Grandfathering(
            original_cost_per_unit, applied=False, note="grandfathering_data_unavailable"
        )

    floor = min(fmv, sale_price_per_unit)
    adjusted = max(original_cost_per_unit, floor)
    return Grandfathering(adjusted, applied=adjusted != original_cost_per_unit, note=None)
