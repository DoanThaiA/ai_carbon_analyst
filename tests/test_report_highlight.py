"""content["1"].highlight — "Điều đáng quan tâm nhất hôm nay" (report_generator._resolve_highlight + backfill)."""
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
    _, user, lookup = rg._highlight_backfill_prompt(CONTENT, "2026-10-22")
    assert lookup == {1: {"source": "Reuters", "url": A1}, 2: {"source": "Argus", "url": A2}}
    assert "[1] **Chính sách ETS:** EU nới ngưỡng MSR." in user and "[2] Tồn kho khí EU 95%." in user
    assert "81.71 EUR/tCO2" in user and "thấp nhất 81.71, cao nhất 82.3" in user
    assert '"highlight"' in user and "{{" not in user


def test_backfill_prompt_no_price_report_hides_prices():
    c = {**CONTENT, "2": {**CONTENT["2"], "no_price_data": True}}
    _, user, _ = rg._highlight_backfill_prompt(c, "2026-10-19")
    assert "BẢNG GIÁ" not in user and '"type": "news"' in user


def test_resolve_highlight_maps_source_and_rejects_bad_type():
    lookup = {1: {"source": "Reuters", "url": A1}}
    assert rg._resolve_highlight({"type": "News", "text": "x", "source_index": 1}, lookup, False) == {
        "type": "news", "text": "x", "source_name": "Reuters", "source_url": A1}
    assert rg._resolve_highlight({"type": "price", "text": "x"}, lookup, True) is None  # báo cáo không có giá
    assert rg._resolve_highlight({"type": "rumor", "text": "x"}, lookup, False) is None
    assert rg._resolve_highlight({"type": "price", "text": " "}, lookup, False) is None


def test_generate_highlight_for_content(monkeypatch):
    async def fake_llm(prompt, system="", **kw):
        return '{"highlight": {"type": "news", "text": "EU nới ngưỡng MSR — áp lực giảm lên EUA.", "source_index": 1}}'
    monkeypatch.setattr(rg, "_call_llm", fake_llm)
    hl = asyncio.run(rg.generate_highlight_for_content(CONTENT, "2026-10-22"))
    assert hl == {"type": "news", "text": "EU nới ngưỡng MSR — áp lực giảm lên EUA.", "source_name": "Reuters", "source_url": A1}
