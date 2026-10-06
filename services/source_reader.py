"""Đọc 1 trang web do NGƯỜI DÙNG cung cấp (nguồn tin/nguồn giá) cho tool
`fetch_user_source` của Quote Chat (services/quote_chat.py).

Chiến lược 2 tầng (giống crawler của dự án — xem crawl_news/fetcher.py):
  1. curl_cffi giả lập TLS fingerprint Chrome — nhanh, qua được phần lớn site chặn bot cơ bản
     (httpx thường bị Reuters/... trả 401/403).
  2. Fallback Playwright (Chromium) khi tầng 1 bị chặn (401/403/429/503), gặp trang chắn bot
     ("JavaScript is disabled"/Cloudflare...) hoặc trang gần như rỗng (render bằng JS).

SSRF: URL do người dùng đưa nên mọi request (kể cả redirect và sub-request của trình duyệt) phải
tới IP công cộng — xem `validate_public_url`.
"""
import asyncio
import ipaddress
import logging
import re
import socket
from typing import Optional
from urllib.parse import urljoin, urlsplit

import trafilatura
from curl_cffi import requests as curl_requests
from selectolax.lexbor import LexborHTMLParser as HTMLParser

logger = logging.getLogger(__name__)

MAX_SOURCE_CHARS = 12000
FETCH_TIMEOUT = 20.0
MAX_BYTES = 2_000_000
MAX_REDIRECTS = 3
MAX_TABLE_ROWS = 80
PLAYWRIGHT_TOTAL_TIMEOUT = 50.0
PLAYWRIGHT_SETTLE_SECONDS = 4.0
MIN_USEFUL_CHARS = 200  # ngắn hơn mức này coi như chưa lấy được nội dung thật
_TEXT_TYPES = ("text/plain", "text/csv", "application/json", "application/xml", "text/xml")
_BLOCK_STATUSES = (401, 403, 429, 503)
_BOT_WALL_RE = re.compile(
    r"javascript is disabled|enable javascript|not a robot|verify you are (a )?human|just a moment|"
    r"attention required|access denied|captcha|unusual traffic|are you a robot",
    re.I,
)
_playwright_slots = asyncio.Semaphore(2)  # tránh mở quá nhiều Chromium cùng lúc


