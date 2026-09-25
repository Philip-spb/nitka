from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "postgresql+psycopg://nitka:nitka@127.0.0.1:5433/nitka")


@pytest.fixture
def database_url() -> str:
    return TEST_DATABASE_URL


@pytest.fixture
def db_engine(database_url: str) -> Iterator[Engine]:
    from nitka.models import Base

    schema = f"test_{uuid4().hex}"
    admin_engine = create_engine(database_url)
    with admin_engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(database_url, execution_options={"schema_translate_map": {None: schema}})
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()
