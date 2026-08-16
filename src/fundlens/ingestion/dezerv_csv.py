"""Dezerv "BANK BOOK" CSV export: a cash-ledger format, not a proper
transaction register — real-data-verified structure (2026-08):

    Dezerv Investments Private Limited,,,,,,,,,,,,,,,,,,
    "<multi-line quoted address>",,,,,,,,,,,,,,,,,,
    BANK BOOK,,,,,,,,,,,,,,,,,,
    From DD/MM/YYYY To DD/MM/YYYY,,,,,,,,,,,,,,,,,,
    <strategy name>,,,,,,,,,,,,,,,,,,
    Code,Name,Bank Account,Bank Name,Transaction Description,Tran Date,
    Set Date,Tran Account,Symbol Code,Security,Buy/Sell Amount,Income,
    Expenses,Dep/With,Balance,Custodian Account,Tran Ref.,Desc/Notes,
    Account Code
    <data rows...>

The preamble's line count isn't fixed (the address field spans a variable
number of physical lines within one quoted CSV field), so the header row
is located structurally — by matching its first five column names — rather
than by skipping N lines, which correctly rides through `csv.reader`
handling the embedded newlines in the quoted address as a single logical
row regardless of exactly how many lines it spans.

Only rows where `Tran Account == "MFDEMAT"` are genuine mutual-fund
transactions; every other row (cash deposits, other custody types) is a
bank-ledger entry with no scheme at all and is dropped. Within MFDEMAT
rows, "Buy"/"Sell" are the only transaction descriptions verified against
real data; anything else is collected and surfaced rather than guessed at
(compute-engine.md-style flag, don't guess) — `DezervParseResult.
unrecognized_transaction_descriptions` names them so the caller can decide
whether to treat them as ignorable or report them.

**Critically, this format has no `units` column and no ISIN/AMFI code** —
only a rupee amount and a scheme name string ("Security", e.g. "HDFC NIFTY
50 Index Direct-G"). `dezerv_matching.py` resolves the scheme name against
AMFI's real feed and `dezerv_service.py` back-computes units from the
resolved scheme's NAV on the transaction date — this module only parses
the raw ledger rows, it doesn't touch AMFI data at all.
"""

import csv
import io
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from dateutil import parser as date_parser

_EXPECTED_HEADER_PREFIX = ("Code", "Name", "Bank Account", "Bank Name", "Transaction Description")
_MFDEMAT = "MFDEMAT"
_BUY = "Buy"
_SELL = "Sell"
_REQUIRED_COLUMNS = (
    "Transaction Description",
    "Tran Date",
    "Tran Account",
    "Symbol Code",
    "Security",
    "Buy/Sell Amount",
    "Custodian Account",
)


class DezervFormatError(Exception):
    """The file doesn't look like a Dezerv BANK BOOK export at all."""


@dataclass(frozen=True)
class DezervRow:
    # Closest stable per-account identifier this format has -- used as folio.
    custodian_account: str
    security: str
    symbol_code: str
    transaction_description: str
    txn_date: date
    amount_paise: int  # always a positive magnitude; direction comes from transaction_description


@dataclass(frozen=True)
class DezervParseResult:
    rows: list[DezervRow]
    unrecognized_transaction_descriptions: set[str]


def _parse_date(value: str) -> date | None:
    try:
        return date_parser.parse(value, dayfirst=True).date()
    except (ValueError, TypeError):
        return None


def _parse_amount_paise(value: str) -> int | None:
    cleaned = value.strip().replace(",", "")
    if not cleaned:
        return None
    try:
        return int(Decimal(cleaned) * 100)
    except InvalidOperation:
        return None


def parse_dezerv_csv(content: str) -> DezervParseResult:
    reader = csv.reader(io.StringIO(content))

    header: list[str] | None = None
    for row in reader:
        if len(row) >= len(_EXPECTED_HEADER_PREFIX) and tuple(
            cell.strip() for cell in row[: len(_EXPECTED_HEADER_PREFIX)]
        ) == _EXPECTED_HEADER_PREFIX:
            header = [cell.strip() for cell in row]
            break

    if header is None:
        raise DezervFormatError("Could not find the Dezerv transaction header row in this file")

    col = {name: i for i, name in enumerate(header)}
    missing = [c for c in _REQUIRED_COLUMNS if c not in col]
    if missing:
        raise DezervFormatError(f"Dezerv header is missing expected column(s): {missing}")

    rows: list[DezervRow] = []
    unrecognized: set[str] = set()

    for row in reader:
        if len(row) <= max(col[c] for c in _REQUIRED_COLUMNS):
            continue  # short/blank trailing row

        if row[col["Tran Account"]].strip() != _MFDEMAT:
            continue

        description = row[col["Transaction Description"]].strip()
        if description not in (_BUY, _SELL):
            unrecognized.add(description)
            continue

        txn_date = _parse_date(row[col["Tran Date"]].strip())
        if txn_date is None:
            continue

        amount_paise = _parse_amount_paise(row[col["Buy/Sell Amount"]])
        if amount_paise is None:
            continue

        rows.append(
            DezervRow(
                custodian_account=row[col["Custodian Account"]].strip(),
                security=row[col["Security"]].strip(),
                symbol_code=row[col["Symbol Code"]].strip(),
                transaction_description=description,
                txn_date=txn_date,
                amount_paise=abs(amount_paise),
            )
        )

    return DezervParseResult(rows=rows, unrecognized_transaction_descriptions=unrecognized)
