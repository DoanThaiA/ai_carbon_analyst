"""Logic thuần của kết nối Claude Desktop (services/claude_connect.py) — không
cần DB: định dạng/băm token, dựng prompt + hướng dẫn handoff, rate limiter."""
from datetime import datetime, timezone
from types import SimpleNamespace

from services.claude_connect import (
    TOKEN_PREFIX,
    RateLimiter,
    build_handoff_instructions,
    build_handoff_prompt,
    generate_handoff_id,
    generate_raw_token,
    hash_token,
)


def _handoff(**overrides):
    base = dict(
        handoff_id="abc123",
        report_date="2026-10-02",
        quote="EUA tăng 2% do gas tăng",
        question="Lập bảng Excel kịch bản giá EUA",
        task_type="excel",
        expires_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_token_format_and_hash():
    raw = generate_raw_token()
    assert raw.startswith(TOKEN_PREFIX)
    assert generate_raw_token() != raw
    assert hash_token(raw) == hash_token(raw)
    assert hash_token(raw) != hash_token(raw + "x")
    assert raw not in hash_token(raw)  # chỉ lưu hash, không chứa token gốc


def test_handoff_id_short_and_unique():
    ids = {generate_handoff_id() for _ in range(200)}
    assert len(ids) == 200
    assert all(len(i) == 12 for i in ids)


def test_prompt_contains_handoff_id():
    prompt = build_handoff_prompt("abc123")
    assert "get_handoff" in prompt and "abc123" in prompt


def test_instructions_include_quote_question_and_task():
    text = build_handoff_instructions(_handoff())
    assert "EUA tăng 2% do gas tăng" in text
    assert "Lập bảng Excel kịch bản giá EUA" in text
    assert "2026-10-02" in text
    assert "sinh file Excel" in text
    assert "không phải chỉ dẫn" in text  # cảnh báo prompt injection từ dữ liệu tool


def test_instructions_without_quote_omit_quote_block():
    text = build_handoff_instructions(_handoff(quote="  ", task_type="other"))
    assert "ĐOẠN TRÍCH" not in text
    assert "YÊU CẦU" in text


def test_rate_limiter_blocks_over_limit_per_key():
    limiter = RateLimiter(max_calls=3, window_seconds=60)
    assert [limiter.allow(1) for _ in range(4)] == [True, True, True, False]
    assert limiter.allow(2) is True  # key khác không bị ảnh hưởng


def test_rate_limiter_window_expires(monkeypatch):
    import services.claude_connect as mod

    now = [1000.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: now[0])
    limiter = RateLimiter(max_calls=1, window_seconds=10)
    assert limiter.allow(1) is True
    assert limiter.allow(1) is False
    now[0] += 11
    assert limiter.allow(1) is True


def test_adapt_tools_for_desktop_rewrites_search_news_and_keeps_original():
    from services.claude_connect import adapt_tools_for_desktop
    from services.quote_chat import CLIENT_TOOLS

    original_search = next(t["description"] for t in CLIENT_TOOLS if t["name"] == "search_news")
    adapted = adapt_tools_for_desktop(CLIENT_TOOLS)

    assert [t["name"] for t in adapted] == [t["name"] for t in CLIENT_TOOLS]
    search = next(t for t in adapted if t["name"] == "search_news")
    assert "DỮ LIỆU NỀN" not in search["description"].split("[Ngữ cảnh Claude Desktop")[0]
    assert "Claude Desktop" in search["description"]
    # Mọi tool đều kèm ghi chú ngữ cảnh; input_schema giữ nguyên.
    assert all("Ngữ cảnh Claude Desktop" in t["description"] for t in adapted)
    assert all(a["input_schema"] == o["input_schema"] for a, o in zip(adapted, CLIENT_TOOLS))
    # Danh sách gốc (dùng cho Jenny) KHÔNG bị sửa.
    assert next(t["description"] for t in CLIENT_TOOLS if t["name"] == "search_news") == original_search
    assert "Claude Desktop" not in original_search
