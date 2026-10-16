"""
crawl_eex_auctions.py
=====================
Lịch + kết quả đấu giá sơ cấp EUA trên EEX → bảng `eua_auctions`. Dùng cho:
  - ước tính giá CBAM của quý đang chạy (services/cbam_estimate.py) — giá CBAM 2026 EC công
    bố = VWAP (gia quyền khối lượng) giá khớp của MỌI phiên đấu giá trong quý; đã đối chiếu
    file kết quả 2026: Q1 75,3641 / Q2 75,2766 / Q3 82,3185 ↔ EC 75,36 / 75,28 / 82,32;
  - lịch đấu giá tuần tới ở Mục 8 báo cáo.

Nguồn (công khai, httpx thường, không cần trình duyệt):
  - KẾT QUẢ: file "Primary Market Auction Report" theo năm (RESULTS_URL) — 1 dòng/phiên,
    cập nhật ngay trong ngày đấu giá (phiên 11:00 CET).
  - LỊCH: file "Auction Calendar" (.xlsx) — tên file đổi theo từng lần EEX sửa lịch (vd
    "EEX_Auction_Calendar_EUA_MSR_adjustment_2026_30.7.2026.xlsx",
    "EEX_ETS1_Auction_Calendar_2027_30.9.2026.xlsx") nên phải đọc link từ trang CALENDAR_PAGE.
    Sheet "Calendar": dòng phiên ("Tuesday | 09.00 am - 11.00 am | T3PA | 4th Period | 2791500 |
    | Spot Market - EU Primary Auction CAP3 - EUA") nằm NGAY TRƯỚC dòng chỉ chứa ngày.

Kết quả ghi đè dòng lịch cùng (ngày, nền tảng). Dòng lịch tương lai bị EEX bỏ khỏi bản lịch
mới (sửa lịch/MSR) được xoá để ước tính không tính khối lượng không còn đấu giá.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional
from urllib.parse import urljoin

import httpx
import openpyxl
from sqlalchemy import and_, delete, extract, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.config import Settings
from db import build_sessionmaker, create_engine
from db.models import EuaAuction

logger = logging.getLogger(__name__)

RESULTS_URL = (
    "https://public.eex-group.com/eex/eua-auction-report/"
    "emission-spot-primary-market-auction-report-{year}-data.xlsx"
)
CALENDAR_PAGE = "https://www.eex.com/en/markets/environmental-markets/emissions-auctions"
HTTP_TIMEOUT = 60.0
TZ_VN = timezone(timedelta(hours=7))
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_CALENDAR_LINK_RE = re.compile(r'href="([^"]*/Auction_Calendar/[^"]+\.xlsx)"', re.IGNORECASE)


def normalize_platform(name: str) -> Optional[str]:
    """Tên phiên ở file kết quả ("Auction 4. Period CAP3 EU") và file lịch ("Spot Market - EU
    Primary Auction CAP3 - EUA") → mã nền tảng chung EU/DE/PL/NIR. None = không phải phiên EUA
    (vd EUAA hàng không, ETS2)."""
    s = (name or "").strip()
    if not s or re.search(r"EUAA|aviation|ETS2|ETS 2", s, re.IGNORECASE):
        return None
    if re.search(r"German|\bDE\b", s):
        return "DE"
    if re.search(r"Polish|\bPL\b", s):
        return "PL"
    if re.search(r"Northern Irish|\bNIR\b|\bXI\b", s):
        return "NIR"
    if re.search(r"\bEU\b", s):
        return "EU"
    return None


def _num(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _as_date(v) -> Optional[date]:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return None


def parse_results_rows(rows: Iterable[tuple]) -> list[dict]:
    """Sheet "Primary Market Auction": tìm dòng tiêu đề (có ô "Date" và "Auction Name") rồi map cột
    theo tên — không phụ thuộc vị trí cột."""
    out: list[dict] = []
    col: dict[str, int] = {}
    for row in rows:
        cells = list(row)
        if not col:
            names = [str(c).strip() if c is not None else "" for c in cells]
            if "Date" in names and "Auction Name" in names:
                col = {n: i for i, n in enumerate(names) if n}
            continue

        def get(prefix: str):
            idx = next((i for n, i in col.items() if n.startswith(prefix)), None)
            return cells[idx] if idx is not None and idx < len(cells) else None

        d = _as_date(get("Date"))
        platform = normalize_platform(str(get("Auction Name") or ""))
        volume = _num(get("Auction Volume"))
        if d is None or platform is None or volume is None:
            continue
        status = str(get("Status") or "").strip().lower()
        price = _num(get("Auction Price"))
        out.append({
            "auction_date": d,
            "auction_name": platform,
            "contract": (str(get("Contract") or "").strip() or None),
            "status": "successful" if status == "successful" and price else "cancelled",
            "volume": volume,
            "auction_price": price if status == "successful" else None,
            "cover_ratio": _num(get("Cover Ratio")),
            "total_bidders": int(_num(get("Total Number of Bidders")) or 0) or None,
        })
    if not col:
        raise ValueError("File kết quả đấu giá EEX: không thấy dòng tiêu đề (Date/Auction Name)")
    return out


def parse_calendar_rows(rows: Iterable[tuple]) -> list[dict]:
    """Sheet "Calendar": gom các dòng phiên (cột 0 = thứ, cột 4 = khối lượng, cột 6 = tên phiên)
    cho tới dòng ngày kế tiếp (cột 0 là datetime) rồi gán ngày đó. Thứ ở dòng phiên phải khớp
    ngày — lệch thì bỏ (bố cục file đổi) thay vì gán nhầm ngày."""
    out: list[dict] = []
    pending: list[tuple[str, dict]] = []
    for row in rows:
        cells = list(row) + [None] * 7
        first = cells[0]
        d = _as_date(first)
        if d is not None:
            for weekday, item in pending:
                if weekday != _WEEKDAYS[d.weekday()]:
                    logger.warning("Lịch EEX: dòng %s gán cho %s (%s) — lệch thứ, bỏ qua", weekday, d, item["auction_name"])
                    continue
                out.append({**item, "auction_date": d})
            pending = []
            continue
        if isinstance(first, str) and first.strip() in _WEEKDAYS:
            volume = _num(cells[4])
            name = str(cells[6] or "")
            platform = normalize_platform(name) if "Primary Auction" in name else None
            if volume and platform:
                pending.append((first.strip(), {
                    "auction_name": platform,
                    "contract": (str(cells[2] or "").strip() or None),
                    "status": "scheduled",
                    "volume": volume,
                }))
    return out


def calendar_links(html: str, base: str = CALENDAR_PAGE) -> list[str]:
    """Link file lịch đấu giá EUA (.xlsx) đang hiệu lực trên trang EEX — bỏ bản lưu trữ (History)
    và lịch ETS2/EUAA."""
    links = []
    for href in _CALENDAR_LINK_RE.findall(html):
        name = href.rsplit("/", 1)[-1]
        if re.search(r"History|ETS2|EUAA", name, re.IGNORECASE):
            continue
        url = urljoin(base, href)
        if url not in links:
            links.append(url)
    return links


def _sheet_rows(content: bytes, sheet: Optional[str] = None) -> list[tuple]:
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb[sheet] if sheet else wb.worksheets[0]
    return list(ws.iter_rows(values_only=True))


async def fetch_results(client: httpx.AsyncClient, year: int) -> list[dict]:
    resp = await client.get(RESULTS_URL.format(year=year))
    if resp.status_code == 404:
        return []  # đầu năm, EEX chưa tạo file năm mới
    resp.raise_for_status()
    return parse_results_rows(_sheet_rows(resp.content))


async def fetch_calendar(client: httpx.AsyncClient) -> list[dict]:
    page = await client.get(CALENDAR_PAGE)
    page.raise_for_status()
    links = calendar_links(page.text)
    if not links:
        raise ValueError("Không tìm thấy link file lịch đấu giá EUA (.xlsx) trên trang EEX")
    out: list[dict] = []
    for url in links:
        resp = await client.get(url)
        resp.raise_for_status()
        rows = parse_calendar_rows(_sheet_rows(resp.content, "Calendar"))
        logger.info("Lịch EEX %s: %d phiên", url.rsplit("/", 1)[-1], len(rows))
        out.extend(rows)
    return out


async def _upsert(session_factory, results: list[dict], calendar: list[dict], today: date) -> None:
    async with session_factory() as session:
        async with session.begin():
            for r in calendar:
                stmt = pg_insert(EuaAuction).values(**r)
                # Chỉ sửa khối lượng của dòng còn là lịch — không đè dòng đã có kết quả.
                stmt = stmt.on_conflict_do_update(
                    constraint="uq_eua_auctions_date_name",
                    set_={"volume": stmt.excluded.volume, "contract": stmt.excluded.contract},
                    where=EuaAuction.status == "scheduled",
                )
                await session.execute(stmt)
            for r in results:
                stmt = pg_insert(EuaAuction).values(**r)
                stmt = stmt.on_conflict_do_update(
                    constraint="uq_eua_auctions_date_name",
                    set_={k: getattr(stmt.excluded, k) for k in
                          ("contract", "status", "volume", "auction_price", "cover_ratio", "total_bidders")},
                )
                await session.execute(stmt)

            # Phiên tương lai không còn trong bản lịch mới → xoá (chỉ trong các năm vừa đọc được lịch).
            years = {r["auction_date"].year for r in calendar}
            if years:
                keep = [(r["auction_date"], r["auction_name"]) for r in calendar]
                await session.execute(
                    delete(EuaAuction).where(and_(
                        EuaAuction.status == "scheduled",
                        EuaAuction.auction_date > today,
                        extract("year", EuaAuction.auction_date).in_(years),
                        tuple_(EuaAuction.auction_date, EuaAuction.auction_name).not_in(keep),
                    ))
                )


async def crawl_eex_auctions(session_factory=None) -> dict:
    """Crawl kết quả (năm nay + năm trước khi đang tháng 1) và lịch hiện hành. Lỗi 1 nguồn không
    chặn nguồn còn lại."""
    stats: dict = {"results": 0, "scheduled": 0, "errors": []}
    engine = None
    if session_factory is None:
        engine = create_engine(Settings.from_env().database_url)
        session_factory = build_sessionmaker(engine)
    today = datetime.now(TZ_VN).date()
    results: list[dict] = []
    calendar: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers={"User-Agent": _UA}, follow_redirects=True) as client:
            for year in ([today.year - 1] if today.month == 1 else []) + [today.year]:
                try:
                    results.extend(await fetch_results(client, year))
                except Exception as exc:
                    logger.exception("Lỗi tải kết quả đấu giá EEX %s", year)
                    stats["errors"].append(f"results_{year}: {exc}")
            try:
                calendar = await fetch_calendar(client)
            except Exception as exc:
                logger.exception("Lỗi tải lịch đấu giá EEX")
                stats["errors"].append(f"calendar: {exc}")
        await _upsert(session_factory, results, calendar, today)
        stats["results"], stats["scheduled"] = len(results), len(calendar)
        latest = max((r for r in results if r["status"] == "successful"), key=lambda r: r["auction_date"], default=None)
        if latest:
            logger.info(
                "Đấu giá EEX: %d phiên kết quả (mới nhất %s %s: %.2f EUR/t), %d phiên lịch",
                len(results), latest["auction_date"], latest["auction_name"], latest["auction_price"], len(calendar),
            )
    finally:
        if engine is not None:
            await engine.dispose()
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    print(asyncio.run(crawl_eex_auctions()))
