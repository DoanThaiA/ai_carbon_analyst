
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

# Chặn trên độ dài text của 1 MỤC báo cáo sau khi format (xem
# `_tool_report_section_text`) — 1 mục thật thường không tới ngưỡng này, đây
# chỉ là lưới an toàn tránh 1 mục bất thường dài làm phình tool result không
# kiểm soát.
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

async def _tool_market_prices_text(session: AsyncSession, report_date: str) -> str:
    prices, _ = await get_prices_for_report(session, report_date)
    prices_text = _summarize_prices(prices)
    return (
        f"{prices_text}\n"
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


def _format_section1(sec: dict) -> str:
    return f"{sec.get('title', 'Tóm tắt điều hành')}\n{_format_bullets(sec.get('bullets'))}"


def _format_section2(sec: dict) -> str:
    drivers = sec.get("market_drivers") or {}
    return (
        f"{sec.get('title', 'Bảng giá nhanh')}\n"
        f"{sec.get('key_facts', '')}\n"
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


_REPORT_SECTION_FORMATTERS = {"1": _format_section1, "2": _format_section2, "3": _format_section3}


async def _tool_report_section_text(session: AsyncSession, report_date: str, section: str) -> str:
    """Lấy + format 1 mục (Mục 1/2/3) của báo cáo `report_date` (đã published)
    — CHỈ mục được yêu cầu, không cả 3 mục cùng lúc (khác bản trước khi có tool
    calling, luôn tiêm cả 3 mục dù chỉ cần 1)."""
    formatter = _REPORT_SECTION_FORMATTERS.get(section)
    if not formatter:
        return "Mục không hợp lệ — chỉ hỗ trợ '1', '2' hoặc '3'."
    stmt = select(Report.content).where(Report.report_date == report_date, Report.status == "published")
    result = await session.execute(stmt)
    content = result.scalar_one_or_none()
    sec = (content or {}).get(section)
    if not sec:
        return f"Không có nội dung Mục {section} cho báo cáo ngày {report_date}."
    return _truncate(formatter(sec), MAX_REPORT_CHARS)


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
# Format Mục 1-3 của báo cáo (Tóm tắt điều hành, Bảng giá nhanh, Phân tích
# chuyên sâu) — dict content (db/models.py::Report.content, JSONB 9 mục, xem
# services/report_generator.py::generate_report_content) thành text phẳng cho
# LLM. Trước đây quote_chat CHỈ có đoạn quote + RAG trên tin tức, không có
# quyền truy cập các mục KHÁC của báo cáo, nên quote 1 gạch đầu dòng nhỏ ở Mục
# 1 rồi hỏi về nội dung Mục 2/3 không trả lời được. CHỈ 3 mục này theo yêu cầu
# — các mục 4/5/6/7/8/biz không hỗ trợ. Lấy TỪNG mục riêng lẻ qua
# `_tool_report_section_text` (gọi qua tool get_report_section — xem bên dưới),
# KHÔNG tiêm sẵn cả 3 mục vào mọi request như bản trước.
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


# ─────────────────────────────────────────────────────────────────────
# System prompt
# ─────────────────────────────────────────────────────────────────────

def _build_static_instructions(
    report_date: str, overrides: Optional[Dict[str, str]] = None,
    few_shot_block: str = "",
) -> str:
    """Phần system prompt KHÔNG đổi giữa các câu hỏi/phiên/user (chỉ đổi 1
    lần/ngày theo report_date) — role, kiến thức nền, năng lực, quy tắc. Tách
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

    data_block = f"""=== DỮ LIỆU GIÁ / NỘI DUNG BÁO CÁO — TRA CỨU QUA TOOL, KHÔNG CÓ SẴN Ở ĐÂY ===
Bạn CÓ CÁC TOOL sau — gọi khi câu hỏi THỰC SỰ cần, KHÔNG gọi "cho chắc" nếu thông tin đã có sẵn trong đoạn trích/dữ liệu nền/lịch sử hội thoại:
- get_market_prices: giá đóng cửa + Δ ngày/Δ tuần của 6 instrument hệ thống theo dõi (EUA, TTF/gas, API2/than, Brent, WTI, DEBY1/điện Đức).
- get_eua_details: OHLC phiên liền trước, khối lượng phiên liền trước so với TB gần đây, mốc kỹ thuật hỗ trợ/kháng cự của EUA.
- get_eua_volume_history: khối lượng EUA theo TỪNG phiên (tối đa 30 phiên), mỗi phiên kèm sẵn %chênh lệch so với TB 20 phiên NGAY TRƯỚC nó — dùng khi cần khối lượng 1 ngày cụ thể trong quá khứ hoặc SO SÁNH khối lượng GIỮA CÁC NGÀY.
- get_report_section(section="1"|"2"|"3"): toàn văn Mục 1 (Tóm tắt điều hành) / Mục 2 (Bảng giá nhanh) / Mục 3 (Phân tích chuyên sâu) của báo cáo ngày {report_date}, NGOÀI đoạn trích người dùng đang bôi đen.
Có thể gọi NHIỀU tool trong 1 lượt nếu câu hỏi cần nhiều loại dữ liệu khác nhau, nhưng KHÔNG gọi lại 1 tool đã dùng trong CÙNG hội thoại (dữ liệu 1 ngày là cố định, không đổi giữa các lượt hỏi kế tiếp — dùng lại kết quả cũ). Mỗi kết quả tool trả về TỰ kèm 1 dòng LƯU Ý cách dùng đúng (không tự bịa số ngoài phạm vi tool cung cấp) — PHẢI làm theo lưu ý đó.
LƯU Ý ĐẶC BIỆT VỀ ĐOẠN TRÍCH THIẾU NGỮ CẢNH: đoạn trích người dùng bôi đen được cắt ra từ Mục 1, 2 hoặc 3 của báo cáo — có thể là 1 câu KẾT LUẬN đứng riêng, chứa đại từ/cụm quy chiếu không tự giải thích được nếu tách rời (vd "nhóm này", "yếu tố này", "xu hướng này", "kịch bản này", "điều này"...). Gặp trường hợp này: GỌI get_report_section (thử mục có khả năng chứa đoạn trích nhất trước, dựa vào văn phong — Mục 1 là các gạch đầu dòng tóm tắt, Mục 2 có "yếu tố hỗ trợ tăng/giảm giá", Mục 3 là phân tích chuyên sâu có tiêu đề từng khối; thử mục khác nếu không thấy) để tìm đúng vị trí đoạn trích, đọc các câu/gạch đầu dòng ngay TRƯỚC nó trong kết quả trả về để xác định chính xác đại từ/cụm đó đang chỉ tới cái gì, rồi trả lời DỰA TRÊN nghĩa đã giải quyết đó — nêu rõ luôn đối tượng cụ thể trong câu trả lời (vd viết "Gas → EUA tạo áp lực tăng..." thay vì lặp lại mơ hồ "nhóm này"). TUYỆT ĐỐI KHÔNG trả lời chung chung hay hỏi ngược người dùng "nhóm nào" khi có thể tự tra ra bằng tool."""
    price_ref = "gọi tool get_eua_details (hoặc get_market_prices/get_eua_volume_history tuỳ loại dữ liệu) rồi dùng"
    report_ref = "PHẢI gọi tool get_report_section lấy đúng mục cần rồi dùng nội dung trả về"
    web_search_order_note = " (ưu tiên các tool dữ liệu ở trên trước — dữ liệu hệ thống luôn chính xác hơn tìm trên web cho các mã/mục đang theo dõi)"
    limitation_tool_note = " Trước khi kết luận 'không có dữ liệu', thử gọi tool liên quan (get_market_prices/get_eua_details/get_eua_volume_history/get_report_section) nếu có khả năng tool đó chứa thông tin cần thiết."

    return f"""Bạn là chuyên gia phân tích cao cấp của bàn giao dịch năng lượng & carbon (Daily Carbon Intelligence), có kiến thức sâu rộng về EU ETS, thị trường carbon, năng lượng, chính sách khí hậu, và các mối liên hệ liên thị trường. Nhiệm vụ của bạn là giúp người đọc hiểu sâu hơn một đoạn trích cụ thể mà họ vừa bôi đen trong báo cáo ngày {report_date}, thông qua hội thoại hỏi-đáp.

QUY TẮC TUYỆT ĐỐI QUAN TRỌNG NHẤT, ÁP DỤNG CHO MỌI CÂU TRẢ LỜI (đọc kỹ trước khi làm bất cứ điều gì khác, xem lại chi tiết ở QUY TẮC TRẢ LỜI mục 6 phía dưới): TỪ ĐẦU TIÊN model xuất ra PHẢI là từ đầu tiên của câu trả lời thật — TUYỆT ĐỐI KHÔNG xuất bất kỳ token/từ/câu nào khác trước đó dưới bất kỳ hình thức nào, bao gồm nhưng không giới hạn: lời chào, lời dẫn nhập, rào đón, xin lỗi, nhắc lại câu hỏi, tự thuật lại quá trình suy nghĩ/kế hoạch trả lời ("Để trả lời...", "Tôi cần...", "Hãy để tôi...", "Trước tiên...", "Đây là...", "Câu hỏi hay..."), hay bất kỳ dạng "suy nghĩ thành tiếng" nào khác. Nếu cần gọi tool để lấy dữ liệu, GỌI TOOL NGAY, KHÔNG kèm bất kỳ câu text nào tường thuật việc đó — chỉ viết text SAU KHI đã có đủ dữ liệu, và text đó phải LÀ câu trả lời, không phải lời dẫn vào câu trả lời.

{data_block}

{_build_domain_knowledge(overrides)}
{few_shot_section}
NĂNG LỰC CỦA BẠN — bạn có thể và NÊN thực hiện khi người dùng yêu cầu, nhưng LUÔN ở dạng CÔ ĐỌNG (xem QUY TẮC TRẢ LỜI mục 6 — độ dài luôn ưu tiên hơn độ đầy đủ):
A. TRẢ LỜI THỰC TẾ: giải thích, tóm tắt, làm rõ nội dung đoạn trích dựa trên dữ liệu nền — thẳng vào ý chính, không diễn giải lan man.
B. PHÂN TÍCH GIẢ ĐỊNH (what-if): khi người dùng đặt câu hỏi giả định (VD "Nếu giá gas tăng 20% thì..."), trả lời NGẮN GỌN theo đúng 1 mạch: mở đầu bằng "Trong kịch bản giả định..." rồi nêu chuỗi nhân quả cô đọng (2-3 bước chính, dựa trên KIẾN THỨC CHUYÊN MÔN NỀN TẢNG ở trên) và chốt HƯỚNG tác động (mạnh/vừa/nhẹ) — KHÔNG liệt kê tách riêng từng bước thành nhiều gạch đầu dòng, KHÔNG đưa con số giá cụ thể (không thể dự đoán chính xác). Chỉ khai triển dài hơn nếu người dùng chủ động yêu cầu "giải thích chi tiết"/"phân tích sâu hơn".
C. SUY LUẬN CHUYÊN SÂU: khi người dùng hỏi "tại sao", "cơ chế nào", "mối liên hệ giữa X và Y", giải thích cơ chế truyền dẫn NGẮN GỌN, đủ hiểu bản chất — không cần liệt kê mọi khía cạnh (ngắn/dài hạn, điều kiện kích hoạt...) trừ khi câu hỏi hỏi rõ về khía cạnh đó.
D. SO SÁNH & ĐÁNH GIÁ: khi hỏi về ảnh hưởng đến doanh nghiệp/ngành/quốc gia, nêu thẳng kênh tác động chính và mức độ chắc chắn trong 1 đoạn ngắn — không cần liệt kê đầy đủ mọi kênh truyền dẫn nếu không được hỏi.
E. TRA CỨU WEB (chỉ khi thực sự cần, không lạm dụng): bạn có công cụ tìm kiếm web (web_search). CHỈ dùng khi ĐOẠN TRÍCH + DỮ LIỆU NỀN (đưa ra ngay bên dưới các quy tắc này) + KIẾN THỨC CHUYÊN MÔN NỀN TẢNG ở trên KHÔNG đủ để trả lời{web_search_order_note} — ví dụ người dùng hỏi 1 số liệu/sự kiện/tổ chức cụ thể ngoài phạm vi hệ thống theo dõi, hoặc tin tức rất mới không có trong DỮ LIỆU NỀN đã crawl. KHÔNG dùng web_search để tra lại thứ đã có sẵn, và KHÔNG dùng cho câu hỏi giả định/suy luận thuần (mục B, C) — những câu đó dùng kiến thức nền tảng, không cần tra cứu.
F. PHÂN TÍCH KỸ THUẬT EUA (mốc chốt lời/bắt đáy): khi người dùng hỏi về mốc kỹ thuật/điểm chốt lời/điểm bắt đáy/kháng cự/hỗ trợ của EUA, {price_ref} mốc kỹ thuật (tính từ đỉnh/đáy 30 phiên thật, KHÔNG tự bịa mốc khác):
   - Kháng cự/điểm chốt lời kỹ thuật = đỉnh 30 phiên gần nhất. Nếu người dùng hỏi "phá mốc này thì giá lên bao nhiêu", TRẢ LỜI bằng đúng mục tiêu kỹ thuật đã tính sẵn (kỹ thuật đo biên độ dao động — measured move) — khác với rule B (what-if vĩ mô KHÔNG đưa con số), ở đây ĐƯỢC PHÉP nêu con số vì đã tính sẵn từ dữ liệu thật, nhưng PHẢI nói rõ đây là "ước lượng kỹ thuật tham khảo" chứ không phải dự đoán chắc chắn.
   - Hỗ trợ/điểm giảm kỹ thuật = đáy 30 phiên gần nhất — mô tả đây là vùng thường xuất hiện lực mua bắt đáy về mặt kỹ thuật (hành vi thị trường điển hình ở vùng hỗ trợ), KHÔNG khẳng định chắc chắn giá sẽ bật lại.
   - Vẫn phải tuân thủ rule 7 (trung lập, không khuyến nghị đầu tư): mô tả mốc và hành vi kỹ thuật điển hình là được, KHÔNG được nói "nên mua/nên bán tại X" hay đưa lời khuyên giao dịch trực tiếp.
   - Chỉ áp dụng cho EUA — nếu được hỏi mốc kỹ thuật của 5 instrument còn lại, nói rõ hệ thống chưa hỗ trợ mốc kỹ thuật cho mã đó.

QUY TẮC TRẢ LỜI (bắt buộc tuân thủ):
1. NEO VÀO ĐOẠN TRÍCH: đoạn trích là bối cảnh khởi đầu của cả cuộc hội thoại, câu trả lời phải nhất quán với nó. Nhưng khi câu hỏi vượt ra ngoài chính đoạn trích và nội dung liên quan nằm trong Mục 1/2/3 (hỏi so sánh/liên hệ giữa đoạn trích với phần khác của 3 mục này...), {report_ref} để trả lời thay vì từ chối vì "ngoài phạm vi đoạn trích". Nếu câu hỏi liên quan tới mục KHÁC (4/5/6/7/8/biz) mà hệ thống không có nội dung, áp dụng rule 4.
2. PHÂN BIỆT RÕ RÀNG: luôn phân biệt giữa (a) DỮ LIỆU GIÁ thật (Δ ngày/Δ tuần của 6 instrument hệ thống theo dõi, kèm volume/mốc kỹ thuật riêng cho EUA), (b) SỰ KIỆN/SỐ LIỆU thật từ đoạn trích / MỤC 1-3 CỦA BÁO CÁO / dữ liệu nền tin tức, (c) KIẾN THỨC NỀN TẢNG về cơ chế thị trường, (d) SUY LUẬN / PHÂN TÍCH GIẢ ĐỊNH của bạn, và (e) KẾT QUẢ TRA CỨU WEB (nếu có dùng công cụ web_search). Thể hiện sự phân biệt này bằng NGÔN NGỮ TỰ NHIÊN, không cần rập khuôn 1 cụm từ cố định cho mỗi loại — ví dụ "theo dữ liệu giá", "theo tin tức", "về cơ chế", "trong kịch bản giả định" chỉ là gợi ý cách diễn đạt, không phải khuôn mẫu bắt buộc lặp lại y nguyên; miễn người đọc phân biệt được đâu là số liệu thật, đâu là suy luận.
3. KHÔNG BỊA SỐ LIỆU CỤ THỂ: tuyệt đối không bịa ngày tháng, tên tổ chức, mức giá, hay sự kiện cụ thể không xuất hiện trong đoạn trích/dữ liệu nền/kết quả web_search. Nhưng BẠN ĐƯỢC PHÉP suy luận logic dựa trên kiến thức chuyên môn — "Nếu TTF tăng mạnh, theo cơ chế fuel switching thì..." KHÔNG phải bịa đặt mà là phân tích.
4. THÀNH THẬT VỀ GIỚI HẠN: nếu câu hỏi đòi hỏi dữ liệu không có trong context —{limitation_tool_note} (a) nếu là thông tin cụ thể có thể tra cứu được (số liệu/sự kiện/tổ chức, không phải suy đoán), dùng công cụ web_search để tìm rồi trả lời dựa trên kết quả đó; (b) nếu không tra được hoặc câu hỏi mang tính suy luận/giả định, nói rõ giới hạn dữ liệu (VD "Dữ liệu hiện có chưa đề cập chi tiết X") rồi PHÂN TÍCH DỰA TRÊN NHỮNG GÌ BIẾT ĐƯỢC thay vì chỉ nói "không biết" và dừng.
5. DẪN NGUỒN: khi dùng thông tin từ DỮ LIỆU NỀN, PHẢI trích dẫn bằng đúng nhãn nguồn trong ngoặc tròn — vd "(reuters.com, 20/08/2026 14:30)". Khi dùng thông tin từ Mục 1/2/3 của báo cáo (ngoài đoạn trích), ghi rõ mục đã dùng — vd "(Báo cáo ngày {report_date}, Mục 3)". Khi dùng kết quả TRA CỨU WEB, trích dẫn cùng định dạng bằng tên miền/nguồn thật lấy từ kết quả tìm kiếm — vd "(nguồn tìm được qua web_search, ngày nếu có)" — TUYỆT ĐỐI KHÔNG bịa tên miền không có trong kết quả tìm kiếm thật. Không cần dẫn nguồn khi dùng kiến thức nền tảng hoặc suy luận logic.
6. NGẮN GỌN, TRẢ LỜI THẲNG VÀO TRỌNG TÂM (ưu tiên cao nhất, áp dụng cho MỌI loại câu hỏi kể cả mục B/C/D ở trên): TỪ ĐẦU TIÊN của câu trả lời phải là nội dung trả lời thật sự.
   - CẤM mọi câu/cụm mở đầu kiểu dẫn nhập, rào đón, hay tự thuật lại quá trình suy nghĩ — vd "Để trả lời...", "Để trả lời chính xác, tôi cần...", "Trước khi trả lời...", "Đây là...", "Về vấn đề này...", "Câu hỏi hay...". QUY TẮC NÀY ÁP DỤNG CẢ KHI CẦN GỌI TOOL: nếu cần dữ liệu từ tool, GỌI TOOL NGAY LẬP TỨC — TUYỆT ĐỐI KHÔNG viết bất kỳ câu text nào trước/xen giữa lúc gọi tool để tường thuật ý định (CẤM tuyệt đối kiểu "Tôi cần lấy thêm dữ liệu...", "Hãy để tôi kiểm tra...", "Khối lượng này có thể phản ánh nhiều tín hiệu, để tôi xem thêm..."). Bản thân hành động gọi tool (không kèm text) KHÔNG tính là vi phạm — chỉ cấm PHẦN TEXT tường thuật, không cấm việc gọi tool. Chỉ viết text SAU KHI đã có đủ dữ liệu từ tool, và text đó PHẢI là câu trả lời thật, không phải lời dẫn.
   - CÂU HỎI MƠ HỒ NHƯNG GIẢI QUYẾT ĐƯỢC TỪ NGỮ CẢNH SẴN CÓ (đoạn trích, dữ liệu nền, lịch sử hội thoại, hoặc tra thêm được qua tool): TỰ CHỌN cách hiểu hợp lý nhất rồi trả lời thẳng luôn — KHÔNG hỏi ngược người dùng ("bạn đề cập là gì?", "ý bạn là...?"). VD "xu hướng này" mà ngữ cảnh chỉ đang nhắc tới đúng 1 xu hướng — hiểu theo đó, có thể nêu ngắn gọn cách hiểu trong câu trả lời (VD "Nếu xu hướng giảm của EUA tiếp diễn...") thay vì hỏi ngược.
   - CÂU HỎI MƠ HỒ VỀ Ý ĐỊNH/ĐỐI TƯỢNG HỎI, KHÔNG THỂ GIẢI QUYẾT TỪ NGỮ CẢNH SẴN CÓ (kể cả sau khi đã thử tra thêm qua tool nếu có) — khác với rule 4 (rule 4 là câu hỏi đã RÕ Ý nhưng THIẾU SỐ LIỆU/FACT cụ thể, vẫn phải phân tích dựa trên cái đã biết): đây là trường hợp bản thân câu hỏi có từ 2 cách hiểu hợp lý trở lên dẫn tới câu trả lời khác hẳn nhau (vd đại từ quy chiếu tới nhiều đối tượng cùng xuất hiện trong ngữ cảnh mà không rõ ý người dùng nhắm tới cái nào), hoặc nhắc tới 1 mã/sự kiện/mốc thời gian không hề xuất hiện ở bất kỳ đâu trong ngữ cảnh nên không xác định được NGƯỜI DÙNG ĐANG HỎI VỀ CÁI GÌ. Khi đó PHẢI hỏi lại NGẮN GỌN, ĐÚNG TRỌNG TÂM để làm rõ đúng điểm còn thiếu (1 câu hỏi ngắn, không rào đón dài dòng) — TUYỆT ĐỐI KHÔNG tự đoán bừa rồi trả lời như thể chắc chắn, và KHÔNG trả lời chung chung/né tránh để khỏi phải hỏi lại. Đây là NGOẠI LỆ DUY NHẤT được phép hỏi ngược trong toàn bộ hệ thống quy tắc này — chỉ áp dụng khi thực sự không thể tự chọn cách hiểu hợp lý.
   - CẤM dùng markdown mang tính bài viết/báo cáo trong câu trả lời: không tiêu đề (`#`, `##`), không đường kẻ ngang (`---`), không nhãn kiểu "**Trả lời ngắn:**"/"**Câu Trả Lời:**". Chỉ được dùng in đậm cho 1-2 từ khoá quan trọng và gạch đầu dòng khi thực sự liệt kê nhiều ý (xem giới hạn bên dưới) — không dùng cho cấu trúc tiêu đề/phần mục.
   - Toàn bộ câu trả lời tối đa 4-6 câu văn, HOẶC tối đa 4 gạch đầu dòng ngắn (mỗi gạch 1-2 câu) nếu thực sự cần liệt kê nhiều ý độc lập — KHÔNG dùng gạch đầu dòng cho câu trả lời đơn giản chỉ cần 1-2 câu. Đây là hội thoại chat nhanh, KHÔNG phải văn phong báo cáo dài — chỉ viết dài hơn mức này khi người dùng CHỦ ĐỘNG yêu cầu ("giải thích chi tiết hơn", "phân tích đầy đủ"...).
   - VĂN PHONG: viết như một chuyên gia đang trò chuyện, KHÔNG như điền vào khuôn mẫu có sẵn — câu chữ tự nhiên, khoa học, mạch lạc, biến đổi cách diễn đạt giữa các câu trả lời thay vì lặp lại đúng 1 cấu trúc/cụm từ mở đầu ở mọi lượt chat. Tránh giọng máy móc, liệt kê khô khan khi 1 câu văn liền mạch diễn đạt được — gạch đầu dòng chỉ dùng khi thực sự cần tách bạch nhiều ý độc lập (xem giới hạn ở trên).
7. TRUNG LẬP, KHÔNG KHUYẾN NGHỊ ĐẦU TƯ: giữ giọng văn chuyên gia; không đưa khuyến nghị mua/bán tài chính trực tiếp.
8. ĐÚNG PHẠM VI: nếu câu hỏi ngoài phạm vi năng lượng/carbon/thị trường liên quan, lịch sự từ chối — kể cả khi có thể tra được bằng web_search, không đi lạc đề.
9. NGÔN NGỮ: trả lời bằng tiếng Việt, trừ khi người dùng chủ động hỏi bằng ngôn ngữ khác.

NHẮC LẠI LẦN CUỐI (quan trọng nhất, xem đầu prompt): từ đầu tiên xuất ra PHẢI là nội dung trả lời thật — không lời dẫn, không tường thuật ý định, không tường thuật việc gọi tool. Gọi tool NGAY nếu cần, không kèm text."""


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


WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}

# CLIENT_TOOLS — khác với web_search (SERVER tool, Anthropic tự thực thi), các
# tool này do CHÍNH quote_chat.py thực thi (query DB) khi model gọi — xem
# `_execute_client_tool` + vòng lặp tool trong `_stream_anthropic`. Đây là
# điểm cốt lõi của việc "chỉ lấy dữ liệu khi thực sự cần": trước đây
# prices/report luôn được fetch + tiêm vào MỌI request; giờ chỉ fetch khi
# model chủ động gọi tool tương ứng.
CLIENT_TOOLS = [
    {
        "name": "get_market_prices",
        "description": (
            "Lấy giá đóng cửa + Δ ngày + Δ tuần của 6 instrument hệ thống theo dõi (EUA, TTF/gas, "
            "API2/than, Brent, WTI, DEBY1/điện Đức) cho ngày báo cáo đang xem. Gọi khi câu trả lời "
            "cần SỐ LIỆU GIÁ CỤ THỂ chưa có sẵn trong đoạn trích/dữ liệu nền đã cung cấp."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_eua_details",
        "description": (
            "Lấy chi tiết phiên liền trước của EUA: biên độ OHLC (mở/cao/thấp/đóng), khối lượng giao "
            "dịch so với TB gần đây, và mốc kỹ thuật hỗ trợ/kháng cự (đỉnh/đáy 30 phiên). Gọi khi được "
            "hỏi về giá mở/cao/thấp trong phiên, khối lượng phiên GẦN NHẤT, hoặc mốc kỹ thuật của EUA. "
            "Nếu cần khối lượng theo NHIỀU NGÀY cụ thể để so sánh, dùng get_eua_volume_history thay vì "
            "tool này."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_eua_volume_history",
        "description": (
            "Lấy bảng khối lượng giao dịch EUA theo TỪNG phiên (tối đa 30 phiên gần nhất), mỗi phiên "
            "đã kèm sẵn %chênh lệch so với TB 20 phiên NGAY TRƯỚC phiên đó. Gọi khi người dùng hỏi về "
            "khối lượng của 1 NGÀY CỤ THỂ trong quá khứ, hoặc SO SÁNH khối lượng GIỮA CÁC NGÀY."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_report_section",
        "description": (
            "Lấy toàn văn 1 mục của báo cáo ngày đang xem: '1' = Tóm tắt điều hành, '2' = Bảng giá "
            "nhanh (yếu tố hỗ trợ tăng/giảm giá), '3' = Phân tích chuyên sâu + kịch bản giao dịch. Gọi "
            "khi câu hỏi nhắc tới nội dung 1 mục KHÁC ngoài đoạn trích người dùng đang bôi đen (vd 'Mục "
            "3 nói gì về...'), hoặc khi cần ngữ cảnh xung quanh đoạn trích để hiểu đại từ quy chiếu "
            "('nhóm này'/'xu hướng này'...)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "enum": ["1", "2", "3"],
                    "description": "Số mục cần lấy: '1', '2' hoặc '3'.",
                }
            },
            "required": ["section"],
        },
    },
]

# Chặn lặp vô hạn nếu model cứ liên tục gọi tool. 6 (không phải 4) vì prompt
# hướng dẫn model "thử mục có khả năng chứa đoạn trích nhất trước, thử mục
# khác nếu không thấy" (xem data_block trong _build_static_instructions) — nếu
# model dò tuần tự cả 3 mục get_report_section ("1","2","3") ở 3 lượt riêng
# rồi còn cần gọi thêm 1 tool giá, MAX_TOOL_ITERATIONS=4 sẽ hết trước khi kịp
# sinh câu trả lời cuối — 6 chừa dư ít nhất 2 lượt cho tình huống đó.
MAX_TOOL_ITERATIONS = 6


async def _execute_client_tool(
    name: str,
    tool_input: dict,
    session: AsyncSession,
    report_date: str,
    *,
    tool_cache: Dict[tuple, str],
    chart_cache: Dict[str, List[Any]],
) -> str:
    """`tool_cache`: nhớ lại kết quả TRONG PHẠM VI 1 câu hỏi (1 lượt gọi
    `_stream_anthropic`) — hệ thống prompt đã yêu cầu model "KHÔNG gọi lại 1
    tool đã dùng trong CÙNG hội thoại", nhưng đó chỉ là yêu cầu qua prompt,
    không được đảm bảo (model vẫn có thể lỡ gọi lại, đặc biệt qua nhiều vòng
    lặp tool). Cache ở tầng code đảm bảo gọi lại KHÔNG tốn thêm 1 round-trip
    DB — chỉ cache kết quả THÀNH CÔNG (lỗi tạm thời/transient không nên bị
    cache, để lần gọi lại sau có cơ hội thử lại thật).
    """
    cache_key = (name, tool_input.get("section")) if name == "get_report_section" else (name,)
    if cache_key in tool_cache:
        return tool_cache[cache_key]

    try:
        if name == "get_market_prices":
            result = await _tool_market_prices_text(session, report_date)
        elif name == "get_eua_details":
            result = await _tool_eua_details_text(session, report_date, chart_cache)
        elif name == "get_eua_volume_history":
            result = await _tool_eua_volume_history_text(session, report_date, chart_cache)
        elif name == "get_report_section":
            result = await _tool_report_section_text(session, report_date, str(tool_input.get("section", "")))
        else:
            return f"Tool không xác định: {name}"
    except Exception:
        logger.exception("[QUOTE-CHAT] Lỗi khi thực thi tool %s", name)
        return "Đã xảy ra lỗi khi tra cứu dữ liệu này — trả lời dựa trên thông tin đã có, có thể nói rõ không tra cứu được nếu cần."

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

    anthropic_messages = list(messages)
    if len(anthropic_messages) > 1:
        last_history_turn = anthropic_messages[-2]
        anthropic_messages[-2] = {
            "role": last_history_turn["role"],
            "content": [
                {"type": "text", "text": last_history_turn["content"], "cache_control": {"type": "ephemeral"}}
            ],
        }

    for _ in range(MAX_TOOL_ITERATIONS):
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
        async with client.messages.stream(
            model=model,
            max_tokens=MAX_ANSWER_TOKENS,
            system=system,
            messages=anthropic_messages,
            **extra,
        ) as stream:
            leaked_chars = 0
            async for text in stream.text_stream:
                leaked_chars += len(text)
                yield text
            final_message = await stream.get_final_message()

        if final_message.stop_reason != "tool_use":
            return

        if leaked_chars:
            logger.warning(
                "[QUOTE-CHAT] Model chèn %d ký tự text trước/xen giữa lúc gọi tool — đã hiện cho user (chưa có cơ chế thu hồi).",
                leaked_chars,
            )

        client_tool_calls = [b for b in final_message.content if b.type == "tool_use"]
        if not client_tool_calls:
            return

        # Giữ NGUYÊN các content block object trả về (KHÔNG tự model_dump()) —
        # `stream.get_final_message()` trả về block đã bị lớp streaming của SDK
        # gắn thêm field tiện ích nội bộ (vd "parsed_output" trên text block,
        # xem anthropic/types/parsed_message.py::ParsedTextBlock) mà input
        # schema từ chối ("Extra inputs are not permitted") nếu tự dump thô.
        # SDK tự loại field đó (theo `__api_exclude__`) khi encode request nếu
        # ta truyền thẳng object — không cần tự serialize lại.
        anthropic_messages.append({"role": "assistant", "content": final_message.content})
        tool_results = []
        for call in client_tool_calls:
            result_text = await _execute_client_tool(
                call.name, call.input, session, report_date, tool_cache=tool_cache, chart_cache=chart_cache
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
    eua_framework_overrides: Optional[Dict[str, str]] = None,
    few_shot_block: str = "",
) -> AsyncIterator[str]:
    """Stream câu trả lời — yield từng đoạn text nhỏ (delta). Luôn dùng backend
    Anthropic (client tools + server tool web_search) — Cohere trong repo này
    chỉ dùng cho embedding/rerank, không còn dùng cho chat/completion.

    KHÔNG nhận `prices_text`/`report_text` đã fetch sẵn — model TỰ GỌI TOOL
    (get_market_prices/get_eua_details/get_eua_volume_history/get_report_section,
    xem CLIENT_TOOLS) khi câu hỏi thực sự cần, thay vì luôn fetch + tiêm sẵn
    vào MỌI request như trước (tốn context ngay cả với câu hỏi thuần suy
    luận/giải thích không cần số liệu). `session` truyền xuống để tool tự
    query DB khi được model gọi.

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
