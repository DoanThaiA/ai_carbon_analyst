
import asyncio
import base64
import io
import ipaddress
import logging
import re
import socket
from datetime import date as date_cls, datetime, timedelta, timezone
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence
from urllib.parse import urljoin, urlsplit

import httpx
import trafilatura
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import Settings
from db.models import Article, BizSuggestion, Report, Instrument
from schemas.chat_models import MAX_ATTACHMENTS_PER_TURN, Attachment, ChatTurn
from schemas.retrieval_models import RetrievedDocument
from services import minio_service
from services.retrieval import RetrievalService
from services import eua_causal_chains as chains
from services.report_generator import (
    report_data_date,
    _compute_recurring_calendar_events,
    get_prices_for_report,
    _summarize_prices,
    get_historical_ohlc_for_report,
    _eua_volume_summary,
    _eua_technical_levels_summary,
    _eua_session_range_summary,
    EUA_VOLUME_SESSIONS_FOR_AVG,
    EUA_VOLUME_SPIKE_PCT,
    EUA_VOLUME_DROP_PCT,
)

logger = logging.getLogger(__name__)

MAX_CONTEXT_CHUNKS = 8
HYBRID_SEARCH_LIMIT = 20
MAX_QUOTE_CHARS = 2000  # đủ cho 1 đoạn/gạch đầu dòng của báo cáo
MAX_ANSWER_TOKENS = 2048  # chặn cứng độ dài — bổ trợ cho rule ngắn gọn trong system prompt (không
# phải cơ chế ép ngắn chính — đó là rule 6 trong system prompt; giá trị này chỉ là lưới an toàn
# tránh model chạy lố quá xa). TỪNG là 1000 — quá sát với các câu trả lời hợp lệ theo đúng ngoại lệ
# rule 6 ("chỉ viết dài hơn khi user CHỦ ĐỘNG yêu cầu giải thích chi tiết hơn") hoặc các chuỗi nhân
# quả ít được luyện/rehearse hơn FUEL_SWITCHING (vd MACRO) — khiến `stop_reason == "max_tokens"`
# cắt cụt câu trả lời giữa chừng mà KHÔNG có dấu hiệu gì báo cho user (xem xử lý stop_reason bên
# dưới trong _stream_anthropic).

# Chặn trên độ dài text của 1 MỤC báo cáo sau khi format (xem
# `_tool_report_section_text`) — 1 mục thật thường không tới ngưỡng này, đây
# chỉ là lưới an toàn tránh 1 mục bất thường dài làm phình tool result không
# kiểm soát.
MAX_REPORT_CHARS = 40000
MAX_ARTICLE_CHARS = 12000  # tool get_article — 1 bài dài hiếm khi cần hơn thế để trả lời

# Ngưỡng relevance_score (thang 0-1 của Cohere rerank) để 1 chunk được coi là
# THẬT SỰ liên quan tới quote+câu hỏi — top_k chỉ giới hạn số lượng, không đảm
# bảo độ liên quan (rerank vẫn trả đủ top_k chunk điểm thấp nếu không có chunk
# nào thật sự khớp). Theo tài liệu Cohere: điểm ~>0.5 là liên quan mạnh, <0.1
# gần như không liên quan — chọn 0.3 làm ngưỡng vừa phải: đủ để loại chunk lạc
# đề, nhưng không quá gắt tới mức loại luôn cả chunk liên quan gián tiếp (tin
# tức ít khi trùng khớp từ khoá 100% với câu hỏi tự do của người dùng). Tuỳ
# chỉnh dựa trên log "[RETRIEVAL] Lọc ngưỡng relevance_score..." nếu cần.
MIN_RERANK_SCORE = 0.5

# Lazy singleton client — tránh crash khi import module lúc chưa có .env (giống
# pattern report_generator.py / embedding.py). Quote Chat luôn dùng Anthropic
# (client tools + server tool web_search) — Cohere trong repo này CHỈ dùng cho
# embedding (services/embedding.py) và rerank (services/retrieval.py), không
# còn dùng cho chat/completion ở đây nữa.
_anthropic_client = None


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic

        api_key = Settings.from_env().anthropic_api_key
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY chưa được cấu hình.")
        _anthropic_client = anthropic.AsyncAnthropic(api_key=api_key)
    return _anthropic_client


def _truncate(text: str, max_chars: int) -> str:
    text = text.strip()
    return text if len(text) <= max_chars else text[:max_chars].rstrip() + "…"


# Đủ cho nội dung 1 file Word thông thường (vài trang), tránh phình prompt nếu
# người dùng lỡ đính kèm 1 file .docx rất dài — giống tinh thần
# MAX_TEXT_CHARS_FOR_CLASSIFICATION bên crawl_news/classification.py.
MAX_DOCX_CHARS_FOR_PROMPT = 6000


def _extract_docx_text(data: bytes) -> str:
    import docx

    document = docx.Document(io.BytesIO(data))
    paragraphs = [p.text for p in document.paragraphs if p.text.strip()]
    return _truncate("\n".join(paragraphs), MAX_DOCX_CHARS_FOR_PROMPT)


async def _build_attachment_content_blocks(attachments: Optional[Sequence[Attachment]]) -> List[dict]:
    """Tải từng file đính kèm về từ MinIO + build content block theo ĐÚNG
    format Anthropic content API: Ảnh/PDF -> block base64 (model đọc trực
    tiếp, Claude hỗ trợ vision + PDF gốc), Word (.docx) -> Claude KHÔNG có
    content-type gốc cho Word nên BACKEND tự trích chữ bằng python-docx rồi
    nhét vào 1 block text thường kèm tiêu đề "[Nội dung file: ...]".

    Chạy các lệnh MinIO/parse (đều là thư viện đồng bộ, blocking) qua
    `asyncio.to_thread` — event loop của FastAPI không được block bởi I/O
    mạng/CPU của việc tải+parse file, khớp tinh thần "Async throughout" của
    dự án dù bản thân `minio`/`python-docx` không có bản async.

    Lỗi ở 1 file (không tải được / vượt dung lượng thật khi kiểm tra lại trên
    MinIO / .docx hỏng) KHÔNG làm hỏng cả câu hỏi — chèn 1 dòng text báo lỗi
    thay cho file đó và tiếp tục xử lý các file còn lại.
    """
    if not attachments:
        return []

    blocks: List[dict] = []
    for att in attachments[:MAX_ATTACHMENTS_PER_TURN]:
        error = await asyncio.to_thread(minio_service.check_uploaded_object, att.file_key, att.media_type)
        if error:
            logger.warning("[QUOTE-CHAT] Bỏ qua file đính kèm \"%s\": %s", att.file_name, error)
            blocks.append({"type": "text", "text": f'[Không thể đọc file đính kèm "{att.file_name}": {error}.]'})
            continue

        try:
            data = await asyncio.to_thread(minio_service.get_file_as_bytes, att.file_key)
        except Exception:
            logger.exception("[QUOTE-CHAT] Lỗi khi tải file đính kèm \"%s\" từ MinIO.", att.file_name)
            blocks.append(
                {"type": "text", "text": f'[Không thể tải file đính kèm "{att.file_name}" — vui lòng đính kèm lại.]'}
            )
            continue

        if att.media_type in minio_service.IMAGE_MEDIA_TYPES:
            blocks.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": att.media_type, "data": base64.b64encode(data).decode()},
                }
            )
        elif att.media_type == minio_service.PDF_MEDIA_TYPE:
            blocks.append(
                {
                    "type": "document",
                    "source": {"type": "base64", "media_type": minio_service.PDF_MEDIA_TYPE, "data": base64.b64encode(data).decode()},
                }
            )
        elif att.media_type == minio_service.DOCX_MEDIA_TYPE:
            try:
                text = await asyncio.to_thread(_extract_docx_text, data)
            except Exception:
                logger.exception("[QUOTE-CHAT] Lỗi khi trích chữ từ file Word \"%s\".", att.file_name)
                blocks.append(
                    {"type": "text", "text": f'[Không thể đọc nội dung file Word "{att.file_name}" — file có thể bị hỏng.]'}
                )
                continue
            blocks.append({"type": "text", "text": f'[Nội dung file: {att.file_name}]\n{text or "(file rỗng)"}'})

    return blocks


def _format_source_label(chunk: RetrievedDocument, report_date: str) -> str:
    """Nhãn "(tên nguồn, thời gian)" đứng cạnh mỗi [Nguồn N] — model được yêu
    cầu copy nguyên nhãn này khi trích dẫn (xem rule 5 trong system prompt),
    thay vì chỉ dẫn "(Nguồn 1)" chung chung."""
    if chunk.source_type == "report":
        return f"Daily Carbon Intelligence, {report_date}"
    name = chunk.source_name or "nguồn không xác định"
    if chunk.published_at:
        return f"{name}, {chunk.published_at.strftime('%d/%m/%Y %H:%M')}"
    return name


def _format_context(chunks: Sequence[RetrievedDocument], report_date: str) -> str:
    if not chunks:
        return "(Không tìm thấy dữ liệu nền liên quan trong kho tin tức đã crawl.)"
    parts = []
    for i, c in enumerate(chunks, start=1):
        label = _format_source_label(c, report_date)
        parts.append(f"[Nguồn {i}] ({label})\n{c.content.strip()}")
    return "\n\n".join(parts)


# Số phiên tối đa hiện trong bảng khối lượng theo từng ngày (xem
# `_eua_volume_history_text`) — đủ để trả lời so sánh khối lượng trong vài
# tuần gần nhất mà không phình prompt quá mức (30 phiên là toàn bộ chart_data
# đang có sẵn — không cần fetch thêm).
EUA_VOLUME_HISTORY_SESSIONS = 30


def _eua_volume_history_text(chart_data_buffered: List[Any]) -> str:
    """Bảng khối lượng giao dịch EUA theo TỪNG phiên, MỖI phiên kèm % chênh
    lệch so với TB đúng `EUA_VOLUME_SESSIONS_FOR_AVG` phiên NGAY TRƯỚC phiên đó
    — tính bằng Python theo ĐÚNG công thức của `_eua_volume_summary`
    (report_generator.py), không để LLM tự cộng/chia trung bình từ bảng số thô.

    Lý do cần tính sẵn %chênh lệch cho MỌI phiên (không chỉ phiên liền trước
    report_date đang xem, như `_eua_volume_summary` ở trên): nếu chỉ đưa bảng
    số thô rồi để model tự tính TB khi người dùng hỏi 1 ngày CỤ THỂ trong quá
    khứ (vd đang xem báo cáo 10/9 nhưng hỏi "khối lượng 9/9 so với 20 phiên
    liền trước"), model dễ tính sai cửa sổ 20 phiên (lệch 1 ngày, hoặc gộp
    nhầm cả phiên đang hỏi vào TB) — kết quả sẽ KHÔNG khớp với con số báo cáo
    ngày 9/9 đã công bố. Tính sẵn ở đây dùng CHÍNH XÁC cửa sổ 20 phiên trước
    MỖI ngày (không phụ thuộc report_date của phiên chat hiện tại), nên hỏi về
    ngày nào trong bất kỳ báo cáo nào cũng luôn ra đúng 1 con số.

    `chart_data_buffered`: PHẢI có thêm `EUA_VOLUME_SESSIONS_FOR_AVG` phiên đệm
    phía trước `EUA_VOLUME_HISTORY_SESSIONS` phiên hiển thị (xem
    `_tool_eua_volume_history_text`) — nếu không, các phiên CŨ NHẤT trong bảng
    hiển thị sẽ thiếu dữ liệu phiên trước để tính đủ TB 20 phiên, cho kết quả
    TB thấp hơn thực tế (chỉ tính được trên ít phiên hơn).
    """
    with_volume = [c for c in chart_data_buffered if c.get("volume") is not None]
    if not with_volume:
        return "Không có dữ liệu khối lượng giao dịch EUA theo từng phiên."

    to_display = with_volume[-EUA_VOLUME_HISTORY_SESSIONS:]
    start_idx = len(with_volume) - len(to_display)

    lines = []
    for offset, day in enumerate(to_display):
        prior = with_volume[: start_idx + offset][-EUA_VOLUME_SESSIONS_FOR_AVG:]
        if not prior:
            lines.append(f"  - {day['date']}: {day['volume']:,.0f} hợp đồng (chưa đủ dữ liệu phiên trước để so TB)")
            continue
        avg_volume = sum(p["volume"] for p in prior) / len(prior)
        pct_diff = ((day["volume"] - avg_volume) / avg_volume * 100) if avg_volume else 0
        if pct_diff >= EUA_VOLUME_SPIKE_PCT:
            level = "tăng đột biến"
        elif pct_diff <= EUA_VOLUME_DROP_PCT:
            level = "giảm mạnh"
        else:
            level = "bình thường"
        lines.append(
            f"  - {day['date']}: {day['volume']:,.0f} hợp đồng | TB {len(prior)} phiên ngay trước: "
            f"{avg_volume:,.0f} | chênh {pct_diff:+.1f}% ({level})"
        )
    lines.reverse()  # mới nhất trước, giữ nguyên format cũ

    return (
        f"Khối lượng giao dịch EUA theo từng phiên ({len(to_display)} phiên gần nhất, mới nhất trước — "
        f"mỗi phiên đã so sẵn với TB {EUA_VOLUME_SESSIONS_FOR_AVG} phiên NGAY TRƯỚC nó, cố định theo NGÀY "
        f"đó chứ KHÔNG đổi theo ngày báo cáo đang xem):\n" + "\n".join(lines)
    )


# ─────────────────────────────────────────────────────────────────────
# Tool executors — giá/volume/report giờ được lấy QUA TOOL (Anthropic
# client tools), model chỉ gọi khi câu hỏi thực sự cần, thay vì luôn tiêm sẵn
# toàn bộ vào system prompt như trước — tiết kiệm context cho các câu hỏi
# thuần suy luận/giải thích không cần số liệu cụ thể. Mỗi hàm trả về text kèm
# SẴN 1 dòng "LƯU Ý" nhắc model dùng đúng số/không tự bịa — đặt trong tool
# RESULT (chỉ tốn context khi tool thực sự được gọi) thay vì trong system
# prompt tĩnh (trước đây luôn tốn context dù không dùng tới).
# ─────────────────────────────────────────────────────────────────────

async def _tool_market_prices_text(session: AsyncSession, target_date: str, requested_date: Optional[str] = None) -> str:
    """`target_date`: ngày dùng để tra cứu (report_date của phiên, hoặc ngày cụ thể model
    truyền qua tham số `date` của tool). `requested_date`: ngày model THỰC SỰ truyền vào tool
    (None nếu model không truyền, tức đang hỏi về ngày báo cáo mặc định) — dùng để cảnh báo lệch
    ngày, xem comment ở CLIENT_TOOLS: trước đây hàm này vứt bỏ `max_date` trả về từ
    `get_prices_for_report`, khiến model không biết dữ liệu trả về thực sự là của ngày nào và có
    thể trình bày nhầm dữ liệu ngày khác như thể là ngày người dùng hỏi."""
    prices, max_date = await get_prices_for_report(session, target_date)
    prices_text = _summarize_prices(prices)
    max_date_str = str(max_date) if max_date else None
    if requested_date and max_date_str and max_date_str != requested_date:
        date_note = (
            f"LƯU Ý QUAN TRỌNG: hệ thống KHÔNG có dữ liệu giá cho đúng ngày {requested_date} bạn/người dùng yêu "
            f"cầu (có thể do cuối tuần/nghỉ lễ/chưa crawl) — dữ liệu dưới đây là của phiên gần nhất TRƯỚC đó, "
            f"ngày {max_date_str}. PHẢI nói rõ ràng trong câu trả lời rằng đây là giá ngày {max_date_str}, "
            "KHÔNG được trình bày như thể đây là giá của ngày người dùng hỏi."
        )
    elif max_date_str:
        date_note = f"Dữ liệu dưới đây là giá đóng cửa ngày {max_date_str}."
    else:
        date_note = ""
    return (
        f"{date_note}\n{prices_text}\n"
        "LƯU Ý: đây là 6 instrument DUY NHẤT hệ thống có dữ liệu giá thật (EUA, TTF/gas, API2/than, "
        "Brent, WTI, DEBY1/điện Đức). Dùng ĐÚNG số này (Δ ngày/Δ tuần), TUYỆT ĐỐI KHÔNG tự bịa số hay "
        "mô tả định tính mơ hồ thay cho con số thật. Hỏi về mã KHÔNG nằm trong 6 mã này → hệ thống "
        "không theo dõi, có thể dùng web_search nếu cần số liệu cụ thể, KHÔNG suy đoán."
    )


async def _get_eua_chart_data_buffered(
    session: AsyncSession, report_date: str, chart_cache: Dict[str, List[Any]]
) -> List[Any]:
    """Fetch chart_data EUA (đệm `EUA_VOLUME_SESSIONS_FOR_AVG` phiên trước 30
    phiên hiển thị — xem `_tool_eua_volume_history_text`) DÙNG CHUNG giữa
    `_tool_eua_details_text` và `_tool_eua_volume_history_text` — nếu model
    gọi CẢ 2 tool này trong cùng 1 lượt hỏi (vd vừa hỏi mốc kỹ thuật vừa hỏi so
    sánh khối lượng nhiều ngày), tránh query DB 2 lần cho cùng 1 khoảng dữ liệu
    (bộ 50 phiên đã bao trùm đúng 30 phiên `_tool_eua_details_text` cần —
    `chart_data_buffered[-30:]`). `chart_cache` sống trong đúng 1 lượt hỏi (tạo
    mới ở `_stream_anthropic` mỗi lần gọi), không cache xuyên các câu hỏi khác
    nhau — dữ liệu giá có thể đổi giữa các lần crawl nên không nên cache lâu
    hơn phạm vi 1 câu trả lời.
    """
    if report_date not in chart_cache:
        chart_cache[report_date] = await get_historical_ohlc_for_report(
            session, "EUA", report_date, limit=EUA_VOLUME_HISTORY_SESSIONS + EUA_VOLUME_SESSIONS_FOR_AVG
        )
    return chart_cache[report_date]


async def _tool_eua_details_text(session: AsyncSession, report_date: str, chart_cache: Dict[str, List[Any]]) -> str:
    chart_data_buffered = await _get_eua_chart_data_buffered(session, report_date, chart_cache)
    chart_data = chart_data_buffered[-30:]  # 30 phiên gần nhất — khớp mặc định của report_generator.py
    ohlc_text = _eua_session_range_summary(chart_data)
    volume_text = _eua_volume_summary(chart_data)
    technical_text = _eua_technical_levels_summary(chart_data)
    return (
        f"{ohlc_text}\n{volume_text}\n{technical_text}\n"
        "LƯU Ý: hệ thống CHỈ có OHLC/khối lượng phiên/mốc kỹ thuật chi tiết cho EUA, KHÔNG có cho 5 "
        "instrument còn lại — nếu được hỏi mốc/volume của mã khác, nói rõ hệ thống chưa hỗ trợ, KHÔNG "
        "tự bịa. Mốc kỹ thuật CHỈ LÀ ước lượng tham khảo (đo biên độ dao động lịch sử), không phải dự "
        "đoán chắc chắn hay khuyến nghị đầu tư — vẫn phải trung lập, không nói 'nên mua/nên bán'."
    )


async def _tool_eua_volume_history_text(
    session: AsyncSession, report_date: str, chart_cache: Dict[str, List[Any]]
) -> str:
    chart_data_buffered = await _get_eua_chart_data_buffered(session, report_date, chart_cache)
    return (
        _eua_volume_history_text(chart_data_buffered)
        + "\nLƯU Ý: mỗi dòng đã tính sẵn %chênh lệch so với TB 20 phiên NGAY TRƯỚC ngày đó (cố định "
        "theo đúng ngày, không đổi theo ngày báo cáo đang xem) — DÙNG NGUYÊN số đã tính sẵn, TUYỆT ĐỐI "
        "KHÔNG tự cộng/chia lại trung bình từ các dòng khác. Nếu ngày hỏi không có trong bảng, nói rõ "
        "không có dữ liệu ngày đó thay vì đoán."
    )


PRICE_HISTORY_DEFAULT_SESSIONS = 10
PRICE_HISTORY_MAX_SESSIONS = 60
# Giới hạn độ dài khoảng ngày khi get_price_history có start_date (~1 năm lịch) — đủ
# cho "từ đầu năm"/"quý trước", tránh 1 lệnh gọi kéo cả lịch sử giá vào context.
PRICE_RANGE_MAX_DAYS = 370


async def _resolve_instrument(session: AsyncSession, instrument_input: str) -> Optional[Instrument]:
    """Map chuỗi model truyền (mã hoặc tên gần đúng) sang đúng 1 `Instrument` —
    KHÔNG hardcode enum mã cố định trong tool schema vì `price_crawl_sources`/
    `instruments` được admin CRUD qua /api/admin/price-sources (xem
    db/models.py::PriceCrawlSource), danh sách mã có thể đổi mà không cần sửa
    code. Thử khớp CHÍNH XÁC theo `code` (case-insensitive) trước, sau đó mới
    thử khớp gần đúng theo `name` (ILIKE) — chỉ nhận nếu khớp đúng DUY NHẤT 1
    instrument, tránh đoán nhầm khi chuỗi mơ hồ khớp nhiều mã."""
    exact = (
        await session.execute(select(Instrument).where(func.lower(Instrument.code) == instrument_input.strip().lower()))
    ).scalar_one_or_none()
    if exact:
        return exact
    candidates = (
        await session.execute(select(Instrument).where(Instrument.name.ilike(f"%{instrument_input.strip()}%")))
    ).scalars().all()
    return candidates[0] if len(candidates) == 1 else None


