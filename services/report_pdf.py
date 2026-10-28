"""Render báo cáo ngày thành PDF theo bố cục người dùng chọn — mở trang dàn trang riêng của frontend
(`/reports/<ngày>/export`, không phải hộp thoại in của trình duyệt) bằng Chromium (Playwright) rồi xuất PDF.

Bố cục (`PdfLayout`): khổ (A4 / vuông / màn hình 16:9) × hướng (dọc / ngang) × thiết bị xem (máy tính /
TV). Backend là nơi DUY NHẤT tính kích thước trang, lề và tỉ lệ chữ rồi truyền qua query cho trang export — trang
export chỉ áp dụng (đặt @page, bề rộng khung báo cáo, zoom), nên đổi preset chỉ cần sửa ở đây.

Trang yêu cầu đăng nhập nên backend tự phát hành 1 access token sống ngắn (`jwt_expire_minutes`) — role "user" (chỉ
đọc được báo cáo published) hoặc "admin" khi admin xuất bản nháp — và đặt vào cookie `access_token` của context;
token chỉ tồn tại trong context Chromium tạm này, không gửi đi đâu khác.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, Tuple
from urllib.parse import urlencode, urlparse

from playwright.async_api import async_playwright

from core.config import Settings
from core.security import create_access_token

logger = logging.getLogger(__name__)

PDF_PAGE_TIMEOUT_MS = 60_000
# Trang export gắn data-pdf-ready khi báo cáo đã render + dàn trang xong (data-pdf-error nếu không tải được báo cáo)
# — chờ nó thay vì sleep cố định.
REPORT_READY_SELECTOR = "[data-pdf-ready], [data-pdf-error]"
# Chromium tốn RAM — giới hạn số PDF render cùng lúc (người dùng bấm xuất + email publish).
_RENDER_SLOTS = asyncio.Semaphore(2)

MM_TO_PX = 96 / 25.4

# Khổ trang (mm, theo hướng DỌC) + lề (trên/dưới, trái/phải). Vuông không có hướng.
PAGE_SIZES: Dict[str, Tuple[float, float]] = {"a4": (210, 297), "square": (210, 210), "wide": (167, 297)}
PAGE_MARGINS: Dict[str, Tuple[float, float]] = {"a4": (12, 10), "square": (10, 10), "wide": (9, 9)}
ORIENTATIONS = ("portrait", "landscape")
# Tỉ lệ chữ/khối theo thiết bị xem (bản xuất cho điện thoại đã bỏ — 26/10/2026). Bề rộng bố cục = bề rộng vùng in / tỉ
# lệ (container query dàn lại số cột theo bề rộng này). Máy tính 0,82 (26/10/2026): A4 dọc rộng 718px → bố cục ~875px ≥
# @3xl/report (768px) nên các khối dàn 2 cột như màn hình thay vì 1 cột, chữ thân ≈ 12,3px (~9pt) — báo cáo từ 19 còn
# ~12–13 trang. TV trình chiếu cả trang 16:9 phóng to ~1,8× nên giữ tỉ lệ 1 — trang ngang đã đủ rộng cho nhiều cột.
DEVICE_SCALES: Dict[str, float] = {"pc": 0.82, "tv": 1.0}
# Khổ NGANG (A4/16:9) bản máy tính (28/10/2026): 0,75 — bố cục ~1400px (A4 ngang) ≥ 1024px nên trang export bật
# `.pdf-wide` (dàn cột như màn hình: tổng quan 3 cột, bảng giá | biểu đồ chỉ số, Phân tích 2 cột...), chữ thân ≈ 11,3px
# (~8,4pt). Báo cáo 28/10: A4 ngang 15 → 14 trang, 16:9 ngang 19 → 17 trang. TV giữ tỉ lệ thường (trình chiếu).
LANDSCAPE_DEVICE_SCALES: Dict[str, float] = {"pc": 0.75}


@dataclass(frozen=True)
class PdfLayout:
    size: str = "a4"
    orientation: str = "portrait"
    device: str = "pc"

    def __post_init__(self):
        if self.size not in PAGE_SIZES:
            raise ValueError(f"size ∈ {tuple(PAGE_SIZES)}")
        if self.orientation not in ORIENTATIONS:
            raise ValueError(f"orientation ∈ {ORIENTATIONS}")
        if self.device not in DEVICE_SCALES:
            raise ValueError(f"device ∈ {tuple(DEVICE_SCALES)}")

    def page_mm(self) -> Tuple[float, float]:
        w, h = PAGE_SIZES[self.size]
        return (h, w) if self.orientation == "landscape" and self.size != "square" else (w, h)

    def margins_mm(self) -> Tuple[float, float]:
        return PAGE_MARGINS[self.size]

    def scale(self) -> float:
        if self.orientation == "landscape" and self.size != "square":
            return LANDSCAPE_DEVICE_SCALES.get(self.device, DEVICE_SCALES[self.device])
        return DEVICE_SCALES[self.device]

    def content_px(self) -> Tuple[int, int]:
        """Vùng in (trừ lề) tính bằng CSS px."""
        (w, h), (mv, mh) = self.page_mm(), self.margins_mm()
        return round((w - 2 * mh) * MM_TO_PX), round((h - 2 * mv) * MM_TO_PX)

    def query(self) -> Dict[str, str]:
        (w, h), (mv, mh) = self.page_mm(), self.margins_mm()
        return {"w": f"{w:g}", "h": f"{h:g}", "mv": f"{mv:g}", "mh": f"{mh:g}",
                "zoom": f"{self.scale():g}"}

    def filename_suffix(self) -> str:
        orient = "" if self.size == "square" else f"-{self.orientation}"
        return f"{self.size}{orient}-{self.device}"


class ReportPdfError(RuntimeError):
    pass


# Cookie ngôn ngữ của next-intl (frontend/src/i18n/config.ts::LOCALE_COOKIE) — "en" để in bản tiếng Anh.
LOCALE_COOKIE = "NEXT_LOCALE"


async def render_report_pdf(report_date: str, settings: Settings, lang: str = "vi",
                            layout: PdfLayout = PdfLayout(), as_admin: bool = False) -> bytes:
    """`as_admin=True`: trang export đọc báo cáo qua API admin (xuất được cả bản nháp)."""
    if not settings.app_base_url:
        raise ReportPdfError("APP_BASE_URL chưa được cấu hình — không có địa chỉ frontend để render PDF.")

    host = urlparse(settings.app_base_url).hostname or ""
    token = create_access_token("report-pdf-renderer", "admin" if as_admin else "user", settings)
    params = {**layout.query(), **({"src": "admin"} if as_admin else {})}
    url = f"{settings.app_base_url}/reports/{report_date}/export?{urlencode(params)}"
    content_w, content_h = layout.content_px()

    async with _RENDER_SLOTS, async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            # Viewport = đúng bề rộng vùng in: bố cục màn hình (ResizeObserver của biểu đồ, container query) trùng
            # bản in, không phải dàn lại khi chuyển sang media print.
            context = await browser.new_context(viewport={"width": content_w, "height": content_h})
            secure = settings.app_base_url.startswith("https://")
            await context.add_cookies([
                {"name": "access_token", "value": token, "domain": host, "path": "/",
                 "httpOnly": True, "secure": secure},
                {"name": LOCALE_COOKIE, "value": lang, "domain": host, "path": "/", "secure": secure},
            ])
            page = await context.new_page()
            try:
                # "load" thay vì "networkidle": Header giữ 1 kết nối SSE tin nóng mở suốt nên mạng không bao giờ "idle".
                await page.goto(url, wait_until="load", timeout=PDF_PAGE_TIMEOUT_MS)
                await page.emulate_media(media="print")
                # Trang export tự chờ font + dàn trang (theo media print) rồi mới gắn data-pdf-ready.
                marker = await page.wait_for_selector(REPORT_READY_SELECTOR, state="attached",
                                                      timeout=PDF_PAGE_TIMEOUT_MS)
                failed = await marker.get_attribute("data-pdf-error")
            except Exception as exc:  # noqa: BLE001 — gói lại thành lỗi có ngữ cảnh
                raise ReportPdfError(f"Không mở được trang báo cáo {report_date}: {exc}") from exc
            if failed is not None:
                raise ReportPdfError(f"Trang export không tải được báo cáo {report_date} (lỗi {failed}).")
            return await page.pdf(print_background=True, prefer_css_page_size=True)
        finally:
            await browser.close()
