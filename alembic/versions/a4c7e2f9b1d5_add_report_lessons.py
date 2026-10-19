"""add report_lessons (bài học từ góp ý trong Jenny chat → prompt sinh báo cáo)

Revision ID: a4c7e2f9b1d5
Revises: d8b3f1a6c4e2
Create Date: 2026-10-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a4c7e2f9b1d5'
down_revision: Union[str, None] = 'd8b3f1a6c4e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "report_lessons",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("report_date", sa.Text(), nullable=False),
        sa.Column("section", sa.Text(), nullable=False, server_default="general"),
        sa.Column("mistake", sa.Text(), nullable=False),
        sa.Column("correction", sa.Text(), nullable=False),
        sa.Column("lesson", sa.Text(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("reported_by", sa.Text(), nullable=False),
        sa.Column("chat_session_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('active', 'dismissed')", name="ck_report_lessons_status"),
        sa.CheckConstraint(
            "section IN ('1', '2', 'dev', '3', '4', '8', 'biz', 'general')", name="ck_report_lessons_section"
        ),
    )
    op.create_index("ix_report_lessons_status_created", "report_lessons", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_report_lessons_status_created", table_name="report_lessons")
    op.drop_table("report_lessons")
