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


# ── Luồng Claude: LLM dịch MỌI chữ hiển thị, không bảng cứng, không ghép mảnh ─────────────
import unicodedata  # noqa: E402

from services.report_translation import english_numbers, translation_problems  # noqa: E402


def _ascii(text):
    """"Bản dịch" giả: bỏ dấu tiếng Việt — giữ nguyên dòng, gạch đầu dòng, **, số."""
    return unicodedata.normalize("NFKD", text.replace("đ", "d").replace("Đ", "D")).encode("ascii", "ignore").decode()


class _WholeTranslator(Translator):
    """Giả lập LLM: tra bảng theo chuỗi đã đổi số, chuỗi lạ thì trả bản bỏ dấu; `retry` = bảng cho lần dịch lại."""

    whole_strings = True

    def __init__(self, table=None, retry=None):
        self.table = table or {}
        self.retry = retry or {}
        self.calls = []
        self.notes = []

    async def translate_batch(self, texts):
        self.calls.append(list(texts))
        return [self.table.get(t, _ascii(t)) for t in texts]

    async def retranslate(self, text, note):
        self.notes.append(note)
        return self.retry.get(text, self.table.get(text, _ascii(text)))


def test_english_numbers_converts_before_sending():
    assert english_numbers("Bán quanh 79,0–79,5 EUR/tCO2, giảm -0,3%") == "Bán quanh 79.0–79.5 EUR/tCO2, giảm -0.3%"
    assert english_numbers("120.900 tấn, xem https://x.test/a,1") == "120,900 tấn, xem https://x.test/a,1"
    assert english_numbers("ngày 13/10/2026 theo 15/2025/NĐ-CP") == "ngày 13/10/2026 theo 15/2025/NĐ-CP"


def test_llm_translates_every_display_string_without_fixed_tables():
    content = {
        "1": {"title": "Tóm tắt điều hành", "bullets": ["Giá EUA tăng 1,5%"]},
        "3": {"analysis_blocks": [{"heading": "Phân tích", "content": "Giá tăng"},
                                  {"heading": "Quan điểm thị trường", "content": "Giá giảm"}],
              "trading_scenarios": []},
        "6": {"articles": [{"title": "Tin", "topics": ["Khí gas"], "url": "https://x.test/a"}]},
        "8": {"events": [{"event": "Họp", "impact": "Cao", "outcome": "Chưa có thông tin kết quả xác nhận"}]},
        "ets_policy": [{"source_name": "Ủy ban châu Âu", "source": "Reuters"}],
        "knowledge_used": {"1": [{"text": "đoạn tài liệu"}]},
    }
    fake = _WholeTranslator({"Tóm tắt điều hành": "Executive Summary", "Quan điểm thị trường": "Market View",
                             "Khí gas": "Natural gas"})
    out = asyncio.run(translate_report_content(content, fake))
    sent = [t for call in fake.calls for t in call]
    # Tiêu đề mục, tiêu đề khối, nhãn chủ đề, tên nguồn đều do LLM dịch (không qua FIXED_STRINGS/TOPIC_LABELS_EN).
    assert {"Tóm tắt điều hành", "Quan điểm thị trường", "Khí gas", "Ủy ban châu Âu", "Giá EUA tăng 1.5%"} <= set(sent)
    assert out["1"]["title"] == "Executive Summary"
    assert out["3"]["analysis_blocks"][1]["heading"] == "Market View"
    assert out["6"]["articles"][0]["topics"] == ["Natural gas"]
    # Enum/lính gác frontend so sánh === và tài liệu tri thức nội bộ giữ nguyên.
    assert out["3"]["analysis_blocks"][0]["heading"] == "Phân tích"
    assert out["8"]["events"][0]["impact"] == "Cao"
    assert out["8"]["events"][0]["outcome"] == "Chưa có thông tin kết quả xác nhận"
    assert out["knowledge_used"] == content["knowledge_used"]
    assert len(fake.calls) == 1 and not fake.notes


