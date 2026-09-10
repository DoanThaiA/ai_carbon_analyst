"""
crawl_prices/market_events_fetcher.py
──────────────────────────────────────
Fetch dữ liệu thực từ 2 nguồn công khai có thể lấy được cho Mục 8:

  1. EIA Weekly Petroleum Status Report — CSV tại ir.eia.gov/wpsr/table1.csv
     - Crude Oil Commercial (excl SPR): số mới nhất và delta so tuần trước
     - Gasoline, Distillate: số liệu tổng quan
     - Trả về dict với ngày báo cáo và các số liệu chính

  2. Baker Hughes North America Rig Count — Excel tại static URL trên rigcount.bakerhughes.com
     - Lấy sheet đầu tiên, hàng gần nhất: tuần kết thúc, US Total, US Oil, US Gas
     - Trả về dict với số liệu rig count mới nhất

KHÔNG hỗ trợ (loại bỏ khỏi pipeline):
  - API Crude Inventory → URL 404, yêu cầu membership
  - EUA Auction EEX    → dữ liệu nằm trong JS widget, không thể fetch bằng HTTP thuần

Sử dụng httpx async (đã có trong requirements), không cần dependency mới.
Timeout 30s mỗi request; trả về None nếu lỗi (graceful degradation).
"""

import io
import logging
from datetime import datetime, date
from typing import Optional, Dict, Any

import httpx

logger = logging.getLogger(__name__)

# URL cố định — EIA dùng ir.eia.gov (CDN, nhanh hơn www.eia.gov cho file tĩnh)
_EIA_CSV_URL = "https://ir.eia.gov/wpsr/table1.csv"

# Baker Hughes: URL static file "New Report" — file Excel chứa data từ Aug 2025 trở đi
# URL này được hardcode trên trang rigcount.bakerhughes.com/na-rig-count
# File: "North America Rig Count Report - New Report"
_BH_EXCEL_URL = "https://rigcount.bakerhughes.com/static-files/2da8181e-4b1c-4f75-9ad8-44854f4fc106"

_HTTP_TIMEOUT = 30.0
_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "*/*",
}

# Akamai (WAF của rigcount.bakerhughes.com) chặn UA giả trình duyệt (thiếu bộ header
# đi kèm như sec-ch-ua/Accept-Language → bị coi là bot giả mạo) nhưng cho qua UA
# "trần" không giả trình duyệt — dùng header riêng, KHÔNG dùng chung _HTTP_HEADERS.
_BH_HTTP_HEADERS = {
    "User-Agent": "curl/8.0",
    "Accept": "*/*",
}


async def fetch_eia_weekly_inventory() -> Optional[Dict[str, Any]]:
    """
    Fetch EIA Weekly Petroleum Status Report — Table 1 CSV.

    Returns dict với các field:
      - report_date: str  "YYYY-MM-DD" (ngày phát hành báo cáo, parse từ header CSV)
      - week_ending: str  "YYYY-MM-DD" (tuần kết thúc số liệu, parse từ cột đầu)
      - crude_commercial_mbbl: float  (Commercial crude excl SPR, triệu thùng)
      - crude_delta_mbbl: float       (delta so tuần trước, triệu thùng)
      - crude_delta_pct: float        (delta %, âm = giảm)
      - gasoline_mbbl: float
      - distillate_mbbl: float
      - total_stocks_excl_spr_mbbl: float
      - raw_summary: str              (chuỗi tóm tắt để inject vào prompt)

    Trả về None nếu fetch/parse lỗi.
    """
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(_EIA_CSV_URL, headers=_HTTP_HEADERS)
            resp.raise_for_status()
            content = resp.text
    except Exception as exc:
        logger.warning("[EIA-FETCH] Không lấy được CSV: %s", exc)
        return None

    return _parse_eia_csv(content)


