"""SQLite session wiring.

Encryption-at-rest status: NOT yet enabled. The intended driver is SQLCipher
(technical-spec.md §8.1), but `sqlcipher3-binary` currently ships no macOS
arm64 wheel for Python 3.12+ (confirmed during Phase 0a scaffolding), so this
module uses plain stdlib SQLite for now via SQLAlchemy's `sqlite://` driver.

The encryption key is generated here and normally stored in the macOS Keychain,
with a local file fallback for development environments where the Keychain is
unavailable or rejects writes (for example on CI / sandboxed shells / some
macOS permission states). That keeps the SQLCipher swap isolated to
`get_engine()`, without blocking local development.
"""

import secrets
from pathlib import Path

import keyring
from keyring.errors import PasswordDeleteError, PasswordSetError
from sqlalchemy import Engine, create_engine

from fundlens.settings import get_settings

_KEYRING_SERVICE = "fundlens"
_KEYRING_KEY_NAME = "db_encryption_key"
_LOCAL_KEY_FILENAME = ".fundlens_db_key"


def _local_key_path() -> Path:
    return Path.cwd() / _LOCAL_KEY_FILENAME


def _read_local_key() -> str | None:
    path = _local_key_path()
    if not path.exists():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def _write_local_key(key: str) -> None:
    path = _local_key_path()
    path.write_text(key, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def get_or_create_encryption_key() -> str:
    """Return the DB encryption key from the Keychain, or a local fallback file.

    macOS error -25299 (errSecDuplicateItem) can occur when a keychain item exists
    but is inaccessible to this process (e.g. written by a different app / access
    group). In that case get_password returns None yet set_password is rejected.
    We handle it by deleting the stale entry and rewriting it; if the Keychain is
    unavailable we persist the key in a local file so development can continue.
    """
    existing = keyring.get_password(_KEYRING_SERVICE, _KEYRING_KEY_NAME)
    if existing:
        return existing

    local_key = _read_local_key()
    if local_key:
        return local_key

    new_key = secrets.token_hex(32)
    try:
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_KEY_NAME, new_key)
        return new_key
    except PasswordSetError:
        # Stale/inaccessible duplicate — clear it and rewrite.
        try:
            keyring.delete_password(_KEYRING_SERVICE, _KEYRING_KEY_NAME)
        except PasswordDeleteError:
            pass
        try:
            keyring.set_password(_KEYRING_SERVICE, _KEYRING_KEY_NAME, new_key)
            return new_key
        except PasswordSetError:
            # Keychain is unavailable (common in sandboxed/dev shells or broken
            # macOS keychain access). Fall back to a local file for continuity.
            _write_local_key(new_key)
            return new_key


def get_engine() -> Engine:
    settings = get_settings()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    # Encryption key is provisioned even though not yet applied — see module docstring.
    get_or_create_encryption_key()
    return create_engine(f"sqlite:///{settings.db_path}")
