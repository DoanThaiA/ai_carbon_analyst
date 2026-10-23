"""Admin: biên bản QC báo cáo do Claude (vai Jack) gửi qua MCP — CHỈ admin (cookie JWT role admin).

- GET  /api/admin/qc-reviews                     danh sách tổng hợp mọi biên bản (trang "QC Result")
- GET  /api/admin/qc-reviews/stats               thống kê nhanh (tỷ lệ kết luận, điểm TB từng tiêu chí, lỗi lặp)
- GET  /api/admin/qc-reviews/{id}                chi tiết 1 biên bản (đủ findings + body_markdown)
- GET  /api/admin/qc-reviews/report/{date}       biên bản mới nhất + lịch sử của 1 báo cáo (panel cạnh báo cáo)
- PATCH /api/admin/qc-reviews/findings/{id}      đánh dấu lỗi đã sửa / không sửa
- POST /api/admin/qc-reviews/report/{date}/fix-preview   đề xuất sửa bản nháp theo biên bản QC (không ghi gì)
- POST /api/admin/qc-reviews/report/{date}/fix-apply     ghi các thay đổi admin đã chọn (hoặc hoàn tác)
- GET/POST/DELETE /api/admin/qc-reviews/agent-tokens   token scope 'qc_agent' cho lịch Claude QC 07:30

Ẩn/hiện theo trạng thái báo cáo (chỉ hiện nút "Kết quả QC" khi báo cáo CHƯA xuất bản) là việc của giao diện
trang báo cáo; trang tổng hợp QC Result vẫn liệt kê đủ để admin xem lại lịch sử.
"""
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import async_session_maker, get_current_admin, get_db, settings
from db.models import Report, ReportQcFinding, ReportQcReview
from services.claude_connect import (
    MAX_ACTIVE_QC_AGENT_TOKENS,
    count_active_tokens,
    create_api_token,
    list_qc_agent_tokens,
    revoke_api_token,
)
from services.report_qc_fix import QcFixError, apply_fix_changes, build_fix_preview
from services.report_qc_review import (
    FINDING_STATUSES,
    QC_CRITERIA,
    QC_ROUTINE_PROMPT,
    VERDICTS,
    QcSubmissionError,
    content_hash,
    finding_dict,
    load_findings,
    publish_gate,
    review_dict,
    update_finding_status,
)

router = APIRouter(
    prefix="/api/admin/qc-reviews",
    tags=["admin-qc-reviews"],
    dependencies=[Depends(get_current_admin)],
)


class FindingStatusUpdate(BaseModel):
    status: str = Field(..., description="open | fixed | wont_fix")
    note: Optional[str] = Field(default=None, max_length=1000)


class QcFixPreviewRequest(BaseModel):
    finding_ids: Optional[List[int]] = Field(default=None, description="Trống = mọi lỗi còn mở của biên bản mới nhất")
    review_id: Optional[int] = None


class QcFixChange(BaseModel):
    path: str = Field(..., max_length=300)
    before: str
    after: str
    finding_ids: List[int] = Field(default_factory=list)


class QcFixApplyRequest(BaseModel):
    base_hash: str
    changes: List[QcFixChange] = Field(..., max_length=200)
    run_no: Optional[int] = None
    undo: bool = Field(default=False, description="Hoàn tác: changes đã đảo before/after, lỗi trả về 'open'")


class QcAgentTokenCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)


async def _current_hashes(session: AsyncSession, dates: list) -> dict:
    if not dates:
        return {}
    rows = (await session.execute(
        select(Report.report_date, Report.status, Report.content).where(Report.report_date.in_(set(dates)))
    )).all()
    return {r.report_date: (r.status, content_hash(r.content) if r.content else None) for r in rows}


