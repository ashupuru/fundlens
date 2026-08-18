"""compute_yearly_report tests. The "this year" / "since inception" XIRR
fixtures below are hand-computable in closed form: a single buy-and-hold
position with no transactions inside the reporting window collapses the
XIRR solve to a plain 2-cash-flow compound-return formula,
`rate = (closing/opening)^(365/days) - 1`, computed independently of
compute_xirr's solver and asserted within a tight tolerance -- the same
"cross-check against an independent formula" gate used for goal planning's
required-SIP test.
"""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from fundlens.allocation.service import set_risk_profile
from fundlens.compute import service as lot_service
from fundlens.compute.yearly_report import compute_yearly_report
from fundlens.db.models import (
    GrowwFundMetadata,
    NavHistory,
    NavSource,
    Persona,
    RawCASUpload,
    RawTransaction,
    TransactionType,
    UploadStatus,
)

_TOLERANCE = 1e-4


def _make_upload(session: Session) -> RawCASUpload:
    upload = RawCASUpload(
        file_hash=f"hash-{uuid.uuid4().hex}", parser_version="test", status=UploadStatus.PARSED
    )
    session.add(upload)
    session.flush()
    return upload


def _add_purchase(
    session: Session,
    upload_id: str,
    *,
    folio: str = "F1",
    scheme_name: str,
    amfi_code: str,
    isin: str,
    txn_date: date,
    units: Decimal,
    nav: Decimal,
    sequence: int = 0,
) -> None:
    session.add(
        RawTransaction(
            upload_id=upload_id,
            folio=folio,
            amc="Test AMC",
            scheme_name=scheme_name,
            amfi_code=amfi_code,
            isin=isin,
            txn_type=TransactionType.PURCHASE,
            txn_date=txn_date,
            sequence=sequence,
            units=units,
            nav=nav,
            amount_paise=int(units * nav * 100),
            natural_key=f"key-{uuid.uuid4().hex}",
        )
    )


def _add_nav(session: Session, amfi_code: str, nav_date: date, nav: str) -> None:
    session.add(
        NavHistory(
            amfi_code=amfi_code, nav_date=nav_date, nav=Decimal(nav), source=NavSource.MANUAL
        )
    )


def _build_single_fund_fixture(session: Session) -> None:
    """100 units bought 2022-01-01 @ NAV 100 (Rs 10,000), held unchanged.
    NAV 150 at FY 2023-24's start (2023-04-01, Rs 15,000), NAV 180 at its
    end (2024-03-31, Rs 18,000)."""
    upload = _make_upload(session)
    _add_purchase(
        session, upload.id, scheme_name="Fund A", amfi_code="111", isin="ISIN111",
        txn_date=date(2022, 1, 1), units=Decimal("100"), nav=Decimal("100"),
    )
    _add_nav(session, "111", date(2022, 1, 1), "100")
    _add_nav(session, "111", date(2023, 4, 1), "150")
    _add_nav(session, "111", date(2024, 3, 31), "180")
    session.commit()
    lot_service.rebuild_all_lots(session)


def test_opening_and_closing_value(session: Session) -> None:
    _build_single_fund_fixture(session)

    report = compute_yearly_report(session, "2023-24")

    assert report.fy_start == date(2023, 4, 1)
    assert report.report_end == date(2024, 3, 31)
    assert report.opening_value_paise == 1_500_000  # 100 units * NAV 150
    assert report.closing_value_paise == 1_800_000  # 100 units * NAV 180


def test_this_year_xirr_matches_hand_computed_compound_return(session: Session) -> None:
    _build_single_fund_fixture(session)

    report = compute_yearly_report(session, "2023-24")

    days = (date(2024, 3, 31) - date(2023, 4, 1)).days
    expected = (18000 / 15000) ** (365.0 / days) - 1
    assert report.portfolio_xirr_this_year is not None
    assert abs(report.portfolio_xirr_this_year - expected) < _TOLERANCE


def test_since_inception_xirr_matches_hand_computed_compound_return(session: Session) -> None:
    _build_single_fund_fixture(session)

    report = compute_yearly_report(session, "2023-24")

    days = (date(2024, 3, 31) - date(2022, 1, 1)).days
    expected = (18000 / 10000) ** (365.0 / days) - 1
    assert report.portfolio_xirr_since_inception is not None
    assert abs(report.portfolio_xirr_since_inception - expected) < _TOLERANCE


