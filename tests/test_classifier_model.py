import asyncio
from types import SimpleNamespace

from crawl_news import classification as c

OK_JSON = '{"topics": ["eua_ets"], "confidence": 0.9, "is_relevant": true, "is_hot_news": false}'


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


def _classifier(replies):
    clf = c.AnthropicClassifier(api_key="x")
    fake = FakeMessages(replies)
    clf._client = SimpleNamespace(messages=fake)
    return clf, fake


def test_default_is_haiku_5_5_with_cached_system_and_low_effort():
    clf, fake = _classifier([("end_turn", OK_JSON)])
    result = asyncio.run(clf.classify("EUA tăng", "nội dung"))
    call = fake.calls[0]
    assert call["model"] == "claude-haiku-5-5" == c.DEFAULT_CLASSIFIER_MODEL
    assert call["output_config"] == {"effort": "low"} and call["thinking"] == {"type": "disabled"}
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "temperature" not in call
    assert [t.value for t in result.topics] == ["eua_ets"]


def test_refusal_falls_back_to_haiku_4_5_without_effort():
    clf, fake = _classifier([("refusal", ""), ("end_turn", OK_JSON)])
    result = asyncio.run(clf.classify("Xung đột Hormuz", "nội dung"))
    assert [k["model"] for k in fake.calls] == ["claude-haiku-5-5", "claude-haiku-4-5"]
    assert "output_config" not in fake.calls[1]
    assert result.is_relevant
