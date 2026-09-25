"""Read-side queries for documents and aggregate statistics."""

from __future__ import annotations

from datetime import date
from typing import Literal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from nitka.models import Author, Document, Organization, Tag, document_tags


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_documents(
    session: Session,
    *,
    page: int,
    page_size: int,
    date_from: date | None,
    date_to: date | None,
    tag: str | None,
    organization: str | None,
    status: str | None,
    query: str | None,
    sort: Literal["id", "score"],
) -> tuple[list[Document], int]:
    statement = select(Document)
    if date_from is not None:
        statement = statement.where(Document.published_at >= date_from)
    if date_to is not None:
        statement = statement.where(Document.published_at <= date_to)
    if tag:
        statement = statement.where(Document.tags.any(Tag.name == tag.strip().casefold()))
    if organization:
        statement = statement.where(
            Document.organization.has(Organization.name == organization.strip())
        )
    if status:
        statement = statement.where(Document.status == status.strip().casefold())
    if query:
        pattern = f"%{_escape_like(query)}%"
        statement = statement.where(
            or_(
                Document.title.ilike(pattern, escape="\\"),
                Document.body.ilike(pattern, escape="\\"),
            )
        )

    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    statement = statement.options(
        selectinload(Document.author),
        selectinload(Document.organization),
        selectinload(Document.tags),
    )
    if sort == "score":
        statement = statement.order_by(Document.completeness_score.desc(), Document.id.asc())
    else:
        statement = statement.order_by(Document.id.asc())
    documents = session.scalars(statement.offset((page - 1) * page_size).limit(page_size)).all()
    return documents, total


def get_document(session: Session, document_id: int) -> Document | None:
    statement = (
        select(Document)
        .where(Document.id == document_id)
        .options(
            selectinload(Document.author),
            selectinload(Document.organization),
            selectinload(Document.tags),
        )
    )
    return session.scalar(statement)


def stats(session: Session) -> dict[str, object]:
    document_count = session.scalar(select(func.count(Document.id))) or 0
    result: dict[str, object] = {
        "documents": document_count,
        "authors": session.scalar(select(func.count(Author.id))) or 0,
        "organizations": session.scalar(select(func.count(Organization.id))) or 0,
        "tags": session.scalar(select(func.count(Tag.id))) or 0,
        "average_completeness_score": session.scalar(select(func.avg(Document.completeness_score))),
        "quality_tiers": {"low": 0, "medium": 0, "high": 0},
        "statuses": {},
        "organizations_by_document": {},
        "tags_by_document": {},
    }
    tiers = result["quality_tiers"]
    assert isinstance(tiers, dict)
    for tier, count in session.execute(
        select(Document.quality_tier, func.count(Document.id)).group_by(Document.quality_tier)
    ):
        tiers[tier] = count

    statuses = result["statuses"]
    assert isinstance(statuses, dict)
    for status, count in session.execute(
        select(Document.status, func.count(Document.id)).group_by(Document.status)
    ):
        statuses[status or "unknown"] = statuses.get(status or "unknown", 0) + count

    organizations = result["organizations_by_document"]
    assert isinstance(organizations, dict)
    for name, count in session.execute(
        select(Organization.name, func.count(Document.id))
        .join(Document, Document.organization_id == Organization.id)
        .group_by(Organization.name)
    ):
        organizations[name] = count

    tags = result["tags_by_document"]
    assert isinstance(tags, dict)
    for name, count in session.execute(
        select(Tag.name, func.count(document_tags.c.document_id))
        .join(document_tags, document_tags.c.tag_id == Tag.id)
        .group_by(Tag.name)
    ):
        tags[name] = count
    return result