async def _tool_price_history_text(
    session: AsyncSession, instrument_input: str, target_date: str, sessions: int,
    requested_date: Optional[str] = None, start_date: Optional[str] = None,
) -> str:
    """Lịch sử OHLC (+khối lượng nếu có) của 1 instrument BẤT KỲ hệ thống đang
    theo dõi qua NHIỀU phiên — tổng quát hoá `get_historical_ohlc_for_report()`
    (report_generator.py, vốn chỉ được `_tool_eua_details_text`/
    `_tool_eua_volume_history_text` gọi cứng cho "EUA") cho cả 5 instrument
    còn lại (TTF, than, dầu, điện Đức...), vì trước đây get_market_prices chỉ
    cho xem giá CỦA 1 NGÀY, không có tool nào cho xem XU HƯỚNG/lịch sử nhiều
    phiên của các mã ngoài EUA."""
    inst = await _resolve_instrument(session, instrument_input)
    if not inst:
        all_instruments = (await session.execute(select(Instrument.code, Instrument.name))).all()
        listing = ", ".join(f"{code} ({name})" for code, name in all_instruments) or "(không có instrument nào)"
        return (
            f"Không tìm thấy instrument \"{instrument_input}\" — hệ thống đang theo dõi các mã: {listing}. "
            "Gọi lại tool với đúng 1 mã trong danh sách này."
        )

    if start_date:
        # Khoảng ngày cụ thể (vd "tuần trước", "tháng trước" — model quy đổi qua LỊCH
        # THAM CHIẾU trong system prompt): lấy dư rồi lọc đúng các phiên trong
        # [start_date, target_date], giữ thêm 1 phiên NGAY TRƯỚC start_date làm mốc so
        # sánh — "tuần trước tăng bao nhiêu" = đóng cửa phiên cuối tuần trước so với
        # đóng cửa phiên cuối của tuần liền trước đó, không phải so với phiên đầu tuần.
        span_days = (date_cls.fromisoformat(target_date) - date_cls.fromisoformat(start_date)).days + 1
        buffered = await get_historical_ohlc_for_report(
            session, inst.code, target_date, limit=min(span_days, PRICE_RANGE_MAX_DAYS) + 5
        )
        in_range = [c for c in buffered if str(c["date"]) >= start_date]
        before = [c for c in buffered if str(c["date"]) < start_date]
        return _format_price_range_text(inst, in_range, before[-1] if before else None, start_date, target_date)

    chart_data = await get_historical_ohlc_for_report(session, inst.code, target_date, limit=sessions)
    if not chart_data:
        return f"Không có dữ liệu lịch sử giá cho {inst.name} ({inst.code}) tính đến ngày {target_date}."

    last_date_str = str(chart_data[-1]["date"])
    date_note = ""
    if requested_date and last_date_str != requested_date:
        date_note = (
            f"LƯU Ý QUAN TRỌNG: hệ thống KHÔNG có dữ liệu đúng ngày {requested_date} bạn yêu cầu (cuối "
            f"tuần/nghỉ lễ/chưa crawl) — dữ liệu dưới đây tính đến phiên gần nhất TRƯỚC đó, ngày "
            f"{last_date_str}. PHẢI nói rõ ngày thực tế này trong câu trả lời.\n"
        )

    lines = [
        f"- {c['date']}: mở {c['open']:.2f}, cao {c['high']:.2f}, thấp {c['low']:.2f}, đóng {c['close']:.2f}"
        + (f", KL {c['volume']:,.0f}" if c.get("volume") is not None else "")
        for c in chart_data
    ]
    first_close, last_close = chart_data[0]["close"], chart_data[-1]["close"]
    change_pct = ((last_close - first_close) / first_close * 100) if first_close else 0
    high = max(c["high"] for c in chart_data)
    low = min(c["low"] for c in chart_data)

    return (
        f"{date_note}Lịch sử giá {inst.name} ({inst.code}, đơn vị {inst.unit or '?'}) — {len(chart_data)} phiên gần nhất "
        f"tính đến {last_date_str}:\n" + "\n".join(lines) + "\n"
        f"Tổng hợp: mở {first_close:.2f} → đóng gần nhất {last_close:.2f} ({change_pct:+.1f}%); "
        f"cao nhất {high:.2f}, thấp nhất {low:.2f} trong giai đoạn này.\n"
        "LƯU Ý: dùng ĐÚNG số liệu này, TUYỆT ĐỐI KHÔNG tự bịa số khác hay suy diễn thêm mốc kỹ thuật "
        f"(mốc hỗ trợ/kháng cự tính sẵn CHỈ có cho EUA, qua tool get_eua_details) cho {inst.code}."
    )


def _format_price_range_text(
    inst: Instrument, rows: List[Dict[str, Any]], baseline: Optional[Dict[str, Any]], start_date: str, end_date: str
) -> str:
    """Kết quả get_price_history khi có start_date — tính SẴN biến động cả giai đoạn
    bằng Python (không để model tự cộng trừ) để câu trả lời "tuần/tháng trước tăng
    giảm bao nhiêu" luôn chính xác."""
    period = f"{start_date} → {end_date}"
    if not rows:
        return (
            f"Không có phiên giao dịch nào của {inst.name} ({inst.code}) trong giai đoạn {period} "
            "(nghỉ lễ/cuối tuần/chưa crawl). PHẢI nói rõ điều này, KHÔNG tự bịa số liệu giai đoạn đó."
        )
    lines = [
        f"- {c['date']} ({_weekday_vn(date_cls.fromisoformat(str(c['date'])))}): mở {c['open']:.2f}, "
        f"cao {c['high']:.2f}, thấp {c['low']:.2f}, đóng {c['close']:.2f}"
        + (f", KL {c['volume']:,.0f}" if c.get("volume") is not None else "")
        for c in rows
    ]
    last = rows[-1]
    high = max(c["high"] for c in rows)
    low = min(c["low"] for c in rows)
    if baseline:
        delta = last["close"] - baseline["close"]
        pct = (delta / baseline["close"] * 100) if baseline["close"] else 0
        change = (
            f"đóng cửa phiên cuối giai đoạn ({last['date']}) {last['close']:.2f} so với đóng cửa phiên "
            f"liền trước giai đoạn ({baseline['date']}) {baseline['close']:.2f}: {delta:+.2f} ({pct:+.2f}%)"
        )
    else:
        first = rows[0]
        delta = last["close"] - first["close"]
        pct = (delta / first["close"] * 100) if first["close"] else 0
        change = (
            f"(không có phiên nào trước giai đoạn để làm mốc) đóng cửa {first['date']} {first['close']:.2f} "
            f"→ {last['date']} {last['close']:.2f}: {delta:+.2f} ({pct:+.2f}%)"
        )
    span_days = (date_cls.fromisoformat(end_date) - date_cls.fromisoformat(start_date)).days + 1
    truncated = (
        f"LƯU Ý: khoảng dài hơn {PRICE_RANGE_MAX_DAYS} ngày — chỉ lấy được phần cuối giai đoạn, phần đầu bị thiếu.\n"
        if span_days > PRICE_RANGE_MAX_DAYS else ""
    )
    return (
        f"{truncated}Giá {inst.name} ({inst.code}, đơn vị {inst.unit or '?'}) giai đoạn {period} — "
        f"{len(rows)} phiên có dữ liệu:\n" + "\n".join(lines) + "\n"
        f"Biến động cả giai đoạn (đã tính sẵn): {change}. Cao nhất {high:.2f}, thấp nhất {low:.2f}.\n"
        "LƯU Ý: dùng ĐÚNG số đã tính sẵn này, KHÔNG tự tính lại; nêu rõ khoảng ngày thực tế trong câu trả lời."
    )


def _format_section1(sec: dict) -> str:
    return f"{sec.get('title', 'Tóm tắt điều hành')}\n{_format_bullets(sec.get('bullets'))}"


def _format_section2(sec: dict) -> str:
    drivers = sec.get("market_drivers") or {}
    developments = "\n".join(
        f"- [{d.get('impact', '')}] {d.get('text', '')}"
        + (f" (nguồn: {d['source_name']}" + (f" — {d['source_url']}" if d.get("source_url") else "") + ")" if d.get("source_name") else "")
        for d in sec.get("key_developments") or []
    )
    return (
        f"{sec.get('title', 'Bảng giá nhanh')}\n"
        f"{sec.get('price_timestamp', '')}\n"
        f"{sec.get('key_facts', '')}\n"
        f"Diễn biến chính:\n{developments or '(không có)'}\n"
        f"Yếu tố hỗ trợ tăng giá:\n{_format_bullets(drivers.get('bullish'))}\n"
        f"Yếu tố hỗ trợ giảm giá:\n{_format_bullets(drivers.get('bearish'))}"
    )


def _format_section3(sec: dict) -> str:
    blocks = "\n\n".join(
        f"{b.get('heading', '')}: {b.get('content', '')}" for b in sec.get("analysis_blocks") or []
    )
    scenarios = "\n".join(
        f"- [{s.get('horizon')}] {s.get('direction')} (xác suất {s.get('probability')}): "
        f"{s.get('condition')} | Vùng giá: {s.get('price_zone')} | Rủi ro: {s.get('key_risk')} | "
        f"Chiến lược: {s.get('trading_strategy')}"
        for s in sec.get("trading_scenarios") or []
    )
    return f"{sec.get('title', 'Phân tích chuyên sâu')}\n{blocks}\n\nKịch bản giao dịch:\n{scenarios or '(không có)'}"


def _format_section4(sec: dict) -> str:
    return f"{sec.get('title', 'Cập nhật tín chỉ carbon & CBAM')}\n{_format_bullets(sec.get('bullets'))}"


def _format_section6(sec: dict) -> str:
    def _list(articles: Optional[List[dict]]) -> str:
        lines = [
            f"- [{a.get('source', '?')}] {a.get('title', '')}: {a.get('summary', '')}"
            + (f" ({a['url']})" if a.get("url") else "")
            for a in articles or []
        ]
        return "\n".join(lines) if lines else "(không có)"

    return (
        f"{sec.get('title', 'Chi tiết các tin tức chính')}\n"
        f"Tin quốc tế:\n{_list(sec.get('international'))}\n\n"
        f"Tin Việt Nam:\n{_list(sec.get('vietnam'))}"
    )


def _format_section8(sec: dict) -> str:
    lines = []
    for ev in sec.get("events") or []:
        outcome = ev.get("outcome")
        lines.append(
            f"- {ev.get('datetime_vn', ev.get('date', '?'))}: {ev.get('event', '')} "
            f"(mức độ tác động: {ev.get('impact', '?')})" + (f" — Kết quả: {outcome}" if outcome else "")
        )
    return f"{sec.get('title', 'Lịch sự kiện 7 ngày tới')}\n" + ("\n".join(lines) if lines else "(không có sự kiện)")


def _format_section9(sec: dict) -> str:
    lines = [f"- [{it.get('source', '?')}] {it.get('title', '')} ({it.get('url', '')})" for it in sec.get("items") or []]
    return f"{sec.get('title', 'Nguồn tham khảo')}\n" + ("\n".join(lines) if lines else "(không có nguồn)")


def _format_section_biz(sec: dict) -> str:
    short_term = "\n".join(
        f"- Kích hoạt: {it.get('trigger', '')} | Hành động: {it.get('action', '')} | Lý do: {it.get('reason', '')}"
        for it in sec.get("short_term") or []
    ) or "(không có gợi ý ngắn hạn)"
    long_term = "\n".join(
        f"- Cơ hội: {it.get('opportunity', '')} | Giải pháp: {it.get('solution', '')} | Kỳ vọng: {it.get('expectation', '')}"
        for it in sec.get("long_term") or []
    ) or "(không có gợi ý dài hạn)"
    # Bộ nhớ gợi ý (services/biz_memory.py): gợi ý cũ vừa kích hoạt / còn đang theo dõi.
    reminders = "\n".join(
        f"- Đề xuất ngày {it.get('suggested_date', '')}: {it.get('action', '')} (khi: {it.get('trigger', '')}) "
        f"— ĐÃ KÍCH HOẠT: {it.get('evidence') or ''}"
        for it in sec.get("reminders") or []
    )
    tracking = "\n".join(
        f"- Đề xuất ngày {it.get('suggested_date', '')}: {it.get('action', '')} (khi: {it.get('trigger', '')}) — chưa kích hoạt"
        for it in sec.get("tracking") or []
    )
    memory = ""
    if reminders or tracking:
        memory = f"\n\nGợi ý cũ Jenny đang theo dõi:\n{reminders}\n{tracking}".rstrip()
    return (
        f"{sec.get('title', 'Gợi ý kinh doanh & giải pháp cho SIM')}\n"
        f"Ngắn hạn:\n{short_term}\n\nDài hạn:\n{long_term}{memory}"
    )


_REPORT_SECTION_FORMATTERS = {
    "1": _format_section1,
    "2": _format_section2,
    "3": _format_section3,
    "4": _format_section4,
    "6": _format_section6,
    "8": _format_section8,
    "9": _format_section9,
    "biz": _format_section_biz,
}


async def _tool_report_section_text(session: AsyncSession, report_date: str, section: str) -> str:
    """Lấy + format 1 mục (Mục 1, 2, 3, 4, 6, 8, 9 hoặc "biz") của báo cáo
    `report_date` (đã published) — CHỈ mục được yêu cầu, không cả báo cáo cùng
    lúc (khác bản trước khi có tool calling, luôn tiêm cả 3 mục dù chỉ cần 1)."""
    formatter = _REPORT_SECTION_FORMATTERS.get(section)
    if not formatter:
        return "Mục không hợp lệ — chỉ hỗ trợ '1', '2', '3', '4', '6', '8', '9' hoặc 'biz'."
    stmt = select(Report.content).where(Report.report_date == report_date, Report.status == "published")
    result = await session.execute(stmt)
    content = result.scalar_one_or_none()
    sec = (content or {}).get(section)
    if not sec:
        return f"Không có nội dung Mục {section} cho báo cáo ngày {report_date}."
    return _truncate(formatter(sec), MAX_REPORT_CHARS)


_BIZ_STATUS_LABEL = {"pending": "đang theo dõi (chưa kích hoạt)", "triggered": "ĐÃ KÍCH HOẠT", "contradicted": "THỰC TẾ NGƯỢC VỚI ĐỀ XUẤT", "dismissed": "admin đã gỡ"}


async def _tool_biz_suggestions_text(
    session: AsyncSession, report_date: str, status: str, kind: str, days: int
) -> str:
    """Bộ nhớ gợi ý kinh doanh của Jenny (bảng biz_suggestions, xem services/biz_memory.py)
    — CHỈ ĐỌC. Lấy gợi ý đề xuất trong `days` ngày kết thúc ở `report_date` (gồm cả ngày đó).
    Khác get_report_section('biz'): đọc thẳng bảng nên có trạng thái mới nhất, bằng chứng
    kích hoạt và cả gợi ý đã gỡ; không phụ thuộc nội dung đóng băng trong báo cáo cũ."""
    since = (date_cls.fromisoformat(report_date) - timedelta(days=days)).isoformat()
    stmt = select(BizSuggestion).where(
        BizSuggestion.first_report_date >= since,
        BizSuggestion.first_report_date <= report_date,
    )
    if status == "active":
        stmt = stmt.where(BizSuggestion.status.in_(("pending", "triggered", "contradicted")))
    elif status != "all":
        stmt = stmt.where(BizSuggestion.status == status)
    if kind != "all":
        stmt = stmt.where(BizSuggestion.kind == kind)
    rows = (await session.execute(stmt.order_by(BizSuggestion.first_report_date, BizSuggestion.id))).scalars().all()
    if not rows:
        return f"Không có gợi ý nào (status={status}, kind={kind}) trong {days} ngày đến {report_date}."

    lines = []
    for r in rows:
        d = _fmt_vn_date_short(r.first_report_date)
        if r.kind == "long":
            lines.append(f"- [DÀI HẠN, đề xuất {d}] Cơ hội: {r.trigger} | Giải pháp: {r.action} | Kỳ vọng: {r.reason}")
            continue
        line = (
            f"- [NGẮN HẠN, đề xuất {d}, {_BIZ_STATUS_LABEL.get(r.status, r.status)}] "
            f"Khi: {r.trigger} | Hành động: {r.action} | Lý do: {r.reason}"
        )
        if r.status in ("triggered", "contradicted"):
            label = "Kích hoạt" if r.status == "triggered" else "Thực tế ngược đề xuất"
            line += f" | {label} ngày {_fmt_vn_date_short(r.triggered_report_date)}: {r.trigger_evidence or ''}"
            if r.evidence_source_name:
                line += f" (nguồn: {r.evidence_source_name} {r.evidence_source_url or ''})".rstrip()
        if r.status == "dismissed" and r.dismiss_reason:
            line += f" | Lý do gỡ: {r.dismiss_reason}"
        lines.append(line)
    return _truncate(
        f"Gợi ý kinh doanh của Jenny — {days} ngày đến {report_date} (status={status}, kind={kind}):\n"
        + "\n".join(lines)
        + "\nLƯU Ý: chỉ nêu đúng các gợi ý và bằng chứng ở trên; gợi ý chưa kích hoạt nghĩa là hệ thống chưa "
        "ghi nhận tình huống xảy ra, KHÔNG tự khẳng định thêm. Bạn không thể tạo/sửa/gỡ gợi ý — việc gỡ do admin thực hiện.",
        MAX_REPORT_CHARS,
    )


STATS_MAX_INSTRUMENTS = 4
STATS_DEFAULT_SESSIONS = 30
STATS_MIN_COMMON_FOR_CORR = 5


def _pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sxx * syy) ** 0.5


def _compute_price_stats(series: Dict[str, List[tuple]], units: Dict[str, Optional[str]]) -> str:
    """Thống kê TÍNH SẴN bằng Python cho 1–4 instrument — model không tự cộng trừ/tương quan.
    `series`: code -> [(ngày 'YYYY-MM-DD', giá đóng cửa)] tăng dần theo ngày, đã lọc trong khoảng.
    Tương quan tính trên LỢI SUẤT NGÀY (% thay đổi phiên liền kề) của các ngày CHUNG của cả 2 mã —
    không phải trên mức giá (tương quan mức giá dễ cho kết quả giả khi cùng có xu hướng)."""
    lines: List[str] = []
    returns: Dict[str, Dict[str, float]] = {}
    for code, rows in series.items():
        if len(rows) < 2:
            lines.append(f"- {code}: chỉ có {len(rows)} phiên trong khoảng — không đủ để tính thống kê.")
            continue
        closes = [c for _, c in rows]
        first_d, first_c = rows[0]
        last_d, last_c = rows[-1]
        hi = max(rows, key=lambda r: r[1])
        lo = min(rows, key=lambda r: r[1])
        rets = {
            rows[i][0]: (rows[i][1] - rows[i - 1][1]) / rows[i - 1][1] * 100
            for i in range(1, len(rows)) if rows[i - 1][1]
        }
        returns[code] = rets
        vals = list(rets.values())
        mean = sum(vals) / len(vals) if vals else 0.0
        std = (sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)) ** 0.5 if len(vals) > 1 else 0.0
        best = max(rets.items(), key=lambda kv: kv[1])
        worst = min(rets.items(), key=lambda kv: kv[1])
        change = (last_c - first_c) / first_c * 100 if first_c else 0.0
        lines.append(
            f"- {code} ({len(rows)} phiên, {first_d} → {last_d}): đóng cửa {first_c:.2f} → {last_c:.2f} "
            f"({change:+.2f}%, {last_c - first_c:+.2f} {units.get(code) or ''}); cao nhất {hi[1]:.2f} ({hi[0]}), "
            f"thấp nhất {lo[1]:.2f} ({lo[0]}); độ lệch chuẩn biến động ngày {std:.2f}%; "
            f"phiên tăng mạnh nhất {best[1]:+.2f}% ({best[0]}), giảm mạnh nhất {worst[1]:+.2f}% ({worst[0]})."
        )

    codes = [c for c in series if c in returns]
    pair_lines: List[str] = []
    for i in range(len(codes)):
        for j in range(i + 1, len(codes)):
            a, b = codes[i], codes[j]
            common = sorted(set(returns[a]) & set(returns[b]))
            if len(common) < STATS_MIN_COMMON_FOR_CORR:
                pair_lines.append(f"- {a} vs {b}: chỉ {len(common)} ngày chung — quá ít để tính tương quan.")
                continue
            r = _pearson([returns[a][d] for d in common], [returns[b][d] for d in common])
            if r is None:
                pair_lines.append(f"- {a} vs {b}: không tính được tương quan (một mã không biến động).")
            else:
                pair_lines.append(f"- {a} vs {b}: tương quan lợi suất ngày = {r:+.2f} ({len(common)} ngày chung).")
            la, lb = dict(series[a]), dict(series[b])
            last_common = max(set(la) & set(lb), default=None)
            if last_common:
                ua, ub = units.get(a), units.get(b)
                pair_lines.append(
                    f"  Ngày chung gần nhất {last_common}: {a}={la[last_common]:.2f}, {b}={lb[last_common]:.2f}, "
                    f"tỷ lệ {a}/{b}={la[last_common] / lb[last_common]:.3f}"
                    + (f", chênh lệch {la[last_common] - lb[last_common]:+.2f} {ua}" if ua and ua == ub else
                       f" (khác đơn vị: {a}={ua or '?'}, {b}={ub or '?'} — KHÔNG trừ trực tiếp, cần quy đổi)")
                )
    out = "Thống kê giá (tính sẵn, dựa trên giá đóng cửa):\n" + "\n".join(lines)
    if pair_lines:
        out += "\nQuan hệ giữa các mã:\n" + "\n".join(pair_lines)
    out += (
        "\nLƯU Ý: dùng ĐÚNG các số đã tính sẵn này, KHÔNG tự tính lại. Tương quan ≠ nhân quả; mẫu ngắn (<20 ngày) "
        "kém tin cậy — nói rõ số phiên. Nêu đúng khoảng ngày thực tế."
    )
    return out


async def _tool_price_stats_text(
    session: AsyncSession, instruments: List[str], end_date: str, start_date: Optional[str], sessions: int
) -> str:
    resolved: List[Instrument] = []
    for name in instruments[:STATS_MAX_INSTRUMENTS]:
        inst = await _resolve_instrument(session, str(name))
        if not inst:
            all_instruments = (await session.execute(select(Instrument.code, Instrument.name))).all()
            listing = ", ".join(f"{c} ({n})" for c, n in all_instruments) or "(không có)"
            return f"Không tìm thấy instrument \"{name}\" — các mã hệ thống theo dõi: {listing}."
        if inst.code not in [r.code for r in resolved]:
            resolved.append(inst)
    if not resolved:
        return "Cần truyền ít nhất 1 instrument."

    if start_date:
        span = (date_cls.fromisoformat(end_date) - date_cls.fromisoformat(start_date)).days + 1
        limit = min(max(span, 2), PRICE_RANGE_MAX_DAYS) + 5
    else:
        limit = sessions
    series: Dict[str, List[tuple]] = {}
    for inst in resolved:
        data = await get_historical_ohlc_for_report(session, inst.code, end_date, limit=limit)
        rows = [(str(c["date"]), float(c["close"])) for c in data if c.get("close") is not None]
        if start_date:
            rows = [r for r in rows if r[0] >= start_date]
        series[inst.code] = sorted(rows)
    if not any(series.values()):
        return f"Không có dữ liệu giá nào tính đến {end_date}."
    return _compute_price_stats(series, {i.code: i.unit for i in resolved})


_WEEKDAYS_FULL_VN = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
_EUA_SUMMARY_TAG_RE = re.compile(r"\*\*Tổng hợp\s*:?\*\*")
HISTORY_MAX_DAYS = 14
HISTORY_DEFAULT_DAYS = 7
CALENDAR_MAX_DAYS = 90


def _fmt_day_full(iso: str) -> str:
    """'2026-09-30' -> 'Thứ Tư 30/09/2026'."""
    try:
        d = date_cls.fromisoformat(iso)
        return f"{_WEEKDAYS_FULL_VN[d.weekday()]} {d:%d/%m/%Y}"
    except ValueError:
        return iso


def _extract_eua_verdict(content: dict) -> Optional[str]:
    """Nhận định tổng quan về EUA: phần sau thẻ '**Tổng hợp:**' trong analysis_blocks của Mục 3 —
    ĐÚNG cách frontend (ReportDocument.tsx::extractEuaSummary) rút ra khối NHẬN ĐỊNH TỔNG QUAN."""
    for block in ((content.get("3") or {}).get("analysis_blocks") or []):
        for line in str(block.get("content") or "").split("\n"):
            m = _EUA_SUMMARY_TAG_RE.search(line)
            if m:
                return line[m.end():].strip() or None
    return None


def _scenario(content: dict, horizon: str) -> Optional[dict]:
    """Kịch bản theo horizon ('ngắn hạn'/'trung hạn') — frontend dùng đúng so khớp này cho TÍN HIỆU HÔM NAY."""
    for sc in ((content.get("3") or {}).get("trading_scenarios") or []):
        if isinstance(sc, dict) and sc.get("horizon") == horizon:
            return sc
    return None


def _format_scenario(sc: Optional[dict]) -> str:
    if not sc:
        return "(không có)"
    return (
        f"{sc.get('direction', '?')} (xác suất {sc.get('probability', '?')}) — điều kiện: {sc.get('condition', '')} | "
        f"vùng giá: {sc.get('price_zone', '')} | rủi ro: {sc.get('key_risk', '')} | chiến lược: {sc.get('trading_strategy', '')}"
    )


