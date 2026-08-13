import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from fundlens.allocation.service import set_risk_profile
from fundlens.compute import service
from fundlens.compute.allocation import compute_allocation_drift
from fundlens.db.models import (
    NavHistory,
    NavSource,
    Persona,
    RawCASUpload,
    RawTransaction,
    SchemeMetadata,
    TransactionType,
    UploadStatus,
)
from fundlens.tax.asset_class import TaxAssetClass


def _make_upload(session: Session) -> RawCASUpload:
    upload = RawCASUpload(
        file_hash=f"hash-{uuid.uuid4().hex}", parser_version="test", status=UploadStatus.PARSED
    )
    session.add(upload)
    session.flush()
    return upload


def _add_purchase(
    session: Session, upload_id: str, *, scheme_name: str, amfi_code: str, isin: str,
    units: Decimal, nav: Decimal,
) -> None:
    session.add(
        RawTransaction(
            upload_id=upload_id,
            folio="F1",
            amc="Test AMC",
            scheme_name=scheme_name,
            amfi_code=amfi_code,
            isin=isin,
            txn_type=TransactionType.PURCHASE,
            txn_date=date(2022, 1, 1),
            sequence=0,
            units=units,
            nav=nav,
            amount_paise=int(units * nav * 100),
            natural_key=f"key-{uuid.uuid4().hex}",
        )
    )


def _build_fixture_portfolio(session: Session) -> None:
    """Equity Large Cap: 100 units @ NAV 100 = Rs 10,000. Equity Mid Cap: 50
    units @ NAV 100 = Rs 5,000. Debt: 100 units @ NAV 50 = Rs 5,000. Total
    priced value = Rs 20,000 -> equity 75%, debt 25%, exactly."""
    upload = _make_upload(session)
    _add_purchase(
        session, upload.id, scheme_name="Large Cap Fund", amfi_code="111", isin="ISIN111",
        units=Decimal("100"), nav=Decimal("100"),
    )
    _add_purchase(
        session, upload.id, scheme_name="Mid Cap Fund", amfi_code="222", isin="ISIN222",
        units=Decimal("50"), nav=Decimal("100"),
    )
    _add_purchase(
        session, upload.id, scheme_name="Debt Fund", amfi_code="333", isin="ISIN333",
        units=Decimal("100"), nav=Decimal("50"),
    )
    session.add(
        SchemeMetadata(
            amfi_code="111",
            category_raw="Open Ended Schemes(Equity Scheme - Large Cap Fund)",
            tax_asset_class=TaxAssetClass.EQUITY,
        )
    )
    session.add(
        SchemeMetadata(
            amfi_code="222",
            category_raw="Open Ended Schemes(Equity Scheme - Mid Cap Fund)",
            tax_asset_class=TaxAssetClass.EQUITY,
        )
    )
    session.add(
        SchemeMetadata(
            amfi_code="333",
            category_raw="Open Ended Schemes(Debt Scheme - Liquid Fund)",
            tax_asset_class=TaxAssetClass.DEBT,
        )
    )
    for amfi_code, nav in (("111", "100"), ("222", "100"), ("333", "50")):
        session.add(
            NavHistory(
                amfi_code=amfi_code, nav_date=date(2024, 1, 1), nav=Decimal(nav),
                source=NavSource.MANUAL,
            )
        )
    session.commit()
    service.rebuild_all_lots(session)


def test_drift_conservative_persona(session: Session) -> None:
    _build_fixture_portfolio(session)
    profile = set_risk_profile(session, Persona.CONSERVATIVE)

    drift = compute_allocation_drift(session, profile)

    assert drift.priced_portfolio_value_paise == 2_000_000  # Rs 20,000
    assert drift.equity_value_paise == 1_500_000  # Rs 15,000
    assert drift.debt_value_paise == 500_000  # Rs 5,000
    assert drift.actual_equity_pct == 75.0
    assert drift.actual_debt_pct == 25.0
    assert drift.equity_drift_pct == 45.0  # 75 - 30
    assert drift.debt_drift_pct == -45.0  # 25 - 70


def test_drift_balanced_persona(session: Session) -> None:
    _build_fixture_portfolio(session)
    profile = set_risk_profile(session, Persona.BALANCED)

    drift = compute_allocation_drift(session, profile)

    assert drift.equity_drift_pct == 15.0  # 75 - 60
    assert drift.debt_drift_pct == -15.0  # 25 - 40


def test_drift_aggressive_persona(session: Session) -> None:
    _build_fixture_portfolio(session)
    profile = set_risk_profile(session, Persona.AGGRESSIVE)

    drift = compute_allocation_drift(session, profile)

    assert drift.equity_drift_pct == -5.0  # 75 - 80
    assert drift.debt_drift_pct == 5.0  # 25 - 20


def test_drift_custom_persona(session: Session) -> None:
    _build_fixture_portfolio(session)
    profile = set_risk_profile(
        session, Persona.CUSTOM, target_equity_pct=Decimal("50"), target_debt_pct=Decimal("50")
    )

    drift = compute_allocation_drift(session, profile)

    assert drift.equity_drift_pct == 25.0  # 75 - 50
    assert drift.debt_drift_pct == -25.0  # 25 - 50


def test_equity_cap_breakdown_matches_fixture(session: Session) -> None:
    _build_fixture_portfolio(session)
    profile = set_risk_profile(session, Persona.BALANCED)

    drift = compute_allocation_drift(session, profile)

    assert drift.equity_cap_breakdown.large_paise == 1_000_000  # Rs 10,000
    assert drift.equity_cap_breakdown.mid_paise == 500_000  # Rs 5,000
    assert drift.equity_cap_breakdown.small_paise == 0


def test_other_bucket_for_missing_metadata_not_folded_into_equity_or_debt(
    session: Session,
) -> None:
    upload = _make_upload(session)
    _add_purchase(
        session, upload.id, scheme_name="Unknown Fund", amfi_code="999", isin="ISIN999",
        units=Decimal("100"), nav=Decimal("100"),
    )
    session.add(
        NavHistory(
            amfi_code="999", nav_date=date(2024, 1, 1), nav=Decimal("100"), source=NavSource.MANUAL
        )
    )
    session.commit()
    service.rebuild_all_lots(session)
    profile = set_risk_profile(session, Persona.BALANCED)

    drift = compute_allocation_drift(session, profile)

    assert drift.equity_value_paise == 0
    assert drift.debt_value_paise == 0
    assert drift.other_value_paise == 1_000_000
    assert drift.schemes_missing_metadata == 1
    assert drift.actual_other_pct == 100.0


def test_holding_missing_nav_excluded_from_percentages_not_zeroed(session: Session) -> None:
    upload = _make_upload(session)
    _add_purchase(
        session, upload.id, scheme_name="No NAV Fund", amfi_code="888", isin="ISIN888",
        units=Decimal("100"), nav=Decimal("100"),
    )
    session.add(
        SchemeMetadata(
            amfi_code="888",
            category_raw="Open Ended Schemes(Equity Scheme - Large Cap Fund)",
            tax_asset_class=TaxAssetClass.EQUITY,
        )
    )
    # Deliberately no NavHistory row for amfi_code 888.
    session.commit()
    service.rebuild_all_lots(session)
    profile = set_risk_profile(session, Persona.BALANCED)

    drift = compute_allocation_drift(session, profile)

    assert drift.priced_portfolio_value_paise == 0
    assert drift.value_unavailable_paise == 1_000_000  # cost basis, flagged not zeroed
    assert drift.actual_equity_pct is None
    assert drift.equity_drift_pct is None
