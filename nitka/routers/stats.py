"""Routes for aggregate document statistics."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy.orm import Session

from nitka.api_dependencies import DatabaseEngine
from nitka.repositories.documents import DocumentRepository
from nitka.schemas import Stats

router = APIRouter(tags=["stats"])


@router.get("/stats", response_model=Stats)
def read_stats(engine: DatabaseEngine) -> dict[str, object]:
    with Session(engine) as session:
        return DocumentRepository(session).stats()
