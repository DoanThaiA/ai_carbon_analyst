import json
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import asyncio
import cohere
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc, func

from db.models import Article, Price, Instrument, Report
from core.config import Settings

logger = logging.getLogger(__name__)

_cohere_client: cohere.AsyncClientV2 | None = None
_settings: Settings | None = None


def _get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings


def _get_cohere_client() -> cohere.AsyncClientV2:
    global _cohere_client
    if _cohere_client is None:
        _cohere_client = cohere.AsyncClientV2(api_key=_get_settings().cohere_api_key)
    return _cohere_client

SECTION_TOPICS: Dict[str, List[str]] = {
    "1": ["eua_ets", "energy_gas", "energy_power_eu", "energy_coal", "energy_oil", "geopolitics", "eu_policy", "cbam"],
    "2": ["eua_ets", "energy_gas", "energy_power_eu", "energy_coal", "energy_oil", "geopolitics", "eu_policy", "cbam"],
    "3": ["eua_ets", "energy_gas", "energy_power_eu", "energy_coal", "energy_oil", "energy_renewable", "geopolitics", "eu_policy", "cbam"],
    "4": ["cbam", "vcm", "global_carbon_market", "vietnam_carbon_policy"],
    "5": ["eua_ets", "energy_gas", "energy_power_eu", "energy_coal", "energy_renewable", "geopolitics", "cbam"],
    "7": ["eua_ets", "geopolitics"],
    "8": ["eua_ets", "energy_gas", "energy_power_eu", "energy_coal", "energy_oil", "eu_policy", "cbam"],
    "biz": ["eua_ets", "energy_gas", "energy_power_eu", "energy_coal", "energy_oil",
            "energy_renewable", "geopolitics", "eu_policy", "cbam", "vcm",
            "global_carbon_market", "vietnam_carbon_policy"],
}

# ─────────────────────────────────────────────────────────────────────
# Khung phân tích giá EUA — tiêm vào prompt Mục 3 và 5
# ─────────────────────────────────────────────────────────────────────
EUA_ANALYSIS_FRAMEWORK = """
=== KHUNG PHÂN TÍCH GIÁ CARBON (EUA) — BẮT BUỘC ÁP DỤNG ===

A. CÁC MỐI LIÊN HỆ LIÊN THỊ TRƯỜNG (cross-market signals):
Chỉ nêu khi có dữ liệu / tin tức thực sự hỗ trợ. KHÔNG suy diễn gượng ép.

1. FUEL SWITCHING — chuỗi logic quan trọng nhất:
   Gas↑ → nhà máy điện chuyển sang than → phát thải↑ → nhu cầu EUA↑ → EUA↑
   Gas↓ hoặc Than↑ → chuyển ngược → phát thải↓ → EUA↓
   Hạ tầng điện sự cố hoặc Gas gián đoạn → thiếu điện → huy động than → EUA↑

   ĐIỆN ĐỨC (DEBY1) ↔ EUA — quan hệ HAI CHIỀU, PHẢI đọc cùng gas/than/RES, không kết luận một chiều:
   a) Power → EUA: Điện Đức↑ do huy động thêm than/gas (không phải do RES thấp) → utility hedge (mua thêm nhiên liệu + EUA tương ứng sản lượng đã bán) → cầu EUA↑ → EUA↑. Ngược lại điện thấp → giảm hedge, có thể unwind (bán EUA) → cầu EUA yếu đi.
   b) EUA → Power (chiều ngược lại, hay bị bỏ sót): EUA↑ → nhà máy đưa chi phí EUA vào giá chào bán điện (carbon cost pass-through) → giá điện Đức↑. Đây là lý do 2 biến số này thường tăng/giảm cùng chiều mà KHÔNG PHẢI lúc nào cũng do fuel switching.
   c) BẮT BUỘC: khi điện Đức biến động, phải đọc CÙNG với gas/than/RES trong cùng phiên trước khi kết luận hướng EUA — điện tăng do RES thấp/nhu cầu cao nhưng cơ cấu phát điện không đổi thì tín hiệu lên EUA YẾU hơn nhiều so với điện tăng do huy động thêm than/gas.
   RES (gió/mặt trời)↑ → dispatch than/gas↓ → phát thải↓ → cầu EUA↓ → EUA↓

2. DẦU & GASOIL:
   Dầu↑ → chi phí vận tải & sản xuất công nghiệp↑ → biên LN ngành thâm dụng NL↓
   Dầu thường tương quan thuận Gas → nếu kéo Gas↑ mạnh → fuel switching than → EUA↑
   Crack spread Gasoil rộng → nhu cầu diesel↑ (vận tải/công nghiệp) → tiêu thụ điện CN↑ → phát thải ngành điện + CN nặng trong EU ETS (thép, xi măng, hóa chất, lọc dầu) ↑ → cầu EUA↑ → EUA↑.
   Con số crack spread (nếu có, đã quy đổi cùng đơn vị USD/bbl) sẽ được cung cấp trực tiếp trong mục "GASOIL CRACK SPREAD" bên dưới — LUÔN dùng đúng con số đó khi nhận định mở rộng/thu hẹp, TUYỆT ĐỐI KHÔNG tự suy luận crack spread từ 2 số liệu khác đơn vị (Gasoil USD/MT vs Brent/WTI USD/bbl).

3. KIM LOẠI CƠ BẢN (nhôm/kẽm):
   Gas/Điện châu Âu↑ → chi phí smelter nhôm/kẽm↑ → cắt giảm công suất → giá kim loại↑
   LƯU Ý: hệ thống KHÔNG theo dõi giá kim loại theo dữ liệu giá (không có instrument nhôm/kẽm) — CHỈ được nêu liên kết này khi tin tức trích dẫn ở trên có SỐ LIỆU CỤ THỂ về giá/công suất kim loại; TUYỆT ĐỐI KHÔNG tự suy đoán chiều giá kim loại khi không có số liệu.

4. CBAM & MỞ RỘNG ETS:
   EUA↑ → CBAM certificate cost↑ → nhập khẩu thép/nhôm/xi măng vào EU đắt hơn → dịch chuyển cầu sang sản phẩm phát thải thấp
   Bổ sung ngành vào ETS (hàng hải, hàng không, xây dựng) → nhu cầu EUA↑ → EUA↑

5. CHÍNH SÁCH & MSR:
   MSR rút thêm cap (tăng tỷ lệ rút vốn) → cung EUA↓ → EUA↑ (ngược lại nếu giải phóng MSR/giảm tỷ lệ rút)
   Lịch đấu giá EUA dồn/tăng khối lượng trong tháng/quý → cung ngắn hạn↑ → áp lực giảm giá; trì hoãn/rút khỏi lịch đấu giá → cung ngắn hạn↓ → áp lực tăng giá
   Giảm tỷ lệ phân bổ miễn phí (free allocation) → doanh nghiệp phải mua thêm EUA → cầu↑ → EUA↑
   EUA↑ quá mạnh → thị trường tự điều chỉnh / MSR can thiệp → áp lực giảm
   Giá EUA phản ánh KỲ VỌNG chính sách tương lai, không chỉ hiện tại — tin về sửa luật/bỏ phiếu/phán quyết tòa án dù chưa có hiệu lực vẫn có thể làm giá phản ứng sớm

6. MACRO:
   USD↑ / Lãi suất thực↑ → áp lực giảm đồng thời vàng, dầu, kim loại cơ bản → có thể lan sang EUA.
   LƯU Ý: hệ thống KHÔNG theo dõi chỉ số USD/lãi suất theo dữ liệu giá — CHỈ được nêu liên kết này khi tin tức trích dẫn ở trên có SỐ LIỆU CỤ THỂ (vd DXY, lợi suất trái phiếu); TUYỆT ĐỐI KHÔNG tự suy đoán chiều USD/lãi suất khi không có số liệu.
   GDP / sản xuất CN↑ → nhu cầu điện & phát thải↑ → EUA↑
   Suy thoái kinh tế → phát thải↓ → EUA↓

B. 7 NHÓM YẾU TỐ CHÍNH TÁC ĐỘNG GIÁ EUA:
1. Giá năng lượng & fuel switching (Gas/Coal/Power → merit order → phát thải)
2. Chính sách & quy định (cap lộ trình, MSR, lịch đấu giá, phân bổ miễn phí, CBAM)
3. Năng lượng tái tạo & thời tiết (RES↑→EUA↓; nắng nóng/rét→nhu cầu↑→EUA↑)
4. Tài chính & đầu cơ (dòng vốn tài chính, thanh khoản — tác động ngắn hạn)
5. Kinh tế vĩ mô & chu kỳ (GDP, sản xuất CN, nhu cầu điện)
6. Địa chính trị (gián đoạn chuỗi cung ứng NL → fuel switching → EUA)
7. Compliance cycle (deadline nộp thuế ETS, mùa báo cáo phát thải → cầu EUA theo mùa)

C. QUY TẮC NHẬN ĐỊNH BẮT BUỘC:
- Luôn bắt đầu bằng số liệu thực tế (giá đóng cửa, % thay đổi) TRƯỚC khi phân tích nguyên nhân.
- Xây dựng chuỗi nhân quả rõ ràng, không gán nhãn cảm tính.
- Kết luận chiều giá EUA chỉ khi ≥2 yếu tố xác nhận cùng hướng.
- Tín hiệu mâu thuẫn nhau → ghi "tín hiệu hỗn hợp" + nêu 2 chiều + điều kiện kích hoạt mỗi chiều.
- Nếu không có liên kết chéo đáng chú ý: ghi "Không có tín hiệu liên thị trường mới" — KHÔNG bịa.
=== KẾT THÚC KHUNG PHÂN TÍCH ===
"""

