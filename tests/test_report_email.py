"""Test email PDF báo cáo ngày — services/email_sender.py (Resend giả bằng httpx.MockTransport)."""
import asyncio
import base64
import json

import httpx
import pytest

from core.config import Settings
from services import email_sender
from services.email_sender import EmailSendError, build_report_email_message, send_report_pdf_email


def _settings(**overrides) -> Settings:
    base = Settings.from_env()
    values = {**base.__dict__, "resend_api_key": "re_test", "email_from": "bot@mcv.test",
              "app_base_url": "https://app.test", "email_reply_to": "", **overrides}
    return Settings(**values)


class _FakeResend:
    def __init__(self, fail_to=()):
        self.requests = []
        self.fail_to = set(fail_to)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if json.loads(request.content)["to"][0] in self.fail_to:
            return httpx.Response(422, json={"message": "boom"})
        return httpx.Response(200, json={"id": f"id{len(self.requests)}"})

    def install(self, monkeypatch):
        monkeypatch.setattr(email_sender, "_REPORT_EMAIL_PAUSE_SECONDS", 0)
        monkeypatch.setattr(
            email_sender, "_http_client",
            lambda s: httpx.AsyncClient(base_url=email_sender.RESEND_API_BASE,
                                        transport=httpx.MockTransport(self.handler)),
        )
        return self


def test_message_subject_and_link():
    subject, text, html = build_report_email_message("2026-10-09", _settings())
    assert subject == "Báo cáo Carbon Analyst ngày 09/10/2026"
    assert "https://app.test/reports/2026-10-09" in text and "https://app.test/reports/2026-10-09" in html


def test_sends_one_email_each_with_pdf_attachment(monkeypatch):
    fake = _FakeResend().install(monkeypatch)
    sent = asyncio.run(send_report_pdf_email("2026-10-09", b"%PDF-x", ["a@x", "b@x"], _settings()))
    assert sent == ["a@x", "b@x"]
    bodies = [json.loads(r.content) for r in fake.requests]
    assert [b["to"] for b in bodies] == [["a@x"], ["b@x"]]
    att = bodies[0]["attachments"][0]
    assert att["filename"] == "carbon-analyst-report-2026-10-09.pdf"
    assert base64.b64decode(att["content"]) == b"%PDF-x"
    assert all(r.url.path == "/emails" for r in fake.requests)
    assert len({r.headers["Idempotency-Key"] for r in fake.requests}) == 2


def test_one_failure_does_not_stop_others(monkeypatch):
    _FakeResend(fail_to={"a@x"}).install(monkeypatch)
    assert asyncio.run(send_report_pdf_email("2026-10-09", b"p", ["a@x", "b@x"], _settings())) == ["b@x"]


def test_raises_when_nobody_sent(monkeypatch):
    _FakeResend(fail_to={"a@x"}).install(monkeypatch)
    with pytest.raises(EmailSendError):
        asyncio.run(send_report_pdf_email("2026-10-09", b"p", ["a@x"], _settings()))
