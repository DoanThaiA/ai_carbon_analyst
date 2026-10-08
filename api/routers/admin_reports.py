import logging
from datetime import datetime
from typing import Dict, Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import defer
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import async_session_maker, get_current_admin, get_db
from db.models import Report
from services import biz_memory
from services.report_generator import generate_report_content

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/reports",
    tags=["admin-reports"],
    dependencies=[Depends(get_current_admin)],
)


class ReportUpdate(BaseModel):
    content: Dict[str, Any]


@router.get("")
async def list_all_reports(session: AsyncSession = Depends(get_db)):
    """Danh sách toàn bộ báo cáo (cả draft lẫn published) — dùng cho hàng đợi duyệt."""
    stmt = select(Report).options(defer(Report.content)).order_by(Report.report_date.desc())
    result = await session.execute(stmt)
    reports = result.scalars().all()

    return [
        {
            "id": r.id,
            "report_date": r.report_date,
            "status": r.status,
            "error_message": r.error_message,
            "created_at": r.created_at,
            "published_at": r.published_at,
        }
        for r in reports
    ]


@router.get("/{date}")
async def get_report_for_review(date: str, session: AsyncSession = Depends(get_db)):
    stmt = select(Report).where(Report.report_date == date)
    result = await session.execute(stmt)
    report = result.scalars().first()

    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    return {
        "id": report.id,
        "report_date": report.report_date,
        "status": report.status,
        "content": report.content,
        "error_message": report.error_message,
        "created_at": report.created_at,
        "published_at": report.published_at,
    }


async def _run_report_generation_job(date: str) -> None:
    """Chạy nền sau khi response /generate đã trả về — mở session RIÊNG (session
    của request đã đóng ngay khi response được gửi, không dùng lại được).
    Sinh xong thì ghi content + status='draft'; lỗi thì ghi status='failed' +
    error_message thay vì để mất báo cáo ngày đó.
    """
    async with async_session_maker() as session:
        try:
            real_content = await generate_report_content(session, date)
        except Exception as e:
            logger.exception("[REPORT-JOB] Lỗi khi sinh báo cáo %s", date)
            stmt = select(Report).where(Report.report_date == date)
            result = await session.execute(stmt)
            report = result.scalars().first()
            if report and report.status == "generating":
                report.status = "failed"
                report.error_message = str(e)
                await session.commit()
            return

        stmt = select(Report).where(Report.report_date == date)
        result = await session.execute(stmt)
        report = result.scalars().first()
        if not report:
            # Bị xóa trong lúc job đang chạy — bỏ kết quả, không tạo lại.
            logger.warning("[REPORT-JOB] Report %s bị xóa trong lúc đang sinh, bỏ qua kết quả.", date)
            return

        report.content = real_content
        report.status = "draft"
        report.error_message = None
        await session.commit()
        logger.info("[REPORT-JOB] Sinh xong báo cáo %s.", date)


@router.post("/generate", status_code=202)
async def generate_report(date: str, background_tasks: BackgroundTasks, session: AsyncSession = Depends(get_db)):
    """Trigger AI tạo/cập nhật bản draft cho ngày cụ thể — chạy NỀN (không block
    request), tránh 504 timeout ở reverse proxy khi sinh report mất vài phút.
    Trả về ngay status='generating'; client tự poll GET /{date} để biết khi xong.
    """
    stmt = select(Report).where(Report.report_date == date)
    result = await session.execute(stmt)
    report = result.scalars().first()

    if report:
        if report.status == "published":
            raise HTTPException(status_code=400, detail="Cannot overwrite published report")
        if report.status == "generating":
            raise HTTPException(status_code=409, detail=f"Report for {date} đang được sinh, vui lòng đợi.")
        report.status = "generating"
        report.error_message = None
    else:
        report = Report(report_date=date, status="generating", content=None)
        session.add(report)

    await session.commit()

    background_tasks.add_task(_run_report_generation_job, date)
    return {"status": "generating", "message": f"Đang sinh báo cáo cho {date}, vui lòng đợi..."}


