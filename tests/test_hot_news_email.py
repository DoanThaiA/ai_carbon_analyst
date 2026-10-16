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
              "app_base_url": "https://app.test", "hot_news_email_batch_size": 2,
              "email_reply_to": "", **overrides}
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
    assert msg.subject == "Tin nổi bật: EUA tăng mạnh 1"
    html = msg.html
    assert "Đảo chiều giá EUA 1" in html
    assert 'href="https://news.test/1"' in html
    assert "08:30 23/09/2026" in html  # hiển thị giờ VN


def test_multiple_articles_subject_uses_count_and_lists_all():
    msg = build_hot_news_digest_message([_article(1), _article(2), _article(3)], _settings())
    assert msg.subject == "Carbon Analyst: 3 tin nổi bật mới"
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


def test_reply_to_and_list_unsubscribe_only_when_configured(monkeypatch):
    fake = _FakeResend().install(monkeypatch)
    asyncio.run(send_hot_news_digest_email([_article(1)], ["a@x"], _settings()))
    email = json.loads(fake.requests[0].content)[0]
    assert "reply_to" not in email and "headers" not in email

    fake = _FakeResend().install(monkeypatch)
    asyncio.run(send_hot_news_digest_email([_article(1)], ["a@x"], _settings(email_reply_to="desk@mcv.test")))
    email = json.loads(fake.requests[0].content)[0]
    assert email["reply_to"] == "desk@mcv.test"
    assert email["headers"]["List-Unsubscribe"] == "<mailto:desk@mcv.test?subject=unsubscribe>"


# ── Tiêu chí tin nóng: 4 nhóm + chống gửi lặp ────────────────────────


def test_classifier_hot_news_requires_valid_category():
    from crawl_news.classification import _parse_json_response

    base = '{"topics": ["energy_gas"], "confidence": 0.9, "is_relevant": true, "is_hot_news": true, '
    r = _parse_json_response(base + '"hot_news_category": "supply_cut", "hot_news_reason": "Nauy dừng đường ống Langeled"}')
    assert r.is_hot_news and r.hot_news_reason == "[Cắt nguồn cung] Nauy dừng đường ống Langeled"
    # thiếu nhóm / nhóm cũ không còn hợp lệ (vd "địa chính trị tiềm ẩn") → không phải tin nóng
    for extra in ('"hot_news_reason": "x"}', '"hot_news_category": "geopolitical_risk", "hot_news_reason": "x"}'):
        r = _parse_json_response(base + extra)
        assert not r.is_hot_news and r.hot_news_reason is None
    r = _parse_json_response(base + '"hot_news_category": " Unprecedented_Policy ", "hot_news_reason": "EU áp trần giá EUA"}')
    assert r.is_hot_news and r.hot_news_reason.startswith("[Chính sách chưa từng có]")


def test_hot_news_prompt_lists_exactly_four_groups():
    from crawl_news.classification import HOT_NEWS_CATEGORIES, _JSON_INSTRUCTION, _SYSTEM_PROMPT

    assert set(HOT_NEWS_CATEGORIES) == {"political_shock", "supply_cut", "price_reversal", "unprecedented_policy"}
    for code in HOT_NEWS_CATEGORIES:
        assert code in _SYSTEM_PROMPT and code in _JSON_INSTRUCTION
    assert "MẶC ĐỊNH is_hot_news=false" in _SYSTEM_PROMPT


def test_split_duplicate_events_keeps_first_report_of_each_event():
    from services.hot_news_email import split_duplicate_events

    arts = [
        SimpleNamespace(id=1, title="Norway halts Langeled gas pipeline after outage"),
        SimpleNamespace(id=2, title="Langeled gas pipeline halted after outage, Norway says"),
        SimpleNamespace(id=3, title="EU proposes cap on carbon prices"),
        SimpleNamespace(id=4, title="OPEC+ agrees surprise 2 million bpd output cut"),
    ]
    sent = ["OPEC+ agrees surprise 2 million bpd output cut in Vienna"]
    keep, dup = split_duplicate_events(arts, sent)
    assert [a.id for a in keep] == [1, 3]
    assert [a.id for a in dup] == [2, 4]
