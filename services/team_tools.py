"""Tool MCP cho chế độ trò chuyện với đội ngũ Sonic – Jenny – Jack trên Claude Desktop/mobile
(api/routers/mcp_gateway.py; prompt "Họp nhóm" ở services/claude_connect.py::DESKTOP_PROMPTS).

CHỈ ĐỌC. CỐ Ý ngoài `CLIENT_TOOLS` (Jenny chat web đã có `get_qc_review` riêng cho admin trong ADMIN_TOOLS):
  - get_qc_review (Jack): biên bản QC Claude của báo cáo — token thường chỉ đọc được báo cáo ĐÃ PHÁT HÀNH
    (quyết định 03/11/2026), qc_agent đọc cả nháp.
  - get_daily_status (cả nhóm): tiến độ báo cáo 1 ngày (nháp → QC → sửa → xuất bản) + khung tin đang thu thập.
    Báo cáo chưa phát hành chỉ lộ trạng thái/giờ, không lộ nội dung hay kết quả QC.
  - get_crawl_stats (Sonic): thống kê kho tin của 1 khung tin (đã lưu theo vùng/chủ đề/nguồn, tin nóng, URL đã loại).
  - get_report_lessons (Jenny): bài học từ góp ý người đọc còn hiệu lực (không lộ người góp ý).
"""
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Article, CrawlSeenUrl, NewsCrawlSource, Report, ReportLesson
from services.report_generator import TOPIC_DISPLAY_LABELS
from services.report_lessons import LESSON_MEMORY_DAYS, SECTION_LABELS
from services.report_qc_review import VERDICT_LABELS, latest_review, load_findings, qc_review_text, score_after_fixes
from services.report_translation import fresh_content_en

_VN_TZ = timezone(timedelta(hours=7))
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
LESSONS_DEFAULT_DAYS = 30
LESSONS_MAX_ITEMS = 20
CRAWL_TOP_SOURCES = 10
CRAWL_HOT_NEWS_ITEMS = 10

_DATE_PROP = {"type": "string", "description": "TUỲ CHỌN — YYYY-MM-DD."}

