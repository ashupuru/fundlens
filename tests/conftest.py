import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

# Point the whole suite at a throwaway database *before* anything reads
# settings. `fundlens.api.main`'s lifespan runs real Alembic migrations, and
# TestClient used as a context manager (tests/test_funds_api.py does) triggers
# it -- so without this the suite migrates whatever `.env` points at, which is
# the developer's real portfolio database.
#
# This is not hypothetical: it corrupted one. SQLite DDL is non-transactional,
# so a migration that created a table and then failed left the real database
# with the table present but `alembic_version` still on the prior revision,
# and every later run aborted with "table already exists".
#
# Set via the environment rather than by patching Settings, because
# `get_settings()` is lru_cached and the app reads it during startup, well
# before any fixture could intervene.
os.environ["FUNDLENS_DB_PATH"] = str(Path(tempfile.mkdtemp(prefix="fundlens-test-")) / "test.db")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from fundlens.db.models import Base


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db_session:
        yield db_session
    engine.dispose()


@pytest.fixture
def client() -> Iterator[TestClient]:
    # Deferred imports: fundlens.api.main pulls in the whole app graph, which
    # only tests that actually need a live API should pay for.
    from fundlens.api.deps import get_session
    from fundlens.api.main import app

    # TestClient dispatches requests on a different thread than the fixture;
    # an in-memory SQLite DB needs a single shared connection (StaticPool) and
    # check_same_thread=False to survive that, or each thread sees an empty DB.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    def override_get_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app)
    app.dependency_overrides.clear()
    engine.dispose()
