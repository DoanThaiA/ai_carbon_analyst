"""Quản lý tài liệu tri thức của Jenny (.pdf/.docx) — chỉ admin. Luồng tải lên giống file đính kèm chat: xin
presigned URL → FE PUT thẳng lên MinIO (prefix knowledge/) → POST metadata + file_key → backend kiểm file THẬT
(dung lượng, magic bytes, trùng nội dung) rồi xử lý nền (trích chữ → chia đoạn → nhúng vector). Xem
services/knowledge.py.
"""
import asyncio
import logging
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import async_session_maker, get_current_admin, get_db
from api.routers.quote_chat import _get_embedder
from schemas.knowledge_models import (
    KnowledgeChunkOut,
    KnowledgeCreateRequest,
    KnowledgeDocumentDetail,
    KnowledgeDocumentOut,
    KnowledgeDownloadUrl,
    KnowledgePresignRequest,
    KnowledgePresignResponse,
    KnowledgeSearchHit,
    KnowledgeSearchRequest,
    KnowledgeSection,
    KnowledgeUpdateRequest,
)
from services import knowledge, minio_service
from services.retrieval import RetrievalService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/knowledge",
    tags=["admin-knowledge"],
    dependencies=[Depends(get_current_admin)],
)

# Cột NOT NULL — PATCH gửi null cho các trường này bị bỏ qua (không ghi đè thành NULL).
_NOT_NULL_FIELDS = {"title", "doc_kind", "language", "use_in_report", "use_in_chat", "valid_from", "verified", "is_active"}


def _raise(e: knowledge.KnowledgeError) -> None:
    raise HTTPException(status_code=e.status_code, detail=str(e))


async def _process_job(doc_id: int) -> None:
    await knowledge.process_document(async_session_maker, _get_embedder(), doc_id)


def _detail(doc) -> KnowledgeDocumentDetail:
    return KnowledgeDocumentDetail.model_validate(doc).model_copy(
        update={"sections": [KnowledgeSection(**s) for s in doc.sections or []]}
    )


@router.post("/presigned-url", response_model=KnowledgePresignResponse)
async def presign_upload(body: KnowledgePresignRequest):
    try:
        result = await asyncio.to_thread(
            minio_service.generate_knowledge_upload_url, body.file_name, body.content_type, body.size
        )
    except minio_service.InvalidAttachmentError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return KnowledgePresignResponse(upload_url=result.upload_url, file_key=result.file_key)


@router.post("", response_model=KnowledgeDocumentOut, status_code=201)
async def create_document(
    body: KnowledgeCreateRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db),
    payload: dict = Depends(get_current_admin),
):
    fields = body.model_dump(exclude={"file_key", "file_name"}, exclude_none=True)
    try:
        doc = await knowledge.create_document(
            session, file_key=body.file_key, file_name=body.file_name,
            uploaded_by=payload.get("sub") or "admin", fields=fields,
        )
    except knowledge.KnowledgeError as e:
        _raise(e)
    background_tasks.add_task(_process_job, doc.id)
    return doc


@router.get("", response_model=List[KnowledgeDocumentOut])
async def list_documents(session: AsyncSession = Depends(get_db)):
    return await knowledge.list_documents(session)


@router.get("/{doc_id}", response_model=KnowledgeDocumentDetail)
async def get_document(doc_id: int, session: AsyncSession = Depends(get_db)):
    try:
        return _detail(await knowledge.get_document(session, doc_id))
    except knowledge.KnowledgeError as e:
        _raise(e)


@router.get("/{doc_id}/chunks", response_model=List[KnowledgeChunkOut])
async def get_chunks(doc_id: int, session: AsyncSession = Depends(get_db)):
    try:
        await knowledge.get_document(session, doc_id)
    except knowledge.KnowledgeError as e:
        _raise(e)
    return [
        KnowledgeChunkOut(
            chunk_id=c.chunk_id, chunk_index=c.chunk_index, content=c.content,
            heading=(c.meta or {}).get("heading"), page_from=(c.meta or {}).get("page_from"),
            page_to=(c.meta or {}).get("page_to"),
        )
        for c in await knowledge.chunks_of(session, doc_id)
    ]


@router.patch("/{doc_id}", response_model=KnowledgeDocumentOut)
async def update_document(doc_id: int, body: KnowledgeUpdateRequest, session: AsyncSession = Depends(get_db)):
    fields = {
        k: v for k, v in body.model_dump(exclude_unset=True).items()
        if not (v is None and k in _NOT_NULL_FIELDS)
    }
    try:
        return await knowledge.update_document(session, doc_id, fields)
    except knowledge.KnowledgeError as e:
        await session.rollback()
        _raise(e)


@router.post("/{doc_id}/reprocess", response_model=KnowledgeDocumentOut)
async def reprocess_document(doc_id: int, background_tasks: BackgroundTasks, session: AsyncSession = Depends(get_db)):
    try:
        doc = await knowledge.mark_for_reprocess(session, doc_id)
    except knowledge.KnowledgeError as e:
        _raise(e)
    background_tasks.add_task(_process_job, doc.id)
    return doc


@router.get("/{doc_id}/download-url", response_model=KnowledgeDownloadUrl)
async def download_url(doc_id: int, session: AsyncSession = Depends(get_db)):
    try:
        doc = await knowledge.get_document(session, doc_id)
    except knowledge.KnowledgeError as e:
        _raise(e)
    if not doc.file_key:
        raise HTTPException(status_code=404, detail="Không còn file gốc.")
    return KnowledgeDownloadUrl(url=await asyncio.to_thread(minio_service.generate_presigned_get_url, doc.file_key))


@router.delete("/{doc_id}")
async def delete_document(doc_id: int, session: AsyncSession = Depends(get_db)):
    try:
        await knowledge.delete_document(session, doc_id)
    except knowledge.KnowledgeError as e:
        _raise(e)
    return {"message": "Đã xoá tài liệu."}


@router.post("/search-test", response_model=List[KnowledgeSearchHit])
async def search_test(body: KnowledgeSearchRequest, session: AsyncSession = Depends(get_db)):
    """Thử truy hồi giống hệt lúc sinh báo cáo/chat (cùng ngưỡng rerank) — để admin kiểm tài liệu có được tìm thấy."""
    retrieval = RetrievalService(embedder=_get_embedder(), session=session)
    try:
        hits = await retrieval.retrieve_knowledge(
            body.query, purpose=body.purpose, as_of=body.as_of or knowledge.vn_today(),
            section=body.section if body.purpose == "report" else None, doc_id=body.doc_id,
            **knowledge.search_options(knowledge.CHAT_MAX_HITS),
        )
    except Exception:
        logger.exception("[KNOWLEDGE] search-test lỗi: %s", body.query)
        raise HTTPException(status_code=502, detail="Không tra được (lỗi embedding/rerank) — xem log server.")
    return [
        KnowledgeSearchHit(
            chunk_id=h.chunk_id, doc_id=h.doc_id, title=h.title, score=h.score, content=h.content,
            heading=h.heading, page_from=h.page_from, page_to=h.page_to,
        )
        for h in hits
    ]
