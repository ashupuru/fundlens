import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from fundlens.compute import service
from fundlens.compute.capital_gains import financial_year_bounds, get_capital_gains_report
from fundlens.db.models import (
    RawCASUpload,
    RawTransaction,
    SchemeMetadata,
    TransactionType,
    UploadStatus,
)
from fundlens.tax.asset_class import TaxAssetClass
from fundlens.tax.classification import GainType


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
    scheme_name: str = "Fund A",
    amfi_code: str | None = "111",
    isin: str | None = "ISIN111",
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
            amc="Test AMC",
            scheme_name=scheme_name,
            amfi_code=amfi_code,
            isin=isin,
            txn_type=txn_type,
            txn_date=txn_date,
            sequence=sequence,
            units=units,
            nav=nav,
            amount_paise=amount_paise,
            natural_key=f"key-{uuid.uuid4().hex}",
        )
    )


def _add_metadata(
    session: Session, amfi_code: str, category_raw: str, tax_asset_class: TaxAssetClass
) -> None:
    session.add(
        SchemeMetadata(
            amfi_code=amfi_code, category_raw=category_raw, tax_asset_class=tax_asset_class
        )
    )


def test_financial_year_bounds() -> None:
    start, end = financial_year_bounds("2024-25")
    assert start == date(2024, 4, 1)
    assert end == date(2025, 3, 31)


def test_no_gains_in_fy_produces_empty_report(session: Session) -> None:
    report = get_capital_gains_report(session, "2024-25")
    assert report.lines == []
    assert report.total_stcg_paise == 0
    assert report.total_ltcg_paise == 0


def test_equity_fund_held_over_a_year_is_ltcg(session: Session) -> None:
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, sequence=0, txn_type=TransactionType.PURCHASE,
        txn_date=date(2022, 1, 1), units=Decimal("100"), nav=Decimal("50"), amount_paise=500_000,
    )
    _add_txn(
        session, upload.id, sequence=0, txn_type=TransactionType.REDEMPTION,
        txn_date=date(2024, 6, 1), units=Decimal("-100"), nav=Decimal("80"), amount_paise=-800_000,
    )
    _add_metadata(
        session, "111", "Open Ended Schemes(Equity Scheme - Large Cap Fund)", TaxAssetClass.EQUITY
    )
    session.commit()
    service.rebuild_lots_for_folio(session, "F1")

    report = get_capital_gains_report(session, "2024-25")

    assert len(report.lines) == 1
    line = report.lines[0]
    assert line.gain_type == GainType.LTCG
    assert line.tax_asset_class == TaxAssetClass.EQUITY
    assert line.sale_value_paise == 800_000  # 100 units * 80 rupees = 8000 rupees
    assert line.cost_of_acquisition_paise == 500_000  # 100 units * 50 rupees = 5000 rupees
    assert line.gain_paise == 300_000
    assert report.total_ltcg_paise == 300_000
    assert report.total_stcg_paise == 0


def test_equity_fund_held_under_a_year_is_stcg(session: Session) -> None:
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, sequence=0, txn_type=TransactionType.PURCHASE,
        txn_date=date(2024, 1, 1), units=Decimal("100"), nav=Decimal("50"), amount_paise=500_000,
    )
    _add_txn(
        session, upload.id, sequence=0, txn_type=TransactionType.REDEMPTION,
        txn_date=date(2024, 6, 1), units=Decimal("-100"), nav=Decimal("60"), amount_paise=-600_000,
    )
    _add_metadata(
        session, "111", "Open Ended Schemes(Equity Scheme - Large Cap Fund)", TaxAssetClass.EQUITY
    )
    session.commit()
    service.rebuild_lots_for_folio(session, "F1")

    report = get_capital_gains_report(session, "2024-25")

    assert report.lines[0].gain_type == GainType.STCG
    assert report.total_stcg_paise == 100_000  # (60-50) rupees * 100 units = 1000 rupees


