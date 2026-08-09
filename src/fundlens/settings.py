"""Application configuration, loaded from environment / .env."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="FUNDLENS_")

    db_path: Path = Path.home() / "Library/Application Support/FundLens/fundlens.db"
    cors_allow_origins: list[str] = ["http://localhost:5173"]
    anthropic_api_key: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
