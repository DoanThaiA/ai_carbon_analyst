"""add_note_date_to_release_notes

Revision ID: c4e8b2f6a9d1
Revises: b3f9a7c1d5e8
Create Date: 2026-09-22 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4e8b2f6a9d1'
down_revision: Union[str, None] = 'b3f9a7c1d5e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'release_notes',
        sa.Column('note_date', sa.Date(), server_default=sa.text('CURRENT_DATE'), nullable=False),
    )


def downgrade() -> None:
    op.drop_column('release_notes', 'note_date')