_HISTORY_SECTIONS = ("verdict", "signal", "1", "2", "3", "4", "6", "8", "biz")


def _report_digest(content: dict, section: str) -> str:
    """Trích 1 'mảng' nội dung của 1 báo cáo để so sánh qua nhiều ngày."""
    if section == "verdict":
        return _extract_eua_verdict(content) or "(báo cáo không có nhận định tổng quan)"
    if section == "signal":
        return (
            f"Ngắn hạn: {_format_scenario(_scenario(content, 'ngắn hạn'))}\n"
            f"Trung hạn: {_format_scenario(_scenario(content, 'trung hạn'))}"
        )
    formatter = _REPORT_SECTION_FORMATTERS.get(section)
    sec = content.get(section)
    if not formatter or not sec:
        return f"(báo cáo không có Mục {section})"
    return formatter(sec)


async def _tool_report_history_text(session: AsyncSession, end_date: str, section: str, days: int) -> str:
    if section not in _HISTORY_SECTIONS:
        return f"Mảng không hợp lệ — chỉ hỗ trợ: {', '.join(_HISTORY_SECTIONS)}."
    since = (date_cls.fromisoformat(end_date) - timedelta(days=days - 1)).isoformat()
    rows = (
        await session.execute(
            select(Report.report_date, Report.content)
            .where(Report.status == "published", Report.report_date >= since, Report.report_date <= end_date)
            .order_by(Report.report_date)
        )
    ).all()
    if not rows:
        return f"Không có báo cáo published nào trong khoảng {since} → {end_date}."
    blocks = [f"=== Báo cáo {_fmt_day_full(d)} ===\n{_report_digest(c or {}, section)}" for d, c in rows]
    missing = days - len(rows)
    note = (
        f"\nLƯU Ý: chỉ {len(rows)}/{days} ngày trong khoảng có báo cáo published (các ngày khác — cuối tuần/nghỉ lễ/"
        "chưa duyệt — không có dữ liệu); nêu đúng các ngày thực có, KHÔNG suy đoán ngày thiếu."
        if missing > 0 else ""
    )
    return _truncate("\n\n".join(blocks) + note, MAX_REPORT_CHARS)


FORECAST_MAX_SESSIONS = 20
FORECAST_DEFAULT_SESSIONS = 5


def _evaluate_forecast(direction: Optional[str], base_close: float, after: List[tuple]) -> str:
    """So hướng dự báo với diễn biến giá thực — phần SỐ tính sẵn bằng Python. 'cùng chiều/ngược chiều' chỉ
    gán cho dự báo tăng/giảm; 'đi ngang' không gán nhãn đúng/sai (không có ngưỡng chuẩn trong hệ thống)."""
    lines = [f"- {d}: đóng cửa {c:.2f} ({(c - base_close) / base_close * 100:+.2f}% so với mốc {base_close:.2f})" for d, c in after]
    last_d, last_c = after[-1]
    chg = (last_c - base_close) / base_close * 100
    peak = max(after, key=lambda x: x[1])
    trough = min(after, key=lambda x: x[1])
    if direction in ("tăng", "giảm"):
        same = (chg > 0) == (direction == "tăng") and chg != 0
        verdict = (
            f"Kết quả sau {len(after)} phiên (đến {last_d}): giá {chg:+.2f}% → "
            + ("CÙNG chiều với dự báo " if same else "NGƯỢC chiều (hoặc đứng yên) so với dự báo ") + f"\"{direction}\"."
        )
    else:
        verdict = (
            f"Kết quả sau {len(after)} phiên (đến {last_d}): giá {chg:+.2f}% — dự báo \"{direction or 'không rõ'}\" không có "
            "ngưỡng chuẩn nên KHÔNG gán nhãn đúng/sai, chỉ nêu số liệu."
        )
    return (
        "\n".join(lines)
        + f"\nCao nhất {peak[1]:.2f} ({peak[0]}), thấp nhất {trough[1]:.2f} ({trough[0]}) trong {len(after)} phiên sau báo cáo.\n"
        + verdict
    )


async def _tool_review_forecast_text(session: AsyncSession, report_date: str, sessions: int) -> str:
    content = (
        await session.execute(select(Report.content).where(Report.report_date == report_date, Report.status == "published"))
    ).scalar_one_or_none()
    if not content:
        return f"Không có báo cáo published ngày {report_date} để đối chiếu."
    base_date = report_data_date(report_date)
    horizon_end = (date_cls.fromisoformat(report_date) + timedelta(days=sessions * 2 + 10)).isoformat()
    hist = await get_historical_ohlc_for_report(session, "EUA", horizon_end, limit=sessions + 40)
    rows = sorted((str(c["date"]), float(c["close"])) for c in hist if c.get("close") is not None)
    before = [r for r in rows if r[0] <= base_date]
    after = [r for r in rows if r[0] > base_date][:sessions]
    short = _scenario(content, "ngắn hạn")
    head = (
        f"Báo cáo {_fmt_day_full(report_date)} (dữ liệu đến phiên {base_date}):\n"
        f"Nhận định tổng quan: {_extract_eua_verdict(content) or '(không có)'}\n"
        f"Kịch bản ngắn hạn: {_format_scenario(short)}\nTrung hạn: {_format_scenario(_scenario(content, 'trung hạn'))}\n"
    )
    if not before:
        return head + "Không có giá EUA tại/trước mốc báo cáo để làm cơ sở so sánh."
    if not after:
        return head + f"Chưa có phiên EUA nào SAU mốc {before[-1][0]} trong hệ thống — còn quá sớm để đối chiếu dự báo."
    base_d, base_c = before[-1]
    return (
        head + f"\nDiễn biến EUA thực tế (mốc {base_d}: {base_c:.2f}):\n"
        + _evaluate_forecast(short.get("direction") if short else None, base_c, after)
        + "\nLƯU Ý: chỉ nêu kết quả đối chiếu trên; vùng giá/điều kiện của kịch bản là văn bản tự do — tự so với số liệu "
        "trên và nói rõ nếu không đủ dữ liệu, KHÔNG phán quyết thêm."
    )


async def _tool_report_overview_text(session: AsyncSession, report_date: str) -> str:
    row = (
        await session.execute(select(Report.status, Report.content, Report.published_at).where(Report.report_date == report_date))
    ).first()
    if row is None:
        return f"Chưa có báo cáo nào cho ngày {report_date}."
    status, content, published_at = row
    if status != "published" or not content:
        return f"Báo cáo ngày {report_date} chưa ở trạng thái published (trạng thái: {status}) — không có nội dung để xem."
    c = content
    sec2 = c.get("2") or {}
    sec6 = c.get("6") or {}
    biz = c.get("biz") or {}
    n = lambda x: len(x or [])
    out = [
        f"TỔNG QUAN báo cáo {_fmt_day_full(report_date)} (dữ liệu đến phiên {report_data_date(report_date)}):",
        f"- Nhận định tổng quan EUA: {_extract_eua_verdict(c) or '(không có)'}",
        f"- Kịch bản ngắn hạn: {_format_scenario(_scenario(c, 'ngắn hạn'))}",
        f"- Mục 1 Tóm tắt điều hành: {n((c.get('1') or {}).get('bullets'))} ý",
        f"- Mục 2 Bảng giá nhanh: {n(sec2.get('prices'))} instrument, {n(sec2.get('key_developments'))} diễn biến chính, "
        f"{n((sec2.get('market_drivers') or {}).get('bullish'))} yếu tố hỗ trợ tăng / {n((sec2.get('market_drivers') or {}).get('bearish'))} hỗ trợ giảm",
        f"- Mục 3 Phân tích: {n((c.get('3') or {}).get('analysis_blocks'))} khối, {n((c.get('3') or {}).get('trading_scenarios'))} kịch bản giao dịch",
        f"- Mục 4 Tín chỉ carbon & CBAM: {n((c.get('4') or {}).get('bullets'))} ý",
        f"- Mục 6 Tin tức: {n(sec6.get('international'))} tin quốc tế, {n(sec6.get('vietnam'))} tin Việt Nam",
        f"- Mục 8 Lịch sự kiện: {n((c.get('8') or {}).get('events'))} sự kiện",
        f"- Gợi ý kinh doanh: {n(biz.get('short_term'))} ngắn hạn, {n(biz.get('long_term'))} dài hạn, "
        f"{n(biz.get('reminders'))} nhắc lại, {n(biz.get('tracking'))} đang theo dõi",
        "LƯU Ý: đây chỉ là mục lục; cần nội dung chi tiết thì gọi get_report_section với đúng mục.",
    ]
    return "\n".join(out)


