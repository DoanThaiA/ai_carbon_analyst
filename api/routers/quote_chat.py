"""Quote Chat — người dùng bôi đen 1 đoạn trong báo cáo và hỏi đáp về đoạn đó.

- POST .../suggestions: trả câu hỏi gợi ý (heuristic, tức thời, không gọi LLM).
- POST .../ (root): stream câu trả lời qua SSE (Server-Sent Events). Dùng
  `fetch` + đọc `ReadableStream` ở FE thay vì `EventSource` gốc, vì EventSource
  không hỗ trợ POST body — cần gửi quote/question có thể khá dài.
- GET .../sessions, GET .../sessions/{id}: xem lại các phiên chat đã lưu.

Lịch sử hội thoại là bộ nhớ ngắn hạn lưu ở Postgres (services/chat_history.py):
mỗi phiên neo vào 1 quote + 1 user; N tin nhắn gần nhất được nạp lại làm context
cho LLM, toàn bộ lịch sử vẫn được lưu lại đầy đủ để xem lại sau.
"""
import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db
from db.models import Report
from schemas.chat_models import (
    ChatSessionDetail,
    ChatSessionSummary,
    ChatTurn,
    QuoteChatRequest,
    SuggestedQuestionsRequest,
    SuggestedQuestionsResponse,
)
from services.chat_history import (
    append_turn,
    create_session,
    get_owned_session,
    list_session_messages,
    list_sessions,
    load_recent_turns,
)
from services.embedding import CohereEmbedder
from services.quote_chat import astream_quote_chat, retrieve_context_for_quote, suggest_questions
from services.retrieval import RetrievalService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reports/{date}/quote-chat", tags=["quote-chat"])

# Lazy singleton — embedder dùng chung cho mọi request, tránh tạo lại Cohere
# client mỗi lần (giống pattern report_generator.py / embedding.py).
_embedder: CohereEmbedder | None = None


def _get_embedder() -> CohereEmbedder:
    global _embedder
    if _embedder is None:
        _embedder = CohereEmbedder()
    return _embedder


async def _ensure_report_published(date: str, session: AsyncSession) -> None:
    """Chỉ cho hỏi đáp trên báo cáo đã published — khớp quyền truy cập với
    GET /api/reports/{date} của user (tránh lộ nội dung draft chưa duyệt)."""
    stmt = select(Report.id).where(Report.report_date == date, Report.status == "published")
    result = await session.execute(stmt)
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Report not found")


def _sse(event: str, data) -> str:
    """1 sự kiện SSE. Luôn JSON-encode `data` (kể cả string) để tránh newline
    trong text (vd token trả lời của LLM) làm vỡ khung `data: ...\\n\\n`."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/suggestions", response_model=SuggestedQuestionsResponse)
async def get_suggested_questions(
    date: str,
    body: SuggestedQuestionsRequest,
    session: AsyncSession = Depends(get_db),
    _payload: dict = Depends(get_current_user),
):
    await _ensure_report_published(date, session)
    return SuggestedQuestionsResponse(questions=suggest_questions(body.quote))


@router.get("/sessions", response_model=list[ChatSessionSummary])
async def get_sessions(
    date: str,
    session: AsyncSession = Depends(get_db),
    payload: dict = Depends(get_current_user),
):
    """Danh sách các phiên Quote Chat đã lưu của user hiện tại cho báo cáo này."""
    sessions = await list_sessions(session, user_email=payload["sub"], report_date=date)
    return [
        ChatSessionSummary(id=s.id, quote=s.quote, created_at=s.created_at, updated_at=s.updated_at)
        for s in sessions
    ]


@router.get("/sessions/{session_id}", response_model=ChatSessionDetail)
async def get_session_detail(
    date: str,
    session_id: int,
    session: AsyncSession = Depends(get_db),
    payload: dict = Depends(get_current_user),
):
    chat_session = await get_owned_session(
        session, session_id=session_id, user_email=payload["sub"], report_date=date
    )
    if chat_session is None:
        raise HTTPException(status_code=404, detail="Chat session not found")

    messages = await list_session_messages(session, session_id=session_id)
    return ChatSessionDetail(
        id=chat_session.id,
        quote=chat_session.quote,
        created_at=chat_session.created_at,
        messages=[ChatTurn(role=m.role, content=m.content) for m in messages],
    )


@router.post("")
async def quote_chat_stream(
    date: str,
    body: QuoteChatRequest,
    session: AsyncSession = Depends(get_db),
    payload: dict = Depends(get_current_user),
):
    await _ensure_report_published(date, session)
    user_email = payload["sub"]

    if body.session_id is not None:
        chat_session = await get_owned_session(
            session, session_id=body.session_id, user_email=user_email, report_date=date
        )
        if chat_session is None:
            raise HTTPException(status_code=404, detail="Chat session not found")
        quote = chat_session.quote
    else:
        if not body.quote:
            raise HTTPException(status_code=400, detail="quote là bắt buộc khi bắt đầu phiên chat mới")
        quote = body.quote
        chat_session = await create_session(session, user_email=user_email, report_date=date, quote=quote)

    # Bộ nhớ ngắn hạn — nạp lại vài tin nhắn gần nhất của phiên từ Postgres,
    # KHÔNG dựa vào lịch sử client tự gửi lại.
    history = await load_recent_turns(session, session_id=chat_session.id)

    retrieval_service = RetrievalService(embedder=_get_embedder(), session=session)

    async def event_stream() -> AsyncIterator[str]:
        try:
            context_chunks = await retrieve_context_for_quote(retrieval_service, quote, body.question, date)
            # Gửi session_id + nguồn tham khảo trước khi bắt đầu stream câu trả
            # lời, để FE lưu lại session_id cho các câu hỏi tiếp theo.
            sources = [
                {"chunk_id": c.chunk_id, "source_type": c.source_type, "source_id": c.source_id}
                for c in context_chunks
            ]
            yield _sse("meta", {"session_id": chat_session.id, "sources": sources})

            answer_parts: list[str] = []
            async for delta in astream_quote_chat(
                quote=quote,
                question=body.question,
                report_date=date,
                history=history,
                context_chunks=context_chunks,
            ):
                answer_parts.append(delta)
                yield _sse("delta", delta)

            await append_turn(
                session,
                session_id=chat_session.id,
                question=body.question,
                answer="".join(answer_parts),
            )

            yield _sse("done", {"session_id": chat_session.id})
        except Exception as e:
            logger.error("[QUOTE-CHAT] Lỗi khi xử lý stream: %s", e)
            yield _sse("error", {"message": "Đã xảy ra lỗi khi xử lý câu hỏi, vui lòng thử lại."})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # tắt buffering ở reverse proxy (nginx) nếu có
        },
    )
