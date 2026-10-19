"""add report_qc_reviews + report_qc_findings (biên bản QC của Claude/Jack gửi qua MCP) + api_tokens.scope

Revision ID: a9e4c7b2d5f1
Revises: d8b3f1a6c4e2
Create Date: 2026-10-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = 'a9e4c7b2d5f1'
down_revision: Union[str, None] = 'd8b3f1a6c4e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_EMPTY_LIST = sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.add_column("api_tokens", sa.Column("scope", sa.Text(), nullable=False, server_default="user"))
    op.create_check_constraint("ck_api_tokens_scope", "api_tokens", "scope IN ('user', 'qc_agent')")

    op.create_table(
        "report_qc_reviews",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("report_date", sa.Text(), sa.ForeignKey("reports.report_date", ondelete="CASCADE"), nullable=False),
        sa.Column("run_no", sa.Integer(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False, server_default="claude_mcp"),
        sa.Column("reviewer", sa.Text(), nullable=True),
        sa.Column("token_id", sa.Integer(), sa.ForeignKey("api_tokens.id", ondelete="SET NULL"), nullable=True),
        sa.Column("report_status", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("claude_verdict", sa.Text(), nullable=True),
        sa.Column("overall_score", sa.Numeric(4, 2), nullable=False),
        sa.Column("criteria", JSONB(), nullable=False, server_default=_EMPTY_LIST),
        sa.Column("critical_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("major_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("minor_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("jack_rule_score", sa.Integer(), nullable=True),
        sa.Column("jack_rule_issue_count", sa.Integer(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("strengths", JSONB(), nullable=False, server_default=_EMPTY_LIST),
        sa.Column("priorities", JSONB(), nullable=False, server_default=_EMPTY_LIST),
        sa.Column("body_markdown", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("verdict IN ('pass', 'pass_with_fixes', 'fail')", name="ck_report_qc_reviews_verdict"),
        sa.CheckConstraint("source IN ('claude_mcp', 'admin_manual')", name="ck_report_qc_reviews_source"),
        sa.UniqueConstraint("report_date", "run_no", name="uq_report_qc_reviews_date_run"),
    )
    op.create_index("ix_report_qc_reviews_report_date", "report_qc_reviews", ["report_date"])
    op.create_index("ix_report_qc_reviews_created_at", "report_qc_reviews", ["created_at"])

    op.create_table(
        "report_qc_findings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("review_id", sa.Integer(), sa.ForeignKey("report_qc_reviews.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("criterion", sa.Text(), nullable=False),
        sa.Column("section", sa.Text(), nullable=True),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("field_path", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("current_text", sa.Text(), nullable=True),
        sa.Column("expected_text", sa.Text(), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("verification", sa.Text(), nullable=False, server_default="unverified"),
        sa.Column("source_refs", JSONB(), nullable=False, server_default=_EMPTY_LIST),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("repeat_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.Text(), nullable=False, server_default="open"),
        sa.Column("status_note", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("severity IN ('critical', 'major', 'minor')", name="ck_report_qc_findings_severity"),
        sa.CheckConstraint(
            "verification IN ('verified', 'partially_verified', 'unverified')",
            name="ck_report_qc_findings_verification",
        ),
        sa.CheckConstraint("status IN ('open', 'fixed', 'wont_fix')", name="ck_report_qc_findings_status"),
    )
    op.create_index("ix_report_qc_findings_review_id", "report_qc_findings", ["review_id"])
    op.create_index("ix_report_qc_findings_fingerprint", "report_qc_findings", ["fingerprint"])


def downgrade() -> None:
    op.drop_index("ix_report_qc_findings_fingerprint", table_name="report_qc_findings")
    op.drop_index("ix_report_qc_findings_review_id", table_name="report_qc_findings")
    op.drop_table("report_qc_findings")
    op.drop_index("ix_report_qc_reviews_created_at", table_name="report_qc_reviews")
    op.drop_index("ix_report_qc_reviews_report_date", table_name="report_qc_reviews")
    op.drop_table("report_qc_reviews")
    op.drop_constraint("ck_api_tokens_scope", "api_tokens", type_="check")
    op.drop_column("api_tokens", "scope")