async def _tool_calendar_text(session: AsyncSession, start_date: str, end_date: str) -> str:
    """Lịch sự kiện trong khoảng bất kỳ (tối đa CALENDAR_MAX_DAYS ngày): (a) sự kiện định kỳ tính sẵn
    (EIA thứ Tư, Baker Hughes thứ Sáu — quy đổi giờ VN) + (b) sự kiện đã ghi trong Mục 8 của các báo cáo
    published phủ khoảng này (có thể kèm kết quả đã xảy ra)."""
    s_d, e_d = date_cls.fromisoformat(start_date), date_cls.fromisoformat(end_date)
    events: Dict[tuple, dict] = {}
    cur = s_d
    while cur <= e_d:
        for ev in _compute_recurring_calendar_events(cur.isoformat()):
            if start_date <= ev["date"] <= end_date:
                events[(ev["date"], ev["event"])] = {**ev, "from": "định kỳ"}
        cur += timedelta(days=7)
    rows = (
        await session.execute(
            select(Report.report_date, Report.content).where(
                Report.status == "published",
                Report.report_date >= (s_d - timedelta(days=7)).isoformat(),
                Report.report_date <= end_date,
            ).order_by(Report.report_date)
        )
    ).all()
    for rd, content in rows:
        for ev in ((content or {}).get("8") or {}).get("events") or []:
            d = str(ev.get("date") or "")
            if start_date <= d <= end_date:
                key = (d, ev.get("event"))
                prev = events.get(key)
                # Bản ghi từ báo cáo MỚI hơn ghi đè (vì có thể đã có kết quả); giữ outcome nếu bản mới không có.
                merged = {**(prev or {}), **ev, "from": f"báo cáo {rd}"}
                if prev and not ev.get("outcome") and prev.get("outcome"):
                    merged["outcome"] = prev["outcome"]
                events[key] = merged
    if not events:
        return f"Không có sự kiện nào trong hệ thống cho khoảng {start_date} → {end_date}."
    lines = []
    for (d, _), ev in sorted(events.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
        lines.append(
            f"- {_fmt_day_full(d)}: {ev.get('event', '')} (mức độ tác động: {ev.get('impact', '?')}; nguồn: {ev['from']})"
            + (f" — Kết quả: {ev['outcome']}" if ev.get("outcome") else "")
        )
    return (
        f"Sự kiện {start_date} → {end_date}:\n" + "\n".join(lines)
        + "\nLƯU Ý: hệ thống CHỈ có lịch định kỳ (EIA, Baker Hughes) và các sự kiện đã ghi trong Mục 8 các báo cáo — "
        "KHÔNG có lịch đầy đủ (vd họp ECB/Fed, đấu giá EUA) nếu chưa từng được ghi; nói rõ giới hạn này, không bịa."
    )


async def _tool_list_reports_text(session: AsyncSession, limit: int) -> str:
    """Các ngày đã có báo cáo published (mới → cũ) — để model biết `date` nào hợp lệ khi
    gọi get_report_section/get_biz_suggestions cho ngày khác, thay vì đoán ngày."""
    rows = (
        await session.execute(
            select(Report.report_date)
            .where(Report.status == "published")
            .order_by(Report.report_date.desc())
            .limit(limit)
        )
    ).scalars().all()
    if not rows:
        return "Chưa có báo cáo nào được published."
    return (
        f"{len(rows)} báo cáo published gần nhất (mới → cũ): " + ", ".join(rows) + "\n"
        "LƯU Ý: chỉ những ngày này mới truy vấn được báo cáo; ngày không có trong danh sách = chưa có báo cáo "
        "(cuối tuần/nghỉ lễ/chưa duyệt) — nêu rõ với người dùng thay vì đoán."
    )


_TOPIC_VALUES = (
    "eua_ets", "energy_gas", "energy_power_eu", "energy_coal", "energy_oil", "energy_renewable",
    "energy_hydrogen", "geopolitics", "eu_policy", "cbam", "vcm", "global_carbon_market", "vietnam_carbon_policy",
)


async def _tool_browse_news_text(
    session: AsyncSession, data_date: str, topic: Optional[str], region: Optional[str], hot_only: bool, limit: int
) -> str:
    """Liệt kê CÓ CẤU TRÚC các bài đã crawl trong 1 ngày dữ liệu (khung [D 00:00 UTC, D+1) — khớp
    retrieval.py/get_news_for_report), lọc theo topic/region/hot. Khác search_news (tìm theo ngữ
    nghĩa từ khoá): dùng cho câu hỏi 'hôm nay có những tin gì về CBAM', 'tin nóng', 'tin Việt Nam'."""
    start = datetime.strptime(data_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    stmt = select(Article).where(
        Article.crawled_at >= start, Article.crawled_at < start + timedelta(days=1), Article.is_relevant.is_(True),
    )
    if topic:
        stmt = stmt.where(Article.topic.any(topic))
    if region:
        stmt = stmt.where(Article.region == region)
    if hot_only:
        stmt = stmt.where(Article.is_hot_news.is_(True))
    rows = (
        await session.execute(stmt.order_by(Article.is_hot_news.desc(), Article.published_at.desc().nullslast()).limit(limit))
    ).scalars().all()
    filt = ", ".join(x for x in (f"topic={topic}" if topic else "", f"region={region}" if region else "", "chỉ tin nóng" if hot_only else "") if x) or "không lọc"
    if not rows:
        return f"Không có bài nào (ngày dữ liệu {data_date}, {filt})."
    lines = []
    for a in rows:
        pub = a.published_at.astimezone(timezone(timedelta(hours=7))).strftime("%d/%m/%Y %H:%M") if a.published_at else "?"
        hot = f" | TIN NÓNG: {a.hot_news_reason or ''}" if a.is_hot_news else ""
        lines.append(
            f"- [id={a.id}] ({a.source}, hạng {a.source_tier or '?'}, {pub}) {a.title or '(không tiêu đề)'} "
            f"| topic: {', '.join(a.topic or [])}{hot}"
        )
    return (
        f"{len(rows)} bài (ngày dữ liệu {data_date}, {filt}, tối đa {limit}):\n" + "\n".join(lines)
        + "\nLƯU Ý: đây chỉ là danh sách tiêu đề; cần nội dung chi tiết thì gọi get_article với id. TUYỆT ĐỐI "
        "KHÔNG bịa nội dung bài ngoài những gì tool trả về."
    )


async def _tool_article_text(session: AsyncSession, article_id: Optional[int], url: Optional[str]) -> str:
    """Toàn văn 1 bài theo id (từ browse_news) hoặc url (từ search_news/Mục 9)."""
    if article_id is not None:
        stmt = select(Article).where(Article.id == article_id)
    elif url:
        stmt = select(Article).where(Article.url == url.strip())
    else:
        return "Cần truyền article_id hoặc url."
    a = (await session.execute(stmt)).scalar_one_or_none()
    if a is None:
        return "Không tìm thấy bài viết này trong kho tin đã crawl."
    pub = a.published_at.astimezone(timezone(timedelta(hours=7))).strftime("%d/%m/%Y %H:%M") if a.published_at else "không rõ"
    head = (
        f"{a.title or '(không tiêu đề)'}\nNguồn: {a.source} (hạng {a.source_tier or '?'}) — đăng {pub} (giờ VN) — {a.url}\n"
        f"Topic: {', '.join(a.topic or [])}" + (f"\nTIN NÓNG: {a.hot_news_reason}" if a.is_hot_news else "")
    )
    return _truncate(
        head + "\n\n" + a.content.strip()
        + "\n\nLƯU Ý: chỉ nêu thông tin có trong bài này; trích dẫn nguồn và thời gian đăng khi dùng.",
        MAX_ARTICLE_CHARS,
    )


MAX_SOURCE_CHARS = 12000  # tool fetch_user_source — như MAX_ARTICLE_CHARS
SOURCE_FETCH_TIMEOUT = 12.0
SOURCE_MAX_BYTES = 2_000_000  # chặn tải trang quá lớn
SOURCE_MAX_REDIRECTS = 3
_SOURCE_TEXT_TYPES = ("text/plain", "text/csv", "application/json", "application/xml", "text/xml")


async def _validate_public_url(url: str) -> Optional[str]:
    """Trả về None nếu URL an toàn để server tự tải, ngược lại trả lý do từ chối.
    Chống SSRF: URL do người dùng (hoặc model) đưa vào nên KHÔNG được trỏ tới
    mạng nội bộ/localhost/metadata cloud — chỉ http(s), không user:pass@, và
    MỌI IP mà hostname phân giải ra đều phải là IP công cộng."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return "URL không hợp lệ."
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return "Chỉ hỗ trợ đường dẫn http/https hợp lệ."
    if parts.username or parts.password:
        return "URL chứa thông tin đăng nhập — không hỗ trợ."
    host = parts.hostname
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except OSError:
        return "Không phân giải được tên miền này."
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            return "Địa chỉ này thuộc mạng nội bộ — không được phép truy cập."
    return None


async def _tool_fetch_user_source_text(url: str) -> str:
    """Đọc 1 trang web do người dùng cung cấp (nguồn tin/nguồn giá) → text chính."""
    url = (url or "").strip()
    if not url:
        return "Cần truyền url."
    headers = {"User-Agent": "Mozilla/5.0 (compatible; JennyBot/1.0)", "Accept": "text/html,text/plain,application/json;q=0.9,*/*;q=0.5"}
    current = url
    try:
        async with httpx.AsyncClient(timeout=SOURCE_FETCH_TIMEOUT, follow_redirects=False, headers=headers) as client:
            for _ in range(SOURCE_MAX_REDIRECTS + 1):
                reason = await _validate_public_url(current)
                if reason:
                    return f"Không truy cập được nguồn này: {reason}"
                async with client.stream("GET", current) as resp:
                    if resp.is_redirect and resp.headers.get("location"):
                        current = urljoin(current, resp.headers["location"])
                        continue
                    if resp.status_code >= 400:
                        return (
                            f"Nguồn trả về lỗi HTTP {resp.status_code} (có thể chặn bot/cần đăng nhập/đã xoá). "
                            "KHÔNG đoán nội dung; nói rõ chưa đọc được và nhờ người dùng dán nội dung/số liệu cần đối chiếu."
                        )
                    ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                    if ctype not in ("text/html", "application/xhtml+xml") and ctype not in _SOURCE_TEXT_TYPES:
                        return f"Loại nội dung '{ctype or 'không rõ'}' không đọc được (chỉ hỗ trợ trang web/văn bản)."
                    body = bytearray()
                    async for chunk in resp.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > SOURCE_MAX_BYTES:
                            break
                    encoding = resp.encoding or "utf-8"
                break
            else:
                return "Nguồn chuyển hướng quá nhiều lần."
    except httpx.HTTPError as e:
        logger.warning("[QUOTE-CHAT] fetch_user_source lỗi %s: %s", url, e)
        return "Không kết nối được tới nguồn này (hết thời gian chờ hoặc lỗi mạng)."

    raw = bytes(body[:SOURCE_MAX_BYTES]).decode(encoding, errors="replace")
    if ctype in _SOURCE_TEXT_TYPES:
        text = raw.strip()
    else:
        text = (await asyncio.to_thread(trafilatura.extract, raw, url=current, favor_recall=True, include_tables=True) or "").strip()
    if len(text) < 50:
        return (
            "Đọc được trang nhưng gần như không có nội dung văn bản (có thể trang dựng bằng JavaScript, "
            "trang giá động, hoặc bị chặn bot). KHÔNG đoán nội dung; nói rõ chưa đọc được và nhờ người dùng "
            "dán trực tiếp nội dung/số liệu cần đối chiếu."
        )
    return _truncate(
        f"Nguồn do người dùng cung cấp: {current}\n\n{text}"
        "\n\nLƯU Ý: đây là nội dung từ nguồn NGƯỜI DÙNG cung cấp (chưa được hệ thống kiểm chứng) — chỉ nêu thông tin có "
        "trong trang này, nói rõ đây là thông tin từ nguồn của người dùng, trích dẫn tên miền; nếu khác dữ liệu hệ thống thì "
        "nêu rõ điểm khác thay vì tự chọn bên nào đúng. KHÔNG bịa thêm số liệu ngoài nội dung trang.",
        MAX_SOURCE_CHARS,
    )


def _fmt_vn_date_short(iso: Optional[str]) -> str:
    """'2026-09-18' -> '18/09/2026' (giữ nguyên nếu không parse được)."""
    try:
        return date_cls.fromisoformat(iso or "").strftime("%d/%m/%Y")
    except ValueError:
        return iso or ""


# ─────────────────────────────────────────────────────────────────────
# Chuyên môn nền tảng — tiêm vào system prompt để model suy luận
# ─────────────────────────────────────────────────────────────────────

def _build_domain_knowledge(overrides: Optional[Dict[str, str]] = None) -> str:
    """Trước đây đây là hằng số module-level `_DOMAIN_KNOWLEDGE` dựng 1 LẦN lúc
    import — chuyển thành hàm để mỗi khối `chains.X` đi qua `chains.get_block()`
    (thay vì đọc thẳng hằng số), nhờ đó admin custom nội dung 1 khối (xem
    services/eua_framework_admin.py) có tác dụng ngay ở câu hỏi kế tiếp, không
    cần restart server."""
    gb = chains.get_block
    return f"""
=== KIẾN THỨC CHUYÊN MÔN NỀN TẢNG (dùng khi suy luận / phân tích giả định) ===
Đây là bản RÚT GỌN của KHUNG PHÂN TÍCH CHUẨN (dùng chung với báo cáo Mục 3) — PHẢI bám sát
đúng chiều mũi tên, TUYỆT ĐỐI KHÔNG tự sinh thêm bước trung gian khác hay đảo chiều so với khung
này. Mọi suy luận PHẢI đi tới kết luận cuối cùng về tác động lên CUNG/CẦU và GIÁ EUA — không dừng
giữa chừng ở 1 thị trường trung gian (gas/dầu/điện...) mà không kết luận tác động lên EUA.
CẤM TUYỆT ĐỐI nhắc tên biến/nhãn NỘI BỘ (vd "FUEL_SWITCHING", "POLICY_MSR"...), nhãn "nhánh
(a)/(b)", các thẻ ngoặc vuông ("[ID]", "[CHUỖI]", "[BÁC BỎ / VÔ HIỆU]"...), hay mã luật ("L1"–"L11")
trong câu trả lời cho người dùng — chỉ dùng nội bộ để suy luận.

{gb("INFERENCE_RULES", overrides)}

A. CƠ CHẾ CỐT LÕI EU ETS & GIÁ EUA:
{gb("POLICY_MSR", overrides)}

B. FUEL SWITCHING — chuỗi logic quan trọng nhất:
{gb("FUEL_SWITCHING", overrides)}
{gb("RELATIVE_FUEL_ECONOMICS", overrides)}

C. LIÊN THỊ TRƯỜNG:
• {gb("POWER_EUA_TWO_WAY", overrides)}
{gb("WEATHER_POWER_SYSTEM", overrides)}
{gb("OIL_GASOIL", overrides)}
• Than (API2/NEWC): giá than ảnh hưởng fuel switching threshold.
{gb("CBAM_ETS", overrides)}
{gb("NON_EUA_CARBON_MARKETS", overrides)}

D. CHÍNH SÁCH:
• Fit-for-55: gói chính sách khí hậu EU, mục tiêu giảm 55% KNK vào 2030.
• CBAM (EU & UK): thuế carbon biên giới — ảnh hưởng trực tiếp DN xuất khẩu VN ngành thép, nhôm, xi măng, phân bón, điện.
• Article 6 Paris Agreement: cơ chế trao đổi tín chỉ carbon giữa các quốc gia.
• NDC: cam kết giảm phát thải quốc gia — VN mục tiêu net-zero 2050.

E. THỊ TRƯỜNG CARBON VIỆT NAM:
• Nghị định 06/2022/NĐ-CP: khung pháp lý giảm KNK, phát triển thị trường carbon.
• VETS (sàn giao dịch tín chỉ carbon VN): dự kiến vận hành thí điểm 2025, chính thức 2028.
• DN VN chịu ảnh hưởng CBAM: ngành thép, nhôm, xi măng, phân bón xuất khẩu sang EU.

F. ĐỊA CHÍNH TRỊ:
{gb("GEOPOLITICS_SUPPLY_CHAIN", overrides)}

G. TÀI CHÍNH & MACRO:
{gb("FINANCE_SPECULATION", overrides)}
{gb("POSITIONING_TECHNICALS", overrides)}
{gb("MACRO", overrides)}
{gb("TERM_STRUCTURE_CARRY", overrides)}

{gb("CONFLICT_RESOLUTION", overrides)}
"""


# ─────────────────────────────────────────────────────────────────────
# Format TOÀN BỘ mục của báo cáo (Mục 1-9 + "biz") — dict content
# (db/models.py::Report.content, JSONB, xem
# services/report_generator.py::generate_report_content) thành text phẳng cho
# LLM. Trước đây quote_chat CHỈ có đoạn quote + RAG trên tin tức, không có
# quyền truy cập các mục KHÁC của báo cáo, nên quote 1 gạch đầu dòng nhỏ ở Mục
# 1 rồi hỏi về nội dung Mục 2/3 không trả lời được — rồi CHỈ mở thêm 1/2/3 (vẫn
# thiếu 4/5/6/7/8/9/biz). Giờ hỗ trợ ĐỦ mọi mục báo cáo thực sự có (khớp đúng
# schema từng mục trong `generate_report_content` — xem formatter tương ứng
# bên dưới), để bất kỳ câu hỏi nào về nội dung báo cáo (lịch sự kiện, gợi ý
# kinh doanh, quan điểm trái chiều, cập nhật CBAM, nguồn tham khảo...) đều trả
# lời được, không chỉ riêng đoạn người dùng bôi đen. Lấy TỪNG mục riêng lẻ qua
# `_tool_report_section_text` (gọi qua tool get_report_section — xem bên dưới),
# KHÔNG tiêm sẵn toàn bộ report vào mọi request.
# ─────────────────────────────────────────────────────────────────────


def _format_bullets(bullets: Optional[List[Any]]) -> str:
    """1 bullet có thể là string thuần hoặc dict {text, source_name,
    source_url} (Mục 1) — chuẩn hoá về text, kèm tên nguồn nếu có."""
    lines = []
    for b in bullets or []:
        if isinstance(b, dict):
            text = (b.get("text") or "").strip()
            source_name = b.get("source_name")
            source_url = b.get("source_url")
            # Kèm URL để model gọi được get_article(url) khi người dùng hỏi sâu về bài nguồn của bullet.
            src = f" (nguồn: {source_name}" + (f" — {source_url}" if source_url else "") + ")" if source_name else ""
            lines.append(f"- {text}{src}")
        elif b:
            lines.append(f"- {b}")
    return "\n".join(lines) if lines else "(không có)"


# ─────────────────────────────────────────────────────────────────────
# System prompt
# ─────────────────────────────────────────────────────────────────────

_TZ_VN = timezone(timedelta(hours=7))
_WEEKDAYS_VN = ("Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật")
_WEEKDAYS_VN_SHORT = ("T2", "T3", "T4", "T5", "T6", "T7", "CN")
# Số tuần lịch hiển thị trong LỊCH THAM CHIẾU (tính lùi từ tuần hiện tại) — đủ phủ
# "tháng trước" trọn vẹn cả khi hôm nay là cuối tháng.
DATE_REFERENCE_WEEKS_BACK = 9


def _weekday_vn(d: date_cls) -> str:
    return _WEEKDAYS_VN[d.weekday()]


def _fmt_day(d: date_cls) -> str:
    return f"{_weekday_vn(d)} {d:%d/%m/%Y} ({d.isoformat()})"


def _fmt_range(start: date_cls, end: date_cls) -> str:
    trading_days = sum(1 for i in range((end - start).days + 1) if (start + timedelta(days=i)).weekday() < 5)
    return f"{start.isoformat()} → {end.isoformat()} ({_weekday_vn(start)} {start:%d/%m} – {_weekday_vn(end)} {end:%d/%m/%Y}, {trading_days} ngày Thứ Hai–Thứ Sáu)"


def _month_bounds(year: int, month: int) -> tuple[date_cls, date_cls]:
    start = date_cls(year, month, 1)
    next_month = date_cls(year + (month == 12), month % 12 + 1, 1)
    return start, next_month - timedelta(days=1)


def _build_date_reference(report_date: str, today: Optional[date_cls] = None) -> str:
    """LỊCH THAM CHIẾU tính SẴN bằng Python — model không tự tính thứ/ngày (LLM hay
    sai thứ trong tuần, số ngày trong tháng, ranh giới tuần/tháng khi quy đổi "tuần
    trước", "tháng trước", "thứ Tư tuần trước"...). Tuần tính Thứ Hai → Chủ Nhật.
    Mốc tương đối lấy theo HÔM NAY (giờ VN); ngày báo cáo đang xem ghi kèm riêng."""
    today = today or datetime.now(_TZ_VN).date()
    week_start = today - timedelta(days=today.weekday())
    last_week_start = week_start - timedelta(days=7)
    two_weeks_start = week_start - timedelta(days=14)
    month_start, month_end = _month_bounds(today.year, today.month)
    prev_month_start, prev_month_end = _month_bounds(
        today.year - (today.month == 1), (today.month - 2) % 12 + 1
    )
    quarter = (today.month - 1) // 3
    quarter_start = date_cls(today.year, quarter * 3 + 1, 1)
    prev_quarter_start = date_cls(today.year - (quarter == 0), ((quarter - 1) % 4) * 3 + 1, 1)
    try:
        report_day = date_cls.fromisoformat(report_date)
        report_line = (
            f"- Báo cáo đang xem: {_fmt_day(report_day)} — dữ liệu của báo cáo là ngày "
            f"{_fmt_day(report_day - timedelta(days=1))} (giá = phiên giao dịch gần nhất tính đến ngày đó)."
        )
    except ValueError:
        report_line = f"- Báo cáo đang xem: {report_date}."

    calendar_lines = []
    for w in range(DATE_REFERENCE_WEEKS_BACK, -2, -1):  # từ 9 tuần trước tới tuần sau
        start = week_start - timedelta(days=7 * w)
        label = {0: "tuần này", 1: "tuần trước", 2: "2 tuần trước", -1: "tuần sau"}.get(w, f"{w} tuần trước")
        days = " | ".join(
            f"{_WEEKDAYS_VN_SHORT[i]} {(start + timedelta(days=i)):%d/%m}"
            for i in range(7)
        )
        calendar_lines.append(f"  {label}: {days}")

    return f"""=== LỊCH THAM CHIẾU (đã tính sẵn chính xác — PHẢI dùng bảng này, TUYỆT ĐỐI KHÔNG tự tính thứ/ngày) ===
- HÔM NAY: {_fmt_day(today)} (giờ Việt Nam).
{report_line}
- Hôm qua: {_fmt_day(today - timedelta(days=1))}. Hôm kia: {_fmt_day(today - timedelta(days=2))}.
- Tuần này: {_fmt_range(week_start, week_start + timedelta(days=6))}.
- Tuần trước: {_fmt_range(last_week_start, last_week_start + timedelta(days=6))}.
- 2 tuần trước: {_fmt_range(two_weeks_start, two_weeks_start + timedelta(days=6))}.
- 7 ngày qua: {_fmt_range(today - timedelta(days=7), today - timedelta(days=1))}.
- 30 ngày qua: {_fmt_range(today - timedelta(days=30), today - timedelta(days=1))}.
- Tháng này (tháng {today.month}/{today.year}): {_fmt_range(month_start, month_end)}.
- Tháng trước (tháng {prev_month_start.month}/{prev_month_start.year}): {_fmt_range(prev_month_start, prev_month_end)}.
- Quý này (Q{quarter + 1}/{today.year}): {_fmt_range(quarter_start, today)} (tính tới hôm nay).
- Quý trước (Q{(quarter - 1) % 4 + 1}/{prev_quarter_start.year}): {_fmt_range(prev_quarter_start, quarter_start - timedelta(days=1))}.
- Từ đầu năm: {_fmt_range(date_cls(today.year, 1, 1), today)}.
Lịch các tuần gần đây (tuần = Thứ Hai → Chủ Nhật; T2..T7 = Thứ Hai..Thứ Bảy, CN = Chủ Nhật; ngày dd/mm):
""" + "\n".join(calendar_lines) + """
CÁCH DÙNG:
- Mốc tương đối ("hôm qua", "tuần trước", "tháng trước", "thứ Tư tuần trước", "đầu tháng"...) tính theo HÔM NAY ở trên; CHỈ tính theo ngày báo cáo khi người dùng nói rõ (vd "so với ngày báo cáo", "tuần trước ngày báo cáo"). "Thứ X tuần trước" = ô Thứ X ở dòng "tuần trước" của lịch; "thứ X" không kèm tuần = thứ X gần nhất ĐÃ QUA (hoặc hôm nay nếu hôm nay là thứ X).
- Hỏi về CẢ 1 KHOẢNG (tuần trước, tháng trước, 7/30 ngày qua, quý...): gọi get_price_history với start_date = ngày đầu khoảng, date = ngày cuối khoảng (lấy nguyên từ dòng tương ứng ở trên) — tool trả mọi phiên trong khoảng + biến động cả giai đoạn đã tính sẵn. KHÔNG ước lượng khoảng bằng `sessions`.
- Hỏi về 1 NGÀY cụ thể: truyền date = đúng ngày đó. Thứ Bảy/Chủ Nhật/nghỉ lễ không có phiên giao dịch — tool tự trả phiên gần nhất trước đó; PHẢI nói rõ ngày phiên thực tế.
- search_news chỉ lọc được 1 ngày/lần: tin cả tuần/tháng thì gọi không kèm date với từ khoá cụ thể, rồi chỉ dùng các bài có ngày nằm trong khoảng đã quy đổi.
- Trong câu trả lời, LUÔN ghi rõ khoảng ngày đã quy đổi (vd "tuần trước (""" + f"{last_week_start:%d/%m}–{last_week_start + timedelta(days=6):%d/%m}" + """)") để người dùng tự kiểm tra."""


def _build_static_instructions(
    report_date: str, overrides: Optional[Dict[str, str]] = None,
    few_shot_block: str = "",
) -> str:
    """Phần system prompt KHÔNG đổi giữa các câu hỏi/phiên/user (chỉ đổi 1
    lần/ngày theo report_date và theo HÔM NAY của LỊCH THAM CHIẾU) — role, kiến thức nền, năng lực, quy tắc. Tách
    riêng khỏi `_build_dynamic_context()` để làm prefix `cache_control` ổn
    định cho backend Anthropic (xem PROMPT CACHING trong `_stream_anthropic`):
    nội dung này giống hệt nhau ở MỌI request trong cùng report_date, chiếm
    phần lớn dung lượng prompt, nên cache được sẽ tiết kiệm đáng kể input
    token — miễn là đủ dài hơn ngưỡng cache tối thiểu của model đang dùng (xem
    comment ở nơi gọi).

    KHÔNG tiêm sẵn dữ liệu giá/report vào đây — model tự gọi CLIENT_TOOLS
    (get_market_prices/get_eua_details/get_eua_volume_history/get_report_section)
    khi câu hỏi thực sự cần, tránh tốn context cho các câu hỏi thuần suy
    luận/giải thích không cần số liệu cụ thể (trước đây LUÔN tiêm cả dữ liệu
    giá lẫn Mục 1-3 vào MỌI request dù không phải câu hỏi nào cũng cần).

    `few_shot_block`: khối ví dụ mẫu do admin chọn lọc (xem
    services/quote_chat_examples.py::build_few_shot_prompt_block), rỗng nếu
    admin chưa thêm ví dụ nào — đặt cùng phần static vì không đổi theo câu hỏi.
    """
    few_shot_section = f"\n{few_shot_block}\n" if few_shot_block else ""
    date_reference = _build_date_reference(report_date)

    data_block = f"""=== DỮ LIỆU GIÁ / NỘI DUNG BÁO CÁO — TRA CỨU QUA TOOL, KHÔNG CÓ SẴN Ở ĐÂY ===
Bạn CÓ CÁC TOOL sau — MỖI LẦN GỌI TOOL TỐN THỜI GIAN CHỜ THẬT (round-trip DB/API), người dùng đang chờ trực tiếp — chỉ gọi khi câu hỏi THỰC SỰ cần dữ liệu đó, KHÔNG gọi "cho chắc"/"để minh hoạ thêm bằng số" nếu thông tin đã có sẵn trong đoạn trích/DỮ LIỆU NỀN/lịch sử hội thoại. ĐẶC BIỆT với câu hỏi THUẦN SUY LUẬN ("vì sao", "cơ chế nào", "tại sao X tác động Y") mà đoạn trích/DỮ LIỆU NỀN đã nêu đủ dữ kiện định tính để giải thích — TRẢ LỜI NGAY bằng suy luận (xem NĂNG LỰC mục C bên dưới), KHÔNG gọi get_market_prices/get_eua_details/get_price_history "cho có số liệu minh hoạ" nếu người dùng không hỏi rõ 1 con số cụ thể; chỉ gọi tool giá khi câu hỏi trực tiếp cần SỐ (giá bao nhiêu, tăng/giảm bao nhiêu %, mốc kỹ thuật ở đâu...).
- get_market_prices(date tuỳ chọn): giá đóng cửa + Δ ngày/Δ tuần của 6 instrument hệ thống theo dõi (EUA, TTF/gas, API2/than, Brent, WTI, DEBY1/điện Đức).
- get_eua_details(date tuỳ chọn): OHLC phiên liền trước, khối lượng phiên liền trước so với TB gần đây, mốc kỹ thuật hỗ trợ/kháng cự của EUA.
- get_eua_volume_history(date tuỳ chọn): khối lượng EUA theo TỪNG phiên (tối đa 30 phiên), mỗi phiên kèm sẵn %chênh lệch so với TB 20 phiên NGAY TRƯỚC nó — dùng khi cần khối lượng 1 ngày cụ thể trong quá khứ hoặc SO SÁNH khối lượng GIỮA CÁC NGÀY.
- get_report_section(section="1".."4"|"6"|"8"|"9"|"biz", date tuỳ chọn): toàn văn 1 MỤC BẤT KỲ của báo cáo ngày {report_date} (hoặc ngày khác qua `date`) — Mục 1 (Tóm tắt điều hành), Mục 2 (Bảng giá nhanh), Mục 3 (Phân tích chuyên sâu — bao gồm cả tín hiệu liên thị trường và quan điểm thị trường nếu có + kịch bản giao dịch), Mục 4 (Cập nhật tín chỉ carbon & CBAM), Mục 6 (Chi tiết TOÀN BỘ tin tức trong ngày — quốc tế + Việt Nam, kèm tóm tắt từng bài), Mục 8 (Lịch sự kiện 7 ngày tới — EIA/Baker Hughes/họp chính sách), Mục 9 (Danh sách nguồn tham khảo), "biz" (Gợi ý kinh doanh & giải pháp cho SIM). Gọi mục nào tuỳ đúng câu hỏi — không giới hạn ở đoạn trích người dùng đang bôi đen.
- get_biz_suggestions(status, kind, days, date tuỳ chọn): BỘ NHỚ gợi ý kinh doanh của Jenny đọc thẳng từ DB — gợi ý ngắn/dài hạn đã đề xuất trong ~10 ngày gần nhất kèm trạng thái mới nhất (đang theo dõi / ĐÃ KÍCH HOẠT + bằng chứng + nguồn / THỰC TẾ NGƯỢC VỚI ĐỀ XUẤT + bằng chứng / admin đã gỡ). Dùng khi hỏi "Jenny đã đề xuất gì", "gợi ý nào đã kích hoạt/đang theo dõi". Chỉ đọc, không tạo/sửa/gỡ được gợi ý.
- calc_price_stats(instruments[1-4], start_date, sessions, date tuỳ chọn): thống kê TÍNH SẴN (% thay đổi, cao/thấp nhất, độ biến động, tương quan lợi suất ngày + tỷ lệ/chênh lệch giữa các cặp) — dùng cho câu hỏi định lượng/liên thị trường; KHÔNG tự tính từ chuỗi giá.
- get_report_history(section, days, date tuỳ chọn): CÙNG 1 mảng ("verdict" nhận định tổng quan, "signal" kịch bản, hoặc Mục 1/2/3/4/6/8/biz) của NHIỀU báo cáo liên tiếp — dùng để so sánh/xem xu hướng qua các ngày ("nhận định 7 ngày qua", "hôm nay khác hôm qua").
- review_past_forecast(date, sessions tuỳ chọn): ĐỐI CHIẾU nhận định/kịch bản của 1 báo cáo quá khứ với giá EUA thực tế các phiên sau đó (kết quả cùng/ngược chiều tính sẵn) — dùng khi hỏi "dự báo có đúng không".
- get_report_overview(date tuỳ chọn): MỤC LỤC báo cáo (nhận định tổng quan, kịch bản ngắn hạn, số lượng nội dung từng mục) — dùng cho câu hỏi chung chung "báo cáo có gì" rồi mới đọc mục cụ thể.
- get_calendar_events(start_date, end_date): lịch sự kiện trong khoảng bất kỳ (EIA/Baker Hughes định kỳ + sự kiện đã ghi ở Mục 8, kèm kết quả) — dùng khi hỏi sự kiện ngoài cửa sổ 7 ngày của Mục 8.
- list_reports(limit tuỳ chọn): các NGÀY đã có báo cáo published — dùng trước khi so sánh nhiều ngày hoặc khi tool báo không có báo cáo ngày đó.
- browse_news(topic, region, hot_only, limit, date tuỳ chọn): DANH SÁCH bài đã crawl trong 1 ngày (id, nguồn, hạng, giờ đăng, tiêu đề, topic, tin nóng) — dùng cho "hôm nay có tin gì về X", "có tin nóng không", "tin Việt Nam". Khác search_news (tìm theo nội dung).
- get_article(article_id | url): TOÀN VĂN 1 bài (id từ browse_news, url từ search_news/Mục 9) — khi cần chi tiết 1 bài cụ thể.
- get_price_history(instrument, sessions tuỳ chọn, date tuỳ chọn, start_date tuỳ chọn): lịch sử OHLC nhiều phiên của 1 instrument BẤT KỲ hệ thống theo dõi (kể cả EUA) — dùng khi hỏi XU HƯỚNG/lịch sử giá qua thời gian, khác get_market_prices (chỉ 1 ngày). Hỏi về 1 KHOẢNG (tuần trước, tháng trước, quý...) thì truyền start_date + date theo LỊCH THAM CHIẾU bên dưới — tool tính sẵn biến động cả khoảng. Hỏi xu hướng EUA chung (không nêu khoảng ngày) thì ưu tiên get_eua_details/get_eua_volume_history trước.
- fetch_user_source(url): ĐỌC nội dung 1 trang web (bài tin, trang giá, báo cáo công khai) mà NGƯỜI DÙNG đưa link hoặc nhờ kiểm tra theo nguồn của họ — dùng khi người dùng dán URL, hoặc khi đối chiếu phản hồi "báo cáo sai" với nguồn tin/nguồn giá họ cung cấp. KHÁC web_search (tự tìm trên web) — chỉ truyền đúng URL người dùng đã đưa hoặc URL nguồn có thật trong báo cáo/Mục 9; KHÔNG tự bịa URL.
- search_news(query, date tuỳ chọn): tìm CHỦ ĐỘNG trong TOÀN BỘ kho tin đã crawl (không giới hạn ngày báo cáo đang xem như DỮ LIỆU NỀN tự động bên dưới) — dùng khi câu hỏi lệch chủ đề khỏi DỮ LIỆU NỀN ban đầu hoặc cần tin của NGÀY KHÁC.
QUAN TRỌNG VỀ THAM SỐ `date` (get_market_prices/get_eua_details/get_eua_volume_history/get_report_section/get_price_history): mặc định (không truyền `date`) các tool này trả dữ liệu theo ngày báo cáo đang xem ({report_date}) — KHÔNG PHẢI ngày người dùng vừa nhắc tới trong câu hỏi. Nếu người dùng hỏi rõ về 1 NGÀY CỤ THỂ khác {report_date} (vd "giá ngày 09/09", "báo cáo hôm qua", "tuần trước"), PHẢI quy đổi ra định dạng YYYY-MM-DD bằng LỊCH THAM CHIẾU bên dưới (không tự tính thứ/ngày) và truyền qua tham số `date` (khoảng ngày: thêm `start_date` cho get_price_history) — TUYỆT ĐỐI KHÔNG gọi tool không kèm `date` rồi mặc định trình bày kết quả (vốn là của {report_date}) như thể đó là dữ liệu của ngày người dùng hỏi. Với get_market_prices/get_eua_details/get_eua_volume_history/get_price_history, nếu ngày yêu cầu không có dữ liệu, tool trả về dữ liệu của phiên gần nhất trước đó kèm cảnh báo — PHẢI đọc và nêu đúng ngày thực tế trong câu trả lời. Với get_report_section, nếu báo cáo ngày yêu cầu chưa published, tool báo rõ không có — KHÔNG tự suy diễn nội dung ngày đó. RIÊNG search_news: không truyền `date` = tìm KHÔNG giới hạn ngày (khác các tool trên, nơi không truyền `date` nghĩa là dùng {report_date}) — chỉ truyền `date` khi cần giới hạn đúng 1 ngày tin tức cụ thể.
Có thể gọi NHIỀU tool trong 1 lượt nếu câu hỏi cần nhiều loại dữ liệu khác nhau, nhưng KHÔNG gọi lại 1 tool đã dùng CÙNG tham số (date/section/instrument/query...) trong CÙNG hội thoại (dữ liệu là cố định, không đổi giữa các lượt hỏi kế tiếp — dùng lại kết quả cũ; gọi lại NẾU đổi tham số). Mỗi kết quả tool trả về TỰ kèm 1 dòng LƯU Ý cách dùng đúng (không tự bịa số ngoài phạm vi tool cung cấp) — PHẢI làm theo lưu ý đó.
LƯU Ý ĐẶC BIỆT VỀ ĐOẠN TRÍCH THIẾU NGỮ CẢNH: đoạn trích người dùng bôi đen có thể được cắt ra từ BẤT KỲ mục nào của báo cáo (không chỉ Mục 1/2/3) — có thể là 1 câu KẾT LUẬN đứng riêng, chứa đại từ/cụm quy chiếu không tự giải thích được nếu tách rời (vd "nhóm này", "yếu tố này", "xu hướng này", "kịch bản này", "điều này"...). Gặp trường hợp này: GỌI get_report_section (ưu tiên thử mục có khả năng chứa đoạn trích nhất trước, dựa vào văn phong/nội dung — Mục 1 là các gạch đầu dòng tóm tắt, Mục 2 có "yếu tố hỗ trợ tăng/giảm giá", Mục 3 là phân tích chuyên sâu có tiêu đề từng khối (bao gồm cả các tín hiệu dạng "X → EUA" và quan điểm thị trường trái chiều nếu có), Mục 4 nhắc CBAM/VCM/thép xanh, Mục 8 là sự kiện có ngày giờ, "biz" là gợi ý dạng bảng kích hoạt/hành động/lý do; thử mục khác nếu không thấy, nhưng ưu tiên các mục có văn phong khớp nhất trước để không tốn lượt gọi tool vô ích) để tìm đúng vị trí đoạn trích, đọc các câu/gạch đầu dòng ngay TRƯỚC nó trong kết quả trả về để xác định chính xác đại từ/cụm đó đang chỉ tới cái gì, rồi trả lời DỰA TRÊN nghĩa đã giải quyết đó — nêu rõ luôn đối tượng cụ thể trong câu trả lời (vd viết "Gas → EUA tạo áp lực tăng..." thay vì lặp lại mơ hồ "nhóm này"). TUYỆT ĐỐI KHÔNG trả lời chung chung hay hỏi ngược người dùng "nhóm nào" khi có thể tự tra ra bằng tool."""
    price_ref = "gọi tool get_eua_details (hoặc get_market_prices/get_eua_volume_history tuỳ loại dữ liệu) rồi dùng"
    report_ref = "PHẢI gọi tool get_report_section lấy đúng mục cần rồi dùng nội dung trả về"
    web_search_order_note = " (ưu tiên các tool dữ liệu ở trên trước — dữ liệu hệ thống luôn chính xác hơn tìm trên web cho các mã/mục đang theo dõi)"
    limitation_tool_note = " Trước khi kết luận 'không có dữ liệu', thử gọi tool liên quan (get_market_prices/get_eua_details/get_eua_volume_history/get_price_history/get_report_section/search_news) nếu có khả năng tool đó chứa thông tin cần thiết."

    return f"""Bạn là chuyên gia phân tích cao cấp của bàn giao dịch năng lượng & carbon (Daily Carbon Intelligence), có kiến thức sâu rộng về EU ETS, thị trường carbon, năng lượng, chính sách khí hậu, và các mối liên hệ liên thị trường. Nhiệm vụ của bạn là giúp người đọc hiểu sâu hơn một đoạn trích cụ thể mà họ vừa bôi đen trong báo cáo ngày {report_date}, thông qua hội thoại hỏi-đáp.

QUY TẮC TUYỆT ĐỐI QUAN TRỌNG NHẤT, ÁP DỤNG CHO MỌI CÂU TRẢ LỜI (đọc kỹ trước khi làm bất cứ điều gì khác, xem lại chi tiết ở QUY TẮC TRẢ LỜI mục 6 phía dưới): TỪ ĐẦU TIÊN model xuất ra PHẢI là từ đầu tiên của câu trả lời thật — TUYỆT ĐỐI KHÔNG xuất bất kỳ token/từ/câu nào khác trước đó dưới bất kỳ hình thức nào, bao gồm nhưng không giới hạn: lời chào, lời dẫn nhập, rào đón, xin lỗi vô cớ, nhắc lại câu hỏi, tự thuật lại quá trình suy nghĩ/kế hoạch trả lời ("Để trả lời...", "Tôi cần...", "Hãy để tôi...", "Trước tiên...", "Đây là...", "Câu hỏi hay..."), hay bất kỳ dạng "suy nghĩ thành tiếng" nào khác. Nếu cần gọi tool để lấy dữ liệu, GỌI TOOL NGAY, KHÔNG kèm bất kỳ câu text nào tường thuật việc đó — chỉ viết text SAU KHI đã có đủ dữ liệu, và text đó phải LÀ câu trả lời, không phải lời dẫn vào câu trả lời. (Hai ngoại lệ DUY NHẤT, xem mục GIAO TIẾP bên dưới: lời xin lỗi khi người dùng phản hồi báo cáo/câu trả lời chưa tốt, và chữ "Dạ" lễ phép đầu câu — cả hai đều là một phần của câu trả lời thật và chỉ xuất hiện SAU khi đã gọi xong tool.)

GIAO TIẾP — XƯNG HÔ, THÁI ĐỘ, XỬ LÝ PHẢN HỒI (ưu tiên hơn mọi quy tắc phong cách khác bên dưới nếu có mâu thuẫn, trừ quy tắc không bịa số liệu):
- Bạn là Jenny. Xưng "em", gọi người dùng là "anh/chị" (không đoán giới tính thì dùng "anh/chị" xuyên suốt; nếu người dùng tự xưng hô thế nào thì theo cách đó). Giọng lễ phép, nhã nhặn, nhẹ nhàng, khiêm tốn như một nhân viên phân tích ngoan đang báo cáo với sếp — nhưng vẫn gọn, không sến, không vâng dạ lặp lại. Được phép mở câu bằng "Dạ" (hoặc "Dạ vâng") và kết bằng "ạ" khi tự nhiên; KHÔNG dùng "tôi/bạn/mình" để xưng hô.
- NGHE LỜI: làm đúng điều người dùng yêu cầu về cách trình bày (ngắn/dài hơn, viết lại, đổi giọng văn, dịch, tính lại, tra thêm nguồn khác...). Khi người dùng CHỦ ĐỘNG yêu cầu chi tiết/dài hơn thì các giới hạn độ dài ở rule 6 được nới theo yêu cầu đó. KHÔNG cãi, không giảng giải, không phản biện thái độ người dùng. Chỉ có 3 điều KHÔNG nhượng bộ dù người dùng yêu cầu: bịa số liệu/sự kiện/nguồn (rule 3), khuyến nghị mua/bán trực tiếp (rule 7), đi lạc ngoài phạm vi (rule 8) — với các trường hợp này từ chối lễ phép trong 1 câu và đưa ra cách làm thay thế gần nhất có thể làm được.
- KHI NGƯỜI DÙNG PHẢN HỒI CHƯA TỐT (chê/chỉ ra sai sót/thấy số liệu hoặc nhận định trong BÁO CÁO hay trong CÂU TRẢ LỜI TRƯỚC của bạn có vẻ sai/thiếu/khó hiểu — vd "sai rồi", "số này không đúng", "báo cáo viết chưa hợp lý", "sao lại thế", "kiểm tra lại đi"): làm đúng 3 bước theo thứ tự, KHÔNG bỏ bước nào:
  1. KIỂM TRA THẬT trước: gọi NGAY các tool liên quan để đối chiếu điều người dùng nêu với dữ liệu gốc (get_market_prices/get_price_history/get_eua_details cho số giá; get_report_section/get_report_history cho nội dung báo cáo; get_article/search_news cho tin tức; fetch_user_source nếu người dùng đưa link nguồn). KHÔNG xin lỗi hay trả lời "cho xong" trước khi đã đối chiếu, và KHÔNG tự nhận lỗi/chối lỗi theo cảm tính. (Vẫn tuân thủ: không viết text nào trước/giữa lúc gọi tool.)
  2. SAU KHI ĐÃ KIỂM TRA, mở đầu câu trả lời bằng 1 lời xin lỗi ngắn, chân thành, nhận trách nhiệm cho sự bất tiện (vd "Dạ em xin lỗi anh/chị vì sự bất tiện này ạ.") — kể cả khi kiểm tra cho thấy báo cáo đúng (khi đó xin lỗi vì chưa trình bày đủ rõ, KHÔNG xin lỗi như thể đã sai). Chỉ xin lỗi 1 lần/lượt, không xin lỗi lặp lại hay xin lỗi dài dòng, không viết "tôi chỉ là AI".
  3. Nêu KẾT QUẢ KIỂM TRA cụ thể và trả lời lại cho đúng: (a) nếu có sai/thiếu/lệch thật → nói thẳng chỗ sai là gì, giá trị/nội dung đúng là gì (kèm nguồn và ngày theo rule 5), rồi trả lời lại câu hỏi ban đầu với thông tin đã sửa; (b) nếu dữ liệu gốc xác nhận báo cáo đúng → nhẹ nhàng, khiêm tốn trình bày số liệu/nguồn đã đối chiếu để anh/chị tự kiểm chứng, giải thích vì sao có thể hiểu lệch (vd khác ngày dữ liệu, khác đơn vị, khác hợp đồng), KHÔNG nhận sai khi dữ liệu không sai, KHÔNG tranh cãi; (c) nếu không đối chiếu được (không có dữ liệu/tool lỗi) → nói thật là chưa xác minh được và ĐỀ NGHỊ anh/chị gửi link nguồn hoặc số liệu gốc (xem bên dưới) thay vì đoán. Nếu người dùng cung cấp số liệu/nguồn mới mâu thuẫn dữ liệu hệ thống, ghi nhận nguồn đó, nói rõ nó khác với dữ liệu hệ thống ở điểm nào, và ghi chú rằng em chỉ ghi nhận trong cuộc trò chuyện này (không sửa được báo cáo gốc).
  Phản hồi tốt/trung tính/câu hỏi bình thường thì KHÔNG xin lỗi.
- KHI KHÔNG BIẾT / THIẾU THÔNG TIN: không đoán bừa, không lấp liếm. Thứ tự: (1) thử tra bằng các tool/web_search nếu có khả năng ra; (2) nếu vẫn thiếu và thông tin đó CHỈ anh/chị mới có hoặc mới xác định được (ngày/mã hợp đồng/khoảng thời gian muốn xem, đoạn trích bị thiếu, số liệu hoặc nguồn gốc mà anh/chị đang đối chiếu, tài liệu nội bộ, vị thế/khẩu vị rủi ro...) → NÓI RÕ em đang thiếu gì và LỊCH SỰ ĐỀ NGHỊ anh/chị cung cấp (nêu cụ thể cần gì, ví dụ "Dạ em chưa tìm thấy số liệu này trong hệ thống ạ, anh/chị gửi giúp em link nguồn hoặc số liệu gốc để em đối chiếu nhé"). Hỏi ĐÚNG những gì cần, tối đa 2 câu hỏi/lượt, và nêu luôn phần em đã làm/biết được cho tới lúc đó nếu có.
- NGUỒN DO NGƯỜI DÙNG CUNG CẤP: khi người dùng dán link (bài tin, trang giá, báo cáo...) hoặc nhờ kiểm tra theo nguồn của họ → dùng tool fetch_user_source để ĐỌC chính trang đó rồi mới trả lời; chỉ dựa vào nội dung tool trả về, trích dẫn đúng tên miền + ngày nếu có, và nói rõ đâu là thông tin từ nguồn của anh/chị, đâu là dữ liệu hệ thống. Nếu tool báo không đọc được (chặn bot/cần đăng nhập/JS) → nói thật và nhờ anh/chị dán trực tiếp nội dung hoặc số liệu cần đối chiếu.

{data_block}

{date_reference}

{_build_domain_knowledge(overrides)}
{few_shot_section}
NĂNG LỰC CỦA BẠN — bạn có thể và NÊN thực hiện khi người dùng yêu cầu, nhưng LUÔN ở dạng CÔ ĐỌNG (xem QUY TẮC TRẢ LỜI mục 6 — độ dài luôn ưu tiên hơn độ đầy đủ):
A. TRẢ LỜI THỰC TẾ: giải thích, tóm tắt, làm rõ nội dung đoạn trích dựa trên dữ liệu nền — thẳng vào ý chính, không diễn giải lan man.
B. PHÂN TÍCH GIẢ ĐỊNH (what-if): khi người dùng đặt câu hỏi giả định (VD "Nếu giá gas tăng 20% thì..."), trả lời NGẮN GỌN theo đúng 1 mạch: mở đầu bằng "Trong kịch bản giả định..." rồi nêu chuỗi nhân quả cô đọng (2-3 bước chính, dựa trên KIẾN THỨC CHUYÊN MÔN NỀN TẢNG ở trên) và chốt HƯỚNG tác động (mạnh/vừa/nhẹ) — KHÔNG liệt kê tách riêng từng bước thành nhiều gạch đầu dòng, KHÔNG đưa con số giá cụ thể (không thể dự đoán chính xác). Chỉ khai triển dài hơn nếu người dùng chủ động yêu cầu "giải thích chi tiết"/"phân tích sâu hơn".
C. SUY LUẬN CHUYÊN SÂU: khi người dùng hỏi "tại sao", "cơ chế nào", "mối liên hệ giữa X và Y", giải thích cơ chế truyền dẫn NGẮN GỌN, đủ hiểu bản chất — không cần liệt kê mọi khía cạnh (ngắn/dài hạn, điều kiện kích hoạt...) trừ khi câu hỏi hỏi rõ về khía cạnh đó. Đây là câu hỏi SUY LUẬN LOGIC dựa trên KIẾN THỨC CHUYÊN MÔN NỀN TẢNG + dữ kiện định tính đã có trong đoạn trích/DỮ LIỆU NỀN — KHÔNG cần gọi thêm get_market_prices/get_eua_details/get_price_history để lấy số liệu "minh hoạ" nếu bản thân câu hỏi không đòi hỏi 1 con số cụ thể; gọi tool giá chỉ tốn thời gian chờ mà không đổi nội dung suy luận. CẤU TRÚC BẮT BUỘC (xem thêm giới hạn độ dài ở rule 6): câu 1 nêu THẲNG tên kênh/cơ chế truyền dẫn đúng + kết luận chiều tác động, câu 2-3 nêu lý do cốt lõi (không nhắc lại số liệu đã có sẵn trong đoạn trích), câu cuối (chỉ nếu thực sự cần) nêu 1 giới hạn/lưu ý độ tin cậy — TUYỆT ĐỐI KHÔNG mở đầu bằng cách tóm tắt lại đoạn trích, và KHÔNG dành hẳn 1 đoạn để liệt kê kênh nào KHÔNG áp dụng trước khi nói tới kênh nào áp dụng.
D. SO SÁNH & ĐÁNH GIÁ: khi hỏi về ảnh hưởng đến doanh nghiệp/ngành/quốc gia, nêu thẳng kênh tác động chính và mức độ chắc chắn trong 1 đoạn ngắn — không cần liệt kê đầy đủ mọi kênh truyền dẫn nếu không được hỏi.
E. TRA CỨU WEB (chỉ khi thực sự cần, không lạm dụng): bạn có công cụ tìm kiếm web (web_search). CHỈ dùng khi ĐOẠN TRÍCH + DỮ LIỆU NỀN (đưa ra ngay bên dưới các quy tắc này) + KIẾN THỨC CHUYÊN MÔN NỀN TẢNG ở trên KHÔNG đủ để trả lời{web_search_order_note} — ví dụ người dùng hỏi 1 số liệu/sự kiện/tổ chức cụ thể ngoài phạm vi hệ thống theo dõi, hoặc tin tức rất mới không có trong DỮ LIỆU NỀN đã crawl. KHÔNG dùng web_search để tra lại thứ đã có sẵn, và KHÔNG dùng cho câu hỏi giả định/suy luận thuần (mục B, C) — những câu đó dùng kiến thức nền tảng, không cần tra cứu.
F. PHÂN TÍCH KỸ THUẬT EUA (mốc chốt lời/bắt đáy): khi người dùng hỏi về mốc kỹ thuật/điểm chốt lời/điểm bắt đáy/kháng cự/hỗ trợ của EUA, {price_ref} mốc kỹ thuật (tính từ đỉnh/đáy 30 phiên thật, KHÔNG tự bịa mốc khác):
   - Kháng cự/điểm chốt lời kỹ thuật = đỉnh 30 phiên gần nhất. Nếu người dùng hỏi "phá mốc này thì giá lên bao nhiêu", TRẢ LỜI bằng đúng mục tiêu kỹ thuật đã tính sẵn (kỹ thuật đo biên độ dao động — measured move) — khác với rule B (what-if vĩ mô KHÔNG đưa con số), ở đây ĐƯỢC PHÉP nêu con số vì đã tính sẵn từ dữ liệu thật, nhưng PHẢI nói rõ đây là "ước lượng kỹ thuật tham khảo" chứ không phải dự đoán chắc chắn.
   - Hỗ trợ/điểm giảm kỹ thuật = đáy 30 phiên gần nhất — mô tả đây là vùng thường xuất hiện lực mua bắt đáy về mặt kỹ thuật (hành vi thị trường điển hình ở vùng hỗ trợ), KHÔNG khẳng định chắc chắn giá sẽ bật lại.
   - Vẫn phải tuân thủ rule 7 (trung lập, không khuyến nghị đầu tư): mô tả mốc và hành vi kỹ thuật điển hình là được, KHÔNG được nói "nên mua/nên bán tại X" hay đưa lời khuyên giao dịch trực tiếp.
   - Chỉ áp dụng cho EUA — nếu được hỏi mốc kỹ thuật của 5 instrument còn lại, nói rõ hệ thống chưa hỗ trợ mốc kỹ thuật cho mã đó.

QUY TẮC TRẢ LỜI (bắt buộc tuân thủ):
1. NEO VÀO ĐOẠN TRÍCH: đoạn trích là bối cảnh khởi đầu của cả cuộc hội thoại, câu trả lời phải nhất quán với nó. Nhưng khi câu hỏi vượt ra ngoài chính đoạn trích và nội dung liên quan nằm ở BẤT KỲ mục nào khác của báo cáo (Mục 1 đến 9, hoặc "biz" — hỏi so sánh/liên hệ giữa đoạn trích với phần khác của báo cáo, hoặc hỏi thẳng về nội dung 1 mục khác như lịch sự kiện/gợi ý kinh doanh/quan điểm trái chiều/nguồn tham khảo...), {report_ref} để trả lời thay vì từ chối vì "ngoài phạm vi đoạn trích" hay "hệ thống không hỗ trợ mục này" — get_report_section hỗ trợ ĐỦ mọi mục, không chỉ 1/2/3.
2. PHÂN BIỆT RÕ RÀNG: luôn phân biệt giữa (a) DỮ LIỆU GIÁ thật (Δ ngày/Δ tuần của 6 instrument hệ thống theo dõi, kèm volume/mốc kỹ thuật riêng cho EUA), (b) SỰ KIỆN/SỐ LIỆU thật từ đoạn trích / CÁC MỤC KHÁC CỦA BÁO CÁO (lấy qua get_report_section) / dữ liệu nền tin tức, (c) KIẾN THỨC NỀN TẢNG về cơ chế thị trường, (d) SUY LUẬN / PHÂN TÍCH GIẢ ĐỊNH của bạn, và (e) KẾT QUẢ TRA CỨU WEB (nếu có dùng công cụ web_search). Thể hiện sự phân biệt này bằng NGÔN NGỮ TỰ NHIÊN, không cần rập khuôn 1 cụm từ cố định cho mỗi loại — ví dụ "theo dữ liệu giá", "theo tin tức", "về cơ chế", "trong kịch bản giả định" chỉ là gợi ý cách diễn đạt, không phải khuôn mẫu bắt buộc lặp lại y nguyên; miễn người đọc phân biệt được đâu là số liệu thật, đâu là suy luận.
3. KHÔNG BỊA SỐ LIỆU CỤ THỂ: tuyệt đối không bịa ngày tháng, tên tổ chức, mức giá, hay sự kiện cụ thể không xuất hiện trong đoạn trích/dữ liệu nền/kết quả web_search. Nhưng BẠN ĐƯỢC PHÉP suy luận logic dựa trên kiến thức chuyên môn — "Nếu TTF tăng mạnh, theo cơ chế fuel switching thì..." KHÔNG phải bịa đặt mà là phân tích.
4. THÀNH THẬT VỀ GIỚI HẠN: nếu câu hỏi đòi hỏi dữ liệu không có trong context —{limitation_tool_note} (a) nếu là thông tin cụ thể có thể tra cứu được (số liệu/sự kiện/tổ chức, không phải suy đoán), dùng công cụ web_search để tìm rồi trả lời dựa trên kết quả đó; (b) nếu không tra được hoặc câu hỏi mang tính suy luận/giả định, nói rõ giới hạn dữ liệu (VD "Dữ liệu hiện có chưa đề cập chi tiết X") rồi PHÂN TÍCH DỰA TRÊN NHỮNG GÌ BIẾT ĐƯỢC thay vì chỉ nói "không biết" và dừng.
5. DẪN NGUỒN: khi dùng thông tin từ DỮ LIỆU NỀN, PHẢI trích dẫn bằng đúng nhãn nguồn trong ngoặc tròn — vd "(reuters.com, 20/08/2026 14:30)". Khi dùng thông tin từ 1 mục của báo cáo (ngoài đoạn trích, lấy qua get_report_section), ghi rõ mục đã dùng — vd "(Báo cáo ngày {report_date}, Mục 3)" hoặc "(Báo cáo ngày {report_date}, Mục Gợi ý kinh doanh)". Khi dùng kết quả TRA CỨU WEB, trích dẫn cùng định dạng bằng tên miền/nguồn thật lấy từ kết quả tìm kiếm — vd "(nguồn tìm được qua web_search, ngày nếu có)" — TUYỆT ĐỐI KHÔNG bịa tên miền không có trong kết quả tìm kiếm thật. Không cần dẫn nguồn khi dùng kiến thức nền tảng hoặc suy luận logic.
6. NGẮN GỌN, TRẢ LỜI THẲNG VÀO TRỌNG TÂM (ưu tiên cao nhất, áp dụng cho MỌI loại câu hỏi kể cả mục B/C/D ở trên): TỪ ĐẦU TIÊN của câu trả lời phải là nội dung trả lời thật sự.
   - NGOẠI LỆ cho mục GIAO TIẾP ở đầu prompt: chữ "Dạ" lễ phép đầu câu và lời xin lỗi khi người dùng phản hồi chưa tốt (sau khi đã kiểm tra bằng tool) được phép, vì là một phần của câu trả lời; vẫn tính trong giới hạn độ dài.
   - CẤM mọi câu/cụm mở đầu kiểu dẫn nhập, rào đón, hay tự thuật lại quá trình suy nghĩ — vd "Để trả lời...", "Để trả lời chính xác, tôi cần...", "Trước khi trả lời...", "Đây là...", "Về vấn đề này...", "Câu hỏi hay...". QUY TẮC NÀY ÁP DỤNG CẢ KHI CẦN GỌI TOOL: nếu cần dữ liệu từ tool, GỌI TOOL NGAY LẬP TỨC — TUYỆT ĐỐI KHÔNG viết bất kỳ câu text nào trước/xen giữa lúc gọi tool để tường thuật ý định (CẤM tuyệt đối kiểu "Tôi cần lấy thêm dữ liệu...", "Hãy để tôi kiểm tra...", "Khối lượng này có thể phản ánh nhiều tín hiệu, để tôi xem thêm..."). Bản thân hành động gọi tool (không kèm text) KHÔNG tính là vi phạm — chỉ cấm PHẦN TEXT tường thuật, không cấm việc gọi tool. Chỉ viết text SAU KHI đã có đủ dữ liệu từ tool, và text đó PHẢI là câu trả lời thật, không phải lời dẫn.
   - CÂU HỎI MƠ HỒ NHƯNG GIẢI QUYẾT ĐƯỢC TỪ NGỮ CẢNH SẴN CÓ (đoạn trích, dữ liệu nền, lịch sử hội thoại, hoặc tra thêm được qua tool): TỰ CHỌN cách hiểu hợp lý nhất rồi trả lời thẳng luôn — KHÔNG hỏi ngược người dùng ("bạn đề cập là gì?", "ý bạn là...?"). VD "xu hướng này" mà ngữ cảnh chỉ đang nhắc tới đúng 1 xu hướng — hiểu theo đó, có thể nêu ngắn gọn cách hiểu trong câu trả lời (VD "Nếu xu hướng giảm của EUA tiếp diễn...") thay vì hỏi ngược.
   - CÂU HỎI MƠ HỒ VỀ Ý ĐỊNH/ĐỐI TƯỢNG HỎI, KHÔNG THỂ GIẢI QUYẾT TỪ NGỮ CẢNH SẴN CÓ (kể cả sau khi đã thử tra thêm qua tool nếu có) — khác với rule 4 (rule 4 là câu hỏi đã RÕ Ý nhưng THIẾU SỐ LIỆU/FACT cụ thể, vẫn phải phân tích dựa trên cái đã biết): đây là trường hợp bản thân câu hỏi có từ 2 cách hiểu hợp lý trở lên dẫn tới câu trả lời khác hẳn nhau (vd đại từ quy chiếu tới nhiều đối tượng cùng xuất hiện trong ngữ cảnh mà không rõ ý người dùng nhắm tới cái nào), hoặc nhắc tới 1 mã/sự kiện/mốc thời gian không hề xuất hiện ở bất kỳ đâu trong ngữ cảnh nên không xác định được NGƯỜI DÙNG ĐANG HỎI VỀ CÁI GÌ. Khi đó PHẢI hỏi lại NGẮN GỌN, ĐÚNG TRỌNG TÂM để làm rõ đúng điểm còn thiếu (1 câu hỏi ngắn, không rào đón dài dòng) — TUYỆT ĐỐI KHÔNG tự đoán bừa rồi trả lời như thể chắc chắn, và KHÔNG trả lời chung chung/né tránh để khỏi phải hỏi lại. Ngoài trường hợp này, chỉ được hỏi/xin thông tin từ người dùng theo mục GIAO TIẾP ở đầu prompt ("KHI KHÔNG BIẾT / THIẾU THÔNG TIN" và khi cần nguồn/số liệu gốc để đối chiếu phản hồi) — chỉ áp dụng khi thực sự không thể tự chọn cách hiểu hợp lý hoặc không tra ra được.
   - CẤM dùng markdown mang tính bài viết/báo cáo trong câu trả lời: không tiêu đề (`#`, `##`), không đường kẻ ngang (`---`), không nhãn kiểu "**Trả lời ngắn:**"/"**Câu Trả Lời:**". Chỉ được dùng in đậm cho 1-2 từ khoá quan trọng và gạch đầu dòng khi thực sự liệt kê nhiều ý (xem giới hạn bên dưới) — không dùng cho cấu trúc tiêu đề/phần mục.
   - Toàn bộ câu trả lời tối đa 4-6 câu văn, HOẶC tối đa 4 gạch đầu dòng ngắn (mỗi gạch 1-2 câu) nếu thực sự cần liệt kê nhiều ý độc lập — KHÔNG dùng gạch đầu dòng cho câu trả lời đơn giản chỉ cần 1-2 câu. Đây là hội thoại chat nhanh, KHÔNG phải văn phong báo cáo dài — chỉ viết dài hơn mức này khi người dùng CHỦ ĐỘNG yêu cầu ("giải thích chi tiết hơn", "phân tích đầy đủ"...).
     + ĐƯỢC PHÉP xuống dòng/gạch đầu dòng/bôi đậm để tách ý cho RÕ RÀNG, dễ đọc — không bắt buộc gò ép viết liền thành 1 câu/1 khối duy nhất. Nhưng đây CHỈ là cách TRÌNH BÀY, không phải cái cớ để mở rộng nội dung: tổng số câu/gạch đầu dòng vẫn PHẢI nằm trong giới hạn ở trên (4-6 câu hoặc tối đa 4 gạch đầu dòng). TUYỆT ĐỐI KHÔNG viết thành nhiều ĐOẠN VĂN XUÔI dài nối tiếp nhau, mỗi đoạn tự ý diễn giải/mở rộng thêm 1 khía cạnh mới (đây là dấu hiệu rõ nhất của việc vượt giới hạn dù từng đoạn riêng lẻ trông có vẻ ngắn) — nếu thật sự cần tách nhiều ý, dùng ĐÚNG định dạng gạch đầu dòng ngắn đã quy định ở trên, không phải đoạn văn.
     + KHÔNG THUẬT LẠI/DIỄN GIẢI LẠI số liệu hay sự kiện đã CÓ SẴN NGUYÊN VĂN trong chính đoạn trích người dùng đang bôi đen — người dùng đã đọc đoạn đó rồi, việc của bạn là trả lời câu hỏi (vd "vì sao"/"cơ chế nào"), không phải tóm tắt lại đoạn trích trước khi trả lời.
     + KHÔNG liệt kê cơ chế/kênh truyền dẫn KHÔNG áp dụng (vd "đây không phải kênh X vì...") trừ khi người dùng hỏi rõ so sánh/phân biệt — chỉ nêu THẲNG kênh/cơ chế ĐÚNG đang áp dụng và kết luận, bỏ qua phần loại trừ.
     + TRƯỚC KHI XUẤT câu trả lời, tự đếm số câu — nếu vượt quá 6, PHẢI cắt/gộp ngay: giữ lại đúng (a) kết luận/cơ chế chính, (b) 1 lý do cốt lõi nhất, (c) lưu ý độ tin cậy CHỈ nếu thực sự cần — bỏ mọi câu diễn giải/bổ sung phụ khác, không cố nhét đủ ý bằng cách viết câu dài hơn để né đếm câu.
   - VĂN PHONG: viết như một chuyên gia đang trò chuyện, KHÔNG như điền vào khuôn mẫu có sẵn — câu chữ tự nhiên, khoa học, mạch lạc, biến đổi cách diễn đạt giữa các câu trả lời thay vì lặp lại đúng 1 cấu trúc/cụm từ mở đầu ở mọi lượt chat. Tránh giọng máy móc, liệt kê khô khan khi 1 câu văn liền mạch diễn đạt được — gạch đầu dòng chỉ dùng khi thực sự cần tách bạch nhiều ý độc lập (xem giới hạn ở trên).
7. TRUNG LẬP, KHÔNG KHUYẾN NGHỊ ĐẦU TƯ: giữ giọng văn chuyên gia; không đưa khuyến nghị mua/bán tài chính trực tiếp.
8. ĐÚNG PHẠM VI: nếu câu hỏi ngoài phạm vi năng lượng/carbon/thị trường liên quan, lịch sự từ chối — kể cả khi có thể tra được bằng web_search, không đi lạc đề.
9. NGÔN NGỮ: trả lời bằng tiếng Việt, trừ khi người dùng chủ động hỏi bằng ngôn ngữ khác.

NHẮC LẠI LẦN CUỐI (quan trọng nhất, xem đầu prompt): từ đầu tiên xuất ra PHẢI là nội dung trả lời thật — không lời dẫn, không tường thuật ý định, không tường thuật việc gọi tool. Gọi tool NGAY nếu cần, không kèm text. Luôn xưng "em", gọi "anh/chị", lễ phép; khi người dùng phản hồi chưa tốt thì kiểm tra bằng tool TRƯỚC, rồi xin lỗi 1 lần, rồi nêu kết quả kiểm tra + trả lời lại; thiếu thông tin chỉ người dùng có thì lịch sự nhờ họ cung cấp."""


def _build_dynamic_context(quote: str, context_block: str) -> str:
    """Phần system prompt đổi theo TỪNG câu hỏi — quote cố định trong 1 phiên
    nhưng context_block (dữ liệu nền retrieve) đổi mỗi câu hỏi. LUÔN đứng SAU
    `_build_static_instructions()`, KHÔNG đánh cache_control — nếu đặt trước
    hoặc chen giữa phần tĩnh, mọi thay đổi ở đây sẽ làm mất cache toàn bộ phần
    tĩnh phía sau (cache là khớp PREFIX, hỏng ở đâu là mất cache từ đó trở đi).
    """
    if not quote.strip():
        # Chat tự do (mở từ avatar Jenny, không bôi đen đoạn nào) — ghi đè các quy
        # tắc về "đoạn trích" trong phần tĩnh phía trên (giữ nguyên phần tĩnh để
        # không mất prompt cache), Jenny trả lời thẳng theo câu hỏi.
        quote_block = (
            "=== KHÔNG CÓ ĐOẠN TRÍCH — CHAT TỰ DO ===\n"
            "Người dùng mở chat trực tiếp, KHÔNG bôi đen đoạn nào trong báo cáo. BỎ QUA mọi quy tắc "
            "nói về \"đoạn trích\" ở trên (neo vào đoạn trích, đoạn trích thiếu ngữ cảnh...): trả lời "
            "thẳng theo CÂU HỎI của người dùng, dựa trên DỮ LIỆU NỀN bên dưới, các mục của báo cáo "
            "(get_report_section khi cần) và các tool dữ liệu giá. KHÔNG nhắc tới \"đoạn trích\"/"
            "\"đoạn bạn bôi đen\" trong câu trả lời."
        )
    else:
        quote_block = (
            "=== ĐOẠN NGƯỜI DÙNG ĐANG BÔI ĐEN (điểm neo của toàn bộ hội thoại) ===\n"
            f"\"\"\"{quote}\"\"\""
        )
    return f"""{quote_block}

=== DỮ LIỆU NỀN LIÊN QUAN (trích từ kho tin tức đã crawl, đánh số để trích dẫn) ===
{context_block}"""


# ─────────────────────────────────────────────────────────────────────
# Retrieval + streaming
# ─────────────────────────────────────────────────────────────────────

async def retrieve_context_for_quote(
    retrieval_service: RetrievalService, quote: str, question: str, report_date: str
) -> List[RetrievedDocument]:
    """Ghép quote + câu hỏi thành 1 query semantic để tìm dữ liệu nền liên quan.

    `only_source_type="article"`: loại chunk `source_type='report'` (chunk của
    chính báo cáo) khỏi kết quả — quote người dùng bôi đen đã trích NGUYÊN VĂN
    từ 1 chunk report, nên chunk đó luôn khớp gần tuyệt đối và chiếm hết top-k
    nếu không loại trừ, khiến "dữ liệu nền" toàn là báo cáo tự trích lại chính
    nó (không có URL) thay vì bài báo thật — hệ quả là "Danh sách tin tức tham
    khảo" ở FE luôn rỗng dù không có lỗi gì (chỉ router filter đúng chunk
    article mới có URL để đưa vào "sources"). Nội dung quote đã có sẵn nguyên
    văn trong system prompt nên không cần retrieve lại từ chunks.
    """
    query = f"{_truncate(quote, MAX_QUOTE_CHARS)}\n\n{question}".strip()
    return await retrieval_service.retrieve(
        query=query,
        top_k=MAX_CONTEXT_CHUNKS,
        hybrid_limit=HYBRID_SEARCH_LIMIT,
        # retrieve() lọc tin theo NGÀY DỮ LIỆU — báo cáo report_date T dùng tin
        # của ngày dữ liệu T-1 (xem report_generator.py::report_data_date).
        report_date=report_data_date(report_date),
        only_source_type="article",
        min_relevance_score=MIN_RERANK_SCORE,
    )


async def _tool_search_news_text(
    retrieval_service: RetrievalService, query: str, date: Optional[str] = None
) -> str:
    """Tìm kiếm CHỦ ĐỘNG (hybrid search + rerank, giống `retrieve_context_for_quote`
    ở trên) trong kho tin tức đã crawl — khác với DỮ LIỆU NỀN tự động (chỉ embed
    ĐÚNG 1 LẦN theo quote+câu hỏi ĐẦU TIÊN của lượt hỏi này, và luôn giới hạn
    đúng ngày `report_date` đang xem), tool này cho model CHỦ ĐỘNG tra lại với
    query khác/ngày khác giữa vòng lặp hội thoại — vd câu hỏi thứ 2 trong cùng
    phiên lệch chủ đề khỏi DỮ LIỆU NỀN ban đầu, hoặc cần tin của 1 NGÀY KHÁC
    report_date.

    `date=None`: tìm KHÔNG giới hạn ngày, trên toàn bộ kho tin đã crawl (mọi
    report_date) — không hỗ trợ khoảng ngày (from/to), chỉ 1 ngày cụ thể hoặc
    không giới hạn."""
    results = await retrieval_service.retrieve(
        query=query,
        top_k=MAX_CONTEXT_CHUNKS,
        hybrid_limit=HYBRID_SEARCH_LIMIT,
        report_date=date,
        only_source_type="article",
        min_relevance_score=MIN_RERANK_SCORE,
    )
    if not results:
        scope = f"trong ngày {date}" if date else "trên toàn bộ kho tin đã crawl"
        return f"Không tìm thấy bài báo nào khớp với \"{query}\" {scope}. Có thể thử từ khoá khác hoặc không giới hạn ngày."

    lines = []
    for i, r in enumerate(results, start=1):
        label = _format_source_label(r, date or "")
        url_part = f" — {r.url}" if r.url else ""
        lines.append(f"[{i}] ({label}){url_part}\n{r.content.strip()}")
    return (
        "\n\n".join(lines)
        + "\n\nLƯU Ý: khi dùng thông tin ở trên, trích dẫn ĐÚNG nhãn nguồn kèm theo (tên nguồn, thời "
        "gian) — TUYỆT ĐỐI KHÔNG bịa thêm bài báo/nguồn không có trong danh sách này."
    )


WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}

# CLIENT_TOOLS — khác với web_search (SERVER tool, Anthropic tự thực thi), các
# tool này do CHÍNH quote_chat.py thực thi (query DB) khi model gọi — xem
# `_execute_client_tool` + vòng lặp tool trong `_stream_anthropic`. Đây là
# điểm cốt lõi của việc "chỉ lấy dữ liệu khi thực sự cần": trước đây
# prices/report luôn được fetch + tiêm vào MỌI request; giờ chỉ fetch khi
# model chủ động gọi tool tương ứng.

# Property `date` dùng chung cho get_market_prices/get_eua_details/get_eua_volume_history —
# mặc định (không truyền) các tool này tra cứu theo ngày báo cáo đang xem; truyền `date` khi
# người dùng hỏi cụ thể về 1 ngày KHÁC ngày báo cáo (vd đang xem báo cáo 10/09 nhưng hỏi "giá
# ngày 09/09") để tránh trả nhầm dữ liệu ngày báo cáo mà không nói rõ (xem comment ở
# `_execute_client_tool`/`_tool_market_prices_text`).
_DATE_PARAM_SCHEMA = {
    "type": "string",
    "description": (
        "TUỲ CHỌN — định dạng YYYY-MM-DD. CHỈ truyền khi người dùng hỏi rõ về 1 ngày CỤ THỂ khác "
        "với ngày báo cáo đang xem. Không truyền = mặc định lấy theo ngày báo cáo đang xem. Nếu "
        "ngày truyền vào không có dữ liệu (cuối tuần/nghỉ lễ/chưa crawl), tool trả về dữ liệu của "
        "phiên gần nhất TRƯỚC đó kèm cảnh báo — PHẢI đọc kỹ và nêu đúng ngày thực tế trong câu trả lời."
    ),
}

CLIENT_TOOLS = [
    {
        "name": "get_market_prices",
        "description": (
            "Lấy giá đóng cửa + Δ ngày + Δ tuần của 6 instrument hệ thống theo dõi (EUA, TTF/gas, "
            "API2/than, Brent, WTI, DEBY1/điện Đức) cho ngày báo cáo đang xem (hoặc ngày cụ thể "
            "truyền qua `date`). Gọi khi câu trả lời cần SỐ LIỆU GIÁ CỤ THỂ chưa có sẵn trong đoạn "
            "trích/dữ liệu nền đã cung cấp."
        ),
        "input_schema": {"type": "object", "properties": {"date": _DATE_PARAM_SCHEMA}},
    },
    {
        "name": "get_eua_details",
        "description": (
            "Lấy chi tiết phiên (mặc định phiên liền trước ngày báo cáo, hoặc phiên gần nhất tính "
            "đến ngày cụ thể truyền qua `date`) của EUA: biên độ OHLC (mở/cao/thấp/đóng), khối lượng "
            "giao dịch so với TB gần đây, và mốc kỹ thuật hỗ trợ/kháng cự (đỉnh/đáy 30 phiên). Gọi "
            "khi được hỏi về giá mở/cao/thấp trong phiên, khối lượng 1 phiên, hoặc mốc kỹ thuật của "
            "EUA. Nếu cần khối lượng theo NHIỀU NGÀY cụ thể để so sánh, dùng get_eua_volume_history "
            "thay vì tool này."
        ),
        "input_schema": {"type": "object", "properties": {"date": _DATE_PARAM_SCHEMA}},
    },
    {
        "name": "get_eua_volume_history",
        "description": (
            "Lấy bảng khối lượng giao dịch EUA theo TỪNG phiên (tối đa 30 phiên gần nhất tính đến "
            "ngày báo cáo đang xem, hoặc tính đến ngày cụ thể truyền qua `date`), mỗi phiên đã kèm "
            "sẵn %chênh lệch so với TB 20 phiên NGAY TRƯỚC phiên đó. Gọi khi người dùng hỏi về khối "
            "lượng của 1 NGÀY CỤ THỂ trong quá khứ, hoặc SO SÁNH khối lượng GIỮA CÁC NGÀY."
        ),
        "input_schema": {"type": "object", "properties": {"date": _DATE_PARAM_SCHEMA}},
    },
    {
        "name": "get_report_section",
        "description": (
            "Lấy toàn văn 1 mục BẤT KỲ của báo cáo ngày đang xem (hoặc ngày cụ thể truyền qua "
            "`date`, nếu báo cáo ngày đó đã published): '1' = Tóm tắt điều hành, "
            "'2' = Bảng giá nhanh (yếu tố hỗ trợ tăng/giảm giá), '3' = Phân tích chuyên sâu (bao gồm cả "
            "tín hiệu liên thị trường và quan điểm thị trường trái chiều nếu có) + kịch bản giao dịch, "
            "'4' = Cập nhật tín chỉ carbon & CBAM (VCM/thép xanh/CBAM), "
            "'6' = Chi tiết toàn bộ tin tức trong ngày (quốc tế + Việt "
            "Nam, kèm tóm tắt từng bài), "
            "'8' = Lịch sự kiện 7 ngày tới (EIA/Baker Hughes/họp chính sách), '9' = Danh sách nguồn "
            "tham khảo (tên nguồn + URL), 'biz' = Gợi ý kinh doanh & giải pháp cho SIM (ngắn hạn/dài "
            "hạn). Gọi khi câu hỏi nhắc tới nội dung 1 mục KHÁC ngoài đoạn trích người dùng đang bôi "
            "đen (vd 'Mục 3 nói gì về...', 'sắp tới có sự kiện gì', 'gợi ý kinh doanh là gì', 'nguồn "
            "tin lấy từ đâu', 'so với báo cáo hôm qua thì...'), hoặc khi cần ngữ cảnh xung quanh đoạn "
            "trích để hiểu đại từ quy chiếu ('nhóm này'/'xu hướng này'...)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "enum": ["1", "2", "3", "4", "6", "8", "9", "biz"],
                    "description": "Số mục cần lấy: '1', '2', '3', '4', '6', '8', '9', hoặc 'biz'.",
                },
                "date": {
                    "type": "string",
                    "description": (
                        "TUỲ CHỌN — định dạng YYYY-MM-DD. CHỈ truyền khi người dùng hỏi về báo cáo của "
                        "1 NGÀY KHÁC ngày đang xem (vd 'so với báo cáo hôm qua'). Không truyền = mặc "
                        "định lấy báo cáo ngày đang xem. Nếu ngày đó chưa có báo cáo đã published, tool "
                        "sẽ báo rõ không có dữ liệu thay vì trả nhầm ngày khác."
                    ),
                },
            },
            "required": ["section"],
        },
    },
    {
        "name": "get_biz_suggestions",
        "description": (
            "Đọc BỘ NHỚ GỢI Ý KINH DOANH của Jenny (bảng biz_suggestions) — gợi ý ngắn hạn/dài hạn đã đề "
            "xuất trong các báo cáo gần đây, kèm TRẠNG THÁI mới nhất: đang theo dõi (chưa kích hoạt), ĐÃ "
            "KÍCH HOẠT (kèm bằng chứng + nguồn + ngày kích hoạt) hoặc admin đã gỡ. Gọi khi hỏi 'Jenny đã "
            "đề xuất gì trước đây', 'gợi ý nào đang theo dõi/đã kích hoạt', 'tình huống X đã xảy ra "
            "chưa'. Khác get_report_section('biz') (chỉ nội dung đóng băng của 1 báo cáo). CHỈ ĐỌC — không "
            "tạo/sửa/gỡ được gợi ý."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["active", "pending", "triggered", "dismissed", "all"],
                    "description": "Mặc định 'active' = đang theo dõi + đã kích hoạt (không gồm đã gỡ).",
                },
                "kind": {
                    "type": "string",
                    "enum": ["short", "long", "all"],
                    "description": "Mặc định 'all'. 'short' = ngắn hạn (có tình huống kích hoạt), 'long' = dài hạn.",
                },
                "days": {
                    "type": "integer",
                    "description": "Số ngày nhìn lại tính đến ngày báo cáo (mặc định 10, tối đa 30).",
                },
                "date": {
                    "type": "string",
                    "description": (
                        "TUỲ CHỌN — YYYY-MM-DD, ngày kết thúc của khoảng nhìn lại. Không truyền = ngày "
                        "báo cáo đang xem."
                    ),
                },
            },
        },
    },
    {
        "name": "list_reports",
        "description": (
            "Liệt kê các NGÀY đã có báo cáo published (mới → cũ). Gọi khi cần biết báo cáo nào tồn tại "
            "trước khi so sánh nhiều ngày ('so với tuần trước', 'các báo cáo gần đây') hoặc khi get_report_section "
            "báo không có dữ liệu cho 1 ngày."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "Số báo cáo tối đa (mặc định 10, tối đa 60)."}},
        },
    },
    {
        "name": "browse_news",
        "description": (
            "Liệt kê CÓ CẤU TRÚC các bài đã crawl trong 1 ngày (id, nguồn, hạng nguồn, giờ đăng, tiêu đề, topic, "
            "tin nóng), lọc theo topic/region/tin nóng. Khác search_news (tìm theo ngữ nghĩa/từ khoá, trả đoạn nội "
            "dung): dùng cho 'hôm nay có những tin gì về CBAM', 'có tin nóng nào không', 'tin Việt Nam hôm nay', "
            "'bao nhiêu bài về khí gas'. Cần nội dung 1 bài thì gọi tiếp get_article."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "enum": list(_TOPIC_VALUES), "description": "Lọc theo chủ đề (tuỳ chọn)."},
                "region": {"type": "string", "enum": ["vietnam", "international"], "description": "Lọc theo phạm vi nguồn (tuỳ chọn)."},
                "hot_only": {"type": "boolean", "description": "true = chỉ tin nóng."},
                "limit": {"type": "integer", "description": "Số bài tối đa (mặc định 15, tối đa 30)."},
                "date": {"type": "string", "description": "TUỲ CHỌN — YYYY-MM-DD, ngày dữ liệu tin. Không truyền = ngày dữ liệu của báo cáo đang xem."},
            },
        },
    },
    {
        "name": "get_article",
        "description": (
            "Lấy TOÀN VĂN 1 bài báo đã crawl theo `article_id` (từ browse_news) hoặc `url` (từ search_news / Mục 9 "
            "nguồn tham khảo). Gọi khi người dùng hỏi chi tiết về 1 bài cụ thể mà tóm tắt/đoạn trích chưa đủ."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "article_id": {"type": "integer", "description": "id bài (từ browse_news)."},
                "url": {"type": "string", "description": "URL bài (nếu không có id)."},
            },
        },
    },
    {
        "name": "calc_price_stats",
        "description": (
            "TÍNH SẴN thống kê giá cho 1–4 instrument trong 1 khoảng: % thay đổi, cao/thấp nhất (kèm ngày), "
            "độ lệch chuẩn biến động ngày, phiên tăng/giảm mạnh nhất; nếu truyền ≥2 mã thì thêm TƯƠNG QUAN lợi "
            "suất ngày, tỷ lệ và chênh lệch (khi cùng đơn vị) giữa từng cặp. Dùng cho câu hỏi định lượng/liên thị "
            "trường: 'EUA và TTF tương quan thế nào 30 ngày qua', 'biến động EUA tháng này so với Brent', 'mã nào "
            "biến động mạnh nhất'. LUÔN dùng tool này thay vì tự tính từ get_price_history."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "instruments": {
                    "type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": STATS_MAX_INSTRUMENTS,
                    "description": "Mã/tên 1–4 instrument, vd [\"EUA\", \"TTF\"].",
                },
                "start_date": {"type": "string", "description": "TUỲ CHỌN — YYYY-MM-DD, đầu khoảng (lấy từ LỊCH THAM CHIẾU). Bỏ trống = lấy `sessions` phiên gần nhất."},
                "sessions": {"type": "integer", "description": f"Số phiên gần nhất khi không có start_date (mặc định {STATS_DEFAULT_SESSIONS}, tối đa {PRICE_HISTORY_MAX_SESSIONS})."},
                "date": _DATE_PARAM_SCHEMA,
            },
            "required": ["instruments"],
        },
    },
    {
        "name": "get_report_history",
        "description": (
            "Lấy CÙNG 1 mảng nội dung của NHIỀU báo cáo liên tiếp (tối đa %d ngày, kết thúc ở ngày báo cáo đang xem) để so "
            "sánh/xem xu hướng theo thời gian: 'verdict' = nhận định tổng quan EUA, 'signal' = kịch bản ngắn/trung hạn, hoặc "
            "Mục '1','2','3','4','6','8','biz'. Dùng cho 'nhận định 7 ngày qua thay đổi thế nào', 'tín hiệu hôm nay khác hôm "
            "qua ra sao', 'tuần này Jenny đề xuất gì'. Nhanh hơn gọi get_report_section từng ngày." % HISTORY_MAX_DAYS
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "section": {"type": "string", "enum": list(_HISTORY_SECTIONS), "description": "Mảng cần so sánh."},
                "days": {"type": "integer", "description": f"Số ngày lịch nhìn lại (mặc định {HISTORY_DEFAULT_DAYS}, tối đa {HISTORY_MAX_DAYS}). Mục '3','6' rất dài — nên dùng ít ngày."},
                "date": {"type": "string", "description": "TUỲ CHỌN — YYYY-MM-DD, ngày báo cáo kết thúc. Không truyền = báo cáo đang xem."},
            },
            "required": ["section"],
        },
    },
    {
        "name": "review_past_forecast",
        "description": (
            "ĐỐI CHIẾU dự báo của 1 báo cáo trong quá khứ với diễn biến giá EUA THỰC TẾ sau đó: trả nhận định + kịch bản "
            "ngắn/trung hạn của báo cáo, giá EUA từng phiên sau mốc báo cáo, % so với mốc, và kết quả cùng/ngược chiều (tính "
            "sẵn). Dùng cho 'dự báo hôm qua có đúng không', 'kịch bản tuần trước có thành hiện thực không', 'Jenny nhận định "
            "đúng hay sai'. Chỉ áp dụng cho EUA."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD, ngày của báo cáo cần đối chiếu (báo cáo đó phải published). Bắt buộc khi hỏi về báo cáo KHÁC ngày đang xem; bỏ trống = báo cáo đang xem."},
                "sessions": {"type": "integer", "description": f"Số phiên giá sau báo cáo để đối chiếu (mặc định {FORECAST_DEFAULT_SESSIONS}, tối đa {FORECAST_MAX_SESSIONS})."},
            },
        },
    },
    {
        "name": "get_report_overview",
        "description": (
            "MỤC LỤC nhanh của 1 báo cáo: nhận định tổng quan EUA, kịch bản ngắn hạn, và SỐ LƯỢNG nội dung từng mục (số ý, "
            "số diễn biến, số tin, số sự kiện, số gợi ý). Gọi khi câu hỏi chung chung ('báo cáo hôm nay có gì', 'tóm tắt "
            "báo cáo') để biết nên đọc mục nào tiếp bằng get_report_section, thay vì đoán."
        ),
        "input_schema": {"type": "object", "properties": {"date": {"type": "string", "description": "TUỲ CHỌN — YYYY-MM-DD. Không truyền = báo cáo đang xem."}}},
    },
    {
        "name": "get_calendar_events",
        "description": (
            "Lịch sự kiện thị trường trong 1 KHOẢNG ngày bất kỳ (tối đa %d ngày, cả quá khứ lẫn tương lai): lịch định kỳ "
            "(EIA thứ Tư, Baker Hughes thứ Sáu — giờ VN) + sự kiện đã ghi trong Mục 8 các báo cáo, kèm kết quả nếu đã xảy ra. "
            "Dùng khi hỏi sự kiện NGOÀI cửa sổ 7 ngày của Mục 8: 'tuần sau/tháng này có sự kiện gì', 'số liệu EIA tuần trước "
            "ra sao'." % CALENDAR_MAX_DAYS
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD, đầu khoảng (lấy từ LỊCH THAM CHIẾU)."},
                "end_date": {"type": "string", "description": "YYYY-MM-DD, cuối khoảng."},
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "get_price_history",
        "description": (
            "Lấy lịch sử OHLC (mở/cao/thấp/đóng, kèm khối lượng nếu có) của 1 instrument BẤT KỲ hệ "
            "thống đang theo dõi (EUA, TTF, Brent, WTI, than API2/API4, điện Đức DEBY1, Gasoil...) qua "
            "NHIỀU phiên gần nhất — dùng khi cần XU HƯỚNG/lịch sử giá theo thời gian của 1 mã, khác "
            "get_market_prices (chỉ trả giá CỦA 1 NGÀY). Với EUA, ưu tiên get_eua_details/"
            "get_eua_volume_history trước (có thêm mốc kỹ thuật/khối lượng chi tiết); dùng tool này "
            "cho EUA chỉ khi cần đơn thuần dãy OHLC nhiều phiên. Nếu không chắc đúng mã hệ thống đang "
            "dùng, cứ thử tên gần đúng (vd \"than\", \"dầu Brent\") — tool sẽ trả về danh sách mã đúng "
            "nếu không khớp được."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "instrument": {
                    "type": "string",
                    "description": "Mã hoặc tên instrument cần xem lịch sử giá, vd \"TTF\", \"WTI\", \"than\", \"điện Đức\".",
                },
                "sessions": {
                    "type": "integer",
                    "description": f"Số phiên gần nhất cần lấy (mặc định {PRICE_HISTORY_DEFAULT_SESSIONS}, tối đa {PRICE_HISTORY_MAX_SESSIONS}). Bỏ qua khi đã truyền start_date.",
                },
                "start_date": {
                    "type": "string",
                    "description": (
                        "TUỲ CHỌN — YYYY-MM-DD, ngày BẮT ĐẦU của 1 khoảng thời gian (vd 'tuần trước', "
                        "'tháng trước', 'từ thứ Hai đến nay'), dùng cùng `date` = ngày KẾT THÚC khoảng. "
                        "Lấy đúng từ LỊCH THAM CHIẾU trong system prompt, không tự tính. Khi có start_date, "
                        "tool trả mọi phiên trong khoảng + biến động cả giai đoạn đã tính sẵn (so với phiên "
                        "liền trước khoảng)."
                    ),
                },
                "date": _DATE_PARAM_SCHEMA,
            },
            "required": ["instrument"],
        },
    },
    {
        "name": "fetch_user_source",
        "description": (
            "ĐỌC nội dung chính của 1 trang web công khai (bài tin, trang giá, báo cáo, file text/CSV/JSON) "
            "mà NGƯỜI DÙNG đưa link hoặc nhờ kiểm tra theo nguồn của họ. Gọi khi người dùng dán URL, hoặc khi "
            "đối chiếu phản hồi kiểu 'số này sai, nguồn X ghi khác' với nguồn tin/nguồn giá họ cung cấp. "
            "KHÁC web_search (tự tìm trên web) và search_news (kho tin đã crawl). Chỉ truyền URL người dùng đã "
            "đưa hoặc URL có thật trong báo cáo/Mục 9 — KHÔNG tự bịa URL. Không đọc được trang cần đăng nhập, "
            "trang dựng bằng JavaScript hoặc địa chỉ nội bộ; khi đó nhờ người dùng dán nội dung/số liệu."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Đường dẫn http/https đầy đủ của nguồn tin/nguồn giá."},
            },
            "required": ["url"],
        },
    },
    {
        "name": "search_news",
        "description": (
            "Tìm kiếm CHỦ ĐỘNG (semantic + full-text) trong TOÀN BỘ kho tin tức đã crawl — KHÁC với "
            "DỮ LIỆU NỀN đã có sẵn ở trên (DỮ LIỆU NỀN chỉ tìm ĐÚNG 1 LẦN theo quote+câu hỏi ĐẦU TIÊN "
            "của lượt hỏi này, luôn giới hạn đúng ngày báo cáo đang xem, KHÔNG tự cập nhật theo các "
            "câu hỏi tiếp theo trong cùng hội thoại). CHỈ gọi khi DỮ LIỆU NỀN không đủ — vd câu hỏi "
            "lệch chủ đề khỏi quote ban đầu, hoặc cần tin của 1 NGÀY KHÁC report_date đang xem ('tin "
            "tuần trước về CBAM', 'hôm qua có tin gì về OPEC'). KHÔNG gọi nếu thông tin cần đã có "
            "trong DỮ LIỆU NỀN hoặc lịch sử hội thoại."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Từ khoá/câu hỏi tìm kiếm, càng cụ thể càng tốt."},
                "date": {
                    "type": "string",
                    "description": (
                        "TUỲ CHỌN — định dạng YYYY-MM-DD, giới hạn tìm đúng 1 NGÀY tin tức cụ thể "
                        "(khung 07:00 giờ VN ngày đó đến 07:00 ngày hôm sau). Không truyền = tìm trên "
                        "TOÀN BỘ kho tin đã crawl, không giới hạn ngày — KHÔNG hỗ trợ khoảng ngày."
                    ),
                },
            },
            "required": ["query"],
        },
    },
]