# ─────────────────────────────────────────────────────────────────────
# Data fetching
# ─────────────────────────────────────────────────────────────────────

async def get_prices_for_report(session: AsyncSession, target_date_str: str) -> tuple[List[Dict], str]:
    """Lấy dữ liệu giá của ngày gần nhất có dữ liệu (<= target_date_str)."""
    # Tìm ngày gần nhất có dữ liệu
    max_date_stmt = select(func.max(Price.price_date)).where(Price.price_date <= target_date_str)
    max_date = await session.scalar(max_date_stmt)

    if not max_date:
        return [], None

    stmt = (
        select(Price, Instrument)
        .join(Instrument, Price.instrument_id == Instrument.id)
        .where(Price.price_date == max_date)
    )
    result = await session.execute(stmt)
    rows = result.all()

    prices = []
    for price, instrument in rows:
        is_up = price.day_change_pct is not None and price.day_change_pct > 0
        # Ghi chú bất thường: volume đột biến hoặc note thủ công
        note = price.note or ""
        prices.append({
            "name": instrument.name,
            "code": instrument.code,
            "price": f"{price.close_price:,.4f} {instrument.unit}",
            "dday": f"{price.day_change_pct:+.2f}%" if price.day_change_pct is not None else "-",
            "dweek": f"{price.week_change_pct:+.2f}%" if price.week_change_pct is not None else "-",
            "up": is_up,
            "note": note,
            "close": price.close_price,
            "day_change_pct": price.day_change_pct,
            "week_change_pct": price.week_change_pct,
            "category": instrument.category,
        })
    return prices, max_date


async def get_historical_ohlc_for_report(session: AsyncSession, instrument_code: str, target_date_str: str) -> List[Dict]:
    """Lấy dữ liệu OHLC của 30 ngày gần nhất cho biểu đồ."""
    stmt = (
        select(Price)
        .join(Instrument, Price.instrument_id == Instrument.id)
        .where(
            and_(
                Instrument.code == instrument_code,
                Price.price_date <= target_date_str
            )
        )
        .order_by(desc(Price.price_date))
        .limit(30)
    )
    result = await session.execute(stmt)
    prices = result.scalars().all()

    chart_data = []
    for p in reversed(prices):
        open_p = p.open_price if p.open_price is not None else p.close_price
        high_p = p.high_price if p.high_price is not None else p.close_price
        low_p = p.low_price if p.low_price is not None else p.close_price
        chart_data.append({
            "date": p.price_date,
            "open": open_p,
            "high": high_p,
            "low": low_p,
            "close": p.close_price
        })
    return chart_data


async def get_news_for_report(session: AsyncSession, target_date_str: str) -> tuple[Dict[str, List[Dict]], List[str]]:
    """Lấy tin tức được crawl vào ngày hôm sau của ngày báo cáo (theo múi giờ Việt Nam UTC+7).
    Ví dụ: Báo cáo ngày 23/08 sẽ lấy tin tức được quét vào sáng 24/08 để tổng kết trọn vẹn ngày 23.
    """
    target_date = datetime.strptime(target_date_str, "%Y-%m-%d")
    
    # Ngày thực hiện crawl là ngày hôm sau của ngày báo cáo
    crawl_date_vn = target_date + timedelta(days=1)
    
    # 00:00 ngày crawl_date_vn ở Việt Nam tương đương 17:00 ngày hôm trước theo UTC
    start_utc = crawl_date_vn - timedelta(hours=7)
    end_utc = start_utc + timedelta(days=1)

    stmt = (
        select(Article)
        .where(
            and_(
                Article.crawled_at >= start_utc,
                Article.crawled_at < end_utc
            )
        )
        .order_by(desc(Article.crawled_at))
        .limit(100)
    )
    result = await session.execute(stmt)
    articles = result.scalars().all()

    news_by_topic: Dict[str, List[Dict]] = {}
    sources: set = set()
    for article in articles:
        if not article.topic:
            continue
        sources.add(article.source)
        for topic in article.topic:
            if topic not in news_by_topic:
                news_by_topic[topic] = []
            news_by_topic[topic].append({
                "title": article.title,
                "summary": article.content[:500] + "...",
                "content_excerpt": article.content[:2000],
                "source": article.source,
                "url": article.url,
                "region": article.region,
            })

    return news_by_topic, list(sources)


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def _filter_news_for_section(news_by_topic: Dict[str, List[Dict]], section_key: str) -> str:
    """Lọc tin tức chỉ lấy các topic liên quan đến mục báo cáo."""
    relevant_topics = SECTION_TOPICS.get(section_key, [])
    text_parts = []
    for topic in relevant_topics:
        articles = news_by_topic.get(topic, [])
        if not articles:
            continue
        text_parts.append(f"\n--- TOPIC: {topic.upper()} ---")
        for i, art in enumerate(articles[:4]):  # tối đa 4 tin/topic để tiết kiệm token
            text_parts.append(
                f"{i+1}. [{art['source']}] {art['title']}\n   Tóm tắt: {art['summary']}"
            )
    return "\n".join(text_parts) if text_parts else "Không có tin tức liên quan."


def _filter_news_with_index(
    news_by_topic: Dict[str, List[Dict]], section_key: str, max_articles: int = 10
) -> tuple[str, Dict[int, Dict]]:
    """Giống _filter_news_for_section nhưng đánh số [N] cho từng bài (dedup theo
    url) và trả kèm bảng tra N -> bài viết gốc — dùng cho các mục cần trích dẫn
    nguồn có thể bấm link (Mục 7). LLM chỉ được chọn số thứ tự có sẵn, backend
    tự map sang URL thật — tránh để LLM tự bịa URL không tồn tại.
    """
    relevant_topics = SECTION_TOPICS.get(section_key, [])
    seen_urls: set = set()
    numbered: List[Dict] = []
    for topic in relevant_topics:
        for art in news_by_topic.get(topic, []):
            if art["url"] in seen_urls:
                continue
            seen_urls.add(art["url"])
            numbered.append(art)
            if len(numbered) >= max_articles:
                break
        if len(numbered) >= max_articles:
            break

    if not numbered:
        return "Không có tin tức liên quan.", {}

    index_lookup = {i + 1: art for i, art in enumerate(numbered)}
    lines = [
        f"[{i}] [{art['source']}] {art['title']}\n   Tóm tắt: {art['summary']}"
        for i, art in index_lookup.items()
    ]
    return "\n".join(lines), index_lookup


