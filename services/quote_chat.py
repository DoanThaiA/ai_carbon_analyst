
import logging
import re
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import Settings
from db.models import Report
from schemas.chat_models import ChatTurn
from schemas.retrieval_models import RetrievedDocument
from services.retrieval import RetrievalService
from services import eua_causal_chains as chains
from services.report_generator import (
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
MAX_ANSWER_TOKENS = 1000  # chặn cứng độ dài — bổ trợ cho rule ngắn gọn trong system prompt

# Chặn trên độ dài text của TOÀN VĂN báo cáo sau khi format (xem
# `_format_report_content`) — báo cáo thật thường không tới ngưỡng này (~9 mục,
# mục 6 nhiều nhất cũng chỉ tới 60 bài tóm tắt 2-4 câu), đây chỉ là lưới an
# toàn tránh 1 báo cáo bất thường dài làm phình prompt không kiểm soát.
MAX_REPORT_CHARS = 40000

# Ngưỡng relevance_score (thang 0-1 của Cohere rerank) để 1 chunk được coi là
# THẬT SỰ liên quan tới quote+câu hỏi — top_k chỉ giới hạn số lượng, không đảm
# bảo độ liên quan (rerank vẫn trả đủ top_k chunk điểm thấp nếu không có chunk
# nào thật sự khớp). Theo tài liệu Cohere: điểm ~>0.5 là liên quan mạnh, <0.1
# gần như không liên quan — chọn 0.3 làm ngưỡng vừa phải: đủ để loại chunk lạc
# đề, nhưng không quá gắt tới mức loại luôn cả chunk liên quan gián tiếp (tin
# tức ít khi trùng khớp từ khoá 100% với câu hỏi tự do của người dùng). Tuỳ
# chỉnh dựa trên log "[RETRIEVAL] Lọc ngưỡng relevance_score..." nếu cần.
MIN_RERANK_SCORE = 0.5

# Lazy singleton clients — tránh crash khi import module lúc chưa có .env (giống
# pattern report_generator.py / embedding.py). Backend chọn qua QUOTE_CHAT_BACKEND
# (mặc định "anthropic" — có web_search; đổi "cohere" nếu muốn dùng Cohere thay thế).
_cohere_client = None
_anthropic_client = None


def _get_cohere_client():
    global _cohere_client
    if _cohere_client is None:
        import cohere

        api_key = Settings.from_env().cohere_api_key
        if not api_key:
            raise RuntimeError("COHERE_API_KEY chưa được cấu hình.")
        _cohere_client = cohere.AsyncClientV2(api_key=api_key)
    return _cohere_client


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
    `get_prices_text_for_chat`) — nếu không, các phiên CŨ NHẤT trong bảng hiển
    thị sẽ thiếu dữ liệu phiên trước để tính đủ TB 20 phiên, cho kết quả TB
    thấp hơn thực tế (chỉ tính được trên ít phiên hơn).
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