GET_QC_REVIEW_TOOL = {
    "name": "get_qc_review",
    "description": (
        "JACK — BIÊN BẢN QC của 1 báo cáo ngày do Jack chấm trên Claude (lịch tự động 07:30 trên bản nháp; 08:00 Jenny "
        "tự sửa theo biên bản; 08:15 tự xuất bản nếu đạt): điểm tổng thang 10 + kết luận (Đạt / Đạt, cần sửa / Không "
        "đạt), điểm 7 tiêu chí, điểm sau khi sửa, danh sách lỗi (báo cáo ghi / nên sửa / căn cứ / đã sửa hay chưa), ưu "
        "tiên cải thiện. Gọi khi hỏi 'QC hôm nay thế nào', 'Jack chấm bao nhiêu điểm', 'báo cáo bị lỗi gì', 'lỗi nào "
        "đã sửa'. KHÁC jack_qc (Jack đối chiếu tự động bằng code ngay lúc gọi, không dùng AI). Chỉ đọc được báo cáo "
        "ĐÃ PHÁT HÀNH — báo cáo hôm nay còn là nháp thì nói rõ biên bản xem được sau khi phát hành (thường 08:15). "
        "Trình bày dưới danh nghĩa Jack, giữ nguyên số liệu biên bản, không tự thêm lỗi."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"date": {**_DATE_PROP, "description": "TUỲ CHỌN — YYYY-MM-DD của báo cáo. Không truyền = báo cáo đã phát hành mới nhất."}},
    },
}
DAILY_STATUS_TOOL = {
    "name": "get_daily_status",
    "description": (
        "TÌNH HÌNH HỆ THỐNG trong 1 ngày — dùng mở đầu buổi họp nhóm hoặc khi hỏi 'hôm nay báo cáo xong chưa', 'đã "
        "phát hành chưa', 'Jack QC chưa', 'Sonic thu được bao nhiêu tin': trạng thái báo cáo ngày đó (chưa sinh / đang "
        "sinh / lỗi / nháp / đã phát hành) kèm giờ sinh, giờ phát hành, đã gửi email PDF chưa, có bản tiếng Anh chưa; "
        "biên bản QC (báo cáo đã phát hành: điểm + kết luận + số lỗi đã sửa; còn nháp: chỉ cho biết đã có biên bản "
        "chưa); lịch tự động (sinh nháp → QC → tự sửa → xuất bản); số tin của khung tin đang thu thập. Chi tiết thì "
        "gọi get_qc_review / get_crawl_stats."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"date": {**_DATE_PROP, "description": "TUỲ CHỌN — YYYY-MM-DD của báo cáo. Không truyền = hôm nay (giờ Việt Nam)."}},
    },
}
CRAWL_STATS_TOOL = {
    "name": "get_crawl_stats",
    "description": (
        "SONIC — THỐNG KÊ THU THẬP TIN của 1 khung tin: số bài đã lưu (theo vùng Việt Nam/quốc tế, chủ đề, nguồn + hạng "
        "nguồn), tin nóng, lần lưu gần nhất, số URL Sonic đã xem nhưng loại (không liên quan EUA, trùng, quá cũ, lỗi "
        "trích/phân loại), số nguồn đang bật. Khung tin ngày D = 07:00 ngày D → 07:00 ngày D+1 (giờ VN) và là tin của "
        "báo cáo ngày D+1. Gọi khi hỏi 'hôm nay Sonic crawl được gì/bao nhiêu tin', 'nguồn nào nhiều tin', 'có tin nóng "
        "không'. Muốn danh sách bài cụ thể thì dùng browse_news."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"date": {**_DATE_PROP, "description": "TUỲ CHỌN — YYYY-MM-DD = ngày D của khung tin. Không truyền = khung tin đang thu thập."}},
    },
}
REPORT_LESSONS_TOOL = {
    "name": "get_report_lessons",
    "description": (
        "JENNY — BÀI HỌC TỪ GÓP Ý của người đọc: các lỗi đã được kiểm chứng ở báo cáo trước (đã sai gì → đúng là gì → "
        "quy tắc Jenny áp dụng cho các báo cáo sau), còn hiệu lực. Gọi khi hỏi 'Jenny đã rút kinh nghiệm gì', 'lỗi cũ "
        "đã được sửa chưa', 'báo cáo hay sai ở đâu'. Chỉ đọc — kênh này không ghi được bài học mới (góp ý qua Jenny chat "
        "trên web)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "days": {"type": "integer", "description": f"TUỲ CHỌN — số ngày gần nhất (mặc định {LESSONS_DEFAULT_DAYS}, tối đa {LESSON_MEMORY_DAYS})."},
            "section": {"type": "string", "enum": list(SECTION_LABELS), "description": "TUỲ CHỌN — chỉ lấy bài học của 1 mục."},
        },
    },
}
TEAM_TOOLS = [GET_QC_REVIEW_TOOL, DAILY_STATUS_TOOL, CRAWL_STATS_TOOL, REPORT_LESSONS_TOOL]
TEAM_TOOL_NAMES = frozenset(t["name"] for t in TEAM_TOOLS)

_SEEN_STATUS_LABELS = {
    "irrelevant": "không liên quan EUA",
    "duplicate": "trùng bài đã có",
    "skipped_old": "đăng quá cũ",
    "extraction_failed": "không trích được nội dung",
    "classification_failed": "lỗi phân loại",
    "bloomberg_resolved": "link Bloomberg đã quy về bài gốc",
    "bloomberg_unresolved": "link Bloomberg chưa tìm được bài gốc",
}
_REPORT_STATUS_LABELS = {"draft": "bản NHÁP (chưa phát hành)", "published": "ĐÃ PHÁT HÀNH"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _valid_date(value: Any) -> Optional[str]:
    return str(value) if value and _DATE_RE.fullmatch(str(value)) else None


def _vn(dt: Optional[datetime]) -> str:
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_VN_TZ).strftime("%H:%M %d/%m/%Y")