def test_translation_problems_checks():
    src = "**Tổng hợp:** [TÍCH CỰC] Giá EUA tăng 1,5% (https://x.test/a)\n- Khí TTF giảm"
    pre = english_numbers(src)
    good = "**Summary:** [POSITIVE] EUA prices rose 1.5% (https://x.test/a)\n- TTF gas fell"
    assert translation_problems(src, pre, good) == ([], [])
    hard = lambda out: translation_problems(src, pre, out)[0]  # noqa: E731
    assert any("**Summary:**" in p for p in hard(good.replace("**Summary:**", "**Overall:**")))
    assert any("[POSITIVE]" in p for p in hard(good.replace("[POSITIVE]", "")))
    assert any("1.6" in p for p in hard(good.replace("1.5%", "1.6%")))
    assert hard(good.replace("1.5%", "1,5%"))                       # chưa đổi kiểu số
    assert any("lines" in p for p in hard(good.replace("\n", " ")))
    assert any("URL" in p for p in hard(good.replace("https://x.test/a", "link")))
    assert translation_problems("Chính sách: siết chặt", "Chính sách: siết chặt", "Policy: tightening") == ([], [])
    assert translation_problems("Chính sách: siết chặt", "Chính sách: siết chặt", "Policies: tightening")[0]
    assert translation_problems("Giá tăng theo 15/2025/NĐ-CP", "Giá tăng theo 15/2025/NĐ-CP",
                                "Prices rose under 15/2025/NĐ-CP") == ([], [])
    assert translation_problems("Giá tăng", "Giá tăng", "Prices tăng")[1]   # còn chữ Việt = lỗi mềm


def test_rejected_translation_is_retried_with_feedback():
    src = "**Tổng hợp:** [TÍCH CỰC] Giá EUA tăng 1,5%"
    pre = english_numbers(src)
    fake = _WholeTranslator({pre: "**Overall:** [POSITIVE] EUA rose 1.5%"},
                            retry={pre: "**Summary:** [POSITIVE] EUA rose 1.5%"})
    out = asyncio.run(translate_report_content({"text": src}, fake))
    assert out["text"] == "**Summary:** [POSITIVE] EUA rose 1.5%"
    assert "**Summary:**" in fake.notes[0] and "**Overall:**" in fake.notes[0]


def test_still_broken_after_retries_keeps_vietnamese():
    src = "**Tổng hợp:** Giá EUA tăng 1,5%"
    fake = _WholeTranslator({english_numbers(src): "**Overall:** EUA rose 1.5%"})
    out = asyncio.run(translate_report_content({"text": src}, fake))
    assert out["text"] == src
    assert len(fake.notes) == 2  # LLM_RETRIES


def test_soft_problem_only_is_accepted_after_retries():
    src = "Giá tăng tại Quảng Ninh"
    fake = _WholeTranslator({src: "Prices rose in Quảng Ninh"})
    out = asyncio.run(translate_report_content({"text": src}, fake))
    assert out["text"] == "Prices rose in Quảng Ninh"
    assert len(fake.notes) == 2


# ── Thẻ KHUYẾN NGHỊ VỊ THẾ phải giữ nguyên khi dịch ────────────────────────────────────
from services.strategy_levels import position_card_plan  # noqa: E402

_SC = {"horizon": "ngắn hạn", "direction": "giảm", "probability": "Cao",
       "condition": "Nếu giá giữ dưới 80,0",
       "trading_strategy": "**Entry:** Bán khi đóng cửa dưới 78,5\n**Mục tiêu:** 75,5\n**Quản trị rủi ro:** Cắt lỗ nếu trên 81,0 trong 2 phiên"}


