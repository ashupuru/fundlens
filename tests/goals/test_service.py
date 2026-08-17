from datetime import date, timedelta

import pytest
from sqlalchemy.orm import Session

from fundlens.goals import service

_FUTURE_DATE = date.today() + timedelta(days=365)


def test_create_and_get_goal(session: Session) -> None:
    goal = service.create_goal(
        session,
        name="Retirement",
        target_amount_paise=10_000_000,
        target_date=date.today() + timedelta(days=3650),
    )

    fetched = service.get_goal(session, goal.id)

    assert fetched is not None
    assert fetched.name == "Retirement"
    assert fetched.target_amount_paise == 10_000_000


def test_create_goal_rejects_past_target_date(session: Session) -> None:
    with pytest.raises(ValueError, match="future"):
        service.create_goal(
            session,
            name="Too late",
            target_amount_paise=1_000,
            target_date=date.today() - timedelta(days=1),
        )


def test_create_goal_rejects_today_as_target_date(session: Session) -> None:
    with pytest.raises(ValueError, match="future"):
        service.create_goal(
            session, name="Today", target_amount_paise=1_000, target_date=date.today()
        )


def test_list_goals_orders_by_target_date(session: Session) -> None:
    far = service.create_goal(
        session,
        name="Far",
        target_amount_paise=1,
        target_date=date.today() + timedelta(days=3650),
    )
    near = service.create_goal(
        session,
        name="Near",
        target_amount_paise=1,
        target_date=date.today() + timedelta(days=365),
    )

    goals = service.list_goals(session)

    assert [g.id for g in goals] == [near.id, far.id]


def test_update_goal_partial_fields(session: Session) -> None:
    goal = service.create_goal(
        session,
        name="Original",
        target_amount_paise=1_000,
        target_date=date.today() + timedelta(days=365),
    )

    updated = service.update_goal(session, goal, name="Renamed")

    assert updated.name == "Renamed"
    assert updated.target_amount_paise == 1_000


def test_update_goal_rejects_past_target_date(session: Session) -> None:
    goal = service.create_goal(
        session, name="Goal", target_amount_paise=1_000, target_date=_FUTURE_DATE
    )

    with pytest.raises(ValueError, match="future"):
        service.update_goal(session, goal, target_date=date.today() - timedelta(days=1))


def test_delete_goal_removes_it(session: Session) -> None:
    goal = service.create_goal(
        session, name="Goal", target_amount_paise=1_000, target_date=_FUTURE_DATE
    )

    service.delete_goal(session, goal)

    assert service.get_goal(session, goal.id) is None


def test_set_goal_holdings_replaces_full_set(session: Session) -> None:
    goal = service.create_goal(
        session, name="Goal", target_amount_paise=1_000, target_date=_FUTURE_DATE
    )

    service.set_goal_holdings(session, goal.id, [("F1", "ISIN1"), ("F1", "ISIN2")])
    first = service.get_goal_holdings(session, goal.id)
    assert {(h.folio, h.scheme_key) for h in first} == {("F1", "ISIN1"), ("F1", "ISIN2")}

    service.set_goal_holdings(session, goal.id, [("F2", "ISIN3")])
    second = service.get_goal_holdings(session, goal.id)
    assert {(h.folio, h.scheme_key) for h in second} == {("F2", "ISIN3")}


def test_set_goal_holdings_dedupes_input(session: Session) -> None:
    goal = service.create_goal(
        session, name="Goal", target_amount_paise=1_000, target_date=_FUTURE_DATE
    )

    rows = service.set_goal_holdings(session, goal.id, [("F1", "ISIN1"), ("F1", "ISIN1")])

    assert len(rows) == 1


def test_delete_goal_cascades_to_holdings(session: Session) -> None:
    goal = service.create_goal(
        session, name="Goal", target_amount_paise=1_000, target_date=_FUTURE_DATE
    )
    service.set_goal_holdings(session, goal.id, [("F1", "ISIN1")])

    service.delete_goal(session, goal)
    session.expire_all()

    assert service.get_goal_holdings(session, goal.id) == []
