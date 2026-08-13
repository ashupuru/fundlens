"""Programmatic Alembic migration runner, invoked on API startup
(api/main.py's lifespan handler) so the app never silently drifts behind
head the way it has twice now — once for `coverage_gap` (P0.6), once for
`risk_profile` (P1.4) — each surfacing only as a runtime crash on whichever
endpoint touched the missing table, well after the migration shipped.

Alembic's `upgrade head` is idempotent (a no-op when already current), so
running it unconditionally on every startup is safe and cheap for a
local-first, single-user SQLite app — no separate migration step for the
user to remember.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config


def run_migrations() -> None:
    migrations_dir = Path(__file__).parent / "migrations"
    config = Config()
    config.set_main_option("script_location", str(migrations_dir))
    command.upgrade(config, "head")
