import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from fundlens.allocation.service import set_risk_profile
from fundlens.assumptions.service import AssumptionKey, create_version, get_current_version
from fundlens.compute import service
from fundlens.compute.allocation import AllocationDrift, EquityCapBreakdown
from fundlens.compute.verdict import (
    OMITTED_COMPONENTS,
    VerdictType,
    compute_verdict,
    get_all_verdicts,
)
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
    txn_date: date, units: Decimal, nav: Decimal,
) -> None:
    session.add(
        RawTransaction(
            upload_id=upload_id,
            folio="F1",
            pan="ABCDE1234F",
            amc="Test AMC",
            scheme_name=scheme_name,
            amfi_code=amfi_code,
            isin=isin,
            txn_type=TransactionType.PURCHASE,
            txn_date=txn_date,
            sequence=0,
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


def _make_drift(equity_drift_pct: float | None, debt_drift_pct: float | None) -> AllocationDrift:
    return AllocationDrift(
        persona=Persona.BALANCED,
        target_equity_pct=60.0,
        target_debt_pct=40.0,
        actual_equity_pct=None,
        actual_debt_pct=None,
        actual_other_pct=None,
        equity_drift_pct=equity_drift_pct,
        debt_drift_pct=debt_drift_pct,
        priced_portfolio_value_paise=0,
        equity_value_paise=0,
        debt_value_paise=0,
        other_value_paise=0,
        value_unavailable_paise=0,
        equity_cap_breakdown=EquityCapBreakdown(0, 0, 0, 0, 0),
        schemes_missing_metadata=0,
    )


def test_no_data_at_all_defaults_to_hold_with_zero_confidence(session: Session) -> None:
    verdict = compute_verdict(
        session,
        folio="F1",
        scheme_key="ISIN111",
        scheme_name="Fund A",
        amfi_code=None,
        metadata=None,
        drift=None,
    )

    assert verdict.verdict_type == VerdictType.HOLD
    assert verdict.overall_score is None
    assert verdict.confidence_pct == 0.0
    assert verdict.tax_impact is None
    assert verdict.omitted_components == OMITTED_COMPONENTS


def test_poor_performer_triggers_sell_with_computable_tax_impact(session: Session) -> None:
    today = date.today()
    upload = _make_upload(session)
    _add_purchase(
        session, upload.id, scheme_name="Regular Underperformer Fund", amfi_code="111",
        isin="ISIN111", txn_date=today - timedelta(days=1200), units=Decimal("100"),
        nav=Decimal("200"),
    )
    session.add(
        SchemeMetadata(
            amfi_code="111",
            category_raw="Open Ended Schemes(Equity Scheme - Large Cap Fund)",
            tax_asset_class=TaxAssetClass.EQUITY,
        )
    )
    # Fund loses value while benchmark gains -- bad alpha/consistency.
    _add_nav(session, "111", today - timedelta(days=1095), "200")
    _add_nav(session, "111", today, "100")
    session.commit()
    service.rebuild_all_lots(session)

    drift = _make_drift(equity_drift_pct=100.0, debt_drift_pct=-100.0)  # very overweight equity
    metadata = session.get(SchemeMetadata, "111")

    verdict = compute_verdict(
        session,
        folio="F1",
        scheme_key="ISIN111",
        scheme_name="Regular Underperformer Fund",
        amfi_code="111",
        metadata=metadata,
        drift=drift,
    )

    assert verdict.overall_score is not None
    assert verdict.overall_score < 35.0
    assert verdict.verdict_type == VerdictType.SELL
    assert verdict.tax_impact is not None
    assert verdict.tax_impact.computable is True
    assert verdict.blocked_reason is None


def test_sell_blocked_when_debt_tax_rate_not_configured(session: Session) -> None:
    today = date.today()
    upload = _make_upload(session)
    _add_purchase(
        session, upload.id, scheme_name="Regular Underperformer Debt Fund", amfi_code="222",
        isin="ISIN222", txn_date=today - timedelta(days=1200), units=Decimal("100"),
        nav=Decimal("200"),
    )
    session.add(
        SchemeMetadata(
            amfi_code="222",
            category_raw="Open Ended Schemes(Debt Scheme - Liquid Fund)",
            tax_asset_class=TaxAssetClass.DEBT,
        )
    )
    _add_nav(session, "222", today - timedelta(days=1095), "200")
    _add_nav(session, "222", today, "100")
    session.commit()
    service.rebuild_all_lots(session)

    drift = _make_drift(equity_drift_pct=-100.0, debt_drift_pct=100.0)  # very overweight debt
    metadata = session.get(SchemeMetadata, "222")

    verdict = compute_verdict(
        session,
        folio="F1",
        scheme_key="ISIN222",
        scheme_name="Regular Underperformer Debt Fund",
        amfi_code="222",
        metadata=metadata,
        drift=drift,
    )

    # Rubric score is bad enough to indicate SELL, but no debt marginal
    # rate is configured, so it must be blocked, not fabricated.
    assert verdict.verdict_type == VerdictType.HOLD
    assert verdict.tax_impact is None
    assert verdict.blocked_reason is not None
    assert "debt_marginal_tax_rate_not_configured" in verdict.blocked_reason


def test_sell_unblocked_once_debt_tax_rate_configured(session: Session) -> None:
    get_current_version(session)
    create_version(
        session, "with debt rate", {AssumptionKey.DEBT_MARGINAL_TAX_RATE_PCT.value: "30"}
    )

    today = date.today()
    upload = _make_upload(session)
    _add_purchase(
        session, upload.id, scheme_name="Regular Underperformer Debt Fund", amfi_code="222",
        isin="ISIN222", txn_date=today - timedelta(days=1200), units=Decimal("100"),
        nav=Decimal("200"),
    )
    session.add(
        SchemeMetadata(
            amfi_code="222",
            category_raw="Open Ended Schemes(Debt Scheme - Liquid Fund)",
            tax_asset_class=TaxAssetClass.DEBT,
        )
    )
    _add_nav(session, "222", today - timedelta(days=1095), "200")
    _add_nav(session, "222", today, "100")
    session.commit()
    service.rebuild_all_lots(session)

    drift = _make_drift(equity_drift_pct=-100.0, debt_drift_pct=100.0)
    metadata = session.get(SchemeMetadata, "222")

    verdict = compute_verdict(
        session,
        folio="F1",
        scheme_key="ISIN222",
        scheme_name="Regular Underperformer Debt Fund",
        amfi_code="222",
        metadata=metadata,
        drift=drift,
    )

    assert verdict.verdict_type == VerdictType.SELL
    assert verdict.tax_impact is not None
    assert verdict.tax_impact.computable is True


def test_get_all_verdicts_empty_portfolio(session: Session) -> None:
    assert get_all_verdicts(session) == []


def test_get_all_verdicts_without_risk_profile_flags_category_fit(session: Session) -> None:
    upload = _make_upload(session)
    _add_purchase(
        session, upload.id, scheme_name="Some Fund", amfi_code="333", isin="ISIN333",
        txn_date=date.today() - timedelta(days=100), units=Decimal("100"), nav=Decimal("100"),
    )
    session.add(
        SchemeMetadata(
            amfi_code="333",
            category_raw="Open Ended Schemes(Equity Scheme - Large Cap Fund)",
            tax_asset_class=TaxAssetClass.EQUITY,
        )
    )
    _add_nav(session, "333", date.today(), "100")
    session.commit()
    service.rebuild_all_lots(session)

    verdicts = get_all_verdicts(session)

    assert len(verdicts) == 1
    category_fit = next(c for c in verdicts[0].components if c.name == "category_fit")
    assert category_fit.score is None
    assert category_fit.reason == "no_risk_profile_set"


def test_get_all_verdicts_with_risk_profile_scores_category_fit(session: Session) -> None:
    set_risk_profile(session, Persona.BALANCED)
    upload = _make_upload(session)
    _add_purchase(
        session, upload.id, scheme_name="Some Fund", amfi_code="333", isin="ISIN333",
        txn_date=date.today() - timedelta(days=100), units=Decimal("100"), nav=Decimal("100"),
    )
    session.add(
        SchemeMetadata(
            amfi_code="333",
            category_raw="Open Ended Schemes(Equity Scheme - Large Cap Fund)",
            tax_asset_class=TaxAssetClass.EQUITY,
        )
    )
    _add_nav(session, "333", date.today(), "100")
    session.commit()
    service.rebuild_all_lots(session)

    verdicts = get_all_verdicts(session)

    category_fit = next(c for c in verdicts[0].components if c.name == "category_fit")
    assert category_fit.score is not None
