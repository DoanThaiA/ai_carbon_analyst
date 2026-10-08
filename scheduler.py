"""
scheduler.py
============
Script chạy ngầm 24/7, tự động kích hoạt 3 tác vụ độc lập theo giờ Việt Nam:

  1. daily_prices_job       06:00   — crawl giá các hợp đồng tương lai (BarchartPriceCrawler)
  2. hourly_news_crawl_job  HH:00   — MỖI GIỜ crawl tin tức TOÀN BỘ nguồn is_active=True (bảng
                                      news_crawl_sources — xem api/routers/admin_news_sources.py).
                                      Cờ is_noon_crawl không còn ảnh hưởng lịch tự động.
  3. auto_report_job        07:00   — CHỈ thứ 3 → thứ 7 (cấu hình AUTO_REPORT_DAYS trong .env)
                                      tự động sinh 1 báo cáo/ngày bằng Claude, lưu report_date = HÔM NAY
                                      (VN) — dữ liệu vẫn là giá/tin của hôm qua, xem
                                      services/report_generator.py::report_data_date

Crawl mỗi giờ không nhân chi phí LLM vì mỗi đợt chỉ xử lý URL MỚI: URL đã lưu (`articles`)
và URL đã bị loại (`crawl_seen_urls` — LLM đánh không liên quan, ngoài cửa sổ ngày, ...)
đều được bỏ qua ngay từ listing page (services/storage.py::load_recent_urls). Bài chỉ được
nhận nếu đăng trong CRAWL_LOOKBACK_HOURS giờ gần nhất (cửa sổ trượt, pipeline/crawl_pipeline.py).

Tin tức đưa vào báo cáo được lọc theo crawled_at ở
services/report_generator.py::get_news_for_report (07:00 VN ngày T-1 → 07:00 VN ngày T,
với T = report_date = ngày sinh báo cáo) — đợt crawl 07:00 chạy cùng lúc với auto_report_job
nên bài của đợt đó thuộc báo cáo hôm sau. Job này chỉ tạo báo cáo mới nếu ngày đó CHƯA có report (hoặc report cũ
bị 'failed') — không đụng vào report đã 'draft'/'published' do admin thao tác thủ công,
các API /api/admin/reports/* (generate/publish/edit/delete) vẫn hoạt động độc lập như cũ.

Chạy thủ công để test:
  python scheduler.py --now        # chạy ngay cả 3 job theo thứ tự (không cần đợi giờ)
  python scheduler.py              # chạy nền, chờ đúng lịch mỗi ngày
"""


import asyncio
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from logging.handlers import TimedRotatingFileHandler

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# ─── Logging ─────────────────────────────────────────────────────────────────
# Ghi vào logs/ (volume scheduler_logs trong docker-compose mount /app/logs), xoay vòng
# mỗi nửa đêm, giữ 14 ngày — crawl mỗi giờ nên log tăng nhanh, không để 1 file phình mãi.
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        TimedRotatingFileHandler("logs/scheduler.log", when="midnight", backupCount=14, encoding="utf-8"),
    ],
)
logger = logging.getLogger("scheduler")

# Múi giờ Việt Nam (UTC+7)
TZ_VN = timezone(timedelta(hours=7))


# ─── Job 1: Crawl Prices (06:00) ─────────────────────────────────────────────

def run_crawl_prices() -> dict:
    """Chạy BarchartPriceCrawler. Hàm này đồng bộ (sync) vì crawler tự gọi asyncio.run() bên trong."""
    from crawl_prices.crawl_barchart import BarchartPriceCrawler
    logger.info("━━━ [PRICES] Bắt đầu crawl giá thị trường...")
    try:
        stats = BarchartPriceCrawler().run()
        logger.info(
            "━━━ [PRICES] Hoàn thành. Đã lưu %d giá. Lỗi: %s",
            stats.get("prices_saved", 0),
            stats.get("errors", []),
        )
        return stats
    except Exception:
        logger.exception("━━━ [PRICES] Lỗi không mong đợi khi crawl giá!")
        return {"prices_saved": 0, "errors": ["unexpected_error"]}


