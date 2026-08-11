from datetime import date
from decimal import Decimal

from casparser.types import TransactionType as CasparserTransactionType

from fundlens.db.models import TransactionType
from fundlens.ingestion.mapping import cas_data_to_parsed
from tests.ingestion.factories import make_cas_data, make_folio, make_scheme, make_transaction


def test_maps_basic_purchase_transaction() -> None:
    cas = make_cas_data()

    parsed = cas_data_to_parsed(cas)

    assert parsed.statement_period_start == date(2021, 4, 1)
    assert parsed.statement_period_end == date(2021, 4, 30)
    assert len(parsed.transactions) == 1

    txn = parsed.transactions[0]
    assert txn.folio == "12345678"
    assert txn.amc == "Test AMC"
    assert txn.pan == "ABCDE1234F"
    assert txn.scheme_name == "Test Fund - Direct - Growth"
    assert txn.isin == "INF000X01234"
    assert txn.amfi_code == "123456"
    assert txn.txn_type == TransactionType.PURCHASE
    assert txn.txn_date == date(2021, 4, 15)
    assert txn.units == Decimal("100.5000")
    assert txn.amount_paise == 500_000  # Decimal("5000.00") -> paise
    assert txn.balance_after == Decimal("100.5000")


def test_amount_converted_to_paise_with_rounding() -> None:
    cas = make_cas_data(
        folios=[
            make_folio(
                schemes=[
                    make_scheme(
                        transactions=[make_transaction(amount=Decimal("1234.567"))],
                    )
                ]
            )
        ]
    )

    parsed = cas_data_to_parsed(cas)

    # 1234.567 * 100 = 123456.7 -> rounds to 123457 paise
    assert parsed.transactions[0].amount_paise == 123_457


def test_scheme_with_zero_opening_balance_has_no_coverage_gap() -> None:
    cas = make_cas_data(
        folios=[make_folio(schemes=[make_scheme(open_balance=Decimal("0"))])]
    )

    parsed = cas_data_to_parsed(cas)

    assert parsed.coverage_gaps == []


def test_scheme_with_nonzero_opening_balance_flags_coverage_gap() -> None:
    cas = make_cas_data(
        folios=[
            make_folio(
                schemes=[
                    make_scheme(scheme_name="Pre-existing Fund", open_balance=Decimal("42.5"))
                ]
            )
        ]
    )

    parsed = cas_data_to_parsed(cas)

    assert len(parsed.coverage_gaps) == 1
    gap = parsed.coverage_gaps[0]
    assert gap.folio == "12345678"
    assert gap.scheme_name == "Pre-existing Fund"
    assert gap.opening_balance_units == Decimal("42.5")
    assert "42.5" in gap.message


def test_scheme_identifier_prefers_isin_then_amfi_then_name() -> None:
    txn_isin_only = make_transaction()
    txn_amfi_only = make_transaction()
    txn_name_only = make_transaction()

    cas = make_cas_data(
        folios=[
            make_folio(
                folio="F1",
                schemes=[
                    make_scheme(
                        scheme_name="Fund A",
                        isin="INF_A",
                        amfi="AMFI_A",
                        transactions=[txn_isin_only],
                    ),
                    make_scheme(
                        scheme_name="Fund B",
                        isin=None,
                        amfi="AMFI_B",
                        transactions=[txn_amfi_only],
                    ),
                    make_scheme(
                        scheme_name="Fund C",
                        isin=None,
                        amfi=None,
                        transactions=[txn_name_only],
                    ),
                ],
            )
        ]
    )

    parsed = cas_data_to_parsed(cas)
    natural_keys = {t.scheme_name: t.natural_key for t in parsed.transactions}

    # Different schemes with otherwise-identical transaction shape must still
    # produce distinct natural keys, proving the identifier fallback chain
    # actually differentiates them.
    assert len({natural_keys["Fund A"], natural_keys["Fund B"], natural_keys["Fund C"]}) == 3


def test_all_casparser_transaction_types_map_without_error() -> None:
    for cas_txn_type in CasparserTransactionType:
        cas = make_cas_data(
            folios=[
                make_folio(schemes=[make_scheme(transactions=[make_transaction(type=cas_txn_type)])])
            ]
        )

        parsed = cas_data_to_parsed(cas)

        assert parsed.transactions[0].txn_type == TransactionType(cas_txn_type.value)


def test_transaction_with_no_units_or_amount_still_maps() -> None:
    # Tax-line rows (STT_TAX etc.) can carry an amount with no units.
    tax_txn = make_transaction(
        type=CasparserTransactionType.STT_TAX,
        units=None,
        amount=Decimal("12.50"),
        nav=None,
        balance=None,
    )
    cas = make_cas_data(folios=[make_folio(schemes=[make_scheme(transactions=[tax_txn])])])

    parsed = cas_data_to_parsed(cas)

    txn = parsed.transactions[0]
    assert txn.units is None
    assert txn.amount_paise == 1250
    assert txn.txn_type == TransactionType.STT_TAX
