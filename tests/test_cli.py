from __future__ import annotations

from pathlib import Path

import pytest

from nitka import cli
from nitka.ingestion import IngestionBusyError


@pytest.mark.parametrize(
    "error",
    [IngestionBusyError("already running"), RuntimeError("synthetic failure")],
)
def test_ingest_errors_return_a_nonzero_status(monkeypatch, tmp_path, error):
    monkeypatch.setattr(cli, "create_db_engine", lambda _settings: object())

    def fail_ingestion(_engine, _path: Path):
        raise error

    monkeypatch.setattr(cli, "ingest", fail_ingestion)

    assert cli.main(["ingest", "--input", str(tmp_path / "records.jsonl")]) == 1
