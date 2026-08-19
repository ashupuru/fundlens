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
    # Deliberately does NOT touch the retirement fields: they're owned by
    # set_retirement_profile below and edited from a different form. This
    # endpoint replaces every field it owns, so folding them in here would
    # silently wipe a filled-in retirement plan whenever the allocation
    # form was saved.
    profile.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.commit()
    return profile


def set_retirement_profile(
    session: Session,
    current_age: int | None = None,
    retirement_age: int | None = None,
    life_expectancy: int | None = None,
    annual_investment_paise: int | None = None,
    annual_investment_step_up_pct: Decimal | None = None,
    monthly_withdrawal_today_paise: int | None = None,
    post_retirement_return_pct: Decimal | None = None,
) -> RiskProfile:
    """Updates only the retirement-projection inputs (compute/retirement.py),
    leaving persona/target allocation untouched -- the two live on the same
    singleton row but are edited from separate forms.

    Requires a risk profile to already exist: target allocation is what the
    projection's expected return is derived from, so there's nothing
    meaningful to project against without it.
    """
    profile = session.get(RiskProfile, _SINGLETON_ID)
    if profile is None:
        raise ValueError("Set a risk profile first — the return assumption is derived from it")

    profile.current_age = current_age
    profile.retirement_age = retirement_age
    profile.life_expectancy = life_expectancy
    profile.annual_investment_paise = annual_investment_paise
    profile.annual_investment_step_up_pct = annual_investment_step_up_pct
    profile.monthly_withdrawal_today_paise = monthly_withdrawal_today_paise
    profile.post_retirement_return_pct = post_retirement_return_pct
    profile.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.commit()
    return profile


def detached_profile_with(
    session: Session, profile: RiskProfile, **overrides: object
) -> RiskProfile:
    """A copy of `profile` with some fields replaced, **deliberately not
    attached to the session** -- for what-if exploration, where the user is
    trying values out rather than saving them.

    Mutating the attached instance instead would be silently destructive:
    SQLAlchemy autoflushes pending changes before the next query, so a
    read-only "what if I retired at 62" request would quietly rewrite the
    stored plan. Constructing a fresh, unattached instance makes that
    impossible by construction rather than by remembering to roll back.
    """
    fields = {c.name: getattr(profile, c.name) for c in RiskProfile.__table__.columns}
    fields.update(overrides)
    detached = RiskProfile(**fields)
    # Belt and braces: even if a caller later hands this to session.add(),
    # expunge-on-construct isn't possible, so assert it isn't tracked now.
    assert detached not in session
    return detached
