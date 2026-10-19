"""Bài học từ góp ý người đọc (services/report_lessons.py) → prompt sinh báo cáo."""
from db.models import ReportLesson
from services import report_lessons


def _lesson(section: str, lesson: str) -> ReportLesson:
    return ReportLesson(
        report_date="2026-10-17", section=section, mistake="sai X", correction="đúng Y", lesson=lesson,
        reported_by="a@b.c", status="active",
    )


def test_block_only_includes_matching_and_general_sections():
    lessons = [_lesson("3", "bài học mục 3"), _lesson("general", "bài học chung"), _lesson("1", "bài học mục 1")]
    block = report_lessons.lessons_prompt_block(lessons, "3")
    assert "bài học mục 3" in block and "bài học chung" in block
    assert "bài học mục 1" not in block


def test_block_empty_when_no_relevant_lessons():
    assert report_lessons.lessons_prompt_block([_lesson("1", "x")], "biz") == ""
    assert report_lessons.lessons_prompt_block([], "1") == ""


def test_lesson_tool_is_web_chat_only():
    """Tool GHI — không được lọt sang MCP gateway (chỉ phục vụ CLIENT_TOOLS)."""
    from services.quote_chat import CLIENT_TOOLS, LESSON_TOOL

    assert LESSON_TOOL["name"] not in {t["name"] for t in CLIENT_TOOLS}
    assert set(LESSON_TOOL["input_schema"]["properties"]["section"]["enum"]) == set(report_lessons.SECTION_LABELS)


def test_section_keys_match_db_check_constraint():
    constraint = next(c for c in ReportLesson.__table__.constraints if c.name == "ck_report_lessons_section")
    for key in report_lessons.SECTION_LABELS:
        assert f"'{key}'" in str(constraint.sqltext)


def test_chat_examples_formatted_old_to_new():
    from db.models import QuoteChatExample
    from services.quote_chat_examples import _format_examples

    newest_first = [QuoteChatExample(question="q2", answer="a2"), QuoteChatExample(question="q1", answer="a1")]
    parts = _format_examples(newest_first)
    assert parts[0].startswith("--- Ví dụ 1 ---\nCâu hỏi: q1") and "a2" in parts[1]