def _collect_cited_articles(news_by_topic: Dict[str, List[Dict]], limit: int = 40) -> List[Dict]:
    """Danh sách bài viết thật (title/source/url) đã đưa vào các prompt — dùng
    cho Mục 9 để trích dẫn URL cụ thể thay vì chỉ tên domain, dedup theo url."""
    seen_urls: set = set()
    articles: List[Dict] = []
    for topic_articles in news_by_topic.values():
        for art in topic_articles:
            if art["url"] in seen_urls:
                continue
            seen_urls.add(art["url"])
            articles.append(art)
    return articles[:limit]


def _build_section6_news(news_by_topic: Dict[str, List[Dict]], limit_per_region: int = 30) -> Dict[str, List[Dict]]:
    """Gom toàn bộ tin tức đã crawl trong ngày, dedup theo url, tách theo
    region ('vietnam' / 'international') cho Mục 6 — mỗi tin giữ title/summary/
    source/url để hiển thị dạng danh sách có thể bấm link tới bài gốc."""
    articles = _collect_cited_articles(news_by_topic, limit=1000)
    international = [a for a in articles if a.get("region") != "vietnam"][:limit_per_region]
    vietnam = [a for a in articles if a.get("region") == "vietnam"][:limit_per_region]
    return {"international": international, "vietnam": vietnam}


def _prompt_section6_summary(article: Dict, target_date: str) -> str:
    return f"""Bạn là chuyên gia phân tích thị trường carbon & năng lượng châu Âu.
Ngày báo cáo: {target_date}

BÀI VIẾT CẦN TÓM TẮT (CHỈ bài này, không liên quan bài nào khác):
Nguồn: {article['source']}
Tiêu đề: {article['title']}
Nội dung (trích): {article.get('content_excerpt') or article['summary']}

YÊU CẦU: Viết đúng 1 đoạn tóm tắt bằng TIẾNG VIỆT (2–4 câu) CHO RIÊNG bài viết này:
- 1–2 câu đầu: tóm tắt ĐÚNG nội dung chính của bài (fact, số liệu nếu bài có nêu) — TUYỆT ĐỐI KHÔNG bịa thêm thông tin ngoài nội dung đã cho, KHÔNG trộn với thông tin của bài viết khác.
- Câu cuối: 1 nhận định ngắn gọn về ý nghĩa/tác động của tin này đối với thị trường carbon/năng lượng châu Âu hoặc giá EUA — chỉ viết câu này nếu có cơ sở hợp lý từ nội dung bài, nếu bài không liên quan thì bỏ qua, chỉ tóm tắt fact.
- Nếu bài viết bằng tiếng Anh hoặc ngôn ngữ khác: dịch ý sang tiếng Việt tự nhiên, không dịch máy móc từng từ.
- Văn phong khách quan, súc tích. KHÔNG dùng markdown (không **, không gạch đầu dòng).

CHỈ TRẢ VỀ JSON HỢP LỆ (không text ngoài):
{{"summary": "..."}}"""


SECTION6_SUMMARY_CONCURRENCY = 4


async def _summarize_section6_articles(
    articles: List[Dict], target_date: str, concurrency: int = SECTION6_SUMMARY_CONCURRENCY
) -> List[Dict]:
    """Sinh tóm tắt bằng LLM CHO RIÊNG TỪNG bài đã lọt vào Mục 6 — mỗi bài 1
    lần gọi LLM ĐỘC LẬP, prompt CHỈ chứa đúng 1 bài (không gộp nhiều bài vào
    chung 1 prompt) để đảm bảo tóm tắt của bài nào chỉ dựa trên đúng nội dung
    bài đó, không bị trộn/tổng hợp chéo với các bài khác. Các lệnh gọi này độc
    lập với nhau nên chạy song song có giới hạn (Semaphore) thay vì tuần tự
    từng bài — giảm đáng kể thời gian sinh báo cáo khi Mục 6 có tới 60 bài, mà
    vẫn không vi phạm yêu cầu "mỗi bài 1 prompt riêng". Chỉ chạy cho các bài đã
    lọt vào Mục 6 (không tóm tắt toàn bộ tin trong ngày — tốn kém không cần thiết).

    Trả về danh sách bài MỚI (không mutate input gốc — các dict này còn được
    share với news_by_topic dùng cho prompt các mục khác) với "summary" đã
    thay bằng bản LLM viết riêng cho bài đó; bài nào LLM lỗi → fallback dùng
    đúng đoạn cắt content gốc của chính bài đó, không ảnh hưởng các bài khác.
    """
    if not articles:
        return []

    sem = asyncio.Semaphore(concurrency)

    async def _summarize_one(art: Dict) -> Dict:
        async with sem:
            await asyncio.sleep(1)  # giãn nhịp nhẹ trong mỗi slot, tránh dồn request tức thời
            raw = await _call_llm(_prompt_section6_summary(art, target_date))
            parsed = _extract_json(raw) if raw else None
            summary = (parsed.get("summary") or "").strip() if parsed else ""

            if not summary:
                logger.warning("[REPORT] Mục 6: tóm tắt LLM thất bại cho bài %s, dùng fallback.", art["url"])
                summary = art["summary"]

            return {**art, "summary": summary}

    return list(await asyncio.gather(*[_summarize_one(art) for art in articles]))


def _summarize_prices(prices: List[Dict]) -> str:
    """Tạo dòng tóm tắt số liệu giá để đưa vào prompt."""
    lines = []
    for p in prices:
        lines.append(
            f"  - {p['name']} ({p['code']}): {p['price']} | Δ ngày {p['dday']} | Δ tuần {p['dweek']}"
        )
    return "\n".join(lines) if lines else "Chưa có dữ liệu giá."


def _eua_trend_summary(chart_data: List[Dict]) -> str:
    """Tóm tắt xu hướng EUA 30 ngày từ OHLC."""
    if not chart_data:
        return "Không có dữ liệu lịch sử EUA."
    first = chart_data[0]["close"]
    last = chart_data[-1]["close"]
    high_30 = max(c["high"] for c in chart_data)
    low_30 = min(c["low"] for c in chart_data)
    change_30 = ((last - first) / first * 100) if first else 0
    return (
        f"EUA 30 ngày: mở {first:.2f} → đóng gần nhất {last:.2f} "
        f"({change_30:+.1f}%); 30-ngày-cao {high_30:.2f}, 30-ngày-thấp {low_30:.2f}."
    )


def _eua_session_range_summary(chart_data: List[Dict]) -> str:
    """Biên độ dao động PHIÊN LIỀN TRƯỚC của EUA — tính trực tiếp từ OHLC thật.

    Mục 1 yêu cầu LLM nêu "biên độ biến động trong phiên", nhưng LLM không có
    số liệu intraday nếu không được truyền vào rõ ràng — hàm này tính sẵn để
    tránh LLM tự bịa ra 1 con số biên độ nghe hợp lý.
    """
    if not chart_data:
        return "Không có dữ liệu biên độ phiên liền trước."
    latest = chart_data[-1]
    high, low, close = latest["high"], latest["low"], latest["close"]
    range_pct = ((high - low) / close * 100) if close else 0
    return (
        f"Biên độ phiên liền trước ({latest['date']}): cao {high:.2f} — thấp {low:.2f} "
        f"EUR/tCO2 (biên độ {high - low:.2f}, ~{range_pct:.1f}% so với giá đóng cửa)."
    )


# Hệ số quy đổi ICE Gasoil (niêm yết USD/tấn) sang USD/thùng để so được cùng đơn
# vị với Brent/WTI (USD/bbl) khi tính crack spread — ~7.45 thùng/tấn là hệ số quy
# đổi chuẩn ngành cho gasoil/diesel (EIA/API), không phải số chính xác tuyệt đối
# cho mọi lô hàng cụ thể — luôn gắn nhãn "ước tính" khi đưa vào prompt.
GASOIL_BBL_PER_TONNE = 7.45