def _parse_eia_csv(content: str) -> Optional[Dict[str, Any]]:
    """Parse nội dung CSV Table 1 EIA WPSR."""
    try:
        lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
        if not lines:
            return None

        # Dòng 1 là header: "STUB_1","8/28/26","8/21/26","Difference","Percent Change",...
        header_parts = [p.strip('"') for p in lines[0].split(",")]
        if len(header_parts) < 5:
            logger.warning("[EIA-PARSE] Header CSV không đúng định dạng: %s", lines[0])
            return None

        # Cột 1 = ngày mới nhất, cột 2 = ngày trước
        def _parse_us_date(s: str) -> Optional[date]:
            """Parse '8/28/26' → date(2026, 8, 28)"""
            try:
                return datetime.strptime(s.strip(), "%m/%d/%y").date()
            except ValueError:
                return None

        week_ending_date = _parse_us_date(header_parts[1])  # cột 2 (index 1)
        week_prev_date = _parse_us_date(header_parts[2])     # cột 3 (index 2)

        # Map tên dòng → index trong lines[]
        row_map: Dict[str, list] = {}
        for line in lines[1:]:
            parts = [p.strip('"') for p in line.split(",")]
            if len(parts) >= 5:
                row_name = parts[0].strip()
                row_map[row_name] = parts

        def _get_val(row_name: str, col_idx: int = 1) -> Optional[float]:
            row = row_map.get(row_name)
            if not row or col_idx >= len(row):
                return None
            try:
                return float(row[col_idx].replace(",", ""))
            except (ValueError, TypeError):
                return None

        # Rows chính từ Table 1 EIA
        crude_commercial = _get_val("Commercial (Excluding SPR)", 1)
        crude_prev       = _get_val("Commercial (Excluding SPR)", 2)
        crude_delta      = _get_val("Commercial (Excluding SPR)", 3)
        crude_delta_pct  = _get_val("Commercial (Excluding SPR)", 4)
        gasoline         = _get_val("Total Motor Gasoline", 1)
        distillate       = _get_val("Distillate Fuel Oil", 1)
        total_excl_spr   = _get_val("Total Stocks (Excluding SPR)", 1)

        if crude_commercial is None:
            logger.warning("[EIA-PARSE] Không tìm thấy dòng 'Commercial (Excluding SPR)' trong CSV")
            return None

        # Tạo chuỗi tóm tắt để inject vào prompt
        week_end_str = week_ending_date.strftime("%d/%m/%Y") if week_ending_date else "N/A"
        direction = "tăng" if (crude_delta or 0) > 0 else "giảm"
        delta_str = f"{abs(crude_delta or 0):.3f} triệu thùng ({crude_delta_pct:+.1f}%)" if crude_delta_pct is not None else "N/A"

        summary_parts = [
            f"EIA WPSR (tuần kết thúc {week_end_str}):",
            f"  Dầu thô thương mại (excl SPR): {crude_commercial:.3f} triệu thùng"
            f" — {direction} {delta_str} so tuần trước ({crude_prev:.3f} triệu thùng)" if crude_prev else
            f"  Dầu thô thương mại (excl SPR): {crude_commercial:.3f} triệu thùng",
        ]
        if gasoline is not None:
            summary_parts.append(f"  Xăng (Motor Gasoline): {gasoline:.3f} triệu thùng")
        if distillate is not None:
            summary_parts.append(f"  Distillate (Diesel/Heating Oil): {distillate:.3f} triệu thùng")
        if total_excl_spr is not None:
            summary_parts.append(f"  Tổng tồn kho (excl SPR): {total_excl_spr:.3f} triệu thùng")

        raw_summary = "\n".join(summary_parts)

        result = {
            "week_ending": week_ending_date.isoformat() if week_ending_date else None,
            "crude_commercial_mbbl": crude_commercial,
            "crude_prev_mbbl": crude_prev,
            "crude_delta_mbbl": crude_delta,
            "crude_delta_pct": crude_delta_pct,
            "gasoline_mbbl": gasoline,
            "distillate_mbbl": distillate,
            "total_stocks_excl_spr_mbbl": total_excl_spr,
            "raw_summary": raw_summary,
        }
        logger.info("[EIA-FETCH] OK — tuần kết thúc %s, crude commercial %.3f Mbbls",
                    week_end_str, crude_commercial)
        return result

    except Exception as exc:
        logger.warning("[EIA-PARSE] Lỗi parse CSV: %s", exc)
        return None


