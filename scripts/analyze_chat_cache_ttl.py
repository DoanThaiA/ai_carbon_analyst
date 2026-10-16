"""
Chọn TTL cache cho prompt tĩnh của Jenny chat (QUOTE_CHAT_CACHE_TTL = 1h | 5m) dựa trên lịch sử câu
hỏi THẬT trong `chat_messages` — mô phỏng chi phí phần prompt tĩnh (tools + system tĩnh) theo 2 TTL.

Mô hình giá (đơn vị = 1 lần gửi prefix ở giá gốc):
  - 1h: ghi cache 2×, đọc 0,1×; entry sống 60 phút kể từ lần dùng gần nhất.
  - 5m: ghi cache 1,25×, đọc 0,1×; entry sống 5 phút kể từ lần dùng gần nhất.
Prefix giống nhau cho mọi câu hỏi cùng report_date trong cùng 1 ngày (giờ VN — lịch tham chiếu đổi
theo ngày). Bỏ qua khác biệt admin/user (admin có thêm tool jack_qc → prefix riêng) và các lượt gọi
tool trong 1 câu hỏi (cùng có lợi như nhau ở 2 TTL). KHÔNG gọi API, không tốn phí.

Usage:
    python -m scripts.analyze_chat_cache_ttl              # 30 ngày gần nhất
    python -m scripts.analyze_chat_cache_ttl --days 14 --prefix-tokens 20000
"""
import argparse
import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from core.config import Settings
from db.models import ChatMessage, ChatSession
from db.session import build_sessionmaker, create_engine

VN_TZ = timezone(timedelta(hours=7))
COSTS = {"1h": (2.0, timedelta(hours=1)), "5m": (1.25, timedelta(minutes=5))}
READ_COST = 0.1
SONNET_INPUT_USD_PER_MTOK = 2.0


def simulate(times: list, ttl: str) -> float:
    write_cost, life = COSTS[ttl]
    cost, last = 0.0, None
    for t in times:
        cost += READ_COST if last is not None and t - last <= life else write_cost
        last = t
    return cost


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--prefix-tokens", type=int, default=20000, help="Ước lượng token prompt tĩnh + tools")
    args = parser.parse_args()

    settings = Settings.from_env()
    engine = create_engine(settings.database_url)
    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    async with build_sessionmaker(engine)() as session:
        rows = (await session.execute(
            select(ChatSession.report_date, ChatMessage.created_at)
            .join(ChatSession, ChatMessage.session_id == ChatSession.id)
            .where(ChatMessage.role == "user", ChatMessage.created_at >= since)
            .order_by(ChatMessage.created_at)
        )).all()
    await engine.dispose()

    groups = defaultdict(list)
    for report_date, created_at in rows:
        groups[(report_date, created_at.astimezone(VN_TZ).date())].append(created_at)

    totals = {ttl: sum(simulate(ts, ttl) for ts in groups.values()) for ttl in COSTS}
    n = len(rows)
    print(f"{n} câu hỏi trong {args.days} ngày, {len(groups)} nhóm (ngày báo cáo × ngày hỏi).")
    if not n:
        print("Chưa có dữ liệu — giữ mặc định QUOTE_CHAT_CACHE_TTL=1h.")
        return
    for ttl, units in totals.items():
        usd = units * args.prefix_tokens / 1e6 * SONNET_INPUT_USD_PER_MTOK
        print(f"  TTL {ttl}: {units:8.1f} đơn vị prefix ≈ ${usd:,.2f} ({units / n:.2f}/câu hỏi)")
    best = min(totals, key=totals.get)
    saving = 1 - totals[best] / max(totals.values())
    print(f"\nKhuyến nghị: QUOTE_CHAT_CACHE_TTL={best} (rẻ hơn {saving:.0%} cho phần prompt tĩnh).")


if __name__ == "__main__":
    asyncio.run(main())
