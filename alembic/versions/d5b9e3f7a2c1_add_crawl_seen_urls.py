"""add_crawl_seen_urls

Revision ID: d5b9e3f7a2c1
Revises: c3a8e5f1b7d9
Create Date: 2026-10-08 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd5b9e3f7a2c1'
down_revision: Union[str, None] = 'c3a8e5f1b7d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # URL đã xử lý nhưng không lưu thành article — tránh fetch + gọi LLM lại ở mỗi
    # đợt crawl hàng giờ (xem db/models.py::CrawlSeenUrl).
    op.create_table(
        'crawl_seen_urls',
        sa.Column('url', sa.Text(), primary_key=True),
        sa.Column('source_domain', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('attempts', sa.Integer(), server_default=sa.text('1'), nullable=False),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('irrelevant', 'skipped_old', 'duplicate', 'extraction_failed', "
            "'classification_failed', 'bloomberg_resolved', 'bloomberg_unresolved')",
            name='ck_crawl_seen_urls_status',
        ),
    )
    op.create_index('idx_crawl_seen_urls_last_seen_at', 'crawl_seen_urls', ['last_seen_at'])


def downgrade() -> None:
    op.drop_index('idx_crawl_seen_urls_last_seen_at', table_name='crawl_seen_urls')
    op.drop_table('crawl_seen_urls')
