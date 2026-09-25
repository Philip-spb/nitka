"""Application configuration loaded from environment variables."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://nitka:nitka@127.0.0.1:5433/nitka"
    input_dir: Path = Path("input_docs")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
