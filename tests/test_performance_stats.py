"""Chỉ số chất lượng trang Thống kê hiệu suất (services/performance_stats.py)."""
from services import performance_stats as ps


def test_chat_quality_counts_and_rates():
    rows = [("2026-10-20", "good", 3), ("2026-10-20", None, 2), ("2026-10-21", "bad", 1), ("2026-10-19", "good", 1)]
    out = ps.summarize_chat_quality(rows, {"2026-10-20": 2}, ["2026-10-20", "2026-10-21"])
    t = out["totals"]
    # Phiên của ngày không có báo cáo phát hành (19/10) vẫn vào tổng.
    assert (t["good"], t["bad"], t["unrated"], t["sessions"], t["examples"]) == (4, 1, 2, 7, 2)
    assert t["good_rate_pct"] == 80.0
    assert t["rated_share_pct"] == round(5 / 7 * 100, 1)
    assert out["items"] == [
        {"report_date": "2026-10-20", "good": 3, "bad": 0, "unrated": 2, "examples": 2},
        {"report_date": "2026-10-21", "good": 0, "bad": 1, "unrated": 0, "examples": 0},
    ]


def test_chat_quality_empty():
    t = ps.summarize_chat_quality([], {}, [])["totals"]
    assert t["sessions"] == 0 and t["good_rate_pct"] is None and t["rated_share_pct"] is None


def _ev(d, verdict, prob="Trung bình", change=1.5, direction="tăng", trade=None):
    return {"report_date": d, "verdict": verdict, "probability": prob, "change_pct": change, "direction": direction,
            "sessions": 10, "graded_by": "direction", "trade": trade, "max_excursion_pct": change,
            "high_confidence_miss": verdict == "wrong" and prob == "Cao", "design_flaws": [], "naive_momentum": "tăng"}


def test_forecast_summary_groups_pending_and_rates():
    out = ps.summarize_forecasts([
        _ev("2026-10-03", "wrong", prob="Cao", change=-2.0, trade={"status": "stopped"}),
        _ev("2026-10-01", "correct", trade={"status": "target_hit"}),
        _ev("2026-10-02", "direction_only", trade={"status": "cancelled_target"}),
        _ev("2026-10-04", "on_track", trade={"status": "not_filled"}),
        _ev("2026-10-05", "unclear"),
    ])
    t = out["totals"]
    assert (t["correct"], t["wrong"], t["direction_only"], t["pending"]) == (1, 1, 1, 2)
    assert t["hit_rate_pct"] == 50.0
    assert t["high_confidence_miss"] == 1
    assert (t["trades"], t["trades_filled"]) == (4, 2)
    assert [i["report_date"] for i in out["items"]] == sorted(i["report_date"] for i in out["items"])
    assert out["items"][3]["verdict"] == "pending" and out["items"][3]["raw_verdict"] == "on_track"
    cal = {c["probability"]: c for c in out["calibration"]}
    assert (cal["Cao"]["correct"], cal["Cao"]["total"]) == (0, 1)
    assert (cal["Trung bình"]["correct"], cal["Trung bình"]["total"]) == (1, 1)
    assert out["baseline"]["model"] == {"correct": 1, "total": 2}


def test_forecast_summary_empty():
    out = ps.summarize_forecasts([])
    assert out["totals"]["reports"] == 0 and out["totals"]["hit_rate_pct"] is None and out["items"] == []


def test_quarter_key():
    assert ps._quarter_key("Q3/2026") == (2026, 3)
    assert ps._quarter_key("bad") is None


def test_cbam_summary_scores_only_closed_quarters():
    est = lambda e, lo, hi, q="Q3/2026": {"quarter": q, "estimate": e, "ci_low": lo, "ci_high": hi, "ci_level": 90}
    out = ps.summarize_cbam(
        [("2026-09-15", est(80.0, 78.0, 82.0)), ("2026-08-01", est(70.0, 60.0, 75.0)),
         ("2026-10-20", est(78.0, 74.0, 82.0, q="Q4/2026"))],
        {"Q3/2026": (82.32, "official"), "Q4/2026": (None, "pending")},
    )
    t = out["totals"]
    assert (t["reports"], t["scored"], t["ci_total"], t["in_ci"]) == (3, 2, 2, 0)
    assert out["items"][0]["report_date"] == "2026-08-01"
    assert out["items"][0]["error_pct"] == round((70 / 82.32 - 1) * 100, 2)
    assert out["items"][2]["error_pct"] is None and out["items"][2]["in_ci"] is None
    assert t["mape_pct"] == round((abs(70 / 82.32 - 1) + abs(80 / 82.32 - 1)) * 100 / 2, 2)
    assert t["bias_pct"] < 0
    q3 = next(q for q in out["quarters"] if q["quarter"] == "Q3/2026")
    assert q3["actual_basis"] == "official" and q3["reports"] == 2
    assert q3["last_error_pct"] == round((80 / 82.32 - 1) * 100, 2)


def test_weekly_vwap_groups_by_iso_week():
    from datetime import date
    w = ps.weekly_vwap([
        (date(2026, 10, 12), 80.0, 1_000_000), (date(2026, 10, 14), 82.0, 3_000_000),  # T2, T4 cùng tuần
        (date(2026, 10, 19), 79.0, 2_000_000), (date(2026, 10, 20), None, 1_000_000),
    ])
    assert w == {"2026-10-12": 81.5, "2026-10-19": 79.0}
    out = ps.summarize_cbam(
        [("2026-10-16", {"quarter": "Q4/2026", "estimate": 80.0, "ci_low": 78.0, "ci_high": 82.0})],
        {"Q4/2026": (None, "pending")}, w,
    )
    assert out["items"][0]["week_start"] == "2026-10-12" and out["items"][0]["week_vwap"] == 81.5