@router.get("")
async def list_reviews(
    session: AsyncSession = Depends(get_db),
    verdict: Optional[str] = Query(default=None),
    report_date: Optional[str] = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    latest_only: bool = Query(default=False, description="Chỉ biên bản mới nhất của mỗi báo cáo"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    stmt = select(ReportQcReview)
    if verdict:
        if verdict not in VERDICTS:
            raise HTTPException(status_code=422, detail=f"verdict ∈ {VERDICTS}")
        stmt = stmt.where(ReportQcReview.verdict == verdict)
    if report_date:
        stmt = stmt.where(ReportQcReview.report_date == report_date)
    if latest_only:
        latest = (
            select(ReportQcReview.report_date, func.max(ReportQcReview.run_no).label("run_no"))
            .group_by(ReportQcReview.report_date).subquery()
        )
        stmt = stmt.join(latest, (latest.c.report_date == ReportQcReview.report_date)
                         & (latest.c.run_no == ReportQcReview.run_no))
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    reviews = list((await session.execute(
        stmt.order_by(ReportQcReview.report_date.desc(), ReportQcReview.run_no.desc()).limit(limit).offset(offset)
    )).scalars())
    states = await _current_hashes(session, [r.report_date for r in reviews])
    findings = await load_findings(session, [r.id for r in reviews])
    items = []
    for r in reviews:
        status, current = states.get(r.report_date, (None, None))
        items.append({**review_dict(r, findings[r.id], current_hash=current, with_body=False),
                      "current_report_status": status})
    return {"total": total, "items": items, "criteria": QC_CRITERIA}


@router.get("/stats")
async def review_stats(session: AsyncSession = Depends(get_db), last: int = Query(default=30, ge=1, le=365)):
    """Trên `last` báo cáo gần nhất (biên bản mới nhất mỗi báo cáo): phân bố kết luận, điểm TB, điểm TB từng tiêu
    chí và lỗi lặp nhiều nhất (theo fingerprint)."""
    latest = (
        select(ReportQcReview.report_date, func.max(ReportQcReview.run_no).label("run_no"))
        .group_by(ReportQcReview.report_date).order_by(ReportQcReview.report_date.desc()).limit(last).subquery()
    )
    reviews = list((await session.execute(
        select(ReportQcReview).join(latest, (latest.c.report_date == ReportQcReview.report_date)
                                    & (latest.c.run_no == ReportQcReview.run_no))
        .order_by(ReportQcReview.report_date)
    )).scalars())
    by_verdict = {v: sum(1 for r in reviews if r.verdict == v) for v in VERDICTS}
    criteria_avg = []
    for c in QC_CRITERIA:
        scores = [x["score"] for r in reviews for x in (r.criteria or []) if x.get("key") == c["key"]]
        criteria_avg.append({"key": c["key"], "label": c["label"], "weight": c["weight"],
                             "avg": round(sum(scores) / len(scores), 2) if scores else None})
    recurring = []
    if reviews:
        rows = (await session.execute(
            select(ReportQcFinding.fingerprint, func.count(func.distinct(ReportQcFinding.review_id)).label("n"),
                   func.max(ReportQcFinding.title), func.max(ReportQcFinding.criterion),
                   func.max(ReportQcFinding.section))
            .where(ReportQcFinding.review_id.in_([r.id for r in reviews]))
            .group_by(ReportQcFinding.fingerprint)
            .having(func.count(func.distinct(ReportQcFinding.review_id)) > 1)
            .order_by(func.count(func.distinct(ReportQcFinding.review_id)).desc()).limit(10)
        )).all()
        recurring = [{"fingerprint": fp, "reports": n, "title": t, "criterion": c, "section": s}
                     for fp, n, t, c, s in rows]
    return {
        "reports": len(reviews),
        "by_verdict": by_verdict,
        "avg_score": round(sum(float(r.overall_score) for r in reviews) / len(reviews), 2) if reviews else None,
        "trend": [{"report_date": r.report_date, "score": float(r.overall_score), "verdict": r.verdict} for r in reviews],
        "criteria_avg": criteria_avg,
        "recurring": recurring,
    }


@router.get("/agent-tokens")
async def get_agent_tokens(session: AsyncSession = Depends(get_db)):
    tokens = await list_qc_agent_tokens(session)
    return {
        "items": [
            {"id": t.id, "name": t.name, "token_prefix": t.token_prefix, "created_by": t.user_email,
             "created_at": t.created_at, "expires_at": t.expires_at, "last_used_at": t.last_used_at,
             "revoked_at": t.revoked_at}
            for t in tokens
        ],
        "routine_prompt": QC_ROUTINE_PROMPT,
        "auto_report_time": settings.auto_report_time,
        "auto_qc_fix_time": settings.auto_qc_fix_time or None,
        "auto_publish_time": settings.auto_publish_time or None,
    }


@router.post("/agent-tokens", status_code=201)
async def create_agent_token(
    body: QcAgentTokenCreate, session: AsyncSession = Depends(get_db), admin: dict = Depends(get_current_admin)
):
    if await count_active_tokens(session, user_email=None, scope="qc_agent") >= MAX_ACTIVE_QC_AGENT_TOKENS:
        raise HTTPException(
            status_code=409,
            detail=f"Đã có {MAX_ACTIVE_QC_AGENT_TOKENS} token QC còn hiệu lực — thu hồi bớt trước khi tạo mới.",
        )
    token, raw = await create_api_token(session, user_email=str(admin.get("sub") or "admin"), name=body.name,
                                        scope="qc_agent")
    # Token gốc chỉ trả 1 lần — frontend dựng URL MCP HTTP `/mcp/<token>` từ đây.
    return {"id": token.id, "name": token.name, "token": raw, "expires_at": token.expires_at}


@router.delete("/agent-tokens/{token_id}")
async def delete_agent_token(token_id: int, session: AsyncSession = Depends(get_db)):
    if not await revoke_api_token(session, token_id=token_id, scope="qc_agent"):
        raise HTTPException(status_code=404, detail="Không tìm thấy token QC.")
    return {"message": "Đã thu hồi token QC."}


@router.get("/report/{date}")
async def get_report_reviews(date: str, session: AsyncSession = Depends(get_db)):
    """Panel "Kết quả QC" cạnh báo cáo: biên bản mới nhất (đủ chi tiết) + tóm tắt các lần QC trước + cổng tự xuất
    bản. Giao diện chỉ mở panel khi báo cáo chưa xuất bản."""
    report = (await session.execute(select(Report.status, Report.content).where(Report.report_date == date))).first()
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    current = content_hash(report.content) if report.content else None
    reviews = list((await session.execute(
        select(ReportQcReview).where(ReportQcReview.report_date == date).order_by(ReportQcReview.run_no.desc())
    )).scalars())
    latest = None
    if reviews:
        findings = (await load_findings(session, [reviews[0].id]))[reviews[0].id]
        latest = review_dict(reviews[0], findings, current_hash=current)
    older = await load_findings(session, [r.id for r in reviews[1:]])
    allowed, reason = await publish_gate(session, date)
    return {
        "report_date": date,
        "report_status": report.status,
        "latest": latest,
        "history": [review_dict(r, older[r.id], current_hash=current, with_body=False) for r in reviews[1:]],
        "auto_publish": {"time": settings.auto_publish_time or None, "allowed": allowed, "reason": reason},
        "criteria": QC_CRITERIA,
    }


@router.get("/{review_id}")
async def get_review(review_id: int, session: AsyncSession = Depends(get_db)):
    review = await session.get(ReportQcReview, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy biên bản QC.")
    findings = (await load_findings(session, [review.id]))[review.id]
    states = await _current_hashes(session, [review.report_date])
    status, current = states.get(review.report_date, (None, None))
    return {**review_dict(review, findings, current_hash=current), "current_report_status": status,
            "criteria_def": QC_CRITERIA}


@router.patch("/findings/{finding_id}")
async def patch_finding(finding_id: int, body: FindingStatusUpdate, session: AsyncSession = Depends(get_db)):
    if body.status not in FINDING_STATUSES:
        raise HTTPException(status_code=422, detail=f"status ∈ {FINDING_STATUSES}")
    try:
        finding = await update_finding_status(session, finding_id, body.status, body.note)
    except QcSubmissionError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if finding is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy lỗi QC.")
    return finding_dict(finding)


@router.post("/report/{date}/fix-preview")
async def fix_preview(date: str, body: QcFixPreviewRequest, session: AsyncSession = Depends(get_db)):
    """Đề xuất sửa bản nháp theo biên bản QC — chỉ trả diff từng đoạn, KHÔNG ghi gì (services/report_qc_fix.py)."""
    try:
        return await build_fix_preview(session, date, body.finding_ids or None, body.review_id)
    except QcFixError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.post("/report/{date}/fix-apply")
async def fix_apply(
    date: str, body: QcFixApplyRequest, background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db), admin: dict = Depends(get_current_admin),
):
    """Ghi các thay đổi đã chọn ở bước xem trước (điều kiện: báo cáo chưa đổi kể từ lúc xem trước) và đánh dấu lỗi
    "Đã sửa"; `undo=true` ghi ngược lại và trả lỗi về "Chưa xử lý". Dịch lại bản tiếng Anh ở nền như PUT báo cáo."""
    from services.report_translation import ensure_report_translation

    try:
        result = await apply_fix_changes(
            session, date, body.base_hash, [c.model_dump() for c in body.changes],
            finding_status="open" if body.undo else "fixed", actor=str(admin.get("sub") or "admin"), run_no=body.run_no,
        )
    except QcFixError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    background_tasks.add_task(ensure_report_translation, async_session_maker, settings, date)
    return result