# Chặn lặp vô hạn nếu model cứ liên tục gọi tool. Báo cáo có tới 10 mục
# (get_report_section: "1".."9" + "biz") — nếu model cần dò vài mục để tìm
# đúng vị trí đoạn trích bị thiếu ngữ cảnh (xem "LƯU Ý ĐẶC BIỆT VỀ ĐOẠN TRÍCH
# THIẾU NGỮ CẢNH" trong data_block) RỒI còn gọi thêm 1-2 tool giá, số lượt cần
# thiết cao hơn hẳn so với khi chỉ có 3 mục — 8 chừa dư đủ cho tình huống dò
# 4-5 mục + 2 tool giá mà vẫn còn ít nhất 1 lượt sinh câu trả lời cuối.
MAX_TOOL_ITERATIONS = 12


_DATE_ANCHORED_TOOLS = ("get_market_prices", "get_eua_details", "get_eua_volume_history", "get_report_section", "get_price_history", "get_biz_suggestions", "browse_news", "calc_price_stats", "get_report_history", "review_past_forecast", "get_report_overview")


async def _execute_client_tool(
    name: str,
    tool_input: dict,
    session: AsyncSession,
    report_date: str,
    *,
    tool_cache: Dict[tuple, str],
    chart_cache: Dict[str, List[Any]],
    retrieval_service: Optional[RetrievalService] = None,
) -> str:
    """`tool_cache`: nhớ lại kết quả TRONG PHẠM VI 1 câu hỏi (1 lượt gọi
    `_stream_anthropic`) — hệ thống prompt đã yêu cầu model "KHÔNG gọi lại 1
    tool đã dùng trong CÙNG hội thoại", nhưng đó chỉ là yêu cầu qua prompt,
    không được đảm bảo (model vẫn có thể lỡ gọi lại, đặc biệt qua nhiều vòng
    lặp tool). Cache ở tầng code đảm bảo gọi lại KHÔNG tốn thêm 1 round-trip
    DB — chỉ cache kết quả THÀNH CÔNG (lỗi tạm thời/transient không nên bị
    cache, để lần gọi lại sau có cơ hội thử lại thật).
    """
    if name == "get_report_section":
        cache_key = (name, tool_input.get("section"), tool_input.get("date"))
    elif name == "get_report_history":
        cache_key = (name, tool_input.get("section"), tool_input.get("days"), tool_input.get("date"))
    elif name == "review_past_forecast":
        cache_key = (name, tool_input.get("date"), tool_input.get("sessions"))
    elif name == "get_report_overview":
        cache_key = (name, tool_input.get("date"))
    elif name == "get_calendar_events":
        cache_key = (name, tool_input.get("start_date"), tool_input.get("end_date"))
    elif name == "calc_price_stats":
        cache_key = (name, tuple(tool_input.get("instruments") or ()), tool_input.get("start_date"), tool_input.get("sessions"), tool_input.get("date"))
    elif name == "list_reports":
        cache_key = (name, tool_input.get("limit"))
    elif name == "browse_news":
        cache_key = (name, tool_input.get("topic"), tool_input.get("region"), tool_input.get("hot_only"), tool_input.get("limit"), tool_input.get("date"))
    elif name == "get_article":
        cache_key = (name, tool_input.get("article_id"), tool_input.get("url"))
    elif name == "get_biz_suggestions":
        cache_key = (name, tool_input.get("status"), tool_input.get("kind"), tool_input.get("days"), tool_input.get("date"))
    elif name == "get_price_history":
        cache_key = (name, tool_input.get("instrument"), tool_input.get("date"), tool_input.get("sessions"), tool_input.get("start_date"))
    elif name == "search_news":
        cache_key = (name, tool_input.get("query"), tool_input.get("date"))
    elif name == "fetch_user_source":
        cache_key = (name, tool_input.get("url"))
    elif name in ("get_market_prices", "get_eua_details", "get_eua_volume_history"):
        cache_key = (name, tool_input.get("date"))
    else:
        cache_key = (name,)
    if cache_key in tool_cache:
        return tool_cache[cache_key]

    # `date` là tham số tuỳ chọn model truyền khi người dùng hỏi về 1 ngày CỤ THỂ khác ngày báo
    # cáo đang xem (xem mô tả tool + rule trong data_block của _build_static_instructions) —
    # validate format trước khi đưa vào query string-compare trên Price.price_date (cột Text,
    # xem db/models.py), tránh so sánh lexicographic sai lệch nếu model gửi format khác
    # "YYYY-MM-DD" (vd "09/09/2026"). search_news dùng `date` khác ngữ nghĩa (lọc đúng 1 ngày
    # crawl, không phải "ngày báo cáo mặc định") nên xử lý fallback riêng bên dưới.
    raw_date = tool_input.get("date")
    invalid_date_note = None
    if raw_date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(raw_date)):
        invalid_date_note = str(raw_date)
        raw_date = None
    # Không truyền `date` -> dữ liệu của báo cáo đang xem: tool giá/EUA tra theo NGÀY
    # DỮ LIỆU (report_date - 1, đúng dữ liệu báo cáo đã dùng — xem
    # report_generator.py::report_data_date), riêng get_report_section tra theo chính
    # report_date (khoá của bảng reports). Có truyền `date` -> dùng nguyên ngày đó.
    if raw_date:
        target_date = raw_date
    elif name in ("get_report_section", "get_biz_suggestions", "get_report_history", "review_past_forecast", "get_report_overview"):
        target_date = report_date
    else:
        target_date = report_data_date(report_date)

    try:
        if name == "get_market_prices":
            result = await _tool_market_prices_text(session, target_date, requested_date=raw_date)
        elif name == "get_eua_details":
            result = await _tool_eua_details_text(session, target_date, chart_cache)
        elif name == "get_eua_volume_history":
            result = await _tool_eua_volume_history_text(session, target_date, chart_cache)
        elif name == "get_report_section":
            result = await _tool_report_section_text(session, target_date, str(tool_input.get("section", "")))
        elif name == "get_report_history":
            try:
                hd = int(tool_input.get("days") or HISTORY_DEFAULT_DAYS)
            except (TypeError, ValueError):
                hd = HISTORY_DEFAULT_DAYS
            result = await _tool_report_history_text(session, target_date, str(tool_input.get("section", "")), max(1, min(hd, HISTORY_MAX_DAYS)))
        elif name == "review_past_forecast":
            try:
                fs = int(tool_input.get("sessions") or FORECAST_DEFAULT_SESSIONS)
            except (TypeError, ValueError):
                fs = FORECAST_DEFAULT_SESSIONS
            result = await _tool_review_forecast_text(session, target_date, max(1, min(fs, FORECAST_MAX_SESSIONS)))
        elif name == "get_report_overview":
            result = await _tool_report_overview_text(session, target_date)
        elif name == "get_calendar_events":
            sd, ed = str(tool_input.get("start_date") or ""), str(tool_input.get("end_date") or "")
            if not (re.fullmatch(r"\d{4}-\d{2}-\d{2}", sd) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", ed)):
                return "Cần truyền start_date và end_date đúng định dạng YYYY-MM-DD."
            if sd > ed:
                sd, ed = ed, sd
            if (date_cls.fromisoformat(ed) - date_cls.fromisoformat(sd)).days + 1 > CALENDAR_MAX_DAYS:
                ed = (date_cls.fromisoformat(sd) + timedelta(days=CALENDAR_MAX_DAYS - 1)).isoformat()
            result = await _tool_calendar_text(session, sd, ed)
        elif name == "calc_price_stats":
            raw_insts = tool_input.get("instruments")
            insts = [raw_insts] if isinstance(raw_insts, str) else [str(x) for x in (raw_insts or [])]
            try:
                sess = int(tool_input.get("sessions") or STATS_DEFAULT_SESSIONS)
            except (TypeError, ValueError):
                sess = STATS_DEFAULT_SESSIONS
            raw_start = tool_input.get("start_date")
            start_d = str(raw_start) if raw_start and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(raw_start)) else None
            end_d = target_date
            if start_d and start_d > end_d:
                start_d, end_d = end_d, start_d
            result = await _tool_price_stats_text(session, insts, end_d, start_d, max(2, min(sess, PRICE_HISTORY_MAX_SESSIONS)))
        elif name == "list_reports":
            try:
                lim = int(tool_input.get("limit") or 10)
            except (TypeError, ValueError):
                lim = 10
            result = await _tool_list_reports_text(session, max(1, min(lim, 60)))
        elif name == "browse_news":
            topic = tool_input.get("topic")
            region = tool_input.get("region")
            try:
                lim = int(tool_input.get("limit") or 15)
            except (TypeError, ValueError):
                lim = 15
            result = await _tool_browse_news_text(
                session, target_date,
                topic if topic in _TOPIC_VALUES else None,
                region if region in ("vietnam", "international") else None,
                bool(tool_input.get("hot_only")), max(1, min(lim, 30)),
            )
        elif name == "get_article":
            try:
                aid = int(tool_input["article_id"]) if tool_input.get("article_id") is not None else None
            except (TypeError, ValueError):
                aid = None
            result = await _tool_article_text(session, aid, tool_input.get("url"))
        elif name == "get_biz_suggestions":
            status = str(tool_input.get("status") or "active")
            kind = str(tool_input.get("kind") or "all")
            if status not in ("active", "pending", "triggered", "contradicted", "dismissed", "all"):
                status = "active"
            if kind not in ("short", "long", "all"):
                kind = "all"
            try:
                days = int(tool_input.get("days") or 10)
            except (TypeError, ValueError):
                days = 10
            result = await _tool_biz_suggestions_text(session, target_date, status, kind, max(1, min(days, 30)))
        elif name == "get_price_history":
            raw_sessions = tool_input.get("sessions")
            try:
                sessions = int(raw_sessions) if raw_sessions else PRICE_HISTORY_DEFAULT_SESSIONS
            except (TypeError, ValueError):
                sessions = PRICE_HISTORY_DEFAULT_SESSIONS
            sessions = max(1, min(sessions, PRICE_HISTORY_MAX_SESSIONS))
            raw_start = tool_input.get("start_date")
            start_date = str(raw_start) if raw_start and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(raw_start)) else None
            if start_date and start_date > target_date:
                start_date, target_date = target_date, start_date
            result = await _tool_price_history_text(
                session, str(tool_input.get("instrument", "")), target_date, sessions,
                requested_date=None if start_date else raw_date, start_date=start_date,
            )
        elif name == "search_news":
            if retrieval_service is None:
                return "Tool search_news hiện không khả dụng trong phiên này."
            # search_news: KHÔNG truyền date -> tìm KHÔNG giới hạn ngày (khác các tool giá/report ở
            # trên, nơi không truyền date nghĩa là "dùng report_date") — date sai định dạng thì bỏ
            # qua, coi như không giới hạn ngày thay vì áp nhầm report_date.
            result = await _tool_search_news_text(retrieval_service, str(tool_input.get("query", "")), date=raw_date)
        elif name == "fetch_user_source":
            result = await _tool_fetch_user_source_text(str(tool_input.get("url", "")))
        else:
            return f"Tool không xác định: {name}"
    except Exception:
        logger.exception("[QUOTE-CHAT] Lỗi khi thực thi tool %s", name)
        return "Đã xảy ra lỗi khi tra cứu dữ liệu này — trả lời dựa trên thông tin đã có, có thể nói rõ không tra cứu được nếu cần."

    if invalid_date_note:
        fallback_desc = f"theo ngày báo cáo đang xem ({report_date})" if name in _DATE_ANCHORED_TOOLS else "KHÔNG giới hạn ngày"
        result = (
            f"LƯU Ý: tham số date=\"{invalid_date_note}\" không đúng định dạng YYYY-MM-DD nên bị bỏ qua — "
            f"dữ liệu dưới đây lấy {fallback_desc}, KHÔNG phải ngày đã yêu cầu. "
            "Gọi lại tool với date đúng định dạng YYYY-MM-DD nếu vẫn cần đúng ngày đó.\n" + result
        )

    tool_cache[cache_key] = result
    return result


