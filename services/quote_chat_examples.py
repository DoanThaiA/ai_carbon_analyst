"""Ví dụ mẫu (few-shot) cho Quote Chat — admin chọn 1 cặp hỏi-đáp trong lịch sử
chat mà mình đánh giá là phân tích hợp lý (xem api/routers/admin_chat_reviews.py),
hệ thống tiêm lại các cặp này vào system prompt của MỌI phiên Quote Chat sau đó
làm chuẩn tham khảo về cách suy luận/văn phong — xem db.models.QuoteChatExample.
"""
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ChatMessage, ChatSession, QuoteChatExample

# Giới hạn số ví dụ tiêm vào prompt mỗi request — chặn prompt phình vô hạn khi
# admin tích luỹ nhiều ví dụ theo thời gian; lấy N ví dụ MỚI NHẤT (admin liên
# tục chọn lọc nên ví dụ mới thường đã thay thế/cải thiện ví dụ cũ).
MAX_INJECTED_EXAMPLES = 5


async def create_example_from_message(
    session: AsyncSession, *, session_id: int, answer_message_id: int, created_by: Optional[str]
) -> QuoteChatExample:
    """Tạo ví dụ mẫu từ 1 câu trả lời cụ thể — tự tra lại câu hỏi liền trước
    trong CÙNG phiên từ DB (không tin question/answer client tự gửi lên, tránh
    admin/FE sửa nội dung khi submit). Idempotent theo source_answer_message_id
    — bấm "thêm" nhiều lần không tạo trùng, trả về row đã có.

    Raise ValueError (câu trả lời không tồn tại/không thuộc phiên/không phải
    role assistant, hoặc không tìm được câu hỏi liền trước) để router trả 400/404.
    """
    existing = await session.execute(
        select(QuoteChatExample).where(QuoteChatExample.source_answer_message_id == answer_message_id)
    )
    existing_row = existing.scalars().first()
    if existing_row is not None:
        return existing_row

    answer_msg = await session.get(ChatMessage, answer_message_id)
    if answer_msg is None or answer_msg.session_id != session_id or answer_msg.role != "assistant":
        raise ValueError("Không tìm thấy câu trả lời tương ứng trong phiên này.")

    question_stmt = (
        select(ChatMessage)
        .where(
            ChatMessage.session_id == session_id,
            ChatMessage.role == "user",
            ChatMessage.id < answer_msg.id,
        )
        .order_by(ChatMessage.id.desc())
        .limit(1)
    )
    question_msg = (await session.execute(question_stmt)).scalars().first()
    if question_msg is None:
        raise ValueError("Không tìm thấy câu hỏi tương ứng với câu trả lời này.")

    example = QuoteChatExample(
        question=question_msg.content,
        answer=answer_msg.content,
        source_session_id=session_id,
        source_answer_message_id=answer_message_id,
        created_by=created_by,
    )
    session.add(example)
    await session.commit()
    await session.refresh(example)
    return example


async def list_examples(session: AsyncSession) -> List[Tuple[QuoteChatExample, Optional[str], Optional[str]]]:
    """Danh sách ví dụ cho màn hình admin quản lý, mới nhất trước — kèm
    report_date/quote của phiên gốc (outer join, None nếu phiên gốc đã bị xoá)
    để admin biết bối cảnh ví dụ đến từ đâu."""
    stmt = (
        select(QuoteChatExample, ChatSession.report_date, ChatSession.quote)
        .outerjoin(ChatSession, QuoteChatExample.source_session_id == ChatSession.id)
        .order_by(QuoteChatExample.created_at.desc())
    )
    result = await session.execute(stmt)
    return [(row[0], row[1], row[2]) for row in result.all()]


async def delete_example(session: AsyncSession, *, example_id: int) -> bool:
    example = await session.get(QuoteChatExample, example_id)
    if example is None:
        return False
    await session.delete(example)
    await session.commit()
    return True


async def get_example_map_for_session(session: AsyncSession, *, session_id: int) -> Dict[int, int]:
    """Map {source_answer_message_id: example_id} cho 1 phiên — dùng để hiện
    trạng thái "đã thêm ví dụ mẫu" ở màn hình admin xem chi tiết phiên chat."""
    stmt = select(QuoteChatExample.source_answer_message_id, QuoteChatExample.id).where(
        QuoteChatExample.source_session_id == session_id
    )
    result = await session.execute(stmt)
    return {msg_id: example_id for msg_id, example_id in result.all() if msg_id is not None}


async def build_few_shot_prompt_block(session: AsyncSession) -> str:
    """Ghép các ví dụ mẫu (tối đa MAX_INJECTED_EXAMPLES, mới nhất) thành 1 khối
    text để tiêm vào system prompt của Quote Chat — rỗng nếu chưa có ví dụ nào
    (không tiêm thêm gì, không đổi hành vi hiện tại)."""
    stmt = select(QuoteChatExample).order_by(QuoteChatExample.created_at.desc()).limit(MAX_INJECTED_EXAMPLES)
    result = await session.execute(stmt)
    examples: Sequence[QuoteChatExample] = list(result.scalars().all())
    if not examples:
        return ""

    parts = [
        "=== VÍ DỤ MẪU ĐÃ ĐƯỢC ADMIN CHỌN LỌC (few-shot) ===\n"
        "Đây là các cặp hỏi-đáp admin đã xem lại và đánh giá là PHÂN TÍCH HỢP LÝ, dùng làm chuẩn "
        "tham khảo về CÁCH SUY LUẬN và VĂN PHONG trả lời — TUYỆT ĐỐI KHÔNG dùng làm nguồn số liệu/sự "
        "kiện: các ví dụ này có thể thuộc đoạn trích/báo cáo NGÀY KHÁC với phiên hiện tại, KHÔNG được "
        "chép lại số liệu, tên sự kiện, hay kết luận cụ thể trong ví dụ vào câu trả lời cho câu hỏi "
        "hiện tại — chỉ học cách trình bày, mức độ ngắn gọn, cách trích dẫn, cách kết luận."
    ]
    # Đảo lại thành cũ -> mới khi in ra, để ví dụ MỚI NHẤT (admin vừa chọn) nằm gần cuối khối,
    # gần vị trí model đọc trước khi trả lời.
    for i, ex in enumerate(reversed(examples), start=1):
        parts.append(f"--- Ví dụ {i} ---\nCâu hỏi: {ex.question}\nCâu trả lời mẫu: {ex.answer}")
    return "\n\n".join(parts)
