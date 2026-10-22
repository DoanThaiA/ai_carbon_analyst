"""
scheduler.py
============
Script chạy ngầm 24/7, tự động kích hoạt các tác vụ độc lập:

  1. daily_prices_job       06:00 VN       — crawl giá các hợp đồng tương lai (BarchartPriceCrawler)
                                            + giá CBAM (EC) + đấu giá EUA EEX (crawl_eex_auctions)
  2. news_crawl_job         08:00 → 17:00  — crawl tin tức đầu mỗi giờ trong giờ hành chính, mọi ngày,
                                             tách 2 job theo `region` của news_crawl_sources (xem
                                             api/routers/admin_news_sources.py, NEWS_CRAWL_SCHEDULES):
                                               - vn_news_crawl:   region='vietnam', giờ VN, thêm 1 đợt
                                                 06:00 để tin qua đêm kịp vào báo cáo 07:00
                                               - intl_news_crawl: region='international', giờ New York
                                                 (≈ 19:00/20:00 → 04:00/05:00 giờ VN, tuỳ giờ mùa hè Mỹ)
                                             Cờ is_noon_crawl không còn ảnh hưởng lịch tự động.
  3. auto_report_job        07:00 VN       — CHỈ thứ 3 → thứ 7 (cấu hình AUTO_REPORT_DAYS / AUTO_REPORT_TIME)
                                      tự động sinh 1 báo cáo NHÁP/ngày bằng Claude, lưu report_date = HÔM NAY
                                      (VN) — dữ liệu vẫn là giá/tin của hôm qua, xem
                                      services/report_generator.py::report_data_date
     (08:00 VN — lịch Claude (routine, NGOÀI scheduler này) QC bản nháp qua MCP, token qc_agent, gửi biên bản
      vào report_qc_reviews — services/report_qc_review.py)
  4. auto_publish_job       08:10 VN       — cùng các ngày; tự xuất bản bản nháp hôm nay + gửi email PDF, CHỈ
                                      chặn khi biên bản QC mới nhất kết luận Không đạt (AUTO_PUBLISH_TIME,
                                      trống = tắt)

Crawl theo giờ không nhân chi phí LLM vì mỗi đợt chỉ xử lý URL MỚI: URL đã lưu (`articles`)
và URL đã bị loại (`crawl_seen_urls` — LLM đánh không liên quan, ngoài cửa sổ ngày, ...)
đều được bỏ qua ngay từ listing page (services/storage.py::load_recent_urls). Bài chỉ được
nhận nếu đăng trong CRAWL_LOOKBACK_HOURS giờ gần nhất (cửa sổ trượt, pipeline/crawl_pipeline.py).

Tin tức đưa vào báo cáo được lọc theo crawled_at ở
services/report_generator.py::get_news_for_report (07:00 VN ngày T-1 → 07:00 VN ngày T,
với T = report_date = ngày sinh báo cáo) — cả 2 nhóm nguồn đều đã crawl xong trước 07:00 VN
(nguồn quốc tế kết thúc khoảng 04:00–05:00 VN). Job auto report chỉ tạo báo cáo mới nếu ngày đó CHƯA có report (hoặc report cũ
bị 'failed') — không đụng vào report đã 'draft'/'published' do admin thao tác thủ công,
các API /api/admin/reports/* (generate/publish/edit/delete) vẫn hoạt động độc lập như cũ.

Chạy thủ công để test:
  python scheduler.py --now        # chạy ngay giá → tin (toàn bộ nguồn) → báo cáo (không cần đợi giờ)
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
# mỗi nửa đêm, giữ 14 ngày — crawl nhiều đợt/ngày nên log tăng nhanh, không để 1 file phình mãi.
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


async def run_crawl_eex_auctions() -> None:
    """Lịch + kết quả đấu giá sơ cấp EUA trên EEX (ước tính giá CBAM quý + lịch Mục 8) — lỗi chỉ log,
    KHÔNG làm hỏng job giá chính."""
    from crawl_prices.crawl_eex_auctions import crawl_eex_auctions
    try:
        stats = await crawl_eex_auctions()
        logger.info("━━━ [EEX] Đấu giá: %d kết quả, %d phiên lịch. Lỗi: %s",
                    stats.get("results", 0), stats.get("scheduled", 0), stats.get("errors", []))
    except Exception:
        logger.exception("━━━ [EEX] Lỗi khi crawl đấu giá EEX")


async def daily_prices_job() -> None:
    """Job lập lịch chạy lúc 06:00 SA (giờ VN) mỗi ngày — chỉ crawl giá."""
    now_vn = datetime.now(TZ_VN)
    logger.info("=" * 60)
    logger.info("🚀 [SCHEDULER] Bắt đầu Daily Prices Job — %s", now_vn.strftime("%Y-%m-%d %H:%M:%S"))
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, run_crawl_prices)
    await run_crawl_cbam_price()
    await run_crawl_eex_auctions()
    logger.info("✅ [SCHEDULER] Daily Prices Job hoàn thành — %s", datetime.now(TZ_VN).strftime("%H:%M:%S"))
    logger.info("=" * 60)


# ─── Job 2: Crawl News (mỗi giờ trong giờ hành chính, tách theo vùng) ──────────

# Giờ hành chính: chạy đầu mỗi giờ từ 08:00 đến 17:00 (cả 2 đầu), MỌI ngày trong tuần,
# theo giờ địa phương của nhóm nguồn — báo trong nước theo giờ VN, báo quốc tế theo giờ
# New York (APScheduler tự xử lý giờ mùa hè/đông của Mỹ). Nhóm trong nước có thêm 1 đợt
# 06:00 VN để tin qua đêm kịp vào báo cáo 07:00 (nhóm quốc tế đã xong lúc ~04:00–05:00 VN).
# Khoảng nghỉ dài nhất giữa 2 đợt là 17:00 → 08:00 = 15h, vẫn nằm trong
# CRAWL_LOOKBACK_HOURS (36h) nên không mất bài.

# (job_id, region của news_crawl_sources, giờ chạy — cú pháp `hour` của cron, múi giờ lịch chạy, nhãn log)
NEWS_CRAWL_SCHEDULES = (
    ("vn_news_crawl", "vietnam", "6,8-17", "Asia/Ho_Chi_Minh", "nguồn trong nước, giờ VN"),
    ("intl_news_crawl", "international", "8-17", "America/New_York", "nguồn quốc tế, giờ New York"),
)


async def run_crawl_news(region: str | None = None) -> None:
    """Chạy pipeline crawl news (async) cho các nguồn is_active=True của `region`
    (None = toàn bộ nguồn). Import tại runtime để tránh xung đột asyncio.run()."""
    from main import main as crawl_news_main
    label = f"region={region}" if region else "toàn bộ nguồn"
    logger.info("━━━ [NEWS] Bắt đầu crawl tin tức (%s)...", label)
    try:
        await crawl_news_main(region=region)
        logger.info("━━━ [NEWS] Hoàn thành crawl tin tức (%s).", label)
    except Exception:
        logger.exception("━━━ [NEWS] Lỗi không mong đợi khi crawl tin tức (%s)!", label)


async def news_crawl_job(region: str | None = None, label: str = "toàn bộ nguồn") -> None:
    """Job lập lịch chạy đầu mỗi giờ trong giờ hành chính — crawl các nguồn của `region`."""
    started = datetime.now(TZ_VN)
    logger.info("=" * 60)
    logger.info("🚀 [SCHEDULER] Bắt đầu News Crawl Job (%s) — %s VN", label, started.strftime("%Y-%m-%d %H:%M:%S"))
    await run_crawl_news(region)
    finished = datetime.now(TZ_VN)
    elapsed = (finished - started).total_seconds()
    logger.info("✅ [SCHEDULER] News Crawl Job (%s) hoàn thành — %s VN (%.0f giây)", label, finished.strftime("%H:%M:%S"), elapsed)
    if elapsed > 3600:
        # max_instances=1 → APScheduler bỏ qua đợt kế tiếp nếu đợt này chưa xong.
        logger.warning("⚠️ [SCHEDULER] Đợt crawl (%s) kéo dài hơn 1 giờ — đợt kế tiếp đã bị bỏ qua.", label)
    logger.info("=" * 60)


# ─── Job 3: Auto-generate Report (07:00) ─────────────────────────────────────

async def _ensure_prices_complete(report_date: str) -> None:
    """Trước khi sinh báo cáo: nếu có mã giá is_active thiếu dòng ở ngày dữ liệu (đợt crawl
    06:00 lỗi từng phần, vd TTF timeout) thì crawl giá lại 1 lần rồi kiểm tra lại. Không raise —
    vẫn sinh báo cáo, chỉ cảnh báo trong log nếu còn thiếu. Bỏ qua báo cáo CN/T2 (không dùng giá)."""
    from api.deps import async_session_maker
    from services.report_generator import find_missing_price_codes, is_no_price_report, report_data_date

    if is_no_price_report(report_date):
        return
    data_date = report_data_date(report_date)
    try:
        async with async_session_maker() as session:
            missing = await find_missing_price_codes(session, data_date)
        if not missing:
            return
        logger.warning("━━━ [REPORT] Thiếu giá %s ngày %s — crawl giá lại trước khi sinh báo cáo", missing, data_date)
        await asyncio.get_event_loop().run_in_executor(None, run_crawl_prices)
        async with async_session_maker() as session:
            missing = await find_missing_price_codes(session, data_date)
        if missing:
            logger.warning("━━━ [REPORT] Sau khi crawl lại vẫn thiếu giá %s — báo cáo sẽ không có các mã này", missing)
        else:
            logger.info("━━━ [REPORT] Đã bổ sung đủ giá sau khi crawl lại")
    except Exception:
        logger.exception("━━━ [REPORT] Lỗi khi kiểm tra/bổ sung giá — vẫn tiếp tục sinh báo cáo")


async def run_auto_report_job() -> None:
    """Tự động sinh 1 báo cáo/ngày, lưu report_date = HÔM NAY (VN) — chạy sau đợt news
    crawl 06:00 (nguồn trong nước).Dữ liệu trong báo cáo vẫn là của hôm qua (giá phiên đóng cửa hôm qua,
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
        await _ensure_prices_complete(target_date)
        await _run_report_generation_job(target_date)
        logger.info("✅ [SCHEDULER] Auto Report Job hoàn thành cho ngày %s.", target_date)
    except Exception:
        logger.exception("━━━ [REPORT] Lỗi không mong đợi khi tự động sinh báo cáo %s!", target_date)
    logger.info("=" * 60)


async def run_auto_publish_job() -> None:
    """Tự xuất bản bản nháp HÔM NAY (VN) lúc AUTO_PUBLISH_TIME — quy tắc C2: chỉ chặn khi biên bản QC (Claude/Jack
    qua MCP) mới nhất của báo cáo kết luận Không đạt; chưa có biên bản vẫn xuất bản. Báo cáo không ở trạng thái
    draft (đang sinh, lỗi, đã xuất bản, chưa có) thì bỏ qua — admin vẫn duyệt tay được như cũ."""
    from sqlalchemy import select
    from api.deps import async_session_maker
    from api.routers.admin_reports import _send_report_email_job
    from db.models import Report
    from services.report_qc_review import publish_gate

    target_date = datetime.now(TZ_VN).strftime("%Y-%m-%d")
    logger.info("=" * 60)
    logger.info("🚀 [SCHEDULER] Auto Publish Job cho báo cáo %s...", target_date)
    async with async_session_maker() as session:
        report = (await session.execute(select(Report).where(Report.report_date == target_date))).scalars().first()
        if report is None or report.status != "draft":
            logger.info("━━━ [PUBLISH] Báo cáo %s không ở trạng thái draft (%s) — bỏ qua.",
                        target_date, report.status if report else "chưa có")
            logger.info("=" * 60)
            return
        allowed, reason = await publish_gate(session, target_date)
        if not allowed:
            logger.warning("━━━ [PUBLISH] KHÔNG tự xuất bản %s: %s — chờ admin xử lý.", target_date, reason)
            logger.info("=" * 60)
            return
        report.status = "published"
        report.published_at = datetime.utcnow()
        await session.commit()
    logger.info("✅ [PUBLISH] Đã tự xuất bản %s (%s) — gửi email PDF...", target_date, reason)
    try:
        await _send_report_email_job(target_date)
    except Exception:
        logger.exception("━━━ [PUBLISH] Lỗi gửi email PDF báo cáo %s (báo cáo vẫn đã xuất bản).", target_date)
    logger.info("=" * 60)


def _parse_hhmm(value: str, default: str) -> tuple:
    raw = (value or default).strip()
    try:
        hour, minute = (int(x) for x in raw.split(":", 1))
        if 0 <= hour < 24 and 0 <= minute < 60:
            return hour, minute
    except ValueError:
        pass
    logger.warning("Giờ '%s' không hợp lệ (cần HH:MM) — dùng %s.", raw, default)
    return tuple(int(x) for x in default.split(":"))


# ─── Main ─────────────────────────────────────────────────────────────────────

async def main() -> None:
    # Nếu gọi với --now → chạy ngay lập tức giá → tin → báo cáo theo thứ tự (dùng để test)
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
    # Mỗi nhóm nguồn 1 job: max_instances=1 để 2 đợt CÙNG nhóm không bao giờ chạy chồng
    # nhau (seen_urls/seen_hashes nằm trong RAM từng đợt — chạy chồng sẽ gọi LLM 2 lần cho
    # cùng bài); 2 nhóm khác nhau có tập nguồn riêng nên chồng giờ cũng không sao.
    # coalesce=True gộp các lần lỡ (server dừng / đợt trước chạy quá giờ) thành 1.
    for job_id, region, hours, tz, label in NEWS_CRAWL_SCHEDULES:
        scheduler.add_job(
            news_crawl_job,
            trigger=CronTrigger(hour=hours, minute=0, timezone=tz),
            args=[region, label],
            id=job_id,
            name=f"News Crawl ({label})",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=1800,
        )
    # Chỉ tự động sinh báo cáo vào các ngày cấu hình ở AUTO_REPORT_DAYS (mặc định
    # thứ 3 → thứ 7, xem core/config.py::Settings.auto_report_days).
    from core.config import Settings
    cfg = Settings.from_env()
    auto_report_days = cfg.auto_report_days
    report_h, report_m = _parse_hhmm(cfg.auto_report_time, "07:00")
    scheduler.add_job(
        run_auto_report_job,
        trigger=CronTrigger(day_of_week=auto_report_days, hour=report_h, minute=report_m, timezone="Asia/Ho_Chi_Minh"),
        id="auto_report",
        name="Auto Report Generation",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    publish_hm = _parse_hhmm(cfg.auto_publish_time, "08:10") if cfg.auto_publish_time else None
    if publish_hm:
        scheduler.add_job(
            run_auto_publish_job,
            trigger=CronTrigger(day_of_week=auto_report_days, hour=publish_hm[0], minute=publish_hm[1],
                                timezone="Asia/Ho_Chi_Minh"),
            id="auto_publish",
            name="Auto Report Publish",
            replace_existing=True,
            # Trễ quá 30 phút (server dừng) thì bỏ lượt — admin duyệt tay, tránh xuất bản giữa trưa bất ngờ.
            misfire_grace_time=1800,
        )

    scheduler.start()
    logger.info("📅 Scheduler đã khởi động:")
    logger.info("   - Giá:      06:00 SA (giờ VN) mỗi ngày")
    for _, _, hours, tz, label in NEWS_CRAWL_SCHEDULES:
        logger.info("   - Tin tức:  đầu giờ %s (%s, %s)", hours, label, tz)
    logger.info("   - Báo cáo nháp: %02d:%02d (giờ VN), chỉ các ngày '%s' (AUTO_REPORT_DAYS)", report_h, report_m, auto_report_days)
    if publish_hm:
        logger.info("   - Tự xuất bản: %02d:%02d (giờ VN), chặn khi biên bản QC Không đạt", *publish_hm)
    else:
        logger.info("   - Tự xuất bản: TẮT (AUTO_PUBLISH_TIME trống)")

    if run_now:
        logger.info("⚡ Chế độ --now: chạy ngay để test (Prices → News toàn bộ nguồn → Report)...")
        await daily_prices_job()
        await news_crawl_job()
        await run_auto_report_job()
        scheduler.shutdown()
        return

    # In thông tin lần chạy tiếp theo của từng job
    for job_id in ("daily_prices", *(j[0] for j in NEWS_CRAWL_SCHEDULES), "auto_report", "auto_publish"):
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
