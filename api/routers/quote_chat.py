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
    ChatSessionRatingRequest,
    ChatSessionSummary,
    ChatTurn,
    QuoteChatRequest,
    SuggestedQuestionsRequest,
    SuggestedQuestionsResponse,
)
from services.chat_history import (
    append_turn,
    count_user_questions_since,
    create_session,
    get_owned_session,
    list_session_messages,
    list_sessions,
    load_recent_turns,
    set_session_rating,
    start_of_today_vn_utc,
)
from services.embedding import CohereEmbedder
from services.eua_framework_admin import get_overrides_map
from services.quote_chat import (
    astream_quote_chat,
    get_prices_text_for_chat,
    retrieve_context_for_quote,
    suggest_questions,
)
from services.retrieval import RetrievalService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reports/{date}/quote-chat", tags=["quote-chat"])

# Giới hạn số câu hỏi/ngày cho mỗi user (theo ngày dương lịch giờ VN, xem
# `start_of_today_vn_utc`) — chặn spam/chi phí LLM (đặc biệt với web_search
# server tool có thể tốn thêm request). Tính chung cho cả tính năng Quote Chat
# (mọi báo cáo/phiên của user), không phải riêng từng báo cáo.
QUOTE_CHAT_DAILY_LIMIT = 10

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
        ChatSessionSummary(
            id=s.id,
            quote=s.quote,
            created_at=s.created_at,
            updated_at=s.updated_at,
            rating=s.rating,
            rating_reason=s.rating_reason,
        )
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
        rating=chat_session.rating,
        rating_reason=chat_session.rating_reason,
    )


@router.post("/sessions/{session_id}/rating", response_model=ChatSessionSummary)
async def rate_session(
    date: str,
    session_id: int,
    body: ChatSessionRatingRequest,
    session: AsyncSession = Depends(get_db),
    payload: dict = Depends(get_current_user),
):
    """Người dùng đánh giá 1 phiên Quote Chat là tốt/không tốt (kèm lý do khi
    không tốt) — hiển thị lại ở màn hình admin quản lý đánh giá chat."""
    chat_session = await set_session_rating(
        session,
        session_id=session_id,
        user_email=payload["sub"],
        report_date=date,
        rating=body.rating,
        reason=body.reason,
    )
    if chat_session is None:
        raise HTTPException(status_code=404, detail="Chat session not found")

    return ChatSessionSummary(
        id=chat_session.id,
        quote=chat_session.quote,
        created_at=chat_session.created_at,
        updated_at=chat_session.updated_at,
        rating=chat_session.rating,
        rating_reason=chat_session.rating_reason,
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

    # Chặn TRƯỚC khi tạo session mới/gọi LLM — tránh tạo session mồ côi khi user
    # đã hết quota.
    asked_today = await count_user_questions_since(
        session, user_email=user_email, since=start_of_today_vn_utc()
    )
    if asked_today >= QUOTE_CHAT_DAILY_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Bạn đã đạt giới hạn {QUOTE_CHAT_DAILY_LIMIT} câu hỏi/ngày cho tính năng "
                "Hỏi đáp AI. Vui lòng quay lại vào ngày mai."
            ),
        )

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
            prices_text = await get_prices_text_for_chat(session, date)
            eua_framework_overrides = await get_overrides_map(session)
            # Gửi session_id + nguồn tham khảo trước khi bắt đầu stream câu trả
            # lời, để FE lưu lại session_id cho các câu hỏi tiếp theo và render
            # "Danh sách tin tức tham khảo" (link + ngày phát hành) dưới câu trả lời.
            # Dedupe theo bài viết — 1 bài có thể góp nhiều chunk vào dữ liệu nền.
            sources = []
            seen_article_ids = set()
            for c in context_chunks:
                if c.source_type != "article" or not c.url or c.source_id in seen_article_ids:
                    continue
                seen_article_ids.add(c.source_id)
                sources.append(
                    {
                        "url": c.url,
                        "title": c.title,
                        "source_name": c.source_name,
                        "published_at": c.published_at.isoformat() if c.published_at else None,
                    }
                )
            yield _sse("meta", {"session_id": chat_session.id, "sources": sources})

            answer_parts: list[str] = []
            async for delta in astream_quote_chat(
                quote=quote,
                question=body.question,
                report_date=date,
                history=history,
                context_chunks=context_chunks,
                prices_text=prices_text,
                eua_framework_overrides=eua_framework_overrides,
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