def news_window(day: str) -> tuple[datetime, datetime]:
    """Khung tin ngày D (giờ UTC): 07:00 VN ngày D = 00:00 UTC ngày D — cùng khung với
    report_generator.get_news_for_report."""
    start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def current_window_day(now: Optional[datetime] = None) -> str:
    return (now or _now()).astimezone(timezone.utc).date().isoformat()


# ── get_qc_review ──

async def _qc_review(session: AsyncSession, date: Optional[str], *, published_only: bool) -> str:
    statuses = ("published",) if published_only else ("published", "draft")
    if date is None:
        date = (await session.execute(
            select(Report.report_date).where(Report.status.in_(statuses)).order_by(Report.report_date.desc()).limit(1)
        )).scalar_one_or_none()
        if date is None:
            return "Chưa có báo cáo nào được phát hành nên chưa có biên bản QC để xem."
    status = (await session.execute(select(Report.status).where(Report.report_date == date))).scalar_one_or_none()
    if status is None:
        return f"Chưa có báo cáo ngày {date}."
    if status not in statuses:
        has_review = await latest_review(session, date) is not None
        return (
            f"Báo cáo ngày {date} chưa phát hành (đang {status}) — biên bản QC chỉ xem được sau khi phát hành "
            f"(thường 08:15 giờ VN). Hiện {'ĐÃ' if has_review else 'CHƯA'} có biên bản QC cho bản nháp."
        )
    return await qc_review_text(session, date)


# ── get_daily_status ──

def schedule_lines(settings: Any) -> List[str]:
    return [
        f"Lịch tự động (giờ VN, ngày chạy: {getattr(settings, 'auto_report_days', '') or '—'}): "
        f"{getattr(settings, 'auto_report_time', '') or '—'} Jenny sinh bản nháp → 07:30 Jack QC trên Claude → "
        f"{getattr(settings, 'auto_qc_fix_time', '') or '—'} Jenny tự sửa theo biên bản → "
        f"{getattr(settings, 'auto_publish_time', '') or '—'} tự xuất bản + gửi email (bị chặn nếu biên bản Không đạt mà "
        "lỗi nặng chưa sửa xong).",
    ]


def report_status_lines(report: Optional[Any], review: Optional[Any], findings: Sequence[Any], day: str) -> List[str]:
    """Hàm thuần: dòng trạng thái báo cáo + QC. Báo cáo chưa phát hành không lộ điểm/kết luận QC."""
    if report is None:
        return [f"Báo cáo ngày {day}: CHƯA được sinh."]
    if report.status in ("generating", "failed") or report.content is None:
        state = "LỖI khi sinh" if report.status == "failed" or report.error_message else "ĐANG SINH"
        return [f"Báo cáo ngày {day}: {state} (bắt đầu {_vn(report.created_at)})."]
    lines = [
        f"Báo cáo ngày {day}: {_REPORT_STATUS_LABELS.get(report.status, report.status)} — sinh lúc {_vn(report.created_at)}"
        + (f", phát hành lúc {_vn(report.published_at)}" if report.published_at else "")
        + (f", đã gửi email PDF lúc {_vn(report.pdf_emailed_at)}" if report.pdf_emailed_at else "")
        + "."
    ]
    if report.status == "published":
        lines.append(f"Bản tiếng Anh: {'có' if fresh_content_en(report) is not None else 'chưa có / chưa khớp bản tiếng Việt'}.")
    if review is None:
        lines.append("Biên bản QC của Jack: chưa có.")
    elif report.status != "published":
        lines.append(f"Biên bản QC của Jack: đã có (lúc {_vn(review.created_at)}) — xem chi tiết sau khi phát hành.")
    else:
        after, _ = score_after_fixes(review.criteria or [], findings)
        fixed = sum(1 for f in findings if f.status == "fixed")
        open_heavy = sum(1 for f in findings if f.status == "open" and f.severity in ("critical", "major"))
        lines.append(
            f"Biên bản QC #{review.run_no} của Jack (lúc {_vn(review.created_at)}): {float(review.overall_score):.2f}/10 — "
            f"{VERDICT_LABELS.get(review.verdict, review.verdict)}; {len(findings)} lỗi, đã sửa {fixed}"
            + (f", điểm sau sửa {after:.2f}/10" if fixed else "")
            + (f"; còn {open_heavy} lỗi nghiêm trọng/quan trọng chưa sửa" if open_heavy else "")
            + ". Chi tiết: get_qc_review."
        )
    return lines


