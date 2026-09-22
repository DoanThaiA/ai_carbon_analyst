"""add_evidence_images_to_release_notes

Revision ID: d6f1a4c8b2e5
Revises: c4e8b2f6a9d1
Create Date: 2026-09-22 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'd6f1a4c8b2e5'
down_revision: Union[str, None] = 'c4e8b2f6a9d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'release_notes',
        sa.Column('evidence_images', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('release_notes', 'evidence_images')
