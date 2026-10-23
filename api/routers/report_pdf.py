"""Xuất PDF báo cáo theo bố cục người dùng chọn (popup "Xuất PDF" — `frontend/src/components/PdfExportModal.tsx`).

- GET /api/reports/{date}/pdf        — user/admin, CHỈ báo cáo published (như GET /api/reports/{date}).
- GET /api/admin/reports/{date}/pdf  — admin, mọi trạng thái có nội dung (xuất được bản nháp để duyệt).

Query: size (a4|square|wide), orientation (portrait|landscape), device (mobile|pc|tv), lang (vi|en). Dàn trang + xuất
bằng Chromium phía server (`services/report_pdf.py`), không dùng hộp thoại in của trình duyệt.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_admin, get_current_user, get_db, settings
from db.models import Report
from services.report_pdf import PdfLayout, ReportPdfError, render_report_pdf

logger = logging.getLogger(__name__)

router = APIRouter(tags=["report-pdf"])


async def _export(session: AsyncSession, date: str, statuses: tuple, size: str, orientation: str, device: str,
                  lang: str, as_admin: bool) -> Response:
    try:
        layout = PdfLayout(size=size, orientation=orientation, device=device)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if lang not in ("vi", "en"):
        raise HTTPException(status_code=422, detail="lang ∈ ('vi', 'en')")
    found = (await session.execute(
        select(Report.id).where(Report.report_date == date, Report.status.in_(statuses))
    )).first()
    if not found:
        raise HTTPException(status_code=404, detail="Report not found")
    try:
        pdf = await render_report_pdf(date, settings, lang=lang, layout=layout, as_admin=as_admin)
    except ReportPdfError as e:
        logger.exception("Xuất PDF báo cáo %s (%s) lỗi", date, layout)
        raise HTTPException(status_code=503, detail=str(e)) from e
    suffix = "" if lang == "vi" else "-en"
    filename = f"bao-cao-carbon-{date}-{layout.filename_suffix()}{suffix}.pdf"
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/api/reports/{date}/pdf")
async def export_report_pdf(
    date: str,
    size: str = Query("a4"), orientation: str = Query("portrait"), device: str = Query("pc"), lang: str = Query("vi"),
    session: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    return await _export(session, date, ("published",), size, orientation, device, lang, as_admin=False)


@router.get("/api/admin/reports/{date}/pdf")
async def export_report_pdf_admin(
    date: str,
    size: str = Query("a4"), orientation: str = Query("portrait"), device: str = Query("pc"), lang: str = Query("vi"),
    session: AsyncSession = Depends(get_db),
    _admin: dict = Depends(get_current_admin),
):
    return await _export(session, date, ("draft", "published"), size, orientation, device, lang, as_admin=True)
