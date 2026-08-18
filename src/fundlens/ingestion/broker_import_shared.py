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
`(amfi_code, txn_date, amount, buy/sell-equivalent txn_type)` *regardless
of folio*, and skips inserting it if so. Units are deliberately not part of
this match (a broker's units can be back-computed or rounded slightly
differently from CAS's exact figure); amount_paise is used instead since
it's stored exactly by every source.
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from fundlens.db.models import RawTransaction, TransactionType
from fundlens.market_data.mfapi_client import fetch_scheme_history
from fundlens.market_data.service import backfill_scheme_via_mfapi

BUY_EQUIVALENT_TXN_TYPES = (TransactionType.PURCHASE, TransactionType.PURCHASE_SIP)
SELL_EQUIVALENT_TXN_TYPES = (TransactionType.REDEMPTION,)


def existing_cross_source_transaction(
    session: Session, amfi_code: str, txn_date: date, is_buy: bool, amount_paise: int
) -> bool:
    equivalent_types = BUY_EQUIVALENT_TXN_TYPES if is_buy else SELL_EQUIVALENT_TXN_TYPES
    match = session.execute(
        select(RawTransaction.id)
        .where(
            RawTransaction.amfi_code == amfi_code,
            RawTransaction.txn_date == txn_date,
            RawTransaction.txn_type.in_(equivalent_types),
            RawTransaction.amount_paise == amount_paise,
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
