"""Test dịch báo cáo sang tiếng Anh — services/report_translation.py (máy dịch giả, không gọi mạng)."""
import asyncio
import copy

from services.report_translation import (
    Translator,
    content_hash,
    split_for_translation,
    translate_report_content,
)


class _FakeTranslator(Translator):
    """Bọc đoạn được dịch trong <...> để thấy rõ phần nào đã qua máy dịch."""

    def __init__(self):
        self.calls = []

    async def translate_batch(self, texts):
        self.calls.append(list(texts))
        return [f"<{t}>" for t in texts]


def _tr(content):
    fake = _FakeTranslator()
    return asyncio.run(translate_report_content(content, fake)), fake


def test_anchors_use_fixed_glossary_not_machine():
    out, _ = _tr({"content": "Chính sách: siết chặt\n- ý một\n**Tổng hợp:** [TÍCH CỰC] Giá EUA tăng"})
    assert out["content"] == "Policy: <siết chặt>\n- <ý một>\n**Summary:** [POSITIVE] <Giá EUA tăng>"


def test_strategy_labels_and_numbers_kept():
    s = "**Entry:** Bán quanh 79,0–79,5 EUR/tCO2\n**Mục tiêu:** 75,5\n**Quản trị rủi ro:** Cắt lỗ nếu trên 81,0"
    out, _ = _tr({"trading_strategy": s})
    lines = out["trading_strategy"].split("\n")
    assert lines[0].startswith("**Entry:** <") and lines[1] == "**Target:** 75,5"
    assert lines[2].startswith("**Risk management:** <")


def test_protected_keys_and_non_vietnamese_untouched():
    content = {
        "3": {"trading_scenarios": [{"horizon": "ngắn hạn", "direction": "giảm", "probability": "Cao",
                                     "condition": "Nếu giá giảm"}],
              "analysis_blocks": [{"heading": "Cần theo dõi", "content": "1. Theo dõi đấu giá"}]},
        "9": {"items": [{"source": "Reuters", "title": "EU carbon prices fall", "url": "https://x.test/a"}]},
        "8": {"events": [{"event": "Tồn kho dầu EIA", "impact": "Cao", "outcome": "Chưa có thông tin kết quả xác nhận"}]},
    }
    out, _ = _tr(content)
    sc = out["3"]["trading_scenarios"][0]
    assert (sc["horizon"], sc["direction"], sc["probability"]) == ("ngắn hạn", "giảm", "Cao")
    assert sc["condition"] == "<Nếu giá giảm>"
    assert out["3"]["analysis_blocks"][0] == {"heading": "Cần theo dõi", "content": "1. <Theo dõi đấu giá>"}
    assert out["9"] == content["9"]
    assert out["8"]["events"][0] == {"event": "<Tồn kho dầu EIA>", "impact": "Cao",
                                     "outcome": "Chưa có thông tin kết quả xác nhận"}


def test_urls_and_unknown_bold_labels():
    out, _ = _tr({"text": "**Thị trường:** xem https://a.test/ngày-mai nhé"})
    assert out["text"] == "**<Thị trường>:** <xem> https://a.test/ngày-mai <nhé>"


def test_segments_deduplicated_and_input_not_mutated():
    content = {"1": {"bullets": [{"text": "giảm mạnh"}, {"text": "giảm mạnh"}]}}
    before = copy.deepcopy(content)
    out, fake = _tr(content)
    assert fake.calls == [["giảm mạnh"]]
    assert content == before and out["1"]["bullets"][1]["text"] == "<giảm mạnh>"


def test_split_keeps_text_reassemblable():
    s = "- **EUA:** đóng cửa 79,20 EUR/tCO2.\n\n**Kết luận:** ổn"
    assert "".join(t for _, t in split_for_translation(s)).replace("EUA", "EUA") == \
        s.replace("**Kết luận:**", "**Conclusion:**")


def test_content_hash_stable_across_key_order():
    assert content_hash({"a": 1, "b": [1, 2]}) == content_hash({"b": [1, 2], "a": 1})
    assert content_hash({"a": 1}) != content_hash({"a": 2})


def test_numbers_codes_and_terms_masked_from_machine():
    fake = _FakeTranslator()
    out = asyncio.run(translate_report_content(
        {"text": "Giá TTF giảm 2,1% xuống 31,5 EUR/MWh, cắt lỗ nếu đóng cửa trên 81,0"}, fake))
    sent = fake.calls[0][0]
    assert "TTF" not in sent and "2,1" not in sent and "EUR/MWh" not in sent and "cắt lỗ" not in sent.lower()
    assert out["text"] == "<Giá TTF giảm 2.1% xuống 31.5 EUR/MWh, stop-loss nếu closes above 81.0>"


class _DropPlaceholderOnce(Translator):
    """Lượt đầu (kiểu X0X) làm mất placeholder, lượt sau (kiểu [0]) dịch đúng."""

    def __init__(self, always_fail=False):
        self.calls = []
        self.always_fail = always_fail

    async def translate_batch(self, texts):
        self.calls.append(list(texts))
        if self.always_fail or len(self.calls) == 1:
            return ["broken" for _ in texts]
        return [f"EN {t}" for t in texts]


def test_retries_with_second_placeholder_style():
    fake = _DropPlaceholderOnce()
    out = asyncio.run(translate_report_content({"text": "giá EUA giảm 3%"}, fake))
    assert fake.calls == [["giá X0X giảm X1X"], ["giá [0] giảm [1]"]]
    assert out["text"] == "EN giá EUA giảm 3%"


def test_keeps_vietnamese_when_placeholders_lost():
    out = asyncio.run(translate_report_content({"text": "giá EUA giảm 3%"}, _DropPlaceholderOnce(always_fail=True)))
    assert out["text"] == "giá EUA giảm 3%"
