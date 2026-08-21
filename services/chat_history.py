"""Lưu trữ & bộ nhớ ngắn hạn cho Quote Chat, dùng Postgres.

Mỗi phiên (`ChatSession`) neo vào 1 quote + 1 người dùng + 1 báo cáo. Toàn bộ
tin nhắn (`ChatMessage`) được lưu lại đầy đủ (lịch sử tra cứu sau này), nhưng
chỉ `SHORT_TERM_MEMORY_TURNS` tin nhắn GẦN NHẤT được nạp lại làm context cho
LLM — đây là "bộ nhớ ngắn hạn": đủ để hội thoại mạch lạc qua vài câu hỏi tiếp
theo, nhưng không để prompt phình to vô hạn theo thời gian.
"""
import logging
from typing import List, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ChatMessage, ChatSession
from schemas.chat_models import ChatTurn

logger = logging.getLogger(__name__)

# Số tin nhắn gần nhất (user+assistant tính chung) nạp lại làm bộ nhớ ngắn hạn.
SHORT_TERM_MEMORY_TURNS = 12


async def create_session(
    session: AsyncSession, *, user_email: str, report_date: str, quote: str
) -> ChatSession:
    chat_session = ChatSession(user_email=user_email, report_date=report_date, quote=quote)
    session.add(chat_session)
    await session.commit()
    await session.refresh(chat_session)
    return chat_session


async def get_owned_session(
    session: AsyncSession, *, session_id: int, user_email: str, report_date: str
) -> Optional[ChatSession]:
    """Lấy phiên chat, đảm bảo đúng chủ sở hữu + đúng báo cáo — chặn user A đọc
    được phiên chat của user B qua việc đoán session_id."""
    stmt = select(ChatSession).where(
        ChatSession.id == session_id,
        ChatSession.user_email == user_email,
        ChatSession.report_date == report_date,
    )
    result = await session.execute(stmt)
    return result.scalars().first()


async def load_recent_turns(
    session: AsyncSession, *, session_id: int, limit: int = SHORT_TERM_MEMORY_TURNS
) -> List[ChatTurn]:
    """Bộ nhớ ngắn hạn: N tin nhắn gần nhất của phiên, theo đúng thứ tự thời gian.

    Sắp thêm `id` làm tiêu chí phụ: user+assistant của cùng 1 lượt được insert
    trong cùng 1 transaction nên `created_at` (server-side `now()`) trùng nhau
    hệt nhau — chỉ sort theo `created_at` sẽ đảo lộn thứ tự user/assistant.
    """
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    messages = list(reversed(result.scalars().all()))
    return [ChatTurn(role=m.role, content=m.content) for m in messages]


async def append_turn(
    session: AsyncSession, *, session_id: int, question: str, answer: str
) -> None:
    """Lưu lại 1 lượt hỏi-đáp sau khi đã stream xong câu trả lời đầy đủ."""
    session.add(ChatMessage(session_id=session_id, role="user", content=question))
    session.add(ChatMessage(session_id=session_id, role="assistant", content=answer))
    await session.execute(
        ChatSession.__table__.update()
        .where(ChatSession.id == session_id)
        .values(updated_at=func.now())
    )
    await session.commit()


async def list_sessions(
    session: AsyncSession, *, user_email: str, report_date: str
) -> Sequence[ChatSession]:
    """Danh sách các phiên Quote Chat của user cho 1 báo cáo, mới nhất trước."""
    stmt = (
        select(ChatSession)
        .where(ChatSession.user_email == user_email, ChatSession.report_date == report_date)
        .order_by(ChatSession.updated_at.desc())
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def list_session_messages(session: AsyncSession, *, session_id: int) -> Sequence[ChatMessage]:
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
    )
    result = await session.execute(stmt)
    return result.scalars().all()
