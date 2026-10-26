"""content["1"].highlight — "Điều đáng quan tâm nhất hôm nay" (report_generator._highlight_prompt / _resolve_highlight)."""
import asyncio

from services import report_generator as rg

A1, A2 = "https://reuters.com/a1", "https://argusmedia.com/a2"
CONTENT = {
    "1": {"bullets": [
        {"text": "**EUA:** Đóng cửa 81,71 (−0,73%).", "source_name": None, "source_url": None},
        {"text": "**Chính sách ETS:** EU nới ngưỡng MSR.", "source_name": "Reuters", "source_url": A1},
    ]},
    "2": {
        "prices": [{"name": "EUA Futures Dec'26", "price": "81.71 EUR/tCO2", "dday": "-0.73%", "dweek": "-0.34%"}],
        "chart_data": [{"date": "2026-10-20", "close": 82.3}, {"date": "2026-10-21", "close": 81.71}],
        "key_developments": [
            {"text": "Tồn kho khí EU 95%.", "impact": "giảm", "source_name": "Argus", "source_url": A2},
            {"text": "EU nới ngưỡng MSR.", "impact": "giảm", "source_name": "Reuters", "source_url": A1},
        ],
    },
}


def test_backfill_prompt_numbers_sources_once_per_url():
    _, user, lookup = rg._highlight_prompt(CONTENT, "2026-10-22")
    assert lookup == {1: {"source": "Reuters", "url": A1}, 2: {"source": "Argus", "url": A2}}
    assert "[1] **Chính sách ETS:** EU nới ngưỡng MSR." in user and "[2] Tồn kho khí EU 95%." in user
    assert "81.71 EUR/tCO2" in user and "thấp nhất 81.71, cao nhất 82.3" in user
    assert '"highlight"' in user and "{{" not in user


def test_backfill_prompt_no_price_report_hides_prices():
    c = {**CONTENT, "2": {**CONTENT["2"], "no_price_data": True}}
    _, user, _ = rg._highlight_prompt(c, "2026-10-19")
    assert "BẢNG GIÁ" not in user and '"type": "news"' in user


def test_resolve_highlight_maps_source_and_rejects_bad_type():
    lookup = {1: {"source": "Reuters", "url": A1}}
    assert rg._resolve_highlight({"type": "News", "text": "x", "source_index": 1}, lookup, False) == {
        "type": "news", "text": "x", "source_name": "Reuters", "source_url": A1}
    assert rg._resolve_highlight({"type": "price", "text": "x"}, lookup, True) is None  # báo cáo không có giá
    assert rg._resolve_highlight({"type": "rumor", "text": "x"}, lookup, False) is None
    assert rg._resolve_highlight({"type": "price", "text": " "}, lookup, False) is None
    assert rg._resolve_highlight({"type": "news", "text": "x", "source_index": 9}, lookup, False) is None  # tin không nguồn
    assert rg._resolve_highlight({"type": "scenario", "text": "x"}, lookup, False) == {
        "type": "scenario", "text": "x", "source_name": None, "source_url": None}
    assert rg._resolve_highlight({"type": "hedge", "text": "x"}, lookup, True) is None  # không có giá → không phòng hộ
    assert rg._resolve_highlight({"type": "biz", "text": "x"}, lookup, True)["type"] == "biz"


def test_generate_highlight_for_content(monkeypatch):
    async def fake_llm(prompt, system="", **kw):
        return '{"highlight": {"type": "news", "text": "EU nới ngưỡng MSR — áp lực giảm lên EUA.", "source_index": 1}}'
    monkeypatch.setattr(rg, "_call_llm", fake_llm)
    hl = asyncio.run(rg.generate_highlight_for_content(CONTENT, "2026-10-22"))
    assert hl == {"type": "news", "text": "EU nới ngưỡng MSR — áp lực giảm lên EUA.", "source_name": "Reuters", "source_url": A1}


FULL = {
    **CONTENT,
    "2": {**CONTENT["2"], "cbam_estimate": {"signal": "increase"}},
    "3": {
        "trading_scenarios": [{"horizon": "ngắn hạn", "direction": "giảm", "probability": "Cao", "price_zone": "80–81",
                               "condition": "thủng 81", "trading_strategy": "Bán 81, cắt lỗ 82,5"}],
        "hedge_action": {"action": "increase", "text": "Tăng phần lõi lên 60%."},
    },
    "biz": {
        "short_term": [{"trigger": "EUA < 80", "action": "Chốt giá CBAM Q4", "source_name": "Argus", "source_url": A2}],
        "reminders": [{"outcome": "triggered", "suggested_date": "2026-10-15", "trigger": "EUA < 82",
                       "action": "Mua Dec-26", "evidence": "EUA đóng 81,71", "source_name": None, "source_url": None}],
    },
}


def test_prompt_covers_all_sections_and_hot_news():
    hot = [{"title": "Na Uy cắt khí", "source": "Reuters", "url": A1, "reason": "[Cắt nguồn cung] ..."},
           {"title": "Ba Lan dừng than", "source": "PAP", "url": "https://pap.pl/x", "reason": None}]
    _, user, lookup = rg._highlight_prompt(FULL, "2026-10-22", hot)
    assert "[1] [TIN NÓNG] **Chính sách ETS:** EU nới ngưỡng MSR." in user  # tin đã trích cũng gắn nhãn nóng
    assert "TIN NÓNG TRONG KỲ" in user and "Ba Lan dừng than" in user and lookup[3]["url"] == "https://pap.pl/x"
    assert "xác suất Cao" in user and "Bán 81, cắt lỗ 82,5" in user
    assert "PHÒNG HỘ CBAM: tín hiệu increase — Tăng phần lõi lên 60%." in user
    assert "[ĐÃ KÍCH HOẠT, đề xuất 2026-10-15] EUA < 82 → Mua Dec-26" in user and "EUA < 80 → Chốt giá CBAM Q4" in user
    assert '"scenario"' in user and '"biz"' in user and "{{" not in user


def test_hot_news_for_highlight_dedupes_by_url():
    news = {"eua_ets": [{"url": A1, "title": "t", "source": "Reuters", "is_hot_news": True, "hot_news_reason": "r"},
                        {"url": A2, "title": "u", "source": "Argus", "is_hot_news": False}],
            "eu_policy": [{"url": A1, "title": "t", "source": "Reuters", "is_hot_news": True}]}
    assert rg._hot_news_for_highlight(news) == [{"title": "t", "source": "Reuters", "url": A1, "reason": "r"}]
