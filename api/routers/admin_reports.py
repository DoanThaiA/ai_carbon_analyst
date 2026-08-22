from datetime import datetime
from typing import Dict, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import defer
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_admin, get_db
from db.models import Report
from services.report_generator import generate_report_content

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
        "created_at": report.created_at,
        "published_at": report.published_at,
    }


@router.post("/generate")
async def generate_report(date: str, session: AsyncSession = Depends(get_db)):
    """Trigger AI tạo/cập nhật bản draft cho ngày cụ thể."""
    try:
        real_content = await generate_report_content(session, date)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi khi sinh báo cáo: {str(e)}")

    stmt = select(Report).where(Report.report_date == date)
    result = await session.execute(stmt)
    report = result.scalars().first()

    if report:
        if report.status == "published":
            raise HTTPException(status_code=400, detail="Cannot overwrite published report")
        report.content = real_content
    else:
        report = Report(report_date=date, status="draft", content=real_content)
        session.add(report)

    await session.commit()
    return {"message": f"Draft report for {date} generated successfully."}


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

    report.status = "published"
    report.published_at = datetime.utcnow()
    await session.commit()

    return {"message": f"Report for {date} published successfully."}


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

    report.content = body.content
    await session.commit()
    return {"message": f"Draft report for {date} updated successfully."}


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