def _gasoil_crack_spread_summary(prices: List[Dict]) -> Optional[str]:
    """Ước tính crack spread Gasoil vs Brent — tính bằng Python (quy đổi đơn vị),
    thay vì để LLM tự suy luận từ 2 số liệu khác đơn vị (Gasoil USD/MT, Brent
    USD/bbl), việc rất dễ cho ra kết luận sai vì lệch đơn vị.
    """
    gasoil = next((p for p in prices if p["code"] == "GASOIL"), None)
    brent = next((p for p in prices if p["code"] == "BRENT"), None)
    if not gasoil or not brent:
        return None

    def _prev_close(p: Dict) -> Optional[float]:
        pct = p.get("day_change_pct")
        if pct is None or p.get("close") is None:
            return None
        return p["close"] / (1 + pct / 100)

    gasoil_bbl = gasoil["close"] / GASOIL_BBL_PER_TONNE
    spread_today = gasoil_bbl - brent["close"]

    prev_gasoil_close = _prev_close(gasoil)
    prev_brent_close = _prev_close(brent)
    trend = ""
    if prev_gasoil_close is not None and prev_brent_close is not None:
        spread_prev = (prev_gasoil_close / GASOIL_BBL_PER_TONNE) - prev_brent_close
        delta = spread_today - spread_prev
        direction = "mở rộng" if delta > 0.01 else "thu hẹp" if delta < -0.01 else "gần như đi ngang"
        trend = (
            f" So với phiên trước, spread {direction} {abs(delta):.2f} USD/bbl "
            f"({spread_prev:+.2f} → {spread_today:+.2f})."
        )

    return (
        f"Gasoil crack spread vs Brent (ƯỚC TÍNH, quy đổi {GASOIL_BBL_PER_TONNE} thùng/tấn — "
        f"KHÔNG phải số liệu chính thức từ sàn): {spread_today:+.2f} USD/bbl "
        f"(Gasoil {gasoil_bbl:.2f} USD/bbl từ {gasoil['close']:.2f} USD/MT; Brent {brent['close']:.2f} USD/bbl)."
        f"{trend}"
    )


async def get_previous_report_events(session: AsyncSession, target_date_str: str) -> List[Dict]:
    """Lấy danh sách sự kiện Mục 8 từ báo cáo gần nhất TRƯỚC ngày target — để Mục
    8 hôm nay có thể cập nhật lại kết quả thực tế của các sự kiện kỳ trước đã qua."""
    stmt = (
        select(Report)
        .where(Report.report_date < target_date_str)
        .order_by(desc(Report.report_date))
        .limit(1)
    )
    result = await session.execute(stmt)
    prev_report = result.scalars().first()
    if not prev_report:
        return []
    return prev_report.content.get("8", {}).get("events", [])


def _format_prev_events(events: List[Dict]) -> str:
    if not events:
        return "(Không có báo cáo trước đó, hoặc báo cáo trước không có sự kiện nào.)"
    lines = [
        f"- {ev.get('datetime_vn', '?')} | {ev.get('event', '?')} | Tác động: {ev.get('impact', '?')}"
        for ev in events
    ]
    return "\n".join(lines)


