"""Maps casparser's CASData into FundLens's DB-independent transaction records."""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import casparser
from casparser.types import CASData, Scheme
from dateutil import parser as date_parser

from fundlens.db.models import TransactionType
from fundlens.ingestion.natural_key import compute_natural_key
from fundlens.ingestion.records import CoverageGap, ParsedCAS, TransactionRecord


def _to_paise(amount: Decimal | None) -> int | None:
    if amount is None:
        return None
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _to_date(value: date | str) -> date:
    if isinstance(value, date):
        return value
    return date_parser.parse(value).date()


def _parse_statement_date(value: str) -> date | None:
    try:
        return date_parser.parse(value).date()
    except (ValueError, TypeError):
        return None


def _scheme_identifier(scheme: Scheme) -> str:
    """Preference order matches data-model.md §2: ISIN, then AMFI code, then raw name."""
    return scheme.isin or scheme.amfi or scheme.scheme


def cas_data_to_parsed(cas: CASData) -> ParsedCAS:
    transactions: list[TransactionRecord] = []
    coverage_gaps: list[CoverageGap] = []

    for folio in cas.folios:
        for scheme in folio.schemes:
            if scheme.open != 0:
                coverage_gaps.append(
                    CoverageGap(
                        folio=folio.folio,
                        scheme_name=scheme.scheme,
                        opening_balance_units=scheme.open,
                        message=(
                            f"{scheme.open} units already held at the start of the statement "
                            "period — this CAS does not cover this scheme's full transaction "
                            "history."
                        ),
                    )
                )

            scheme_identifier = _scheme_identifier(scheme)
            for txn in scheme.transactions:
                amount_paise = _to_paise(txn.amount)
                txn_date = _to_date(txn.date)
                natural_key = compute_natural_key(
                    folio=folio.folio,
                    scheme_identifier=scheme_identifier,
                    txn_date=txn_date,
                    txn_type=txn.type.value,
                    amount_paise=amount_paise,
                    units=txn.units,
                )
                transactions.append(
                    TransactionRecord(
                        pan=folio.PAN,
                        folio=folio.folio,
                        amc=folio.amc,
                        scheme_name=scheme.scheme,
                        isin=scheme.isin,
                        amfi_code=scheme.amfi,
                        txn_type=TransactionType(txn.type.value),
                        txn_date=txn_date,
                        description=txn.description,
                        units=txn.units,
                        nav=txn.nav,
                        amount_paise=amount_paise,
                        balance_after=txn.balance,
                        dividend_rate=txn.dividend_rate,
                        gift_folio=txn.gift_folio,
                        natural_key=natural_key,
                    )
                )

    return ParsedCAS(
        statement_period_start=_parse_statement_date(cas.statement_period.from_),
        statement_period_end=_parse_statement_date(cas.statement_period.to),
        parser_version=casparser.__version__,
        transactions=transactions,
        coverage_gaps=coverage_gaps,
    )
