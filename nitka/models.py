"""SQLAlchemy models for the document intake database."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    Enum as SqlEnum,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


document_tags = Table(
    "document_tags",
    Base.metadata,
    Column("document_id", ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Author(Base):
    __tablename__ = "authors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)


class IngestionStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[IngestionStatus] = mapped_column(
        SqlEnum(
            IngestionStatus,
            name="ingestion_run_status",
            values_callable=lambda status: [item.value for item in status],
        ),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    inserted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    already_imported: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    skipped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    warnings: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("length(trim(title)) > 0", name="documents_title_not_blank"),
        CheckConstraint(
            "citation_count IS NULL OR citation_count >= 0",
            name="documents_citation_count_nonnegative",
        ),
        CheckConstraint(
            "page_count IS NULL OR page_count >= 0", name="documents_page_count_nonnegative"
        ),
        CheckConstraint(
            "word_count IS NULL OR word_count >= 0", name="documents_word_count_nonnegative"
        ),
        CheckConstraint("completeness_score BETWEEN 0 AND 100", name="documents_score_range"),
        CheckConstraint(
            "quality_tier IN ('low', 'medium', 'high')", name="documents_quality_tier_valid"
        ),
        UniqueConstraint("doi", name="documents_doi_unique"),
        UniqueConstraint("content_fingerprint", name="documents_content_fingerprint_unique"),
        Index("documents_published_at_idx", "published_at"),
        Index("documents_status_idx", "status"),
        Index("documents_score_idx", "completeness_score"),
        Index("documents_organization_id_idx", "organization_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    content_fingerprint: Mapped[str | None] = mapped_column(String(64))
    external_id: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    abstract: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[object | None] = mapped_column(Date)
    updated_at: Mapped[object | None] = mapped_column(Date)
    status: Mapped[str | None] = mapped_column(String(20))
    document_type: Mapped[str | None] = mapped_column(String(100))
    language: Mapped[str | None] = mapped_column(String(20))
    region: Mapped[str | None] = mapped_column(String(255))
    source_name: Mapped[str | None] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(Text)
    doi: Mapped[str | None] = mapped_column(String(255))
    open_access: Mapped[bool | None] = mapped_column()
    peer_reviewed: Mapped[bool | None] = mapped_column()
    citation_count: Mapped[int | None] = mapped_column(Integer)
    page_count: Mapped[int | None] = mapped_column(Integer)
    word_count: Mapped[int | None] = mapped_column(Integer)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("authors.id"))
    organization_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"))
    completeness_score: Mapped[int] = mapped_column(Integer, nullable=False)
    quality_tier: Mapped[str] = mapped_column(String(10), nullable=False)

    author: Mapped[Author | None] = relationship()
    organization: Mapped[Organization | None] = relationship()
    tags: Mapped[list[Tag]] = relationship(secondary=document_tags, lazy="selectin")


class IngestionIssue(Base):
    __tablename__ = "ingestion_issues"

    id: Mapped[int] = mapped_column(primary_key=True)
    ingestion_run_id: Mapped[int] = mapped_column(
        ForeignKey("ingestion_runs.id", ondelete="CASCADE"), nullable=False
    )
    source_file: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_line: Mapped[int] = mapped_column(Integer, nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255))
    field: Mapped[str] = mapped_column(String(100), nullable=False)
    reason: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), default="warning", nullable=False)
    value_preview: Mapped[str] = mapped_column(String(200), nullable=False)

    ingestion_run: Mapped[IngestionRun] = relationship()