async def run_crawl_cbam_price() -> bool:
    """Crawl giá CBAM Certificate (trang EC) và lưu DB — tách riêng khỏi Barchart vì nguồn khác,
    lỗi ở đây KHÔNG được làm hỏng job giá chính (chỉ log)."""
    from core.config import Settings
    from db.session import build_sessionmaker, create_engine
    from services.report_generator import save_cbam_price
    engine = create_engine(Settings.from_env().database_url)
    try:
        saved = await save_cbam_price(build_sessionmaker(engine))
        if not saved:
            logger.warning("━━━ [CBAM] Không lấy được giá CBAM — giữ nguyên giá đã lưu gần nhất")
        return saved
    except Exception:
        logger.exception("━━━ [CBAM] Lỗi khi lưu giá CBAM")
        return False
    finally:
        await engine.dispose()


async def daily_prices_job() -> None:
    """Job lập lịch chạy lúc 06:00 SA (giờ VN) mỗi ngày — chỉ crawl giá."""
    now_vn = datetime.now(TZ_VN)
    logger.info("=" * 60)
    logger.info("🚀 [SCHEDULER] Bắt đầu Daily Prices Job — %s", now_vn.strftime("%Y-%m-%d %H:%M:%S"))
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, run_crawl_prices)
    await run_crawl_cbam_price()
    logger.info("✅ [SCHEDULER] Daily Prices Job hoàn thành — %s", datetime.now(TZ_VN).strftime("%H:%M:%S"))
    logger.info("=" * 60)


# ─── Job 2: Crawl News (mỗi giờ, toàn bộ nguồn) ──────────────────────────────

async def run_crawl_news() -> None:
    """Chạy pipeline crawl news (async) cho toàn bộ nguồn is_active=True. Import tại
    runtime để tránh xung đột asyncio.run()."""
    from main import main as crawl_news_main
    logger.info("━━━ [NEWS] Bắt đầu crawl tin tức (toàn bộ nguồn)...")
    try:
        await crawl_news_main()
        logger.info("━━━ [NEWS] Hoàn thành crawl tin tức (toàn bộ nguồn).")
    except Exception:
        logger.exception("━━━ [NEWS] Lỗi không mong đợi khi crawl tin tức!")


async def hourly_news_crawl_job() -> None:
    """Job lập lịch chạy đầu mỗi giờ (giờ VN) — crawl TOÀN BỘ nguồn."""
    started = datetime.now(TZ_VN)
    logger.info("=" * 60)
    logger.info("🚀 [SCHEDULER] Bắt đầu Hourly News Crawl Job (toàn bộ nguồn) — %s", started.strftime("%Y-%m-%d %H:%M:%S"))
    await run_crawl_news()
    finished = datetime.now(TZ_VN)
    elapsed = (finished - started).total_seconds()
    logger.info("✅ [SCHEDULER] Hourly News Crawl Job hoàn thành — %s (%.0f giây)", finished.strftime("%H:%M:%S"), elapsed)
    if elapsed > 3600:
        # max_instances=1 → APScheduler bỏ qua đợt kế tiếp nếu đợt này chưa xong.
        logger.warning("⚠️ [SCHEDULER] Đợt crawl kéo dài hơn 1 giờ — đợt kế tiếp đã bị bỏ qua.")
    logger.info("=" * 60)


# ─── Job 3: Auto-generate Report (07:00) ─────────────────────────────────────

