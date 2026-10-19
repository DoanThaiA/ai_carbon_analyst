"""
services/report_lessons.py
==========================
Bài học từ góp ý của người dùng trong Jenny chat → prompt sinh báo cáo các ngày sau.

Luồng:
  1. Người dùng góp ý/chỉ ra lỗi trong báo cáo. Jenny kiểm tra bằng tool (xem khối GIAO
     TIẾP trong services/quote_chat.py::_build_static_instructions) — CHỈ khi xác nhận
     sai/thiếu THẬT mới gọi tool `record_report_lesson` → record_lesson() ghi 1 dòng vào
     bảng `report_lessons` (db/models.py::ReportLesson). Góp ý mà báo cáo vẫn đúng thì
     không ghi gì.
  2. Mỗi lần sinh báo cáo ngày D (services/report_generator.py::generate_report_content),
     load_active() lấy các bài học còn hiệu lực (ghi trong LESSON_MEMORY_DAYS ngày gần
     nhất, status='active') và lessons_prompt_block() nối vào prompt của từng mục: bài
     học của chính mục đó + bài học "general".
Admin tắt bài học sai/không còn đúng: `python -m scripts.report_lessons --dismiss ID`.

Tool chỉ có ở Jenny chat web (không nằm trong CLIENT_TOOLS) — kênh MCP/Claude Desktop là
chỉ đọc, kết quả Claude sinh ra không ghi ngược về hệ thống.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ReportLesson

logger = logging.getLogger(__name__)

# Khoá mục của bộ sinh báo cáo (SECTIONS trong generate_report_content) → tên hiển thị.
SECTION_LABELS: Dict[str, str] = {
    "1": "Mục 1 — Tóm tắt điều hành",
    "2": "Mục 2 — Bảng giá nhanh / yếu tố hỗ trợ tăng-giảm giá",
    "dev": "Diễn biến chính",
    "3": "Mục 3 — Phân tích chuyên sâu + kịch bản giao dịch / khuyến nghị vị thế",
    "4": "Mục 4 — Tín chỉ carbon & CBAM",
    "8": "Mục 8 — Lịch sự kiện",
    "biz": "Gợi ý kinh doanh & giải pháp cho SIM",
    "general": "Chung (mọi mục)",
}
# Chỉ nhớ bài học trong khoảng này — quy tắc cũ hơn thường đã lỗi thời theo thị trường.
LESSON_MEMORY_DAYS = 90
# Tối đa số bài học nạp cho 1 lần sinh (mới nhất trước) — giữ prompt gọn.
MAX_LESSONS_PER_REPORT = 30
# Chống spam: tối đa số bài học 1 người dùng ghi trong 24h.
MAX_LESSONS_PER_USER_PER_DAY = 10
MAX_FIELD_CHARS = 600


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())[:MAX_FIELD_CHARS]


async def record_lesson(
    session: AsyncSession,
    *,
    report_date: str,
    section: str,
    mistake: str,
    correction: str,
    lesson: str,
    evidence: str = "",
    reported_by: str,
    chat_session_id: Optional[int] = None,
) -> str:
    """Ghi 1 bài học (đã được Jenny kiểm chứng) và commit. Trả văn bản làm tool_result."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", report_date or ""):
        return "Chưa ghi: date phải có dạng YYYY-MM-DD."
    section = section if section in SECTION_LABELS else "general"
    mistake, correction, lesson = _clean(mistake), _clean(correction), _clean(lesson)
    if not (mistake and correction and lesson):
        return "Chưa ghi: cần đủ mistake, correction và lesson."

    since = datetime.now(timezone.utc) - timedelta(days=1)
    recent = await session.scalar(
        select(func.count()).select_from(ReportLesson).where(
            ReportLesson.reported_by == reported_by, ReportLesson.created_at >= since,
        )
    )
    if (recent or 0) >= MAX_LESSONS_PER_USER_PER_DAY:
        return "Chưa ghi: đã đạt giới hạn số góp ý ghi nhận trong 24 giờ. Vẫn trả lời người dùng bình thường."

    duplicate = await session.scalar(
        select(ReportLesson.id).where(
            ReportLesson.status == "active",
            ReportLesson.section == section,
            func.lower(ReportLesson.lesson) == lesson.lower(),
        )
    )
    if duplicate:
        return f"Bài học này đã được ghi trước đó (#{duplicate}) — các báo cáo sau đã áp dụng."

    row = ReportLesson(
        report_date=report_date, section=section, mistake=mistake, correction=correction,
        lesson=lesson, evidence=_clean(evidence) or None, reported_by=reported_by,
        chat_session_id=chat_session_id,
    )
    session.add(row)
    await session.commit()
    logger.info("[REPORT-LESSON] #%s ghi nhận (%s, %s) từ %s: %s", row.id, report_date, section, reported_by, lesson)
    return (
        f"Đã ghi nhận bài học #{row.id} cho {SECTION_LABELS[section]} — sẽ được đưa vào prompt khi sinh các "
        "báo cáo tiếp theo. (Bản báo cáo đã phát hành KHÔNG bị sửa.)"
    )


async def load_active(session: AsyncSession, report_date: str) -> List[ReportLesson]:
    """Bài học còn hiệu lực cho lần sinh báo cáo `report_date`: status='active', ghi trong
    LESSON_MEMORY_DAYS ngày gần nhất và về báo cáo TRƯỚC ngày đó (sinh lại báo cáo cũ không
    nhận bài học rút ra từ chính nó hay từ các ngày sau)."""
    since = datetime.now(timezone.utc) - timedelta(days=LESSON_MEMORY_DAYS)
    rows = await session.scalars(
        select(ReportLesson)
        .where(
            ReportLesson.status == "active",
            ReportLesson.created_at >= since,
            ReportLesson.report_date < report_date,
        )
        .order_by(ReportLesson.created_at.desc())
        .limit(MAX_LESSONS_PER_REPORT)
    )
    return list(rows)


def lessons_prompt_block(lessons: Sequence[ReportLesson], section_key: str) -> str:
    """Khối nối vào cuối user prompt của mục `section_key` (hàm thuần). Rỗng nếu không có bài học."""
    relevant = [l for l in lessons if l.section in (section_key, "general")]
    if not relevant:
        return ""
    lines = [
        f"- [{l.report_date}, {SECTION_LABELS.get(l.section, l.section)}] Đã sai: {l.mistake} → Đúng: "
        f"{l.correction}. BÀI HỌC: {l.lesson}"
        for l in relevant
    ]
    return (
        "\n\n=== BÀI HỌC TỪ GÓP Ý CỦA NGƯỜI ĐỌC (lỗi đã được kiểm chứng ở các báo cáo trước) ===\n"
        "Các lỗi dưới đây đã thật sự xảy ra và được người đọc chỉ ra. TUYỆT ĐỐI không lặp lại: áp dụng đúng "
        "BÀI HỌC khi viết mục này. Đây là quy tắc cách làm — KHÔNG chép lại số liệu/sự kiện cũ trong đó vào "
        "báo cáo hôm nay, và KHÔNG nhắc tới việc từng bị góp ý.\n"
        + "\n".join(lines)
    )


async def dismiss(session: AsyncSession, lesson_id: int) -> bool:
    result = await session.execute(
        update(ReportLesson)
        .where(ReportLesson.id == lesson_id, ReportLesson.status == "active")
        .values(status="dismissed", dismissed_at=datetime.now(timezone.utc))
    )
    await session.commit()
    return result.rowcount > 0
