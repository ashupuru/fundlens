import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from fundlens.compute import metrics, service
from fundlens.db.models import (
    NavHistory,
    NavSource,
    RawCASUpload,
    RawTransaction,
    TransactionType,
    UploadStatus,
)


def _make_upload(session: Session) -> RawCASUpload:
    upload = RawCASUpload(
        file_hash=f"hash-{uuid.uuid4().hex}", parser_version="test", status=UploadStatus.PARSED
    )
    session.add(upload)
    session.flush()
    return upload


def _add_txn(
    session: Session,
    upload_id: str,
    *,
    folio: str = "F1",
    amc: str = "Test AMC",
    scheme_name: str = "Fund A",
    amfi_code: str | None = "111",
    txn_type: TransactionType,
    txn_date: date,
    sequence: int = 0,
    units: Decimal | None,
    nav: Decimal | None = None,
    amount_paise: int | None = None,
) -> None:
    session.add(
        RawTransaction(
            upload_id=upload_id,
            folio=folio,
            amc=amc,
            scheme_name=scheme_name,
            amfi_code=amfi_code,
            txn_type=txn_type,
            txn_date=txn_date,
            sequence=sequence,
            units=units,
            nav=nav,
            amount_paise=amount_paise,
            natural_key=f"key-{uuid.uuid4().hex}",
        )
    )


def _add_nav(session: Session, amfi_code: str, nav_date: date, nav: Decimal) -> None:
    session.add(
        NavHistory(amfi_code=amfi_code, nav_date=nav_date, nav=nav, source=NavSource.AMFI_NAVALL)
    )


def test_still_held_fund_computes_current_value_and_returns(session: Session) -> None:
    upload = _make_upload(session)
    purchase_date = date.today() - timedelta(days=730)
    _add_txn(
        session, upload.id, txn_type=TransactionType.PURCHASE, txn_date=purchase_date,
        units=Decimal("100"), nav=Decimal("50"), amount_paise=500_000,
    )
    session.commit()
    service.rebuild_lots_for_folio(session, "F1")
    _add_nav(session, "111", date.today(), Decimal("75"))
    session.commit()

    all_metrics = metrics.get_all_holding_metrics(session)

    assert len(all_metrics) == 1
    m = all_metrics[0]
    assert m.invested_paise == 500_000
    assert m.current_value_paise == 750_000  # 100 units * 75
    assert m.is_fully_exited is False
    assert m.xirr is not None
    assert m.xirr > 0  # grew from 50 to 75 nav
    assert m.absolute_return_pct == 50.0  # (750000-500000)/500000 * 100
    assert m.less_than_one_year is False


def test_fully_exited_fund_has_zero_current_value_and_realized_xirr(session: Session) -> None:
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, sequence=0, txn_type=TransactionType.PURCHASE,
        txn_date=date(2020, 1, 1), units=Decimal("100"), nav=Decimal("50"), amount_paise=500_000,
    )
    _add_txn(
        session, upload.id, sequence=0, txn_type=TransactionType.REDEMPTION,
        # amount_paise is negative here, matching real data: casparser signs
        # amount the same way as units (verified against a real CAS — see
        # compute/metrics.py's module docstring).
        txn_date=date(2022, 1, 1), units=Decimal("-100"), nav=Decimal("80"), amount_paise=-800_000,
    )
    session.commit()
    service.rebuild_lots_for_folio(session, "F1")

    all_metrics = metrics.get_all_holding_metrics(session)

    assert len(all_metrics) == 1
    m = all_metrics[0]
    assert m.is_fully_exited is True
    assert m.current_value_paise == 0
    assert m.received_paise == 800_000
    assert m.invested_paise == 500_000
    assert m.xirr is not None  # realized XIRR from purchase + full redemption
    assert m.xirr > 0
    assert m.realized_gain_paise == 300_000  # (80-50)*100 units = 3000 rupees = 300,000 paise


