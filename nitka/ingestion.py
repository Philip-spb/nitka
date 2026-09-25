"""Streaming JSONL importer shared by the CLI and HTTP endpoint."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, func, or_, select, text
from sqlalchemy.orm import Session

from nitka.eventlog import emit
from nitka.models import Author, Document, IngestionIssue, IngestionRun, Organization, Tag
from nitka.normalization import Issue, content_fingerprint, normalize_record, record_fingerprint

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


def _get_or_create(session: Session, model: type[Any], name: str, cache: dict[str, Any]) -> Any:
    if name in cache:
        return cache[name]
    entity = session.scalar(select(model).where(model.name == name))
    if entity is None:
        entity = model(name=name)
        session.add(entity)
    cache[name] = entity
    return entity


def _counts(session: Session) -> dict[str, int]:
    return {
        "documents": session.scalar(select(func.count(Document.id))) or 0,
        "authors": session.scalar(select(func.count(Author.id))) or 0,
        "organizations": session.scalar(select(func.count(Organization.id))) or 0,
        "tags": session.scalar(select(func.count(Tag.id))) or 0,
    }


def _tier_counts(session: Session) -> dict[str, int]:
    counts = {"low": 0, "medium": 0, "high": 0}
    for tier, count in session.execute(
        select(Document.quality_tier, func.count(Document.id)).group_by(Document.quality_tier)
    ):
        counts[tier] = count
    return counts


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


def _process_batch(
    session: Session,
    *,
    pending: list[PendingRecord],
    run_id: int,
    counters: Counter[str],
    author_cache: dict[str, Author],
    organization_cache: dict[str, Organization],
    tag_cache: dict[str, Tag],
) -> None:
    if not pending:
        return
    normalized_records = [(record, normalize_record(record.value)) for record in pending]
    fingerprints: set[str] = set()
    for _, result in normalized_records:
        if result.document is not None:
            fingerprint = _deduplication_fingerprint(result.document)
            if fingerprint is not None:
                fingerprints.add(fingerprint)
    dois = {
        result.document["doi"]
        for _, result in normalized_records
        if result.document is not None and result.document["doi"] is not None
    }
    predicates = []
    if fingerprints:
        predicates.append(Document.content_fingerprint.in_(fingerprints))
    if dois:
        predicates.append(Document.doi.in_(dois))
    existing_documents = (
        session.scalars(select(Document).where(or_(*predicates))).all() if predicates else []
    )
    documents_by_fingerprint = {
        document.content_fingerprint: document
        for document in existing_documents
        if document.content_fingerprint is not None
    }
    documents_by_doi = {
        document.doi: document for document in existing_documents if document.doi is not None
    }

    for record, result in normalized_records:
        external_id = (
            result.document["external_id"]
            if result.document is not None
            else _external_id_for_log(record.value)
        )
        for issue in result.issues:
            session.add(
                _make_issue(run_id, record.source_file, record.source_line, issue, external_id)
            )
            counters["warnings"] += 1
            emit(
                "record_warning",
                file=record.source_file,
                line=record.source_line,
                field=issue.field,
                reason=issue.reason,
            )
        if result.document is None:
            counters["skipped"] += 1
            reason = result.issues[0].reason if result.issues else "invalid_document"
            emit(
                "document_not_inserted",
                file=record.source_file,
                line=record.source_line,
                external_id=external_id,
                title=None,
                reason=reason,
            )
            continue

        document_data = result.document
        fingerprint = _deduplication_fingerprint(document_data)
        doi = document_data["doi"]
        duplicate_by_doi = documents_by_doi.get(doi) if doi else None
        duplicate_by_content = documents_by_fingerprint.get(fingerprint) if fingerprint else None
        duplicate = duplicate_by_doi or duplicate_by_content
        if duplicate is not None:
            if duplicate.id is None:
                session.flush()
            deduplication_rule = (
                "doi"
                if duplicate_by_doi is not None
                else "title_body_fingerprint"
                if document_data["body"] is not None
                else "record_fingerprint"
            )
            if duplicate_by_doi and fingerprint and duplicate.content_fingerprint != fingerprint:
                reason = "duplicate_conflicting_content"
            elif duplicate_by_content and doi and duplicate.doi and duplicate.doi != doi:
                reason = "duplicate_conflicting_doi"
            else:
                reason = "duplicate_document"
                if doi and duplicate.doi is None:
                    duplicate.doi = doi
                    documents_by_doi[doi] = duplicate
            duplicate_issue = Issue("document", reason, str(duplicate.id))
            session.add(
                _make_issue(
                    run_id,
                    record.source_file,
                    record.source_line,
                    duplicate_issue,
                    external_id,
                )
            )
            counters["already_imported"] += 1
            counters["warnings"] += 1
            emit(
                "document_not_inserted",
                file=record.source_file,
                line=record.source_line,
                external_id=external_id,
                title=document_data["title"],
                reason=reason,
                deduplication_rule=deduplication_rule,
                existing_document_id=duplicate.id,
            )
            emit(
                "record_warning",
                file=record.source_file,
                line=record.source_line,
                field=duplicate_issue.field,
                reason=duplicate_issue.reason,
            )
            continue

        author_name = document_data.pop("author_name")
        organization_name = document_data.pop("organization_name")
        tags = document_data.pop("tags")
        author = _get_or_create(session, Author, author_name, author_cache) if author_name else None
        organization = (
            _get_or_create(session, Organization, organization_name, organization_cache)
            if organization_name
            else None
        )
        document = Document(
            **document_data,
            content_fingerprint=fingerprint,
            author=author,
            organization=organization,
        )
        document.tags = [_get_or_create(session, Tag, tag, tag_cache) for tag in tags]
        session.add(document)
        if fingerprint:
            documents_by_fingerprint[fingerprint] = document
        if doi:
            documents_by_doi[doi] = document
        counters["inserted"] += 1
    session.flush()
    pending.clear()


def ingest(engine: Engine, input_path: Path) -> dict[str, Any]:
    """Import one JSONL/NDJSON file in one PostgreSQL transaction."""
    input_file = _input_file(input_path)
    counters: Counter[str] = Counter()
    author_cache: dict[str, Author] = {}
    organization_cache: dict[str, Organization] = {}
    tag_cache: dict[str, Tag] = {}
    emit("ingestion_started", input_file=str(input_file))

    try:
        with Session(engine) as session, session.begin():
            locked = session.scalar(
                text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _IMPORT_LOCK}
            )
            if not locked:
                raise IngestionBusyError("another ingestion is already running")
            run = IngestionRun(status="running")
            session.add(run)
            session.flush()
            pending: list[PendingRecord] = []

            for source_file, line_number, raw_line in _iter_lines(input_file):
                counters["processed"] += 1
                if not raw_line.strip():
                    counters["skipped"] += 1
                    issue = Issue("record", "blank_line", "<blank line>")
                    session.add(_make_issue(run.id, source_file, line_number, issue))
                    counters["warnings"] += 1
                    emit("record_skipped", file=source_file, line=line_number, reason=issue.reason)
                    continue
                try:
                    decoded = raw_line.decode("utf-8")
                    source_value = _json_loads(decoded)
                except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
                    counters["skipped"] += 1
                    issue = Issue(
                        "record", "invalid_json", f"<{type(error).__name__}: {str(error)[:160]}>"
                    )
                    session.add(_make_issue(run.id, source_file, line_number, issue))
                    counters["warnings"] += 1
                    emit("record_skipped", file=source_file, line=line_number, reason=issue.reason)
                    continue

                pending.append(
                    PendingRecord(
                        source_file=source_file,
                        source_line=line_number,
                        value=source_value,
                    )
                )
                if len(pending) == _BATCH_SIZE:
                    _process_batch(
                        session,
                        pending=pending,
                        run_id=run.id,
                        counters=counters,
                        author_cache=author_cache,
                        organization_cache=organization_cache,
                        tag_cache=tag_cache,
                    )

            _process_batch(
                session,
                pending=pending,
                run_id=run.id,
                counters=counters,
                author_cache=author_cache,
                organization_cache=organization_cache,
                tag_cache=tag_cache,
            )
            final_counts = _counts(session)
            quality_tier_counts = _tier_counts(session)
            run.status = "completed"
            run.ended_at = datetime.now(UTC)
            run.processed = counters["processed"]
            run.inserted = counters["inserted"]
            run.already_imported = counters["already_imported"]
            run.skipped = counters["skipped"]
            run.warnings = counters["warnings"]
            run.final_document_count = final_counts["documents"]
            run.final_author_count = final_counts["authors"]
            run.final_organization_count = final_counts["organizations"]
            run.final_tag_count = final_counts["tags"]
            session.flush()
            summary = {
                "run_id": run.id,
                "status": run.status,
                "processed": run.processed,
                "inserted": run.inserted,
                "already_imported": run.already_imported,
                "skipped": run.skipped,
                "warnings": run.warnings,
                "final_counts": final_counts,
            }
    except IngestionBusyError:
        raise
    except Exception as error:
        failure_reason = type(error).__name__
        try:
            with Session(engine) as failure_session, failure_session.begin():
                failed_run = IngestionRun(
                    status="failed",
                    ended_at=datetime.now(UTC),
                    processed=counters["processed"],
                    inserted=0,
                    already_imported=counters["already_imported"],
                    skipped=counters["skipped"],
                    warnings=counters["warnings"],
                    failure_reason=failure_reason,
                )
                failure_session.add(failed_run)
                failure_session.flush()
                failed_run_id = failed_run.id
        except Exception:
            failed_run_id = None
        emit("ingestion_failed", run_id=failed_run_id, reason=failure_reason)
        raise RuntimeError("ingestion failed") from error

    emit("scoring_completed", run_id=summary["run_id"], quality_tiers=quality_tier_counts)
    emit("ingestion_completed", **summary)
    return summary
