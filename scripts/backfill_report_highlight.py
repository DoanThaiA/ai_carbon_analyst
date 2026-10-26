"""
Bổ sung khối "Điều đáng quan tâm nhất hôm nay" (`content["1"].highlight`) cho báo cáo ĐÃ SINH trước khi có tính năng này
(hoặc chọn lại bằng `--force` theo tiêu chí mới) — báo cáo mới tự có (chọn ở cuối generate_report_content, xem
report_generator.HIGHLIGHT_RULE). Mỗi báo cáo 1 lượt gọi Claude ngắn, chỉ dựa trên nội dung báo cáo đã có (bảng giá, Tóm tắt,
Diễn biến chính, kịch bản, phòng hộ CBAM, Mục 4, gợi ý kinh doanh) + tin nóng trong khung tin; KHÔNG đụng phần còn lại của
báo cáo, không đổi trạng thái duyệt.

Bản tiếng Anh: nếu đang khớp bản tiếng Việt thì chỉ dịch thêm câu mới rồi cập nhật hash (không dịch lại cả báo cáo); đang
lệch sẵn thì để nguyên — chạy `python -m scripts.translate_reports --missing` nếu cần.

Mặc định CHỈ báo cáo published: đổi `content` của bản nháp làm lệch `assignment_id`/hash của biên bản QC Claude → job
08:00 bỏ qua bước Jenny tự sửa theo QC (xem report_qc_review.auto_fix_skip_reason). Muốn chạy cả nháp thì `--include-drafts`
(tránh khung 07:00–08:15).

Usage:
    python -m scripts.backfill_report_highlight 2026-10-22            # 1 ngày
    python -m scripts.backfill_report_highlight --last 5              # 5 báo cáo published gần nhất
    python -m scripts.backfill_report_highlight --from 2026-10-01 --to 2026-10-22
    python -m scripts.backfill_report_highlight --last 5 --dry-run    # in câu sinh ra, không ghi DB
    python -m scripts.backfill_report_highlight 2026-10-22 --force    # sinh lại dù đã có highlight
"""
import argparse
import asyncio
import logging

from sqlalchemy import select

from core.config import Settings
from db.models import Report
from db.session import build_sessionmaker, create_engine
from services.report_generator import (
    _hot_news_for_highlight, generate_highlight_for_content, get_news_for_report, report_data_date,
)
from services.report_translation import build_translator, content_hash, fresh_content_en, translate_report_content

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def _backfill_one(session_factory, translator, report_date: str, args: argparse.Namespace) -> bool:
    async with session_factory() as session:
        report = (await session.execute(select(Report).where(Report.report_date == report_date))).scalars().first()
        if report is None or not report.content:
            logger.warning("Báo cáo %s: không có nội dung — bỏ qua.", report_date)
            return False
        allowed = {"published", "draft"} if args.include_drafts else {"published"}
        if report.status not in allowed:
            logger.warning("Báo cáo %s: trạng thái %s — bỏ qua (nháp cần --include-drafts).", report_date, report.status)
            return False
        if (report.content.get("1") or {}).get("highlight") and not args.force:
            logger.info("Báo cáo %s: đã có highlight — bỏ qua (dùng --force để sinh lại).", report_date)
            return True
        content = report.content
        en_fresh = fresh_content_en(report) is not None
        news_by_topic, _ = await get_news_for_report(session, report_data_date(report_date))

    highlight = await generate_highlight_for_content(content, report_date, _hot_news_for_highlight(news_by_topic))
    if not highlight:
        logger.warning("Báo cáo %s: không sinh được highlight hợp lệ (LLM lỗi hoặc không chọn được).", report_date)
        return False
    logger.info("Báo cáo %s [%s]: %s%s", report_date, highlight["type"], highlight["text"],
                f" (Nguồn: {highlight['source_name']})" if highlight.get("source_name") else "")
    if args.dry_run:
        return True

    highlight_en = None
    if en_fresh and translator is not None:
        try:
            highlight_en = (await translate_report_content({"1": {"highlight": highlight}}, translator))["1"]["highlight"]
        except Exception as exc:  # noqa: BLE001 — lỗi dịch chỉ làm bản EN lệch, không chặn bản tiếng Việt
            logger.warning("Báo cáo %s: dịch câu highlight lỗi (%s) — bản tiếng Anh sẽ lệch.", report_date, exc)

    async with session_factory() as session:
        report = (await session.execute(select(Report).where(Report.report_date == report_date))).scalars().first()
        # Báo cáo bị sửa trong lúc gọi LLM → không ghi đè lên bản mới.
        if report is None or content_hash(report.content) != content_hash(content):
            logger.warning("Báo cáo %s: nội dung vừa thay đổi trong lúc chạy — bỏ qua, chạy lại sau.", report_date)
            return False
        new_content = {**report.content, "1": {**(report.content.get("1") or {}), "highlight": highlight}}
        report.content = new_content
        if highlight_en is not None and report.content_en:
            report.content_en = {**report.content_en, "1": {**(report.content_en.get("1") or {}), "highlight": highlight_en}}
            report.content_en_source_hash = content_hash(new_content)
        await session.commit()
    logger.info("Báo cáo %s: đã lưu%s.", report_date,
                " (kèm bản tiếng Anh)" if highlight_en is not None else
                " — bản tiếng Anh chưa có câu này, chạy scripts.translate_reports nếu cần" if en_fresh else "")
    return True


async def _main(args: argparse.Namespace) -> None:
    settings = Settings.from_env()
    engine = create_engine(settings.database_url)
    session_factory = build_sessionmaker(engine)
    translator = build_translator(settings)
    try:
        dates = list(args.dates)
        if args.date_from or args.date_to or args.last:
            statuses = ["published", "draft"] if args.include_drafts else ["published"]
            stmt = select(Report.report_date).where(Report.content.is_not(None), Report.status.in_(statuses))
            if args.date_from:
                stmt = stmt.where(Report.report_date >= args.date_from)
            if args.date_to:
                stmt = stmt.where(Report.report_date <= args.date_to)
            stmt = stmt.order_by(Report.report_date.desc())
            if args.last:
                stmt = stmt.limit(args.last)
            async with session_factory() as session:
                dates += sorted((await session.execute(stmt)).scalars().all())
        dates = list(dict.fromkeys(dates))
        if not dates:
            raise SystemExit("Chưa chọn báo cáo nào: truyền ngày, --last N hoặc --from/--to.")
        ok = 0
        for d in dates:
            ok += await _backfill_one(session_factory, translator, d, args)
        logger.info("Xong: %d/%d báo cáo có highlight%s.", ok, len(dates), " (dry-run, chưa ghi DB)" if args.dry_run else "")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dates", nargs="*", help="Ngày báo cáo YYYY-MM-DD")
    parser.add_argument("--from", dest="date_from", help="Từ ngày YYYY-MM-DD (gồm cả ngày này)")
    parser.add_argument("--to", dest="date_to", help="Đến ngày YYYY-MM-DD (gồm cả ngày này)")
    parser.add_argument("--last", type=int, help="Chỉ N báo cáo gần nhất")
    parser.add_argument("--force", action="store_true", help="Sinh lại dù báo cáo đã có highlight")
    parser.add_argument("--dry-run", action="store_true", help="Chỉ in câu sinh ra, không ghi DB")
    parser.add_argument("--include-drafts", action="store_true",
                        help="Chạy cả bản nháp (làm lệch hash biên bản QC — tránh khung 07:00–08:15)")
    asyncio.run(_main(parser.parse_args()))
