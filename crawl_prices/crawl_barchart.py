"""
crawl_barchart.py
=================
Lấy giá đóng cửa ngày hôm qua từ Barchart cho toàn bộ các hợp đồng tương lai
cần thiết cho hệ thống phân tích carbon:

  Carbon   : EUA (CK*0)
  Gas      : TTF (INXU26), Henry Hub NG (NG*0)
  Oil      : Brent (CB*0), WTI (CL*0), Gasoil (LF*0)
  Coal     : API2/ARA (ITF*1), API4/Richards Bay (LV*0)

Kỹ thuật:
  1. GET trang price-history để lấy cookie session + XSRF-TOKEN
  2. GET API nội bộ /proxies/timeseries/queryeod.ashx với cookie + header
     X-XSRF-TOKEN — Barchart cho phép user ẩn danh lấy vài phiên gần nhất.

Không cần tài khoản, không cần API key trả phí.
Thay thế hoàn toàn crawl_eia.py (WTI, Brent, Henry Hub) và mở rộng thêm
các instrument mà EIA không cung cấp.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import unquote

# Giờ Việt Nam = UTC+7
TZ_VN = timezone(timedelta(hours=7))

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from base_crawler import BaseCrawler
from core.config import Settings
from db import create_engine, build_sessionmaker
from db.models import Instrument, Price, PriceCrawlSource

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hằng số
# ---------------------------------------------------------------------------

BARCHART_BASE  = "https://www.barchart.com"
EOD_ENDPOINT   = f"{BARCHART_BASE}/proxies/timeseries/queryeod.ashx"
REQUEST_TIMEOUT = 30
MIN_VALID_PRICE = 0.01

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Spec cho từng hợp đồng
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BarchartSpec:
    """Một hợp đồng cần crawl từ Barchart."""
    symbol: str           # ký hiệu trên Barchart, vd "CKZ26"
    instrument_code: str  # mã nội bộ,           vd "EUA_DEC26"
    instrument_name: str
    category: str
    unit: str    = "EUR/tCO2"
    exchange: str = "ICE"


async def load_specs_from_db(session_factory) -> list[BarchartSpec]:
    """Đọc danh sách hợp đồng cần crawl từ bảng price_crawl_sources (admin CRUD
    qua /api/admin/price-sources) — thay cho BARCHART_SPECS hardcode trước đây.
    Chỉ lấy các nguồn đang is_active=true."""
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(PriceCrawlSource).where(PriceCrawlSource.is_active.is_(True))
            )
        ).scalars().all()

    return [
        BarchartSpec(
            symbol=row.symbol,
            instrument_code=row.instrument_code,
            instrument_name=row.instrument_name,
            category=row.category,
            unit=row.unit,
            exchange=row.exchange,
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# HTTP session
# ---------------------------------------------------------------------------

def _build_http_session() -> requests.Session:
    """Session dùng chung: connection pooling + auto-retry cho lỗi tạm thời."""
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({
        "User-Agent": _UA,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    return session


def _prime_session(http: requests.Session, symbol: str) -> str:
    """
    Load trang price-history để nhận cookie session + XSRF-TOKEN.
    Barchart dùng Laravel double-submit CSRF: token nằm trong cookie,
    phải gửi lại qua header X-XSRF-TOKEN khi gọi API.

    Trả về giá trị XSRF-TOKEN đã URL-decode.
    """
    url = f"{BARCHART_BASE}/futures/quotes/{symbol}/price-history/historical"
    resp = http.get(url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()

    token = http.cookies.get("XSRF-TOKEN", "")
    if token:
        token = unquote(token)
    return token


# ---------------------------------------------------------------------------
# Fetch + parse
# ---------------------------------------------------------------------------

def _yesterday_vn() -> date:
    """
    Trả về ngày hôm qua theo giờ Việt Nam (UTC+7).
    Khi chạy lúc 7h sáng VN, date.today() có thể khác với ngày VN thực tế
    nếu server đang dùng UTC — dùng hàm này để luôn đúng.
    """
    return datetime.now(TZ_VN).date() - timedelta(days=1)


def _fetch_eod(
    http: requests.Session,
    spec: BarchartSpec,
    xsrf_token: str,
    target_date: date,
) -> str:
    """
    Gọi queryeod.ashx, trả về raw CSV text.
    Lấy 21 ngày trước target_date để có đủ:
      - íe nhất 1 phiên cho Δ ngày
      - íe nhất 1 phiên cách 7+ ngày lịch cho Δ tuần
    """
    start = (target_date - timedelta(days=21)).strftime("%Y%m%d")
    end   = target_date.strftime("%Y%m%d")

    params = {
        "data": "daily",
        "maxrecords": "20",
        "volume": "total",
        "order": "asc",
        "dividends": "false",
        "backadjusted": "false",
        "contractroll": "expiration",
        "symbol": spec.symbol,
        "startDate": start,
        "endDate":   end,
        "customcols": "date|open|high|low|last|volume|openInterest",
        "raw": "1",
    }
    headers = {
        "Referer": (
            f"{BARCHART_BASE}/futures/quotes/{spec.symbol}/price-history/historical"
        ),
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "text/plain, */*; q=0.01",
    }
    if xsrf_token:
        headers["X-XSRF-TOKEN"] = xsrf_token

    resp = http.get(EOD_ENDPOINT, params=params, headers=headers, timeout=REQUEST_TIMEOUT)

    if resp.status_code == 401:
        raise PermissionError(
            f"Barchart từ chối request (401) cho {spec.symbol}. "
            "Session cookie chưa được khởi tạo hoặc Barchart đã thay đổi cơ chế auth."
        )
    resp.raise_for_status()
    return resp.text



# Thứ tự cột trả về từ Barchart queryeod (KHÔNG có header row)
_COL_SYMBOL  = 0
_COL_DATE    = 1
_COL_OPEN    = 2
_COL_HIGH    = 3
_COL_LOW     = 4
_COL_LAST    = 5
_COL_VOLUME  = 6
_COL_OI      = 7


def _parse_eod(csv_text: str, spec: BarchartSpec, target_date: date) -> list[dict]:
    """
    Parse CSV positional — Barchart trả về KHÔNG có header row.
    Format mỗi dòng: symbol,date,open,high,low,last,volume,openInterest

    Chỉ lấy các phiên <= target_date (ngày hôm qua VN) — bỏ qua phiên
    hôm nay nếu Barchart đã có giá nờ trong ngày.

    Tính:
      Δ ngày  — so với phiên liền trước
      Δ tuần  — so với phiên gần nhất cách target_date ít nhất 7 ngày lịch
              (xử lý đúng cuối tuần/lễ giống pattern crawl_eia.py)
    """
    text = csv_text.strip()
    if not text or text.startswith("null") or "error" in text[:50].lower():
        raise ValueError(
            f"Barchart trả về dữ liệu rỗng/lỗi cho {spec.symbol}: {text[:200]}"
        )

    target_str = target_date.isoformat()  # "YYYY-MM-DD"

    rows = []
    for line in csv.reader(io.StringIO(text)):
        if len(line) >= 6 and line[_COL_DATE].strip():
            row_date = line[_COL_DATE].strip()
            # Chỉ lấy phiên ≤ target_date — loại phiên hôm nay nếu thị trường đang mở
            if row_date <= target_str:
                rows.append(line)

    if not rows:
        raise ValueError(
            f"Không có dữ liệu phiên nào <= {target_str} cho {spec.symbol}. "
            "Thị trường có thể đang nghỉ (cuối tuần/lễ)."
        )

    def _f(row: list, col: int) -> Optional[float]:
        try:
            val = row[col].strip()
            return float(val) if val else None
        except (IndexError, ValueError):
            return None

    # Lấy tối đa 30 ngày gần nhất
    recent_rows = rows[-30:]
    results = []

    for i, current_row in enumerate(recent_rows):
        current_date = date.fromisoformat(current_row[_COL_DATE].strip())
        close_price = _f(current_row, _COL_LAST) or 0.0
        if close_price < MIN_VALID_PRICE:
            continue
            
        open_price = _f(current_row, _COL_OPEN)
        high_price = _f(current_row, _COL_HIGH)
        low_price = _f(current_row, _COL_LOW)

        # Tính day_change_pct so với phiên liền trước trong toàn bộ list rows
        day_change_pct = None
        row_idx_in_all = rows.index(current_row)
        if row_idx_in_all >= 1:
            prev_close = _f(rows[row_idx_in_all - 1], _COL_LAST)
            if prev_close and prev_close > 0:
                day_change_pct = round((close_price - prev_close) / prev_close * 100, 3)

        # Tính week_change_pct
        week_change_pct = None
        for r in reversed(rows[:row_idx_in_all]):
            r_date = date.fromisoformat(r[_COL_DATE].strip())
            if (current_date - r_date).days >= 7:
                week_close = _f(r, _COL_LAST)
                if week_close and week_close > 0:
                    week_change_pct = round((close_price - week_close) / week_close * 100, 3)
                break

        results.append({
            "instrument_code": spec.instrument_code,
            "price_date":      current_row[_COL_DATE].strip(),
            "open_price":      open_price,
            "high_price":      high_price,
            "low_price":       low_price,
            "close_price":     close_price,
            "volume":          _f(current_row, _COL_VOLUME),
            "day_change_pct":  day_change_pct,
            "week_change_pct": week_change_pct,
        })
        
    if results:
        latest = results[-1]
        logger.debug(
            "%s: latest=%s  rows=%d  Δ1d=%s  Δ7d=%s",
            spec.symbol, latest["price_date"], len(results), latest["day_change_pct"], latest["week_change_pct"],
        )

    return results


# ---------------------------------------------------------------------------
# DB upsert — theo đúng pattern crawl_eia.py
# ---------------------------------------------------------------------------

async def _upsert(results: list[dict], spec: BarchartSpec, session_factory) -> None:
    """
    Upsert instrument nếu chưa có, upsert price theo UNIQUE(instrument_id, price_date).
    Chạy lại trong ngày → chỉ update, không tạo bản ghi trùng.
    """
    fetched_at = datetime.now(timezone.utc).isoformat()

    async with session_factory() as session:
        async with session.begin():
            # 1. Instrument
            row = await session.execute(
                select(Instrument).where(Instrument.code == spec.instrument_code)
            )
            instrument = row.scalar_one_or_none()

            if instrument is None:
                instrument = Instrument(
                    code=spec.instrument_code,
                    name=spec.instrument_name,
                    category=spec.category,
                    exchange=spec.exchange,
                    unit=spec.unit,
                )
                session.add(instrument)
                await session.flush()

            # 2. Price upsert - batch for all results
            for result in results:
                stmt = (
                    pg_insert(Price)
                    .values(
                        instrument_id=instrument.id,
                        price_date=result["price_date"],
                        price_time=fetched_at,
                        open_price=result.get("open_price"),
                        high_price=result.get("high_price"),
                        low_price=result.get("low_price"),
                        close_price=result["close_price"],
                        day_change_pct=result["day_change_pct"],
                        week_change_pct=result["week_change_pct"],
                        volume=result["volume"],
                        source_name="Barchart",
                    )
                    .on_conflict_do_update(
                        index_elements=["instrument_id", "price_date"],
                        set_={
                            "price_time":      fetched_at,
                            "open_price":      result.get("open_price"),
                            "high_price":      result.get("high_price"),
                            "low_price":       result.get("low_price"),
                            "close_price":     result["close_price"],
                            "day_change_pct":  result["day_change_pct"],
                            "week_change_pct": result["week_change_pct"],
                            "volume":          result["volume"],
                        },
                    )
                )
                await session.execute(stmt)


# ---------------------------------------------------------------------------
# Crawler class
# ---------------------------------------------------------------------------

class BarchartPriceCrawler(BaseCrawler):
    """
    Crawl giá ngày hôm qua từ Barchart cho các hợp đồng tương lai ICE/EEX.
    Không cần API key — dùng session cookie ẩn danh của Barchart.
    """

    name = "Barchart"
    source_type = "web"

    def __init__(self, engine: Optional[AsyncEngine] = None) -> None:
        self._engine = engine

    def run(self) -> dict:
        return asyncio.run(self._run_async())

    async def _run_async(self) -> dict:
        stats: dict = {"prices_saved": 0, "errors": []}

        own_engine = self._engine is None
        settings   = Settings.from_env()
        engine     = self._engine or create_engine(settings.database_url)
        session_factory = build_sessionmaker(engine)

        http = _build_http_session()
        specs = await load_specs_from_db(session_factory)
        if not specs:
            logger.warning(
                "Không có price_crawl_sources nào is_active=true — không có gì để crawl. "
                "Cấu hình qua admin panel (/admin/price-sources)."
            )

        for spec in specs:
            try:
                # 1. Xác định ngày hôm qua theo giờ Việt Nam
                target_date = _yesterday_vn()
                logger.info("target_date (giờ VN): %s", target_date)

                # 2. Khởi tạo session cookie + lấy XSRF token
                xsrf_token = _prime_session(http, spec.symbol)
                logger.debug(
                    "XSRF-TOKEN cho %s: %s…", spec.symbol, xsrf_token[:12]
                )

                # 3. Fetch EOD
                csv_text = _fetch_eod(http, spec, xsrf_token, target_date)

                # 4. Parse
                results = _parse_eod(csv_text, spec, target_date)

                # 5. Lưu DB
                await _upsert(results, spec, session_factory)

                stats["prices_saved"] += len(results)
                if results:
                    latest = results[-1]
                    logger.info(
                        "Da luu %s (%d phien), moi nhat %s: close=%.4f  D1d=%s%%  D7d=%s%%  vol=%s",
                        latest["instrument_code"],
                        len(results),
                        latest["price_date"],
                        latest["close_price"],
                        latest["day_change_pct"],
                        latest["week_change_pct"],
                        latest["volume"],
                    )

            except Exception:
                logger.exception("Lỗi crawl Barchart symbol %s", spec.symbol)
                stats["errors"].append(spec.symbol)

        if own_engine:
            await engine.dispose()

        return stats


# ---------------------------------------------------------------------------
# Chạy thủ công để test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    print(BarchartPriceCrawler().run())
