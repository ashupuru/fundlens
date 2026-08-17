"""Test gate (development-plan.md P2.4): required-SIP cross-checked against
a standard future-value-of-annuity formula, computed independently of
compute/goal.py, for 3 fixture goals with varying corpus/rate/years/
inflation combinations.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from fundlens.assumptions.service import GoalAssumptions
from fundlens.compute import service as lot_service
from fundlens.compute.goal import compute_goal_projection
from fundlens.db.models import (
    Goal,
    NavHistory,
    NavSource,
    Persona,
    RawCASUpload,
    RawTransaction,
    RiskProfile,
    TransactionType,
    UploadStatus,
)

_TOLERANCE_PAISE = 1  # rounding-order tolerance between the two independent computations


def _reference_required_sip(gap: Decimal, annual_rate_pct: Decimal, years: Decimal) -> Decimal:
    """Textbook FV-of-an-ordinary-annuity formula, solved for the payment,
    written independently from compute/goal.py's implementation."""
    if gap <= 0:
        return Decimal(0)
    i = annual_rate_pct / 100 / 12
    n = years * 12
    if i == 0:
        return gap / n
    factor = ((1 + i) ** n - 1) / i
    return gap / factor


def _add_holding(
    session: Session, *, folio: str, amfi_code: str, isin: str, units: Decimal, nav: Decimal
) -> str:
    """Creates a priced, currently-open holding and returns its scheme_key
    (== isin, per scheme_identity.scheme_key's precedence)."""
    upload = RawCASUpload(
        file_hash=f"hash-{uuid.uuid4().hex}", parser_version="test", status=UploadStatus.PARSED
    )
    session.add(upload)
    session.flush()
    session.add(
        RawTransaction(
            upload_id=upload.id,
            folio=folio,
            amc="Test AMC",
            scheme_name=f"Fund {isin}",
            amfi_code=amfi_code,
            isin=isin,
            txn_type=TransactionType.PURCHASE,
            txn_date=date(2020, 1, 1),
            sequence=0,
            units=units,
            nav=nav,
            amount_paise=int(units * nav * 100),
            natural_key=f"key-{uuid.uuid4().hex}",
        )
    )
    session.add(
        NavHistory(amfi_code=amfi_code, nav_date=date(2024, 1, 1), nav=nav, source=NavSource.MANUAL)
    )
    session.commit()
    lot_service.rebuild_all_lots(session)
    return isin


def _make_goal(target_amount_rupees: Decimal, years: int) -> Goal:
    return Goal(
        id=str(uuid.uuid4()),
        name="Fixture goal",
        target_amount_paise=int(target_amount_rupees * 100),
        target_date=date.today() + timedelta(days=365 * years),
    )


def _all_equity_profile(target_equity_pct: str, target_debt_pct: str) -> RiskProfile:
    return RiskProfile(
        id=1,
        persona=Persona.CUSTOM,
        target_equity_pct=Decimal(target_equity_pct),
        target_debt_pct=Decimal(target_debt_pct),
    )


def test_fixture_1_no_current_corpus_no_inflation(session: Session) -> None:
    goal = _make_goal(target_amount_rupees=Decimal("500000"), years=5)
    profile = _all_equity_profile("100", "0")
    assumptions = GoalAssumptions(
        inflation_pct=Decimal("0"),
        expected_equity_return_pct=Decimal("12"),
        expected_debt_return_pct=Decimal("7"),
    )

    projection = compute_goal_projection(session, goal, set(), profile, assumptions)

    expected_sip = _reference_required_sip(Decimal("500000"), Decimal("12"), Decimal("5"))
    assert (
        abs(projection.required_monthly_sip_paise - round(expected_sip * 100)) <= _TOLERANCE_PAISE
    )
    assert projection.current_corpus_paise == 0
    assert projection.inflated_target_paise == 50_000_000  # no inflation -> unchanged


def test_fixture_2_current_corpus_no_inflation(session: Session) -> None:
    scheme_key = _add_holding(
        session,
        folio="F1",
        amfi_code="111",
        isin="ISIN111",
        units=Decimal("3000"),
        nav=Decimal("100"),
    )
    goal = _make_goal(target_amount_rupees=Decimal("2000000"), years=10)
    profile = _all_equity_profile("100", "0")
    assumptions = GoalAssumptions(
        inflation_pct=Decimal("0"),
        expected_equity_return_pct=Decimal("10"),
        expected_debt_return_pct=Decimal("7"),
    )

    projection = compute_goal_projection(
        session, goal, {("F1", scheme_key)}, profile, assumptions
    )

    assert projection.current_corpus_paise == 30_000_000  # Rs 3,00,000
    projected = Decimal("300000") * (Decimal("1.10") ** 10)
    gap = Decimal("2000000") - projected
    expected_sip = _reference_required_sip(gap, Decimal("10"), Decimal("10"))
    assert (
        abs(projection.required_monthly_sip_paise - round(expected_sip * 100)) <= _TOLERANCE_PAISE
    )


