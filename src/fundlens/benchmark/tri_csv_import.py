"""Manual TRI CSV import escape hatch: FundLens has no free source of real
(non-proxy) TRI data (docs/tech-d.md; every benchmark_history row today is a
proxy — see BenchmarkHistory's docstring). Rather than reverse-engineer NSE
Indices' unstable, undocumented export shape, this defines FundLens's own
simple two-column format the user reformats their downloaded data into
(spreadsheet copy-paste is trivial) — same "never guess a format
sight-unseen" discipline as ingestion/csv_import.py, not an attempt to parse
any vendor's native shape.

Required columns: date, value.
"""

import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from io import StringIO

REQUIRED_COLUMNS = {"date", "value"}


class TRICSVValidationError(Exception):
    """Raised when the CSV is missing required columns or has an unparseable row."""


@dataclass(frozen=True)
class TRIRow:
    nav_date: date
    value: Decimal


def parse_tri_csv(content: str) -> list[TRIRow]:
    reader = csv.DictReader(StringIO(content))
    fieldnames = set(reader.fieldnames or [])
    if not REQUIRED_COLUMNS.issubset(fieldnames):
        missing = sorted(REQUIRED_COLUMNS - fieldnames)
        raise TRICSVValidationError(f"CSV is missing required columns: {missing}")

    rows: list[TRIRow] = []
    for row_number, row in enumerate(reader, start=2):  # header is row 1
        if not row.get("date") and not row.get("value"):
            continue  # skip blank lines
        try:
            rows.append(
                TRIRow(
                    nav_date=date.fromisoformat(row["date"]),
                    value=Decimal(row["value"]),
                )
            )
        except (KeyError, ValueError, ArithmeticError) as exc:
            raise TRICSVValidationError(f"Row {row_number}: {exc}") from exc

    return rows
