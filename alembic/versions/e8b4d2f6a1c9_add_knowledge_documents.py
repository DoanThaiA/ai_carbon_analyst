"""add knowledge_documents (tài liệu tri thức admin tải lên cho Jenny) + chunks.source_type 'knowledge'

Gộp luôn 2 head đang tách nhánh từ d8b3f1a6c4e2 (a4c7e2f9b1d5 report_lessons, a9e4c7b2d5f1 report_qc_reviews) —
trước revision này `alembic upgrade head` báo lỗi nhiều head.

Revision ID: e8b4d2f6a1c9
Revises: a4c7e2f9b1d5, a9e4c7b2d5f1
Create Date: 2026-10-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision: str = 'e8b4d2f6a1c9'
down_revision: Union[str, Sequence[str], None] = ('a4c7e2f9b1d5', 'a9e4c7b2d5f1')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SECTIONS = "ARRAY['1','2','dev','3','4','8','biz']::text[]"


def upgrade() -> None:
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("publisher", sa.Text(), nullable=True),
        sa.Column("issued_on", sa.Date(), nullable=True),
        sa.Column("doc_kind", sa.Text(), nullable=False, server_default="reference"),
        sa.Column("language", sa.Text(), nullable=False, server_default="vi"),
        sa.Column("file_name", sa.Text(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("file_key", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.CHAR(64), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("warnings", JSONB(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("char_count", sa.Integer(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("sections", JSONB(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("use_in_report", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("report_sections", ARRAY(sa.Text()), nullable=True),
        sa.Column("use_in_chat", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("valid_from", sa.Date(), nullable=False, server_default=sa.func.current_date()),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("uploaded_by", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'failed', 'deleted')", name="ck_knowledge_documents_status"
        ),
        sa.CheckConstraint("media_type IN ('pdf', 'docx')", name="ck_knowledge_documents_media_type"),
        sa.CheckConstraint(
            "doc_kind IN ('regulation', 'research', 'market_report', 'internal', 'reference')",
            name="ck_knowledge_documents_doc_kind",
        ),
        sa.CheckConstraint("language IN ('vi', 'en')", name="ck_knowledge_documents_language"),
        sa.CheckConstraint(
            f"report_sections IS NULL OR report_sections <@ {_SECTIONS}", name="ck_knowledge_documents_report_sections"
        ),
        sa.UniqueConstraint("sha256", name="uq_knowledge_documents_sha256"),
    )
    op.create_index("ix_knowledge_documents_status_active", "knowledge_documents", ["status", "is_active"])

    op.drop_constraint("ck_chunks_source_type", "chunks", type_="check")
    op.create_check_constraint(
        "ck_chunks_source_type", "chunks", "source_type IN ('report', 'article', 'knowledge')"
    )
    op.add_column("chunks", sa.Column("meta", JSONB(), nullable=True))
    # Index MỘT PHẦN cho đoạn tài liệu (xem docstring db/models.py::Chunk) — truy vấn phải ghi literal
    # source_type = 'knowledge' (không bind param) để planner dùng được 2 index này.
    op.execute(
        "CREATE INDEX idx_chunks_knowledge_embedding ON chunks USING hnsw (embedding vector_cosine_ops) "
        "WHERE source_type = 'knowledge'"
    )
    op.execute(
        "CREATE INDEX idx_chunks_knowledge_tsv_simple ON chunks USING gin (to_tsvector('simple', content)) "
        "WHERE source_type = 'knowledge'"
    )


def downgrade() -> None:
    op.execute("DELETE FROM chunks WHERE source_type = 'knowledge'")
    op.drop_index("idx_chunks_knowledge_tsv_simple", table_name="chunks")
    op.drop_index("idx_chunks_knowledge_embedding", table_name="chunks")
    op.drop_column("chunks", "meta")
    op.drop_constraint("ck_chunks_source_type", "chunks", type_="check")
    op.create_check_constraint("ck_chunks_source_type", "chunks", "source_type IN ('report', 'article')")
    op.drop_index("ix_knowledge_documents_status_active", table_name="knowledge_documents")
    op.drop_table("knowledge_documents")
