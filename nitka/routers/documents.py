"""Routes for retrieving documents."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.orm import Session

from nitka.api_dependencies import DatabaseEngine
from nitka.repositories.documents import DocumentRepository
from nitka.schemas import DocumentDetail, DocumentItem, DocumentPage

router = APIRouter(tags=["documents"])


@router.get("/documents", response_model=DocumentPage)
def read_documents(
    engine: DatabaseEngine,
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
        raise HTTPException(status_code=422, detail="date_from must be before or equal to date_to")
    with Session(engine) as session:
        repository = DocumentRepository(session)
        documents, total = repository.list_documents(
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
            items=[DocumentItem.model_validate(document) for document in documents],
            total=total,
            page=page,
            page_size=page_size,
        )


@router.get("/documents/{document_id}", response_model=DocumentDetail)
def read_document(engine: DatabaseEngine, document_id: int) -> DocumentDetail:
    with Session(engine) as session:
        document = DocumentRepository(session).get_document(document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="document not found")
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
