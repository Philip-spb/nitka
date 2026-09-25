"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import Depends, Request
from sqlalchemy import Engine

from nitka.config import Settings


def get_settings(request: Request) -> Settings:
    """Return the settings configured for the current application."""
    return cast(Settings, request.app.state.settings)


def get_engine(request: Request) -> Engine:
    """Return the database engine configured for the current application."""
    return cast(Engine, request.app.state.engine)


AppSettings = Annotated[Settings, Depends(get_settings)]
DatabaseEngine = Annotated[Engine, Depends(get_engine)]