def test_still_held_fund_without_nav_data_reports_unavailable(session: Session) -> None:
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, txn_type=TransactionType.PURCHASE, txn_date=date(2022, 1, 1),
        units=Decimal("100"), nav=Decimal("50"), amount_paise=500_000,
    )
    session.commit()
    service.rebuild_lots_for_folio(session, "F1")
    # deliberately no NavHistory row added

    all_metrics = metrics.get_all_holding_metrics(session)

    assert len(all_metrics) == 1
    m = all_metrics[0]
    assert m.current_value_paise is None
    assert m.xirr is None
    assert m.xirr_reason == "nav_unavailable"
    # Regression: unknown current value must not silently read as "0", which
    # would make absolute_return_pct misrepresent "unknown" as "-100% (total
    # loss)" — caught via a live smoke test, not by an earlier unit test.
    assert m.absolute_return_pct is None
    assert m.cagr_pct is None


def test_dividend_reinvest_excluded_from_invested_amount(session: Session) -> None:
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, sequence=0, txn_type=TransactionType.PURCHASE,
        txn_date=date(2022, 1, 1), units=Decimal("100"), nav=Decimal("50"), amount_paise=500_000,
    )
    _add_txn(
        session, upload.id, sequence=1, txn_type=TransactionType.DIVIDEND_REINVEST,
        txn_date=date(2022, 6, 1), units=Decimal("5"), nav=Decimal("55"), amount_paise=27_500,
    )
    session.commit()
    service.rebuild_lots_for_folio(session, "F1")
    _add_nav(session, "111", date.today(), Decimal("60"))
    session.commit()

    all_metrics = metrics.get_all_holding_metrics(session)

    m = all_metrics[0]
    # Only the real PURCHASE counts as invested capital — the reinvested
    # dividend was never external cash.
    assert m.invested_paise == 500_000


def test_realized_gain_reflects_lot_ledger_not_gross_proceeds(session: Session) -> None:
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, sequence=0, txn_type=TransactionType.PURCHASE,
        txn_date=date(2020, 1, 1), units=Decimal("100"), nav=Decimal("50"), amount_paise=500_000,
    )
    _add_txn(
        session, upload.id, sequence=0, txn_type=TransactionType.REDEMPTION,
        txn_date=date(2021, 1, 1), units=Decimal("-40"), nav=Decimal("70"), amount_paise=-280_000,
    )
    session.commit()
    service.rebuild_lots_for_folio(session, "F1")
    _add_nav(session, "111", date.today(), Decimal("80"))
    session.commit()

    all_metrics = metrics.get_all_holding_metrics(session)

    m = all_metrics[0]
    # gain-only: (70-50)*40 = 800 rupees = 80,000 paise — NOT the 280,000
    # paise gross proceeds.
    assert m.realized_gain_paise == 80_000
    assert m.received_paise == 280_000
    assert m.current_value_paise == 480_000  # 60 remaining units * 80 nav = 4800 rupees


def test_get_all_positions_includes_fully_exited_funds(session: Session) -> None:
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, sequence=0, txn_type=TransactionType.PURCHASE,
        txn_date=date(2020, 1, 1), units=Decimal("50"), nav=Decimal("10"),
    )
    _add_txn(
        session, upload.id, sequence=0, txn_type=TransactionType.REDEMPTION,
        txn_date=date(2021, 1, 1), units=Decimal("-50"), nav=Decimal("15"),
    )
    session.commit()

    positions = metrics.get_all_positions(session)

    assert ("F1", "111") in positions


def test_less_than_one_year_flag_set_for_recent_investment(session: Session) -> None:
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, txn_type=TransactionType.PURCHASE,
        txn_date=date.today() - timedelta(days=30),
        units=Decimal("10"), nav=Decimal("100"), amount_paise=1_000_000,
    )
    session.commit()
    service.rebuild_lots_for_folio(session, "F1")
    _add_nav(session, "111", date.today(), Decimal("105"))
    session.commit()

    all_metrics = metrics.get_all_holding_metrics(session)

    assert all_metrics[0].less_than_one_year is True


