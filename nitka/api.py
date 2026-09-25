"""Synchronous FastAPI application for document intake and review."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from shutil import copyfileobj
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from nitka.config import Settings
from nitka.db import create_db_engine
from nitka.ingestion import SUPPORTED_SUFFIXES, IngestionBusyError, IngestionResult, ingest
from nitka.models import Document
from nitka.queries import get_document, list_documents, stats
from nitka.schemas import DocumentDetail, DocumentItem, DocumentPage, Stats

logger = logging.getLogger(__name__)


def _configure_default_logging() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO)


def _store_upload(upload: UploadFile, destination_dir: Path) -> Path:
    """Persist one supported upload under a generated name inside input_docs."""
    original_name = Path((upload.filename or "").replace("\\", "/")).name
    suffix = Path(original_name).suffix.casefold()
    if not original_name or suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(status_code=422, detail="file must have a .jsonl or .ndjson extension")

    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{uuid4().hex}_{original_name}"
    temporary = destination_dir / f".{uuid4().hex}.upload"
    try:
        with temporary.open("xb") as target:
            copyfileobj(upload.file, target)
        temporary.replace(destination)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="unable to store uploaded file") from error
    return destination


def _item(document: Document) -> DocumentItem:
    return DocumentItem.model_validate(document)


def _detail(document: Document) -> DocumentDetail:
    return DocumentDetail(
        **DocumentItem.model_validate(document).model_dump(),
        body=document.body,
        updated_at=document.updated_at,
        language=document.language,
        region=document.region,
        source_name=document.source_name,
        url=document.url,
        doi=document.doi,
        open_access=document.open_access,
        peer_reviewed=document.peer_reviewed,
        citation_count=document.citation_count,
        page_count=document.page_count,
        word_count=document.word_count,
        author_name=document.author.name if document.author else None,
        organization_name=document.organization.name if document.organization else None,
        tags=[tag.name for tag in document.tags],
    )


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    _configure_default_logging()
    active_settings = settings or Settings()
    active_engine = engine or create_db_engine(active_settings)
    app = FastAPI(title="Document Intake and Review Service", version="0.1.0")

    @app.post("/ingestions", response_model=IngestionResult)
    def create_ingestion(
        file: Annotated[UploadFile, File(description="JSONL or NDJSON file")],
    ) -> IngestionResult:
        uploaded_file = _store_upload(file, active_settings.input_dir)
        try:
            return ingest(active_engine, uploaded_file)
        except IngestionBusyError as error:
            raise HTTPException(
                status_code=409, detail="an ingestion is already running"
            ) from error
        except ValueError as error:
            raise HTTPException(
                status_code=400, detail="uploaded dataset is unavailable"
            ) from error
        except RuntimeError as error:
            raise HTTPException(status_code=500, detail="ingestion failed") from error

    @app.get("/documents", response_model=DocumentPage)
    def read_documents(
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
        date_from: date | None = None,
        date_to: date | None = None,
        tag: str | None = None,
        organization: str | None = None,
        status: str | None = None,
        q: str | None = None,
        sort: Literal["id", "score"] = "id",
    ) -> DocumentPage:
        if date_from and date_to and date_from > date_to:
            raise HTTPException(
                status_code=422, detail="date_from must be before or equal to date_to"
            )
        with Session(active_engine) as session:
            documents, total = list_documents(
                session,
                page=page,
                page_size=page_size,
                date_from=date_from,
                date_to=date_to,
                tag=tag,
                organization=organization,
                status=status,
                query=q,
                sort=sort,
            )
            return DocumentPage(
                items=[_item(document) for document in documents],
                total=total,
                page=page,
                page_size=page_size,
            )

    @app.get("/documents/{document_id}", response_model=DocumentDetail)
    def read_document(document_id: int) -> DocumentDetail:
        with Session(active_engine) as session:
            document = get_document(session, document_id)
            if document is None:
                raise HTTPException(status_code=404, detail="document not found")
            return _detail(document)

    @app.get("/stats", response_model=Stats)
    def read_stats() -> dict:
        with Session(active_engine) as session:
            return stats(session)

    return app


app = create_app()
