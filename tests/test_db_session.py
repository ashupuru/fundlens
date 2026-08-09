from pathlib import Path

import pytest

from fundlens.db import session as db_session
from fundlens.settings import Settings


class _FakeKeyring:
    """In-memory stand-in for the OS keychain, so tests never touch the real one."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._store[(service, username)] = password


@pytest.fixture
def fake_keyring(monkeypatch: pytest.MonkeyPatch) -> _FakeKeyring:
    fake = _FakeKeyring()
    monkeypatch.setattr(db_session.keyring, "get_password", fake.get_password)
    monkeypatch.setattr(db_session.keyring, "set_password", fake.set_password)
    return fake


def test_encryption_key_is_generated_once_and_reused(fake_keyring: _FakeKeyring) -> None:
    first = db_session.get_or_create_encryption_key()
    second = db_session.get_or_create_encryption_key()
    assert first == second
    assert len(first) == 64  # secrets.token_hex(32) -> 64 hex chars


def test_get_engine_creates_db_parent_dir_and_binds_to_settings_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_keyring: _FakeKeyring
) -> None:
    db_path = tmp_path / "nested" / "fundlens.db"
    monkeypatch.setattr(
        db_session, "get_settings", lambda: Settings(db_path=db_path)
    )

    engine = db_session.get_engine()

    assert db_path.parent.is_dir()
    assert engine.url.database == str(db_path)
