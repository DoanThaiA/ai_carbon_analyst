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


def test_topic_tags_use_fixed_labels():
    out, _ = _tr({"6": {"international": [{"title": "t", "topics": ["Năng lượng tái tạo", "Địa chính trị", "Lạ"]}]}})
    assert out["6"]["international"][0]["topics"] == ["Renewables", "Geopolitics", "Lạ"]


def test_topic_labels_cover_generator():
    from services.report_generator import TOPIC_DISPLAY_LABELS
    from services.report_translation import TOPIC_LABELS_EN
    assert set(TOPIC_DISPLAY_LABELS.values()) <= set(TOPIC_LABELS_EN)


class _GarbageWithPlaceholders(Translator):
    """Đoạn có placeholder → rác; nguyên văn → dịch giữ số; câu đơn lẻ → dịch được."""

    async def translate_batch(self, texts):
        out = []
        for t in texts:
            if "X0X" in t or "[0]" in t:
                out.append("Organisation" if "Lịch" in t or "và" in t else f"EN {t}")
            else:
                out.append("Event calendar next 7 days" if t.startswith("Lịch") else "garbage")
        return out


def test_raw_fallback_accepts_only_when_numbers_kept():
    out = asyncio.run(translate_report_content({"title": "Lịch họp 7 ngày tới"}, _GarbageWithPlaceholders()))
    assert out["title"] == "Event calendar next 7 days"


def test_sentence_split_fallback():
    out = asyncio.run(translate_report_content(
        {"text": "Giá EUA giảm 2% và yếu. Giá TTF tăng 1%."}, _GarbageWithPlaceholders()))
    # Cả đoạn hỏng → tách câu; chỉ câu 1 (vẫn hỏng) giữ tiếng Việt, câu 2 vẫn được dịch
    assert out["text"] == "Giá EUA giảm 2% và yếu. EN Giá TTF tăng 1%."


class _FailsOnMultiSentence(Translator):
    async def translate_batch(self, texts):
        return ["broken" if ". " in t else f"EN({t})" for t in texts]


def test_sentence_split_fallback_success():
    out = asyncio.run(translate_report_content({"text": "Giá EUA giảm 2%. Giá TTF tăng 1%."}, _FailsOnMultiSentence()))
    assert out["text"] == "EN(Giá EUA giảm 2%.) EN(Giá TTF tăng 1%.)"


def test_fixed_section_titles_not_machine_translated():
    out, fake = _tr({"8": {"title": "Lịch sự kiện 7 ngày tới", "events": []}})
    assert out["8"]["title"] == "Events calendar — next 7 days" and fake.calls == []


def test_loose_unmask_fixes_mangled_brackets():
    from services.report_translation import _LOOSE_PATTERNS, _cleanup_markers, unmask_segment
    loose = lambda out, toks, src: _cleanup_markers(unmask_segment(out, toks, _LOOSE_PATTERNS["[{}]"]), "[{}]", src)
    assert loose("Close ([0][1]]: [2].", ["2026-10-13", "x", "88.05 EUR/tCO2"], "src (a)") == \
        "Close (2026-10-13x): 88.05 EUR/tCO2."
    assert loose("lost [0] only", ["1", "2"], "src") is None


def test_repair_raw_overwrites_numbers_in_order():
    from services.report_translation import _repair_raw, mask_segment
    src = "EUA giảm 0,29 (-0,3%)"
    _, tokens, _ = mask_segment(src)
    assert _repair_raw(src, "EUA reduced 0.170 (0.3%)", tokens) == "EUA reduced 0.29 (-0.3%)"
    assert _repair_raw(src, "EUA reduced 0.29 (- 0.3%)", tokens) == "EUA reduced 0.29 (-0.3%)"
    assert _repair_raw(src, "EUU reduced 0.29 (0.3%)", tokens) is None      # mã bị đổi
    assert _repair_raw(src, "EUA reduced 0.29", tokens) is None             # thiếu số


def test_iso_date_and_delta_masked_whole():
    from services.report_translation import mask_segment
    _, tokens, _ = mask_segment("Đóng cửa (2026-10-13): Δ ngày ~1,7%")
    assert tokens == ["2026-10-13", "Δ", "~1,7%"]


def test_number_format_and_units_to_english():
    from services.report_translation import mask_segment
    _, tokens, en = mask_segment("doanh thu 120.900 tỷ đồng, giá 97,25 USD/tấn, EUA 81.4400 EUR/tCO2, Cal-27")
    assert dict(zip(tokens, en)) == {
        "120.900": "120,900", "tỷ đồng": "billion VND", "97,25 USD/tấn": "97.25 USD/t", "EUA": "EUA",
        "81.4400 EUR/tCO2": "81.4400 EUR/tCO2", "Cal-27": "Cal-27",
    }
    _, _, en = mask_segment("tăng 1.250 thùng, giảm 0,5%")  # đoạn dùng dấu phẩy thập phân → dấu chấm là nghìn
    assert en == ["1,250 thùng".split()[0], "0.5%"]


def test_rejects_hallucinated_digits():
    from services.report_translation import PLACEHOLDER_STYLES, unmask_segment
    x_style = PLACEHOLDER_STYLES[0][1]
    assert unmask_segment("price X0X/203", ["97.25 USD"], x_style) is None
    assert unmask_segment("price X0X/t", ["97.25 USD"], x_style) == "price 97.25 USD/t"


def test_arrow_chains_split_and_labels_fixed():
    out, _ = _tr({"x": ["Cơ hội: Chính sách CBAM của EU → Giải pháp: Hỗ trợ doanh nghiệp → Kỳ vọng: Giảm rủi ro."]})
    assert out["x"][0] == "Opportunity: <Chính sách X0X của X1X> → Solution: <Hỗ trợ doanh nghiệp> → Expectation: <Giảm rủi ro.>" \
        .replace("X0X", "CBAM").replace("X1X", "EU")


def test_superscript_units_and_document_numbers_masked():
    from services.report_translation import mask_segment
    _, tokens, _ = mask_segment("diện tích 9.000 m² và 18 nghìn tỷ m³, theo Quyết định số 781/QĐ-UBND ngày 28/02/2026")
    assert tokens == ["9.000 m²", "m³", "781/QĐ-UBND", "28/02/2026"] or tokens == ["9.000 m²", "18", "m³", "781/QĐ-UBND", "28/02/2026"]
    assert "18" in tokens and "781/QĐ-UBND" in tokens and "9.000 m²" in tokens


def test_thousands_with_area_units():
    from services.report_translation import mask_segment
    _, tokens, en = mask_segment("đám cháy rộng 9.000 m², giá 81.4400 EUR/tCO2")
    assert dict(zip(tokens, en)) == {"9.000 m²": "9,000 m²", "81.4400 EUR/tCO2": "81.4400 EUR/tCO2"}
