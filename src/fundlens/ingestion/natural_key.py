"""The FR-1.2 idempotent-merge dedupe key.

natural_key = sha256(folio, scheme_identifier, txn_date, txn_type, amount_paise, units)

Shared by both the CAS-PDF path and the CSV-import escape hatch so a transaction
entered either way can never be double-counted.
"""

import hashlib
from datetime import date
from decimal import Decimal

_UNITS_PRECISION = Decimal("0.0001")


def compute_natural_key(
    folio: str,
    scheme_identifier: str,
    txn_date: date,
    txn_type: str,
    amount_paise: int | None,
    units: Decimal | None,
) -> str:
    parts = [
        folio,
        scheme_identifier,
        txn_date.isoformat(),
        txn_type,
        "" if amount_paise is None else str(amount_paise),
        "" if units is None else str(units.quantize(_UNITS_PRECISION)),
    ]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
