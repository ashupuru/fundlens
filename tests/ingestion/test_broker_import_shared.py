"""Cross-source duplicate matching.

Fixtures use the *real* figures from the portfolio where this bug was found:
the CAS and the Zerodha tradebook report the same SIP installment with
amounts a few paise apart (CAS is net of stamp duty, Zerodha is the gross
order) but identical units. An exact-amount match never fires on those, which
is how 113 transactions got ingested twice.
"""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from fundlens.db.models import RawCASUpload, RawTransaction, TransactionType, UploadStatus
from fundlens.ingestion.broker_import_shared import existing_cross_source_transaction

_AMFI = "120700"
_DATE = date(2021, 10, 6)
# The CAS side of a real ICICI Pru Aggressive Hybrid installment.
_CAS_AMOUNT_PAISE = 4999750
_CAS_UNITS = Decimal("379.9850")
# Zerodha's record of the same trade: 6 paise apart, units identical.
_ZERODHA_AMOUNT_PAISE = 4999744


def _seed(
    session: Session,
    *,
    amfi_code: str = _AMFI,
    txn_date: date = _DATE,
    txn_type: TransactionType = TransactionType.PURCHASE,
    amount_paise: int = _CAS_AMOUNT_PAISE,
    units: Decimal | None = _CAS_UNITS,
    folio: str = "38873887 / 81",
) -> None:
    upload = RawCASUpload(
        file_hash=f"h-{uuid.uuid4().hex}", parser_version="1.3.0", status=UploadStatus.PARSED
    )
    session.add(upload)
    session.flush()
    session.add(
        RawTransaction(
            upload_id=upload.id,
            folio=folio,
            amc="ICICI Prudential Mutual Fund",
            scheme_name="ICICI Prudential Aggressive Hybrid Active FOF",
            isin="INF109KC1NT3",
            amfi_code=amfi_code,
            txn_type=txn_type,
            txn_date=txn_date,
            units=units,
            amount_paise=amount_paise,
            natural_key=f"k-{uuid.uuid4().hex}",
        )
    )
    session.commit()


def test_units_match_catches_a_duplicate_whose_amount_differs_by_paise(
    session: Session,
) -> None:
    """The bug this fixes: same trade, amounts 6 paise apart, units identical."""
    _seed(session)

    assert existing_cross_source_transaction(
        session, _AMFI, _DATE, True, _ZERODHA_AMOUNT_PAISE, units=_CAS_UNITS
    )


def test_amount_only_would_have_missed_it(session: Session) -> None:
    """Documents precisely why 113 real transactions slipped through: without
    units, the differing amount means no match."""
    _seed(session)

    assert not existing_cross_source_transaction(
        session, _AMFI, _DATE, True, _ZERODHA_AMOUNT_PAISE
    )


def test_amount_match_still_works_when_units_differ(session: Session) -> None:
    """Dezerv's case must keep working: its units are back-computed from NAV
    and can differ, while its rupee amounts are exact."""
    _seed(session)

    assert existing_cross_source_transaction(
        session, _AMFI, _DATE, True, _CAS_AMOUNT_PAISE, units=Decimal("380.1234")
    )


def test_amount_match_still_works_with_no_units_supplied(session: Session) -> None:
    _seed(session)

    assert existing_cross_source_transaction(session, _AMFI, _DATE, True, _CAS_AMOUNT_PAISE)


def test_matching_units_on_a_different_date_is_not_a_duplicate(session: Session) -> None:
    _seed(session)

    assert not existing_cross_source_transaction(
        session, _AMFI, date(2021, 11, 8), True, _ZERODHA_AMOUNT_PAISE, units=_CAS_UNITS
    )


def test_matching_units_in_a_different_fund_is_not_a_duplicate(session: Session) -> None:
    _seed(session)

    assert not existing_cross_source_transaction(
        session, "999999", _DATE, True, _ZERODHA_AMOUNT_PAISE, units=_CAS_UNITS
    )


def test_a_buy_never_matches_a_sell(session: Session) -> None:
    _seed(session, txn_type=TransactionType.REDEMPTION, units=-_CAS_UNITS)

    assert not existing_cross_source_transaction(
        session, _AMFI, _DATE, True, _ZERODHA_AMOUNT_PAISE, units=_CAS_UNITS
    )


def test_sell_matches_on_units_despite_opposite_sign_storage(session: Session) -> None:
    """Redemption units are stored negative; the caller passes its own signed
    value. Direction is already pinned by txn_type, so magnitudes are compared
    -- otherwise a sell could never match."""
    _seed(session, txn_type=TransactionType.REDEMPTION, units=-_CAS_UNITS)

    assert existing_cross_source_transaction(
        session, _AMFI, _DATE, False, -_ZERODHA_AMOUNT_PAISE, units=-_CAS_UNITS
    )


def test_zero_units_falls_back_to_amount_only(session: Session) -> None:
    """Tax/fee lines carry no units -- matching on 0 would collide with every
    other zero-unit row on the same day."""
    _seed(session, units=Decimal("0"))

    assert not existing_cross_source_transaction(
        session, _AMFI, _DATE, True, _ZERODHA_AMOUNT_PAISE, units=Decimal("0")
    )
    assert existing_cross_source_transaction(
        session, _AMFI, _DATE, True, _CAS_AMOUNT_PAISE, units=Decimal("0")
    )


def test_purchase_sip_counts_as_buy_equivalent(session: Session) -> None:
    """A CAS labels recurring installments PURCHASE_SIP while a tradebook only
    knows buy/sell."""
    _seed(session, txn_type=TransactionType.PURCHASE_SIP)

    assert existing_cross_source_transaction(
        session, _AMFI, _DATE, True, _ZERODHA_AMOUNT_PAISE, units=_CAS_UNITS
    )
