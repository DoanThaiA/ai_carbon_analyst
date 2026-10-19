"""
Xem / tắt bài học từ góp ý người đọc (bảng report_lessons, xem services/report_lessons.py).
Jenny ghi bài học trong chat sau khi đã kiểm chứng lỗi; bài học còn hiệu lực được đưa vào
prompt sinh báo cáo các ngày sau. Tắt bài học ghi sai hoặc không còn đúng bằng --dismiss.

Usage:
    python -m scripts.report_lessons                  # liệt kê bài học đang hiệu lực
    python -m scripts.report_lessons --all            # gồm cả bài học đã tắt
    python -m scripts.report_lessons --dismiss 12     # tắt bài học #12
"""
import argparse
import asyncio
import logging

from sqlalchemy import select

from core.config import Settings
from db.models import ReportLesson
from db.session import build_sessionmaker, create_engine
from services import report_lessons

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all", action="store_true", help="Liệt kê cả bài học đã tắt")
    parser.add_argument("--dismiss", type=int, metavar="ID", help="Tắt bài học theo id")
    args = parser.parse_args()

    settings = Settings.from_env()
    engine = create_engine(settings.database_url)
    session_factory = build_sessionmaker(engine)
    try:
        async with session_factory() as session:
            if args.dismiss is not None:
                ok = await report_lessons.dismiss(session, args.dismiss)
                logger.info("Đã tắt bài học #%d." if ok else "Không có bài học #%d đang hiệu lực.", args.dismiss)
                return
            query = select(ReportLesson).order_by(ReportLesson.created_at.desc())
            if not args.all:
                query = query.where(ReportLesson.status == "active")
            rows = (await session.scalars(query)).all()
            logger.info("%d bài học:", len(rows))
            for r in rows:
                logger.info(
                    "  #%d [%s] báo cáo %s, %s — bởi %s, %s\n      Sai: %s\n      Đúng: %s\n      Bài học: %s",
                    r.id, r.status, r.report_date, report_lessons.SECTION_LABELS.get(r.section, r.section),
                    r.reported_by, r.created_at.strftime("%Y-%m-%d %H:%M"),
                    r.mistake, r.correction, r.lesson,
                )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
