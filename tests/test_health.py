import asyncio

from fastapi.testclient import TestClient

import fundlens.api.main as main
from fundlens.api.main import app


def test_health_returns_ok() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_lifespan_refreshes_nav_history(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(main, "run_migrations", lambda: None)
    monkeypatch.setattr(main, "refresh_all_held_schemes", lambda session: calls.append("refreshed"))

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(main, "Session", lambda *_args, **_kwargs: FakeSession())
    monkeypatch.setattr(main, "get_engine", lambda: object())

    async def run_lifespan() -> None:
        async with main.lifespan(main.app):
            pass

    asyncio.run(run_lifespan())

    assert calls == ["refreshed"]
