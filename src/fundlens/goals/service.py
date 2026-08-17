"""PRD FR-8.1/8.2, phase P2.4: goal CRUD and holding-tagging.

Goals are persisted, current-state CRUD (like RiskProfile) — the corpus
*projection* built on top of a goal is compute-on-demand and never
persisted (compute/goal.py), matching this app's general bias toward
deriving derived state fresh rather than caching it prematurely.
"""

from datetime import UTC, date, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from fundlens.db.models import Goal, GoalHolding


def _validate_target_date(target_date: date) -> None:
    if target_date <= date.today():
        raise ValueError("target_date must be in the future")


def create_goal(
    session: Session, name: str, target_amount_paise: int, target_date: date
) -> Goal:
    _validate_target_date(target_date)
    goal = Goal(name=name, target_amount_paise=target_amount_paise, target_date=target_date)
    session.add(goal)
    session.commit()
    return goal


def get_goal(session: Session, goal_id: str) -> Goal | None:
    return session.get(Goal, goal_id)


def list_goals(session: Session) -> list[Goal]:
    return list(session.scalars(select(Goal).order_by(Goal.target_date)))


def update_goal(
    session: Session,
    goal: Goal,
    name: str | None = None,
    target_amount_paise: int | None = None,
    target_date: date | None = None,
) -> Goal:
    if target_date is not None:
        _validate_target_date(target_date)
        goal.target_date = target_date
    if name is not None:
        goal.name = name
    if target_amount_paise is not None:
        goal.target_amount_paise = target_amount_paise
    goal.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.commit()
    return goal


def delete_goal(session: Session, goal: Goal) -> None:
    # SQLite doesn't enforce ON DELETE CASCADE unless `PRAGMA foreign_keys`
    # is turned on per-connection, which this app never does anywhere else
    # -- delete the tagged holdings explicitly rather than relying on a
    # DB-level cascade that's silently inert here.
    session.execute(delete(GoalHolding).where(GoalHolding.goal_id == goal.id))
    session.delete(goal)
    session.commit()


def get_goal_holdings(session: Session, goal_id: str) -> list[GoalHolding]:
    return list(session.scalars(select(GoalHolding).where(GoalHolding.goal_id == goal_id)))


def set_goal_holdings(
    session: Session, goal_id: str, holdings: list[tuple[str, str]]
) -> list[GoalHolding]:
    """Replace the full tagged set in one call — same "replace, don't
    incrementally patch" shape as set_risk_profile (allocation/service.py).
    `holdings` is a list of (folio, scheme_key) pairs.
    """
    session.execute(delete(GoalHolding).where(GoalHolding.goal_id == goal_id))
    rows = [
        GoalHolding(goal_id=goal_id, folio=folio, scheme_key=scheme_key)
        for folio, scheme_key in dict.fromkeys(holdings)
    ]
    session.add_all(rows)
    session.commit()
    return rows
