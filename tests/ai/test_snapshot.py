import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy.orm import Session

from fundlens.ai.snapshot import PortfolioMetricsSnapshot, build_portfolio_snapshot
from fundlens.allocation.service import set_risk_profile
from fundlens.compute import service
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

_FORBIDDEN_FIELD_NAMES = {"folio", "pan", "amc"}


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
            pan="ABCDE1234F",
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
    upload = _make_upload(session)
    _add_purchase(
        session, upload.id, scheme_name="Large Cap Fund", amfi_code="111", isin="ISIN111",
        units=Decimal("100"), nav=Decimal("100"),
    )
    _add_purchase(
        session, upload.id, scheme_name="Mid Cap Fund", amfi_code="222", isin="ISIN222",
        units=Decimal("50"), nav=Decimal("100"),
    )
    session.add(
        GrowwFundMetadata(
            amfi_code="111", scheme_name="Large Cap Fund", category="Equity",
            sub_category="Large Cap", plan_type="Direct", aum=Decimal("5000"),
            groww_rating=4, expense_ratio=Decimal("0.5"),
        )
    )
    session.add(
        GrowwFundMetadata(
            amfi_code="222", scheme_name="Mid Cap Fund", category="Equity",
            sub_category="Mid Cap", plan_type="Regular", aum=Decimal("2000"),
            groww_rating=3, expense_ratio=Decimal("1.2"),
        )
    )
    for amfi_code, nav in (("111", "120"), ("222", "110")):
        session.add(
            NavHistory(
                amfi_code=amfi_code, nav_date=date(2024, 1, 1), nav=Decimal(nav),
                source=NavSource.MANUAL,
            )
        )
    session.commit()
    service.rebuild_all_lots(session)


def _all_field_names(model_cls: type[BaseModel]) -> set[str]:
    """Recursively walk a Pydantic model's schema (including nested models
    and list-of-model fields) collecting every field name -- the PII gate
    needs a structural guarantee, not a scan of one instance's serialized
    output."""
    names: set[str] = set()
    for field_name, field_info in model_cls.model_fields.items():
        names.add(field_name)
        for candidate in _unwrap_annotation(field_info.annotation):
            if isinstance(candidate, type) and issubclass(candidate, BaseModel):
                names |= _all_field_names(candidate)
    return names


def _unwrap_annotation(annotation: object) -> list[object]:
    """Pull concrete types out of Optional[...]/list[...]/unions for the
    recursive walk above."""
    args = getattr(annotation, "__args__", None)
    if args:
        result: list[object] = []
        for arg in args:
            result.extend(_unwrap_annotation(arg))
        return result
    return [annotation]


def test_snapshot_schema_never_contains_forbidden_pii_fields() -> None:
    field_names = _all_field_names(PortfolioMetricsSnapshot)
    assert field_names.isdisjoint(_FORBIDDEN_FIELD_NAMES), field_names & _FORBIDDEN_FIELD_NAMES


def test_build_portfolio_snapshot_no_risk_profile_set(session: Session) -> None:
    _build_fixture_portfolio(session)

    snapshot = build_portfolio_snapshot(session)

    assert snapshot.allocation is None
    assert len(snapshot.funds) == 2


def test_build_portfolio_snapshot_with_risk_profile(session: Session) -> None:
    _build_fixture_portfolio(session)
    set_risk_profile(session, Persona.BALANCED)

    snapshot = build_portfolio_snapshot(session)

    assert snapshot.allocation is not None
    assert snapshot.allocation.target_equity_pct == 60.0


def test_build_portfolio_snapshot_fund_fields(session: Session) -> None:
    _build_fixture_portfolio(session)

    snapshot = build_portfolio_snapshot(session)

    by_name = {f.scheme_name: f for f in snapshot.funds}
    large_cap = by_name["Large Cap Fund"]
    assert large_cap.category == "Equity"
    assert large_cap.expense_ratio == 0.5
    assert large_cap.plan_type == "Direct"
    assert large_cap.aum_crores == 5000.0
    assert large_cap.groww_rating == 4
    assert large_cap.verdict in ("hold", "sell")


def test_build_portfolio_snapshot_concentration_and_fee_drag(session: Session) -> None:
    _build_fixture_portfolio(session)

    snapshot = build_portfolio_snapshot(session)

    # Large Cap: 100u * 120 = Rs 12,000. Mid Cap: 50u * 110 = Rs 5,500.
    # Total = Rs 17,500. Large Cap share = 12000/17500 = 68.57%.
    assert snapshot.concentration.top_1_pct > 68.0
    assert snapshot.concentration.hhi > 0
    assert snapshot.fee_drag_funds_excluded == 0
    # 12000*0.5% + 5500*1.2% = 60 + 66 = Rs 126
    assert snapshot.fee_drag_annual_rupees is not None
    assert abs(snapshot.fee_drag_annual_rupees - 126.0) < 0.01


def test_build_portfolio_snapshot_excludes_funds_missing_expense_ratio(session: Session) -> None:
    upload = _make_upload(session)
    _add_purchase(
        session, upload.id, scheme_name="No Metadata Fund", amfi_code="999", isin="ISIN999",
        units=Decimal("10"), nav=Decimal("100"),
    )
    session.add(
        NavHistory(
            amfi_code="999", nav_date=date(2024, 1, 1), nav=Decimal("100"), source=NavSource.MANUAL
        )
    )
    session.commit()
    service.rebuild_all_lots(session)

    snapshot = build_portfolio_snapshot(session)

    assert snapshot.fee_drag_funds_excluded == 1
    assert snapshot.fee_drag_annual_rupees is None


def test_build_portfolio_snapshot_empty_portfolio(session: Session) -> None:
    snapshot = build_portfolio_snapshot(session)

    assert snapshot.funds == []
    assert snapshot.concentration.top_1_pct == 0.0
    assert snapshot.fee_drag_annual_rupees is None
    assert snapshot.overlap_pairs == []
    assert snapshot.style_drift_flags == []