async def get_prices_text_for_chat(session: AsyncSession, report_date: str) -> str:
    """Lấy dữ liệu giá (đóng cửa + Δ ngày/Δ tuần) của ngày báo cáo — CÙNG nguồn dữ
    liệu report_generator.py dùng để sinh báo cáo gốc.

    Trước khi có hàm này, quote_chat.py KHÔNG có quyền truy cập bảng giá — chỉ có
    đoạn quote + RAG trên tin tức — nên khi người dùng hỏi về giá 1 mã không xuất
    hiện nguyên số trong quote/tin tức retrieve được, model buộc phải viết mơ hồ
    kiểu "tín hiệu chưa dứt khoát" vì không có số cụ thể để bám. Dùng đúng
    `get_prices_for_report`/`_summarize_prices` của report_generator.py (không tự
    viết lại logic định dạng) để câu trả lời của chat khớp đúng số liệu report
    gốc đã dùng, không lệch nhau giữa 2 nơi.

    Nối thêm dòng OHLC (mở/cao/thấp/đóng) phiên liền trước của EUA — CÙNG
    `_eua_session_range_summary` mà report_generator.py dùng cho Mục 1 —
    để người dùng có thể hỏi trực tiếp giá mở cửa/cao/thấp trong phiên, không
    chỉ giá đóng cửa + Δ ngày/Δ tuần như `_summarize_prices` ở trên.

    Nối thêm dòng khối lượng giao dịch EUA (phiên liền trước so với TB các phiên
    gần nhất — CÙNG `_eua_volume_summary` mà report_generator.py dùng cho Mục 2)
    để người dùng có thể hỏi trực tiếp về volume EUA, không chỉ giá/Δ ngày/Δ tuần.

    Nối thêm dòng mốc kỹ thuật EUA (kháng cự/điểm chốt lời + mục tiêu nếu phá
    vỡ, hỗ trợ/điểm bắt đáy — `_eua_technical_levels_summary`, tính từ đỉnh/đáy
    30 phiên gần nhất CÙNG bộ OHLC vừa lấy ở trên) — CHỈ cho EUA theo yêu cầu,
    không mở rộng sang 5 instrument còn lại.

    Nối thêm BẢNG khối lượng EUA theo từng phiên (`_eua_volume_history_text`) —
    dòng volume ở trên chỉ tóm tắt phiên liền trước report_date so với TB,
    không đủ để trả lời câu hỏi so sánh khối lượng GIỮA CÁC NGÀY CỤ THỂ. Fetch
    `chart_data` với `limit` LỚN HƠN 30 (thêm đệm `EUA_VOLUME_SESSIONS_FOR_AVG`
    phiên phía trước) rồi CẮT lại đúng 30 phiên cuối cho OHLC/volume/technical
    ở trên (giữ nguyên ý nghĩa "30 phiên gần nhất" như report gốc) — phần đệm
    chỉ dùng riêng cho `_eua_volume_history_text` để tính đúng TB 20 phiên
    trước cho cả những phiên CŨ NHẤT trong bảng hiển thị, không bị thiếu dữ
    liệu do giới hạn fetch.
    """
    prices, _ = await get_prices_for_report(session, report_date)
    prices_text = _summarize_prices(prices)
    chart_data_buffered = await get_historical_ohlc_for_report(
        session, "EUA", report_date, limit=EUA_VOLUME_HISTORY_SESSIONS + EUA_VOLUME_SESSIONS_FOR_AVG
    )
    chart_data = chart_data_buffered[-30:]  # 30 phiên gần nhất — khớp mặc định của report_generator.py
    ohlc_text = _eua_session_range_summary(chart_data)
    volume_text = _eua_volume_summary(chart_data)
    technical_text = _eua_technical_levels_summary(chart_data)
    volume_history_text = _eua_volume_history_text(chart_data_buffered)
    return (
        f"{prices_text}\n  - {ohlc_text}\n  - {volume_text}\n  - {technical_text}\n\n"
        f"{volume_history_text}"
    )


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
Đây là bản RÚT GỌN của KHUNG PHÂN TÍCH CHUẨN (dùng chung với báo cáo Mục 3/5) — PHẢI bám sát
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
# Toàn văn báo cáo (Mục 1-3) — trước đây quote_chat CHỈ có đoạn quote + RAG
# trên tin tức, không có quyền truy cập các mục KHÁC của báo cáo, nên quote 1
# gạch đầu dòng nhỏ ở Mục 1 rồi hỏi về nội dung Mục 2/3 không trả lời được.
# Format phẳng Mục 1 (Tóm tắt điều hành), Mục 2 (Bảng giá nhanh), Mục 3 (Phân
# tích chuyên sâu) của dict content (db/models.py::Report.content, JSONB 9 mục
# — xem services/report_generator.py::generate_report_content) thành text để
# tiêm vào system prompt, CÙNG cơ chế với prices_text (đặt ở static
# instructions vì chỉ đổi 1 lần/ngày theo report_date, tận dụng prompt
# caching). CHỈ 3 mục này theo yêu cầu — các mục 4/5/6/7/8/biz không đưa vào.
# ─────────────────────────────────────────────────────────────────────


def _format_bullets(bullets: Optional[List[Any]]) -> str:
    """1 bullet có thể là string thuần hoặc dict {text, source_name,
    source_url} (Mục 1) — chuẩn hoá về text, kèm tên nguồn nếu có."""
    lines = []
    for b in bullets or []:
        if isinstance(b, dict):
            text = (b.get("text") or "").strip()
            source_name = b.get("source_name")
            lines.append(f"- {text}" + (f" ({source_name})" if source_name else ""))
        elif b:
            lines.append(f"- {b}")
    return "\n".join(lines) if lines else "(không có)"


def _format_report_content(content: Optional[dict]) -> str:
    """Chuyển Mục 1 (Tóm tắt điều hành), Mục 2 (Bảng giá nhanh) và Mục 3
    (Phân tích chuyên sâu) của báo cáo thành text phẳng, dễ đọc cho LLM — CHỈ
    3 mục này theo yêu cầu, các mục còn lại (4/5/6/7/8/biz) KHÔNG đưa vào để
    tránh phình prompt không cần thiết.

    Bỏ qua "prices"/"chart_data" thô của Mục 2 (đã có `prices_text` riêng —
    xem `get_prices_text_for_chat` — tránh trùng lặp dữ liệu trong prompt).
    """
    if not content:
        return "(Không có nội dung báo cáo cho ngày này.)"

    parts: List[str] = []

    sec1 = content.get("1")
    if sec1:
        parts.append(f"## {sec1.get('title', 'Tóm tắt điều hành')}\n{_format_bullets(sec1.get('bullets'))}")

    sec2 = content.get("2")
    if sec2:
        drivers = sec2.get("market_drivers") or {}
        parts.append(
            f"## {sec2.get('title', 'Bảng giá nhanh')}\n"
            f"{sec2.get('key_facts', '')}\n"
            f"Yếu tố hỗ trợ tăng giá:\n{_format_bullets(drivers.get('bullish'))}\n"
            f"Yếu tố hỗ trợ giảm giá:\n{_format_bullets(drivers.get('bearish'))}"
        )

    sec3 = content.get("3")
    if sec3:
        blocks = "\n\n".join(
            f"{b.get('heading', '')}: {b.get('content', '')}" for b in sec3.get("analysis_blocks") or []
        )
        scenarios = "\n".join(
            f"- [{s.get('horizon')}] {s.get('direction')} (xác suất {s.get('probability')}): "
            f"{s.get('condition')} | Vùng giá: {s.get('price_zone')} | Rủi ro: {s.get('key_risk')} | "
            f"Chiến lược: {s.get('trading_strategy')}"
            for s in sec3.get("trading_scenarios") or []
        )
        parts.append(
            f"## {sec3.get('title', 'Phân tích chuyên sâu')}\n{blocks}\n\nKịch bản giao dịch:\n{scenarios or '(không có)'}"
        )

    return _truncate("\n\n".join(parts), MAX_REPORT_CHARS)


