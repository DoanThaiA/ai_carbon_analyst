"""Test dịch báo cáo sang tiếng Anh — services/report_translation.py (máy dịch giả, không gọi mạng)."""
import asyncio
import copy

from services.report_translation import (
    Translator,
    content_hash,
    translate_report_content,
)


class _WholeTranslator(Translator):
    """Giả lập LLM: tra bảng, chuỗi lạ thì trả "EN(...)"."""

    def __init__(self, table=None):
        self.table = table or {}
        self.calls = []

    async def translate_batch(self, texts):
        self.calls.append(list(texts))
        return [self.table.get(t, f"EN({t})") for t in texts]


class _DropsFirst(_WholeTranslator):
    async def translate_batch(self, texts):
        self.calls.append(list(texts))
        return list(texts) if len(self.calls) == 1 else [f"EN({t})" for t in texts]


def test_content_hash_stable_across_key_order():
    assert content_hash({"a": 1, "b": [1, 2]}) == content_hash({"b": [1, 2], "a": 1})
    assert content_hash({"a": 1}) != content_hash({"a": 2})


def test_deduplicated_and_input_not_mutated():
    content = {"1": {"bullets": [{"text": "giảm mạnh"}, {"text": "giảm mạnh"}]}}
    before = copy.deepcopy(content)
    fake = _WholeTranslator()
    out = asyncio.run(translate_report_content(content, fake))
    assert fake.calls == [["giảm mạnh"]]
    assert content == before and out["1"]["bullets"] == [{"text": "EN(giảm mạnh)"}, {"text": "EN(giảm mạnh)"}]


def test_llm_translates_every_display_string_and_output_taken_as_is():
    content = {
        "1": {"title": "Tóm tắt điều hành", "bullets": ["Giá EUA tăng 1,5% (tCO2)"]},
        "3": {"analysis_blocks": [{"heading": "Phân tích", "content": "Giá tăng"},
                                  {"heading": "Quan điểm thị trường", "content": "Giá giảm"}],
              "trading_scenarios": [{"horizon": "ngắn hạn", "direction": "giảm", "probability": "Cao",
                                     "trading_strategy": "**Entry:** Bán quanh 79,0"}]},
        "6": {"articles": [{"title": "Tin", "topics": ["Khí gas"], "url": "https://x.test/a"}]},
        "8": {"events": [{"event": "Họp", "impact": "Cao", "outcome": "Chưa có thông tin kết quả xác nhận"}]},
        "knowledge_used": {"1": [{"text": "đoạn tài liệu"}]},
    }
    fake = _WholeTranslator({"Tóm tắt điều hành": "Executive Summary", "Khí gas": "Natural gas",
                             "Giá EUA tăng 1,5% (tCO2)": "EUA prices rose 1.5% (tCO₂)"})
    out = asyncio.run(translate_report_content(content, fake))
    assert len(fake.calls) == 1
    assert out["1"]["title"] == "Executive Summary"
    assert out["1"]["bullets"] == ["EUA prices rose 1.5% (tCO₂)"]  # nhận nguyên văn, không kiểm số
    assert out["3"]["analysis_blocks"][1]["heading"] == "EN(Quan điểm thị trường)"
    assert out["3"]["trading_scenarios"][0]["trading_strategy"] == "EN(**Entry:** Bán quanh 79,0)"
    assert out["6"]["articles"][0]["topics"] == ["Natural gas"]
    # Enum/lính gác frontend so sánh === và tài liệu tri thức nội bộ giữ nguyên.
    sc = out["3"]["trading_scenarios"][0]
    assert (sc["horizon"], sc["direction"], sc["probability"]) == ("ngắn hạn", "giảm", "Cao")
    assert out["3"]["analysis_blocks"][0]["heading"] == "Phân tích"
    assert out["8"]["events"][0]["impact"] == "Cao"
    assert out["8"]["events"][0]["outcome"] == "Chưa có thông tin kết quả xác nhận"
    assert out["knowledge_used"] == content["knowledge_used"]


def test_missing_translations_resent_once():
    fake = _DropsFirst()
    out = asyncio.run(translate_report_content({"text": "Giá tăng"}, fake))
    assert out["text"] == "EN(Giá tăng)" and len(fake.calls) == 2
