"""SQLite session wiring.

Encryption-at-rest status: NOT yet enabled. The intended driver is SQLCipher
(technical-spec.md §8.1), but `sqlcipher3-binary` currently ships no macOS
arm64 wheel for Python 3.12+ (confirmed during Phase 0a scaffolding), so this
module uses plain stdlib SQLite for now via SQLAlchemy's `sqlite://` driver.

The encryption key is still generated and stored in the macOS Keychain here,
so that swapping in a SQLCipher-backed engine later is a driver change in
`get_engine()`, not a re-architecture. Track the driver swap before any real
financial data is stored.
"""

import secrets

import keyring
from sqlalchemy import Engine, create_engine

from fundlens.settings import get_settings

_KEYRING_SERVICE = "fundlens"
_KEYRING_KEY_NAME = "db_encryption_key"


def get_or_create_encryption_key() -> str:
    """Return the DB encryption key from the Keychain, generating one on first run."""
    existing = keyring.get_password(_KEYRING_SERVICE, _KEYRING_KEY_NAME)
    if existing:
        return existing
    new_key = secrets.token_hex(32)
    keyring.set_password(_KEYRING_SERVICE, _KEYRING_KEY_NAME, new_key)
    return new_key


def get_engine() -> Engine:
    settings = get_settings()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    # Encryption key is provisioned even though not yet applied — see module docstring.
    get_or_create_encryption_key()
    return create_engine(f"sqlite:///{settings.db_path}")
