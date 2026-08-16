import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from fundlens.assumptions.service import AssumptionKey, create_version, get_current_version
from fundlens.compute import service
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
from fundlens.tax.friction import compute_tax_friction


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
    pan: str | None = "ABCDE1234F",
    amfi_code: str = "111",
    isin: str = "ISIN111",
    txn_date: date,
    units: Decimal,
    cost_per_unit: Decimal,
) -> None:
    session.add(
        RawTransaction(
            upload_id=upload_id,
            folio="F1",
            pan=pan,
            amc="Test AMC",
            scheme_name="Fund A",
            amfi_code=amfi_code,
            isin=isin,
            txn_type=TransactionType.PURCHASE,
            txn_date=txn_date,
            sequence=0,
            units=units,
            nav=cost_per_unit,
            amount_paise=int(units * cost_per_unit * 100),
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


def test_no_open_position_is_not_computable(session: Session) -> None:
    result = compute_tax_friction(session, "F1", "ISIN111")
    assert result.computable is False
    assert result.reason == "no_open_position"


def test_equity_stcg_taxed_at_20_percent(session: Session) -> None:
    upload = _make_upload(session)
    recent = date.today() - timedelta(days=100)  # held < 365 days -> STCG
    _add_purchase(
        session, upload.id, txn_date=recent, units=Decimal("100"), cost_per_unit=Decimal("50")
    )
    _add_metadata(
        session, "111", "Open Ended Schemes(Equity Scheme - Large Cap Fund)", TaxAssetClass.EQUITY
    )
    _add_nav(session, "111", date.today(), "80")
    session.commit()
    service.rebuild_all_lots(session)

    result = compute_tax_friction(session, "F1", "ISIN111")

    assert result.computable is True
    assert result.stcg_gain_paise == 300_000  # (80-50)*100 rupees = 3000 rupees
    assert result.ltcg_gain_paise == 0
    assert result.tax_owed_paise == 60_000  # 20% of 300,000 paise = 60,000 paise (Rs 600)


def test_equity_ltcg_fully_within_exemption_headroom_is_untaxed(session: Session) -> None:
    upload = _make_upload(session)
    old = date.today() - timedelta(days=500)  # held > 365 days -> LTCG
    _add_purchase(
        session, upload.id, txn_date=old, units=Decimal("100"), cost_per_unit=Decimal("50")
    )
    _add_metadata(
        session, "111", "Open Ended Schemes(Equity Scheme - Large Cap Fund)", TaxAssetClass.EQUITY
    )
    _add_nav(session, "111", date.today(), "60")  # gain = 10 rupees/unit * 100 = Rs 1,000
    session.commit()
    service.rebuild_all_lots(session)

    result = compute_tax_friction(session, "F1", "ISIN111")

    assert result.computable is True
    assert result.ltcg_gain_paise == 100_000  # Rs 1,000
    assert result.ltcg_exemption_consumed_paise == 100_000  # fully within Rs 1,25,000 headroom
    assert result.taxable_ltcg_paise == 0
    assert result.tax_owed_paise == 0


def test_equity_ltcg_exceeding_exemption_headroom_is_partially_taxed(session: Session) -> None:
    upload = _make_upload(session)
    old = date.today() - timedelta(days=500)
    _add_purchase(
        session,
        upload.id,
        txn_date=old,
        units=Decimal("100"),
        cost_per_unit=Decimal("500"),
    )
    _add_metadata(
        session, "111", "Open Ended Schemes(Equity Scheme - Large Cap Fund)", TaxAssetClass.EQUITY
    )
    # Gain = (2000-500)*100 = Rs 1,50,000, exceeding the Rs 1,25,000 default
    # headroom by Rs 25,000 -> taxable_ltcg = Rs 25,000 = 2,500,000 paise.
    _add_nav(session, "111", date.today(), "2000")
    session.commit()
    service.rebuild_all_lots(session)

    result = compute_tax_friction(session, "F1", "ISIN111")

    assert result.ltcg_gain_paise == 15_000_000  # Rs 1,50,000
    assert result.ltcg_exemption_consumed_paise == 12_500_000  # full Rs 1,25,000 headroom
    assert result.taxable_ltcg_paise == 2_500_000  # Rs 25,000
    assert result.tax_owed_paise == 312_500  # 12.5% of 2,500,000 = 312,500 paise (Rs 3,125)


def test_debt_without_configured_marginal_rate_is_not_computable(session: Session) -> None:
    upload = _make_upload(session)
    recent = date.today() - timedelta(days=100)
    _add_purchase(
        session, upload.id, txn_date=recent, units=Decimal("100"), cost_per_unit=Decimal("50")
    )
    _add_metadata(
        session, "111", "Open Ended Schemes(Debt Scheme - Liquid Fund)", TaxAssetClass.DEBT
    )
    _add_nav(session, "111", date.today(), "60")
    session.commit()
    service.rebuild_all_lots(session)

    result = compute_tax_friction(session, "F1", "ISIN111")

    assert result.computable is False
    assert result.reason == "debt_marginal_tax_rate_not_configured"
    assert result.tax_owed_paise == 0


def test_debt_with_configured_marginal_rate_is_computed(session: Session) -> None:
    get_current_version(session)  # seed defaults
    create_version(
        session, "with debt rate", {AssumptionKey.DEBT_MARGINAL_TAX_RATE_PCT.value: "30"}
    )

    upload = _make_upload(session)
    recent = date.today() - timedelta(days=100)
    _add_purchase(
        session, upload.id, txn_date=recent, units=Decimal("100"), cost_per_unit=Decimal("50")
    )
    _add_metadata(
        session, "111", "Open Ended Schemes(Debt Scheme - Liquid Fund)", TaxAssetClass.DEBT
    )
    _add_nav(session, "111", date.today(), "60")
    session.commit()
    service.rebuild_all_lots(session)

    result = compute_tax_friction(session, "F1", "ISIN111")

    assert result.computable is True
    assert result.stcg_gain_paise == 100_000  # (60-50)*100 = Rs 1,000
    assert result.tax_owed_paise == 30_000  # 30% of 100,000 = 30,000 paise (Rs 300)


def test_needs_review_category_is_not_computable(session: Session) -> None:
    upload = _make_upload(session)
    _add_purchase(
        session,
        upload.id,
        txn_date=date.today() - timedelta(days=100),
        units=Decimal("100"),
        cost_per_unit=Decimal("50"),
    )
    _add_metadata(
        session,
        "111",
        "Open Ended Schemes(Hybrid Scheme - Balanced Hybrid Fund)",
        TaxAssetClass.NEEDS_REVIEW,
    )
    _add_nav(session, "111", date.today(), "60")
    session.commit()
    service.rebuild_all_lots(session)

    result = compute_tax_friction(session, "F1", "ISIN111")

    assert result.computable is False
    assert result.reason == "scheme_category_needs_review"


def test_exit_load_never_modeled(session: Session) -> None:
    upload = _make_upload(session)
    _add_purchase(
        session,
        upload.id,
        txn_date=date.today() - timedelta(days=100),
        units=Decimal("100"),
        cost_per_unit=Decimal("50"),
    )
    _add_metadata(
        session, "111", "Open Ended Schemes(Equity Scheme - Large Cap Fund)", TaxAssetClass.EQUITY
    )
    _add_nav(session, "111", date.today(), "60")
    session.commit()
    service.rebuild_all_lots(session)

    result = compute_tax_friction(session, "F1", "ISIN111")

    assert result.exit_load_modeled is False
