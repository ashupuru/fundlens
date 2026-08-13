"""run_migrations() is what api/main.py's lifespan handler calls on every
startup so the app never again silently drifts behind head the way it did
twice (coverage_gap, risk_profile) before this existed — each surfacing
only as a runtime 500 on whichever endpoint touched the missing table."""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from fundlens.db.migrate import run_migrations
from fundlens.settings import get_settings


def test_run_migrations_creates_all_tables_on_a_fresh_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "fresh.db"
    monkeypatch.setenv("FUNDLENS_DB_PATH", str(db_path))
    get_settings.cache_clear()
    try:
        run_migrations()

        engine = create_engine(f"sqlite:///{db_path}")
        tables = set(inspect(engine).get_table_names())
        engine.dispose()
    finally:
        get_settings.cache_clear()

    # A representative sample spanning every phase, not an exhaustive list —
    # proves the full migration chain ran, not just the first one.
    assert {
        "raw_transaction",
        "lot",
        "scheme_metadata",
        "coverage_gap",
        "assumption_version",
        "risk_profile",
    }.issubset(tables)


def test_run_migrations_is_idempotent_on_an_already_current_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "current.db"
    monkeypatch.setenv("FUNDLENS_DB_PATH", str(db_path))
    get_settings.cache_clear()
    try:
        run_migrations()
        run_migrations()  # must not raise or double-apply

        engine = create_engine(f"sqlite:///{db_path}")
        tables = set(inspect(engine).get_table_names())
        engine.dispose()
    finally:
        get_settings.cache_clear()

    assert "risk_profile" in tables
