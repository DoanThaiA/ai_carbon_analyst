"""
scheduler.py
============
Script chạy ngầm 24/7, tự động kích hoạt crawl vào 6:00 SA (giờ Việt Nam) mỗi ngày.

Thứ tự thực thi:
  1. crawl_prices  (BarchartPriceCrawler)  — crawl giá các hợp đồng tương lai
  2. crawl_news    (main() trong main.py)  — crawl tin tức từ 34 nguồn

Chạy thủ công để test:
  python scheduler.py --now        # chạy ngay lập tức (không cần đợi 6:00)
  python scheduler.py              # chờ đến 6:00 SA mỗi ngày
"""

import asyncio
import logging
import sys
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# ─── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("scheduler.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("scheduler")

# Múi giờ Việt Nam (UTC+7)
TZ_VN = timezone(timedelta(hours=7))


# ─── Task: Crawl Prices ──────────────────────────────────────────────────────

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


# ─── Task: Crawl News ────────────────────────────────────────────────────────

async def run_crawl_news() -> None:
    """Chạy pipeline crawl news (async). Import tại runtime để tránh xung đột asyncio.run()."""
    from main import main as crawl_news_main
    logger.info("━━━ [NEWS] Bắt đầu crawl tin tức từ các nguồn...")
    try:
        await crawl_news_main()
        logger.info("━━━ [NEWS] Hoàn thành crawl tin tức.")
    except Exception:
        logger.exception("━━━ [NEWS] Lỗi không mong đợi khi crawl tin tức!")


# ─── Job chính: chạy nối tiếp prices → news ─────────────────────────────────

async def daily_crawl_job() -> None:
    """
    Job lập lịch chạy lúc 6:00 SA (giờ VN) mỗi ngày.
    Thứ tự: crawl_prices → crawl_news (nối tiếp, không song song).
    """
    now_vn = datetime.now(TZ_VN)
    logger.info("=" * 60)
    logger.info("🚀 [SCHEDULER] Bắt đầu Daily Crawl Job — %s", now_vn.strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("=" * 60)

    # Bước 1: Crawl giá (sync — chạy trong thread pool để không block event loop)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, run_crawl_prices)

    # Bước 2: Crawl tin tức (async — chạy trực tiếp)
    await run_crawl_news()

    logger.info("=" * 60)
    logger.info("✅ [SCHEDULER] Daily Crawl Job hoàn thành — %s", datetime.now(TZ_VN).strftime("%H:%M:%S"))
    logger.info("=" * 60)


# ─── Main ─────────────────────────────────────────────────────────────────────

async def main() -> None:
    # Nếu gọi với --now → chạy ngay lập tức (dùng để test)
    run_now = "--now" in sys.argv

    scheduler = AsyncIOScheduler(timezone="Asia/Ho_Chi_Minh")

    # Lập lịch: mỗi ngày lúc 6:00 SA giờ Việt Nam
    scheduler.add_job(
        daily_crawl_job,
        trigger=CronTrigger(hour=6, minute=0, timezone="Asia/Ho_Chi_Minh"),
        id="daily_crawl",
        name="Daily Crawl: Prices → News",
        replace_existing=True,
        misfire_grace_time=3600,  # Nếu server tạm dừng, vẫn chạy nếu trễ < 1 tiếng
    )

    scheduler.start()
    logger.info("📅 Scheduler đã khởi động. Job sẽ chạy lúc 06:00 SA (giờ VN) mỗi ngày.")

    if run_now:
        logger.info("⚡ Chế độ --now: chạy Job ngay lập tức để test...")
        await daily_crawl_job()
        scheduler.shutdown()
        return

    # In thông tin lần chạy tiếp theo
    job = scheduler.get_job("daily_crawl")
    if job and job.next_run_time:
        logger.info("⏰ Lần chạy tiếp theo: %s", job.next_run_time.strftime("%Y-%m-%d %H:%M:%S %Z"))

    # Giữ process chạy mãi
    try:
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        logger.info("🛑 Scheduler đang dừng...")
        scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