async def _daily_status(session: AsyncSession, date: Optional[str], settings: Any) -> str:
    day = date or _now().astimezone(_VN_TZ).date().isoformat()
    report = (await session.execute(select(Report).where(Report.report_date == day))).scalars().first()
    review = await latest_review(session, day) if report is not None else None
    findings = (await load_findings(session, [review.id]))[review.id] if review is not None else []
    lines = [f"TÌNH HÌNH NGÀY {day} (lúc {_vn(_now())} giờ VN)"]
    lines += report_status_lines(report, review, findings, day)
    lines += schedule_lines(settings)
    # Báo cáo ngày D dùng khung tin ngày D−1; khung đang thu thập là của báo cáo kế tiếp.
    data_day = (datetime.strptime(day, "%Y-%m-%d").date() - timedelta(days=1)).isoformat()
    for label, window_day in (("dùng cho báo cáo này", data_day), ("đang thu thập", current_window_day())):
        if label == "đang thu thập" and window_day == data_day:
            continue
        start, end = news_window(window_day)
        count = (await session.execute(
            select(func.count(Article.id)).where(Article.crawled_at >= start, Article.crawled_at < end)
        )).scalar_one()
        lines.append(f"Khung tin {window_day} ({label}): Sonic đã lưu {count} bài. Chi tiết: get_crawl_stats(date=\"{window_day}\").")
    return "\n".join(lines)


# ── get_crawl_stats ──

def crawl_stats_text(
    day: str,
    articles: Sequence[Any],
    seen: Dict[str, int],
    active_sources: Dict[str, int],
    tiers: Dict[str, str],
) -> str:
    """Hàm thuần. `articles`: các bài trong khung (source, region, topic, is_hot_news, title, id, crawled_at)."""
    lines = [f"THỐNG KÊ THU THẬP TIN — khung tin {day} (07:00 {day} → 07:00 hôm sau, giờ VN; tin của báo cáo ngày "
             f"{(datetime.strptime(day, '%Y-%m-%d').date() + timedelta(days=1)).isoformat()})"]
    if active_sources:
        lines.append("Nguồn đang bật: " + ", ".join(
            f"{'Việt Nam' if r == 'vietnam' else 'quốc tế'} {n}" for r, n in sorted(active_sources.items())))
    if not articles:
        lines.append("Chưa lưu bài nào trong khung này.")
    else:
        region = Counter(a.region for a in articles)
        lines.append(
            f"Đã lưu {len(articles)} bài (Việt Nam {region.get('vietnam', 0)}, quốc tế {region.get('international', 0)}); "
            f"lần lưu gần nhất {_vn(max(a.crawled_at for a in articles))}."
        )
        topics = Counter(t for a in articles for t in (a.topic or []))
        lines.append("Theo chủ đề (1 bài có thể nhiều chủ đề): " + ", ".join(
            f"{TOPIC_DISPLAY_LABELS.get(t, t)} {n}" for t, n in topics.most_common()))
        sources = Counter(a.source for a in articles)
        lines.append(f"Nguồn nhiều tin nhất: " + ", ".join(
            f"{s} (hạng {tiers.get(s, '?')}) {n}" for s, n in sources.most_common(CRAWL_TOP_SOURCES)))
        hot = [a for a in articles if a.is_hot_news]
        if hot:
            lines.append(f"Tin nóng ({len(hot)}):")
            lines += [f"- [ID {a.id}] {a.title or '(không tiêu đề)'} — {a.source}" for a in hot[:CRAWL_HOT_NEWS_ITEMS]]
        else:
            lines.append("Tin nóng: không có.")
    if seen:
        lines.append(f"URL đã xem nhưng không lưu ({sum(seen.values())}): " + ", ".join(
            f"{_SEEN_STATUS_LABELS.get(s, s)} {n}" for s, n in sorted(seen.items(), key=lambda kv: -kv[1])))
    return "\n".join(lines)