async def validate_public_url(url: str) -> Optional[str]:
    """None nếu URL an toàn để server tự tải, ngược lại trả lý do từ chối. Chống SSRF: chỉ
    http(s), không user:pass@, và MỌI IP mà hostname phân giải ra đều phải là IP công cộng."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return "URL không hợp lệ."
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return "Chỉ hỗ trợ đường dẫn http/https hợp lệ."
    if parts.username or parts.password:
        return "URL chứa thông tin đăng nhập — không hỗ trợ."
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            parts.hostname, port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM
        )
    except OSError:
        return "Không phân giải được tên miền này."
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            return "Địa chỉ này thuộc mạng nội bộ — không được phép truy cập."
    return None


class _Blocked(Exception):
    """Tầng 1 bị chặn bot (status/nội dung) — nên thử Playwright."""


async def _fetch_curl(url: str) -> tuple[str, str, str]:
    """Trả (nội dung text/html, content-type, url cuối). Raise _Blocked nếu bị chặn,
    ValueError(thông báo) nếu lỗi không đáng thử lại."""
    current = url
    async with curl_requests.AsyncSession(impersonate="chrome110", timeout=FETCH_TIMEOUT) as client:
        for _ in range(MAX_REDIRECTS + 1):
            reason = await validate_public_url(current)
            if reason:
                raise ValueError(f"Không truy cập được nguồn này: {reason}")
            resp = await client.get(current, allow_redirects=False)
            loc = resp.headers.get("location")
            if 300 <= resp.status_code < 400 and loc:
                current = urljoin(current, loc)
                continue
            if resp.status_code in _BLOCK_STATUSES:
                raise _Blocked(f"HTTP {resp.status_code}")
            if resp.status_code >= 400:
                raise ValueError(f"Nguồn trả về lỗi HTTP {resp.status_code} (trang không tồn tại hoặc đã bị xoá).")
            ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            if ctype not in ("text/html", "application/xhtml+xml") and ctype not in _TEXT_TYPES:
                raise ValueError(f"Loại nội dung '{ctype or 'không rõ'}' không đọc được (chỉ hỗ trợ trang web/văn bản).")
            return resp.content[:MAX_BYTES].decode(resp.encoding or "utf-8", errors="replace"), ctype, current
    raise ValueError("Nguồn chuyển hướng quá nhiều lần.")


async def _fetch_playwright(url: str) -> tuple[str, str]:
    """Render bằng Chromium. Mọi sub-request của trình duyệt đều bị kiểm tra IP công cộng."""
    from playwright.async_api import async_playwright

    host_ok: dict[str, bool] = {}

    async def guard(route):
        req = route.request
        if req.resource_type in ("image", "media", "font", "stylesheet"):
            return await route.abort()
        if urlsplit(req.url).scheme not in ("http", "https"):
            return await route.continue_()  # data:/blob: — không ra mạng
        host = urlsplit(req.url).hostname or ""
        if host not in host_ok:
            host_ok[host] = await validate_public_url(req.url) is None
        await (route.continue_() if host_ok[host] else route.abort())

    async with _playwright_slots, async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"]
        )
        try:
            ctx = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            )
            await ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            page = await ctx.new_page()
            await page.route("**/*", guard)
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(PLAYWRIGHT_SETTLE_SECONDS)
            return await page.content(), page.url
        finally:
            await browser.close()


def _tables_text(html: str) -> str:
    """Bảng dữ liệu của trang dạng 'ô | ô | ô' — trafilatura thường bỏ bảng, mà trang giá/số liệu
    (vd bảng giá chứng chỉ CBAM của EC) lại nằm chính trong bảng."""
    lines: list[str] = []
    for table in HTMLParser(html).css("table"):
        for row in table.css("tr"):
            cells = [c.text(strip=True).replace("\xa0", " ") for c in row.css("th, td")]
            if any(cells):
                lines.append(" | ".join(cells))
            if len(lines) >= MAX_TABLE_ROWS:
                return "\n".join(lines)
    return "\n".join(lines)


def _extract_text(content: str, ctype: str, url: str) -> str:
    if ctype in _TEXT_TYPES:
        return content.strip()
    text = (trafilatura.extract(content, url=url, favor_recall=True, include_tables=True) or "").strip()
    tables = _tables_text(content)
    if tables and tables.splitlines()[0] not in text:
        text += "\n\nBảng dữ liệu trên trang:\n" + tables
    return text.strip()


def _looks_blocked(text: str) -> bool:
    return len(text) < MIN_USEFUL_CHARS or bool(_BOT_WALL_RE.search(text[:800]))


def _unreadable_message(why: str) -> str:
    return (
        f"Không đọc được nội dung nguồn này ({why}). KHÔNG đoán nội dung; nói rõ chưa đọc được và nhờ "
        "người dùng dán trực tiếp nội dung/số liệu cần đối chiếu."
    )


async def read_source(url: str) -> str:
    """Đọc nguồn → text kèm nhãn + lưu ý cho model; luôn trả chuỗi (lỗi cũng là chuỗi hướng dẫn)."""
    from services.quote_chat import _truncate  # tránh import vòng ở top-level

    url = (url or "").strip()
    if not url:
        return "Cần truyền url."
    reason = await validate_public_url(url)
    if reason:
        return f"Không truy cập được nguồn này: {reason}"

    text, final_url, used = "", url, "curl"
    try:
        content, ctype, final_url = await _fetch_curl(url)
        text = _extract_text(content, ctype, final_url)
        if _looks_blocked(text):
            raise _Blocked("nội dung rỗng/trang chắn bot")
    except _Blocked as e:
        logger.info("[SOURCE-READER] curl bị chặn (%s), thử Playwright: %s", e, url)
        used = "playwright"
        try:
            html, final_url = await asyncio.wait_for(_fetch_playwright(url), PLAYWRIGHT_TOTAL_TIMEOUT)
            if await validate_public_url(final_url) is not None:
                return "Không truy cập được nguồn này: trang chuyển hướng tới địa chỉ nội bộ."
            text = _extract_text(html, "text/html", final_url)
        except Exception as pe:  # Playwright thiếu browser/timeout/lỗi mạng
            logger.warning("[SOURCE-READER] Playwright lỗi %s: %s", url, pe)
            return _unreadable_message(f"trang chặn bot ({e}) và trình duyệt cũng không tải được")
        if _looks_blocked(text):
            return _unreadable_message("trang chặn bot/cần đăng nhập/không có nội dung văn bản")
    except ValueError as e:
        return str(e)
    except Exception as e:  # lỗi mạng/timeout của curl_cffi
        logger.warning("[SOURCE-READER] curl lỗi %s: %s", url, e)
        return _unreadable_message("không kết nối được hoặc hết thời gian chờ")

    logger.info("[SOURCE-READER] OK (%s, %d ký tự): %s", used, len(text), final_url)
    return _truncate(
        f"Nguồn do người dùng cung cấp: {final_url}\n\n{text}"
        "\n\nLƯU Ý: đây là nội dung từ nguồn NGƯỜI DÙNG cung cấp (chưa được hệ thống kiểm chứng) — chỉ nêu thông tin có "
        "trong trang này, nói rõ đây là thông tin từ nguồn của người dùng, trích dẫn tên miền; nếu khác dữ liệu hệ thống thì "
        "nêu rõ điểm khác thay vì tự chọn bên nào đúng. KHÔNG bịa thêm số liệu ngoài nội dung trang.",
        MAX_SOURCE_CHARS,
    )