async def get_report_text_for_chat(session: AsyncSession, report_date: str) -> str:
    """Lấy + format Mục 1-3 của báo cáo `report_date` (đã published) để tiêm
    vào system prompt — cho phép trả lời câu hỏi liên quan tới nội dung 3 mục
    này ngoài đoạn quote, không chỉ dựa vào đoạn quote + RAG trên tin tức như
    trước đây.

    Đặt cùng nhóm với `get_prices_text_for_chat` (chỉ đổi 1 lần/ngày theo
    report_date, không đổi theo câu hỏi) — dùng ở static instructions để tận
    dụng prompt caching, KHÔNG phải dynamic context.
    """
    stmt = select(Report.content).where(Report.report_date == report_date, Report.status == "published")
    result = await session.execute(stmt)
    content = result.scalar_one_or_none()
    return _format_report_content(content)


# ─────────────────────────────────────────────────────────────────────
# System prompt
# ─────────────────────────────────────────────────────────────────────

def _build_static_instructions(
    report_date: str, prices_text: str, overrides: Optional[Dict[str, str]] = None,
    few_shot_block: str = "", report_text: str = "",
) -> str:
    """Phần system prompt KHÔNG đổi giữa các câu hỏi/phiên/user (chỉ đổi 1
    lần/ngày theo report_date) — role, kiến thức nền, dữ liệu giá, năng lực,
    quy tắc. Tách riêng khỏi `_build_dynamic_context()` để làm prefix
    `cache_control` ổn định cho backend Anthropic (xem PROMPT CACHING trong
    `_stream_anthropic`): nội dung này giống hệt nhau ở MỌI request trong
    cùng report_date, chiếm phần lớn dung lượng prompt, nên cache được sẽ
    tiết kiệm đáng kể input token — miễn là đủ dài hơn ngưỡng cache tối thiểu
    của model đang dùng (xem comment ở nơi gọi).

    `prices_text` (giá đóng cửa + Δ ngày/Δ tuần, CÙNG dữ liệu report_generator.py
    dùng để sinh báo cáo gốc — xem `get_prices_text_for_chat`) đặt Ở ĐÂY, không
    phải trong `_build_dynamic_context()`, vì nó chỉ đổi theo report_date (1
    lần/ngày, giống mọi phần khác của static instructions), không đổi theo
    từng câu hỏi — đặt trong dynamic context sẽ phá cache mỗi request mà không
    có lý do.

    `report_text`: Mục 1-3 của báo cáo ngày `report_date` đã format phẳng
    (xem `get_report_text_for_chat`/`_format_report_content`) — CÙNG lý do đặt
    ở static instructions như `prices_text` (chỉ đổi theo report_date). Giải
    quyết hạn chế trước đây: quote_chat CHỈ biết đoạn quote + RAG trên tin tức,
    không biết nội dung các mục KHÁC của báo cáo, nên quote 1 đoạn nhỏ rồi hỏi
    về phần khác sẽ không trả lời được.

    `few_shot_block`: khối ví dụ mẫu do admin chọn lọc (xem
    services/quote_chat_examples.py::build_few_shot_prompt_block), rỗng nếu
    admin chưa thêm ví dụ nào — đặt cùng phần static vì không đổi theo câu hỏi.
    """
    few_shot_section = f"\n{few_shot_block}\n" if few_shot_block else ""
    return f"""Bạn là chuyên gia phân tích cao cấp của bàn giao dịch năng lượng & carbon (Daily Carbon Intelligence), có kiến thức sâu rộng về EU ETS, thị trường carbon, năng lượng, chính sách khí hậu, và các mối liên hệ liên thị trường. Nhiệm vụ của bạn là giúp người đọc hiểu sâu hơn một đoạn trích cụ thể mà họ vừa bôi đen trong báo cáo ngày {report_date}, thông qua hội thoại hỏi-đáp.

=== DỮ LIỆU GIÁ NGÀY BÁO CÁO {report_date} (đóng cửa + Δ ngày + Δ tuần — CÙNG số liệu report gốc đã dùng) ===
{prices_text}
LƯU Ý BẮT BUỘC: đây là 6 instrument DUY NHẤT hệ thống có dữ liệu giá thật (EUA, TTF/gas, API2/than, Brent, WTI, DEBY1/điện Đức). Khi được hỏi về giá/biến động của 1 trong 6 mã này, PHẢI dùng ĐÚNG số ở trên (Δ ngày/Δ tuần), TUYỆT ĐỐI KHÔNG tự bịa số hay mô tả định tính mơ hồ ("biến động nhẹ", "chưa dứt khoát"...) thay cho con số thật đã có sẵn. Khi được hỏi về giá 1 mã KHÔNG nằm trong danh sách trên (vd giá than cốc, giá kim loại, giá điện nước khác Đức), nói rõ hệ thống không theo dõi giá đó — có thể dùng web_search nếu người dùng cần số liệu cụ thể — KHÔNG suy đoán con số.
LƯU Ý VỀ OHLC: dòng thứ 3 từ cuối ở trên là giá MỞ CỬA/CAO/THẤP/ĐÓNG CỬA (OHLC) phiên liền trước của EUA (tính trực tiếp từ dữ liệu giá thật) — hệ thống CHỈ có OHLC chi tiết theo phiên cho EUA, KHÔNG có cho 5 instrument còn lại (chỉ có giá đóng cửa + Δ ngày/Δ tuần ở bảng trên). Khi được hỏi giá mở cửa/cao nhất/thấp nhất trong phiên của EUA, PHẢI dùng ĐÚNG số ở dòng này, KHÔNG tự bịa hay suy đoán từ Δ ngày/Δ tuần.
LƯU Ý VỀ VOLUME: dòng thứ 2 từ cuối ở trên là khối lượng giao dịch EUA (hợp đồng) phiên liền trước so với TB các phiên gần nhất — hệ thống CHỈ theo dõi volume cho EUA, KHÔNG có volume cho 5 instrument còn lại. Khi được hỏi về khối lượng/volume EUA, PHẢI dùng ĐÚNG con số này (không tự bịa); dùng làm căn cứ suy luận volume có "xác nhận" xu hướng giá hay không (volume tăng cùng chiều giá = tín hiệu mạnh; volume cao nhưng giá đi ngang/ngược chiều, hoặc giá biến động mạnh mà volume thấp = tín hiệu yếu/đáng nghi ngờ) — nhưng đây CHỈ LÀ SUY LUẬN, không phải kết luận chắc chắn.
Ngay sau khối DỮ LIỆU GIÁ ở trên (cách 1 dòng trắng) còn có BẢNG "Khối lượng giao dịch EUA theo từng phiên" — mỗi dòng là 1 ngày, ĐÃ KÈM SẴN mức chênh lệch % so với TB 20 phiên NGAY TRƯỚC ngày đó (tính cố định theo đúng ngày, KHÔNG đổi theo ngày báo cáo {report_date} đang xem — vd hỏi về 1 ngày trong quá khứ ở báo cáo hôm nay vẫn ra đúng số như hỏi ở đúng báo cáo ngày đó). Khi được hỏi về khối lượng 1 ngày cụ thể hoặc SO SÁNH khối lượng GIỮA CÁC NGÀY (vd "hôm nay so với hôm qua/tuần trước/ngày X", "ngày X so với 20 phiên liền trước"), PHẢI tra đúng dòng của ngày đó và DÙNG NGUYÊN con số volume + %chênh lệch đã tính sẵn — TUYỆT ĐỐI KHÔNG tự cộng/chia lại trung bình từ các dòng khác (dễ lệch cửa sổ 20 phiên) và KHÔNG bịa số của ngày không có trong bảng; nếu ngày hỏi không có trong bảng, nói rõ hệ thống không có dữ liệu ngày đó thay vì đoán.
LƯU Ý VỀ MỐC KỸ THUẬT: dòng cuối cùng ở trên là mốc hỗ trợ/kháng cự kỹ thuật của EUA (đỉnh/đáy 30 phiên gần nhất, tính từ giá thật) — hệ thống CHỈ tính mốc kỹ thuật cho EUA, KHÔNG có cho 5 instrument còn lại (nếu được hỏi mốc kỹ thuật của mã khác, nói rõ hệ thống chưa hỗ trợ, KHÔNG tự bịa mốc). Xem chi tiết cách dùng ở mục F (NĂNG LỰC CỦA BẠN) bên dưới.

=== NỘI DUNG MỤC 1-3 CỦA BÁO CÁO NGÀY {report_date} (Tóm tắt điều hành, Bảng giá nhanh, Phân tích chuyên sâu — để trả lời câu hỏi liên quan tới nội dung các mục này ngoài đoạn trích người dùng đang bôi đen) ===
{report_text}
LƯU Ý: đoạn trích người dùng bôi đen (đưa ra bên dưới, ở phần ĐOẠN NGƯỜI DÙNG ĐANG BÔI ĐEN) vẫn là điểm neo của hội thoại, nhưng khi câu hỏi nhắc tới nội dung Ở MỤC 1/2/3 khác đoạn trích (vd "Mục 3 nói gì về...", hoặc chỉ đơn giản hỏi 1 điều không có trong chính đoạn trích nhưng có trong 3 mục này), PHẢI dùng đúng nội dung ở trên để trả lời, KHÔNG được nói "không có thông tin" nếu thông tin đó thực sự có. Nếu câu hỏi liên quan tới mục KHÁC (4/5/6/7/8/biz) không có ở đây, áp dụng rule 4 (THÀNH THẬT VỀ GIỚI HẠN) bên dưới.
LƯU Ý QUAN TRỌNG VỀ ĐOẠN TRÍCH THIẾU NGỮ CẢNH: đoạn trích người dùng bôi đen là 1 câu/gạch đầu dòng CẮT RA từ nội dung MỤC 1-3 ở trên — nên có thể là 1 câu KẾT LUẬN đứng riêng, chứa đại từ/cụm quy chiếu không tự giải thích được nếu tách rời khỏi phần trước nó (vd "nhóm này", "yếu tố này", "xu hướng này", "kịch bản này", "điều này"...). Khi gặp trường hợp này: TRƯỚC TIÊN, tìm đúng vị trí của đoạn trích trong nội dung MỤC 1-3 ở trên (khớp gần đúng câu chữ, kể cả khi đoạn trích không dấu câu/khoảng trắng y hệt), đọc các câu/gạch đầu dòng ngay TRƯỚC nó trong cùng mục để xác định chính xác đại từ/cụm đó đang chỉ tới cái gì (vd "nhóm này" = nhóm yếu tố nào vừa được liệt kê ngay trước), rồi trả lời DỰA TRÊN nghĩa đã giải quyết đó — nêu rõ luôn đối tượng cụ thể trong câu trả lời (vd viết "Gas → EUA tạo áp lực tăng..." thay vì lặp lại mơ hồ "nhóm này"). TUYỆT ĐỐI KHÔNG trả lời chung chung hay hỏi ngược người dùng "nhóm nào" khi ngữ cảnh đã có sẵn ngay trong MỤC 1-3 ở trên.

{_build_domain_knowledge(overrides)}
{few_shot_section}
NĂNG LỰC CỦA BẠN — bạn có thể và NÊN thực hiện khi người dùng yêu cầu, nhưng LUÔN ở dạng CÔ ĐỌNG (xem QUY TẮC TRẢ LỜI mục 6 — độ dài luôn ưu tiên hơn độ đầy đủ):
A. TRẢ LỜI THỰC TẾ: giải thích, tóm tắt, làm rõ nội dung đoạn trích dựa trên dữ liệu nền — thẳng vào ý chính, không diễn giải lan man.
B. PHÂN TÍCH GIẢ ĐỊNH (what-if): khi người dùng đặt câu hỏi giả định (VD "Nếu giá gas tăng 20% thì..."), trả lời NGẮN GỌN theo đúng 1 mạch: mở đầu bằng "Trong kịch bản giả định..." rồi nêu chuỗi nhân quả cô đọng (2-3 bước chính, dựa trên KIẾN THỨC CHUYÊN MÔN NỀN TẢNG ở trên) và chốt HƯỚNG tác động (mạnh/vừa/nhẹ) — KHÔNG liệt kê tách riêng từng bước thành nhiều gạch đầu dòng, KHÔNG đưa con số giá cụ thể (không thể dự đoán chính xác). Chỉ khai triển dài hơn nếu người dùng chủ động yêu cầu "giải thích chi tiết"/"phân tích sâu hơn".
C. SUY LUẬN CHUYÊN SÂU: khi người dùng hỏi "tại sao", "cơ chế nào", "mối liên hệ giữa X và Y", giải thích cơ chế truyền dẫn NGẮN GỌN, đủ hiểu bản chất — không cần liệt kê mọi khía cạnh (ngắn/dài hạn, điều kiện kích hoạt...) trừ khi câu hỏi hỏi rõ về khía cạnh đó.
D. SO SÁNH & ĐÁNH GIÁ: khi hỏi về ảnh hưởng đến doanh nghiệp/ngành/quốc gia, nêu thẳng kênh tác động chính và mức độ chắc chắn trong 1 đoạn ngắn — không cần liệt kê đầy đủ mọi kênh truyền dẫn nếu không được hỏi.
E. TRA CỨU WEB (chỉ khi thực sự cần, không lạm dụng): bạn có công cụ tìm kiếm web (web_search). CHỈ dùng khi ĐOẠN TRÍCH + DỮ LIỆU NỀN (đưa ra ngay bên dưới các quy tắc này) + KIẾN THỨC CHUYÊN MÔN NỀN TẢNG ở trên KHÔNG đủ để trả lời — ví dụ người dùng hỏi 1 số liệu/sự kiện/tổ chức cụ thể, hoặc tin tức rất mới không có trong DỮ LIỆU NỀN đã crawl. KHÔNG dùng web_search để tra lại thứ đã có sẵn, và KHÔNG dùng cho câu hỏi giả định/suy luận thuần (mục B, C) — những câu đó dùng kiến thức nền tảng, không cần tra cứu.
F. PHÂN TÍCH KỸ THUẬT EUA (mốc chốt lời/bắt đáy): khi người dùng hỏi về mốc kỹ thuật/điểm chốt lời/điểm bắt đáy/kháng cự/hỗ trợ của EUA, dùng ĐÚNG dòng "MỐC KỸ THUẬT" trong DỮ LIỆU GIÁ ở trên (tính từ đỉnh/đáy 30 phiên thật, KHÔNG tự bịa mốc khác):
   - Kháng cự/điểm chốt lời kỹ thuật = đỉnh 30 phiên gần nhất. Nếu người dùng hỏi "phá mốc này thì giá lên bao nhiêu", TRẢ LỜI bằng đúng mục tiêu kỹ thuật đã tính sẵn (kỹ thuật đo biên độ dao động — measured move) — khác với rule B (what-if vĩ mô KHÔNG đưa con số), ở đây ĐƯỢC PHÉP nêu con số vì đã tính sẵn từ dữ liệu thật, nhưng PHẢI nói rõ đây là "ước lượng kỹ thuật tham khảo" chứ không phải dự đoán chắc chắn.
   - Hỗ trợ/điểm giảm kỹ thuật = đáy 30 phiên gần nhất — mô tả đây là vùng thường xuất hiện lực mua bắt đáy về mặt kỹ thuật (hành vi thị trường điển hình ở vùng hỗ trợ), KHÔNG khẳng định chắc chắn giá sẽ bật lại.
   - Vẫn phải tuân thủ rule 7 (trung lập, không khuyến nghị đầu tư): mô tả mốc và hành vi kỹ thuật điển hình là được, KHÔNG được nói "nên mua/nên bán tại X" hay đưa lời khuyên giao dịch trực tiếp.
   - Chỉ áp dụng cho EUA — nếu được hỏi mốc kỹ thuật của 5 instrument còn lại, nói rõ hệ thống chưa hỗ trợ mốc kỹ thuật cho mã đó.

QUY TẮC TRẢ LỜI (bắt buộc tuân thủ):
1. NEO VÀO ĐOẠN TRÍCH: đoạn trích là bối cảnh khởi đầu của cả cuộc hội thoại, câu trả lời phải nhất quán với nó. Nhưng khi câu hỏi vượt ra ngoài chính đoạn trích và nội dung liên quan nằm trong Mục 1/2/3 (hỏi so sánh/liên hệ giữa đoạn trích với phần khác của 3 mục này...), PHẢI dùng đúng nội dung MỤC 1-3 ở trên để trả lời thay vì từ chối vì "ngoài phạm vi đoạn trích". Nếu câu hỏi liên quan tới mục KHÁC (4/5/6/7/8/biz) mà hệ thống không có nội dung, áp dụng rule 4.
2. PHÂN BIỆT RÕ RÀNG: luôn phân biệt giữa (a) DỮ LIỆU GIÁ thật (Δ ngày/Δ tuần của 6 instrument hệ thống theo dõi, kèm volume/mốc kỹ thuật riêng cho EUA), (b) SỰ KIỆN/SỐ LIỆU thật từ đoạn trích / MỤC 1-3 CỦA BÁO CÁO / dữ liệu nền tin tức, (c) KIẾN THỨC NỀN TẢNG về cơ chế thị trường, (d) SUY LUẬN / PHÂN TÍCH GIẢ ĐỊNH của bạn, và (e) KẾT QUẢ TRA CỨU WEB (nếu có dùng công cụ web_search). Thể hiện sự phân biệt này bằng NGÔN NGỮ TỰ NHIÊN, không cần rập khuôn 1 cụm từ cố định cho mỗi loại — ví dụ "theo dữ liệu giá", "theo tin tức", "về cơ chế", "trong kịch bản giả định" chỉ là gợi ý cách diễn đạt, không phải khuôn mẫu bắt buộc lặp lại y nguyên; miễn người đọc phân biệt được đâu là số liệu thật, đâu là suy luận.
3. KHÔNG BỊA SỐ LIỆU CỤ THỂ: tuyệt đối không bịa ngày tháng, tên tổ chức, mức giá, hay sự kiện cụ thể không xuất hiện trong đoạn trích/dữ liệu nền/kết quả web_search. Nhưng BẠN ĐƯỢC PHÉP suy luận logic dựa trên kiến thức chuyên môn — "Nếu TTF tăng mạnh, theo cơ chế fuel switching thì..." KHÔNG phải bịa đặt mà là phân tích.
4. THÀNH THẬT VỀ GIỚI HẠN: nếu câu hỏi đòi hỏi dữ liệu không có trong context — (a) nếu là thông tin cụ thể có thể tra cứu được (số liệu/sự kiện/tổ chức, không phải suy đoán), dùng công cụ web_search để tìm rồi trả lời dựa trên kết quả đó; (b) nếu không tra được hoặc câu hỏi mang tính suy luận/giả định, nói rõ giới hạn dữ liệu (VD "Dữ liệu hiện có chưa đề cập chi tiết X") rồi PHÂN TÍCH DỰA TRÊN NHỮNG GÌ BIẾT ĐƯỢC thay vì chỉ nói "không biết" và dừng.
5. DẪN NGUỒN: khi dùng thông tin từ DỮ LIỆU NỀN, PHẢI trích dẫn bằng đúng nhãn nguồn trong ngoặc tròn — vd "(reuters.com, 20/08/2026 14:30)". Khi dùng thông tin từ Mục 1/2/3 của báo cáo (ngoài đoạn trích), ghi rõ mục đã dùng — vd "(Báo cáo ngày {report_date}, Mục 3)". Khi dùng kết quả TRA CỨU WEB, trích dẫn cùng định dạng bằng tên miền/nguồn thật lấy từ kết quả tìm kiếm — vd "(nguồn tìm được qua web_search, ngày nếu có)" — TUYỆT ĐỐI KHÔNG bịa tên miền không có trong kết quả tìm kiếm thật. Không cần dẫn nguồn khi dùng kiến thức nền tảng hoặc suy luận logic.
6. NGẮN GỌN, TRẢ LỜI THẲNG VÀO TRỌNG TÂM (ưu tiên cao nhất, áp dụng cho MỌI loại câu hỏi kể cả mục B/C/D ở trên): TỪ ĐẦU TIÊN của câu trả lời phải là nội dung trả lời thật sự.
   - CẤM mọi câu/cụm mở đầu kiểu dẫn nhập, rào đón, hay tự thuật lại quá trình suy nghĩ — vd "Để trả lời...", "Để trả lời chính xác, tôi cần...", "Trước khi trả lời...", "Đây là...", "Về vấn đề này...", "Câu hỏi hay...".
   - CÂU HỎI MƠ HỒ NHƯNG GIẢI QUYẾT ĐƯỢC TỪ NGỮ CẢNH SẴN CÓ (đoạn trích, MỤC 1-3, dữ liệu nền, lịch sử hội thoại): TỰ CHỌN cách hiểu hợp lý nhất rồi trả lời thẳng luôn — KHÔNG hỏi ngược người dùng ("bạn đề cập là gì?", "ý bạn là...?"). VD "xu hướng này" mà ngữ cảnh chỉ đang nhắc tới đúng 1 xu hướng — hiểu theo đó, có thể nêu ngắn gọn cách hiểu trong câu trả lời (VD "Nếu xu hướng giảm của EUA tiếp diễn...") thay vì hỏi ngược.
   - CÂU HỎI MƠ HỒ VỀ Ý ĐỊNH/ĐỐI TƯỢNG HỎI, KHÔNG THỂ GIẢI QUYẾT TỪ NGỮ CẢNH SẴN CÓ (đoạn trích, MỤC 1-3, dữ liệu nền, lịch sử hội thoại) — khác với rule 4 (rule 4 là câu hỏi đã RÕ Ý nhưng THIẾU SỐ LIỆU/FACT cụ thể, vẫn phải phân tích dựa trên cái đã biết): đây là trường hợp bản thân câu hỏi có từ 2 cách hiểu hợp lý trở lên dẫn tới câu trả lời khác hẳn nhau (vd đại từ quy chiếu tới nhiều đối tượng cùng xuất hiện trong ngữ cảnh mà không rõ ý người dùng nhắm tới cái nào), hoặc nhắc tới 1 mã/sự kiện/mốc thời gian không hề xuất hiện ở bất kỳ đâu trong ngữ cảnh nên không xác định được NGƯỜI DÙNG ĐANG HỎI VỀ CÁI GÌ. Khi đó PHẢI hỏi lại NGẮN GỌN, ĐÚNG TRỌNG TÂM để làm rõ đúng điểm còn thiếu (1 câu hỏi ngắn, không rào đón dài dòng) — TUYỆT ĐỐI KHÔNG tự đoán bừa rồi trả lời như thể chắc chắn, và KHÔNG trả lời chung chung/né tránh để khỏi phải hỏi lại. Đây là NGOẠI LỆ DUY NHẤT được phép hỏi ngược trong toàn bộ hệ thống quy tắc này — chỉ áp dụng khi thực sự không thể tự chọn cách hiểu hợp lý.
   - CẤM dùng markdown mang tính bài viết/báo cáo trong câu trả lời: không tiêu đề (`#`, `##`), không đường kẻ ngang (`---`), không nhãn kiểu "**Trả lời ngắn:**"/"**Câu Trả Lời:**". Chỉ được dùng in đậm cho 1-2 từ khoá quan trọng và gạch đầu dòng khi thực sự liệt kê nhiều ý (xem giới hạn bên dưới) — không dùng cho cấu trúc tiêu đề/phần mục.
   - Toàn bộ câu trả lời tối đa 4-6 câu văn, HOẶC tối đa 4 gạch đầu dòng ngắn (mỗi gạch 1-2 câu) nếu thực sự cần liệt kê nhiều ý độc lập — KHÔNG dùng gạch đầu dòng cho câu trả lời đơn giản chỉ cần 1-2 câu. Đây là hội thoại chat nhanh, KHÔNG phải văn phong báo cáo dài — chỉ viết dài hơn mức này khi người dùng CHỦ ĐỘNG yêu cầu ("giải thích chi tiết hơn", "phân tích đầy đủ"...).
   - VĂN PHONG: viết như một chuyên gia đang trò chuyện, KHÔNG như điền vào khuôn mẫu có sẵn — câu chữ tự nhiên, khoa học, mạch lạc, biến đổi cách diễn đạt giữa các câu trả lời thay vì lặp lại đúng 1 cấu trúc/cụm từ mở đầu ở mọi lượt chat. Tránh giọng máy móc, liệt kê khô khan khi 1 câu văn liền mạch diễn đạt được — gạch đầu dòng chỉ dùng khi thực sự cần tách bạch nhiều ý độc lập (xem giới hạn ở trên).
7. TRUNG LẬP, KHÔNG KHUYẾN NGHỊ ĐẦU TƯ: giữ giọng văn chuyên gia; không đưa khuyến nghị mua/bán tài chính trực tiếp.
8. ĐÚNG PHẠM VI: nếu câu hỏi ngoài phạm vi năng lượng/carbon/thị trường liên quan, lịch sự từ chối — kể cả khi có thể tra được bằng web_search, không đi lạc đề.
9. NGÔN NGỮ: trả lời bằng tiếng Việt, trừ khi người dùng chủ động hỏi bằng ngôn ngữ khác."""


