"""
Sinh lại riêng MỤC 1 — "Tóm tắt điều hành" (`content["1"]`) của 1 báo cáo ngày, KHÔNG đụng các mục khác.

Dựng lại đúng prompt Mục 1 như `report_generator.generate_report_content` (tin Mục 1, bảng giá, biên độ/xu hướng EUA,
spread than–khí, tin báo cáo trước, hồ sơ ETS/MSR, chiều đã chốt của "Diễn biến chính", bài học, tri thức tài liệu,
đoạn chat tham khảo), gọi LLM 1 lần rồi thay `content["1"]` bằng kết quả. Các phần khác giữ nguyên, kể cả
"Điều đáng quan tâm nhất hôm nay" (`content["1"].highlight`) — chọn lại bằng `scripts.backfill_report_highlight --force`
nếu cần. Không chạy lại gợi ý kinh doanh, không ghi bộ nhớ gợi ý, không đổi trạng thái duyệt.

Mặc định CHỈ IN kết quả (dry-run). Thêm `--save` để ghi DB; khi đó bản tiếng Anh của Mục 1 được dịch lại nếu bản EN
đang khớp (không dịch lại cả báo cáo). Lưu ý: báo cáo nháp đổi nội dung sẽ làm lệch hash biên bản QC Claude — nếu đang
trước 08:00 và có biên bản QC, cân nhắc chạy sau khi QC xong.

Usage:
    python -m scripts.regenerate_report_section1                      # hôm nay (giờ VN), chỉ in
    python -m scripts.regenerate_report_section1 2026-10-29           # ngày khác, chỉ in
    python -m scripts.regenerate_report_section1 2026-10-29 --save    # in + ghi DB (+ EN nếu đang khớp)
"""
import argparse
import asyncio
import copy
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from core.config import Settings
from db.models import KNOWLEDGE_SECTIONS, Report
from db.session import build_sessionmaker, create_engine
from services import knowledge, report_lessons
from services.quote_chat_examples import build_report_reference_block
from services.report_consistency import apply_consistency_fixes
from services.report_generator import (
    EXEC_SUMMARY_MAX_BULLETS,
    SECTION_MAX_TOKENS,
    SECTION_PARSE_RETRIES,
    SECTION_TOPICS,
    _call_llm,
    _dev_verdict_block,
    _dossier_lookup,
    _eua_session_range_summary,
    _eua_trend_summary,
    _exec_policy_block,
    _extract_json,
    _filter_news_with_index,
    _fuel_switching_spread_summary,
    _has_cjk,
    _knowledge_retrieval,
    _load_ets_dossiers,
    _prompt_section1,
    _prompt_section1_news_only,
    _resolve_bullet_sources,
    _stale_price_note,
    _summarize_prices,
    annotate_price_revisions,
    get_historical_ohlc_for_report,
    get_news_for_report,
    get_prices_for_report,
    get_previous_report_digest,
    is_no_price_report,
    report_data_date,
)
from services.report_translation import build_translator, content_hash, fresh_content_en, translate_report_content

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

VN_TZ = timezone(timedelta(hours=7))


async def _build_prompt(session, report_date: str, stored: dict) -> tuple[str, str, dict, dict]:
    """Dựng (system, user) của Mục 1 đúng thứ tự như generate_report_content / _process_section."""
    target_date = report_data_date(report_date)
    no_price = is_no_price_report(report_date)

    lessons = await report_lessons.load_active(session, report_date)
    chat_reference_block = await build_report_reference_block(session)
    news_by_topic, _ = await get_news_for_report(session, target_date)
    section1_news_text, section1_lookup = _filter_news_with_index(news_by_topic, "1")

    knowledge_hits = await knowledge.load_report_knowledge(
        session, lambda: _knowledge_retrieval(session), target_date, news_by_topic, SECTION_TOPICS,
        [s for s in KNOWLEDGE_SECTIONS if not (no_price and s == "2")],
    )
    knowledge_block, knowledge_lookup = knowledge.knowledge_prompt_block(knowledge_hits.get("1", []), "1")

    # Chiều tác động đã chốt của "Diễn biến chính" (content["2"].key_developments đã lưu) — Mục 1 phải theo đúng chiều đó.
    dev_impacts = {
        d["source_url"]: d["impact"]
        for d in ((stored.get("2") or {}).get("key_developments") or [])
        if d.get("source_url") and d.get("impact")
    }
    dossier_lookup = _dossier_lookup(_load_ets_dossiers())

    if no_price:
        logger.info("Ngày dữ liệu %s không có phiên giao dịch — dùng prompt chỉ tin tức.", target_date)
        system, user = _prompt_section1_news_only(section1_news_text, target_date)
    else:
        prices, max_price_date = await get_prices_for_report(session, target_date)
        chart_data = await get_historical_ohlc_for_report(session, "EUA", target_date)
        if prices:
            await annotate_price_revisions(session, report_date, prices)  # gắn p["revision"] vào giá (vào prompt)
        prices_text = _summarize_prices(prices)
        stale_note = _stale_price_note(max_price_date, target_date)
        if prices_text and stale_note:
            prices_text = f"{prices_text}\n{stale_note}"
        prev_digest = await get_previous_report_digest(session, report_date)
        system, user = _prompt_section1(
            section1_news_text + prev_digest,
            prices_text,
            _eua_trend_summary(chart_data),
            _eua_session_range_summary(chart_data),
            target_date,
            fuel_spread=_fuel_switching_spread_summary(prices),
        )

    user += _exec_policy_block(dossier_lookup)
    user += _dev_verdict_block(dev_impacts, section1_lookup)
    user += report_lessons.lessons_prompt_block(lessons, "1")
    user += knowledge_block
    system += chat_reference_block
    return system, user, section1_lookup, knowledge_lookup


