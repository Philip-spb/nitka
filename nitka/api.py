"""Synchronous FastAPI application for document intake and review."""

from __future__ import annotations

from fastapi import FastAPI
from sqlalchemy import Engine

from nitka.api_helpers import configure_default_logging
from nitka.config import Settings
from nitka.db import create_db_engine
from nitka.routers.documents import router as documents_router
from nitka.routers.ingestions import router as ingestions_router
from nitka.routers.stats import router as stats_router


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    configure_default_logging()
    active_settings = settings or Settings()
    active_engine = engine or create_db_engine(active_settings)
    app = FastAPI(title="Document Intake and Review Service", version="0.1.0")
    app.state.settings = active_settings
    app.state.engine = active_engine
    app.include_router(ingestions_router)
    app.include_router(documents_router)
    app.include_router(stats_router)

    return app


app = create_app()
