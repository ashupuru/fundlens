"""Application configuration, loaded from environment / .env."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="FUNDLENS_")

    # default_factory, not a plain default, so this reflects the process's
    # actual working directory at startup rather than getting frozen to
    # whatever it was when this module was first imported.
    db_path: Path = Field(default_factory=lambda: Path.cwd() / "fundlens.db")
    cors_allow_origins: list[str] = ["http://localhost:5173"]
    anthropic_api_key: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
