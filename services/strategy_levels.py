"""Parser mức giá của thẻ chiến lược kịch bản ("**Entry:** ...\n**Mục tiêu:** ...\n**Quản trị rủi ro:** ...").

Dùng chung cho Jack QC (services/report_qc.py — re-export cùng tên) và đối chiếu dự báo
(services/forecast_review.py); tách riêng để report_generator dùng được mà không import vòng qua report_qc.
"""
import re
from typing import Any, Dict, List, Optional

# Port 1-1 của parseStrategy/priceNums/splitBuySell/parseSignalLevels trong
# frontend/src/components/ReportDocument.tsx — QC kiểm ĐÚNG những mức giá mà thẻ
# "KHUYẾN NGHỊ VỊ THẾ" sẽ hiển thị. Sửa parser bên frontend thì phải sửa cả ở đây.
_STRATEGY_LINE_RE = re.compile(r"^\*\*([^*]+?)\*\*\s*:?\s*(.*)$")


def parse_strategy(text: Any) -> Optional[List[tuple]]:
    """"**Entry:** ...\n**Mục tiêu:** ..." -> [(label, body)], None nếu có dòng không đúng định dạng."""
    if not isinstance(text, str) or not text.strip():
        return None
    parts = []
    for line in (l.strip() for l in re.split(r"\n+", text)):
        if not line:
            continue
        m = _STRATEGY_LINE_RE.match(line)
        if not m:
            return None
        parts.append((re.sub(r":\s*$", "", m.group(1)).strip(), m.group(2).strip()))
    return parts or None


def _price_nums(text: str) -> List[float]:
    clean = text.replace("**", "")
    clean = re.sub(r"EUR\s*/\s*tCO[₂2]", "", clean, flags=re.IGNORECASE)
    clean = re.sub(
        r"\d+(?:[.,]\d+)?\s*(?:%|phiên|ngày|tuần|tháng|sessions?\b|days?\b|weeks?\b|months?\b)", "", clean,
        flags=re.IGNORECASE,
    )
    clean = re.sub(r"tCO[₂2]", "", clean, flags=re.IGNORECASE)
    nums = [float(m.replace(",", ".", 1)) for m in re.findall(r"\d+(?:[.,]\d+)?", clean)]
    return [n for n in nums if n >= 10]


def _split_buy_sell(body: str) -> tuple:
    buy: List[float] = []
    sell: List[float] = []
    free: List[float] = []
    for c in (x.strip() for x in re.split(r";|,\s+|\.\s+|\s/\s", body)):
        nums = _price_nums(c) if c else []
        if not nums:
            continue
        is_buy, is_sell = bool(re.search(r"mua|\bbuy", c, re.I)), bool(re.search(r"bán|\bsell", c, re.I))
        if is_buy and not is_sell:
            buy += nums
        elif is_sell and not is_buy:
            sell += nums
        else:
            free += nums
    return buy, sell, free


def parse_signal_levels(direction: str, entry_body: str, target_body: str, risk_body: str) -> Dict[str, Any]:
    e_buy, e_sell, e_free = _split_buy_sell(entry_body)
    entry_buy, entry_sell = e_buy[:2], e_sell[:2]
    if not entry_buy and not entry_sell and e_free:
        if direction == "giảm":
            entry_sell = e_free[:2]
        else:
            entry_buy = e_free[:2]

    t_buy, t_sell, t_free = _split_buy_sell(target_body)
    target_buy = t_buy[0] if t_buy else None
    target_sell = t_sell[0] if t_sell else None
    if target_buy is None and target_sell is None and t_free:
        if direction == "giảm":
            target_sell = t_free[0]
        elif direction == "tăng":
            target_buy = t_free[0]
        else:
            target_buy = t_free[0]
            target_sell = t_free[1] if len(t_free) > 1 else None

    def _num(m):
        return float(m.group(1).replace(",", ".", 1)) if m else None

    stop_lower = _num(re.search(r"(?:dưới|\bbelow\b|\bunder\b)[^\d]{0,15}(\d+(?:[.,]\d+)?)", risk_body, re.I))
    stop_upper = _num(re.search(r"(?:trên|\babove\b|\bover\b)[^\d]{0,15}(\d+(?:[.,]\d+)?)", risk_body, re.I))
    if stop_lower is None and stop_upper is None:
        nums = _price_nums(risk_body)
        if nums:
            if direction == "tăng":
                stop_lower = min(nums)
            elif direction == "giảm":
                stop_upper = max(nums)
            else:
                stop_lower = min(nums)
                if len(nums) > 1:
                    stop_upper = max(nums)
    return {
        "entry_buy": entry_buy, "entry_sell": entry_sell, "target_buy": target_buy,
        "target_sell": target_sell, "stop_lower": stop_lower, "stop_upper": stop_upper,
    }


def is_breakout_entry(direction: str, entry: str) -> bool:
    """Lệnh dừng (vào khi giá PHÁ mức) chứ không phải lệnh giới hạn. Chỉ nhận cụm có điều kiện kích hoạt
    ("mua khi đóng cửa trên X", "bán nếu thủng X", "phá đỉnh", "breakout") — chữ "trên" đứng lẻ trong
    "Mua quanh 87,00–87,50 (trên hỗ trợ 86,90)" KHÔNG làm lệnh thành lệnh phá vỡ (bản 15/10 từng bị chấm sai vì vậy).
    Bản port: isBreakoutEntry trong frontend/src/components/ReportDocument.tsx (nhãn "Mua/Bán khi phá" của thẻ
    KHUYẾN NGHỊ VỊ THẾ) — sửa bên nào phải sửa cả bên kia."""
    word = r"trên|vượt|phá(?:\s+(?:lên|đỉnh))?" if direction == "tăng" else r"dưới|thủng|phá(?:\s+(?:xuống|đáy))?"
    if re.search(rf"(?:khi|nếu|sau khi|lúc)\s+(?:giá\s+)?(?:EUA\s+)?(?:đóng cửa\s+)?(?:{word})", entry, re.I):
        return True
    return bool(re.search(r"breakout|break(?:s)?\s+(?:above|below)|buy stop|sell stop|lệnh dừng|phá (?:đỉnh|đáy)", entry, re.I))


def strategy_levels(sc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Mức giá (entry/mục tiêu/cắt lỗ) của 1 kịch bản tăng/giảm — None nếu không tách được."""
    direction = sc.get("direction")
    parts = parse_strategy(sc.get("trading_strategy"))
    if direction not in ("tăng", "giảm") or not parts:
        return None

    def _body(pattern: str) -> Optional[str]:
        return next((b.replace("*", "") for lbl, b in parts if re.search(pattern, lbl, re.I)), None)

    entry, target, risk = _body("entry"), _body("mục tiêu|target"), _body("quản trị rủi ro|cắt lỗ|stop|risk")
    if entry is None or risk is None:
        return None
    levels = parse_signal_levels(direction, entry, target or "", risk)
    # Lệnh chờ PHÁ VỠ ("mua khi đóng cửa trên X" / "bán khi thủng X") khác lệnh giới hạn ("mua quanh X"):
    # giá mở gap qua vùng Entry theo chiều có lợi chỉ khớp lệnh giới hạn (forecast_review.simulate_trade).
    levels["entry_breakout"] = is_breakout_entry(direction, entry)
    return levels