async def fetch_baker_hughes_rig_count() -> Optional[Dict[str, Any]]:
    """
    Fetch Baker Hughes North America Rig Count — Excel file.

    Returns dict với các field:
      - week_ending: str "YYYY-MM-DD" (ngày tuần kết thúc)
      - us_total: int    (tổng giàn khoan Mỹ)
      - us_oil: int      (giàn khoan dầu)
      - us_gas: int      (giàn khoan khí)
      - us_misc: int     (miscellaneous)
      - canada_total: int (giàn khoan Canada nếu có)
      - raw_summary: str  (chuỗi tóm tắt để inject vào prompt)

    Trả về None nếu fetch/parse lỗi.
    """
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(_BH_EXCEL_URL, headers=_BH_HTTP_HEADERS)
            resp.raise_for_status()
            content_bytes = resp.content
    except Exception as exc:
        logger.warning("[BH-FETCH] Không tải được file Excel Baker Hughes: %s", exc)
        return None

    return _parse_bh_excel(content_bytes)


def _parse_bh_excel(content_bytes: bytes) -> Optional[Dict[str, Any]]:
    """Parse Excel Baker Hughes Rig Count.

    Định dạng file kể từ ~09/2026 ("New Report"): nhiều sheet
    (NAM Summary/Breakdown/Yearly/Quarterly/Monthly/Weekly); số liệu tuần mới nhất
    theo Oil/Gas/Total nằm ở sheet "NAM Breakdown", pivot theo CỘT (mỗi cột 1 mốc
    thời gian — cột index 2 là tuần hiện tại, index 3 là tuần trước — thay vì mỗi
    HÀNG 1 tuần như định dạng cũ). Trong sheet đó có 3 block lặp lại theo hàng
    (nhãn ở cột index 1): "Location" (Inland Waters/Land/Offshore/United States/
    Canada), "DrillFor" (Gas/Oil/Miscellaneous/United States/Canada), "Trajectory"
    (Directional/Horizontal/Vertical/United States) — chỉ lấy occurrence ĐẦU TIÊN
    của mỗi nhãn (thuộc block "Location"/"DrillFor" cho US) vì nhãn "United States"/
    "Canada"/"Gas"/"Oil"/"Miscellaneous" lặp lại ở nhiều block.
    """
    try:
        import openpyxl  # lazy import — chỉ cần khi gọi hàm này
    except ImportError:
        logger.warning("[BH-PARSE] openpyxl chưa cài — không parse được Excel BH. "
                       "Chạy: pip install openpyxl")
        return None

    try:
        wb = openpyxl.load_workbook(io.BytesIO(content_bytes), read_only=True, data_only=True)
        if "NAM Breakdown" not in wb.sheetnames:
            logger.warning(
                "[BH-PARSE] Không tìm thấy sheet 'NAM Breakdown' trong Excel BH "
                "(sheets hiện có: %s) — định dạng file có thể đã đổi", wb.sheetnames,
            )
            wb.close()
            return None
        ws = wb["NAM Breakdown"]

        CUR_COL = 2  # cột giá trị tuần hiện tại (cột index 1 là nhãn hàng)

        week_ending_date: Optional[date] = None
        us_total: Optional[int] = None
        canada_total: Optional[int] = None
        us_gas: Optional[int] = None
        us_oil: Optional[int] = None
        us_misc: Optional[int] = None

        def _to_int(val: Any) -> Optional[int]:
            try:
                return int(float(str(val).replace(",", "")))
            except (ValueError, TypeError):
                return None

        for row in ws.iter_rows(values_only=True):
            if len(row) <= CUR_COL:
                continue
            label = str(row[1]).strip() if row[1] is not None else ""

            if label in ("Location", "DrillFor", "Trajectory") and week_ending_date is None:
                for fmt in ("%d/%b/%y", "%m/%d/%Y", "%Y-%m-%d"):
                    try:
                        week_ending_date = datetime.strptime(str(row[CUR_COL]).strip(), fmt).date()
                        break
                    except (ValueError, TypeError):
                        continue
            elif label == "United States" and us_total is None:
                us_total = _to_int(row[CUR_COL])
            elif label == "Canada" and canada_total is None:
                canada_total = _to_int(row[CUR_COL])
            elif label == "Gas" and us_gas is None:
                us_gas = _to_int(row[CUR_COL])
            elif label == "Oil" and us_oil is None:
                us_oil = _to_int(row[CUR_COL])
            elif label == "Miscellaneous" and us_misc is None:
                us_misc = _to_int(row[CUR_COL])

        wb.close()

        if us_total is None:
            logger.warning(
                "[BH-PARSE] Không đọc được 'US Total' từ sheet 'NAM Breakdown' "
                "(định dạng file có thể đã đổi)"
            )
            return None

        week_end_str = week_ending_date.strftime("%d/%m/%Y") if week_ending_date else "N/A"
        parts = [f"Baker Hughes NA Rig Count (tuần kết thúc {week_end_str}):"]
        if us_total is not None:
            parts.append(f"  Tổng giàn khoan Mỹ: {us_total:,}")
        if us_oil is not None:
            parts.append(f"  Dầu: {us_oil:,}")
        if us_gas is not None:
            parts.append(f"  Khí: {us_gas:,}")
        if canada_total is not None:
            parts.append(f"  Canada (tổng): {canada_total:,}")
        raw_summary = "\n".join(parts)

        result = {
            "week_ending": week_ending_date.isoformat() if week_ending_date else None,
            "us_total": us_total,
            "us_oil": us_oil,
            "us_gas": us_gas,
            "us_misc": us_misc,
            "canada_total": canada_total,
            "raw_summary": raw_summary,
        }
        logger.info("[BH-FETCH] OK — tuần kết thúc %s, US Total rigs: %s", week_end_str, us_total)
        return result

    except Exception as exc:
        logger.warning("[BH-PARSE] Lỗi parse Excel BH: %s", exc)
        return None


