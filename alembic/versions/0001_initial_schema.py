"""Initial document intake schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-09-25
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None

_ingestion_run_status = postgresql.ENUM(
    "running", "completed", "failed", name="ingestion_run_status", create_type=False
)


def upgrade() -> None:
    _ingestion_run_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "authors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False, unique=True),
    )
    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False, unique=True),
    )
    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False, unique=True),
    )
    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("status", _ingestion_run_status, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("inserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("already_imported", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warnings", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_reason", sa.Text()),
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("content_fingerprint", sa.String(length=64)),
        sa.Column("external_id", sa.String(length=255)),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("abstract", sa.Text()),
        sa.Column("body", sa.Text()),
        sa.Column("published_at", sa.Date()),
        sa.Column("updated_at", sa.Date()),
        sa.Column("status", sa.String(length=20)),
        sa.Column("document_type", sa.String(length=100)),
        sa.Column("language", sa.String(length=20)),
        sa.Column("region", sa.String(length=255)),
        sa.Column("source_name", sa.String(length=255)),
        sa.Column("url", sa.Text()),
        sa.Column("doi", sa.String(length=255)),
        sa.Column("open_access", sa.Boolean()),
        sa.Column("peer_reviewed", sa.Boolean()),
        sa.Column("citation_count", sa.Integer()),
        sa.Column("page_count", sa.Integer()),
        sa.Column("word_count", sa.Integer()),
        sa.Column("author_id", sa.Integer(), sa.ForeignKey("authors.id")),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id")),
        sa.Column("completeness_score", sa.Integer(), nullable=False),
        sa.Column("quality_tier", sa.String(length=10), nullable=False),
        sa.CheckConstraint("length(trim(title)) > 0", name="documents_title_not_blank"),
        sa.CheckConstraint(
            "citation_count IS NULL OR citation_count >= 0",
            name="documents_citation_count_nonnegative",
        ),
        sa.CheckConstraint(
            "page_count IS NULL OR page_count >= 0", name="documents_page_count_nonnegative"
        ),
        sa.CheckConstraint(
            "word_count IS NULL OR word_count >= 0", name="documents_word_count_nonnegative"
        ),
        sa.CheckConstraint("completeness_score BETWEEN 0 AND 100", name="documents_score_range"),
        sa.CheckConstraint(
            "quality_tier IN ('low', 'medium', 'high')", name="documents_quality_tier_valid"
        ),
        sa.UniqueConstraint("doi", name="documents_doi_unique"),
        sa.UniqueConstraint("content_fingerprint", name="documents_content_fingerprint_unique"),
    )
    op.create_index("documents_published_at_idx", "documents", ["published_at"])
    op.create_index("documents_status_idx", "documents", ["status"])
    op.create_index("documents_score_idx", "documents", ["completeness_score"])
    op.create_index("documents_organization_id_idx", "documents", ["organization_id"])
    op.create_table(
        "document_tags",
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tag_id", sa.Integer(), sa.ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
        ),
    )
    op.create_table(
        "ingestion_issues",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "ingestion_run_id",
            sa.Integer(),
            sa.ForeignKey("ingestion_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_file", sa.String(length=1024), nullable=False),
        sa.Column("source_line", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(length=255)),
        sa.Column("field", sa.String(length=100), nullable=False),
        sa.Column("reason", sa.String(length=100), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False, server_default="warning"),
        sa.Column("value_preview", sa.String(length=200), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ingestion_issues")
    op.drop_table("document_tags")
    op.drop_index("documents_organization_id_idx", table_name="documents")
    op.drop_index("documents_score_idx", table_name="documents")
    op.drop_index("documents_status_idx", table_name="documents")
    op.drop_index("documents_published_at_idx", table_name="documents")
    op.drop_table("documents")
    op.drop_table("ingestion_runs")
    _ingestion_run_status.drop(op.get_bind(), checkfirst=True)
    op.drop_table("tags")
    op.drop_table("organizations")
    op.drop_table("authors")
