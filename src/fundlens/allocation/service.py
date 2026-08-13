"""PRD §3: persona presets and risk-profile CRUD.

Single, app-wide profile (RiskProfile.id is a fixed singleton) — the app
has no multi-PAN concept anywhere else yet (same reasoning as P1.1's
portfolio-level benchmark comparison, compute-engine.md §4), so
data-model.md's originally-planned one-per-PAN design is deferred until
multi-PAN support exists elsewhere in the app.
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from fundlens.db.models import Persona, RiskProfile

_SINGLETON_ID = 1

PERSONA_TARGETS: dict[Persona, tuple[Decimal, Decimal]] = {
    Persona.CONSERVATIVE: (Decimal("30"), Decimal("70")),
    Persona.BALANCED: (Decimal("60"), Decimal("40")),
    Persona.AGGRESSIVE: (Decimal("80"), Decimal("20")),
}


def get_risk_profile(session: Session) -> RiskProfile | None:
    return session.get(RiskProfile, _SINGLETON_ID)


def set_risk_profile(
    session: Session,
    persona: Persona,
    target_equity_pct: Decimal | None = None,
    target_debt_pct: Decimal | None = None,
    horizon_years: int | None = None,
    drawdown_tolerance: str | None = None,
    income_stability: str | None = None,
    has_dependents: bool | None = None,
) -> RiskProfile:
    if persona == Persona.CUSTOM:
        if target_equity_pct is None or target_debt_pct is None:
            raise ValueError(
                "Custom persona requires explicit target_equity_pct and target_debt_pct"
            )
    else:
        target_equity_pct, target_debt_pct = PERSONA_TARGETS[persona]

    profile = session.get(RiskProfile, _SINGLETON_ID)
    if profile is None:
        profile = RiskProfile(id=_SINGLETON_ID)
        session.add(profile)

    profile.persona = persona
    profile.target_equity_pct = target_equity_pct
    profile.target_debt_pct = target_debt_pct
    profile.horizon_years = horizon_years
    profile.drawdown_tolerance = drawdown_tolerance
    profile.income_stability = income_stability
    profile.has_dependents = has_dependents
    profile.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.commit()
    return profile
