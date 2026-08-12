from collections.abc import Iterator

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