class _CardTranslator(_WholeTranslator):
    """Lần đầu dịch chiến lược làm lệch thẻ (bỏ "close below" → lệnh giới hạn, "over 2" → cắt lỗ sai);
    lần dịch lại theo góp ý thì đúng."""

    BAD = "**Entry:** Sell at 78.5\n**Target:** 75.5\n**Risk management:** Stop-loss over 2 sessions above 81.0"
    GOOD = "**Entry:** Sell on a close below 78.5\n**Target:** 75.5\n**Risk management:** Stop-loss if above 81.0 within 2 sessions"

    def __init__(self, good=True):
        super().__init__()
        self.good = good

    async def translate_batch(self, texts):
        return [self.BAD if t.startswith("**Entry:**") else "If prices hold below 80.0" for t in texts]

    async def retranslate(self, text, note):
        self.notes.append(note)
        return self.GOOD if self.good else self.BAD


def test_position_card_parser_reads_english_like_vietnamese():
    en = {**_SC, "condition": "If prices hold below 80.0", "trading_strategy": _CardTranslator.GOOD}
    assert position_card_plan(en) == position_card_plan(_SC)
    assert position_card_plan(_SC)["legs"][0]["kind"] == "breakout"
    assert position_card_plan({**_SC, "trading_strategy": _CardTranslator.BAD}) != position_card_plan(_SC)


def test_broken_position_card_is_retranslated_with_feedback():
    fake = _CardTranslator()
    out = asyncio.run(translate_report_content({"3": {"trading_scenarios": [_SC]}}, fake))
    assert out["3"]["trading_scenarios"][0]["trading_strategy"] == _CardTranslator.GOOD
    assert fake.notes and "position-card" in fake.notes[0]


def test_position_card_falls_back_to_vietnamese_when_still_broken():
    out = asyncio.run(translate_report_content({"3": {"trading_scenarios": [_SC]}}, _CardTranslator(good=False)))
    sc = out["3"]["trading_scenarios"][0]
    assert sc["trading_strategy"] == _SC["trading_strategy"]
    assert position_card_plan(sc) == position_card_plan(_SC)


def test_dates_may_be_written_in_english_words():
    # Log 22/10/2026: 18 đoạn bị loại oan vì Claude viết "December 2026" cho "tháng 12/2026".
    ok = [
        ("dời sang Hội đồng tháng 12/2026, gửi ngày 30/09/2026", "pushed to the December 2026 Council, sent on 30 September 2026"),
        ("Hội đồng châu Âu 15–16/10 chưa kết luận", "The 15–16 October European Council reached no conclusion"),
        ("từ 01/01/2031", "from 1 January 2031"),
        ("lần đầu từ giữa tháng 5", "for the first time since mid-May"),
        ("Triland ngày 2026-10-21", "Triland on 21 October 2026"),
        ("theo 15/2025/NĐ-CP và Q3/2026", "under 15/2025/NĐ-CP and Q3/2026"),
    ]
    for vi, en in ok:
        assert translation_problems(vi, english_numbers(vi), en)[0] == [], vi
    vi = "gửi ngày 30/09/2026"
    assert translation_problems(vi, english_numbers(vi), "sent on 30 August 2026")[0]   # sai tháng
    assert translation_problems(vi, english_numbers(vi), "sent on 31 September 2026")[0]  # sai ngày


def test_card_feedback_explains_hyphenated_counts():
    from services.report_translation import card_differences
    vi = {"direction": "đi ngang",
          "trading_strategy": "**Entry:** Tích lũy dần quanh 82,35 (đáy 30 phiên)\n**Mục tiêu:** chưa đủ cơ sở định lượng\n"
                              "**Quản trị rủi ro:** Cắt lỗ nếu đóng cửa dưới 80,50"}
    bad = {**vi, "trading_strategy": "**Entry:** Gradually accumulate around 82.35 (30-session low)\n"
                                     "**Target:** no quantitative basis yet\n**Risk management:** Stop-loss on a close below 80.50"}
    good = {**bad, "trading_strategy": bad["trading_strategy"].replace("(30-session low)", "(low of the last 30 sessions)")}
    assert position_card_plan(good) == position_card_plan(vi)
    diffs = card_differences(position_card_plan(vi), position_card_plan(bad))
    assert len(diffs) == 1 and "30-session" in diffs[0]
