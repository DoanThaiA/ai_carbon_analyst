"""Quản lý ví dụ mẫu (few-shot) cho Quote Chat — admin chọn 1 câu trả lời đã
đánh giá tốt trong lịch sử chat (xem admin_chat_reviews.py) để tiêm lại vào
system prompt của các phiên chat sau — xem services/quote_chat_examples.py.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_admin, get_db
from schemas.quote_chat_examples_models import QuoteChatExample, QuoteChatExampleCreateRequest
from services.quote_chat_examples import create_example_from_message, delete_example, list_examples

router = APIRouter(
    prefix="/api/admin/quote-chat-examples",
    tags=["admin-quote-chat-examples"],
    dependencies=[Depends(get_current_admin)],
)


def _to_schema(row, report_date, quote) -> QuoteChatExample:
    return QuoteChatExample(
        id=row.id,
        question=row.question,
        answer=row.answer,
        source_session_id=row.source_session_id,
        source_report_date=report_date,
        source_quote=quote,
        created_by=row.created_by,
        created_at=row.created_at,
    )


@router.get("", response_model=list[QuoteChatExample])
async def get_examples(session: AsyncSession = Depends(get_db)):
    rows = await list_examples(session)
    return [_to_schema(row, report_date, quote) for row, report_date, quote in rows]


@router.post("", response_model=QuoteChatExample, status_code=201)
async def add_example(
    body: QuoteChatExampleCreateRequest,
    session: AsyncSession = Depends(get_db),
    payload: dict = Depends(get_current_admin),
):
    try:
        example = await create_example_from_message(
            session,
            session_id=body.session_id,
            answer_message_id=body.answer_message_id,
            created_by=payload.get("sub"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _to_schema(example, None, None)


@router.delete("/{example_id}")
async def remove_example(example_id: int, session: AsyncSession = Depends(get_db)):
    ok = await delete_example(session, example_id=example_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Không tìm thấy ví dụ mẫu này.")
    return {"message": "Đã xoá ví dụ mẫu."}