def test_debt_fund_purchased_before_2023_uses_36_month_threshold(session: Session) -> None:
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, sequence=0, txn_type=TransactionType.PURCHASE,
        txn_date=date(2020, 1, 1), units=Decimal("100"), nav=Decimal("50"), amount_paise=500_000,
    )
    _add_txn(
        session, upload.id, sequence=0, txn_type=TransactionType.REDEMPTION,
        txn_date=date(2024, 6, 1), units=Decimal("-100"), nav=Decimal("70"), amount_paise=-700_000,
    )
    _add_metadata(
        session, "111", "Open Ended Schemes(Debt Scheme - Liquid Fund)", TaxAssetClass.DEBT
    )
    session.commit()
    service.rebuild_lots_for_folio(session, "F1")

    report = get_capital_gains_report(session, "2024-25")

    assert report.lines[0].gain_type == GainType.LTCG  # held > 36 months, pre-2023 purchase


def test_debt_fund_purchased_after_2023_cutoff_is_always_stcg(session: Session) -> None:
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, sequence=0, txn_type=TransactionType.PURCHASE,
        txn_date=date(2023, 5, 1), units=Decimal("100"), nav=Decimal("50"), amount_paise=500_000,
    )
    _add_txn(
        session, upload.id, sequence=0, txn_type=TransactionType.REDEMPTION,
        txn_date=date(2024, 6, 1), units=Decimal("-100"), nav=Decimal("70"), amount_paise=-700_000,
    )
    _add_metadata(
        session, "111", "Open Ended Schemes(Debt Scheme - Liquid Fund)", TaxAssetClass.DEBT
    )
    session.commit()
    service.rebuild_lots_for_folio(session, "F1")

    report = get_capital_gains_report(session, "2024-25")

    assert report.lines[0].gain_type == GainType.STCG  # "specified mutual fund" rule


def test_missing_scheme_metadata_is_unclassified_not_guessed(session: Session) -> None:
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, sequence=0, txn_type=TransactionType.PURCHASE,
        txn_date=date(2022, 1, 1), units=Decimal("100"), nav=Decimal("50"), amount_paise=500_000,
    )
    _add_txn(
        session, upload.id, sequence=0, txn_type=TransactionType.REDEMPTION,
        txn_date=date(2024, 6, 1), units=Decimal("-100"), nav=Decimal("80"), amount_paise=-800_000,
    )
    # deliberately no SchemeMetadata row added
    session.commit()
    service.rebuild_lots_for_folio(session, "F1")

    report = get_capital_gains_report(session, "2024-25")

    assert report.lines[0].gain_type == GainType.UNCLASSIFIED
    assert report.lines[0].tax_asset_class is None
    assert report.schemes_missing_metadata == 1
    assert report.total_unclassified_paise == 300_000
    assert report.total_stcg_paise == 0
    assert report.total_ltcg_paise == 0


def test_needs_review_category_is_unclassified(session: Session) -> None:
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, sequence=0, txn_type=TransactionType.PURCHASE,
        txn_date=date(2022, 1, 1), units=Decimal("100"), nav=Decimal("50"), amount_paise=500_000,
    )
    _add_txn(
        session, upload.id, sequence=0, txn_type=TransactionType.REDEMPTION,
        txn_date=date(2024, 6, 1), units=Decimal("-100"), nav=Decimal("80"), amount_paise=-800_000,
    )
    _add_metadata(
        session, "111",
        "Open Ended Schemes(Hybrid Scheme - Balanced Hybrid Fund)",
        TaxAssetClass.NEEDS_REVIEW,
    )
    session.commit()
    service.rebuild_lots_for_folio(session, "F1")

    report = get_capital_gains_report(session, "2024-25")

    assert report.lines[0].gain_type == GainType.UNCLASSIFIED


def test_gain_outside_fy_range_is_excluded(session: Session) -> None:
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, sequence=0, txn_type=TransactionType.PURCHASE,
        txn_date=date(2020, 1, 1), units=Decimal("100"), nav=Decimal("50"), amount_paise=500_000,
    )
    _add_txn(
        session, upload.id, sequence=0, txn_type=TransactionType.REDEMPTION,
        txn_date=date(2023, 6, 1), units=Decimal("-100"), nav=Decimal("80"), amount_paise=-800_000,
    )
    _add_metadata(
        session, "111", "Open Ended Schemes(Equity Scheme - Large Cap Fund)", TaxAssetClass.EQUITY
    )
    session.commit()
    service.rebuild_lots_for_folio(session, "F1")

    # Sale happened in FY 2023-24, not FY 2024-25.
    report = get_capital_gains_report(session, "2024-25")

    assert report.lines == []