@router.post("/{date}/publish")
async def publish_report(date: str, session: AsyncSession = Depends(get_db)):
    """Admin duyệt: chốt bản draft thành published để hiện lên màn hình user."""
    stmt = select(Report).where(Report.report_date == date)
    result = await session.execute(stmt)
    report = result.scalars().first()

    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    if report.status == "published":
        raise HTTPException(status_code=400, detail="Report is already published")
    if report.status in ("generating", "failed"):
        raise HTTPException(status_code=400, detail="Report chưa sinh xong hoặc bị lỗi, không thể duyệt.")

    report.status = "published"
    report.published_at = datetime.utcnow()
    await session.commit()

    return {"message": f"Report for {date} published successfully."}


@router.post("/{date}/unpublish")
async def unpublish_report(
    date: str,
    session: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Admin đưa báo cáo đã xuất bản về lại draft để sửa — user không còn thấy báo cáo
    (mọi API phía user chỉ lọc status='published') cho tới khi duyệt lại. Lúc duyệt không
    có tác dụng phụ nào khác (email, thông báo...) nên chỉ cần đổi lại status."""
    stmt = select(Report).options(defer(Report.content)).where(Report.report_date == date)
    report = (await session.execute(stmt)).scalars().first()

    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if report.status != "published":
        raise HTTPException(status_code=400, detail="Chỉ đưa về draft được báo cáo đã xuất bản.")

    report.status = "draft"
    report.published_at = None
    await session.commit()
    logger.info("[REPORT] Admin %s đưa báo cáo %s về draft.", admin.get("sub"), date)
    return {"message": f"Report for {date} reverted to draft."}


@router.put("/{date}")
async def update_report(date: str, body: ReportUpdate, session: AsyncSession = Depends(get_db)):
    """Admin sửa nội dung JSON của bản draft báo cáo."""
    stmt = select(Report).where(Report.report_date == date)
    result = await session.execute(stmt)
    report = result.scalars().first()

    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    if report.status == "published":
        raise HTTPException(status_code=400, detail="Cannot edit a published report")
    if report.status == "generating":
        raise HTTPException(status_code=400, detail="Report đang được sinh, chưa thể sửa.")

    report.content = body.content
    await session.commit()
    return {"message": f"Draft report for {date} updated successfully."}


class BizSuggestionDismiss(BaseModel):
    reason: Optional[str] = Field(None, max_length=1000)


@router.post("/{date}/biz-suggestions/{suggestion_id}/dismiss")
async def dismiss_biz_suggestion(
    date: str,
    suggestion_id: int,
    body: Optional[BizSuggestionDismiss] = None,
    session: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Admin gỡ 1 gợi ý kinh doanh (ngắn hạn hoặc dài hạn) khỏi báo cáo `date` (đề xuất phi thực tế...) —
    xoá khỏi mục "Gợi ý kinh doanh" của CHÍNH báo cáo này (cả bản đã phát hành),
    và thôi theo dõi/nhắc lại gợi ý đó ở các báo cáo sau (services/biz_memory.py).
    Các báo cáo khác đã sinh trước đó giữ nguyên làm lịch sử."""
    stmt = select(Report).where(Report.report_date == date)
    report = (await session.execute(stmt)).scalars().first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if report.status == "generating" or not report.content:
        raise HTTPException(status_code=400, detail="Report đang được sinh, chưa thể sửa.")

    new_biz, found = biz_memory.remove_from_biz_content(report.content.get("biz"), suggestion_id)
    if not found:
        raise HTTPException(status_code=404, detail="Không tìm thấy gợi ý này trong báo cáo.")

    await biz_memory.dismiss(session, suggestion_id, admin.get("sub"), body.reason if body else None)
    # Gán object content MỚI — SQLAlchemy không tự nhận ra thay đổi lồng bên trong JSONB.
    report.content = {**report.content, "biz": new_biz}
    await session.commit()
    logger.info("[REPORT] Admin %s gỡ gợi ý kinh doanh #%s khỏi báo cáo %s.", admin.get("sub"), suggestion_id, date)
    return {"message": "Đã gỡ gợi ý khỏi báo cáo.", "biz": new_biz}


@router.delete("/{date}")
async def delete_report(date: str, session: AsyncSession = Depends(get_db)):
    """Admin xóa báo cáo."""
    stmt = select(Report).where(Report.report_date == date)
    result = await session.execute(stmt)
    report = result.scalars().first()

    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    await session.delete(report)
    await session.commit()
    return {"message": f"Report for {date} deleted successfully."}
