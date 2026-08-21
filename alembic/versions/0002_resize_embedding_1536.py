"""resize embedding column from 384 to 1536 dims (cohere embed-v4.0)

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-18
"""
from typing import Sequence, Union

from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_DIM = 384
NEW_DIM = 1536


def upgrade() -> None:
    # Drop the HNSW index before altering column type — PostgreSQL requires this
    op.drop_index("idx_chunks_embedding", table_name="chunks")

    op.alter_column(
        "chunks",
        "embedding",
        type_=Vector(NEW_DIM),
        postgresql_using=f"embedding::vector({NEW_DIM})",
    )

    # Re-create the HNSW index on the new dimension
    op.create_index(
        "idx_chunks_embedding",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index("idx_chunks_embedding", table_name="chunks")

    op.alter_column(
        "chunks",
        "embedding",
        type_=Vector(OLD_DIM),
        postgresql_using=f"embedding::vector({OLD_DIM})",
    )

    op.create_index(
        "idx_chunks_embedding",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