async def _crawl_stats(session: AsyncSession, date: Optional[str]) -> str:
    day = date or current_window_day()
    start, end = news_window(day)
    articles = (await session.execute(
        select(Article.id, Article.title, Article.source, Article.source_tier, Article.region, Article.topic,
               Article.is_hot_news, Article.crawled_at)
        .where(Article.crawled_at >= start, Article.crawled_at < end)
    )).all()
    seen_rows = (await session.execute(
        select(CrawlSeenUrl.status, func.count())
        .where(CrawlSeenUrl.first_seen_at >= start, CrawlSeenUrl.first_seen_at < end)
        .group_by(CrawlSeenUrl.status)
    )).all()
    source_rows = (await session.execute(
        select(NewsCrawlSource.region, func.count()).where(NewsCrawlSource.is_active == True)  # noqa: E712
        .group_by(NewsCrawlSource.region)
    )).all()
    tiers = {a.source: a.source_tier or "?" for a in articles}
    return crawl_stats_text(day, articles, dict(seen_rows), dict(source_rows), tiers)


# ── get_report_lessons ──

def lessons_text(lessons: Iterable[Any], days: int) -> str:
    """Hàm thuần — CỐ Ý không in `reported_by` (email người góp ý)."""
    rows = list(lessons)
    if not rows:
        return f"Không có bài học nào từ góp ý người đọc trong {days} ngày gần nhất."
    lines = [f"BÀI HỌC TỪ GÓP Ý NGƯỜI ĐỌC — {len(rows)} bài học còn hiệu lực trong {days} ngày gần nhất (mới nhất trước):"]
    for l in rows:
        lines.append(f"- #{l.id} [báo cáo {l.report_date}, {SECTION_LABELS.get(l.section, l.section)}]")
        lines.append(f"  Đã sai: {l.mistake}")
        lines.append(f"  Đúng là: {l.correction}")
        lines.append(f"  Bài học áp dụng cho báo cáo sau: {l.lesson}")
    return "\n".join(lines)


async def _report_lessons(session: AsyncSession, days: int, section: Optional[str]) -> str:
    stmt = (
        select(ReportLesson)
        .where(ReportLesson.status == "active", ReportLesson.created_at >= _now() - timedelta(days=days))
        .order_by(ReportLesson.created_at.desc())
        .limit(LESSONS_MAX_ITEMS)
    )
    if section:
        stmt = stmt.where(ReportLesson.section == section)
    return lessons_text((await session.execute(stmt)).scalars().all(), days)


async def execute_team_tool(
    session: AsyncSession, name: str, tool_input: Dict[str, Any], *, published_only: bool, settings: Any
) -> str:
    date = _valid_date(tool_input.get("date"))
    bad_date = tool_input.get("date") and date is None
    if bad_date:
        return f"date=\"{tool_input.get('date')}\" không hợp lệ — phải có dạng YYYY-MM-DD (hoặc bỏ trống)."
    if name == "get_qc_review":
        return await _qc_review(session, date, published_only=published_only)
    if name == "get_daily_status":
        return await _daily_status(session, date, settings)
    if name == "get_crawl_stats":
        return await _crawl_stats(session, date)
    if name == "get_report_lessons":
        try:
            days = int(tool_input.get("days") or LESSONS_DEFAULT_DAYS)
        except (TypeError, ValueError):
            days = LESSONS_DEFAULT_DAYS
        section = tool_input.get("section")
        return await _report_lessons(
            session, max(1, min(days, LESSON_MEMORY_DAYS)), section if section in SECTION_LABELS else None
        )
    return f"Tool không xác định: {name}"
