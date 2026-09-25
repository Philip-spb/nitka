"""Persistence operations used by document ingestion."""

from __future__ import annotations

from typing import TypeVar

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from nitka.models import Author, Document, Organization, Tag

NamedEntity = TypeVar("NamedEntity", Author, Organization, Tag)


class IngestionRepository:
    """Database access required while importing one dataset."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.author_cache: dict[str, Author] = {}
        self.organization_cache: dict[str, Organization] = {}
        self.tag_cache: dict[str, Tag] = {}

    def find_document_indexes(
        self, fingerprints: set[str], dois: set[str]
    ) -> tuple[dict[str, Document], dict[str, Document]]:
        """Return existing documents keyed by their supported deduplication values."""
        predicates = []
        if fingerprints:
            predicates.append(Document.content_fingerprint.in_(fingerprints))
        if dois:
            predicates.append(Document.doi.in_(dois))
        existing_documents = self.session.scalars(select(Document).where(or_(*predicates))).all() if predicates else []
        documents_by_fingerprint = {
            document.content_fingerprint: document
            for document in existing_documents
            if document.content_fingerprint is not None
        }
        documents_by_doi = {document.doi: document for document in existing_documents if document.doi is not None}
        return documents_by_fingerprint, documents_by_doi

    def get_or_create_author(self, name: str) -> Author:
        return self._get_or_create(Author, name, self.author_cache)

    def get_or_create_organization(self, name: str) -> Organization:
        return self._get_or_create(Organization, name, self.organization_cache)

    def get_or_create_tag(self, name: str) -> Tag:
        return self._get_or_create(Tag, name, self.tag_cache)

    def _get_or_create(self, model: type[NamedEntity], name: str, cache: dict[str, NamedEntity]) -> NamedEntity:
        if name in cache:
            return cache[name]
        entity = self.session.scalar(select(model).where(model.name == name))
        if entity is None:
            entity = model(name=name)
            self.session.add(entity)
        cache[name] = entity
        return entity