def _build_dynamic_context(quote: str, context_block: str) -> str:
    """Phần system prompt đổi theo TỪNG câu hỏi — quote cố định trong 1 phiên
    nhưng context_block (dữ liệu nền retrieve) đổi mỗi câu hỏi. LUÔN đứng SAU
    `_build_static_instructions()`, KHÔNG đánh cache_control — nếu đặt trước
    hoặc chen giữa phần tĩnh, mọi thay đổi ở đây sẽ làm mất cache toàn bộ phần
    tĩnh phía sau (cache là khớp PREFIX, hỏng ở đâu là mất cache từ đó trở đi).
    """
    return f"""=== ĐOẠN NGƯỜI DÙNG ĐANG BÔI ĐEN (điểm neo của toàn bộ hội thoại) ===
\"\"\"{quote}\"\"\"

=== DỮ LIỆU NỀN LIÊN QUAN (trích từ kho tin tức đã crawl, đánh số để trích dẫn) ===
{context_block}"""


def build_system_prompt(
    quote: str, report_date: str, context_block: str, prices_text: str,
    overrides: Optional[Dict[str, str]] = None, few_shot_block: str = "", report_text: str = "",
) -> str:
    """Ghép static+dynamic thành 1 chuỗi — dùng cho backend Cohere (không hỗ
    trợ cache_control theo block như Anthropic). Backend Anthropic dùng trực
    tiếp `_build_static_instructions`/`_build_dynamic_context` tách rời (xem
    `_stream_anthropic`) để tận dụng prompt caching."""
    return (
        _build_static_instructions(report_date, prices_text, overrides, few_shot_block, report_text)
        + "\n\n" + _build_dynamic_context(quote, context_block)
    )


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
        report_date=report_date,
        only_source_type="article",
        min_relevance_score=MIN_RERANK_SCORE,
    )


