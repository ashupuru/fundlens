from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from fundlens.allocation import service
from fundlens.db.models import Persona, RiskProfile


def test_get_risk_profile_none_before_onboarding(session: Session) -> None:
    assert service.get_risk_profile(session) is None


def test_set_risk_profile_conservative_uses_preset(session: Session) -> None:
    profile = service.set_risk_profile(session, Persona.CONSERVATIVE)

    assert profile.target_equity_pct == Decimal("30")
    assert profile.target_debt_pct == Decimal("70")


def test_set_risk_profile_balanced_uses_preset(session: Session) -> None:
    profile = service.set_risk_profile(session, Persona.BALANCED)
    assert profile.target_equity_pct == Decimal("60")
    assert profile.target_debt_pct == Decimal("40")


def test_set_risk_profile_aggressive_uses_preset(session: Session) -> None:
    profile = service.set_risk_profile(session, Persona.AGGRESSIVE)
    assert profile.target_equity_pct == Decimal("80")
    assert profile.target_debt_pct == Decimal("20")


def test_set_risk_profile_custom_requires_explicit_targets(session: Session) -> None:
    with pytest.raises(ValueError, match="Custom persona requires"):
        service.set_risk_profile(session, Persona.CUSTOM)


def test_set_risk_profile_custom_uses_explicit_targets(session: Session) -> None:
    profile = service.set_risk_profile(
        session, Persona.CUSTOM, target_equity_pct=Decimal("55"), target_debt_pct=Decimal("45")
    )
    assert profile.target_equity_pct == Decimal("55")
    assert profile.target_debt_pct == Decimal("45")


def test_set_risk_profile_is_a_singleton_upsert(session: Session) -> None:
    service.set_risk_profile(session, Persona.CONSERVATIVE)
    service.set_risk_profile(session, Persona.AGGRESSIVE)

    rows = session.query(RiskProfile).all()
    assert len(rows) == 1
    assert rows[0].persona == Persona.AGGRESSIVE


def test_set_risk_profile_stores_questionnaire_answers(session: Session) -> None:
    profile = service.set_risk_profile(
        session,
        Persona.BALANCED,
        horizon_years=15,
        drawdown_tolerance="medium",
        income_stability="stable",
        has_dependents=True,
    )
    assert profile.horizon_years == 15
    assert profile.drawdown_tolerance == "medium"
    assert profile.income_stability == "stable"
    assert profile.has_dependents is True