def test_fixture_3_with_inflation_and_blended_return(session: Session) -> None:
    scheme_key = _add_holding(
        session,
        folio="F1",
        amfi_code="222",
        isin="ISIN222",
        units=Decimal("10000"),
        nav=Decimal("100"),
    )
    goal = _make_goal(target_amount_rupees=Decimal("3000000"), years=7)
    profile = _all_equity_profile("70", "30")
    assumptions = GoalAssumptions(
        inflation_pct=Decimal("6"),
        expected_equity_return_pct=Decimal("12"),
        expected_debt_return_pct=Decimal("7"),
    )

    projection = compute_goal_projection(
        session, goal, {("F1", scheme_key)}, profile, assumptions
    )

    blended_rate = Decimal("0.7") * Decimal("12") + Decimal("0.3") * Decimal("7")  # 10.5
    assert projection.blended_expected_return_pct == blended_rate
    inflated_target = Decimal("3000000") * (Decimal("1.06") ** 7)
    projected = Decimal("1000000") * (Decimal("1") + blended_rate / 100) ** 7
    gap = inflated_target - projected
    expected_sip = _reference_required_sip(gap, blended_rate, Decimal("7"))
    assert (
        abs(projection.required_monthly_sip_paise - round(expected_sip * 100)) <= _TOLERANCE_PAISE
    )
    assert abs(projection.inflated_target_paise - round(inflated_target * 100)) <= _TOLERANCE_PAISE


def test_goal_already_funded_requires_zero_sip(session: Session) -> None:
    scheme_key = _add_holding(
        session,
        folio="F1",
        amfi_code="333",
        isin="ISIN333",
        units=Decimal("100000"),
        nav=Decimal("100"),
    )
    goal = _make_goal(target_amount_rupees=Decimal("1"), years=1)
    profile = _all_equity_profile("100", "0")
    assumptions = GoalAssumptions(
        inflation_pct=Decimal("0"),
        expected_equity_return_pct=Decimal("12"),
        expected_debt_return_pct=Decimal("7"),
    )

    projection = compute_goal_projection(
        session, goal, {("F1", scheme_key)}, profile, assumptions
    )

    assert projection.required_monthly_sip_paise == 0
    assert projection.surplus_or_shortfall_paise > 0


def test_untagged_holding_excluded_from_corpus(session: Session) -> None:
    _add_holding(
        session,
        folio="F1",
        amfi_code="444",
        isin="ISIN444",
        units=Decimal("100"),
        nav=Decimal("100"),
    )
    goal = _make_goal(target_amount_rupees=Decimal("100000"), years=1)
    profile = _all_equity_profile("100", "0")
    assumptions = GoalAssumptions(
        inflation_pct=Decimal("0"),
        expected_equity_return_pct=Decimal("12"),
        expected_debt_return_pct=Decimal("7"),
    )

    projection = compute_goal_projection(session, goal, set(), profile, assumptions)

    assert projection.current_corpus_paise == 0


def test_holding_with_no_nav_flagged_not_fabricated(session: Session) -> None:
    upload = RawCASUpload(file_hash="hash-nonav", parser_version="test", status=UploadStatus.PARSED)
    session.add(upload)
    session.flush()
    session.add(
        RawTransaction(
            upload_id=upload.id,
            folio="F1",
            amc="Test AMC",
            scheme_name="No NAV Fund",
            amfi_code="555",
            isin="ISIN555",
            txn_type=TransactionType.PURCHASE,
            txn_date=date(2020, 1, 1),
            sequence=0,
            units=Decimal("100"),
            nav=Decimal("50"),
            amount_paise=500_000,
            natural_key="key-nonav",
        )
    )
    session.commit()
    lot_service.rebuild_all_lots(session)

    goal = _make_goal(target_amount_rupees=Decimal("100000"), years=1)
    profile = _all_equity_profile("100", "0")
    assumptions = GoalAssumptions(
        inflation_pct=Decimal("0"),
        expected_equity_return_pct=Decimal("12"),
        expected_debt_return_pct=Decimal("7"),
    )

    projection = compute_goal_projection(session, goal, {("F1", "ISIN555")}, profile, assumptions)

    assert projection.current_corpus_paise == 0
    assert projection.current_corpus_unavailable_paise == 500_000


def test_sensitivity_table_has_three_points_at_plus_minus_2pct(session: Session) -> None:
    goal = _make_goal(target_amount_rupees=Decimal("500000"), years=5)
    profile = _all_equity_profile("100", "0")
    assumptions = GoalAssumptions(
        inflation_pct=Decimal("0"),
        expected_equity_return_pct=Decimal("12"),
        expected_debt_return_pct=Decimal("7"),
    )

    projection = compute_goal_projection(session, goal, set(), profile, assumptions)

    low, mid, high = projection.sensitivity
    assert [s.return_pct for s in (low, mid, high)] == [Decimal("10"), Decimal("12"), Decimal("14")]
    # Lower return -> larger required SIP; higher return -> smaller required SIP.
    assert low.required_monthly_sip_paise > mid.required_monthly_sip_paise
    assert mid.required_monthly_sip_paise > high.required_monthly_sip_paise
    assert mid.required_monthly_sip_paise == projection.required_monthly_sip_paise
