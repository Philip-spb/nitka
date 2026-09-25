from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nitka.ingestion import ingest
from nitka.models import Document, IngestionIssue, IngestionRun

FIXTURE_FILE = Path(__file__).parent / "fixtures" / "sample.jsonl"


def test_imports_normalized_documents_and_records_all_outcomes(db_engine):
    summary = ingest(db_engine, FIXTURE_FILE)

    assert summary.status == "completed"
    assert summary.processed == summary.inserted + summary.already_imported + summary.skipped
    assert summary.inserted == 5
    assert summary.skipped == 4
    assert summary.warnings > 0

    with Session(db_engine) as session:
        assert session.scalar(select(func.count(Document.id))) == 5
        document = (
            session.execute(select(Document).where(Document.external_id == "shared-id").order_by(Document.id))
            .scalars()
            .first()
        )
        assert document is not None
        assert document.status == "published"
        assert document.language == "en"
        assert document.completeness_score == 100
        assert document.quality_tier == "high"
        assert session.scalar(select(func.count(IngestionIssue.id))) >= summary.warnings
        assert session.scalar(select(func.count(IngestionRun.id))) == 1


def test_repeat_import_does_not_add_any_document(db_engine):
    first = ingest(db_engine, FIXTURE_FILE)
    second = ingest(db_engine, FIXTURE_FILE)

    assert second.inserted == 0
    assert second.already_imported == first.inserted
    with Session(db_engine) as session:
        assert session.scalar(select(func.count(Document.id))) == first.inserted


def test_invalid_document_emits_a_not_inserted_event(db_engine, tmp_path, caplog):
    path = tmp_path / "invalid.jsonl"
    path.write_text('{"external_id":"doc-1", "title":null}\n', encoding="utf-8")

    caplog.set_level(logging.INFO, logger="nitka.eventlog")
    summary = ingest(db_engine, path)

    assert summary.skipped == 1
    events = [json.loads(record.message) for record in caplog.records]
    invalid_event = next(event for event in events if event["event"] == "document_not_inserted")
    assert invalid_event == {
        "timestamp": invalid_event["timestamp"],
        "event": "document_not_inserted",
        "file": "invalid.jsonl",
        "line": 1,
        "external_id": "doc-1",
        "title": None,
        "reason": "missing_required_title",
    }


def test_same_normalized_title_and_body_in_two_files_is_a_duplicate(db_engine, tmp_path):
    first_path = tmp_path / "first.jsonl"
    second_path = tmp_path / "second.jsonl"
    first_path.write_text(
        '{"title":"Climate Policy", "body":"A  document about policy.", '
        '"published_at":"2024-01-01", "updated_at":"2024-01-01"}\n',
        encoding="utf-8",
    )
    second_path.write_text(
        '{"title":"  climate policy ", "body":"A document about policy.", '
        '"published_at":"2024-01-01", "updated_at":"2024-01-01"}\n',
        encoding="utf-8",
    )

    first = ingest(db_engine, first_path)
    second = ingest(db_engine, second_path)

    assert first.inserted == 1
    assert second.inserted == 0
    assert second.already_imported == 1
    assert second.warnings == 1
    with Session(db_engine) as session:
        assert session.scalar(select(func.count(Document.id))) == 1
        assert session.scalar(select(IngestionIssue.reason)) == "duplicate_document"
        assert session.scalar(select(IngestionIssue.value_preview)) == "1"


def test_duplicate_content_does_not_enrich_the_stored_document_with_a_doi(db_engine, tmp_path):
    first_path = tmp_path / "without-doi.jsonl"
    second_path = tmp_path / "with-doi.jsonl"
    first_path.write_text('{"title":"Document", "body":"Full text"}\n', encoding="utf-8")
    second_path.write_text(
        '{"title":"document", "body":"Full text", "doi":"10.1234/EXAMPLE"}\n',
        encoding="utf-8",
    )

    ingest(db_engine, first_path)
    summary = ingest(db_engine, second_path)

    assert summary.inserted == 0
    with Session(db_engine) as session:
        assert session.scalar(select(Document.doi)) is None


def test_same_bodyless_normalized_record_in_two_files_is_a_duplicate(db_engine, tmp_path):
    first_path = tmp_path / "first.jsonl"
    second_path = tmp_path / "second.jsonl"
    first_path.write_text(
        '{"external_id":"DOC-1", "title":"Policy Brief", "status":"PUBLISHED"}\n',
        encoding="utf-8",
    )
    second_path.write_text(
        '{"external_id":"doc-1", "title":"  policy brief ", "status":"published"}\n',
        encoding="utf-8",
    )

    first = ingest(db_engine, first_path)
    second = ingest(db_engine, second_path)

    assert first.inserted == 1
    assert second.inserted == 0
    assert second.already_imported == 1
    with Session(db_engine) as session:
        assert session.scalar(select(func.count(Document.id))) == 1
        assert session.scalar(select(Document.content_fingerprint)) is not None


def test_same_title_with_different_external_ids_remains_two_documents(db_engine, tmp_path):
    path = tmp_path / "records.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"external_id":"doc-1", "title":"Policy Brief"}',
                '{"external_id":"doc-2", "title":"Policy Brief"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    summary = ingest(db_engine, path)

    assert summary.inserted == 2


def test_same_external_id_does_not_overwrite_a_distinct_source_record(db_engine, tmp_path):
    path = tmp_path / "records.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"external_id":"same","title":"First"}',
                '{"external_id":"same","title":"Second"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    summary = ingest(db_engine, path)

    assert summary.inserted == 2
    with db_engine.connect() as connection:
        assert connection.scalar(select(func.count(Document.id))) == 2


def test_changed_document_content_is_stored_as_a_new_document(db_engine, tmp_path):
    path = tmp_path / "records.jsonl"
    path.write_text('{"title":"First", "body":"Original"}\n', encoding="utf-8")
    first = ingest(db_engine, path)
    path.write_text('{"title":"First", "body":"Changed"}\n', encoding="utf-8")

    second = ingest(db_engine, path)

    assert first.inserted == 1
    assert second.inserted == 1
    with Session(db_engine) as session:
        assert session.scalar(select(func.count(Document.id))) == 2


def test_fatal_import_failure_rolls_back_documents_and_records_failed_run(db_engine, tmp_path, monkeypatch):
    from nitka import ingestion

    (tmp_path / "records.jsonl").write_text('{"title":"First"}\n', encoding="utf-8")

    def fail_normalization(_value):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(ingestion, "normalize_record", fail_normalization)
    with pytest.raises(RuntimeError, match="ingestion failed"):
        ingest(db_engine, tmp_path / "records.jsonl")

    with db_engine.connect() as connection:
        assert connection.scalar(select(func.count(Document.id))) == 0
        assert connection.scalar(select(func.count(IngestionRun.id))) == 1
        assert connection.scalar(select(IngestionRun.status)) == "failed"


def test_input_path_must_be_a_jsonl_file(db_engine, tmp_path):
    with pytest.raises(ValueError, match="file"):
        ingest(db_engine, tmp_path)
