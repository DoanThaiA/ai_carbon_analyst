"""shift_report_date_to_creation_date

Đổi quy ước reports.report_date từ NGÀY DỮ LIỆU (T-1) sang NGÀY TẠO báo cáo (T):
báo cáo sinh 07:00 ngày 29/08 giờ lưu report_date = 29/08 (trước đây 28/08).
Logic lấy dữ liệu không đổi — code tự lùi 1 ngày qua
services/report_generator.py::report_data_date. Migration này dời +1 ngày cho
các dòng CŨ để khớp quy ước mới (hiển thị không đổi so với trước, Quote Chat vẫn
tra đúng dữ liệu). chat_sessions.report_date trùng khoá với reports.report_date
(không FK cứng) nên dời cùng.

reports.report_date là UNIQUE (kiểm tra theo từng dòng, không deferrable) — dời
thẳng +1 trong 1 câu UPDATE có thể va chạm tạm thời (28→29 khi 29 chưa kịp dời),
nên đi 2 bước qua 1 giá trị tạm có tiền tố.

Revision ID: b6d2e8f4a1c3
Revises: a9c3e7f1b5d2
Create Date: 2026-09-29 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'b6d2e8f4a1c3'
down_revision: Union[str, None] = 'a9c3e7f1b5d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Chỉ đụng các dòng đúng định dạng YYYY-MM-DD.
_DATE_RE = r"'^\d{4}-\d{2}-\d{2}$'"


def _shift(days: int) -> None:
    op.execute(
        f"UPDATE reports SET report_date = 'shift:' || "
        f"to_char(report_date::date + {days}, 'YYYY-MM-DD') WHERE report_date ~ {_DATE_RE}"
    )
    op.execute(
        "UPDATE reports SET report_date = substr(report_date, 7) WHERE report_date LIKE 'shift:%'"
    )
    op.execute(
        f"UPDATE chat_sessions SET report_date = "
        f"to_char(report_date::date + {days}, 'YYYY-MM-DD') WHERE report_date ~ {_DATE_RE}"
    )


def upgrade() -> None:
    _shift(1)


def downgrade() -> None:
    _shift(-1)