async def run_auto_report_job() -> None:
    """Tự động sinh 1 báo cáo/ngày, lưu report_date = HÔM NAY (VN) — chạy sau đợt news
    crawl 06:00. Dữ liệu trong báo cáo vẫn là của hôm qua (giá phiên đóng cửa hôm qua,
    tin crawl 07:00 hôm qua → 07:00 hôm nay) — generate_report_content tự lùi 1 ngày
    qua report_data_date(), ở đây chỉ quyết định ngày LƯU.

    Không tạo/ghi đè nếu report ngày đó đã 'generating'/'draft'/'published' (tránh
    đụng vào báo cáo admin đã tạo/duyệt thủ công qua POST /api/admin/reports/generate) —
    chỉ tự tạo mới khi CHƯA có report, hoặc tự retry khi lần tự động trước đó 'failed'.
    """
    # Import tại runtime (giống run_crawl_news) để tránh side-effect lúc module
    # scheduler.py được import mà chưa cần DB, và tránh xung đột asyncio.run().
    from sqlalchemy import select
    from api.deps import async_session_maker
    from api.routers.admin_reports import _run_report_generation_job
    from db.models import Report

    target_date = datetime.now(TZ_VN).strftime("%Y-%m-%d")
    logger.info("=" * 60)
    logger.info("🚀 [SCHEDULER] Bắt đầu Auto Report Job cho ngày %s...", target_date)

    async with async_session_maker() as session:
        stmt = select(Report).where(Report.report_date == target_date)
        result = await session.execute(stmt)
        report = result.scalars().first()

        if report and report.status in ("generating", "draft", "published"):
            logger.info(
                "━━━ [REPORT] Report %s đã tồn tại (status=%s) — bỏ qua tự động sinh.",
                target_date, report.status,
            )
            logger.info("=" * 60)
            return

        if report:  # status == "failed" từ lần tự động trước — retry
            report.status = "generating"
            report.error_message = None
        else:
            report = Report(report_date=target_date, status="generating", content=None)
            session.add(report)
        await session.commit()

    try:
        await _run_report_generation_job(target_date)
        logger.info("✅ [SCHEDULER] Auto Report Job hoàn thành cho ngày %s.", target_date)
    except Exception:
        logger.exception("━━━ [REPORT] Lỗi không mong đợi khi tự động sinh báo cáo %s!", target_date)
    logger.info("=" * 60)


# ─── Main ─────────────────────────────────────────────────────────────────────

async def main() -> None:
    # Nếu gọi với --now → chạy ngay lập tức cả 3 job theo thứ tự (dùng để test)
    run_now = "--now" in sys.argv

    scheduler = AsyncIOScheduler(timezone="Asia/Ho_Chi_Minh")

    scheduler.add_job(
        daily_prices_job,
        trigger=CronTrigger(hour=6, minute=0, timezone="Asia/Ho_Chi_Minh"),
        id="daily_prices",
        name="Daily Prices Crawl",
        replace_existing=True,
        misfire_grace_time=3600,  # Nếu server tạm dừng, vẫn chạy nếu trễ < 1 tiếng
    )
    # 1 job duy nhất cho tin tức: max_instances=1 để 2 đợt không bao giờ chạy chồng nhau
    # (seen_urls/seen_hashes nằm trong RAM từng đợt — chạy chồng sẽ gọi LLM 2 lần cho cùng
    # bài); coalesce=True gộp các lần lỡ (server dừng / đợt trước chạy quá giờ) thành 1.
    scheduler.add_job(
        hourly_news_crawl_job,
        trigger=CronTrigger(minute=0, timezone="Asia/Ho_Chi_Minh"),
        id="hourly_news_crawl",
        name="Hourly News Crawl (toàn bộ nguồn)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
    )
    # Chỉ tự động sinh báo cáo vào các ngày cấu hình ở AUTO_REPORT_DAYS (mặc định
    # thứ 3 → thứ 7, xem core/config.py::Settings.auto_report_days).
    from core.config import Settings
    auto_report_days = Settings.from_env().auto_report_days
    scheduler.add_job(
        run_auto_report_job,
        trigger=CronTrigger(day_of_week=auto_report_days, hour=7, minute=0, timezone="Asia/Ho_Chi_Minh"),
        id="auto_report",
        name="Auto Report Generation",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    scheduler.start()
    logger.info("📅 Scheduler đã khởi động:")
    logger.info("   - Giá:      06:00 SA (giờ VN) mỗi ngày")
    logger.info("   - Tin tức:  đầu mỗi giờ (toàn bộ nguồn)")
    logger.info("   - Báo cáo:  07:00 SA (giờ VN), chỉ các ngày '%s' (AUTO_REPORT_DAYS)", auto_report_days)

    if run_now:
        logger.info("⚡ Chế độ --now: chạy cả 3 job ngay lập tức để test (Prices → News → Report)...")
        await daily_prices_job()
        await hourly_news_crawl_job()
        await run_auto_report_job()
        scheduler.shutdown()
        return

    # In thông tin lần chạy tiếp theo của từng job
    for job_id in ("daily_prices", "hourly_news_crawl", "auto_report"):
        job = scheduler.get_job(job_id)
        if job and job.next_run_time:
            logger.info("⏰ [%s] Lần chạy tiếp theo: %s", job_id, job.next_run_time.strftime("%Y-%m-%d %H:%M:%S %Z"))

    # Giữ process chạy mãi
    try:
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        logger.info("🛑 Scheduler đang dừng...")
        scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
