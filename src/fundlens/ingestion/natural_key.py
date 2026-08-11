"""The FR-1.2 idempotent-merge dedupe key.

natural_key = sha256(folio, scheme_identifier, txn_date, txn_type, amount_paise,
                      units, balance_after, ordinal)

Shared by both the CAS-PDF path and the CSV-import escape hatch so a transaction
entered either way can never be double-counted.

Real MFCentral CAS data (26 folios / 57 schemes / 1,578 transactions spanning
1980-2026) proved the first five fields alone are not always unique: multiple
distinct same-day, same-amount, same-unit transactions collide. Two more
fields were added to disambiguate, in order of preference:

1. `balance_after` — casparser's running unit balance after the row. Two
   genuinely distinct transactions necessarily leave the ledger at different
   cumulative balances, even when every other field matches. This resolved
   100% of the real-data collisions among unit-bearing rows (purchases,
   switches). It's None for zero-unit rows (tax lines), so it can't help
   there.
2. `ordinal` — for rows where every other field (including balance_after)
   still ties — chiefly STT/stamp-duty/TDS tax lines, which don't carry
   units or a balance — the Nth occurrence of an identical tuple within its
   scheme's transaction list, in the stable order casparser emits them.
   Deterministic and stable across re-uploads of overlapping data, since the
   relative order of historical entries doesn't change between CAS pulls.
   Callers default this to 0; only the mapping/import layers that iterate a
   scheme's transactions in order need to track and pass repeats.
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
    balance_after: Decimal | None = None,
    ordinal: int = 0,
) -> str:
    parts = [
        folio,
        scheme_identifier,
        txn_date.isoformat(),
        txn_type,
        "" if amount_paise is None else str(amount_paise),
        "" if units is None else str(units.quantize(_UNITS_PRECISION)),
        "" if balance_after is None else str(balance_after.quantize(_UNITS_PRECISION)),
        str(ordinal),
    ]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
