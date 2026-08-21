"""Quote Chat — hỏi đáp về 1 đoạn (quote) người dùng bôi đen trong báo cáo.

Luồng xử lý:
1. Ghép quote + câu hỏi hiện tại thành 1 query, đưa qua `RetrievalService`
   (hybrid search + rerank Cohere trên bảng `chunks`) để lấy tối đa
   `MAX_CONTEXT_CHUNKS` đoạn tin tức liên quan làm "dữ liệu nền".
2. Nhét quote + dữ liệu nền vào system prompt, gọi Claude ở chế độ stream,
   yield từng phần text (delta) — router lớp trên đóng gói lại thành SSE.
3. Câu hỏi gợi ý (`suggest_questions`) dùng heuristic từ khoá thay vì gọi LLM,
   để hiện ngay lập tức khi người dùng vừa bôi đen, không tốn round-trip.
"""
import logging
import re
from typing import AsyncIterator, List, Optional, Sequence

from core.config import Settings
from schemas.chat_models import ChatTurn
from schemas.retrieval_models import RetrievedDocument
from services.retrieval import RetrievalService

logger = logging.getLogger(__name__)

MAX_CONTEXT_CHUNKS = 5
HYBRID_SEARCH_LIMIT = 15
MAX_QUOTE_CHARS = 2000  # đủ cho 1 đoạn/gạch đầu dòng của báo cáo
MAX_ANSWER_TOKENS = 1024

# Lazy singleton clients — tránh crash khi import module lúc chưa có .env (giống
# pattern report_generator.py / embedding.py). Backend chọn qua QUOTE_CHAT_BACKEND
# (mặc định "cohere" — đã có key sẵn; đổi "anthropic" khi lên production).
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


def _format_context(chunks: Sequence[RetrievedDocument]) -> str:
    if not chunks:
        return "(Không tìm thấy dữ liệu nền liên quan trong kho tin tức đã crawl.)"
    parts = []
    for i, c in enumerate(chunks, start=1):
        parts.append(f"[Nguồn {i}]\n{c.content.strip()}")
    return "\n\n".join(parts)


# ─────────────────────────────────────────────────────────────────────
# System prompt
# ─────────────────────────────────────────────────────────────────────

def build_system_prompt(quote: str, report_date: str, context_block: str) -> str:
    """System prompt tối ưu cho việc trả lời TỔNG QUAN, bám sát đoạn trích,
    có căn cứ từ dữ liệu nền, và không bịa đặt số liệu/sự kiện.
    """
    return f"""Bạn là trợ lý phân tích của bàn giao dịch năng lượng & carbon (Daily Carbon Intelligence). Nhiệm vụ của bạn là giúp người đọc hiểu sâu hơn một đoạn trích cụ thể mà họ vừa bôi đen trong báo cáo ngày {report_date}, thông qua hội thoại hỏi-đáp ngắn gọn.

=== ĐOẠN NGƯỜI DÙNG ĐANG BÔI ĐEN (điểm neo bắt buộc của toàn bộ hội thoại) ===
\"\"\"{quote}\"\"\"

=== DỮ LIỆU NỀN LIÊN QUAN (trích từ kho tin tức đã crawl, đánh số để trích dẫn) ===
{context_block}

QUY TẮC TRẢ LỜI (bắt buộc tuân thủ theo đúng thứ tự ưu tiên):
1. NEO VÀO ĐOẠN TRÍCH: mọi câu trả lời — kể cả câu hỏi tiếp theo không nhắc lại đoạn trích — đều phải xoay quanh và nhất quán với nội dung đoạn trích ở trên. Đây là bối cảnh cố định của cả cuộc hội thoại.
2. CĂN CỨ DỮ LIỆU: chỉ dùng đoạn trích, DỮ LIỆU NỀN, và kiến thức nền tảng phổ quát về thị trường năng lượng/carbon (cơ chế EU ETS, MSR, CBAM, VCM, fuel switching, crack spread...) để lập luận. TUYỆT ĐỐI KHÔNG bịa số liệu, ngày tháng, tên tổ chức, hay sự kiện cụ thể không xuất hiện trong đoạn trích/dữ liệu nền.
3. THÀNH THẬT VỀ GIỚI HẠN: nếu dữ liệu hiện có không đủ để trả lời chắc chắn một phần của câu hỏi, nói rõ điều đó (ví dụ: "Dữ liệu hiện có chưa đề cập chi tiết X") thay vì suy diễn hoặc phỏng đoán như sự thật.
4. DẪN NGUỒN: khi dùng thông tin từ DỮ LIỆU NỀN, ghi chú ngắn gọn "(Nguồn 1)", "(Nguồn 2)"... theo đúng số thứ tự tương ứng. Không cần dẫn nguồn khi chỉ diễn giải lại đoạn trích hoặc dùng kiến thức nền tảng.
5. TRẢ LỜI TỔNG QUAN, ĐÚNG TRỌNG TÂM: mở đầu bằng 1 đoạn ngắn (2-4 câu) trả lời thẳng vào câu hỏi chính — không lan man, không nhắc lại đề bài. Chỉ thêm tối đa 2-3 gạch đầu dòng bổ sung/chi tiết nếu thực sự cần làm rõ. Đây là hội thoại chat, KHÔNG phải văn phong báo cáo dài.
6. TRUNG LẬP, KHÔNG KHUYẾN NGHỊ ĐẦU TƯ: giữ giọng văn chuyên gia, dựa trên dữ liệu; tuyệt đối không đưa khuyến nghị mua/bán tài chính trực tiếp (vd "nên mua/bán/long/short X").
7. ĐÚNG PHẠM VI: nếu câu hỏi nằm ngoài phạm vi năng lượng/carbon/thị trường liên quan đến đoạn trích, lịch sự từ chối và gợi ý người dùng quay lại chủ đề của đoạn trích.
8. NGÔN NGỮ: trả lời bằng tiếng Việt, trừ khi người dùng chủ động hỏi bằng ngôn ngữ khác."""


