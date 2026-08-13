"""PRD §5.7 assumption registry: tax thresholds and the LTCG exemption
limit as versioned, editable config (db/models.py AssumptionVersion,
Assumption) rather than code constants — needed now because the P1.2 tax
engine is the first consumer (dev plan P1.2).

Only the values the current tax engine actually reads are seeded/typed
here (`TaxAssumptions`); inflation % and expected-return assumptions named
in the PRD belong to the goal-planning phase (P2+) and aren't added until
something reads them — an untyped, unread assumption key is dead config.
"""

import enum
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from fundlens.db.models import Assumption, AssumptionVersion


class AssumptionKey(enum.StrEnum):
    EQUITY_LTCG_THRESHOLD_DAYS = "equity_ltcg_threshold_days"
    DEBT_LTCG_THRESHOLD_DAYS = "debt_ltcg_threshold_days"
    DEBT_SPECIFIED_FUND_CUTOFF = "debt_specified_fund_cutoff"
    EQUITY_GRANDFATHER_CUTOFF = "equity_grandfather_cutoff"
    EQUITY_LTCG_EXEMPTION_PAISE_PER_FY = "equity_ltcg_exemption_paise_per_fy"


DEFAULT_ASSUMPTIONS: dict[AssumptionKey, str] = {
    AssumptionKey.EQUITY_LTCG_THRESHOLD_DAYS: "365",
    AssumptionKey.DEBT_LTCG_THRESHOLD_DAYS: "1095",
    AssumptionKey.DEBT_SPECIFIED_FUND_CUTOFF: "2023-04-01",
    # Section 112A 4th/5th proviso valuation date — NOT when the rule
    # started applying (that's 2018-04-01/FY2018-19); this is the specific
    # date whose NAV grandfathers pre-existing equity cost basis.
    AssumptionKey.EQUITY_GRANDFATHER_CUTOFF: "2018-01-31",
    AssumptionKey.EQUITY_LTCG_EXEMPTION_PAISE_PER_FY: "12500000",  # Rs 1,25,000
}


def _seed_default_version(session: Session) -> AssumptionVersion:
    version = AssumptionVersion(label="Default", is_current=True)
    session.add(version)
    session.flush()
    for key, value in DEFAULT_ASSUMPTIONS.items():
        session.add(Assumption(version_id=version.id, key=key.value, value=value))
    session.commit()
    return version


def get_current_version(session: Session) -> AssumptionVersion:
    version = session.scalars(
        select(AssumptionVersion).where(AssumptionVersion.is_current.is_(True))
    ).first()
    return version if version is not None else _seed_default_version(session)


def get_values(session: Session, version_id: str) -> dict[str, str]:
    rows = session.scalars(select(Assumption).where(Assumption.version_id == version_id)).all()
    return {r.key: r.value for r in rows}


def get_current_values(session: Session) -> dict[str, str]:
    return get_values(session, get_current_version(session).id)


def list_versions(session: Session) -> list[AssumptionVersion]:
    return list(
        session.scalars(select(AssumptionVersion).order_by(AssumptionVersion.created_at.desc()))
    )


def create_version(session: Session, label: str, overrides: dict[str, str]) -> AssumptionVersion:
    unknown = set(overrides) - {k.value for k in AssumptionKey}
    if unknown:
        raise ValueError(f"Unknown assumption key(s): {sorted(unknown)}")

    current_values = get_current_values(session)
    new_values = {**current_values, **overrides}

    session.execute(
        update(AssumptionVersion).where(AssumptionVersion.is_current.is_(True)).values(
            is_current=False
        )
    )
    new_version = AssumptionVersion(label=label, is_current=True)
    session.add(new_version)
    session.flush()
    for key, value in new_values.items():
        session.add(Assumption(version_id=new_version.id, key=key, value=value))
    session.commit()
    return new_version


@dataclass(frozen=True)
class TaxAssumptions:
    equity_ltcg_threshold_days: int
    debt_ltcg_threshold_days: int
    debt_specified_fund_cutoff: date
    equity_grandfather_cutoff: date
    equity_ltcg_exemption_paise_per_fy: int


def get_current_tax_assumptions(session: Session) -> TaxAssumptions:
    values = get_current_values(session)
    return TaxAssumptions(
        equity_ltcg_threshold_days=int(values[AssumptionKey.EQUITY_LTCG_THRESHOLD_DAYS.value]),
        debt_ltcg_threshold_days=int(values[AssumptionKey.DEBT_LTCG_THRESHOLD_DAYS.value]),
        debt_specified_fund_cutoff=date.fromisoformat(
            values[AssumptionKey.DEBT_SPECIFIED_FUND_CUTOFF.value]
        ),
        equity_grandfather_cutoff=date.fromisoformat(
            values[AssumptionKey.EQUITY_GRANDFATHER_CUTOFF.value]
        ),
        equity_ltcg_exemption_paise_per_fy=int(
            values[AssumptionKey.EQUITY_LTCG_EXEMPTION_PAISE_PER_FY.value]
        ),
    )
