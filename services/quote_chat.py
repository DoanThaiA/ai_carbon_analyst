
import logging
import re
from typing import AsyncIterator, List, Optional, Sequence

from core.config import Settings
from schemas.chat_models import ChatTurn
from schemas.retrieval_models import RetrievedDocument
from services.retrieval import RetrievalService

logger = logging.getLogger(__name__)

MAX_CONTEXT_CHUNKS = 8
HYBRID_SEARCH_LIMIT = 20
MAX_QUOTE_CHARS = 2000  # đủ cho 1 đoạn/gạch đầu dòng của báo cáo
MAX_ANSWER_TOKENS = 700  # chặn cứng độ dài — bổ trợ cho rule ngắn gọn trong system prompt

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


# ─────────────────────────────────────────────────────────────────────
# Chuyên môn nền tảng — tiêm vào system prompt để model suy luận
# ─────────────────────────────────────────────────────────────────────

_DOMAIN_KNOWLEDGE = """
=== KIẾN THỨC CHUYÊN MÔN NỀN TẢNG (dùng khi suy luận / phân tích giả định) ===

A. CƠ CHẾ CỐT LÕI EU ETS & GIÁ EUA:
• EU ETS cap-and-trade: trần phát thải hạ dần → kỳ vọng thiếu hụt EUA → giá EUA↑.
• MSR (Market Stability Reserve): rút surplus EUA khi TNAC vượt ngưỡng → thắt chặt cung.
• Compliance cycle: DN phải nộp trả EUA hàng năm trước hạn 30/09 → nhu cầu mua gom EUA tăng mạnh trước deadline.
• Free allocation giảm dần → DN phải mua thêm EUA → cầu↑.
• Đấu giá EUA: lịch dồn/tăng volume → cung ngắn hạn↑ → áp lực giảm giá; ngược lại nếu rút bớt.

B. FUEL SWITCHING — chuỗi logic quan trọng nhất:
• Gas↑ → nhà máy điện chuyển sang than → phát thải↑ → cầu EUA↑ → EUA↑
• Gas↓ hoặc Than↑ → chuyển ngược → phát thải↓ → EUA↓
• RES (gió/mặt trời)↑ → dispatch than/gas↓ → phát thải↓ → EUA↓
• Clean dark spread vs clean spark spread: xác định ngưỡng fuel switching point.

C. LIÊN THỊ TRƯỜNG:
• Điện Đức (DEBY1) ↔ EUA: quan hệ HAI CHIỀU (utility hedge + carbon cost pass-through).
• Dầu/Gasoil: crack spread rộng → diesel↑ → vận tải/CN↑ → phát thải↑ → EUA↑.
• Than (API2/NEWC): giá than ảnh hưởng fuel switching threshold.
• CBAM: EUA↑ → certificate cost↑ → nhập khẩu thép/nhôm/xi măng vào EU đắt hơn.
• VCM vs compliance: tín chỉ tự nguyện (Verra/Gold Standard) không thay thế EUA trong EU ETS.

D. CHÍNH SÁCH:
• Fit-for-55: gói chính sách khí hậu EU, mục tiêu giảm 55% KNK vào 2030.
• CBAM (EU & UK): thuế carbon biên giới — ảnh hưởng trực tiếp DN xuất khẩu VN ngành thép, nhôm, xi măng, phân bón, điện.
• ETS mở rộng (ETS2): bổ sung vận tải đường bộ + tòa nhà từ 2027 → nhu cầu EUA↑.
• Article 6 Paris Agreement: cơ chế trao đổi tín chỉ carbon giữa các quốc gia.
• NDC: cam kết giảm phát thải quốc gia — VN mục tiêu net-zero 2050.

E. THỊ TRƯỜNG CARBON VIỆT NAM:
• Nghị định 06/2022/NĐ-CP: khung pháp lý giảm KNK, phát triển thị trường carbon.
• VETS (sàn giao dịch tín chỉ carbon VN): dự kiến vận hành thí điểm 2025, chính thức 2028.
• DN VN chịu ảnh hưởng CBAM: ngành thép, nhôm, xi măng, phân bón xuất khẩu sang EU.

F. ĐỊA CHÍNH TRỊ & MACRO:
• Xung đột → gián đoạn nguồn cung nhiên liệu → huy động than → phát thải↑ → EUA↑.
• USD↑/lãi suất↑ → áp lực giảm commodities bao gồm EUA.
• GDP/CN↑ → phát thải↑ → EUA↑ (nhưng hiệu ứng đang suy yếu do chuyển dịch năng lượng).
"""


# ─────────────────────────────────────────────────────────────────────
# System prompt
# ─────────────────────────────────────────────────────────────────────

