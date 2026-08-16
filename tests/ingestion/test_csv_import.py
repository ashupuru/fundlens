from datetime import date
from decimal import Decimal

import pytest

from fundlens.db.models import TransactionType
from fundlens.ingestion.csv_import import CSVValidationError, parse_transactions_csv
from fundlens.ingestion.natural_key import compute_natural_key

VALID_CSV = """folio,amc,scheme_name,txn_type,txn_date,amount,units,isin
F1,Test AMC,International Feeder Fund,PURCHASE,2021-04-15,5000.00,100.5,INF999
"""


def test_parses_valid_csv_row() -> None:
    records = parse_transactions_csv(VALID_CSV)

    assert len(records) == 1
    record = records[0]
    assert record.folio == "F1"
    assert record.amc == "Test AMC"
    assert record.scheme_name == "International Feeder Fund"
    assert record.txn_type == TransactionType.PURCHASE
    assert record.txn_date == date(2021, 4, 15)
    assert record.amount_paise == 500_000
    assert record.units == Decimal("100.5")
    assert record.isin == "INF999"


def test_missing_required_column_raises() -> None:
    csv_missing_amount = (
        "folio,amc,scheme_name,txn_type,txn_date\nF1,AMC,Fund,PURCHASE,2021-04-15\n"
    )

    with pytest.raises(CSVValidationError, match="missing required columns"):
        parse_transactions_csv(csv_missing_amount)


def test_unparseable_row_raises_with_row_number() -> None:
    bad_csv = (
        "folio,amc,scheme_name,txn_type,txn_date,amount\n"
        "F1,AMC,Fund,PURCHASE,not-a-date,5000\n"
    )

    with pytest.raises(CSVValidationError, match="Row 2"):
        parse_transactions_csv(bad_csv)


def test_unknown_txn_type_raises() -> None:
    bad_csv = (
        "folio,amc,scheme_name,txn_type,txn_date,amount\n"
        "F1,AMC,Fund,NOT_A_TYPE,2021-04-15,5000\n"
    )

    with pytest.raises(CSVValidationError):
        parse_transactions_csv(bad_csv)


def test_natural_key_matches_cas_path_for_equivalent_transaction() -> None:
    """A CSV-imported txn and a CAS-imported txn for the same real-world
    transaction must collide on natural_key, or a user could double-count
    a transaction by importing it both ways."""
    records = parse_transactions_csv(VALID_CSV)
    record = records[0]

    expected_key = compute_natural_key(
        folio="F1",
        scheme_identifier="INF999",  # ISIN preferred, matching mapping.py's fallback order
        txn_date=date(2021, 4, 15),
        txn_type="PURCHASE",
        amount_paise=500_000,
        units=Decimal("100.5"),
    )
    assert record.natural_key == expected_key


def test_optional_columns_default_to_none() -> None:
    minimal_csv = (
        "folio,amc,scheme_name,txn_type,txn_date,amount\nF1,AMC,Fund,PURCHASE,2021-04-15,5000\n"
    )

    records = parse_transactions_csv(minimal_csv)

    record = records[0]
    assert record.pan is None
    assert record.isin is None
    assert record.amfi_code is None
    assert record.units is None
    assert record.nav is None
