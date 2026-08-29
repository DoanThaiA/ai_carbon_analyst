"""
scheduler.py
============
Script chạy ngầm 24/7, tự động kích hoạt 4 tác vụ độc lập theo giờ Việt Nam:

  1. daily_prices_job       06:00  — crawl giá các hợp đồng tương lai (BarchartPriceCrawler)
  2. morning_news_crawl_job 06:00  — crawl tin tức TOÀN BỘ nguồn trong sources.yaml
  3. noon_news_crawl_job    12:00  — crawl lại CHỈ nhóm Tier A (dữ liệu sàn/cơ quan chính
                                     thức: EIA, IETA, OPEC, Nasdaq, EU Commission, ESMA,
                                     ICE, EEX — xem NOON_TIER_A_DOMAINS)
  4. auto_report_job        07:00  — tự động sinh 1 báo cáo/ngày cho ngày hôm qua (VN) bằng Claude

Cửa sổ lọc bài báo theo published_at (pipeline/crawl_pipeline.py):
  Cả đợt crawl 06:00 lẫn 12:00 đều lọc bài theo cùng khung cố định:
    [06:00 VN ngày T, 06:00 VN ngày T+1)
  Ví dụ: báo cáo ngày 28/08 chỉ chứa bài publish từ 06:00 ngày 28/08 đến 06:00 ngày 29/08.
  Đợt 12:00 bổ sung bài bị miss trong cùng cửa sổ đó, không lấy bài mới hơn 06:00 hôm nay.

auto_report_job chạy SAU đợt morning_news_crawl 06:00 — tin tức đưa vào báo cáo được lọc
theo crawled_at ở services/report_generator.py::get_news_for_report (07:00 VN ngày T →
07:00 VN ngày T+1). Job này chỉ tạo báo cáo mới nếu ngày đó CHƯA có report (hoặc report cũ
bị 'failed') — không đụng vào report đã 'draft'/'published' do admin thao tác thủ công,
các API /api/admin/reports/* (generate/publish/edit/delete) vẫn hoạt động độc lập như cũ.

Chạy thủ công để test:
  python scheduler.py --now        # chạy ngay cả 4 job theo thứ tự (không cần đợi giờ)
  python scheduler.py              # chạy nền, chờ đúng lịch mỗi ngày
"""


import asyncio
import logging
import sys
from datetime import datetime, timezone, timedelta
from typing import List, Optional

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


async def daily_prices_job() -> None:
    """Job lập lịch chạy lúc 06:00 SA (giờ VN) mỗi ngày — chỉ crawl giá."""
    now_vn = datetime.now(TZ_VN)
    logger.info("=" * 60)
    logger.info("🚀 [SCHEDULER] Bắt đầu Daily Prices Job — %s", now_vn.strftime("%Y-%m-%d %H:%M:%S"))
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, run_crawl_prices)
    logger.info("✅ [SCHEDULER] Daily Prices Job hoàn thành — %s", datetime.now(TZ_VN).strftime("%H:%M:%S"))
    logger.info("=" * 60)


# ─── Job 2: Crawl News (06:00 toàn bộ nguồn & 12:00 chỉ Tier A) ──────────────

# Đợt 12:00 chỉ chạy lại nhóm Tier A (dữ liệu sàn/cơ quan chính thức) thay vì
# toàn bộ ~47 nguồn — đối chiếu domain trong sources.yaml, chỉ giữ domain THỰC
# SỰ có nguồn tương ứng trong file (bỏ qua iea.org, climateimpactx.com, acx.net,
# cmegroup.com, lme.com — không có trong sources.yaml).
NOON_TIER_A_DOMAINS: List[str] = [
    "eia.gov",
    "ieta.org",
    "opec.org",
    "nasdaq.com",
    "commission.europa.eu",
    "esma.europa.eu",
    "ice.com",
    "eex.com",
    "climate.ec.europa.eu",
]