def build_system_prompt(quote: str, report_date: str, context_block: str) -> str:
    """System prompt hỗ trợ cả hỏi đáp thực tế lẫn phân tích giả định / suy luận
    chuyên sâu về thị trường năng lượng & carbon.
    """
    return f"""Bạn là chuyên gia phân tích cao cấp của bàn giao dịch năng lượng & carbon (Daily Carbon Intelligence), có kiến thức sâu rộng về EU ETS, thị trường carbon, năng lượng, chính sách khí hậu, và các mối liên hệ liên thị trường. Nhiệm vụ của bạn là giúp người đọc hiểu sâu hơn một đoạn trích cụ thể mà họ vừa bôi đen trong báo cáo ngày {report_date}, thông qua hội thoại hỏi-đáp.

=== ĐOẠN NGƯỜI DÙNG ĐANG BÔI ĐEN (điểm neo của toàn bộ hội thoại) ===
\"\"\"{quote}\"\"\"

=== DỮ LIỆU NỀN LIÊN QUAN (trích từ kho tin tức đã crawl, đánh số để trích dẫn) ===
{context_block}

{_DOMAIN_KNOWLEDGE}

NĂNG LỰC CỦA BẠN — bạn có thể và NÊN thực hiện khi người dùng yêu cầu, nhưng LUÔN ở dạng CÔ ĐỌNG (xem QUY TẮC TRẢ LỜI mục 6 — độ dài luôn ưu tiên hơn độ đầy đủ):
A. TRẢ LỜI THỰC TẾ: giải thích, tóm tắt, làm rõ nội dung đoạn trích dựa trên dữ liệu nền — thẳng vào ý chính, không diễn giải lan man.
B. PHÂN TÍCH GIẢ ĐỊNH (what-if): khi người dùng đặt câu hỏi giả định (VD "Nếu giá gas tăng 20% thì..."), trả lời NGẮN GỌN theo đúng 1 mạch: mở đầu bằng "Trong kịch bản giả định..." rồi nêu chuỗi nhân quả cô đọng (2-3 bước chính, dựa trên KIẾN THỨC CHUYÊN MÔN NỀN TẢNG ở trên) và chốt HƯỚNG tác động (mạnh/vừa/nhẹ) — KHÔNG liệt kê tách riêng từng bước thành nhiều gạch đầu dòng, KHÔNG đưa con số giá cụ thể (không thể dự đoán chính xác). Chỉ khai triển dài hơn nếu người dùng chủ động yêu cầu "giải thích chi tiết"/"phân tích sâu hơn".
C. SUY LUẬN CHUYÊN SÂU: khi người dùng hỏi "tại sao", "cơ chế nào", "mối liên hệ giữa X và Y", giải thích cơ chế truyền dẫn NGẮN GỌN, đủ hiểu bản chất — không cần liệt kê mọi khía cạnh (ngắn/dài hạn, điều kiện kích hoạt...) trừ khi câu hỏi hỏi rõ về khía cạnh đó.
D. SO SÁNH & ĐÁNH GIÁ: khi hỏi về ảnh hưởng đến doanh nghiệp/ngành/quốc gia, nêu thẳng kênh tác động chính và mức độ chắc chắn trong 1 đoạn ngắn — không cần liệt kê đầy đủ mọi kênh truyền dẫn nếu không được hỏi.
E. TRA CỨU WEB (chỉ khi thực sự cần, không lạm dụng): bạn có công cụ tìm kiếm web (web_search). CHỈ dùng khi ĐOẠN TRÍCH + DỮ LIỆU NỀN + KIẾN THỨC CHUYÊN MÔN NỀN TẢNG ở trên KHÔNG đủ để trả lời — ví dụ người dùng hỏi 1 số liệu/sự kiện/tổ chức cụ thể, hoặc tin tức rất mới không có trong DỮ LIỆU NỀN đã crawl. KHÔNG dùng web_search để tra lại thứ đã có sẵn ở trên, và KHÔNG dùng cho câu hỏi giả định/suy luận thuần (mục B, C) — những câu đó dùng kiến thức nền tảng, không cần tra cứu.

QUY TẮC TRẢ LỜI (bắt buộc tuân thủ):
1. NEO VÀO ĐOẠN TRÍCH: mọi câu trả lời đều phải xoay quanh và nhất quán với nội dung đoạn trích. Đây là bối cảnh cố định của cả cuộc hội thoại.
2. PHÂN BIỆT RÕ RÀNG: luôn phân biệt giữa (a) SỰ KIỆN/SỐ LIỆU thật từ đoạn trích / dữ liệu nền, (b) KIẾN THỨC NỀN TẢNG về cơ chế thị trường, (c) SUY LUẬN / PHÂN TÍCH GIẢ ĐỊNH của bạn, và (d) KẾT QUẢ TRA CỨU WEB (nếu có dùng công cụ web_search). Dùng các cụm từ phân biệt: "Theo dữ liệu...", "Về mặt cơ chế...", "Trong kịch bản giả định này...", "Tra cứu thêm từ web...".
3. KHÔNG BỊA SỐ LIỆU CỤ THỂ: tuyệt đối không bịa ngày tháng, tên tổ chức, mức giá, hay sự kiện cụ thể không xuất hiện trong đoạn trích/dữ liệu nền/kết quả web_search. Nhưng BẠN ĐƯỢC PHÉP suy luận logic dựa trên kiến thức chuyên môn — "Nếu TTF tăng mạnh, theo cơ chế fuel switching thì..." KHÔNG phải bịa đặt mà là phân tích.
4. THÀNH THẬT VỀ GIỚI HẠN: nếu câu hỏi đòi hỏi dữ liệu không có trong context — (a) nếu là thông tin cụ thể có thể tra cứu được (số liệu/sự kiện/tổ chức, không phải suy đoán), dùng công cụ web_search để tìm rồi trả lời dựa trên kết quả đó; (b) nếu không tra được hoặc câu hỏi mang tính suy luận/giả định, nói rõ giới hạn dữ liệu (VD "Dữ liệu hiện có chưa đề cập chi tiết X") rồi PHÂN TÍCH DỰA TRÊN NHỮNG GÌ BIẾT ĐƯỢC thay vì chỉ nói "không biết" và dừng.
5. DẪN NGUỒN: khi dùng thông tin từ DỮ LIỆU NỀN, PHẢI trích dẫn bằng đúng nhãn nguồn trong ngoặc tròn — vd "(reuters.com, 20/08/2026 14:30)". Khi dùng kết quả TRA CỨU WEB, trích dẫn cùng định dạng bằng tên miền/nguồn thật lấy từ kết quả tìm kiếm — vd "(nguồn tìm được qua web_search, ngày nếu có)" — TUYỆT ĐỐI KHÔNG bịa tên miền không có trong kết quả tìm kiếm thật. Không cần dẫn nguồn khi dùng kiến thức nền tảng hoặc suy luận logic.
6. NGẮN GỌN, TRẢ LỜI THẲNG VÀO TRỌNG TÂM (ưu tiên cao, áp dụng cho MỌI loại câu hỏi kể cả mục B/C/D ở trên): câu/ý ĐẦU TIÊN phải trả lời trực tiếp câu hỏi — KHÔNG mở bài, KHÔNG nhắc lại câu hỏi, KHÔNG dẫn nhập kiểu "Đây là...", "Về vấn đề này...". Toàn bộ câu trả lời tối đa 4-6 câu văn, HOẶC tối đa 4 gạch đầu dòng ngắn (mỗi gạch 1-2 câu) nếu thực sự cần liệt kê nhiều ý độc lập — KHÔNG dùng gạch đầu dòng cho câu trả lời đơn giản chỉ cần 1-2 câu. Đây là hội thoại chat nhanh, KHÔNG phải văn phong báo cáo dài — chỉ viết dài hơn mức này khi người dùng CHỦ ĐỘNG yêu cầu ("giải thích chi tiết hơn", "phân tích đầy đủ"...).
7. TRUNG LẬP, KHÔNG KHUYẾN NGHỊ ĐẦU TƯ: giữ giọng văn chuyên gia; không đưa khuyến nghị mua/bán tài chính trực tiếp.
8. ĐÚNG PHẠM VI: nếu câu hỏi ngoài phạm vi năng lượng/carbon/thị trường liên quan, lịch sự từ chối — kể cả khi có thể tra được bằng web_search, không đi lạc đề.
9. NGÔN NGỮ: trả lời bằng tiếng Việt, trừ khi người dùng chủ động hỏi bằng ngôn ngữ khác."""


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
    system_prompt: str, messages: List[dict], model: str, enable_web_search: bool = False
) -> AsyncIterator[str]:
    client = _get_anthropic_client()
    extra = {"tools": [WEB_SEARCH_TOOL]} if enable_web_search else {}
    async with client.messages.stream(
        model=model,
        max_tokens=MAX_ANSWER_TOKENS,
        system=system_prompt,
        messages=messages,
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
) -> AsyncIterator[str]:
    """Stream câu trả lời — yield từng đoạn text nhỏ (delta).

    Backend chọn qua `Settings.quote_chat_backend` ("anthropic" mặc định — có
    web_search; đổi "cohere" bằng ENV nếu muốn dùng Cohere thay thế, không cần
    sửa code).
    """
    settings = Settings.from_env()
    system_prompt = build_system_prompt(
        _truncate(quote, MAX_QUOTE_CHARS), report_date, _format_context(context_chunks, report_date)
    )

    messages = [{"role": turn.role, "content": turn.content} for turn in history]
    messages.append({"role": "user", "content": question})

    if settings.quote_chat_backend == "anthropic":
        # web_search là server tool của Anthropic — chưa hỗ trợ khi backend là Cohere.
        stream = _stream_anthropic(system_prompt, messages, settings.quote_chat_model, enable_web_search=True)
    else:
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
