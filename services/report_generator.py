import json
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

import cohere
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc

from db.models import Article, Price, Instrument, Report
from core.config import Settings

logger = logging.getLogger(__name__)

# Lazy singleton — khởi tạo khi cần, không chạy lúc import module.
# Tránh crash khi test / import thiếu .env.
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

async def get_prices_for_report(session: AsyncSession, target_date_str: str) -> List[Dict]:
    """Lấy dữ liệu giá của ngày target."""
    stmt = (
        select(Price, Instrument)
        .join(Instrument, Price.instrument_id == Instrument.id)
        .where(Price.price_date == target_date_str)
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
    return prices


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
    """Lấy tin tức 48h qua, phân loại theo topic."""
    target_date = datetime.strptime(target_date_str, "%Y-%m-%d")
    start_date = target_date - timedelta(days=2)

    stmt = (
        select(Article)
        .where(Article.crawled_at >= start_date)
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
                "source": article.source,
                "url": article.url,
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


async def _call_llm(prompt: str) -> Optional[str]:
    """Gọi Cohere async và trả về text thô, hoặc None nếu lỗi.

    Dùng AsyncClientV2 để không block asyncio event loop trong lúc
    chờ Cohere trả kết quả.
    """
    try:
        response = await _get_cohere_client().chat(
            model="command-a-03-2025",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.15,
        )
        return response.message.content[0].text
    except Exception as e:
        logger.error(f"Lỗi Cohere: {e}")
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


def _prompt_section3(
    news_text: str, prices_text: str, eua_trend: str, gasoil_crack_spread: str, target_date: str
) -> str:
    return f"""Bạn là chuyên gia phân tích thị trường năng lượng & carbon châu Âu.
Ngày báo cáo: {target_date}

{EUA_ANALYSIS_FRAMEWORK}

DỮ LIỆU GIÁ PHIÊN VỪA QUA (dùng số liệu này để xây dựng chuỗi nhân quả):
{prices_text}

GASOIL CRACK SPREAD (số liệu đã tính sẵn — PHẢI dùng đúng con số này nếu nhắc đến crack spread, KHÔNG tự tính lại từ giá Gasoil/Brent thô vì khác đơn vị):
{gasoil_crack_spread}

XU HƯỚNG EUA 30 NGÀY:
{eua_trend}

TIN TỨC LIÊN QUAN:
{news_text}

YÊU CẦU: Viết MỤC 3 — PHÂN TÍCH NĂNG LƯỢNG & TÁC ĐỘNG LÊN EUA.
Mục này gồm 3 phần con:

A. "analysis_blocks": mảng 4 object, mỗi object gồm "heading" và "content" (2–4 câu):
   1. heading="Diễn biến chính" — bắt đầu bằng số liệu giá thực tế (EUA, TTF, Gas, Coal, Power) trước; phân tích nguyên nhân sau.
   2. heading="Yếu tố dẫn dắt" — liệt kê các driver chính hôm nay; áp dụng đúng các mối liên hệ trong KHUNG PHÂN TÍCH ở trên (fuel switching, crack spread, MSR, RES...). Kiểm tra từng yếu tố trong 7 nhóm.
   3. heading="Quan điểm thị trường" — nêu cả consensus view VÀ contrarian view (kèm nguồn cụ thể). Nếu không có: ghi "Không có quan điểm thị trường cụ thể."
   4. heading="Cần theo dõi" — nêu sự kiện/mốc quan trọng kèm ngày giờ Việt Nam cụ thể.

B. "correlation_analysis": object gồm:
   - "gas_coal_power": phân tích độc lập mức tăng/giảm trong ngày của Gas (%Δ) + Coal (%Δ) + Điện Đức (%Δ). Xây dựng chuỗi logic đúng theo KHUNG: Gas+Coal+Power → fuel switching → phát điện → phát thải → EUA.
     Kết luận bằng câu: tổng hợp 3 yếu tố này tạo áp lực [tăng/giảm/hỗn hợp] lên EUA.
   - "eua_conclusion": nhận định riêng, độc lập về hướng đi của giá EUA dựa trên phân tích trên.
     Áp dụng quy tắc: kết luận chiều giá chỉ khi ≥2 yếu tố cùng hướng; nếu mâu thuẫn → ghi "tín hiệu hỗn hợp" + nêu 2 chiều + điều kiện kích hoạt.

C. "trading_scenarios": mảng tối đa 3 kịch bản, mỗi kịch bản gồm:
   - "horizon": "ngắn hạn" / "trung hạn" / "dài hạn"
   - "condition": "Nếu [X] xảy ra..."
   - "market_pricing": "...thị trường định giá theo hướng [Y]..."
   - "key_risk": "Rủi ro chính là [Z]."
   - "action_plan": kế hoạch hành động (TUYỆT ĐỐI KHÔNG có câu lệnh mua/bán trực tiếp như "nên long/short").

CHỈ TRẢ VỀ JSON HỢP LỆ (không text ngoài):
{{"3": {{"title": "Phân tích năng lượng & tác động lên EUA", "analysis_blocks": [{{"heading": "...", "content": "..."}}], "correlation_analysis": {{"gas_coal_power": "...", "eua_conclusion": "..."}}, "trading_scenarios": [{{"horizon": "...", "condition": "...", "market_pricing": "...", "key_risk": "...", "action_plan": "..."}}]}}}}"""


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
Quy tắc BẮT BUỘC:
- Mục này LUÔN XUẤT HIỆN trong báo cáo.
- Dựa vào KHUNG PHÂN TÍCH bên trên, kiểm tra từng mối liên kết:
    A Đầu tiên: quét dữ liệu giá để xác định nhóm nào có biến động đáng kể (>0.5% hoặc có tin tức hỗ trợ).
    B Sau đó: kiểm tra xem biến động đó có tạo ra chuỗi lan truyền sang EUA không.
    C. Nếu có tín hiệu LAN TRUYỀN: viết phân tích liên kết chéo có số liệu và chuỗi logic (lưu ý: không liệt kê chỉ đơn thuần, phải có kết luận chiều EUA).
    D. Nếu tín hiệu mâu thuẫn nhau: ghi "tín hiệu hỗn hợp" + giải thích cụ thể.
    E. Nếu không có biến động đáng kể: ghi đúng câu "Không có tín hiệu liên thị trường mới." — KHÔNG bịa liên kết gượng ép.

CHỈ TRẢ VỀ JSON HỢP LỆ (không text ngoài):
{{"5": {{"title": "Tín hiệu liên thị trường", "text": "..."}}}}"""


def _prompt_section7(news_text: str, target_date: str) -> str:
    return f"""Bạn là chuyên gia phân tích thị trường năng lượng & carbon châu Âu.
Ngày báo cáo: {target_date}

TIN TỨC LIÊN QUAN (eua_ets, geopolitics):
{news_text}

YÊU CẦU: Viết MỤC 7 — QUAN ĐIỂM TRÁI CHIỀU ĐÁNG CHÚ Ý.
Quy tắc:
- CHỈ viết khi có quan điểm contrarian có cơ sở dữ liệu từ nguồn chuẩn (nhà phân tích, báo cáo, chuyên gia).
- Kèm nguồn cụ thể và luận điểm chính.
- Nếu không có: set "has_content" = false và "text" = "Không có quan điểm trái chiều có cơ sở trong kỳ này."

CHỈ TRẢ VỀ JSON HỢP LỆ (không text ngoài):
{{"7": {{"title": "Quan điểm trái chiều đáng chú ý", "has_content": true/false, "text": "..."}}}}"""


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
    prices = await get_prices_for_report(session, target_date)
    chart_data = await get_historical_ohlc_for_report(session, "EUA", target_date)
    news_by_topic, sources = await get_news_for_report(session, target_date)

    prices_text = _summarize_prices(prices)
    eua_trend = _eua_trend_summary(chart_data)
    eua_session_range = _eua_session_range_summary(chart_data)
    gasoil_crack_spread = _gasoil_crack_spread_summary(prices) or (
        "Không có dữ liệu Gasoil hoặc Brent trong phiên này — không tính được crack spread."
    )
    prev_events_text = _format_prev_events(await get_previous_report_events(session, target_date))

    # Nhận xét ngắn về biểu đồ EUA cho Mục 2
    eua_prices = [p for p in prices if p["code"] == "EUA"]
    eua_chart_comment = ""
    if eua_prices and chart_data:
        latest_close = eua_prices[0]["close"]
        prev_close = chart_data[-2]["close"] if len(chart_data) >= 2 else None
        if prev_close:
            delta = latest_close - prev_close
            direction = "tăng" if delta > 0 else "giảm"
            eua_chart_comment = (
                f"EUA phiên {target_date}: đóng cửa {latest_close:.2f} EUR/tCO2, "
                f"{direction} {abs(delta):.2f} so với phiên trước ({prev_close:.2f}). {eua_trend}"
            )
        else:
            eua_chart_comment = eua_trend

    # ── 2. Gọi LLM từng mục song song (tuần tự để tránh rate limit) ──
    content: Dict[str, Any] = {}

    SECTIONS = [
        ("1", _prompt_section1(
            _filter_news_for_section(news_by_topic, "1"),
            prices_text, eua_trend, eua_session_range, target_date
        )),
        ("3", _prompt_section3(
            _filter_news_for_section(news_by_topic, "3"),
            prices_text, eua_trend, gasoil_crack_spread, target_date
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
            _filter_news_for_section(news_by_topic, "7"),
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
        "3": {"title": "Phân tích năng lượng & tác động lên EUA",
              "analysis_blocks": [{"heading": "Diễn biến chính", "content": "Không có dữ liệu."}],
              "correlation_analysis": {"gas_coal_power": "Không có dữ liệu.", "eua_conclusion": "Không có dữ liệu."},
              "trading_scenarios": []},
        "4": {"title": "Cập nhật tín chỉ carbon & CBAM", "bullets": ["Không có diễn biến trọng yếu."]},
        "5": {"title": "Tín hiệu liên thị trường", "text": "Không có tín hiệu liên thị trường mới."},
        "7": {"title": "Quan điểm trái chiều đáng chú ý", "has_content": False, "text": "Không có quan điểm trái chiều có cơ sở trong kỳ này."},
        "8": {"title": "Lịch sự kiện 7 ngày tới", "events": []},
        "biz": {"title": "Gợi ý kinh doanh & giải pháp cho SIM", "short_term": [], "long_term": []},
    }

    for section_key, prompt in SECTIONS:
        logger.info(f"[REPORT] Đang sinh mục {section_key}...")
        raw = await _call_llm(prompt)  # async — không block event loop
        parsed = _extract_json(raw) if raw else None

        if parsed and section_key in parsed:
            section_data = parsed[section_key]
            logger.info(f"[REPORT] Mục {section_key} OK.")
        else:
            logger.warning(f"[REPORT] Mục {section_key} thất bại, dùng fallback. Raw: {raw[:200] if raw else 'None'}")
            section_data = FALLBACKS[section_key]

        if section_key == "7":
            # Mục 7 ĐƯỢC PHÉP BỎ khi không có quan điểm trái chiều có cơ sở —
            # không set content["7"] thay vì luôn hiện placeholder rỗng.
            # ReportDocument.tsx đã guard render bằng `report.content["7"] &&`.
            if section_data.get("has_content"):
                content["7"] = section_data
        else:
            content[section_key] = section_data

    # ── 3. Bổ sung Mục 2 (Bảng giá + biểu đồ) ───────────────────────
    content["2"] = {
        "title": "Bảng giá nhanh",
        "price_timestamp": f"Giá chốt phiên {target_date} (nguồn: Barchart EOD)",
        "prices": prices,
        "chart_data": chart_data,
        "chart_comment": eua_chart_comment,
    }

    cited_articles = _collect_cited_articles(news_by_topic)
    content["9"] = {
        "title": "Nguồn tham khảo",
        "bullets": [f"[{a['source']}] {a['title']} — {a['url']}" for a in cited_articles]
                   if cited_articles else ["Không có nguồn tin tức trong 48h qua."],
    }

    return content
