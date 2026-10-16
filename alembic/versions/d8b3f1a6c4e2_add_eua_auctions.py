"""add eua_auctions (lịch + kết quả đấu giá sơ cấp EUA trên EEX)

Revision ID: d8b3f1a6c4e2
Revises: c2f7a9e4d6b1
Create Date: 2026-10-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd8b3f1a6c4e2'
down_revision: Union[str, None] = 'c2f7a9e4d6b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "eua_auctions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("auction_date", sa.Date(), nullable=False),
        sa.Column("auction_name", sa.Text(), nullable=False),
        sa.Column("contract", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("auction_price", sa.Float(), nullable=True),
        sa.Column("cover_ratio", sa.Float(), nullable=True),
        sa.Column("total_bidders", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("auction_date", "auction_name", name="uq_eua_auctions_date_name"),
        sa.CheckConstraint("status IN ('scheduled', 'successful', 'cancelled')", name="ck_eua_auctions_status"),
    )
    op.create_index("idx_eua_auctions_auction_date", "eua_auctions", ["auction_date"])


def downgrade() -> None:
    op.drop_index("idx_eua_auctions_auction_date", table_name="eua_auctions")
    op.drop_table("eua_auctions")
