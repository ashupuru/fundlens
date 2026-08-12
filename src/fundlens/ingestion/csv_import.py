"""CSV import escape hatch (PRD FR-1.3), for cases no CAS covers — e.g.
international feeder funds. Produces the same TransactionRecord shape as the
CAS path and computes the same kind of natural_key, so it flows through the
identical merge_transactions dedupe logic and can never bypass it.

Required columns: folio, amc, scheme_name, txn_type, txn_date, amount.
Optional columns: pan, isin, amfi_code, description, units, nav, balance_after,
dividend_rate, gift_folio.
"""

import csv
from datetime import date
from decimal import Decimal
from io import StringIO

from fundlens.db.models import TransactionType
from fundlens.ingestion.natural_key import compute_natural_key
from fundlens.ingestion.records import TransactionRecord
from fundlens.money import to_paise

REQUIRED_COLUMNS = {"folio", "amc", "scheme_name", "txn_type", "txn_date", "amount"}


class CSVValidationError(Exception):
    """Raised when the CSV is missing required columns or has an unparseable row."""


def _optional_decimal(value: str | None) -> Decimal | None:
    return Decimal(value) if value else None


def parse_transactions_csv(content: str) -> list[TransactionRecord]:
    reader = csv.DictReader(StringIO(content))
    fieldnames = set(reader.fieldnames or [])
    if not REQUIRED_COLUMNS.issubset(fieldnames):
        missing = sorted(REQUIRED_COLUMNS - fieldnames)
        raise CSVValidationError(f"CSV is missing required columns: {missing}")

    records: list[TransactionRecord] = []
    ordinal_counts: dict[tuple, int] = {}
    sequence_counts: dict[tuple[str, str], int] = {}
    for row_number, row in enumerate(reader, start=2):  # header is row 1
        try:
            txn_date = date.fromisoformat(row["txn_date"])
            txn_type = TransactionType(row["txn_type"])
            amount_paise = to_paise(Decimal(row["amount"]))
            units = _optional_decimal(row.get("units"))
            balance_after = _optional_decimal(row.get("balance_after"))
            isin = row.get("isin") or None
            amfi_code = row.get("amfi_code") or None
            scheme_identifier = isin or amfi_code or row["scheme_name"]

            # Ordinal tiebreaker for rows tying on every other field — see
            # natural_key.py docstring. Tracked per (folio, scheme), in file order.
            tie_key = (
                row["folio"],
                scheme_identifier,
                txn_date,
                txn_type.value,
                amount_paise,
                units,
                balance_after,
            )
            ordinal = ordinal_counts.get(tie_key, 0)
            ordinal_counts[tie_key] = ordinal + 1

            # Sequence within (folio, scheme), in file order — same role as
            # mapping.py's casparser-order sequence, for FIFO lot ordering.
            scheme_group = (row["folio"], scheme_identifier)
            sequence = sequence_counts.get(scheme_group, 0)
            sequence_counts[scheme_group] = sequence + 1

            records.append(
                TransactionRecord(
                    pan=row.get("pan") or None,
                    folio=row["folio"],
                    amc=row["amc"],
                    scheme_name=row["scheme_name"],
                    isin=isin,
                    amfi_code=amfi_code,
                    txn_type=txn_type,
                    txn_date=txn_date,
                    sequence=sequence,
                    description=row.get("description") or None,
                    units=units,
                    nav=_optional_decimal(row.get("nav")),
                    amount_paise=amount_paise,
                    balance_after=balance_after,
                    dividend_rate=_optional_decimal(row.get("dividend_rate")),
                    gift_folio=row.get("gift_folio") or None,
                    natural_key=compute_natural_key(
                        folio=row["folio"],
                        scheme_identifier=scheme_identifier,
                        txn_date=txn_date,
                        txn_type=txn_type.value,
                        amount_paise=amount_paise,
                        units=units,
                        balance_after=balance_after,
                        ordinal=ordinal,
                    ),
                )
            )
        except (KeyError, ValueError, ArithmeticError) as exc:
            raise CSVValidationError(f"Row {row_number}: {exc}") from exc

    return records
