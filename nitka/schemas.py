"""Pydantic contracts returned by the HTTP API."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class DocumentItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    external_id: str | None
    title: str
    abstract: str | None
    published_at: date | None
    status: str | None
    document_type: str | None
    completeness_score: int
    quality_tier: str


class DocumentDetail(DocumentItem):
    body: str | None
    updated_at: date | None
    language: str | None
    region: str | None
    source_name: str | None
    url: str | None
    doi: str | None
    open_access: bool | None
    peer_reviewed: bool | None
    citation_count: int | None
    page_count: int | None
    word_count: int | None
    author_name: str | None
    organization_name: str | None
    tags: list[str]


class DocumentPage(BaseModel):
    items: list[DocumentItem]
    total: int
    page: int
    page_size: int


class IngestionResponse(BaseModel):
    run_id: int
    status: str
    processed: int
    inserted: int
    already_imported: int
    skipped: int
    warnings: int


class Stats(BaseModel):
    documents: int
    authors: int
    organizations: int
    tags: int
    average_completeness_score: float | None
    quality_tiers: dict[str, int] = Field(default_factory=dict)
    statuses: dict[str, int] = Field(default_factory=dict)
    organizations_by_document: dict[str, int] = Field(default_factory=dict)
    tags_by_document: dict[str, int] = Field(default_factory=dict)
