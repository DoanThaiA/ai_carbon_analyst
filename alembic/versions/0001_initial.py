"""initial schema: articles + chunks

Revision ID: 0001
Revises:
Create Date: 2026-08-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIM = 384


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "articles",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_tier", sa.CHAR(1)),
        sa.Column("title", sa.Text()),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.CHAR(64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("date_confidence", sa.Text(), nullable=False, server_default="unknown"),
        sa.Column("crawled_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("is_relevant", sa.Boolean()),
        sa.Column("category", postgresql.ARRAY(sa.Text())),
        sa.UniqueConstraint("url", name="uq_articles_url"),
        sa.UniqueConstraint("content_hash", name="uq_articles_content_hash"),
        sa.CheckConstraint("source_tier IN ('A', 'B', 'C')", name="ck_articles_source_tier"),
        sa.CheckConstraint(
            "date_confidence IN ('metadata', 'url', 'unknown')", name="ck_articles_date_confidence"
        ),
        sa.CheckConstraint(
            "category <@ ARRAY['energy_fossil_fuels', 'carbon_credits', 'policy']::text[]",
            name="ck_articles_category",
        ),
    )
    op.create_index(
        "idx_articles_published_relevant",
        "articles",
        ["published_at"],
        postgresql_where=sa.text("is_relevant = true"),
    )

    op.create_table(
        "chunks",
        sa.Column("chunk_id", sa.BigInteger(), primary_key=True),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM)),
        sa.Column(
            "content_tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', content)", persisted=True),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "source_type", "source_id", "chunk_index", name="uq_chunks_source_chunk_index"
        ),
        sa.CheckConstraint("source_type IN ('report', 'article')", name="ck_chunks_source_type"),
    )
    op.create_index("idx_chunks_source", "chunks", ["source_type", "source_id"])
    op.create_index(
        "idx_chunks_embedding",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index("idx_chunks_tsv", "chunks", ["content_tsv"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_table("chunks")
    op.drop_table("articles")
    op.execute("DROP EXTENSION IF EXISTS vector")