def test_since_inception_xirr_excludes_transactions_after_report_end(session: Session) -> None:
    _build_single_fund_fixture(session)
    upload = _make_upload(session)
    # A purchase dated well into FY 2024-25 -- must not affect a report for
    # FY 2023-24, whose report_end is 2024-03-31.
    _add_purchase(
        session, upload.id, scheme_name="Fund A", amfi_code="111", isin="ISIN111",
        txn_date=date(2024, 6, 1), units=Decimal("50"), nav=Decimal("200"), sequence=1,
    )
    session.commit()
    lot_service.rebuild_all_lots(session)

    report = compute_yearly_report(session, "2023-24")

    days = (date(2024, 3, 31) - date(2022, 1, 1)).days
    expected = (18000 / 10000) ** (365.0 / days) - 1
    assert report.portfolio_xirr_since_inception is not None
    assert abs(report.portfolio_xirr_since_inception - expected) < _TOLERANCE
    assert report.closing_value_paise == 1_800_000  # unaffected by the later purchase


def test_best_and_worst_funds_ranked_by_this_years_return(session: Session) -> None:
    upload = _make_upload(session)
    # Fund A: 150 -> 300 (doubles, best). Fund B: 150 -> 120 (loses, worst).
    _add_purchase(
        session, upload.id, scheme_name="Fund A", amfi_code="111", isin="ISIN111",
        txn_date=date(2022, 1, 1), units=Decimal("100"), nav=Decimal("100"),
    )
    _add_purchase(
        session, upload.id, scheme_name="Fund B", amfi_code="222", isin="ISIN222",
        txn_date=date(2022, 1, 1), units=Decimal("100"), nav=Decimal("100"),
    )
    for amfi_code, start_nav, end_nav in (("111", "150", "300"), ("222", "150", "120")):
        _add_nav(session, amfi_code, date(2023, 4, 1), start_nav)
        _add_nav(session, amfi_code, date(2024, 3, 31), end_nav)
    session.commit()
    lot_service.rebuild_all_lots(session)

    report = compute_yearly_report(session, "2023-24")

    assert len(report.best_funds) == 1
    assert len(report.worst_funds) == 1
    assert report.best_funds[0].scheme_name == "Fund A"
    assert report.worst_funds[0].scheme_name == "Fund B"
    assert report.best_funds[0].xirr is not None
    assert report.worst_funds[0].xirr is not None
    assert report.best_funds[0].xirr > report.worst_funds[0].xirr


def test_fee_estimate_excludes_holdings_missing_expense_ratio(session: Session) -> None:
    upload = _make_upload(session)
    _add_purchase(
        session, upload.id, scheme_name="Fund A", amfi_code="111", isin="ISIN111",
        txn_date=date(2022, 1, 1), units=Decimal("100"), nav=Decimal("100"),
    )
    _add_purchase(
        session, upload.id, scheme_name="Fund B", amfi_code="222", isin="ISIN222",
        txn_date=date(2022, 1, 1), units=Decimal("100"), nav=Decimal("100"),
    )
    _add_nav(session, "111", date(2024, 3, 31), "100")
    _add_nav(session, "222", date(2024, 3, 31), "100")
    session.add(
        GrowwFundMetadata(amfi_code="111", scheme_name="Fund A", expense_ratio=Decimal("1.00"))
    )
    # Fund B deliberately has no GrowwFundMetadata row -- excluded, not fabricated.
    session.commit()
    lot_service.rebuild_all_lots(session)

    report = compute_yearly_report(session, "2023-24")

    # Fund A: Rs 10,000 current value * 1% = Rs 100/yr = 10,000 paise.
    assert report.estimated_annual_fee_paise == 10_000
    assert report.fee_funds_excluded == 1


def test_allocation_drift_none_without_risk_profile(session: Session) -> None:
    _build_single_fund_fixture(session)

    report = compute_yearly_report(session, "2023-24")

    assert report.allocation_drift is None


def test_allocation_drift_present_with_risk_profile(session: Session) -> None:
    _build_single_fund_fixture(session)
    set_risk_profile(session, Persona.BALANCED)

    report = compute_yearly_report(session, "2023-24")

    assert report.allocation_drift is not None
    assert report.allocation_drift.persona == Persona.BALANCED


def test_capital_gains_section_matches_direct_call(session: Session) -> None:
    from fundlens.compute.capital_gains import get_capital_gains_report

    _build_single_fund_fixture(session)

    report = compute_yearly_report(session, "2023-24")
    direct = get_capital_gains_report(session, "2023-24")

    assert report.capital_gains == direct


def test_empty_portfolio_does_not_crash(session: Session) -> None:
    report = compute_yearly_report(session, "2023-24")

    assert report.opening_value_paise == 0
    assert report.closing_value_paise == 0
    assert report.best_funds == []
    assert report.worst_funds == []
