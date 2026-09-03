"""
crawl_barchart.py
=================
Lấy giá đóng cửa ngày hôm qua từ Barchart cho toàn bộ các hợp đồng tương lai
cần thiết cho hệ thống phân tích carbon:

  Carbon   : EUA (CK*0)
  Gas      : TTF (INXU26), Henry Hub NG (NG*0)
  Oil      : Brent (CB*0), WTI (CL*0), Gasoil (LF*0)
  Coal     : API2/ARA (ITF*1), API4/Richards Bay (LV*0)

Kỹ thuật (Playwright, không còn dùng requests):
  Barchart đã bật AWS WAF (trả 202 + JS challenge cho request thường, không
  còn set cookie XSRF-TOKEN) và đổi sang API JSON mới
  (/proxies/core-api/v1/historical/get) — API này còn 403 nếu gọi trực tiếp
  với meta-symbol dạng "CK*0" (phải là symbol hợp đồng thực tế, vd "CKZ26",
  do chính JS trên trang tự resolve).

  Ta mở trang price-history của meta-symbol bằng Chromium (mô phỏng trình
  duyệt thật để vượt qua challenge của AWS WAF), rồi dùng
  `page.expect_response()` để chặn (intercept) đúng response JSON mà trang
  tự gọi (với symbol thực đã resolve) thay vì tự dựng request.

Không cần tài khoản, không cần API key trả phí.
Thay thế hoàn toàn crawl_eia.py (WTI, Brent, Henry Hub) và mở rộng thêm
các instrument mà EIA không cung cấp.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

# Giờ Việt Nam = UTC+7
TZ_VN = timezone(timedelta(hours=7))

from playwright.async_api import (
    Browser,
    BrowserContext,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)
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

BARCHART_BASE      = "https://www.barchart.com"
HISTORICAL_API_PATH = "proxies/core-api/v1/historical/get"
PAGE_TIMEOUT_MS    = 30_000
MIN_VALID_PRICE    = 0.01
MAX_RESULTS_PER_SYMBOL = 30  # chỉ upsert tối đa 30 phiên gần nhất mỗi lần chạy

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/127.0.0.0 Safari/537.36"
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
# Fetch (Playwright) + parse
# ---------------------------------------------------------------------------

def _yesterday_vn() -> date:
    """
    Trả về ngày hôm qua theo giờ Việt Nam (UTC+7).
    Khi chạy lúc 7h sáng VN, date.today() có thể khác với ngày VN thực tế
    nếu server đang dùng UTC — dùng hàm này để luôn đúng.
    """
    return datetime.now(TZ_VN).date() - timedelta(days=1)


async def _fetch_historical_json(context: BrowserContext, spec: BarchartSpec) -> dict:
    """
    Mở trang price-history của meta-symbol (vd "CK*0") trong Chromium và chặn
    (intercept) response JSON mà chính trang gọi tới
    proxies/core-api/v1/historical/get.

    Trang tự resolve meta-symbol sang symbol hợp đồng thực (vd "CKZ26") và tự
    vượt qua challenge AWS WAF khi chạy JS — ta không tự dựng request, chỉ
    lắng nghe response Playwright đã thấy.
    """
    page = await context.new_page()
    try:
        url = f"{BARCHART_BASE}/futures/quotes/{spec.symbol}/price-history/historical"
        async with page.expect_response(
            lambda r: HISTORICAL_API_PATH in r.url,
            timeout=PAGE_TIMEOUT_MS,
        ) as response_info:
            await page.goto(url, timeout=PAGE_TIMEOUT_MS)
        response = await response_info.value

        if response.status != 200:
            raise RuntimeError(
                f"Barchart trả về status {response.status} cho {spec.symbol} "
                f"(url={response.url})"
            )
        return await response.json()
    finally:
        await page.close()


def _parse_historical_json(data: dict, spec: BarchartSpec, target_date: date) -> list[dict]:
    """
    Parse JSON trả về từ proxies/core-api/v1/historical/get: mỗi phần tử
    data["data"] có field "raw" chứa tradeTime ("YYYY-MM-DD"), openPrice,
    highPrice, lowPrice, lastPrice, volume — Barchart trả về mới nhất trước
    (orderDir=desc), ta sắp lại tăng dần để tính Δ ngày/Δ tuần.

    Chỉ lấy các phiên <= target_date (ngày hôm qua VN) — bỏ qua phiên hôm nay
    nếu Barchart đã có giá nờ trong ngày.

    Tính:
      Δ ngày  — so với phiên liền trước
      Δ tuần  — so với phiên gần nhất cách target_date ít nhất 7 ngày lịch
    """
    items = data.get("data") or []
    if not items:
        raise ValueError(f"Barchart trả về dữ liệu rỗng cho {spec.symbol}")

    target_str = target_date.isoformat()

    rows: list[tuple[date, dict]] = []
    for item in items:
        raw = item.get("raw") or {}
        trade_time = raw.get("tradeTime")
        if not trade_time:
            continue
        row_date = date.fromisoformat(trade_time[:10])
        # Chỉ lấy phiên ≤ target_date — loại phiên hôm nay nếu thị trường đang mở
        if row_date.isoformat() <= target_str:
            rows.append((row_date, raw))

    if not rows:
        raise ValueError(
            f"Không có dữ liệu phiên nào <= {target_str} cho {spec.symbol}. "
            "Thị trường có thể đang nghỉ (cuối tuần/lễ)."
        )

    rows.sort(key=lambda r: r[0])  # Barchart trả về mới nhất trước -> sắp tăng dần

    def _num(raw: dict, key: str) -> Optional[float]:
        val = raw.get(key)
        return float(val) if val is not None else None

    results = []
    output_from = max(0, len(rows) - MAX_RESULTS_PER_SYMBOL)

    for i, (row_date, raw) in enumerate(rows):
        close_price = _num(raw, "lastPrice") or 0.0
        if close_price < MIN_VALID_PRICE or i < output_from:
            continue

        # Tính day_change_pct so với phiên liền trước trong toàn bộ list rows
        day_change_pct = None
        if i >= 1:
            prev_close = _num(rows[i - 1][1], "lastPrice")
            if prev_close and prev_close > 0:
                day_change_pct = round((close_price - prev_close) / prev_close * 100, 3)

        # Tính week_change_pct
        week_change_pct = None
        for prev_date, prev_raw in reversed(rows[:i]):
            if (row_date - prev_date).days >= 7:
                week_close = _num(prev_raw, "lastPrice")
                if week_close and week_close > 0:
                    week_change_pct = round((close_price - week_close) / week_close * 100, 3)
                break

        results.append({
            "instrument_code": spec.instrument_code,
            "price_date":      row_date.isoformat(),
            "open_price":      _num(raw, "openPrice"),
            "high_price":      _num(raw, "highPrice"),
            "low_price":       _num(raw, "lowPrice"),
            "close_price":     close_price,
            "volume":          _num(raw, "volume"),
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
    Không cần API key — dùng Chromium (Playwright) để vượt AWS WAF và chặn
    response JSON mà chính trang Barchart tự gọi.
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

        specs = await load_specs_from_db(session_factory)
        if not specs:
            logger.warning(
                "Không có price_crawl_sources nào is_active=true — không có gì để crawl. "
                "Cấu hình qua admin panel (/admin/price-sources)."
            )

        # 1 browser + 1 context dùng chung cho toàn bộ symbol (context giữ lại
        # cookie AWS WAF/CloudFront đã pass challenge, mỗi symbol chỉ cần mở
        # page mới, không cần khởi động lại Chromium)
        async with async_playwright() as p:
            browser: Browser = await p.chromium.launch(headless=True)
            context: BrowserContext = await browser.new_context(
                user_agent=_UA,
                locale="en-US",
            )
            try:
                # Xác định ngày hôm qua theo giờ Việt Nam (giống cho mọi symbol)
                target_date = _yesterday_vn()
                logger.info("target_date (giờ VN): %s", target_date)

                for spec in specs:
                    try:
                        # 1. Mở trang price-history + chặn response JSON
                        data = await _fetch_historical_json(context, spec)

                        # 2. Parse
                        results = _parse_historical_json(data, spec, target_date)

                        # 3. Lưu DB
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

                    except PlaywrightTimeoutError:
                        logger.exception(
                            "Timeout cho Barchart symbol %s — không thấy response %s trong %dms",
                            spec.symbol, HISTORICAL_API_PATH, PAGE_TIMEOUT_MS,
                        )
                        stats["errors"].append(spec.symbol)
                    except Exception:
                        logger.exception("Lỗi crawl Barchart symbol %s", spec.symbol)
                        stats["errors"].append(spec.symbol)
            finally:
                await context.close()
                await browser.close()

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
