import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from fundlens.compute import service
from fundlens.compute.capital_gains import financial_year_bounds, get_capital_gains_report
from fundlens.db.models import (
    NavHistory,
    NavSource,
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
    pan: str | None = "ABCDE1234F",
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
            pan=pan,
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


def _add_nav(session: Session, amfi_code: str, nav_date: date, nav: str) -> None:
    session.add(
        NavHistory(
            amfi_code=amfi_code, nav_date=nav_date, nav=Decimal(nav), source=NavSource.MANUAL
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


def test_equity_grandfathering_uses_higher_of_actual_cost_and_capped_fmv(session: Session) -> None:
    # Bought 100 units @ Rs 20/unit (Rs 2,000) on 2016-01-01 (pre-cutoff).
    # FMV as of 2018-01-31 = Rs 50/unit. Sold @ Rs 80/unit on 2024-06-01.
    # Grandfathered cost = higher of (actual 20, lower of (FMV 50, sale 80))
    # = higher of (20, 50) = 50/unit -> cost basis Rs 5,000, gain Rs 3,000.
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, sequence=0, txn_type=TransactionType.PURCHASE,
        txn_date=date(2016, 1, 1), units=Decimal("100"), nav=Decimal("20"), amount_paise=200_000,
    )
    _add_txn(
        session, upload.id, sequence=0, txn_type=TransactionType.REDEMPTION,
        txn_date=date(2024, 6, 1), units=Decimal("-100"), nav=Decimal("80"), amount_paise=-800_000,
    )
    _add_metadata(
        session, "111", "Open Ended Schemes(Equity Scheme - Large Cap Fund)", TaxAssetClass.EQUITY
    )
    _add_nav(session, "111", date(2018, 1, 31), "50")
    session.commit()
    service.rebuild_lots_for_folio(session, "F1")

    report = get_capital_gains_report(session, "2024-25")

    line = report.lines[0]
    assert line.gain_type == GainType.LTCG
    assert line.grandfathering_applied is True
    assert line.grandfathering_note is None
    assert line.cost_of_acquisition_paise == 500_000  # 100 * 50 rupees
    assert line.gain_paise == 300_000  # 8000 - 5000 rupees


def test_equity_grandfathering_capped_at_sale_price_when_fmv_exceeds_it(session: Session) -> None:
    # FMV (Rs 100) > sale price (Rs 80) -> capped cost = lower of (FMV 100,
    # sale 80) = 80/unit, still higher than actual cost (Rs 20) -> cost
    # basis = 80/unit = Rs 8,000, gain = 0 (no phantom loss from an FMV
    # spike, and no artificial gain either).
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, sequence=0, txn_type=TransactionType.PURCHASE,
        txn_date=date(2016, 1, 1), units=Decimal("100"), nav=Decimal("20"), amount_paise=200_000,
    )
    _add_txn(
        session, upload.id, sequence=0, txn_type=TransactionType.REDEMPTION,
        txn_date=date(2024, 6, 1), units=Decimal("-100"), nav=Decimal("80"), amount_paise=-800_000,
    )
    _add_metadata(
        session, "111", "Open Ended Schemes(Equity Scheme - Large Cap Fund)", TaxAssetClass.EQUITY
    )
    _add_nav(session, "111", date(2018, 1, 31), "100")
    session.commit()
    service.rebuild_lots_for_folio(session, "F1")

    line = get_capital_gains_report(session, "2024-25").lines[0]

    assert line.cost_of_acquisition_paise == 800_000  # 100 * 80 rupees (capped)
    assert line.gain_paise == 0


def test_equity_grandfathering_not_applied_when_purchased_after_cutoff(session: Session) -> None:
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, sequence=0, txn_type=TransactionType.PURCHASE,
        txn_date=date(2020, 1, 1), units=Decimal("100"), nav=Decimal("50"), amount_paise=500_000,
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

    line = get_capital_gains_report(session, "2024-25").lines[0]

    assert line.grandfathering_applied is False
    assert line.grandfathering_note is None
    assert line.cost_of_acquisition_paise == 500_000


def test_equity_grandfathering_flags_missing_fmv_data_conservatively(session: Session) -> None:
    # Purchased pre-cutoff, but no NavHistory row exists on/before the
    # cutoff date -> can't compute the FMV floor. Falls back to actual cost
    # (the higher-tax, conservative direction) and is flagged, not silently
    # treated as "no grandfathering needed."
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, sequence=0, txn_type=TransactionType.PURCHASE,
        txn_date=date(2016, 1, 1), units=Decimal("100"), nav=Decimal("20"), amount_paise=200_000,
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

    line = get_capital_gains_report(session, "2024-25").lines[0]

    assert line.grandfathering_applied is False
    assert line.grandfathering_note == "grandfathering_data_unavailable"
    assert line.cost_of_acquisition_paise == 200_000  # actual cost, unadjusted


def test_ltcg_exemption_consumed_first_realized_first_consumed_within_pan(
    session: Session,
) -> None:
    # Same PAN, two equity LTCG sales in the same FY: Rs 1,00,000 gain on
    # 2024-05-01, then Rs 50,000 gain on 2024-06-01 -- Rs 1,50,000 total,
    # exceeding the default Rs 1,25,000/FY headroom by Rs 25,000. First
    # sale consumes 1,00,000 (fully exempt, taxable=0), leaving Rs 25,000
    # headroom; second sale consumes that remaining 25,000 of its 50,000
    # gain, taxable = 25,000.
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, sequence=0, txn_type=TransactionType.PURCHASE,
        txn_date=date(2020, 1, 1), units=Decimal("100"), nav=Decimal("500"),
        amount_paise=5_000_000,
    )
    _add_txn(
        session, upload.id, sequence=1, txn_type=TransactionType.REDEMPTION,
        txn_date=date(2024, 5, 1), units=Decimal("-100"), nav=Decimal("1500"),
        amount_paise=-15_000_000,
    )
    _add_txn(
        session, upload.id, scheme_name="Fund B", amfi_code="222", isin="ISIN222", sequence=0,
        txn_type=TransactionType.PURCHASE, txn_date=date(2020, 1, 1), units=Decimal("100"),
        nav=Decimal("500"), amount_paise=5_000_000,
    )
    _add_txn(
        session, upload.id, scheme_name="Fund B", amfi_code="222", isin="ISIN222", sequence=1,
        txn_type=TransactionType.REDEMPTION, txn_date=date(2024, 6, 1), units=Decimal("-100"),
        nav=Decimal("1000"), amount_paise=-10_000_000,
    )
    _add_metadata(
        session, "111", "Open Ended Schemes(Equity Scheme - Large Cap Fund)", TaxAssetClass.EQUITY
    )
    _add_metadata(
        session, "222", "Open Ended Schemes(Equity Scheme - Large Cap Fund)", TaxAssetClass.EQUITY
    )
    session.commit()
    service.rebuild_all_lots(session)

    report = get_capital_gains_report(session, "2024-25")
    lines_by_scheme = {line.scheme_name: line for line in report.lines}

    first = lines_by_scheme["Fund A"]
    assert first.gain_paise == 10_000_000  # Rs 1,00,000
    assert first.ltcg_exemption_consumed_paise == 10_000_000
    assert first.taxable_gain_paise == 0

    second = lines_by_scheme["Fund B"]
    assert second.gain_paise == 5_000_000  # Rs 50,000
    assert second.ltcg_exemption_consumed_paise == 2_500_000  # remaining Rs 25,000 headroom
    assert second.taxable_gain_paise == 2_500_000  # Rs 25,000 taxable

    assert report.total_ltcg_exemption_used_paise == 12_500_000  # full Rs 1,25,000 headroom used
    assert report.total_taxable_gain_paise == 2_500_000


