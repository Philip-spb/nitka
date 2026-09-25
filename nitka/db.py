"""Synchronous PostgreSQL engine creation."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine

from nitka.config import Settings


def create_db_engine(settings: Settings | None = None) -> Engine:
    active_settings = settings or Settings()
    return create_engine(active_settings.database_url, pool_pre_ping=True)
