"""
Module lấy giá thị trường cho "Bảng giá nhanh" và biểu đồ nến EUA.

- WTI, Brent: dùng yfinance (miễn phí, đủ tin cậy cho tham khảo).
- EUA, TTF, German Power, NEWC/API 2-4-5: đây là dữ liệu độc quyền của
  ICE/EEX/GlobalCOAL, KHÔNG có API miễn phí hợp pháp. Để dạng interface
  (PriceProvider) - cắm implementation thật vào tuỳ theo công ty dùng
  Bloomberg/Refinitiv/ICE Data Vault/Barchart OnDemand.
"""
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import List, Optional

import yfinance as yf

from schemas.crawl_models import PriceQuote

logger = logging.getLogger(__name__)


class PriceProvider(ABC):
    """Interface chung — implement lớp này khi có nguồn dữ liệu giá thật cho EUA/TTF/..."""

    @abstractmethod
    def get_quote(self, ticker: str) -> Optional[PriceQuote]:
        ...

    @abstractmethod
    def get_ohlc_history(self, ticker: str, days: int = 30):
        """Trả về DataFrame OHLC để vẽ biểu đồ nến (candlestick)."""
        ...


class YFinanceProvider(PriceProvider):
    """Dùng cho các ticker có sẵn trên Yahoo Finance: WTI, Brent."""

    def get_quote(self, ticker: str) -> Optional[PriceQuote]:
        try:
            data = yf.Ticker(ticker)
            hist = data.history(period="5d")
            if hist.empty:
                logger.warning("Không có dữ liệu giá cho %s", ticker)
                return None

            last_close = float(hist["Close"].iloc[-1])
            prev_close = float(hist["Close"].iloc[-2]) if len(hist) > 1 else last_close
            change_pct = ((last_close - prev_close) / prev_close) * 100 if prev_close else 0.0

            return PriceQuote(
                instrument=ticker,
                ticker=ticker,
                price=round(last_close, 2),
                change_pct_day=round(change_pct, 2),
                as_of=datetime.now(timezone.utc),
                source="Yahoo Finance",
            )
        except Exception:
            logger.exception("Lỗi lấy giá cho %s", ticker)
            return None

    def get_ohlc_history(self, ticker: str, days: int = 30):
        data = yf.Ticker(ticker)
        return data.history(period=f"{days}d")


class ManualOrVendorProvider(PriceProvider):
    """
    TODO: cắm implementation thật khi công ty xác định nguồn dữ liệu cho
    EUA (ICE), TTF (ICE), German Power (EEX), NEWC/API 2-4-5 (GlobalCOAL).
    Ví dụ khi có Refinitiv: import và gọi `refinitiv-data` SDK ở đây.

    Raise lỗi rõ ràng thay vì âm thầm trả về giá sai/giá cũ — báo cáo tài
    chính sai số liệu nguy hiểm hơn nhiều so với báo cáo báo lỗi rõ ràng.
    """

    def get_quote(self, ticker: str) -> Optional[PriceQuote]:
        raise NotImplementedError(
            f"Chưa cấu hình nguồn dữ liệu giá cho '{ticker}'. "
            "Cần Bloomberg/Refinitiv/ICE Data Vault/Barchart OnDemand, "
            "hoặc tạm dùng web search tool của Claude làm giá tham khảo "
            "(ghi rõ 'giá tham khảo, có độ trễ' trong báo cáo)."
        )

    def get_ohlc_history(self, ticker: str, days: int = 30):
        raise NotImplementedError("Xem ghi chú ở get_quote().")


INSTRUMENT_PROVIDERS = {
    "CL=F": YFinanceProvider(),  # WTI
    "BZ=F": YFinanceProvider(),  # Brent
    "EUA_DEC26": ManualOrVendorProvider(),
    "TTF_FRONT_MONTH": ManualOrVendorProvider(),
    "DEBY1": ManualOrVendorProvider(),
    "NEWC_INDEX": ManualOrVendorProvider(),
}


def fetch_all_quotes(tickers: List[str]) -> List[PriceQuote]:
    """Lấy giá cho danh sách ticker, bỏ qua (có log) ticker nào chưa có provider thật."""
    quotes = []
    for ticker in tickers:
        provider = INSTRUMENT_PROVIDERS.get(ticker)
        if provider is None:
            logger.warning("Không tìm thấy provider cho ticker %s", ticker)
            continue
        try:
            quote = provider.get_quote(ticker)
            if quote:
                quotes.append(quote)
        except NotImplementedError as e:
            logger.warning(str(e))
    return quotes