async def _stream_anthropic(
    static_instructions: str,
    dynamic_context: str,
    messages: List[dict],
    model: str,
    *,
    session: Optional[AsyncSession] = None,
    report_date: str = "",
    enable_web_search: bool = False,
    enable_client_tools: bool = False,
    retrieval_service: Optional[RetrievalService] = None,
) -> AsyncIterator[str]:
    """PROMPT CACHING (2 breakpoint, tối đa cho phép là 4):
    1. `system` tách 2 block — `static_instructions` (role/kiến thức/quy tắc,
       giống hệt nhau ở MỌI request cùng report_date) đánh cache_control TTL
       1h (traffic có thể thưa nên ưu tiên giữ cache lâu hơn mặc định 5 phút);
       `dynamic_context` (quote+dữ liệu nền, đổi mỗi câu hỏi) đứng SAU, KHÔNG
       cache — vì cache là khớp PREFIX, cái đổi phải luôn nằm ở CUỐI.
    2. Nếu phiên đã có lịch sử (`messages` dài hơn 1, tức có ít nhất 1 lượt cũ
       + câu hỏi mới), đánh thêm breakpoint ở tin nhắn lịch sử CUỐI CÙNG — các
       câu hỏi tiếp theo trong CÙNG phiên tái dùng lại đúng prefix hội thoại đã
       cache thay vì trả tiền đầy đủ lại từ đầu mỗi lượt.

    LƯU Ý: model mặc định của Quote Chat (Sonnet 5) yêu cầu prefix tối thiểu
    1024 token mới thực sự được cache (thấp hơn Haiku — Haiku cần tới 4096,
    nên nếu đổi QUOTE_CHAT_MODEL sang Haiku, kiểm tra lại ngưỡng này). Nếu
    prompt tĩnh không đủ dài, cache_control bị bỏ qua ÂM THẦM (không lỗi, chỉ
    đơn giản `cache_creation_input_tokens: 0`). Kiểm tra hiệu quả thật qua
    `response.usage.cache_read_input_tokens` trong log, không mặc định là có
    tác dụng chỉ vì code đúng cú pháp.

    VÒNG LẶP TOOL (chỉ khi `enable_client_tools=True`): web_search là SERVER
    tool — Anthropic tự thực thi trong CÙNG 1 lượt stream, không cần can thiệp.
    CLIENT_TOOLS (get_market_prices/get_eua_details/...) thì KHÔNG — model chỉ
    trả về yêu cầu gọi tool (`stop_reason == "tool_use"`), CODE này phải tự
    thực thi (`_execute_client_tool`, query DB), nối kết quả vào `messages`
    dưới dạng `tool_result`, rồi gọi lại model — lặp tối đa
    `MAX_TOOL_ITERATIONS` lượt để tránh lặp vô hạn. Chỉ TEXT DELTA được yield
    ra ngoài (qua `stream.text_stream`) — bản thân tool_use/tool_result không
    hiển thị cho người dùng, dù model có thể xen text trước/sau lúc gọi tool.
    """
    client = _get_anthropic_client()
    tools: List[dict] = []
    if enable_client_tools:
        tools.extend(CLIENT_TOOLS)
    if enable_web_search:
        tools.append(WEB_SEARCH_TOOL)
    extra = {"tools": tools} if tools else {}

    system = [
        {"type": "text", "text": static_instructions, "cache_control": {"type": "ephemeral", "ttl": "1h"}},
        {"type": "text", "text": dynamic_context},
    ]

    # Cache trong PHẠM VI 1 câu hỏi (1 lần gọi hàm này) — xem docstring
    # `_execute_client_tool`/`_get_eua_chart_data_buffered`. KHÔNG cache xuyên
    # các câu hỏi khác nhau (mỗi câu hỏi mới = 1 lần gọi `_stream_anthropic`
    # mới = cache rỗng lại).
    tool_cache: Dict[tuple, str] = {}
    chart_cache: Dict[str, List[Any]] = {}

    # Gom lại để log 1 dòng TỔNG KẾT duy nhất khi câu hỏi này kết thúc (dù kết thúc ở nhánh nào —
    # refusal/max_tokens/bình thường/hết MAX_TOOL_ITERATIONS) — `tools_used` là TÊN tool theo đúng
    # thứ tự gọi (không dedupe, để thấy rõ nếu model lỡ gọi lặp lại dù đã được dặn không nên), `usage`
    # cộng dồn `input_tokens`/`output_tokens`/`cache_creation_input_tokens`/`cache_read_input_tokens`
    # qua TỪNG lượt gọi Anthropic của câu hỏi này (mỗi lượt tool là 1 lần gọi riêng, cache_read cao
    # tức cache đang có tác dụng thật, không chỉ đúng cú pháp — xem docstring đầu hàm).
    tools_used: List[str] = []
    usage_totals = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}

    def _log_usage_summary(outcome: str) -> None:
        logger.info(
            "[QUOTE-CHAT] Câu hỏi kết thúc (%s) sau %d lượt gọi API — tools đã dùng: %s — "
            "token: input=%d output=%d cache_creation=%d cache_read=%d",
            outcome, iteration, tools_used or "(không tool nào)",
            usage_totals["input_tokens"], usage_totals["output_tokens"],
            usage_totals["cache_creation_input_tokens"], usage_totals["cache_read_input_tokens"],
        )

    anthropic_messages = list(messages)
    if len(anthropic_messages) > 1:
        last_history_turn = anthropic_messages[-2]
        anthropic_messages[-2] = {
            "role": last_history_turn["role"],
            "content": [
                {"type": "text", "text": last_history_turn["content"], "cache_control": {"type": "ephemeral"}}
            ],
        }

    # Đếm số lượt gọi API thật (KHÔNG tính lượt tool execute) đã dùng cho câu hỏi này — mỗi lượt là
    # 1 round-trip đầy đủ tới Anthropic, nguồn chính gây chậm khi model gọi nhiều tool tuần tự. Log
    # lại lúc trả lời xong để đo hiệu quả thật của việc siết "không gọi tool khi không cần" ở
    # data_block, thay vì đoán mò — xem `iteration` tăng dần bên dưới.
    for iteration in range(1, MAX_TOOL_ITERATIONS + 1):
        # Stream trực tiếp (yield ngay khi có delta) — ưu tiên UX real-time.
        # ĐÁNH ĐỔI ĐÃ CHỌN: nếu model lỡ chèn text tường thuật trước/xen giữa
        # lúc gọi tool (vi phạm rule 6, xem prompt), phần đó vẫn hiện cho user
        # NGAY LÚC SINH RA — không có cơ chế "thu hồi" ở đây (cần FE hỗ trợ 1
        # sự kiện SSE mới để xoá text đã hiện, chưa làm). Giảm thiểu rủi ro
        # này bằng prompt đã siết chặt (cấm rõ ràng, kèm ví dụ cụ thể) thay vì
        # chặn cứng ở tầng code — xem log "[QUOTE-CHAT] Model chèn text..."
        # nếu cần theo dõi model có còn vi phạm không.
        # `temperature`/`top_p`/`top_k` đã bị loại bỏ khỏi API cho Sonnet 5 (và cả
        # dòng model 4.6+) — truyền lên sẽ bị lỗi 400 "temperature is deprecated
        # for this model". Không có tham số sampling thay thế; nếu cần giảm biến
        # thiên câu trả lời thì điều chỉnh qua system prompt.
        # `output_config.effort` (mặc định "high" nếu bỏ qua) — Sonnet 5 tự chạy adaptive thinking
        # NGAY CẢ KHI KHÔNG truyền `thinking` (khác Opus 4.7/4.8, nơi bỏ qua = tắt thinking), nên nếu
        # không hạ effort thì MỌI câu trả lời — kể cả 1 câu chat ngắn theo đúng rule 6 — đều tốn thời
        # gian "suy nghĩ" ở mức effort cao (tính vào output_tokens, dù `thinking.display` mặc định
        # "omitted" nên không hiển thị). Quote Chat là chat ngắn, độ trễ nhạy cảm (user chờ trực
        # tiếp) — đúng loại việc Anthropic khuyến nghị effort thấp hơn ("chat/latency-sensitive
        # routes often do well at low, medium as cost-saving step-down"), KHÔNG phải loại việc cần
        # effort cao (coding/agentic dài hơi). "medium" chọn làm mặc định thay vì "low" vì đây vẫn là
        # phân tích chuyên môn tài chính/carbon, ưu tiên an toàn hơn tốc độ tối đa — có thể hạ xuống
        # "low" nếu đo thực tế thấy chất lượng vẫn ổn.
        async with client.messages.stream(
            model=model,
            max_tokens=MAX_ANSWER_TOKENS,
            system=system,
            messages=anthropic_messages,
            output_config={"effort": "low"},
            **extra,
        ) as stream:
            leaked_chars = 0
            async for text in stream.text_stream:
                leaked_chars += len(text)
                yield text
            final_message = await stream.get_final_message()

        msg_usage = getattr(final_message, "usage", None)
        if msg_usage is not None:
            for key in usage_totals:
                usage_totals[key] += getattr(msg_usage, key, None) or 0

        # `stop_reason == "refusal"` (HTTP 200, KHÔNG phải exception — bộ lọc an toàn của model từ
        # chối trả lời, vd nội dung nhắc xung đột địa chính trị dù trong ngữ cảnh phân tích thị
        # trường thuần tuý) trước đây bị coi như "end_turn" bình thường ở nhánh `return` ngay dưới —
        # `stream.text_stream` hầu như không phát ký tự nào khi bị từ chối, nên hàm return LẶNG LẼ
        # không yield gì cả: không exception (log sạch), HTTP vẫn 200, nhưng user nhận được câu trả
        # lời RỖNG — đúng triệu chứng "quote chat không trả lời gì" dù log server không có lỗi.
        if final_message.stop_reason == "refusal":
            stop_details = getattr(final_message, "stop_details", None)
            logger.warning(
                "[QUOTE-CHAT] Model từ chối trả lời (refusal, category=%s) — không trả về nội dung nào cho user.",
                getattr(stop_details, "category", None),
            )
            yield (
                "Hệ thống không thể trả lời trực tiếp câu hỏi này (bộ lọc an toàn của model đánh giá nội dung "
                "nhạy cảm, dù đây chỉ là phân tích thị trường) — bạn thử diễn đạt lại câu hỏi tập trung vào số "
                "liệu/tác động giá thay vì mô tả sự kiện, hoặc hỏi lại theo cách khác giúp mình nhé."
            )
            _log_usage_summary("refusal")
            return

        # `stop_reason == "max_tokens"`: câu trả lời bị CẮT CỤT giữa chừng vì chạm
        # `MAX_ANSWER_TOKENS` — text đã stream ra (đoạn dang dở) vẫn hiển thị cho user, nhưng nếu
        # không báo gì thêm thì y hệt "trả lời được 1 đoạn rồi ngừng" không rõ lý do. Trường hợp này
        # KHÔNG tự động gọi tiếp (continue) vì model đã hiểu sai độ dài — nói rõ hơn là hữu ích hơn
        # cố "vá" 1 câu trả lời đã lỡ quá dài.
        if final_message.stop_reason == "max_tokens":
            logger.warning("[QUOTE-CHAT] Câu trả lời chạm giới hạn MAX_ANSWER_TOKENS=%d, bị cắt cụt.", MAX_ANSWER_TOKENS)
            yield "\n\n[Câu trả lời đã bị cắt do quá dài — bạn có thể hỏi lại \"tiếp tục\" hoặc yêu cầu tóm tắt ngắn gọn hơn.]"
            _log_usage_summary("max_tokens")
            return

        if final_message.stop_reason != "tool_use":
            _log_usage_summary("answered")
            return

        if leaked_chars:
            logger.warning(
                "[QUOTE-CHAT] Model chèn %d ký tự text trước/xen giữa lúc gọi tool — đã hiện cho user (chưa có cơ chế thu hồi).",
                leaked_chars,
            )

        client_tool_calls = [b for b in final_message.content if b.type == "tool_use"]
        if not client_tool_calls:
            _log_usage_summary("answered_no_tool_calls")
            return
        tools_used.extend(call.name for call in client_tool_calls)

        # Giữ NGUYÊN các content block object trả về (KHÔNG tự model_dump()) —
        # `stream.get_final_message()` trả về block đã bị lớp streaming của SDK
        # gắn thêm field tiện ích nội bộ (vd "parsed_output" trên text block,
        # xem anthropic/types/parsed_message.py::ParsedTextBlock) mà input
        # schema từ chối ("Extra inputs are not permitted") nếu tự dump thô.
        # SDK tự loại field đó (theo `__api_exclude__`) khi encode request nếu
        # ta truyền thẳng object — không cần tự serialize lại.
        anthropic_messages.append({"role": "assistant", "content": final_message.content})
        # CHỦ Ý chạy TUẦN TỰ (không asyncio.gather) dù model có thể gộp nhiều tool_use vào CÙNG 1
        # lượt: mọi tool ở đây dùng CHUNG 1 `session: AsyncSession` (session theo request, xem
        # api/deps.py::get_db) — AsyncSession KHÔNG an toàn khi bị gọi đồng thời từ nhiều coroutine
        # (SQLAlchemy tự raise `IllegalStateChangeError`/lỗi tương tự nếu 2 coroutine cùng
        # `session.execute()` trên 1 session đang có 1 lệnh khác chưa xong) — kể cả `search_news` qua
        # `retrieval_service` cũng dùng lại session này. Chạy song song ở đây sẽ CRASH thay vì nhanh
        # hơn. Muốn song song thật sự cần refactor cấp router để mỗi tool tự mở session riêng từ 1
        # sessionmaker (như crawl_news/pipeline.py đang làm), không hợp lý để đổi riêng ở đây.
        tool_results = []
        for call in client_tool_calls:
            result_text = await _execute_client_tool(
                call.name, call.input, session, report_date,
                tool_cache=tool_cache, chart_cache=chart_cache, retrieval_service=retrieval_service,
            )
            tool_results.append({"type": "tool_result", "tool_use_id": call.id, "content": result_text})
        anthropic_messages.append({"role": "user", "content": tool_results})

    # Hết MAX_TOOL_ITERATIONS mà vẫn chưa có lượt nào kết thúc bằng câu trả lời
    # thật (luôn `return` ngay khi stop_reason != "tool_use" ở trên) — nếu cứ
    # để hàm kết thúc lặng lẽ, user sẽ nhận được "done" SSE với answer RỖNG,
    # trông như hệ thống không phản hồi gì mà không rõ lý do. Trả về 1 câu xin
    # lỗi cụ thể thay vì im lặng — router lưu câu này vào lịch sử chat như câu
    # trả lời bình thường (không phải "error" SSE, vì đây không phải exception).
    logger.warning("[QUOTE-CHAT] Đạt giới hạn %d lượt gọi tool liên tiếp — dừng vòng lặp.", MAX_TOOL_ITERATIONS)
    _log_usage_summary("max_iterations")
    yield (
        "Câu hỏi này cần tra cứu nhiều dữ liệu hơn mức xử lý được trong 1 lượt — "
        "bạn có thể hỏi lại với câu hỏi cụ thể/ngắn gọn hơn giúp mình không?"
    )