def test_no_positions_returns_empty_list(session: Session) -> None:
    assert metrics.get_all_holding_metrics(session) == []


def test_portfolio_metrics_with_no_positions_is_degenerate(session: Session) -> None:
    result = metrics.compute_portfolio_metrics(session)
    assert result.xirr is None
    assert result.xirr_reason == "insufficient_flow_variation"
    assert result.invested_paise == 0


def test_portfolio_metrics_aggregates_across_multiple_funds(session: Session) -> None:
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, scheme_name="Fund A", amfi_code="111",
        txn_type=TransactionType.PURCHASE, txn_date=date.today() - timedelta(days=730),
        units=Decimal("100"), nav=Decimal("50"), amount_paise=500_000,
    )
    _add_txn(
        session, upload.id, scheme_name="Fund B", amfi_code="222",
        txn_type=TransactionType.PURCHASE, txn_date=date.today() - timedelta(days=365),
        units=Decimal("50"), nav=Decimal("100"), amount_paise=500_000,
    )
    session.commit()
    service.rebuild_lots_for_folio(session, "F1")
    _add_nav(session, "111", date.today(), Decimal("75"))
    _add_nav(session, "222", date.today(), Decimal("110"))
    session.commit()

    result = metrics.compute_portfolio_metrics(session)

    assert result.invested_paise == 1_000_000  # 500,000 + 500,000
    assert result.current_value_paise == 750_000 + 550_000  # (100*75 + 50*110) rupees, in paise
    assert result.xirr is not None
    assert result.positions_missing_nav == 0


def test_switch_between_two_funds_cancels_exactly_in_portfolio_xirr(session: Session) -> None:
    """A SWITCH_OUT from fund A and its matching SWITCH_IN to fund B, same
    date and same amount, must cancel out in the cash-flow list used for
    XIRR — algebraically, two opposite flows discounted at the same date
    sum to zero for any rate, so the portfolio XIRR here must equal exactly
    what a simple two-flow series (original purchase -> final value) would
    give, as if the switch had never happened. Verified against the
    closed-form rate for that reduced two-flow series, not just "some
    plausible number"."""
    upload = _make_upload(session)
    purchase_date = date.today() - timedelta(days=400)
    switch_date = date.today() - timedelta(days=200)
    today = date.today()

    _add_txn(
        session, upload.id, scheme_name="Fund A", amfi_code="111",
        txn_type=TransactionType.PURCHASE, txn_date=purchase_date,
        units=Decimal("100"), nav=Decimal("50"), amount_paise=500_000,
    )
    _add_txn(
        session, upload.id, scheme_name="Fund A", amfi_code="111", sequence=1,
        txn_type=TransactionType.SWITCH_OUT, txn_date=switch_date,
        # negative, matching real data's amount/units sign convention for
        # outflow-type transactions.
        units=Decimal("-100"), nav=Decimal("60"), amount_paise=-600_000,
    )
    _add_txn(
        session, upload.id, scheme_name="Fund B", amfi_code="222",
        txn_type=TransactionType.SWITCH_IN, txn_date=switch_date,
        units=Decimal("60"), nav=Decimal("100"), amount_paise=600_000,
    )
    session.commit()
    service.rebuild_lots_for_folio(session, "F1")
    _add_nav(session, "222", today, Decimal("110"))
    session.commit()

    result = metrics.compute_portfolio_metrics(session)

    current_value_paise = 660_000  # 60 units * 110 nav = 6600 rupees
    assert result.current_value_paise == current_value_paise

    # Reduced series: -500,000 at purchase_date, +6,600,000 at today — the
    # switch pair (+600,000/-600,000 on switch_date) is algebraically inert.
    days = (today - purchase_date).days
    expected_rate = (current_value_paise / 500_000) ** (365 / days) - 1
    assert result.xirr == pytest.approx(expected_rate, rel=1e-9)
