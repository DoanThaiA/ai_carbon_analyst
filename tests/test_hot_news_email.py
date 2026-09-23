"""Test dựng email digest Hot News — services/email_sender.py. Hàm build là
THUẦN (không đụng SMTP/DB); phần gửi được test bằng SMTP giả (monkeypatch)."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

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
    values = {**base.__dict__, "smtp_host": "smtp.test", "smtp_user": "bot@mcv.test",
              "smtp_password": "x", "smtp_from": "bot@mcv.test", "app_base_url": "https://app.test",
              "hot_news_email_bcc_batch_size": 2, **overrides}
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


def _html(message) -> str:
    return message.get_body(preferencelist=("html",)).get_content()


def test_single_article_subject_uses_title():
    msg = build_hot_news_digest_message([_article(1)], _settings())
    assert msg["Subject"] == "[HOT NEWS] EUA tăng mạnh 1"
    html = _html(msg)
    assert "Đảo chiều giá EUA 1" in html
    assert 'href="https://news.test/1"' in html
    assert "08:30 23/09/2026" in html  # hiển thị giờ VN


def test_multiple_articles_subject_uses_count_and_lists_all():
    msg = build_hot_news_digest_message([_article(1), _article(2), _article(3)], _settings())
    assert msg["Subject"] == "[HOT NEWS] 3 tin tức quan trọng vừa được cập nhật"
    html = _html(msg)
    for i in (1, 2, 3):
        assert f"EUA tăng mạnh {i}" in html
    text = msg.get_body(preferencelist=("plain",)).get_content()
    assert "https://news.test/3" in text


def test_untrusted_content_is_escaped_and_bad_urls_neutralised():
    msg = build_hot_news_digest_message(
        [_article(1, title="<script>alert(1)</script>\nX", url="javascript:alert(1)")], _settings()
    )
    html = _html(msg)
    assert "<script>" not in html
    assert "javascript:" not in html
    assert "\n" not in msg["Subject"]


def test_recipients_never_in_headers():
    msg = build_hot_news_digest_message([_article(1)], _settings())
    assert msg["To"] == "bot@mcv.test"
    assert msg["Bcc"] is None


def test_batches():
    assert _batches(["a", "b", "c"], 2) == [["a", "b"], ["c"]]
    assert _batches([], 2) == []


class _FakeSMTP:
    def __init__(self, fail_batches=()):
        self.sent = []
        self.fail_batches = set(fail_batches)

    def __call__(self, **_kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send_message(self, message, sender, recipients):
        idx = len(self.sent)
        self.sent.append(list(recipients))
        if idx in self.fail_batches:
            raise email_sender.aiosmtplib.SMTPException("boom")


def test_send_splits_recipients_into_bcc_batches(monkeypatch):
    fake = _FakeSMTP()
    monkeypatch.setattr(email_sender.aiosmtplib, "SMTP", fake)
    sent = asyncio.run(send_hot_news_digest_email([_article(1)], ["a@x", "b@x", "c@x"], _settings()))
    assert sent == 3
    assert fake.sent == [["a@x", "b@x"], ["c@x"]]


def test_send_partial_failure_counts_successful_batches(monkeypatch):
    monkeypatch.setattr(email_sender.aiosmtplib, "SMTP", _FakeSMTP(fail_batches={0}))
    sent = asyncio.run(send_hot_news_digest_email([_article(1)], ["a@x", "b@x", "c@x"], _settings()))
    assert sent == 1


def test_send_raises_when_every_batch_fails(monkeypatch):
    monkeypatch.setattr(email_sender.aiosmtplib, "SMTP", _FakeSMTP(fail_batches={0, 1}))
    with pytest.raises(EmailSendError):
        asyncio.run(send_hot_news_digest_email([_article(1)], ["a@x", "b@x", "c@x"], _settings()))