async def _call_section1(system: str, user: str) -> dict | None:
    """Gọi LLM tới SECTION_PARSE_RETRIES lần; trả về dict Mục 1 hoặc None (không ghi đè bằng kết quả hỏng)."""
    for attempt in range(SECTION_PARSE_RETRIES):
        raw = await _call_llm(user, system=system, max_tokens=SECTION_MAX_TOKENS.get("1", 8192))
        parsed = _extract_json(raw) if raw else None
        if parsed and not _has_cjk(parsed):
            if isinstance(parsed.get("1"), dict):
                return parsed["1"]
            if "bullets" in parsed:  # model bỏ lớp bọc {"1": ...}
                return parsed
        logger.warning("Mục 1 chưa hợp lệ (lần %d/%d) — %s", attempt + 1, SECTION_PARSE_RETRIES,
                       "lẫn ký tự CJK" if parsed and _has_cjk(parsed) else "JSON lỗi hoặc rỗng")
    return None


def _print_section(sec: dict) -> None:
    print("\n=== MỤC 1 — " + str(sec.get("title", "Tóm tắt điều hành")) + " ===")
    for b in sec.get("bullets") or []:
        src = f"  (Nguồn: {b['source_name']})" if b.get("source_name") else ""
        print(f"- {b.get('text', '')}{src}")


async def _main(report_date: str, save: bool) -> None:
    settings = Settings.from_env()
    engine = create_engine(settings.database_url)
    session_factory = build_sessionmaker(engine)
    translator = build_translator(settings)
    try:
        # 1. Đọc báo cáo + dựng prompt (session ngắn, không giữ khi gọi LLM).
        async with session_factory() as session:
            report = (await session.execute(select(Report).where(Report.report_date == report_date))).scalars().first()
            if report is None or not report.content:
                raise SystemExit(f"Báo cáo {report_date} chưa có nội dung — sinh báo cáo trước.")
            if report.status == "generating":
                raise SystemExit(f"Báo cáo {report_date} đang sinh — thử lại sau.")
            stored = report.content
            en_was_fresh = fresh_content_en(report) is not None
            system, user, section1_lookup, knowledge_lookup = await _build_prompt(session, report_date, stored)

        logger.info("Báo cáo %s (trạng thái %s): đang sinh lại Mục 1...", report_date, report.status)

        # 2. Gọi LLM cho riêng Mục 1.
        sec = await _call_section1(system, user)
        if sec is None:
            raise SystemExit("LLM không trả về Mục 1 hợp lệ — không ghi gì. Chạy lại sau.")
        sec_new = {**sec, "bullets": _resolve_bullet_sources(
            sec.get("bullets"), section1_lookup, knowledge_lookup,
        )[:EXEC_SUMMARY_MAX_BULLETS]}

        # 3. Tự sửa số học/trình bày như bộ sinh (trên bản sao — không đụng `stored`).
        candidate = copy.deepcopy(stored)
        candidate["1"] = sec_new
        for fix in apply_consistency_fixes(candidate):
            logger.warning("tự sửa %s — %s", fix["path"], fix["message"])
        sec_new = candidate["1"]

        _print_section(sec_new)
        highlight = (stored.get("1") or {}).get("highlight")

        if not save:
            print("\n(dry-run — chưa ghi DB. Thêm --save để lưu.)")
            return

        # 4. Dịch lại riêng Mục 1 cho bản EN (chỉ khi bản EN đang khớp trước khi sửa).
        content_en = None
        if en_was_fresh and translator is not None:
            try:
                translated = (await translate_report_content({"1": dict(sec_new)}, translator))["1"]
                old_en_highlight = ((report.content_en or {}).get("1") or {}).get("highlight")
                new_en_1 = {**translated, **({"highlight": old_en_highlight} if old_en_highlight else {})}
                content_en = {**report.content_en, "1": new_en_1}
            except Exception as exc:  # noqa: BLE001 — lỗi dịch chỉ làm bản EN lệch, không chặn bản tiếng Việt
                logger.warning("Dịch lại Mục 1 lỗi (%s) — bản tiếng Anh sẽ lệch, chạy scripts.translate_reports --force.", exc)

        # 5. Ghi DB — chỉ khi báo cáo không bị sửa trong lúc gọi LLM.
        async with session_factory() as session:
            report = (await session.execute(select(Report).where(Report.report_date == report_date))).scalars().first()
            if report is None or content_hash(report.content) != content_hash(stored):
                raise SystemExit(f"Báo cáo {report_date} vừa thay đổi trong lúc chạy — không ghi. Chạy lại.")
            new_content = {**stored, "1": {**sec_new, **({"highlight": highlight} if highlight else {})}}
            report.content = new_content
            if content_en is not None:
                report.content_en = content_en
                report.content_en_source_hash = content_hash(new_content)
            await session.commit()
        logger.info("Đã ghi Mục 1 báo cáo %s%s.", report_date,
                    " (kèm bản tiếng Anh)" if content_en is not None else
                    " — bản tiếng Anh chưa khớp, chạy scripts.translate_reports nếu cần" if en_was_fresh else "")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("report_date", nargs="?", help="Ngày báo cáo YYYY-MM-DD (mặc định: hôm nay giờ VN)")
    parser.add_argument("--save", action="store_true", help="Ghi kết quả vào DB (mặc định chỉ in)")
    args = parser.parse_args()
    target = args.report_date or datetime.now(VN_TZ).date().isoformat()
    asyncio.run(_main(target, args.save))
