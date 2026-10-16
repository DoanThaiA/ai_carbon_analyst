"""Test phần THUẦN của bộ nhớ gợi ý kinh doanh — services/biz_memory.py (không đụng DB/LLM)."""
from types import SimpleNamespace

from services import biz_memory

PRICES = [
    {"code": "BRENT", "name": "Brent", "close": 101.5},
    {"code": "EUA", "name": "EUA Dec'26", "close": 79.2},
    {"code": "CBAM", "name": "CBAM", "close": None},
]
CODES = {"BRENT", "EUA"}


def test_normalize_trigger_rule_valid():
    rule = biz_memory.normalize_trigger_rule({"code": "brent", "op": ">", "value": "100"}, CODES)
    assert rule == {"code": "BRENT", "op": ">", "value": 100.0}


def test_normalize_trigger_rule_rejects_bad_input():
    assert biz_memory.normalize_trigger_rule(None, CODES) is None
    assert biz_memory.normalize_trigger_rule({"code": "WTI", "op": ">", "value": 1}, CODES) is None  # mã không có
    assert biz_memory.normalize_trigger_rule({"code": "EUA", "op": "==", "value": 1}, CODES) is None  # op sai
    assert biz_memory.normalize_trigger_rule({"code": "EUA", "op": "<", "value": "abc"}, CODES) is None


def test_check_price_rule_hit_and_miss():
    assert biz_memory.check_price_rule({"code": "BRENT", "op": ">", "value": 100.0}, PRICES)
    assert biz_memory.check_price_rule({"code": "BRENT", "op": ">", "value": 105.0}, PRICES) is None
    assert biz_memory.check_price_rule({"code": "EUA", "op": "<=", "value": 80.0}, PRICES)


def test_check_price_rule_missing_price():
    assert biz_memory.check_price_rule({"code": "CBAM", "op": ">", "value": 1.0}, PRICES) is None
    assert biz_memory.check_price_rule({"code": "TTF", "op": ">", "value": 1.0}, PRICES) is None


def test_reminders_for_content_splits_triggered_and_tracking():
    s1 = SimpleNamespace(id=1, first_report_date="2026-09-18", trigger="Nếu Brent vượt 100", action="Chốt HĐ DO", reason="r1")
    s2 = SimpleNamespace(id=2, first_report_date="2026-09-20", trigger="Khi EU công bố ...", action="Cập nhật mô hình", reason="r2")
    triggered = {1: {"evidence": "Brent 101,5", "source_name": "Barchart", "source_url": None}}
    reminders, tracking = biz_memory.reminders_for_content([s1, s2], triggered)
    assert [r["suggested_date"] for r in reminders] == ["2026-09-18"]
    assert reminders[0]["evidence"] == "Brent 101,5"
    assert [t["action"] for t in tracking] == ["Cập nhật mô hình"]
    # chưa kích hoạt ≠ đề xuất sai: hiển thị kèm hạn theo dõi
    assert tracking[0]["expires_on"] == "2026-09-30"


def test_remove_from_biz_content():
    biz = {
        "title": "Gợi ý",
        "short_term": [{"id": 5, "trigger": "a"}, {"id": 6, "trigger": "b"}],
        "reminders": [{"id": 1}],
        "tracking": [{"id": 2}],
        "long_term": [{"opportunity": "x"}],
    }
    new_biz, found = biz_memory.remove_from_biz_content(biz, 6)
    assert found
    assert [it["id"] for it in new_biz["short_term"]] == [5]
    assert new_biz["long_term"] == biz["long_term"]
    assert new_biz is not biz  # object mới để SQLAlchemy nhận ra JSONB đã đổi

    _, found = biz_memory.remove_from_biz_content(biz, 99)
    assert not found
    assert biz_memory.remove_from_biz_content(None, 1) == (None, False)


