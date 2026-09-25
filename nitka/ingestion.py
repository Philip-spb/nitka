"""Streaming JSONL importer shared by the CLI and HTTP endpoint."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Engine, or_, select, text
from sqlalchemy.orm import Session

from nitka.eventlog import emit_log
from nitka.models import (
    Author,
    Document,
    IngestionIssue,
    IngestionRun,
    IngestionStatus,
    Organization,
    Tag,
)
from nitka.normalization import (
    Issue,
    NormalizationResult,
    content_fingerprint,
    normalize_record,
    record_fingerprint,
)

SUPPORTED_SUFFIXES = {".jsonl", ".ndjson"}
_IMPORT_LOCK = 4_782_311_903
_BATCH_SIZE = 250


class IngestionBusyError(RuntimeError):
    """Raised when another import transaction holds the application lock."""


@dataclass(frozen=True)
class PendingRecord:
    source_file: str
    source_line: int
    value: Any


@dataclass(frozen=True)
class PreparedRecord:
    record: PendingRecord
    result: NormalizationResult
    fingerprint: str | None
    doi: str | None


@dataclass
class IngestionCounters:
    processed: int = 0
    inserted: int = 0
    already_imported: int = 0
    skipped: int = 0
    warnings: int = 0


class IngestionResult(BaseModel):
    run_id: int
    status: IngestionStatus
    processed: int
    inserted: int
    already_imported: int
    skipped: int
    warnings: int


def _input_file(path: Path) -> Path:
    input_file = path.expanduser().resolve()
    if not input_file.is_file():
        raise ValueError("input path does not exist or is not a file")
    if input_file.suffix.casefold() not in SUPPORTED_SUFFIXES:
        raise ValueError("input file must have a .jsonl or .ndjson extension")
    return input_file


def _json_loads(value: str) -> Any:
    def reject_constant(constant: str) -> Any:
        raise ValueError(f"non-standard JSON constant: {constant}")

    return json.loads(value, parse_constant=reject_constant)


def _make_issue(
    run_id: int,
    source_file: str,
    source_line: int,
    issue: Issue,
    external_id: str | None = None,
) -> IngestionIssue:
    return IngestionIssue(
        ingestion_run_id=run_id,
        source_file=source_file,
        source_line=source_line,
        external_id=external_id,
        field=issue.field,
        reason=issue.reason,
        severity="warning",
        value_preview=issue.value_preview,
    )


def _iter_lines(input_file: Path) -> Iterable[tuple[str, int, bytes]]:
    with input_file.open("rb") as source:
        for line_number, raw_line in enumerate(source, start=1):
            yield input_file.name, line_number, raw_line.rstrip(b"\r\n")


def _deduplication_fingerprint(document: dict[str, Any]) -> str | None:
    fingerprint = content_fingerprint(document["title"], document["body"])
    if fingerprint is not None or document["doi"] is not None:
        return fingerprint
    return record_fingerprint(document)


def _external_id_for_log(value: Any) -> str | None:
    if isinstance(value, dict) and isinstance(value.get("external_id"), str):
        external_id = value["external_id"].strip()
        return external_id[:255] or None
    return None


def ingest(engine: Engine, input_path: Path) -> IngestionResult:
    """Import one JSONL/NDJSON file in one PostgreSQL transaction."""
    return Ingestor(engine, input_path).run()


class Ingestor:
    """Stateful implementation of one atomic document import."""

    def __init__(self, engine: Engine, input_path: Path) -> None:
        self.engine = engine
        self.input_file = _input_file(input_path)
        self.counters = IngestionCounters()
        self.author_cache: dict[str, Author] = {}
        self.organization_cache: dict[str, Organization] = {}
        self.tag_cache: dict[str, Tag] = {}
        self.pending: list[PendingRecord] = []
        self.session: Session | None = None
        self.run_record: IngestionRun | None = None

    def run(self) -> IngestionResult:
        emit_log("ingestion_started", input_file=str(self.input_file))
        try:
            with Session(self.engine) as session, session.begin():
                self.session = session
                self._acquire_lock()
                self._start_run()
                for source_file, line_number, raw_line in _iter_lines(self.input_file):
                    self._process_line(source_file, line_number, raw_line)
                self._process_pending()
                summary = self._complete()
        except IngestionBusyError:
            raise
        except Exception as error:
            self._record_failure(error)
            raise RuntimeError("ingestion failed") from error
        finally:
            self.session = None

        emit_log("scoring_completed", run_id=summary.run_id, scored_documents=summary.inserted)
        emit_log("ingestion_completed", **summary.model_dump(mode="json"))
        return summary

    def _acquire_lock(self) -> None:
        locked = self._session.scalar(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _IMPORT_LOCK}
        )
        if not locked:
            raise IngestionBusyError("another ingestion is already running")

    def _start_run(self) -> None:
        self.run_record = IngestionRun(status=IngestionStatus.RUNNING)
        self._session.add(self.run_record)
        self._session.flush()

    def _process_line(self, source_file: str, line_number: int, raw_line: bytes) -> None:
        self.counters.processed += 1
        if not raw_line.strip():
            self._add_issue(source_file, line_number, Issue("record", "blank_line", "<blank line>"))
            self.counters.skipped += 1
            emit_log("record_skipped", file=source_file, line=line_number, reason="blank_line")
            return
        try:
            source_value = _json_loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
            issue = Issue("record", "invalid_json", f"<{type(error).__name__}: {str(error)[:160]}>")
            self._add_issue(source_file, line_number, issue)
            self.counters.skipped += 1
            emit_log("record_skipped", file=source_file, line=line_number, reason=issue.reason)
            return

        self.pending.append(PendingRecord(source_file, line_number, source_value))
        if len(self.pending) == _BATCH_SIZE:
            self._process_pending()

    def _process_pending(self) -> None:
        if not self.pending:
            return
        records = self._prepare_pending()
        documents_by_fingerprint, documents_by_doi = self._existing_document_indexes(records)

        for prepared in records:
            self._process_record(prepared, documents_by_fingerprint, documents_by_doi)
        self._session.flush()
        self.pending.clear()

    def _prepare_pending(self) -> list[PreparedRecord]:
        prepared_records = []
        for record in self.pending:
            result = normalize_record(record.value)
            document = result.document
            prepared_records.append(
                PreparedRecord(
                    record=record,
                    result=result,
                    fingerprint=(
                        _deduplication_fingerprint(document) if document is not None else None
                    ),
                    doi=document["doi"] if document is not None else None,
                )
            )
        return prepared_records

    def _existing_document_indexes(
        self, records: list[PreparedRecord]
    ) -> tuple[dict[str, Document], dict[str, Document]]:
        fingerprints = {record.fingerprint for record in records if record.fingerprint}
        dois = {record.doi for record in records if record.doi}
        predicates = []
        if fingerprints:
            predicates.append(Document.content_fingerprint.in_(fingerprints))
        if dois:
            predicates.append(Document.doi.in_(dois))
        existing_documents = (
            self._session.scalars(select(Document).where(or_(*predicates))).all()
            if predicates
            else []
        )
        documents_by_fingerprint = {
            document.content_fingerprint: document
            for document in existing_documents
            if document.content_fingerprint is not None
        }
        documents_by_doi = {
            document.doi: document for document in existing_documents if document.doi is not None
        }
        return documents_by_fingerprint, documents_by_doi

    def _process_record(
        self,
        prepared: PreparedRecord,
        documents_by_fingerprint: dict[str, Document],
        documents_by_doi: dict[str, Document],
    ) -> None:
        record = prepared.record
        result = prepared.result
        external_id = (
            result.document["external_id"]
            if result.document is not None
            else _external_id_for_log(record.value)
        )
        for issue in result.issues:
            self._add_issue(record.source_file, record.source_line, issue, external_id)
            emit_log(
                "record_warning",
                file=record.source_file,
                line=record.source_line,
                field=issue.field,
                reason=issue.reason,
            )
        if result.document is None:
            self.counters.skipped += 1
            reason = result.issues[0].reason if result.issues else "invalid_document"
            emit_log(
                "document_not_inserted",
                file=record.source_file,
                line=record.source_line,
                external_id=external_id,
                title=None,
                reason=reason,
            )
            return

        document_data = result.document
        duplicate_by_doi = documents_by_doi.get(prepared.doi) if prepared.doi else None
        duplicate_by_content = (
            documents_by_fingerprint.get(prepared.fingerprint) if prepared.fingerprint else None
        )
        duplicate = duplicate_by_doi or duplicate_by_content
        if duplicate is not None:
            self._record_duplicate(
                record,
                prepared,
                duplicate,
                duplicate_by_doi,
                duplicate_by_content,
                external_id,
            )
            return

        author_name = document_data.pop("author_name")
        organization_name = document_data.pop("organization_name")
        tags = document_data.pop("tags")
        author = (
            self._get_or_create(Author, author_name, self.author_cache) if author_name else None
        )
        organization = (
            self._get_or_create(Organization, organization_name, self.organization_cache)
            if organization_name
            else None
        )
        document = Document(
            **document_data,
            content_fingerprint=prepared.fingerprint,
            author=author,
            organization=organization,
        )
        document.tags = [self._get_or_create(Tag, tag, self.tag_cache) for tag in tags]
        self._session.add(document)
        if prepared.fingerprint:
            documents_by_fingerprint[prepared.fingerprint] = document
        if prepared.doi:
            documents_by_doi[prepared.doi] = document
        self.counters.inserted += 1

    def _record_duplicate(
        self,
        record: PendingRecord,
        prepared: PreparedRecord,
        duplicate: Document,
        duplicate_by_doi: Document | None,
        duplicate_by_content: Document | None,
        external_id: str | None,
    ) -> None:
        if duplicate.id is None:
            self._session.flush()
        if (
            duplicate_by_doi
            and prepared.fingerprint
            and duplicate.content_fingerprint != prepared.fingerprint
        ):
            reason = "duplicate_conflicting_content"
        elif (
            duplicate_by_content
            and prepared.doi
            and duplicate.doi
            and duplicate.doi != prepared.doi
        ):
            reason = "duplicate_conflicting_doi"
        else:
            reason = "duplicate_document"
        self._add_issue(
            record.source_file,
            record.source_line,
            Issue("document", reason, str(duplicate.id)),
            external_id,
        )
        self.counters.already_imported += 1

    def _get_or_create(self, model: type[Any], name: str, cache: dict[str, Any]) -> Any:
        if name in cache:
            return cache[name]
        entity = self._session.scalar(select(model).where(model.name == name))
        if entity is None:
            entity = model(name=name)
            self._session.add(entity)
        cache[name] = entity
        return entity

    def _add_issue(
        self,
        source_file: str,
        source_line: int,
        issue: Issue,
        external_id: str | None = None,
    ) -> None:
        self._session.add(_make_issue(self._run.id, source_file, source_line, issue, external_id))
        self.counters.warnings += 1

    def _complete(self) -> IngestionResult:
        self._run.status = IngestionStatus.COMPLETED
        self._run.ended_at = datetime.now(UTC)
        self._run.processed = self.counters.processed
        self._run.inserted = self.counters.inserted
        self._run.already_imported = self.counters.already_imported
        self._run.skipped = self.counters.skipped
        self._run.warnings = self.counters.warnings
        self._session.flush()
        return IngestionResult(
            run_id=self._run.id,
            status=self._run.status,
            processed=self._run.processed,
            inserted=self._run.inserted,
            already_imported=self._run.already_imported,
            skipped=self._run.skipped,
            warnings=self._run.warnings,
        )

    def _record_failure(self, error: Exception) -> None:
        failure_reason = type(error).__name__
        try:
            with Session(self.engine) as failure_session, failure_session.begin():
                failed_run = IngestionRun(
                    status=IngestionStatus.FAILED,
                    ended_at=datetime.now(UTC),
                    processed=self.counters.processed,
                    inserted=0,
                    already_imported=self.counters.already_imported,
                    skipped=self.counters.skipped,
                    warnings=self.counters.warnings,
                    failure_reason=failure_reason,
                )
                failure_session.add(failed_run)
                failure_session.flush()
                failed_run_id = failed_run.id
        except Exception:
            failed_run_id = None
        emit_log("ingestion_failed", run_id=failed_run_id, reason=failure_reason)

    @property
    def _session(self) -> Session:
        if self.session is None:
            raise RuntimeError("ingestor session is not active")
        return self.session

    @property
    def _run(self) -> IngestionRun:
        if self.run_record is None or self.run_record.id is None:
            raise RuntimeError("ingestion run has not been started")
        return self.run_record