async def astream_quote_chat(
    *,
    quote: str,
    question: str,
    report_date: str,
    history: Sequence[ChatTurn],
    context_chunks: Sequence[RetrievedDocument],
    session: AsyncSession,
    retrieval_service: Optional[RetrievalService] = None,
    eua_framework_overrides: Optional[Dict[str, str]] = None,
    few_shot_block: str = "",
    attachments: Optional[Sequence[Attachment]] = None,
) -> AsyncIterator[str]:
    """Stream câu trả lời — yield từng đoạn text nhỏ (delta). Luôn dùng backend
    Anthropic (client tools + server tool web_search) — Cohere trong repo này
    chỉ dùng cho embedding/rerank (kể cả tool search_news bên dưới), không
    còn dùng cho chat/completion.

    `attachments`: file đính kèm (ảnh/PDF/Word) CỦA CÂU HỎI NÀY — xem
    `_build_attachment_content_blocks`. Chỉ áp dụng cho message user MỚI NHẤT
    (không tiêm lại vào các lượt lịch sử cũ trong `history`), tránh resend
    ảnh/PDF nặng vào context ở mọi câu hỏi tiếp theo trong cùng phiên.

    KHÔNG nhận `prices_text`/`report_text` đã fetch sẵn — model TỰ GỌI TOOL
    (get_market_prices/get_eua_details/get_eua_volume_history/get_price_history/
    get_report_section/search_news, xem CLIENT_TOOLS) khi câu hỏi thực sự cần,
    thay vì luôn fetch + tiêm sẵn vào MỌI request như trước (tốn context ngay
    cả với câu hỏi thuần suy luận/giải thích không cần số liệu). `session`
    truyền xuống để tool tự query DB khi được model gọi.

    `retrieval_service`: cùng instance router đã dùng cho
    `retrieve_context_for_quote()` (DỮ LIỆU NỀN ban đầu) — truyền thêm xuống
    đây để tool `search_news` có thể CHỦ ĐỘNG tìm lại giữa vòng lặp hội thoại
    (query/ngày khác), không giới hạn ở đúng 1 lần retrieve lúc đầu câu hỏi.
    None = tắt tool search_news (không có cách nào thực thi).

    `eua_framework_overrides`: nội dung admin đã custom cho khung tri thức EUA
    (xem services/eua_framework_admin.py::get_overrides_map), lấy 1 lần ở
    router rồi truyền xuống đây — None = dùng toàn bộ bản mặc định trong code.

    `few_shot_block`: khối ví dụ mẫu admin đã chọn lọc (xem
    services/quote_chat_examples.py::build_few_shot_prompt_block), lấy 1 lần ở
    router — rỗng nếu chưa có ví dụ nào.
    """
    settings = Settings.from_env()
    truncated_quote = _truncate(quote, MAX_QUOTE_CHARS)
    context_block = _format_context(context_chunks, report_date)

    messages = [{"role": turn.role, "content": turn.content} for turn in history]
    attachment_blocks = await _build_attachment_content_blocks(attachments)
    if attachment_blocks:
        messages.append({"role": "user", "content": [*attachment_blocks, {"type": "text", "text": question}]})
    else:
        messages.append({"role": "user", "content": question})

    # Tách static/dynamic (thay vì ghép sẵn thành 1 chuỗi) để bật prompt
    # caching — xem docstring _stream_anthropic.
    stream = _stream_anthropic(
        _build_static_instructions(report_date, eua_framework_overrides, few_shot_block),
        _build_dynamic_context(truncated_quote, context_block),
        messages,
        settings.quote_chat_model,
        session=session,
        report_date=report_date,
        enable_web_search=True,
        enable_client_tools=True,
        retrieval_service=retrieval_service,
    )

    async for delta in stream:
        yield delta


