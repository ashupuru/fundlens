"""Plain, DB-independent records produced by the ingestion layer.

Kept separate from the SQLAlchemy models so the CASData -> record mapping
(the part with real logic worth testing) can be unit tested without a DB.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from fundlens.db.models import TransactionType


@dataclass(frozen=True)
class TransactionRecord:
    pan: str | None
    folio: str
    amc: str
    scheme_name: str
    isin: str | None
    amfi_code: str | None
    txn_type: TransactionType
    txn_date: date
    sequence: int
    description: str | None
    units: Decimal | None
    nav: Decimal | None
    amount_paise: int | None
    balance_after: Decimal | None
    dividend_rate: Decimal | None
    gift_folio: str | None
    natural_key: str


@dataclass(frozen=True)
class CoverageGap:
    folio: str
    scheme_name: str
    opening_balance_units: Decimal
    message: str


@dataclass(frozen=True)
class ParsedCAS:
    statement_period_start: date | None
    statement_period_end: date | None
    parser_version: str
    transactions: list[TransactionRecord]
    coverage_gaps: list[CoverageGap]