def test_remove_from_biz_content_long_term():
    biz = {"short_term": [], "long_term": [{"id": 7, "opportunity": "x"}, {"id": 8, "opportunity": "y"}]}
    new_biz, found = biz_memory.remove_from_biz_content(biz, 7)
    assert found
    assert [it["id"] for it in new_biz["long_term"]] == [8]


def test_describe_for_prompt_labels_long_term():
    s = SimpleNamespace(id=3, kind="long", first_report_date="2026-09-18", trigger="CBAM mở rộng", action="Tư vấn kiểm kê")
    assert "Cơ hội: CBAM mở rộng" in biz_memory.describe_for_prompt([s])


def test_qtd_avg_rule():
    """Trigger liên quan chi phí CBAM so với EUA TB lũy kế quý, không với giá đóng cửa 1 phiên."""
    prices = [{"code": "EUA", "name": "EUA", "close": 85.4, "qtd_avg": 81.9}]
    rule = biz_memory.normalize_trigger_rule({"code": "EUA", "basis": "qtd_avg", "op": ">", "value": 82.32}, {"EUA"})
    assert rule == {"code": "EUA", "op": ">", "value": 82.32, "basis": "qtd_avg"}
    assert biz_memory.check_price_rule(rule, prices) is None  # TB quý 81,9 chưa vượt dù close 85,4 đã vượt
    assert "trung bình lũy kế quý" in biz_memory.check_price_rule({**rule, "op": "<"}, prices)
    inferred = biz_memory.infer_price_rule("Nếu giá EUA trung bình lũy kế quý IV vượt 82,32 EUR/t", prices)
    assert inferred == {"code": "EUA", "op": ">", "value": 82.32, "basis": "qtd_avg"}
    assert "basis" not in biz_memory.infer_price_rule("Nếu EUA đóng cửa trên 88,82 EUR/t", prices)


def test_threshold_parsing_regressions():
    """Lỗi thật: 'Gasoil vượt mốc 1.500' bị đọc 1,5 (→ ĐÃ KÍCH HOẠT sai) hoặc không nhận là ngưỡng giá
    (→ LLM đọc tin rồi tự kết luận đã xảy ra)."""
    prices = [{"code": "GASOIL", "name": "Gasoil", "close": 1386.75}, {"code": "EUA", "name": "EUA", "close": 85.84}]
    for trigger, expected in [
        ("Nếu giá Gasoil vượt mốc 1.500 USD/t", {"code": "GASOIL", "op": ">", "value": 1500.0}),
        ("Nếu EUA vượt kháng cự 88,82 EUR/t", {"code": "EUA", "op": ">", "value": 88.82}),
        ("Nếu EUA phá vỡ hỗ trợ 82,35", {"code": "EUA", "op": "<", "value": 82.35}),
        ("Nếu EUA giảm về 82,35", {"code": "EUA", "op": "<", "value": 82.35}),
    ]:
        assert biz_memory.looks_like_price_condition(trigger)
        rule = biz_memory.resolve_price_rule(None, trigger, prices)
        assert rule == expected, trigger
        assert biz_memory.check_price_rule(rule, prices) is None  # chưa kích hoạt
    # rule LLM lưu sai (1,5 / sai chiều) → câu chữ thắng
    assert biz_memory.resolve_price_rule({"code": "GASOIL", "op": ">", "value": 1.5}, "Nếu Gasoil vượt mốc 1.500 USD/t", prices)["value"] == 1500.0
    assert biz_memory.resolve_price_rule({"code": "EUA", "op": "<", "value": 88.82}, "Nếu EUA vượt 88,82", prices)["op"] == ">"
    # rule sai bậc độ lớn, không dựng lại được từ câu chữ → bỏ, không kích hoạt
    assert biz_memory.resolve_price_rule({"code": "GASOIL", "op": ">", "value": 1.5}, "Khi giá diesel leo thang", prices) is None
    assert not biz_memory.looks_like_price_condition("Khi EU công bố cắt giảm phân bổ miễn phí cho ngành thép 2027")
    assert not biz_memory.looks_like_price_condition("Nếu Brent tăng 10% trong tuần")
