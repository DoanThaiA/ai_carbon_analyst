import asyncio
from types import SimpleNamespace

from services import report_generator as rg


class FakeMessages:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        stop, text = self.replies.pop(0)
        return SimpleNamespace(
            stop_reason=stop, stop_details=None,
            content=[SimpleNamespace(type="text", text=text)] if text else [],
        )


def _patch(monkeypatch, replies):
    fake = FakeMessages(replies)
    monkeypatch.setattr(rg, "_get_anthropic_client", lambda: SimpleNamespace(messages=fake))
    return fake


def test_section6_uses_haiku_5_5_low_effort(monkeypatch):
    fake = _patch(monkeypatch, [("end_turn", '{"summary": "ok"}')])
    assert asyncio.run(rg._call_llm("p", model=rg.REPORT_MODEL_HAIKU, max_tokens=768)) == '{"summary": "ok"}'
    call = fake.calls[0]
    assert call["model"] == "claude-haiku-5-5" and call["output_config"] == {"effort": "low"}
    assert "system" not in call


def test_haiku_refusal_falls_back_to_4_5(monkeypatch):
    fake = _patch(monkeypatch, [("refusal", ""), ("end_turn", '{"summary": "ok"}')])
    assert asyncio.run(rg._call_llm("p", model=rg.REPORT_MODEL_HAIKU)) == '{"summary": "ok"}'
    assert [c["model"] for c in fake.calls] == ["claude-haiku-5-5", "claude-haiku-4-5"]
    assert "output_config" not in fake.calls[1]


def test_opus_sections_unchanged(monkeypatch):
    fake = _patch(monkeypatch, [("end_turn", "x")])
    asyncio.run(rg._call_llm("p", system="S"))
    call = fake.calls[0]
    assert call["model"] == rg.REPORT_MODEL_OPUS and "output_config" not in call and call["system"] == "S"
