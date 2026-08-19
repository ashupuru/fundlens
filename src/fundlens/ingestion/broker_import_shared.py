"""Shared logic between broker/platform CSV importers (`dezerv_service.py`,
`zerodha_service.py`) that isn't specific to either source's own file format:
cross-source duplicate detection and AMC-name resolution.

**Cross-source duplicate detection.** Real-data-verified against a live
portfolio (originally found via Dezerv): the same real transaction can
already exist in `raw_transaction` from a CAS/CSV import *before* a broker
import of the same activity is run -- a broker's own account identifier
(Dezerv's custodian account, Zerodha's synthetic folio) is never the same
string as a CAS folio (the AMC's own folio number), so the natural-key
dedup (keyed on `folio` among other fields) never recognizes these as the
same transaction and both get inserted. `commit_*` therefore checks, per
row, whether a transaction already exists for the same
`(amfi_code, txn_date, buy/sell-equivalent txn_type)` *regardless of
folio*, and skips inserting it if so.

**Matching on amount OR units, because each source reports exactly one of
them exactly.** The original rule matched on `amount_paise` alone, with a
docstring explaining that units were deliberately excluded since "a
broker's units can be back-computed". That was correct for Dezerv --
whose export has no units column at all, so they're derived from NAV,
while its rupee amounts are exact -- and exactly backwards for the
Zerodha tradebook, which reports exact units and an amount that drifts by
paise from what the CAS shows:

    2021-10-06  CAS Rs 49,997.50   Zerodha Rs 49,997.44   units 379.9850 both
    2026-01-12  CAS Rs 37,498.13   Zerodha Rs 37,498.92   units  16.5230 both

The CAS reports the amount net of stamp duty (carried on its own separate
line); Zerodha reports the gross order. So an exact-amount match never
fired, and 113 real transactions -- Rs 8.69 L -- were ingested twice
before this was caught. Matching on *either* field, rather than replacing
one with the other, keeps Dezerv's real-data-verified amount match (35
transactions) working while catching Zerodha's.

Deliberately **not** an amount tolerance: two genuinely separate same-day
purchases of the same fund for a similar amount are indistinguishable
from a duplicate, so widening the amount arm adds false-positive risk
without catching anything the units arm misses.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from fundlens.db.models import RawTransaction, TransactionType
from fundlens.market_data.mfapi_client import fetch_scheme_history
from fundlens.market_data.service import backfill_scheme_via_mfapi

BUY_EQUIVALENT_TXN_TYPES = (TransactionType.PURCHASE, TransactionType.PURCHASE_SIP)
SELL_EQUIVALENT_TXN_TYPES = (TransactionType.REDEMPTION,)

# raw_transaction.units is Numeric(20, 4); comparing at the column's own
# precision avoids a stored 379.9850 failing to equal an incoming 379.985.
UNITS_PRECISION = Decimal("0.0001")


def existing_cross_source_transaction(
    session: Session,
    amfi_code: str,
    txn_date: date,
    is_buy: bool,
    amount_paise: int,
    units: Decimal | None = None,
) -> bool:
    equivalent_types = BUY_EQUIVALENT_TXN_TYPES if is_buy else SELL_EQUIVALENT_TXN_TYPES

    same_amount = RawTransaction.amount_paise == amount_paise
    if units is None or units == 0:
        identity = same_amount
    else:
        # Compare magnitudes on both sides: stored units are signed (negative
        # for redemptions) and so is the caller's value, but the direction is
        # already pinned by txn_type above -- matching signed values would
        # simply never fire for a sell if either side normalised differently.
        identity = or_(
            same_amount,
            func.abs(RawTransaction.units) == abs(units).quantize(UNITS_PRECISION),
        )

    match = session.execute(
        select(RawTransaction.id)
        .where(
            RawTransaction.amfi_code == amfi_code,
            RawTransaction.txn_date == txn_date,
            RawTransaction.txn_type.in_(equivalent_types),
            identity,
        )
        .limit(1)
    ).first()
    return match is not None


def resolve_amc_names(session: Session, amfi_codes: set[str]) -> dict[str, str]:
    amc_by_code: dict[str, str] = {}
    for amfi_code in amfi_codes:
        backfill_scheme_via_mfapi(session, amfi_code)  # idempotent NAV insert
        try:
            payload = fetch_scheme_history(amfi_code)
        except Exception:  # noqa: BLE001 - degrade to a generic AMC label, never crash the commit
            amc_by_code[amfi_code] = "Unknown AMC"
            continue
        amc_by_code[amfi_code] = payload.get("meta", {}).get("fund_house", "Unknown AMC")
    return amc_by_code