# ─────────────────────────────────────────────────────────────────────
# Retrieval + streaming
# ─────────────────────────────────────────────────────────────────────

async def retrieve_context_for_quote(
    retrieval_service: RetrievalService, quote: str, question: str, report_date: str
) -> List[RetrievedDocument]:
    """Ghép quote + câu hỏi thành 1 query semantic để tìm dữ liệu nền liên quan."""
    query = f"{_truncate(quote, MAX_QUOTE_CHARS)}\n\n{question}".strip()
    return await retrieval_service.retrieve(
        query=query, top_k=MAX_CONTEXT_CHUNKS, hybrid_limit=HYBRID_SEARCH_LIMIT, report_date=report_date
    )


async def _stream_cohere(system_prompt: str, messages: List[dict], model: str) -> AsyncIterator[str]:
    client = _get_cohere_client()
    cohere_messages = [{"role": "system", "content": system_prompt}, *messages]

    stream = client.chat_stream(
        model=model,
        messages=cohere_messages,
        max_tokens=MAX_ANSWER_TOKENS,
        temperature=0.2,
    )
    async for event in stream:
        if event.type == "content-delta":
            text = event.delta.message.content.text
            if text:
                yield text


async def _stream_anthropic(system_prompt: str, messages: List[dict], model: str) -> AsyncIterator[str]:
    client = _get_anthropic_client()
    async with client.messages.stream(
        model=model,
        max_tokens=MAX_ANSWER_TOKENS,
        system=system_prompt,
        messages=messages,
        temperature=0.2,
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
) -> AsyncIterator[str]:
    """Stream câu trả lời — yield từng đoạn text nhỏ (delta).

    Backend chọn qua `Settings.quote_chat_backend` ("cohere" mặc định — đã có
    key sẵn để chạy thử ngay; đổi "anthropic" bằng ENV khi lên production,
    không cần sửa code).
    """
    settings = Settings.from_env()
    system_prompt = build_system_prompt(
        _truncate(quote, MAX_QUOTE_CHARS), report_date, _format_context(context_chunks)
    )

    messages = [{"role": turn.role, "content": turn.content} for turn in history]
    messages.append({"role": "user", "content": question})

    if settings.quote_chat_backend == "anthropic":
        stream = _stream_anthropic(system_prompt, messages, settings.quote_chat_model)
    else:
        stream = _stream_cohere(system_prompt, messages, settings.quote_chat_model)

    async for delta in stream:
        yield delta


# ─────────────────────────────────────────────────────────────────────
# Suggested questions — heuristic, không gọi LLM (cần hiện tức thời)
# ─────────────────────────────────────────────────────────────────────

_KEYWORD_QUESTIONS: List[tuple[str, str]] = [
    (r"EUA|ETS|hạn ngạch", "Vì sao diễn biến này tác động đến giá EUA?"),
    (r"CBAM", "CBAM ảnh hưởng thế nào đến doanh nghiệp xuất khẩu Việt Nam?"),
    (r"MSR", "Cơ chế MSR sẽ can thiệp vào nguồn cung EUA như thế nào?"),
    (r"TTF|khí đốt|\bgas\b", "Diễn biến giá khí đốt liên quan gì đến giá điện/than và EUA?"),
    (r"than\b|coal", "Vì sao giá than lại ảnh hưởng đến phát thải và giá EUA?"),
    (r"dầu\b|Brent|WTI|oil", "Giá dầu tác động thế nào đến chi phí sản xuất và phát thải?"),
    (r"VCM|tín chỉ carbon tự nguyện", "Thị trường tín chỉ carbon tự nguyện (VCM) khác gì EU ETS?"),
    (r"chính sách|policy|quy định", "Chính sách này có thể thay đổi hay bị trì hoãn không?"),
    (r"\d", "Con số này so với xu hướng gần đây thế nào?"),
]

_GENERIC_QUESTIONS = [
    "Giải thích ngắn gọn ý nghĩa của đoạn này?",
    "Có nguồn tin nào khác xác nhận thông tin này không?",
    "Điều này có thể ảnh hưởng thế nào đến doanh nghiệp Việt Nam?",
    "Xu hướng này dự kiến kéo dài bao lâu?",
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
