"""add_region_to_articles

Revision ID: f1a2b3c4d5e6
Revises: e3bfab111b29
Create Date: 2026-08-25 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = 'e3bfab111b29'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'articles',
        sa.Column('region', sa.Text(), server_default='international', nullable=False),
    )
    op.create_check_constraint(
        'ck_articles_region', 'articles', "region IN ('vietnam', 'international')",
    )


def downgrade() -> None:
    op.drop_constraint('ck_articles_region', 'articles', type_='check')
    op.drop_column('articles', 'region')
