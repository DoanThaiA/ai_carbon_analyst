"""Gửi PDF báo cáo ngày tới mọi user is_active SAU KHI admin duyệt (publish).

Chạy NỀN sau response của POST /api/admin/reports/{date}/publish — render PDF bằng
Chromium mất vài giây, không được chặn request duyệt. Trạng thái "đã gửi" nằm ở DB
(`reports.pdf_emailed_at`): duyệt lại sau khi unpublish không gửi trùng; `force=True`
(admin bấm gửi lại) bỏ qua cờ này. KHÔNG raise — lỗi chỉ log, báo cáo vẫn giữ trạng thái
published (admin gửi lại bằng POST /{date}/send-email).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import defer

from core.config import Settings
from db.models import Report, User
from services.email_sender import EmailSendError, email_configured, send_report_pdf_email
from services.report_pdf import ReportPdfError, render_report_pdf

logger = logging.getLogger(__name__)


@dataclass
class ReportEmailResult:
    sent: List[str]
    recipients_total: int = 0
    skipped_reason: str = ""


async def send_report_pdf_to_users(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    report_date: str,
    *,
    force: bool = False,
) -> ReportEmailResult:
    result = ReportEmailResult(sent=[])
    if not settings.report_pdf_email_enabled:
        result.skipped_reason = "disabled"
        return result
    if not email_configured(settings):
        logger.warning("[REPORT-EMAIL] Resend chưa cấu hình — bỏ qua gửi PDF báo cáo %s.", report_date)
        result.skipped_reason = "email_not_configured"
        return result

    try:
        async with session_factory() as session:
            report = (
                await session.execute(
                    select(Report).options(defer(Report.content)).where(Report.report_date == report_date)
                )
            ).scalars().first()
            if report is None or report.status != "published":
                result.skipped_reason = "not_published"
                return result
            if report.pdf_emailed_at is not None and not force:
                result.skipped_reason = "already_sent"
                return result
            recipients = (
                await session.execute(
                    select(User.email).where(User.is_active.is_(True)).order_by(User.id)
                )
            ).scalars().all()
        result.recipients_total = len(recipients)
        if not recipients:
            logger.warning("[REPORT-EMAIL] Không có user is_active nào — bỏ qua.")
            result.skipped_reason = "no_recipients"
            return result

        pdf_bytes = await render_report_pdf(report_date, settings)
        result.sent = await send_report_pdf_email(report_date, pdf_bytes, recipients, settings)

        async with session_factory() as session:
            await session.execute(
                update(Report).where(Report.report_date == report_date).values(pdf_emailed_at=func.now())
            )
            await session.commit()
    except (ReportPdfError, EmailSendError):
        logger.exception("[REPORT-EMAIL] Gửi PDF báo cáo %s thất bại.", report_date)
        result.skipped_reason = "send_failed"
        return result
    except Exception:  # noqa: BLE001 — không được làm hỏng luồng duyệt
        logger.exception("[REPORT-EMAIL] Lỗi không mong đợi khi gửi PDF báo cáo %s.", report_date)
        result.skipped_reason = "unexpected_error"
        return result

    logger.info(
        "[REPORT-EMAIL] Đã gửi PDF báo cáo %s tới %d/%d người nhận.",
        report_date, len(result.sent), result.recipients_total,
    )
    return result