async def run_crawl_news(domains: Optional[List[str]] = None) -> None:
    """Chạy pipeline crawl news (async). Import tại runtime để tránh xung đột asyncio.run().
    domains=None -> crawl toàn bộ nguồn; truyền list -> chỉ crawl đúng các domain đó.
    """
    from main import main as crawl_news_main
    label = f"{len(domains)} nguồn Tier A" if domains else "toàn bộ nguồn"
    logger.info("━━━ [NEWS] Bắt đầu crawl tin tức (%s)...", label)
    try:
        await crawl_news_main(domains=domains)
        logger.info("━━━ [NEWS] Hoàn thành crawl tin tức (%s).", label)
    except Exception:
        logger.exception("━━━ [NEWS] Lỗi không mong đợi khi crawl tin tức (%s)!", label)


async def morning_news_crawl_job() -> None:
    """Job lập lịch chạy lúc 06:00 (giờ VN) mỗi ngày — crawl TOÀN BỘ nguồn."""
    now_vn = datetime.now(TZ_VN)
    logger.info("=" * 60)
    logger.info("🚀 [SCHEDULER] Bắt đầu Morning News Crawl Job (toàn bộ nguồn) — %s", now_vn.strftime("%Y-%m-%d %H:%M:%S"))
    await run_crawl_news()
    logger.info("✅ [SCHEDULER] Morning News Crawl Job hoàn thành — %s", datetime.now(TZ_VN).strftime("%H:%M:%S"))
    logger.info("=" * 60)


async def noon_news_crawl_job() -> None:
    """Job lập lịch chạy lúc 12:00 (giờ VN) mỗi ngày — chỉ crawl lại nhóm Tier A."""
    now_vn = datetime.now(TZ_VN)
    logger.info("=" * 60)
    logger.info("🚀 [SCHEDULER] Bắt đầu Noon News Crawl Job (Tier A) — %s", now_vn.strftime("%Y-%m-%d %H:%M:%S"))
    await run_crawl_news(domains=NOON_TIER_A_DOMAINS)
    logger.info("✅ [SCHEDULER] Noon News Crawl Job hoàn thành — %s", datetime.now(TZ_VN).strftime("%H:%M:%S"))
    logger.info("=" * 60)


# ─── Job 3: Auto-generate Report (07:00) ─────────────────────────────────────

async def run_auto_report_job() -> None:
    """Tự động sinh 1 báo cáo/ngày cho ngày hôm qua (VN) — chạy sau đợt news crawl 06:00.

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

    target_date = (datetime.now(TZ_VN) - timedelta(days=1)).strftime("%Y-%m-%d")
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
    # Nếu gọi với --now → chạy ngay lập tức cả 4 job theo thứ tự (dùng để test)
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
    scheduler.add_job(
        morning_news_crawl_job,
        trigger=CronTrigger(hour=6, minute=0, timezone="Asia/Ho_Chi_Minh"),
        id="morning_news_crawl",
        name="Morning News Crawl (toàn bộ nguồn)",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        noon_news_crawl_job,
        trigger=CronTrigger(hour=12, minute=0, timezone="Asia/Ho_Chi_Minh"),
        id="noon_news_crawl",
        name="Noon News Crawl (Tier A)",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        run_auto_report_job,
        trigger=CronTrigger(hour=7, minute=0, timezone="Asia/Ho_Chi_Minh"),
        id="auto_report",
        name="Auto Report Generation",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    scheduler.start()
    logger.info("📅 Scheduler đã khởi động:")
    logger.info("   - Giá:      06:00 SA (giờ VN) mỗi ngày")
    logger.info("   - Tin tức:  06:00 (toàn bộ nguồn) & 12:00 (chỉ Tier A) giờ VN mỗi ngày")
    logger.info("   - Báo cáo:  07:00 SA (giờ VN) mỗi ngày (tự động, 1 lần/ngày)")

    if run_now:
        logger.info("⚡ Chế độ --now: chạy cả 4 job ngay lập tức để test (Prices → Morning News → Noon News → Report)...")
        await daily_prices_job()
        await morning_news_crawl_job()
        await noon_news_crawl_job()
        await run_auto_report_job()
        scheduler.shutdown()
        return

    # In thông tin lần chạy tiếp theo của từng job
    for job_id in ("daily_prices", "morning_news_crawl", "noon_news_crawl", "auto_report"):
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