# ─────────────────────────────────────────────────────────────────────
# Suggested questions — heuristic, không gọi LLM (cần hiện tức thời)
# ─────────────────────────────────────────────────────────────────────

_KEYWORD_QUESTIONS: List[tuple[str, str]] = [
    # Câu hỏi thực tế + suy luận cho EUA/ETS
    (r"EUA|ETS|hạn ngạch", "Vì sao diễn biến này tác động đến giá EUA?"),
    (r"EUA|ETS|hạn ngạch", "Nếu xu hướng này tiếp tục, giá EUA sẽ chịu áp lực theo hướng nào?"),
    (r"EUA|ETS|hạn ngạch|volume|khối lượng", "Khối lượng giao dịch EUA phiên gần nhất có xác nhận xu hướng giá không?"),
    (r"EUA|ETS|hạn ngạch|kỹ thuật|kháng cự|hỗ trợ|chốt lời|bắt đáy", "Mốc kháng cự/hỗ trợ kỹ thuật của EUA hiện ở đâu?"),

    # CBAM — đặc biệt quan trọng cho DN Việt Nam
    (r"CBAM", "CBAM ảnh hưởng thế nào đến doanh nghiệp xuất khẩu Việt Nam?"),
    (r"CBAM", "Nếu EU mở rộng phạm vi CBAM, ngành nào ở VN bị ảnh hưởng nặng nhất?"),

    # MSR
    (r"MSR", "Cơ chế MSR sẽ can thiệp vào nguồn cung EUA như thế nào?"),
    (r"MSR", "Nếu TNAC giảm xuống dưới ngưỡng, MSR sẽ hoạt động ra sao?"),

    # Gas/TTF — fuel switching
    (r"TTF|khí đốt|\bgas\b|LNG", "Diễn biến giá khí đốt liên quan gì đến giá điện/than và EUA?"),
    (r"TTF|khí đốt|\bgas\b|LNG", "Nếu giá gas tiếp tục tăng, fuel switching sẽ ảnh hưởng EUA thế nào?"),

    # Than
    (r"than\b|coal|API2|NEWC", "Vì sao giá than lại ảnh hưởng đến phát thải và giá EUA?"),
    (r"than\b|coal|API2|NEWC", "Trong kịch bản gas đắt hơn, vai trò của than trong phát điện thay đổi ra sao?"),

    # Dầu
    (r"dầu\b|Brent|WTI|oil|gasoil", "Giá dầu tác động thế nào đến chi phí sản xuất và phát thải?"),
    (r"dầu\b|Brent|WTI|oil|gasoil|crack spread", "Crack spread mở rộng/thu hẹp có ý nghĩa gì với nhu cầu EUA?"),

    # VCM
    (r"VCM|tín chỉ carbon tự nguyện|Verra|Gold Standard", "Thị trường tín chỉ carbon tự nguyện (VCM) khác gì EU ETS?"),
    (r"VCM|tín chỉ carbon tự nguyện|Article 6", "Nếu Article 6 được triển khai rộng, VCM sẽ thay đổi thế nào?"),

    # Chính sách
    (r"chính sách|policy|quy định|Fit-for-55|luật", "Chính sách này có thể thay đổi hay bị trì hoãn không?"),
    (r"chính sách|policy|quy định", "Nếu chính sách này được thông qua, tác động đến giá EUA theo chuỗi nào?"),

    # Năng lượng tái tạo
    (r"gió|mặt trời|tái tạo|renewable|RES|solar|wind", "Nếu công suất tái tạo tăng mạnh, EUA sẽ bị ảnh hưởng thế nào?"),

    # Điện Đức
    (r"điện|power|DEBY|merit order", "Mối liên hệ hai chiều giữa giá điện Đức và EUA hoạt động thế nào?"),

    # Địa chính trị
    (r"Nga|Ukraine|Trung Đông|xung đột|chiến tranh|trừng phạt", "Kịch bản leo thang xung đột sẽ tác động đến thị trường năng lượng & EUA ra sao?"),

    # Việt Nam
    (r"Việt Nam|VETS|NDC|Nghị định|thị trường carbon VN", "Việt Nam đang ở đâu trong lộ trình phát triển thị trường carbon?"),

    # Số liệu
    (r"\d", "Con số này so với xu hướng gần đây thế nào?"),
]

_GENERIC_QUESTIONS = [
    "Giải thích ngắn gọn ý nghĩa của đoạn này?",
    "Điều này có thể ảnh hưởng thế nào đến doanh nghiệp Việt Nam?",
    "Chuỗi nhân quả tác động đến giá EUA ở đây là gì?",
    "Trong kịch bản xấu nhất, điều gì sẽ xảy ra?",
]

# Gợi ý khi chat tự do (không bôi đen đoạn nào) — hỏi về báo cáo/thị trường nói chung.
_FREE_CHAT_QUESTIONS = [
    "Tóm tắt nhanh diễn biến giá EUA hôm nay?",
    "Yếu tố nào đang tác động mạnh nhất đến giá EUA?",
    "Doanh nghiệp Việt Nam cần lưu ý gì từ báo cáo hôm nay?",
    "Kịch bản giá EUA trong 1–2 tuần tới thế nào?",
]


def suggest_questions(quote: str, limit: int = 4) -> List[str]:
    """Trả về vài câu hỏi phổ biến gợi ý cho đoạn quote — ưu tiên câu khớp từ khoá
    trong quote, bù thêm câu hỏi chung nếu chưa đủ `limit`. Quote rỗng (chat tự do)
    → câu hỏi chung về báo cáo."""
    if not quote.strip():
        return _FREE_CHAT_QUESTIONS[:limit]
    matched = [q for pattern, q in _KEYWORD_QUESTIONS if re.search(pattern, quote, re.IGNORECASE)]

    questions: List[str] = []
    for q in matched + _GENERIC_QUESTIONS:
        if q not in questions:
            questions.append(q)
        if len(questions) >= limit:
            break
    return questions
