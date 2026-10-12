"""Render báo cáo ngày thành PDF — mở đúng trang user xem (`/reports/<ngày>` của frontend)
bằng Chromium (Playwright) rồi in, nên PDF giống hệt nút "Tải PDF" trên web (cùng CSS print).

Trang yêu cầu đăng nhập nên backend tự phát hành 1 access token role "user" sống ngắn
(`jwt_expire_minutes`) và đặt vào cookie `access_token` của context — token chỉ tồn tại
trong context Chromium tạm này, không gửi đi đâu khác.
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse

from playwright.async_api import async_playwright

from core.config import Settings
from core.security import create_access_token

logger = logging.getLogger(__name__)

PDF_PAGE_TIMEOUT_MS = 60_000
# Phần tử chỉ có khi báo cáo đã render xong (ReportDocument) — chờ nó thay vì sleep cố định.
REPORT_READY_SELECTOR = ".report-heading"


class ReportPdfError(RuntimeError):
    pass


async def render_report_pdf(report_date: str, settings: Settings) -> bytes:
    if not settings.app_base_url:
        raise ReportPdfError("APP_BASE_URL chưa được cấu hình — không có địa chỉ frontend để render PDF.")

    host = urlparse(settings.app_base_url).hostname or ""
    token = create_access_token("report-pdf-renderer", "user", settings)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context(viewport={"width": 1280, "height": 1600})
            await context.add_cookies([{
                "name": "access_token",
                "value": token,
                "domain": host,
                "path": "/",
                "httpOnly": True,
                "secure": settings.app_base_url.startswith("https://"),
            }])
            page = await context.new_page()
            try:
                await page.goto(
                    f"{settings.app_base_url}/reports/{report_date}",
                    wait_until="networkidle",
                    timeout=PDF_PAGE_TIMEOUT_MS,
                )
                await page.wait_for_selector(REPORT_READY_SELECTOR, timeout=PDF_PAGE_TIMEOUT_MS)
            except Exception as exc:  # noqa: BLE001 — gói lại thành lỗi có ngữ cảnh
                raise ReportPdfError(f"Không mở được trang báo cáo {report_date}: {exc}") from exc
            await page.emulate_media(media="print")
            return await page.pdf(format="A4", print_background=True, prefer_css_page_size=True)
        finally:
            await browser.close()