def test_ltcg_exemption_not_consumed_by_stcg_or_debt_gains(session: Session) -> None:
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, sequence=0, txn_type=TransactionType.PURCHASE,
        txn_date=date(2024, 1, 1), units=Decimal("100"), nav=Decimal("50"), amount_paise=500_000,
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

    line = get_capital_gains_report(session, "2024-25").lines[0]

    assert line.gain_type == GainType.STCG
    assert line.ltcg_exemption_consumed_paise == 0
    assert line.taxable_gain_paise == line.gain_paise


def test_ltcg_exemption_flags_missing_pan_and_treats_as_fully_taxable(session: Session) -> None:
    upload = _make_upload(session)
    _add_txn(
        session, upload.id, pan=None, sequence=0, txn_type=TransactionType.PURCHASE,
        txn_date=date(2020, 1, 1), units=Decimal("100"), nav=Decimal("50"), amount_paise=500_000,
    )
    _add_txn(
        session, upload.id, pan=None, sequence=1, txn_type=TransactionType.REDEMPTION,
        txn_date=date(2024, 6, 1), units=Decimal("-100"), nav=Decimal("80"), amount_paise=-800_000,
    )
    _add_metadata(
        session, "111", "Open Ended Schemes(Equity Scheme - Large Cap Fund)", TaxAssetClass.EQUITY
    )
    session.commit()
    service.rebuild_lots_for_folio(session, "F1")

    line = get_capital_gains_report(session, "2024-25").lines[0]

    assert line.pan is None
    assert line.ltcg_exemption_consumed_paise == 0
    assert line.taxable_gain_paise == line.gain_paise
    assert line.exemption_note == "no_pan_on_transaction"
