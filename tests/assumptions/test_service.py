from datetime import date

import pytest
from sqlalchemy.orm import Session

from fundlens.assumptions import service
from fundlens.assumptions.service import AssumptionKey
from fundlens.db.models import AssumptionVersion


def test_get_current_version_seeds_defaults_on_first_call(session: Session) -> None:
    version = service.get_current_version(session)

    assert version.is_current is True
    values = service.get_values(session, version.id)
    assert values[AssumptionKey.EQUITY_LTCG_THRESHOLD_DAYS.value] == "365"
    assert values[AssumptionKey.EQUITY_LTCG_EXEMPTION_PAISE_PER_FY.value] == "12500000"


def test_get_current_version_is_idempotent(session: Session) -> None:
    first = service.get_current_version(session)
    second = service.get_current_version(session)

    assert first.id == second.id
    versions = session.query(AssumptionVersion).all()
    assert len(versions) == 1


def test_create_version_overrides_only_specified_keys(session: Session) -> None:
    service.get_current_version(session)  # seed defaults

    new_version = service.create_version(
        session,
        label="Higher exemption test",
        overrides={AssumptionKey.EQUITY_LTCG_EXEMPTION_PAISE_PER_FY.value: "20000000"},
    )

    assert new_version.is_current is True
    values = service.get_values(session, new_version.id)
    assert values[AssumptionKey.EQUITY_LTCG_EXEMPTION_PAISE_PER_FY.value] == "20000000"
    # Unchanged keys carried forward from the prior version.
    assert values[AssumptionKey.EQUITY_LTCG_THRESHOLD_DAYS.value] == "365"


def test_create_version_flips_previous_current_flag(session: Session) -> None:
    first = service.get_current_version(session)
    second = service.create_version(session, label="v2", overrides={})

    session.refresh(first)
    assert first.is_current is False
    assert second.is_current is True


def test_create_version_rejects_unknown_key(session: Session) -> None:
    service.get_current_version(session)
    with pytest.raises(ValueError, match="Unknown assumption key"):
        service.create_version(session, label="bad", overrides={"not_a_real_key": "1"})


def test_old_version_values_remain_reproducible_after_new_version(session: Session) -> None:
    first = service.get_current_version(session)
    first_values = service.get_values(session, first.id)

    service.create_version(
        session,
        label="v2",
        overrides={AssumptionKey.EQUITY_LTCG_EXEMPTION_PAISE_PER_FY.value: "1"},
    )

    # The old version's own stored values are untouched by the new version.
    assert service.get_values(session, first.id) == first_values


def test_list_versions_newest_first(session: Session) -> None:
    first = service.get_current_version(session)
    second = service.create_version(session, label="v2", overrides={})

    versions = service.list_versions(session)

    assert [v.id for v in versions] == [second.id, first.id]


def test_get_current_tax_assumptions_typed_values(session: Session) -> None:
    assumptions = service.get_current_tax_assumptions(session)

    assert assumptions.equity_ltcg_threshold_days == 365
    assert assumptions.debt_ltcg_threshold_days == 1095
    assert assumptions.debt_specified_fund_cutoff == date(2023, 4, 1)
    assert assumptions.equity_grandfather_cutoff == date(2018, 1, 31)
    assert assumptions.equity_ltcg_exemption_paise_per_fy == 12_500_000
