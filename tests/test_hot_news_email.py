"""Test dựng email digest Hot News — services/email_sender.py. Hàm build là
THUẦN (không đụng Resend/DB); phần gửi được test bằng Resend giả (httpx.MockTransport)."""
import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from core.config import Settings
from services import email_sender
from services.email_sender import (
    EmailSendError,
    _batches,
    build_hot_news_digest_message,
    send_hot_news_digest_email,
)


def _settings(**overrides) -> Settings:
    base = Settings.from_env()
    values = {**base.__dict__, "resend_api_key": "re_test", "email_from": "bot@mcv.test",
              "app_base_url": "https://app.test", "hot_news_email_batch_size": 2, **overrides}
    return Settings(**values)


def _article(i: int, **overrides):
    values = dict(
        title=f"EUA tăng mạnh {i}",
        url=f"https://news.test/{i}",
        source="reuters.com",
        hot_news_reason=f"Đảo chiều giá EUA {i}",
        published_at=datetime(2026, 9, 23, 1, 30, tzinfo=timezone.utc),
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_single_article_subject_uses_title():
    msg = build_hot_news_digest_message([_article(1)], _settings())
    assert msg.subject == "[HOT NEWS] EUA tăng mạnh 1"
    html = msg.html
    assert "Đảo chiều giá EUA 1" in html
    assert 'href="https://news.test/1"' in html
    assert "08:30 23/09/2026" in html  # hiển thị giờ VN


def test_multiple_articles_subject_uses_count_and_lists_all():
    msg = build_hot_news_digest_message([_article(1), _article(2), _article(3)], _settings())
    assert msg.subject == "[HOT NEWS] 3 tin tức quan trọng vừa được cập nhật"
    for i in (1, 2, 3):
        assert f"EUA tăng mạnh {i}" in msg.html
    assert "https://news.test/3" in msg.text


def test_untrusted_content_is_escaped_and_bad_urls_neutralised():
    msg = build_hot_news_digest_message(
        [_article(1, title="<script>alert(1)</script>\nX", url="javascript:alert(1)")], _settings()
    )
    assert "<script>" not in msg.html
    assert "javascript:" not in msg.html
    assert "\n" not in msg.subject


def test_batches():
    assert _batches(["a", "b", "c"], 2) == [["a", "b"], ["c"]]
    assert _batches([], 2) == []


class _FakeResend:
    """Giả lập POST /emails/batch — ghi lại danh sách `to` của từng request."""

    def __init__(self, fail_batches=()):
        self.requests = []
        self.fail_batches = set(fail_batches)

    def handler(self, request: httpx.Request) -> httpx.Response:
        idx = len(self.requests)
        self.requests.append(request)
        if idx in self.fail_batches:
            return httpx.Response(422, json={"message": "boom"})
        body = json.loads(request.content)
        return httpx.Response(200, json={"data": [{"id": f"e{idx}-{n}"} for n in range(len(body))]})

    def install(self, monkeypatch):
        monkeypatch.setattr(
            email_sender,
            "_http_client",
            lambda settings: httpx.AsyncClient(
                base_url=email_sender.RESEND_API_BASE, transport=httpx.MockTransport(self.handler)
            ),
        )
        return self

    def recipients(self):
        return [[e["to"] for e in json.loads(r.content)] for r in self.requests]


def test_send_one_email_per_recipient_in_batches(monkeypatch):
    fake = _FakeResend().install(monkeypatch)
    sent = asyncio.run(send_hot_news_digest_email([_article(1)], ["a@x", "b@x", "c@x"], _settings()))
    assert sent == 3
    # Mỗi người nhận 1 email riêng — không ai thấy địa chỉ người khác.
    assert fake.recipients() == [[["a@x"], ["b@x"]], [["c@x"]]]
    assert all(r.url.path == "/emails/batch" for r in fake.requests)
    keys = [r.headers["Idempotency-Key"] for r in fake.requests]
    assert len(set(keys)) == 2


def test_send_partial_failure_counts_successful_batches(monkeypatch):
    _FakeResend(fail_batches={0}).install(monkeypatch)
    sent = asyncio.run(send_hot_news_digest_email([_article(1)], ["a@x", "b@x", "c@x"], _settings()))
    assert sent == 1


def test_send_raises_when_every_batch_fails(monkeypatch):
    _FakeResend(fail_batches={0, 1}).install(monkeypatch)
    with pytest.raises(EmailSendError):
        asyncio.run(send_hot_news_digest_email([_article(1)], ["a@x", "b@x", "c@x"], _settings()))


def test_send_raises_when_not_configured():
    with pytest.raises(EmailSendError):
        asyncio.run(send_hot_news_digest_email([_article(1)], ["a@x"], _settings(resend_api_key="")))
