"""Link trang giá dưới câu trả lời Jenny chat (quote_chat.price_source_candidates / price_sources_for_answer)."""
import asyncio
from types import SimpleNamespace

from services import quote_chat as qc

ROWS = [
    SimpleNamespace(symbol="CK*0", instrument_code="EUA", instrument_name="EUA Carbon Futures (Front Month)"),
    SimpleNamespace(symbol="TG*0", instrument_code="TTF", instrument_name="TTF Dutch Natural Gas"),
    SimpleNamespace(symbol="NG*0", instrument_code="NG", instrument_name="Henry Hub Natural Gas"),
    SimpleNamespace(symbol="ICE:517:681:243", instrument_code="API2", instrument_name="API2 Coal ARA"),
    SimpleNamespace(symbol="ICE:1:2", instrument_code="X", instrument_name="ICE không có pageId"),
]


class FakeSession:
    async def execute(self, _stmt):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: ROWS))


def _candidates(tools):
    return asyncio.run(qc.price_source_candidates(FakeSession(), tools))


def test_only_price_tools_produce_links():
    assert _candidates(["search_news", "get_report_section"]) == []
    urls = [c["url"] for c in _candidates(["get_market_prices"])]
    assert urls == [
        "https://www.barchart.com/futures/quotes/CK%2A0/overview",
        "https://www.barchart.com/futures/quotes/TG%2A0/overview",
        "https://www.barchart.com/futures/quotes/NG%2A0/overview",
        "https://www.ice.com/products/243",
    ]  # mã ICE không khai pageId → không có link


def test_filter_by_mentioned_instruments():
    cands = _candidates(["get_market_prices", "get_eua_details"])
    answer = "Jenny nhận định: EUA đóng cửa 81,71 (−0,7%), giá API 2 tăng; giá TĂNG chung, khí ổn định."
    got = qc.price_sources_for_answer(cands, answer)
    assert [g["source_name"] for g in got] == ["Barchart", "ICE"]
    assert got[0]["title"] == "Giá EUA Carbon Futures (Front Month) — Barchart" and "match" not in got[0]
    assert qc.price_sources_for_answer(cands, "Henry Hub giảm") [0]["url"].endswith("NG%2A0/overview")
    assert qc.price_sources_for_answer(cands, "Không nhắc mã nào.") == []


def test_cbam_and_auction_pages():
    cands = _candidates(["get_cbam_estimate", "get_eua_auctions"])
    got = qc.price_sources_for_answer(cands, "Giá CBAM ước tính quý này 76; phiên đấu giá EEX gần nhất...")
    assert [g["url"] for g in got] == [qc.CBAM_PRICE_PAGE_URL, qc.EEX_AUCTION_PAGE_URL]