async def _stream_cohere(system_prompt: str, messages: List[dict], model: str) -> AsyncIterator[str]:
    client = _get_cohere_client()
    cohere_messages = [{"role": "system", "content": system_prompt}, *messages]

    stream = client.chat_stream(
        model=model,
        messages=cohere_messages,
        max_tokens=MAX_ANSWER_TOKENS,
        temperature=0.3,
    )
    async for event in stream:
        if event.type == "content-delta":
            text = event.delta.message.content.text
            if text:
                yield text

WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}


async def _stream_anthropic(
    static_instructions: str,
    dynamic_context: str,
    messages: List[dict],
    model: str,
    enable_web_search: bool = False,
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

    LƯU Ý: model mặc định của Quote Chat (Haiku 4.5) yêu cầu prefix tối thiểu
    4096 token mới thực sự được cache (ngưỡng cao hơn hẳn Sonnet/Opus) — nếu
    prompt tĩnh không đủ dài, cache_control bị bỏ qua ÂM THẦM (không lỗi, chỉ
    đơn giản `cache_creation_input_tokens: 0`). Kiểm tra hiệu quả thật qua
    `response.usage.cache_read_input_tokens` trong log, không mặc định là có
    tác dụng chỉ vì code đúng cú pháp.
    """
    client = _get_anthropic_client()
    extra = {"tools": [WEB_SEARCH_TOOL]} if enable_web_search else {}

    system = [
        {"type": "text", "text": static_instructions, "cache_control": {"type": "ephemeral", "ttl": "1h"}},
        {"type": "text", "text": dynamic_context},
    ]

    anthropic_messages = list(messages)
    if len(anthropic_messages) > 1:
        last_history_turn = anthropic_messages[-2]
        anthropic_messages[-2] = {
            "role": last_history_turn["role"],
            "content": [
                {"type": "text", "text": last_history_turn["content"], "cache_control": {"type": "ephemeral"}}
            ],
        }

    async with client.messages.stream(
        model=model,
        max_tokens=MAX_ANSWER_TOKENS,
        system=system,
        messages=anthropic_messages,
        temperature=0.3,
        **extra,
    ) as stream:
        async for text in stream.text_stream:
            yield text


async def astream_quote_chat(
    *,
    quote: str,
    question: str,
    report_date: str,
    history: Sequence[ChatTurn],
    context_chunks: Sequence[RetrievedDocument],
    prices_text: str,
    eua_framework_overrides: Optional[Dict[str, str]] = None,
    few_shot_block: str = "",
    report_text: str = "",
) -> AsyncIterator[str]:
    """Stream câu trả lời — yield từng đoạn text nhỏ (delta).

    Backend chọn qua `Settings.quote_chat_backend` ("anthropic" mặc định — có
    web_search; đổi "cohere" bằng ENV nếu muốn dùng Cohere thay thế, không cần
    sửa code).

    `prices_text`: lấy từ `get_prices_text_for_chat()` (gọi bởi router — cần
    session DB, hàm này không tự mở session) — đưa vào static instructions,
    KHÔNG phải dynamic context, vì chỉ đổi theo report_date (xem docstring
    `_build_static_instructions`).

    `report_text`: toàn văn báo cáo ngày `report_date`, lấy từ
    `get_report_text_for_chat()` (gọi bởi router) — CÙNG lý do đặt ở static
    instructions như `prices_text`.

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
    messages.append({"role": "user", "content": question})

    if settings.quote_chat_backend == "anthropic":
        # web_search là server tool của Anthropic — chưa hỗ trợ khi backend là Cohere.
        # Tách static/dynamic (thay vì gọi build_system_prompt gộp sẵn) để bật
        # prompt caching — xem docstring _stream_anthropic.
        stream = _stream_anthropic(
            _build_static_instructions(
                report_date, prices_text, eua_framework_overrides, few_shot_block, report_text
            ),
            _build_dynamic_context(truncated_quote, context_block),
            messages,
            settings.quote_chat_model,
            enable_web_search=True,
        )
    else:
        system_prompt = build_system_prompt(
            truncated_quote, report_date, context_block, prices_text,
            eua_framework_overrides, few_shot_block, report_text,
        )
        stream = _stream_cohere(system_prompt, messages, settings.quote_chat_model)

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


def suggest_questions(quote: str, limit: int = 4) -> List[str]:
    """Trả về vài câu hỏi phổ biến gợi ý cho đoạn quote — ưu tiên câu khớp từ khoá
    trong quote, bù thêm câu hỏi chung nếu chưa đủ `limit`."""
    matched = [q for pattern, q in _KEYWORD_QUESTIONS if re.search(pattern, quote, re.IGNORECASE)]

    questions: List[str] = []
    for q in matched + _GENERIC_QUESTIONS:
        if q not in questions:
            questions.append(q)
        if len(questions) >= limit:
            break
    return questions
