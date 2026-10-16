"""
Dịch bù bản tiếng Anh cho báo cáo ngày (bình thường tự chạy sau khi sinh/sửa/duyệt báo cáo —
xem services/report_translation.py). Dùng khi LibreTranslate tạm lỗi lúc sinh, hoặc để dịch
các báo cáo có từ trước khi có tính năng này.

Usage:
    python -m scripts.translate_reports 2026-10-16            # 1 ngày
    python -m scripts.translate_reports --missing              # mọi báo cáo chưa có bản dịch khớp content
    python -m scripts.translate_reports 2026-10-16 --force     # dịch lại dù bản dịch đang khớp
    python -m scripts.translate_reports --sample "**Tổng hợp:** [TÍCH CỰC] Giá EUA tăng"  # thử máy dịch, không ghi DB
"""
import argparse
import asyncio
import logging

from sqlalchemy import select

from core.config import Settings
from db.models import Report
from db.session import build_sessionmaker, create_engine
from services.report_translation import (
    build_translator,
    ensure_report_translation,
    fresh_content_en,
    translate_report_content,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def _main(args: argparse.Namespace) -> None:
    settings = Settings.from_env()
    translator = build_translator(settings)
    if translator is None:
        raise SystemExit("LIBRETRANSLATE_URL chưa được cấu hình.")

    if args.sample:
        print((await translate_report_content({"text": args.sample}, translator))["text"])
        return

    engine = create_engine(settings.database_url)
    session_factory = build_sessionmaker(engine)
    try:
        dates = list(args.dates)
        if args.missing:
            async with session_factory() as session:
                reports = (
                    await session.execute(
                        select(Report).where(Report.content.is_not(None)).order_by(Report.report_date)
                    )
                ).scalars().all()
                dates += [r.report_date for r in reports if fresh_content_en(r) is None]
        if not dates:
            logger.info("Không có báo cáo nào cần dịch.")
        ok = 0
        for d in dict.fromkeys(dates):
            if await ensure_report_translation(session_factory, settings, d, force=args.force):
                ok += 1
            else:
                logger.warning("Báo cáo %s: chưa dịch được (xem log phía trên).", d)
        logger.info("Xong: %d/%d báo cáo có bản tiếng Anh.", ok, len(dict.fromkeys(dates)))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dates", nargs="*", help="Ngày báo cáo YYYY-MM-DD")
    parser.add_argument("--missing", action="store_true", help="Dịch mọi báo cáo chưa có bản dịch khớp content")
    parser.add_argument("--force", action="store_true", help="Dịch lại dù bản dịch đang khớp")
    parser.add_argument("--sample", help="Dịch thử 1 đoạn văn, không đụng DB")
    asyncio.run(_main(parser.parse_args()))