async def _call_llm(prompt: str, max_retries: int = 3) -> Optional[str]:
    """Gọi Cohere async và trả về text thô, hoặc None nếu lỗi.

    Dùng AsyncClientV2 để không block asyncio event loop trong lúc
    chờ Cohere trả kết quả.
    """
    for attempt in range(max_retries):
        try:
            response = await _get_cohere_client().chat(
                model="command-a-03-2025",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.15,
            )
            return response.message.content[0].text
        except Exception as e:
            logger.error(f"Lỗi Cohere (lần {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                # Đợi một lúc rồi thử lại do Rate Limit (429 Too Many Requests)
                await asyncio.sleep(10 * (attempt + 1))
            else:
                return None
    return None


def _escape_bare_control_chars_in_json_strings(text: str) -> str:
    """Escape các control character (newline/tab/carriage-return) xuất hiện THÔ
    bên trong JSON string literal.

    LLM (đặc biệt khi trả lời dài, nhiều gạch đầu dòng như Mục 5) thường chèn
    xuống dòng thật thay vì "\\n" hợp lệ trong 1 giá trị string — vi phạm JSON
    strict và làm json.loads() raise, khiến cả mục bị fallback dù nội dung
    LLM sinh ra hoàn toàn hợp lệ. Quét theo từng ký tự, chỉ escape khi đang ở
    TRONG 1 string literal (không đụng vào whitespace định dạng JSON ở ngoài).
    """
    out = []
    in_string = False
    escape_next = False
    for ch in text:
        if in_string:
            if escape_next:
                out.append(ch)
                escape_next = False
            elif ch == "\\":
                out.append(ch)
                escape_next = True
            elif ch == '"':
                out.append(ch)
                in_string = False
            elif ch == "\n":
                out.append("\\n")
            elif ch == "\r":
                continue  # bỏ qua CR, giữ lại \n tương ứng nếu có (CRLF)
            elif ch == "\t":
                out.append("\\t")
            else:
                out.append(ch)
        else:
            if ch == '"':
                in_string = True
            out.append(ch)
    return "".join(out)


def _extract_json(raw: str) -> Optional[dict]:
    """Tìm và parse khối JSON đầu tiên trong chuỗi.

    Thử parse trực tiếp trước; nếu lỗi (thường do control character thô trong
    string — xem `_escape_bare_control_chars_in_json_strings`), thử lại sau khi
    sanitize thay vì bỏ cuộc ngay, tránh mất nội dung LLM đã sinh hợp lệ.
    """
    if not raw:
        return None
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start == -1 or end == 0:
        return None

    snippet = raw[start:end]
    try:
        return json.loads(snippet)
    except json.JSONDecodeError:
        pass

    try:
        return json.loads(_escape_bare_control_chars_in_json_strings(snippet))
    except json.JSONDecodeError:
        return None


# ─────────────────────────────────────────────────────────────────────
# Section-specific prompt builders
# ─────────────────────────────────────────────────────────────────────

def _prompt_section1(
    news_text: str, prices_text: str, eua_trend: str, eua_session_range: str, target_date: str
) -> str:
    return f"""Bạn là chuyên gia phân tích thị trường năng lượng & carbon châu Âu.
Ngày báo cáo: {target_date}

DỮ LIỆU GIÁ PHIÊN VỪA QUA:
{prices_text}

BIÊN ĐỘ PHIÊN LIỀN TRƯỚC CỦA EUA (số liệu thật, PHẢI dùng đúng con số này):
{eua_session_range}

XU HƯỚNG EUA 30 NGÀY:
{eua_trend}

TIN TỨC LIÊN QUAN (eua_ets, energy_gas, energy_power_eu, energy_coal, energy_oil, geopolitics, eu_policy, cbam):
{news_text}

YÊU CẦU: Viết MỤC 1 — TÓM TẮT ĐIỀU HÀNH.
Quy tắc bắt buộc:
- Tối đa 5 gạch đầu dòng, mỗi gạch KHÔNG QUÁ 2 câu.
- Sắp xếp theo mức độ tác động đến EUA (cao → thấp), KHÔNG theo nhóm hàng hoá.
- Gạch đầu PHẢI nêu: giá đóng cửa EUA phiên liền trước, biên độ biến động trong phiên (dùng ĐÚNG số liệu ở mục "BIÊN ĐỘ PHIÊN LIỀN TRƯỚC" ở trên, KHÔNG tự ước tính con số khác), và xu hướng 30 ngày.
- Nếu "BIÊN ĐỘ PHIÊN LIỀN TRƯỚC" ghi "Không có dữ liệu" → chỉ nêu giá đóng cửa + xu hướng 30 ngày, bỏ qua phần biên độ thay vì tự bịa số.
- Nêu rõ tin chính sách / địa chính trị nào ảnh hưởng trực tiếp đến giá EUA.
- Bắt đầu mỗi gạch bằng tag in đậm kiểu "**EUA:**" hoặc "**Chính sách:**" v.v.
- Nếu không có tin nổi bật: ghi "Không có sự kiện nổi bật."

CHỈ TRẢ VỀ JSON HỢP LỆ (không text ngoài):
{{"1": {{"title": "Tóm tắt điều hành", "bullets": ["..."]}}}}"""


def _prompt_section2(
    eua_key_facts: str, prices_text: str, news_text: str, target_date: str
) -> str:
    return f"""Bạn là chuyên gia phân tích thị trường carbon châu Âu.
Ngày báo cáo: {target_date}

SỐ LIỆU THẬT VỀ EUA (PHẢI dùng đúng các con số này, TUYỆT ĐỐI KHÔNG tự bịa hay tính lại số khác — các con số này đã hiển thị riêng trong bảng giá nên KHÔNG cần lặp lại nguyên văn, chỉ dùng để neo lập luận):
{eua_key_facts}

DỮ LIỆU GIÁ CÁC INSTRUMENT KHÁC TRONG PHIÊN:
{prices_text}

TIN TỨC LIÊN QUAN (eua_ets, energy_gas, energy_power_eu, energy_coal, energy_oil, geopolitics, eu_policy, cbam):
{news_text}

YÊU CẦU: Viết "chart_comment" — phần NHẬN XÉT PHÂN TÍCH về diễn biến giá EUA trong phiên liền trước, đặt ngay dưới bảng giá & biểu đồ nến ở Mục 2.
Quy tắc BẮT BUỘC:
- Đây là nhận xét PHÂN TÍCH, không phải nhắc lại số liệu (giá đóng cửa/biên độ/xu hướng 30 ngày đã hiển thị riêng ở bảng giá phía trên) — chỉ nhắc số liệu khi cần neo lập luận.
- 4–6 câu, đủ ý, đi thẳng vào NGUYÊN NHÂN đứng sau biến động: driver nào (tin tức, giá năng lượng liên quan — gas/than/điện/dầu, chính sách EU ETS, địa chính trị) đã khiến EUA tăng/giảm/đi ngang trong phiên.
- Nếu biến động trong phiên nhỏ và không có driver rõ ràng: nói rõ đây là phiên giằng co/thanh khoản thấp, không suy diễn nguyên nhân gượng ép.
- Đối chiếu với xu hướng 30 ngày: phiên này đang củng cố, đảo chiều, hay tiếp diễn xu hướng đó — nêu rõ.
- Kết câu bằng 1 câu nhận định ngắn gọn về tâm lý thị trường hiện tại (thận trọng/lạc quan/thận trọng chờ tin...).

CHỈ TRẢ VỀ JSON HỢP LỆ (không text ngoài):
{{"2": {{"chart_comment": "..."}}}}"""


def _prompt_section3(
    news_text: str, prices_text: str, eua_trend: str, eua_session_range: str,
    gasoil_crack_spread: str, target_date: str
) -> str:
    return f"""Bạn là chuyên gia phân tích thị trường năng lượng & carbon châu Âu.
Ngày báo cáo: {target_date}

{EUA_ANALYSIS_FRAMEWORK}

DỮ LIỆU GIÁ PHIÊN VỪA QUA (dùng số liệu này để xây dựng chuỗi nhân quả, TUYỆT ĐỐI KHÔNG tự bịa số khác):
{prices_text}

BIÊN ĐỘ PHIÊN LIỀN TRƯỚC CỦA EUA:
{eua_session_range}

GASOIL CRACK SPREAD (số liệu đã tính sẵn — PHẢI dùng đúng con số này nếu nhắc đến crack spread, KHÔNG tự tính lại từ giá Gasoil/Brent thô vì khác đơn vị):
{gasoil_crack_spread}

XU HƯỚNG EUA 30 NGÀY:
{eua_trend}

TIN TỨC LIÊN QUAN (eua_ets, energy_gas, energy_power_eu, energy_coal, energy_oil, energy_renewable, geopolitics, eu_policy, cbam):
{news_text}

YÊU CẦU: Viết MỤC 3 — PHÂN TÍCH CÁC YẾU TỐ NĂNG LƯỢNG TƯƠNG QUAN, CHÍNH SÁCH ẢNH HƯỞNG ĐẾN GIÁ EUA.
Đây là mục phân tích SÂU NHẤT của báo cáo — PHẢI đầy đủ, chi tiết, không được viết hời hợt hay bỏ trống bất kỳ phần nào bên dưới. Mục này gồm 3 phần con:

A. "analysis_blocks": mảng ĐÚNG 4 object, mỗi object gồm "heading" và "content" (3–5 câu, đủ ý, không viết chung chung):
   1. heading="Diễn biến chính" — bắt đầu bằng số liệu giá thực tế (EUA đóng cửa + biên độ phiên, TTF, Gas, Coal, Power Đức) trước; phân tích nguyên nhân sau. Fact trước, diễn giải sau.
   2. heading="Yếu tố dẫn dắt" — TỔNG HỢP TẤT CẢ thông tin liên quan trực tiếp đến giá EUA, trình bày dưới dạng ĐÚNG 7 DÒNG RIÊNG BIỆT, mỗi dòng 1 trong 7 NHÓM YẾU TỐ ở KHUNG PHÂN TÍCH (theo đúng thứ tự: 1. Giá năng lượng & fuel switching, 2. Chính sách & quy định, 3. Năng lượng tái tạo & thời tiết, 4. Tài chính & đầu cơ, 5. Kinh tế vĩ mô & chu kỳ, 6. Địa chính trị, 7. Compliance cycle).
      QUAN TRỌNG VỀ ĐỊNH DẠNG: "content" là 1 chuỗi string, nhưng PHẢI chèn ký tự xuống dòng thật (\\n) giữa dòng 1 và dòng 2, giữa dòng 2 và dòng 3, ... giữa dòng 6 và dòng 7 — TUYỆT ĐỐI KHÔNG viết liền 7 nhóm thành 1 đoạn văn dài không xuống dòng. Mỗi dòng bắt đầu bằng "N. Tên nhóm: " rồi tới nội dung (driver cụ thể nếu có dữ liệu/tin hỗ trợ + chiều tác động tăng/giảm lên EUA; nếu nhóm đó không có thông tin mới trong TIN TỨC/DỮ LIỆU GIÁ ở trên, ghi ngắn gọn "Không có thông tin mới").
      Ví dụ format (chỉ minh hoạ cấu trúc, không copy nội dung mẫu):
      "1. Giá năng lượng & fuel switching: TTF tăng 3.7%, có thể thúc đẩy fuel switching sang than → EUA↑.\\n2. Chính sách & quy định: Không có thông tin mới.\\n3. Năng lượng tái tạo & thời tiết: ...\\n4. Tài chính & đầu cơ: ...\\n5. Kinh tế vĩ mô & chu kỳ: ...\\n6. Địa chính trị: ...\\n7. Compliance cycle: ..."
   3. heading="Quan điểm thị trường" — nêu cả consensus view VÀ contrarian view (kèm nguồn cụ thể: tên tổ chức/nhà phân tích/báo cáo). Nếu không có: ghi "Không có quan điểm thị trường cụ thể."
   4. heading="Cần theo dõi" — liệt kê sự kiện/mốc/số liệu công bố sắp tới kèm ngày giờ Việt Nam cụ thể (nếu tin tức có đề cập), và vì sao mốc đó quan trọng với EUA. MỖI sự kiện là 1 DÒNG RIÊNG, đánh số "1.", "2.", "3."... — PHẢI chèn ký tự xuống dòng thật (\\n) giữa các dòng, TUYỆT ĐỐI KHÔNG viết liền các sự kiện thành 1 đoạn văn dài không xuống dòng (áp dụng đúng quy tắc định dạng như "Yếu tố dẫn dắt" ở trên).

B. "correlation_analysis": object PHẢI có đủ các trường sau — đây là phần bắt buộc theo yêu cầu "nhận xét ĐỘC LẬP" từng yếu tố tương quan trước khi tổng hợp:
   - "gas_comment": nhận xét ĐỘC LẬP về biến động Gas (TTF) trong ngày — nêu %Δ cụ thể lấy từ DỮ LIỆU GIÁ ở trên, và ý nghĩa của mức tăng/giảm đó.
   - "coal_comment": nhận xét ĐỘC LẬP về biến động than (Newcastle/API2 nếu có) trong ngày — %Δ cụ thể + ý nghĩa.
   - "power_comment": nhận xét ĐỘC LẬP về biến động Điện Đức trong ngày — %Δ cụ thể; áp dụng mục A.1 trong KHUNG (đọc CÙNG gas/than/RES trước khi kết luận điện tăng/giảm là do fuel switching hay do carbon cost pass-through).
   - "fuel_switching_chain": tổng hợp 3 nhận xét trên thành chuỗi logic đầy đủ theo đúng thứ tự Gas + Coal + Power → fuel switching → phát điện → phát thải → cầu EUA. Kết thúc bằng câu: "Tổng hợp 3 yếu tố này tạo áp lực [tăng/giảm/hỗn hợp] lên EUA."
   - "eua_conclusion": nhận định riêng, độc lập, dứt khoát về hướng đi của giá EUA dựa trên toàn bộ phân tích ở mục A và B.
     Áp dụng quy tắc: kết luận chiều giá chỉ khi ≥2 yếu tố cùng hướng; nếu mâu thuẫn → ghi "tín hiệu hỗn hợp" + nêu rõ 2 chiều + điều kiện kích hoạt mỗi chiều.

C. "trading_scenarios": mảng ĐÚNG 3 kịch bản — BẮT BUỘC đủ cả 3 horizon "ngắn hạn", "trung hạn", "dài hạn" (không được bỏ trống horizon nào). ĐÂY LÀ PHẦN NHẬN ĐỊNH CHIẾN LƯỢC QUAN TRỌNG NHẤT BÁO CÁO — viết theo đúng văn phong 1 note chiến lược của bàn giao dịch tổ chức (institutional trading desk), CHUYÊN NGHIỆP, THỰC TẾ, có số liệu cụ thể — TUYỆT ĐỐI KHÔNG viết chung chung, sáo rỗng, hay lặp lại nguyên văn câu ở mục A/B. Mỗi kịch bản gồm:
   - "horizon": "ngắn hạn" (1–2 tuần) / "trung hạn" (1–3 tháng) / "dài hạn" (>3 tháng)
   - "probability": xác suất kịch bản này xảy ra — CHỈ 1 trong 3 giá trị "Cao" / "Trung bình" / "Thấp", dựa trên driver ở mục A/B đã được dữ liệu/tin tức xác nhận rõ (probability cao hơn) hay mới chỉ là suy đoán/tin đồn (probability thấp hơn).
   - "condition": "Nếu [X cụ thể — gắn thẳng với 1 driver đã nêu ở mục A/B, có số liệu/ngưỡng/ngày tháng cụ thể] xảy ra..." — KHÔNG viết mơ hồ kiểu "nếu thị trường biến động mạnh".
   - "price_zone": vùng giá EUA tham chiếu CỤ THỂ bằng EUR/tCO2 cho kịch bản này (vùng hỗ trợ gần nhất / vùng kháng cự gần nhất) — PHẢI neo vào đúng số liệu 30-ngày-cao, 30-ngày-thấp, giá đóng cửa, biên độ phiên liền trước đã cung cấp ở trên, TUYỆT ĐỐI KHÔNG bịa con số không có căn cứ từ dữ liệu đã cho.
   - "market_pricing": nhận định hướng đi giá (tăng/giảm/đi ngang) KÈM lý do thị trường sẽ định giá theo hướng đó — viết như 1 nhận định phân tích thực sự có lập luận, không phải câu mẫu lặp lại.
   - "key_risk": kịch bản rủi ro CỤ THỂ — gắn với 1 sự kiện/ngưỡng/mốc thời gian rõ ràng (KHÔNG viết chung chung "rủi ro là biến động thị trường"), nêu rõ nếu rủi ro này xảy ra thì có thể đẩy giá lệch khỏi "price_zone" theo hướng nào, mức độ bao nhiêu.
   - "action_plan": kế hoạch hành động THỰC TẾ theo đúng quy trình quản trị rủi ro của 1 bàn giao dịch (mốc/ngưỡng giá cụ thể cần theo dõi để đánh giá lại kịch bản, tần suất cập nhật, chỉ báo/dữ liệu cần bổ sung theo dõi) — TUYỆT ĐỐI KHÔNG có câu lệnh mua/bán trực tiếp như "nên long/short", "nên mua/bán".

CHỈ TRẢ VỀ JSON HỢP LỆ (không text ngoài):
{{"3": {{"title": "Phân tích các yếu tố năng lượng tương quan, chính sách ảnh hưởng đến giá EUA", "analysis_blocks": [{{"heading": "...", "content": "..."}}], "correlation_analysis": {{"gas_comment": "...", "coal_comment": "...", "power_comment": "...", "fuel_switching_chain": "...", "eua_conclusion": "..."}}, "trading_scenarios": [{{"horizon": "...", "probability": "Cao/Trung bình/Thấp", "condition": "...", "price_zone": "...", "market_pricing": "...", "key_risk": "...", "action_plan": "..."}}]}}}}"""


def _prompt_section4(news_text: str, target_date: str) -> str:
    return f"""Bạn là chuyên gia phân tích thị trường carbon tự nguyện và CBAM.
Ngày báo cáo: {target_date}

TIN TỨC LIÊN QUAN (cbam, vcm, global_carbon_market, vietnam_carbon_policy):
{news_text}

YÊU CẦU: Viết MỤC 4 — CẬP NHẬT TÍN CHỈ CARBON & CBAM.
Mục này gồm 4 cấu phần bắt buộc (khi có tin). Nếu không có tin cho cấu phần đó, ghi rõ "Không có diễn biến trọng yếu":
  (i)   VCM quốc tế: thông báo từ tổ chức xác minh (Verra, Gold Standard, ACR, CAR, Article 6...).
  (ii)  VCM ngoài EU: quy định, chính sách thị trường tín chỉ tự nguyện ngoài EU.
  (iii) Dự án carbon gắn thép xanh / kim loại xanh.
  (iv)  Diễn biến CBAM: EU CBAM, UK CBAM, lộ trình của các nước.

Mỗi cấu phần là 1 gạch đầu dòng với prefix "[i]", "[ii]", "[iii]", "[iv]".

CHỈ TRẢ VỀ JSON HỢP LỆ (không text ngoài):
{{"4": {{"title": "Cập nhật tín chỉ carbon & CBAM", "bullets": ["[i] ...", "[ii] ...", "[iii] ...", "[iv] ..."]}}}}"""


def _prompt_section5(news_text: str, prices_text: str, gasoil_crack_spread: str, target_date: str) -> str:
    return f"""Bạn là chuyên gia phân tích liên thị trường năng lượng và carbon.
Ngày báo cáo: {target_date}

{EUA_ANALYSIS_FRAMEWORK}

DỮ LIỆU GIÁ PHIÊN VỪA QUA:
{prices_text}

GASOIL CRACK SPREAD (số liệu đã tính sẵn — PHẢI dùng đúng con số này nếu nhắc đến crack spread):
{gasoil_crack_spread}

TIN TỨC LIÊN QUAN (eua_ets, energy_gas, energy_power_eu, energy_coal, energy_renewable, geopolitics, cbam):
{news_text}

YÊU CẦU: Viết MỤC 5 — TÍN HIỆU LIÊN THỊ TRƯỜNG.
Kết quả PHẢI là "bullets": một MẢNG các chuỗi, MỖI TÍN HIỆU LIÊN THỊ TRƯỜNG LÀ MỘT PHẦN TỬ RIÊNG (xuống dòng riêng khi hiển thị) — TUYỆT ĐỐI KHÔNG gộp nhiều tín hiệu vào chung một đoạn văn dài.

Quy tắc BẮT BUỘC:
- Mục này LUÔN XUẤT HIỆN trong báo cáo.
- Dựa vào KHUNG PHÂN TÍCH bên trên, quét LẦN LƯỢT từng mối liên kết có thể áp dụng (fuel switching Gas/Coal/Power, Dầu & Gasoil crack spread, RES/thời tiết, CBAM & mở rộng ETS, Chính sách & MSR, Kim loại cơ bản nếu có số liệu, Macro nếu có số liệu):
    A. Với mỗi nhóm: xác định xem có biến động đáng kể (>0.5% hoặc có tin tức hỗ trợ cụ thể) hay không.
    B. Nếu có: kiểm tra xem biến động đó có tạo ra chuỗi lan truyền sang EUA không (theo đúng chuỗi nhân quả trong KHUNG).
    C. Nếu có tín hiệu LAN TRUYỀN: viết THÀNH MỘT BULLET RIÊNG cho liên kết đó — bắt đầu bằng tag in đậm nêu rõ cặp liên kết (vd "**Gas → EUA:**", "**Dầu/Crack spread → EUA:**", "**Điện Đức → EUA:**", "**Địa chính trị → EUA:**", "**Chính sách/MSR → EUA:**"...), sau đó nêu số liệu cụ thể (%Δ, mức giá), chuỗi logic nhân quả, và KẾT LUẬN rõ ràng về chiều tác động lên EUA trong bullet đó (không liệt kê suông, phải chốt chiều tăng/giảm/trung lập).
    D. Nếu tín hiệu của các nhóm mâu thuẫn nhau: thêm 1 bullet riêng ghi "**Tín hiệu hỗn hợp:**" + giải thích cụ thể 2 chiều đối lập và điều kiện nào sẽ khiến chiều nào thắng thế.
    E. Nếu không nhóm nào có biến động đáng kể: "bullets" chỉ gồm đúng 1 phần tử là câu "Không có tín hiệu liên thị trường mới." — KHÔNG bịa liên kết gượng ép.
- Nếu có từ 2 tín hiệu lan truyền trở lên: thêm 1 bullet CUỐI CÙNG bắt đầu bằng "**Tổng hợp:**" tóm tắt lại tất cả tín hiệu vừa nêu và kết luận áp lực chung (tăng/giảm/hỗn hợp) lên EUA trong phiên.

CHỈ TRẢ VỀ JSON HỢP LỆ (không text ngoài):
{{"5": {{"title": "Tín hiệu liên thị trường", "bullets": ["**Gas → EUA:** ...", "**Dầu/Crack spread → EUA:** ...", "**Tổng hợp:** ..."]}}}}"""


def _prompt_section7(news_text: str, target_date: str) -> str:
    return f"""Bạn là chuyên gia phân tích thị trường năng lượng & carbon châu Âu.
Ngày báo cáo: {target_date}

TIN TỨC LIÊN QUAN (đánh số [1], [2], ... — eua_ets, geopolitics):
{news_text}

YÊU CẦU: Viết MỤC 7 — QUAN ĐIỂM TRÁI CHIỀU ĐÁNG CHÚ Ý.
Quy tắc:
- CHỈ viết khi có quan điểm contrarian có cơ sở dữ liệu, dựa ĐÚNG vào tin tức đã đánh số ở trên — TUYỆT ĐỐI KHÔNG bịa quan điểm hay nguồn không có trong danh sách.
- Nếu có, trả về mảng "points" — MỖI quan điểm trái chiều là 1 phần tử RIÊNG, gồm:
    - "viewpoint": phân tích ĐẦY ĐỦ, CHI TIẾT (5–8 câu, không viết ngắn/hời hợt): (1) quan điểm consensus — đa số thị trường/nhà phân tích đang nghĩ gì; (2) quan điểm contrarian khác biệt ra sao; (3) luận điểm/bằng chứng cụ thể mà nguồn đưa ra để bảo vệ quan điểm trái chiều đó; (4) điều kiện/kịch bản nào sẽ khiến quan điểm contrarian này đúng thay vì consensus.
    - "source_index": số thứ tự [N] của tin tức ở trên đã dùng làm căn cứ — PHẢI là số có thật trong danh sách đã đánh số, KHÔNG được bịa số khác.
  Nếu có nhiều quan điểm trái chiều đáng chú ý, liệt kê đủ thành nhiều phần tử trong "points" (không giới hạn 1 phần tử).
- Nếu KHÔNG có quan điểm contrarian có cơ sở nào trong tin tức đã cho: "has_content" = false, "points" = [], "text" = "Không có quan điểm trái chiều có cơ sở trong kỳ này."

CHỈ TRẢ VỀ JSON HỢP LỆ (không text ngoài):
{{"7": {{"title": "Quan điểm trái chiều đáng chú ý", "has_content": true/false, "points": [{{"viewpoint": "...", "source_index": 1}}], "text": "..."}}}}"""


def _prompt_section8(news_text: str, prev_events_text: str, target_date: str) -> str:
    return f"""Bạn là chuyên gia lịch trình thị trường năng lượng & carbon châu Âu.
Ngày báo cáo: {target_date} (Giờ Việt Nam, UTC+7)

SỰ KIỆN ĐÃ NÊU TỪ BÁO CÁO KỲ TRƯỚC:
{prev_events_text}

TIN TỨC LIÊN QUAN:
{news_text}

YÊU CẦU: Viết MỤC 8 — LỊCH SỰ KIỆN 7 NGÀY TỚI.
Bắt buộc liệt kê các sự kiện định kỳ trọng yếu sau (kèm ngày giờ Việt Nam ước tính và mức tác động):
  - Tồn kho dầu EIA (thứ Tư hàng tuần, ~22h30 VN)
  - Tồn kho dầu API (thứ Ba hàng tuần, ~22h30 VN)
  - Rig count Baker Hughes (thứ Sáu hàng tuần)
  - Đấu giá EUA (nếu có trong tuần, xem lịch ICE)
  - Họp FOMC / ECB / chính sách liên quan (nếu có)
  - Đáo hạn hợp đồng tương lai (nếu có)
  - Sự kiện bổ sung từ tin tức (nếu có).
Mỗi event gồm: ngày giờ VN | Tên sự kiện | Mức tác động (Cao/Trung/Thấp).

CẬP NHẬT KẾT QUẢ SỰ KIỆN KỲ TRƯỚC (bắt buộc):
Với mỗi sự kiện trong "SỰ KIỆN ĐÃ NÊU TỪ BÁO CÁO KỲ TRƯỚC" ở trên mà ngày diễn ra đã qua so với {target_date}:
- Thêm field "outcome" vào object event tương ứng trong "events" trả về.
- CHỈ điền "outcome" bằng kết quả THỰC TẾ nếu TIN TỨC LIÊN QUAN ở trên xác nhận rõ ràng (vd số liệu tồn kho thực tế, kết quả cuộc họp...).
- Nếu tin tức KHÔNG xác nhận kết quả, ghi đúng "Chưa có thông tin kết quả xác nhận" — TUYỆT ĐỐI KHÔNG tự bịa số liệu/kết quả.
- Sự kiện kỳ trước mà ngày diễn ra CHƯA qua (vẫn còn trong 7 ngày tới) → liệt kê lại bình thường, KHÔNG cần field "outcome".
- Sự kiện mới của kỳ 7 ngày tới tính từ {target_date} → liệt kê bình thường, KHÔNG cần field "outcome".

CHỈ TRẢ VỀ JSON HỢP LỆ (không text ngoài):
{{"8": {{"title": "Lịch sự kiện 7 ngày tới", "events": [{{"datetime_vn": "...", "event": "...", "impact": "Cao/Trung/Thấp", "outcome": "... (optional, chỉ khi sự kiện đã qua)"}}]}}}}"""


def _prompt_biz_recommendation(news_text: str, prices_text: str, eua_trend: str, target_date: str) -> str:
    return f"""Bạn là chuyên gia tư vấn kinh doanh về carbon và năng lượng cho doanh nghiệp Việt Nam (SIM).
Ngày báo cáo: {target_date}

DỮ LIỆU GIÁ:
{prices_text}

XU HƯỚNG EUA 30 NGÀY:
{eua_trend}

TIN TỨC ĐA CHIỀU:
{news_text}

YÊU CẦU: Viết MỤC GỢI Ý KINH DOANH & GIẢI PHÁP CHO SIM.
Gồm 2 phần:
A. "short_term": mảng gạch đầu dòng, gợi ý ngắn hạn gắn TRỰC TIẾP với tin quan trọng/cập nhật mới nhất. Mỗi gạch nêu rõ: tình huống kích hoạt → hành động cụ thể → lý do.
B. "long_term": mảng gạch đầu dòng, gợi ý dài hạn rút ra từ cơ hội phân tích (chính sách CBAM, VCM, chuyển dịch năng lượng...). Mỗi gạch nêu rõ: cơ hội → giải pháp đề xuất → kỳ vọng.

Lưu ý: KHÔNG dùng câu lệnh mua/bán tài chính trực tiếp.

CHỈ TRẢ VỀ JSON HỢP LỆ (không text ngoài):
{{"biz": {{"title": "Gợi ý kinh doanh & giải pháp cho SIM", "short_term": ["..."], "long_term": ["..."]}}}}"""


# ─────────────────────────────────────────────────────────────────────
# Main orchestrator
# ─────────────────────────────────────────────────────────────────────

async def generate_report_content(session: AsyncSession, target_date: str) -> Dict[str, Any]:
    """
    Sinh nội dung báo cáo bằng cách gọi LLM riêng cho từng mục.
    Mỗi mục chỉ nhận đúng những topic tin tức liên quan.
    """
    # ── 1. Thu thập dữ liệu ──────────────────────────────────────────
    prices, max_price_date = await get_prices_for_report(session, target_date)
    chart_data = await get_historical_ohlc_for_report(session, "EUA", target_date)
    news_by_topic, sources = await get_news_for_report(session, target_date)

    prices_text = _summarize_prices(prices)
    eua_trend = _eua_trend_summary(chart_data)
    eua_session_range = _eua_session_range_summary(chart_data)
    gasoil_crack_spread = _gasoil_crack_spread_summary(prices) or (
        "Không có dữ liệu Gasoil hoặc Brent trong phiên này — không tính được crack spread."
    )
    prev_events_text = _format_prev_events(await get_previous_report_events(session, target_date))

    # Số liệu thật (tính sẵn bằng Python, không để LLM tự bịa) cho Mục 2:
    # giá đóng cửa phiên liền trước, biến động trong phiên liền trước, xu hướng 30 ngày.
    eua_prices = [p for p in prices if p["code"] == "EUA"]
    eua_key_facts = ""
    if eua_prices and chart_data:
        latest_close = eua_prices[0]["close"]
        prev_close = chart_data[-2]["close"] if len(chart_data) >= 2 else None
        if prev_close:
            delta = latest_close - prev_close
            delta_pct = (delta / prev_close * 100) if prev_close else 0
            direction = "tăng" if delta > 0 else ("giảm" if delta < 0 else "đi ngang")
            eua_key_facts = (
                f"Đóng cửa phiên liền trước ({target_date}): {latest_close:.2f} EUR/tCO2, "
                f"{direction} {abs(delta):.2f} ({delta_pct:+.1f}%) so với phiên trước đó ({prev_close:.2f}). "
                f"{eua_session_range} {eua_trend}"
            )
        else:
            eua_key_facts = (
                f"Đóng cửa phiên liền trước ({target_date}): {latest_close:.2f} EUR/tCO2. "
                f"{eua_session_range} {eua_trend}"
            )
    else:
        eua_key_facts = "Không có dữ liệu giá EUA cho phiên này."

    # Mục 7 cần trích dẫn nguồn có thể bấm link — đánh số tin tức trước, LLM chỉ
    # được chọn số thứ tự, backend tự map số đó sang URL thật (tránh bịa link).
    section7_news_text, section7_index_lookup = _filter_news_with_index(news_by_topic, "7")

    # ── 2. Gọi LLM từng mục song song (tuần tự để tránh rate limit) ──
    content: Dict[str, Any] = {}

    SECTIONS = [
        ("1", _prompt_section1(
            _filter_news_for_section(news_by_topic, "1"),
            prices_text, eua_trend, eua_session_range, target_date
        )),
        ("2", _prompt_section2(
            eua_key_facts, prices_text,
            _filter_news_for_section(news_by_topic, "2"), target_date
        )),
        ("3", _prompt_section3(
            _filter_news_for_section(news_by_topic, "3"),
            prices_text, eua_trend, eua_session_range, gasoil_crack_spread, target_date
        )),
        ("4", _prompt_section4(
            _filter_news_for_section(news_by_topic, "4"),
            target_date
        )),
        ("5", _prompt_section5(
            _filter_news_for_section(news_by_topic, "5"),
            prices_text, gasoil_crack_spread, target_date
        )),
        ("7", _prompt_section7(
            section7_news_text,
            target_date
        )),
        ("8", _prompt_section8(
            _filter_news_for_section(news_by_topic, "8"),
            prev_events_text, target_date
        )),
        ("biz", _prompt_biz_recommendation(
            _filter_news_for_section(news_by_topic, "biz"),
            prices_text, eua_trend, target_date
        )),
    ]

    FALLBACKS: Dict[str, dict] = {
        "1": {"title": "Tóm tắt điều hành", "bullets": ["Không thể sinh nội dung tự động."]},
        "2": {"chart_comment": eua_trend},
        "3": {"title": "Phân tích các yếu tố năng lượng tương quan, chính sách ảnh hưởng đến giá EUA",
              "analysis_blocks": [{"heading": "Diễn biến chính", "content": "Không có dữ liệu."}],
              "correlation_analysis": {
                  "gas_comment": "Không có dữ liệu.", "coal_comment": "Không có dữ liệu.",
                  "power_comment": "Không có dữ liệu.", "fuel_switching_chain": "Không có dữ liệu.",
                  "eua_conclusion": "Không có dữ liệu.",
              },
              "trading_scenarios": []},
        "4": {"title": "Cập nhật tín chỉ carbon & CBAM", "bullets": ["Không có diễn biến trọng yếu."]},
        "5": {"title": "Tín hiệu liên thị trường", "bullets": ["Không có tín hiệu liên thị trường mới."]},
        "7": {"title": "Quan điểm trái chiều đáng chú ý", "has_content": False, "points": [], "text": "Không có quan điểm trái chiều có cơ sở trong kỳ này."},
        "8": {"title": "Lịch sự kiện 7 ngày tới", "events": []},
        "biz": {"title": "Gợi ý kinh doanh & giải pháp cho SIM", "short_term": [], "long_term": []},
    }

    sem = asyncio.Semaphore(1)

    async def _process_section(sec_key: str, s_prompt: str):
        async with sem:
            logger.info(f"[REPORT] Đang sinh mục {sec_key}...")
            # Tạo khoảng trễ giữa các request liên tiếp để giảm tải rate limit
            await asyncio.sleep(3)
            raw = await _call_llm(s_prompt)
            parsed = _extract_json(raw) if raw else None

            if parsed and sec_key in parsed:
                sec_data = parsed[sec_key]
                logger.info(f"[REPORT] Mục {sec_key} OK.")
            else:
                logger.warning(f"[REPORT] Mục {sec_key} thất bại, dùng fallback. Raw: {raw[:200] if raw else 'None'}")
                sec_data = FALLBACKS[sec_key]
            
            return sec_key, sec_data

    tasks = [_process_section(k, p) for k, p in SECTIONS]
    results = await asyncio.gather(*tasks)

    section2_data: dict = FALLBACKS["2"]
    for section_key, section_data in results:
        if section_key == "7":
            if section_data.get("has_content") and section_data.get("points"):
                resolved_points = []
                for pt in section_data["points"]:
                    src_art = section7_index_lookup.get(pt.get("source_index"))
                    resolved_points.append({
                        "viewpoint": pt.get("viewpoint", ""),
                        "source_name": src_art["source"] if src_art else None,
                        "source_url": src_art["url"] if src_art else None,
                    })
                content["7"] = {**section_data, "points": resolved_points}
        elif section_key == "2":
            section2_data = section_data
        else:
            content[section_key] = section_data

    content["2"] = {
        "title": "Bảng giá nhanh",
        "price_timestamp": f"Giá chốt phiên {max_price_date or target_date} (nguồn: Barchart EOD)",
        "key_facts": eua_key_facts,
        "prices": prices,
        "chart_data": chart_data,
        "chart_comment": section2_data.get("chart_comment") or eua_key_facts,
    }

    section6_news = _build_section6_news(news_by_topic)
    section6_international = await _summarize_section6_articles(section6_news["international"], target_date)
    section6_vietnam = await _summarize_section6_articles(section6_news["vietnam"], target_date)
    content["6"] = {
        "title": "Chi tiết các tin tức chính",
        "international": section6_international,
        "vietnam": section6_vietnam,
    }

    cited_articles = _collect_cited_articles(news_by_topic)
    content["9"] = {
        "title": "Nguồn tham khảo",
        "items": [{"source": a["source"], "title": a["title"], "url": a["url"]} for a in cited_articles],
    }

    return content