def format_eia_outcome(eia_data: Dict[str, Any]) -> str:
    """
    Chuyển dict EIA thành chuỗi outcome ngắn gọn để điền vào field 'outcome'
    của sự kiện EIA trong Mục 8.

    Ví dụ: "Crude commercial: 424.460 Mbbl (↓ -4.450 Mbbl, -1.0% so tuần trước).
            Gasoline: 205.669 Mbbl. Distillate: 104.187 Mbbl."
    """
    crude = eia_data.get("crude_commercial_mbbl")
    delta = eia_data.get("crude_delta_mbbl")
    pct   = eia_data.get("crude_delta_pct")
    gas   = eia_data.get("gasoline_mbbl")
    dist  = eia_data.get("distillate_mbbl")
    week  = eia_data.get("week_ending", "")

    if crude is None:
        return "Có dữ liệu EIA nhưng parse lỗi."

    arrow = "↑" if (delta or 0) > 0 else "↓"
    delta_str = f"{arrow} {delta:+.3f} Mbbl ({pct:+.1f}%)" if delta is not None and pct is not None else ""
    parts = [f"Crude commercial: {crude:.3f} Mbbl {delta_str}".strip()]
    if gas is not None:
        parts.append(f"Gasoline: {gas:.3f} Mbbl")
    if dist is not None:
        parts.append(f"Distillate: {dist:.3f} Mbbl")
    week_str = f" (tuần kết thúc {week})" if week else ""
    return ". ".join(parts) + "." + week_str


def format_bh_outcome(bh_data: Dict[str, Any]) -> str:
    """
    Chuyển dict Baker Hughes thành chuỗi outcome cho Mục 8.
    Ví dụ: "US Total: 585 rigs (Oil: 472, Gas: 103). Tuần kết thúc 05/09/2026."
    """
    total = bh_data.get("us_total")
    oil   = bh_data.get("us_oil")
    gas   = bh_data.get("us_gas")
    week  = bh_data.get("week_ending", "")

    if total is None:
        return "Có dữ liệu Baker Hughes nhưng parse lỗi."

    detail = ""
    sub = []
    if oil is not None:
        sub.append(f"Oil: {oil:,}")
    if gas is not None:
        sub.append(f"Gas: {gas:,}")
    if sub:
        detail = f" ({', '.join(sub)})"

    week_str = ""
    if week:
        try:
            w = datetime.strptime(week, "%Y-%m-%d").date()
            week_str = f" — tuần kết thúc {w.strftime('%d/%m/%Y')}"
        except ValueError:
            week_str = f" — tuần kết thúc {week}"

    return f"US Total: {total:,} rigs{detail}{week_str}."
