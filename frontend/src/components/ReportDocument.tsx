"use client";

import { Fragment, useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import clsx from "clsx";
import {
  Clock, CalendarRange, Compass, TrendingUp, TrendingDown, Minus, Target, AlertTriangle,
  Sparkles, LineChart, BarChart3, Newspaper, Link2,
  Crosshair, ChevronDown, ChevronUp, Info, ShieldCheck, ExternalLink, Menu, X, Trash2,
  Leaf, Calendar, RefreshCw, ArrowLeftRight, CalendarClock, ClipboardCheck
} from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { format as formatDateFns } from "date-fns";
import type { CbamEstimate, ForecastReview, HedgeAction, OpenPosition, Report } from "@/lib/types";
import PriceIndexPanel from "@/components/PriceIndexPanel";
import { CbamEstimateCard } from "@/components/CbamEstimateCard";
import { ForecastReviewCard } from "@/components/ForecastReviewCard";
import { dateFnsLocale } from "@/i18n/dateLocale";

// Hàm dịch của namespace "reportDoc" — truyền xuống các hàm thuần (không gọi được hook).
type ReportT = ReturnType<typeof useTranslations>;

// Giá trị enum trong content (horizon/direction/probability/impact/heading) LUÔN là
// tiếng Việt, kể cả trong content_en (backend cố ý không dịch để mọi phép so sánh
// === ở dưới giữ nguyên) — chỉ đổi nhãn khi HIỂN THỊ. Giá trị lạ → hiện nguyên văn.
const ENUM_KEYS: Record<string, string> = {
  "ngắn hạn": "shortTerm",
  "trung hạn": "midTerm",
  "dài hạn": "longTerm",
  "tăng": "up",
  "giảm": "down",
  "đi ngang": "sideways",
  "trung lập": "neutral",
  "Cao": "high",
  "Trung bình": "medium",
  "Trung": "mediumShort",
  "Thấp": "low",
  "Phân tích": "headingAnalysis",
  "Cần theo dõi": "headingWatch",
  "Diễn biến chính": "headingKeyDev",
};

function useEnumLabel() {
  const t = useTranslations("reportDoc");
  return (value: string | null | undefined): string => {
    if (!value) return value ?? "";
    const key = ENUM_KEYS[value];
    return key ? t(`enum.${key}`) : value;
  };
}

// "YYYY-MM-DD" -> "dd/MM" (nhãn trục X) — tránh phụ thuộc date-fns chỉ cho 1 format đơn giản.
function formatShortDate(dateStr: string) {
  const parts = dateStr.split("-");
  return parts.length === 3 ? `${parts[2]}/${parts[1]}` : dateStr;
}

// Backend luôn trả 4 số thập phân (vd "72.4000") — rút gọn còn 2 số khi hiển thị
// trong bảng giá để chuỗi số ngắn lại, đủ nằm gọn trong cột hẹp trên mobile mà
// không cần ngắt dòng giữa chừng. Không đổi dữ liệu gốc, chỉ rút gọn phần hiển thị.
// Giá trong DB/nội dung lưu dạng "1,386.7500" — hiển thị theo quy ước số của ngôn ngữ đang xem
// (tiếng Việt: "1.386,75", cùng kiểu với phần văn bản của báo cáo; tiếng Anh: "1,386.75").
function formatCompactPriceNumber(numStr: string, locale?: string): string {
  const n = Number(numStr.replace(/,/g, ""));
  if (Number.isNaN(n)) return numStr;
  // Giá nhỏ (Henry Hub ~3,2 USD/MMBtu): 3 chữ số thập phân — 2 chữ số không đủ để người đọc tự kiểm Δ%.
  const digits = Math.abs(n) < 10 ? 3 : 2;
  return n.toLocaleString(locale === "en" ? "en-US" : "vi-VN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

// Δ ngày/Δ tuần lưu dạng "+1.23% (+1.62)" → tiếng Việt "+1,23% (+1,62)".
function formatDelta(delta: string, locale?: string): string {
  return locale === "en" ? delta : delta.replace(/(\d)\.(\d)/g, "$1,$2");
}

// Các mục trong menu hamburger điều hướng (id khớp với <section id> trong báo cáo).
// labelKey: khoá dịch trong reportDoc.nav.*
const REPORT_SECTIONS = [
  { id: "section-highlights", labelKey: "highlights" },
  { id: "section-market", labelKey: "market" },
  { id: "section-analysis", labelKey: "analysis" },
  { id: "section-news", labelKey: "news" },
  { id: "section-forecast-review", labelKey: "forecastReview" },
  { id: "section-sources", labelKey: "sources" },
] as const;

function formatFullDate(dateStr: string) {
  const parts = dateStr.split("-");
  return parts.length === 3 ? `${parts[2]}/${parts[1]}/${parts[0]}` : dateStr;
}

const HORIZON_META: Record<string, { icon: typeof Clock; accent: string; iconBg: string }> = {
  "ngắn hạn": { icon: Clock, accent: "border-l-warn", iconBg: "bg-warn-tint text-warn" },
  "trung hạn": { icon: CalendarRange, accent: "border-l-primary", iconBg: "bg-tint text-primary-dark" },
  "dài hạn": { icon: Compass, accent: "border-l-muted-light", iconBg: "bg-surface-alt text-muted-light" },
};

const DIRECTION_META: Record<string, { icon: typeof TrendingUp; className: string }> = {
  // Chiều giá: tăng xanh lá, giảm đỏ, đi ngang xanh dương (nhãn đặc, chữ trắng).
  // "Xác suất" KHÔNG dùng màu — chỉ là chữ thường (xem dòng "Nhận định" bảng Kịch bản).
  "tăng": { icon: TrendingUp, className: "bg-emerald-600 text-white border-emerald-600" },
  "giảm": { icon: TrendingDown, className: "bg-red-600 text-white border-red-600" },
  "đi ngang": { icon: Minus, className: "bg-blue-600 text-white border-blue-600" },
};

// Mũi tên + màu chiều giá của từng kịch bản trong thẻ KHUYẾN NGHỊ VỊ THẾ (Phần 2) — chỉ là
// cách hiển thị khác của cùng "direction" (tăng/giảm/đi ngang) đã có sẵn trong
// trading_scenarios, KHÔNG phải trường dữ liệu mới/suy diễn thêm.
const TREND_META: Record<string, { arrow: string; className: string }> = {
  "tăng": { arrow: "▲", className: "text-up" },
  "giảm": { arrow: "▼", className: "text-down" },
  "đi ngang": { arrow: "↔", className: "text-blue-700" },
};

// Nhãn tác động của từng SỰ KIỆN TIN TỨC trong "Diễn biến chính" lên cán cân
// cung–cầu EUA (mục này chỉ là tin tức, không phải diễn biến giá hợp đồng).
const IMPACT_META: Record<string, { arrow: string; label: string; badge: string; bar: string }> = {
  "tăng": { arrow: "▲", label: "Hỗ trợ EUA", badge: "text-up border-up/30 bg-up/10", bar: "border-l-up" },
  "giảm": { arrow: "▼", label: "Áp lực EUA", badge: "text-down border-down/30 bg-red-50", bar: "border-l-down" },
  "trung lập": { arrow: "●", label: "Chờ xác nhận", badge: "text-blue-700 border-blue-200 bg-blue-50", bar: "border-l-blue-400" },
};

// Mục 1: mỗi bullet mở đầu bằng 1 tag tự do do LLM đặt tên (EUA, Chính sách, Địa
// chính trị...) — không phải enum cố định nên không thể map tay từng giá trị.
// Băm tên tag thành 1 màu trong bảng màu cố định để CÙNG 1 tag luôn ra cùng màu
// giữa các bullet/báo cáo, còn tag khác nhau nhìn tách bạch nhau ngay.
const TAG_COLOR_PALETTE = [
  "bg-tint text-primary-dark border-primary/20",
  "bg-warn-tint text-warn border-warn/30",
  "bg-red-50 text-down border-down/30",
  "bg-indigo-50 text-indigo-700 border-indigo-200",
  "bg-sky-50 text-sky-700 border-sky-200",
  "bg-violet-50 text-violet-700 border-violet-200",
];

function tagColorClass(tag: string): string {
  if (!tag) return "bg-surface-alt text-muted-light border-border";
  let hash = 0;
  for (let i = 0; i < tag.length; i++) hash = (hash * 31 + tag.charCodeAt(i)) >>> 0;
  return TAG_COLOR_PALETTE[hash % TAG_COLOR_PALETTE.length];
}

// Nội dung từ backend đôi khi chứa markdown **bold** thô (đôi khi cả dấu ** lẻ, không cặp đôi)
// — render thành <strong> và luôn dọn sạch mọi dấu * còn sót lại thay vì hiện literal.
// tCO2 -> tCO₂ (số 2 viết dạng subscript)
const subCO2 = (t: string) => t.replace(/CO2/g, "CO\u2082");

function RichText({ text }: { text: string }) {
  if (!text) return null;
  text = subCO2(text);
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return (
    <>
      {parts.map((part, i) =>
        part.startsWith("**") && part.endsWith("**") ? (
          <strong key={i} className="font-semibold text-label">
            {part.slice(2, -2)}
          </strong>
        ) : (
          <span key={i}>{part.replace(/\*\*/g, "")}</span>
        )
      )}
    </>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// KHUYẾN NGHỊ VỊ THẾ (trước đây "TÍN HIỆU HÔM NAY") — thẻ chiến lược trading của phiên (kịch bản "ngắn hạn")
// ═══════════════════════════════════════════════════════════════════════════
// Backend (report_generator.py, mục C) sinh mỗi kịch bản gồm: direction (tăng/
// giảm/đi ngang), probability, condition, price_zone, key_risk và
// trading_strategy = 3 dòng "**Entry:** … \n**Mục tiêu:** … \n**Quản trị rủi
// ro:** …". Các mức giá nằm trong văn bản tự do nên được tách ở đây theo chiều
// giá (direction) — KHÔNG bịa số: mức nào không tách được thì hiện "—" hoặc
// rơi về văn bản gốc.

type Dir = "tăng" | "giảm" | "đi ngang";

// Các mức giá EUA (>= 10) trong 1 đoạn văn: bỏ %, "2 phiên/ngày/tuần/tháng", đơn vị.
function priceNums(text: string): number[] {
  const clean = text
    .replace(/\*\*/g, "")
    .replace(/EUR\s*\/\s*tCO[₂2]/gi, "")
    .replace(/\d+(?:[.,]\d+)?\s*(?:%|phiên|ngày|tuần|tháng|sessions?\b|days?\b|weeks?\b|months?\b)/gi, "")
    .replace(/tCO[₂2]/gi, "");
  return (clean.match(/\d+(?:[.,]\d+)?/g) ?? [])
    .map(m => parseFloat(m.replace(",", ".")))
    .filter(n => !Number.isNaN(n) && n >= 10);
}

// Tách thành mệnh đề theo ; , . / — dấu phẩy/chấm THẬP PHÂN (không theo sau bởi
// khoảng trắng) giữ nguyên.
const clauses = (t: string) => t.split(/;|,\s+|\.\s+|\s\/\s/).map(s => s.trim()).filter(Boolean);

// Gán số vào phía mua/bán theo từ khoá trong từng mệnh đề; mệnh đề không từ khoá
// → gom vào "free" để fallback theo chiều giá.
function splitBuySell(body: string) {
  const buy: number[] = [], sell: number[] = [], free: number[] = [];
  for (const c of clauses(body)) {
    const nums = priceNums(c);
    if (!nums.length) continue;
    // Bản tiếng Anh (content_en): "buy"/"sell" thay cho "mua"/"bán".
    const isBuy = /mua|\bbuy/i.test(c), isSell = /bán|\bsell/i.test(c);
    if (isBuy && !isSell) buy.push(...nums);
    else if (isSell && !isBuy) sell.push(...nums);
    else free.push(...nums);
  }
  return { buy, sell, free };
}

interface SignalLevels {
  entryBuy: number[]; entrySell: number[];      // vùng vào lệnh (1–2 số = khoảng)
  targetBuy?: number; targetSell?: number;      // chốt lời
  stopLower?: number; stopUpper?: number;       // cắt lỗ
}

function parseSignalLevels(
  dir: Dir, entryBody: string, targetBody: string, riskBody: string,
): SignalLevels {
  const e = splitBuySell(entryBody);
  let entryBuy = e.buy.slice(0, 2), entrySell = e.sell.slice(0, 2);
  if (!entryBuy.length && !entrySell.length && e.free.length) {
    if (dir === "giảm") entrySell = e.free.slice(0, 2);
    else entryBuy = e.free.slice(0, 2);
  }

  const t = splitBuySell(targetBody);
  let targetBuy = t.buy[0], targetSell = t.sell[0];
  if (targetBuy === undefined && targetSell === undefined && t.free.length) {
    if (dir === "giảm") targetSell = t.free[0];
    else if (dir === "tăng") targetBuy = t.free[0];
    else { targetBuy = t.free[0]; targetSell = t.free[1]; }
  }

  // Cắt lỗ: ưu tiên từ khoá "dưới"/"trên" ngay trước số; không có → theo chiều giá.
  // Bản tiếng Anh: "below"/"under" ↔ "dưới", "above"/"over" ↔ "trên".
  const below = riskBody.match(/(?:dưới|\bbelow\b|\bunder\b)[^\d]{0,15}(\d+(?:[.,]\d+)?)/i);
  const above = riskBody.match(/(?:trên|\babove\b|\bover\b)[^\d]{0,15}(\d+(?:[.,]\d+)?)/i);
  const num = (m: RegExpMatchArray | null) => (m ? parseFloat(m[1].replace(",", ".")) : undefined);
  let stopLower = num(below), stopUpper = num(above);
  if (stopLower === undefined && stopUpper === undefined) {
    const nums = priceNums(riskBody);
    if (nums.length) {
      if (dir === "tăng") stopLower = Math.min(...nums);
      else if (dir === "giảm") stopUpper = Math.max(...nums);
      else {
        stopLower = Math.min(...nums);
        if (nums.length > 1) stopUpper = Math.max(...nums);
      }
    }
  }
  return { entryBuy, entrySell, targetBuy, targetSell, stopLower, stopUpper };
}

// 84.8 → "84,80" (quy ước dấu phẩy thập phân như phần còn lại của báo cáo);
// giao diện tiếng Anh → "84.80".
function fmtPrice(n: number, locale?: string): string {
  const s = n.toFixed(2);
  return locale === "en" ? s : s.replace(".", ",");
}
// Số trong biểu đồ/dashboard EUA theo ngôn ngữ — trước dùng toFixed() thô nên trang 3 bản PDF tiếng Việt
// lẫn dấu chấm thập phân ("85.41", "6.0%") với dấu phẩy ở các trang khác.
function fnum(n: number, digits: number, locale?: string): string {
  const s = n.toFixed(digits);
  return locale === "en" ? s : s.replace(".", ",");
}
const fmtRange = (r: number[], locale?: string) => r.map(n => fmtPrice(n, locale)).join("–");
const mid = (r: number[]) => r.reduce((a, b) => a + b, 0) / r.length;

// In đậm + tô màu mọi con số trong văn bản (giá, %, số phiên…) để mắt bắt ngay.
function HighlightNumbers({ text }: { text: string }) {
  const parts = subCO2(stripMarkdown(text)).split(/(\d+(?:[.,]\d+)?(?:\s?[–-]\s?\d+(?:[.,]\d+)?)?%?)/g);
  return (
    <>
      {parts.map((p, i) =>
        i % 2 === 1
          ? <strong key={i} className="font-extrabold text-primary-dark tabular-nums">{p}</strong>
          : <span key={i}>{p}</span>
      )}
    </>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// KHUYẾN NGHỊ VỊ THẾ — gộp 3 kịch bản (ngắn/trung/dài hạn) vào 1 thẻ: hàng chỉ số →
// thang giá → bảng lệnh (Entry/Mục tiêu/Cắt lỗ/R:R) → phòng hộ CBAM. Mọi con số lấy
// thẳng từ dữ liệu báo cáo hoặc tính bằng công thức cố định của backend (ghi ở từng
// hàm) — không suy diễn thêm; mức nào không tách được thì hiện "—".
// ═══════════════════════════════════════════════════════════════════════════

// = report_consistency.ATR_SESSIONS / forecast_review.MIN_RR (báo cáo cũ không có rules.min_rr)
// / TB khối lượng 20 phiên của report_generator._eua_volume_summary.
const ATR_SESSIONS = 14;
const DEFAULT_MIN_RR = 1.5;
const VOL_AVG_SESSIONS = 20;

type Side = "buy" | "sell";
// limit: chờ giá về vùng · now: giá đang nằm trong vùng · breakout: lệnh dừng khi phá mức · scale: tích lũy/bán dần
type OrderKind = "limit" | "now" | "breakout" | "scale";

interface Leg {
  side: Side;
  kind: OrderKind;
  zone: number[];              // vùng Entry (1–2 số); lệnh phá vỡ: đúng 1 mức kích hoạt
  entry?: number;              // điểm giữa vùng / mức kích hoạt — gốc tính R:R và % so với giá
  target?: number;
  stop?: number;
  onClose: boolean;            // kích hoạt theo giá ĐÓNG CỬA ("bán khi đóng cửa dưới X")
  targetUnquantified: boolean; // "Mục tiêu: chưa đủ cơ sở định lượng" (dài hạn)
  risk?: number;
  reward?: number;
  rr?: number;
}

// Các trường của 1 kịch bản (content["3"].trading_scenarios — report_generator.py, mục C) mà thẻ dùng.
interface Scenario {
  horizon: string; direction?: string; probability?: string | null; condition?: string; price_zone?: string;
  key_risk?: string; trading_strategy?: string; action_plan?: string; hedge_note?: string;
}
// 1 phiên OHLC của content["2"].chart_data / dòng EUA của Bảng giá nhanh (content["2"].prices).
interface OhlcRow { date: string; open: number; high: number; low: number; close: number; volume?: number | null }
interface PriceRow { code?: string; close?: number; day_change_pct?: number | null; week_change_pct?: number | null }

interface HorizonPlan { horizon: string; sc: Scenario; dir: Dir; legs: Leg[] }

const PLAN_HORIZONS = ["ngắn hạn", "trung hạn", "dài hạn"];
const HORIZON_KEY: Record<string, "short" | "mid" | "long"> = { "ngắn hạn": "short", "trung hạn": "mid", "dài hạn": "long" };

const SCALE_RE: Record<Side, RegExp> = {
  buy: /tích l(?:ũy|uỹ)|gom dần|mua dần|mua rải|accumulat|scale in/i,
  sell: /bán dần|bán rải|hạ dần|distribut|scale out/i,
};
const UNQUANTIFIED_RE = /chưa đủ cơ sở|chưa định lượng|quantitative basis|not quantif|unquantif/i;

// Port services/strategy_levels.py::is_breakout_entry (+ cụm tiếng Anh của content_en): lệnh DỪNG ("bán khi đóng
// cửa dưới X", "phá đỉnh") khác lệnh giới hạn ("mua quanh X") — chữ "trên"/"dưới" đứng lẻ không tính.
function isBreakoutEntry(side: Side, entry: string): boolean {
  const word = side === "buy" ? "trên|vượt|phá(?:\\s+(?:lên|đỉnh))?" : "dưới|thủng|phá(?:\\s+(?:xuống|đáy))?";
  if (new RegExp(`(?:khi|nếu|sau khi|lúc)\\s+(?:giá\\s+)?(?:EUA\\s+)?(?:đóng cửa\\s+)?(?:${word})`, "i").test(entry)) return true;
  if ((side === "buy" ? /\bclos(?:e[sd]?|ing)\s+(?:above|over)\b/i : /\bclos(?:e[sd]?|ing)\s+(?:below|under)\b/i).test(entry)) return true;
  return /breakout|breaks?\s+(?:above|below)|buy stop|sell stop|lệnh dừng|phá (?:đỉnh|đáy)/i.test(entry);
}

// Mệnh đề Entry của 1 chân lệnh — bỏ mệnh đề của chân ngược chiều ("Mua quanh A, bán quanh B").
function legClauses(body: string, side: Side): string {
  const other = side === "buy" ? /bán|\bsell/i : /mua|\bbuy/i;
  return clauses(body).filter(c => !other.test(c)).join(", ");
}

function buildLeg(side: Side, lv: SignalLevels, entryBody: string, targetBody: string, last?: number): Leg {
  const zone = side === "buy" ? lv.entryBuy : lv.entrySell;
  const text = legClauses(entryBody, side);
  const breakout = zone.length > 0 && isBreakoutEntry(side, text);
  const inZone = last !== undefined && zone.length > 1 && last >= Math.min(...zone) && last <= Math.max(...zone);
  const kind: OrderKind = breakout ? "breakout" : SCALE_RE[side].test(text) ? "scale" : inZone ? "now" : "limit";
  // Lệnh phá vỡ: mức kích hoạt là số ĐẦU TIÊN ("bán khi đóng cửa dưới 81,50 (đáy 30 phiên 82,35)").
  const z = breakout ? zone.slice(0, 1) : zone;
  const entry = z.length ? mid(z) : undefined;
  const target = side === "buy" ? lv.targetBuy : lv.targetSell;
  const stop = side === "buy" ? lv.stopLower : lv.stopUpper;
  const reward = entry !== undefined && target !== undefined ? (side === "buy" ? target - entry : entry - target) : undefined;
  const risk = entry !== undefined && stop !== undefined ? (side === "buy" ? entry - stop : stop - entry) : undefined;
  return {
    side, kind, zone: z, entry, target, stop,
    onClose: breakout && /đóng cửa|\bclos(?:e|ing)/i.test(text),
    targetUnquantified: target === undefined && UNQUANTIFIED_RE.test(targetBody),
    risk, reward,
    rr: reward !== undefined && risk !== undefined && reward > 0 && risk > 0 ? reward / risk : undefined,
  };
}

function buildPlan(sc: Scenario, last?: number): HorizonPlan {
  const dir: Dir = sc.direction === "tăng" || sc.direction === "giảm" ? sc.direction : "đi ngang";
  const strategy = parseStrategy(sc.trading_strategy);
  const body = (re: RegExp) => {
    const p = strategy?.find(s => re.test(s.label));
    return p ? subCO2(stripMarkdown(p.body)) : "";
  };
  // Nhãn tiếng Anh (content_en): "Target", "Risk management"/"Stop-loss".
  const entryBody = body(/entry/i), targetBody = body(/mục tiêu|target/i), riskBody = body(/quản trị rủi ro|cắt lỗ|stop|risk/i);
  const lv = parseSignalLevels(dir, entryBody, targetBody, riskBody);
  const sides: Side[] = [];
  if (dir !== "giảm" && lv.entryBuy.length) sides.push("buy");
  if (dir !== "tăng" && lv.entrySell.length) sides.push("sell");
  if (!sides.length && dir === "tăng" && (lv.targetBuy !== undefined || lv.stopLower !== undefined)) sides.push("buy");
  if (!sides.length && dir === "giảm" && (lv.targetSell !== undefined || lv.stopUpper !== undefined)) sides.push("sell");
  // "Đi ngang" chỉ có 1 chân (dữ liệu cũ): parseSignalLevels gán số không có từ khoá vào chân mua trước — chân bán lấy lại.
  if (dir === "đi ngang" && sides.length === 1) {
    if (sides[0] === "sell") { lv.targetSell ??= lv.targetBuy; lv.stopUpper ??= lv.stopLower; }
    else { lv.targetBuy ??= lv.targetSell; lv.stopLower ??= lv.stopUpper; }
  }
  return { horizon: sc.horizon, sc, dir, legs: sides.map(s => buildLeg(s, lv, entryBody, targetBody, last)) };
}

// Nhận định = chiều kịch bản ngắn hạn, "nghiêng" theo trung hạn — trung hạn xác suất Thấp là kịch bản THAY THẾ, không tính.
function outlookOf(short?: Scenario, midTerm?: Scenario): { pos: number; key: string; tone: string } {
  const s = short?.direction;
  const m = midTerm && !(midTerm.probability === "Thấp" && midTerm.direction !== "đi ngang") ? midTerm.direction : undefined;
  if (s === "tăng") return m === "giảm" ? { pos: 65, key: "upMidDown", tone: "text-up" } : { pos: 85, key: "up", tone: "text-up" };
  if (s === "giảm") return m === "tăng" ? { pos: 35, key: "downMidUp", tone: "text-down" } : { pos: 15, key: "down", tone: "text-down" };
  if (m === "tăng") return { pos: 62, key: "neutralUp", tone: "text-warn" };
  if (m === "giảm") return { pos: 38, key: "neutralDown", tone: "text-warn" };
  return { pos: 50, key: "neutral", tone: "text-warn" };
}

// = report_consistency.atr_true_range: TB True Range n phiên cuối, TR = max(cao − thấp, |cao − đóng cửa trước|, |thấp − đóng cửa trước|).
function atrTrueRange(chart: OhlcRow[], n = ATR_SESSIONS): number | undefined {
  const rows = chart.filter(c => typeof c?.high === "number" && typeof c?.low === "number");
  if (!rows.length) return undefined;
  const trs: number[] = [];
  for (let i = Math.max(0, rows.length - n); i < rows.length; i++) {
    const { high: h, low: l } = rows[i];
    const pc = i > 0 ? rows[i - 1].close : undefined;
    trs.push(typeof pc === "number" ? Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc)) : h - l);
  }
  return trs.reduce((a, b) => a + b, 0) / trs.length;
}

// = report_consistency.range_position + MIDDLE_BAND_PCT (40–60% biên = "vùng giữa", không gọi nửa trên/dưới).
type RangePos = "above" | "below" | "nearTop" | "nearBottom" | "middle" | "upper" | "lower";
function rangePosition(price: number, low: number, high: number): { pct: number; pos: RangePos } | null {
  if (high <= low) return null;
  const pct = ((price - low) / (high - low)) * 100;
  const r = Math.round(pct * 10) / 10;
  const pos: RangePos = pct > 100 ? "above" : pct < 0 ? "below" : pct >= 80 ? "nearTop" : pct <= 20 ? "nearBottom"
    : r > 40 && r < 60 ? "middle" : pct > 50 ? "upper" : "lower";
  return { pct, pos };
}

// ["2026-10-13", "2026-10-15", "2026-10-16"] → "13, 15, 16/10" (gộp theo tháng).
function joinDayMonth(dates: string[]): string {
  const byMonth = new Map<string, string[]>();
  [...dates].sort().forEach(d => {
    const [, m, day] = d.split("-");
    byMonth.set(m, [...(byMonth.get(m) ?? []), day]);
  });
  return [...byMonth].map(([m, days]) => `${days.join(", ")}/${m}`).join(" · ");
}

// ── Thang giá ────────────────────────────────────────────────────────────────
type MarkTone = "buy" | "sell" | "target" | "stop" | "ref" | "current";
interface LadderMark {
  lo: number; hi: number;   // lo === hi: 1 mức giá; khác nhau: vùng Entry
  names: string[];
  tone: MarkTone;
  prio: number;             // 0 = quan trọng nhất; > 2 có thể bị bỏ khi hết chỗ (bảng lệnh vẫn đủ số)
  below: boolean;           // nhãn ưu tiên dưới trục (mốc tham chiếu); lệnh ưu tiên trên trục
  faded: boolean;           // vùng Entry trung/dài hạn — vẽ nhạt hơn vùng ngắn hạn
}

const MARK_TEXT: Record<MarkTone, string> = {
  buy: "text-up", sell: "text-down", target: "text-up", stop: "text-down", ref: "text-body", current: "text-label",
};
const MARK_BG: Record<MarkTone, string> = {
  buy: "bg-up", sell: "bg-down", target: "bg-up", stop: "bg-down", ref: "bg-muted-light", current: "bg-label",
};

function buildLadderMarks(plans: HorizonPlan[], range: [number, number] | null, last: number | undefined, t: ReportT): LadderMark[] {
  const marks: LadderMark[] = [];
  // Cùng 1 mức giá (vd cắt lỗ chân mua = mức kích hoạt bán trung hạn) → 1 nhãn gộp.
  const add = (m: LadderMark) => {
    const same = m.lo === m.hi ? marks.find(o => o.lo === o.hi && Math.abs(o.lo - m.lo) < 0.005) : undefined;
    if (!same) { marks.push(m); return; }
    same.names.push(...m.names);
    if (m.prio < same.prio) Object.assign(same, { tone: m.tone, prio: m.prio, below: m.below });
  };
  if (last !== undefined) add({ lo: last, hi: last, names: [t("ladder.current")], tone: "current", prio: 0, below: true, faded: false });
  if (range) {
    add({ lo: range[0], hi: range[0], names: [t("ladder.support")], tone: "ref", prio: 2, below: true, faded: false });
    add({ lo: range[1], hi: range[1], names: [t("ladder.resistance")], tone: "ref", prio: 2, below: true, faded: false });
  }
  for (const p of plans) {
    const hk = HORIZON_KEY[p.horizon] ?? "short";
    const prio = hk === "short" ? 1 : hk === "mid" ? 2 : 3;
    const named = (name: string) => (hk === "short" ? name : t("ladder.withHorizon", { name, horizon: t(`ladder.horizon.${hk}`) }));
    for (const leg of p.legs) {
      // Trung/dài hạn 1 chân: "MT trung hạn" thay vì "MT bán trung hạn".
      const levelName = (key: "target" | "stop") => named(t(`ladder.${key}.${hk === "short" || p.legs.length > 1 ? leg.side : "plain"}`));
      if (leg.zone.length) {
        add({
          lo: Math.min(...leg.zone), hi: Math.max(...leg.zone), tone: leg.side, prio, below: false, faded: hk !== "short",
          names: [named(t(`ladder.${leg.kind === "breakout" ? "trigger" : leg.kind === "scale" ? "scale" : "zone"}.${leg.side}`))],
        });
      }
      const levelPrio = hk === "short" ? 1 : prio + 1;
      if (leg.target !== undefined) add({ lo: leg.target, hi: leg.target, names: [levelName("target")], tone: "target", prio: levelPrio, below: false, faded: false });
      if (leg.stop !== undefined) add({ lo: leg.stop, hi: leg.stop, names: [levelName("stop")], tone: "stop", prio: levelPrio, below: false, faded: false });
    }
  }
  return marks;
}

// Bề rộng giả định để xếp nhãn không chồng nhau: ≤ bề rộng thật của thẻ trên màn hình lẫn khổ A4 khi in — vị trí vẽ
// theo %, khung thật rộng hơn chỉ làm nhãn thưa ra chứ không chồng thêm.
const LADDER_W = 600;
const LADDER_ROW_H = 30;
const LADDER_AXIS_H = 26;
const LADDER_GAP = 6;

type LadderSide = "above" | "below";
interface PlacedMark extends LadderMark {
  value: string; name: string; c: number; anchor: "center" | "left" | "right"; side: LadderSide; row: number;
}

function layoutLadder(marks: LadderMark[], fmt: (n: number) => string) {
  const lo = Math.min(...marks.map(m => m.lo)), hi = Math.max(...marks.map(m => m.hi));
  const pad = (hi - lo) * 0.03 || 1;
  const min = lo - pad, span = hi + pad - min;
  const px = (v: number) => ((v - min) / span) * LADDER_W;
  // Mỗi hàng nhãn giữ các khoảng [trái, phải] đã chiếm — gồm cả đường dẫn của nhãn ở hàng xa trục hơn đi xuyên qua.
  const rows: Record<LadderSide, [number, number][][]> = { above: [], below: [] };
  const hits = (side: LadderSide, r: number, a: number, b: number) => (rows[side][r] ?? []).some(([x, y]) => a < y && b > x);
  const placed: PlacedMark[] = [];
  const ordered = [...marks].sort((a, b) => a.prio - b.prio || Number(b.hi > b.lo) - Number(a.hi > a.lo) || a.lo - b.lo);
  for (const m of ordered) {
    const value = m.hi - m.lo > 0.005 ? `${fmt(m.lo)}–${fmt(m.hi)}` : fmt(m.lo);
    const name = m.names.join(" · ");
    // Ước lượng bề rộng chữ (11,5px đậm cho số, 10,5px cho tên) — dư một chút để an toàn.
    const width = Math.max(value.length * 6.6, name.length * 5.7) + 8;
    const c = (px(m.lo) + px(m.hi)) / 2;
    const left = Math.min(Math.max(c - width / 2, 0), LADDER_W - width);
    const anchor = c - width / 2 < 0 ? "left" : c + width / 2 > LADDER_W ? "right" : "center";
    const own: LadderSide = m.below ? "below" : "above", other: LadderSide = m.below ? "above" : "below";
    const fits = ([side, r]: [LadderSide, number]) =>
      !hits(side, r, left - LADDER_GAP, left + width + LADDER_GAP)
      && Array.from({ length: r }, (_, k) => k).every(k => !hits(side, k, c - 2, c + 2));
    const slots: [LadderSide, number][] = [[own, 0], [own, 1], [other, 0], [other, 1]];
    if (m.prio <= 2) slots.push([own, 2], [other, 2]);
    let slot = slots.find(fits);
    if (!slot) {
      if (m.prio > 2) continue; // mức phụ (mục tiêu/cắt lỗ trung–dài hạn) hết chỗ: bỏ khỏi thang, bảng vẫn có
      slot = [own, 2];
    }
    const [side, r] = slot;
    (rows[side][r] ??= []).push([left, left + width]);
    for (let k = 0; k < r; k++) (rows[side][k] ??= []).push([c - 1, c + 1]);
    placed.push({ ...m, value, name, c, anchor, side, row: r });
  }
  return { placed, min, span, rowsAbove: rows.above.length, rowsBelow: rows.below.length };
}

function PriceLadder({ marks, range }: { marks: LadderMark[]; range: [number, number] | null }) {
  const locale = useLocale();
  const { placed, min, span, rowsAbove, rowsBelow } = layoutLadder(marks, n => fmtPrice(n, locale));
  const pct = (v: number) => `${((v - min) / span) * 100}%`;
  const len = (d: number) => `${(d / span) * 100}%`;
  const axisTop = rowsAbove * LADDER_ROW_H;
  const axisY = axisTop + LADDER_AXIS_H / 2;
  const zones = placed.filter(m => m.hi - m.lo > 0.005).sort((a, b) => Number(b.faded) - Number(a.faded));
  const points = placed.filter(m => m.hi - m.lo <= 0.005);
  return (
    <div
      className="relative hidden sm:block select-none [print-color-adjust:exact] [-webkit-print-color-adjust:exact]"
      style={{ height: axisTop + LADDER_AXIS_H + rowsBelow * LADDER_ROW_H }}
      aria-hidden="true"
    >
      <div className="absolute inset-x-0 h-[2px] bg-border" style={{ top: axisY - 1 }} />
      {/* Biên 30 phiên (hỗ trợ → kháng cự) */}
      {range && <div className="absolute h-[6px] rounded-full bg-[#d5dbd9]" style={{ top: axisY - 3, left: pct(range[0]), width: len(range[1] - range[0]) }} />}
      {zones.map((m, i) => (
        <div
          key={`z${i}`}
          className={clsx(
            "absolute h-[12px] rounded-[3px]",
            m.faded
              ? clsx("border border-dashed", m.tone === "buy" ? "bg-up/10 border-up/70" : "bg-down/10 border-down/70")
              : m.tone === "buy" ? "bg-up/85" : "bg-down/85",
          )}
          style={{ top: axisY - 6, left: pct(m.lo), width: len(m.hi - m.lo), minWidth: 4 }}
        />
      ))}
      {points.map((m, i) => (
        <div
          key={`p${i}`}
          className={clsx("absolute w-[2px] rounded-full", MARK_BG[m.tone])}
          style={m.tone === "current"
            ? { top: axisY - 11, height: 22, left: pct(m.lo), transform: "translateX(-50%)" }
            : { top: axisY - 6, height: 12, left: pct(m.lo), transform: "translateX(-50%)" }}
        />
      ))}
      {placed.map((m, i) => {
        const top = m.side === "above" ? (rowsAbove - 1 - m.row) * LADDER_ROW_H : axisTop + LADDER_AXIS_H + m.row * LADDER_ROW_H;
        const [lineTop, lineBottom] = m.side === "above" ? [top + LADDER_ROW_H - 3, axisY - 7] : [axisY + 7, top + 1];
        const cx = `${(m.c / LADDER_W) * 100}%`;
        const pos = m.anchor === "left" ? { left: 0 } : m.anchor === "right" ? { right: 0 } : { left: cx, transform: "translateX(-50%)" };
        return (
          <Fragment key={`l${i}`}>
            {lineBottom > lineTop && <div className="absolute w-px bg-border-soft" style={{ left: cx, top: lineTop, height: lineBottom - lineTop }} />}
            <div
              className={clsx("absolute leading-[1.2] whitespace-nowrap", m.anchor === "left" ? "text-left" : m.anchor === "right" ? "text-right" : "text-center")}
              style={{ top, ...pos }}
            >
              <div className={clsx("text-[11.5px] font-bold tabular-nums", MARK_TEXT[m.tone])}>{m.value}</div>
              <div className={clsx("text-[10.5px]", m.tone === "current" ? "font-bold text-label" : m.tone === "ref" ? "text-muted-light" : MARK_TEXT[m.tone])}>
                {m.name}
              </div>
            </div>
          </Fragment>
        );
      })}
    </div>
  );
}

// Mobile: thang giá dọc — mọi mức xếp từ cao xuống thấp (màn hẹp không đủ chỗ cho trục ngang nhiều nhãn).
function PriceLevelList({ marks }: { marks: LadderMark[] }) {
  const locale = useLocale();
  const fp = (n: number) => fmtPrice(n, locale);
  const items = [...marks].sort((a, b) => b.hi - a.hi || b.lo - a.lo);
  return (
    <ol className="relative sm:hidden [print-color-adjust:exact] [-webkit-print-color-adjust:exact]">
      <span className="absolute left-[102px] top-2 bottom-2 w-[2px] bg-border" aria-hidden="true" />
      {items.map((m, i) => {
        const isZone = m.hi - m.lo > 0.005;
        return (
          <li key={i} className={clsx("relative flex items-center gap-3 py-[3px]", m.tone === "current" && "bg-surface rounded")}>
            <span className={clsx("w-[84px] shrink-0 text-right text-[12.5px] font-bold tabular-nums", MARK_TEXT[m.tone])}>
              {isZone ? `${fp(m.lo)}–${fp(m.hi)}` : fp(m.lo)}
            </span>
            <span className="relative w-[14px] shrink-0 flex justify-center" aria-hidden="true">
              <span className={clsx(
                "block",
                isZone ? "w-[10px] h-[14px] rounded-[3px]" : m.tone === "current" ? "w-[14px] h-[3px]" : "w-[10px] h-[3px] rounded-full",
                isZone && m.faded ? clsx("border border-dashed bg-background", m.tone === "buy" ? "border-up" : "border-down") : MARK_BG[m.tone],
              )} />
            </span>
            <span className={clsx("min-w-0 text-[12px] leading-snug", m.tone === "current" ? "font-bold text-label" : m.tone === "ref" ? "text-muted-light" : MARK_TEXT[m.tone])}>
              {m.names.join(" · ")}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

// ── Bảng lệnh ────────────────────────────────────────────────────────────────
const ORDER_PILL: Record<Side, string> = {
  buy: "bg-up/10 text-up border-up/25",
  sell: "bg-down/10 text-down border-down/25",
};

function OrderPill({ leg }: { leg: Leg }) {
  const t = useTranslations("reportDoc");
  return (
    <span className={clsx(
      "inline-block rounded-full border px-2 py-[1px] text-[11.5px] font-semibold leading-[1.5] whitespace-nowrap [print-color-adjust:exact] [-webkit-print-color-adjust:exact]",
      leg.kind === "scale" ? "bg-surface text-body border-border" : ORDER_PILL[leg.side],
    )}>
      {t(`signal.order.${leg.kind}.${leg.side}`)}
    </span>
  );
}

// Thanh R:R cùng thang đo cho mọi chân lệnh (đỏ = rủi ro tới cắt lỗ, xanh = lợi nhuận tới mục tiêu).
function RrBar({ leg, scale }: { leg: Leg; scale: number }) {
  const W = 64;
  if (!(leg.risk !== undefined && leg.risk > 0 && scale > 0)) return null;
  const r = (leg.risk / scale) * W;
  const g = leg.reward !== undefined && leg.reward > 0 ? (leg.reward / scale) * W : 0;
  return (
    <span className="mt-1 flex h-[5px] [print-color-adjust:exact] [-webkit-print-color-adjust:exact]" style={{ width: W }} aria-hidden="true">
      <span className="h-full rounded-l-[2px] bg-down" style={{ width: r }} />
      {g > 0
        ? <span className="h-full rounded-r-[2px] bg-up" style={{ width: g }} />
        : <span className="h-full flex-1 rounded-r-[2px] border border-dashed border-border-soft" />}
    </span>
  );
}

interface LegCtx { t: ReportT; locale: string; last?: number; minRr: number; rrScale: number }

// Ô số liệu của 1 chân lệnh — dùng chung cho bảng (sm+/in) và khối dọc (mobile).
function legCells(leg: Leg, { t, locale, last, minRr, rrScale }: LegCtx) {
  const fp = (n: number) => fmtPrice(n, locale);
  const entryText = !leg.zone.length ? "—"
    : leg.kind === "breakout"
      ? `${leg.onClose ? `${t("signal.onClose")} ` : ""}${leg.side === "buy" ? ">" : "<"} ${fp(leg.zone[0])}`
      : fmtRange(leg.zone, locale);
  const dist = leg.entry !== undefined && last ? ((leg.entry - last) / last) * 100 : undefined;
  const weak = leg.rr !== undefined && leg.rr < minRr;
  return {
    entry: (
      <>
        <div className="font-bold tabular-nums text-label whitespace-nowrap">{entryText}</div>
        {dist !== undefined && (
          <div className="text-[11px] text-muted-light tabular-nums">{t("signal.vsPrice", { pct: `${dist > 0 ? "+" : dist < 0 ? "−" : ""}${fnum(Math.abs(dist), 1, locale)}` })}</div>
        )}
      </>
    ),
    target: leg.target !== undefined
      ? <span className="font-bold tabular-nums text-up">{fp(leg.target)}</span>
      : leg.targetUnquantified
        ? <span className="text-[11.5px] italic text-muted-light">{t("signal.unquantified")}</span>
        : <span className="text-muted-light">—</span>,
    stop: leg.stop !== undefined ? <span className="font-bold tabular-nums text-down">{fp(leg.stop)}</span> : <span className="text-muted-light">—</span>,
    rr: (
      <>
        <span
          className={clsx("font-bold tabular-nums whitespace-nowrap", leg.rr === undefined ? "text-muted-light" : weak ? "text-warn" : "text-label")}
          title={weak ? t("signal.rrWeak", { min: fnum(minRr, 1, locale) }) : undefined}
        >
          {leg.rr !== undefined ? `1 : ${fnum(leg.rr, 1, locale)}${weak ? " !" : ""}` : "—"}
        </span>
        <RrBar leg={leg} scale={rrScale} />
      </>
    ),
  };
}

function HorizonCell({ plan, compact }: { plan: HorizonPlan; compact?: boolean }) {
  const t = useTranslations("reportDoc");
  const enumLabel = useEnumLabel();
  const hk = HORIZON_KEY[plan.horizon] ?? "short";
  const label = enumLabel(plan.horizon);
  // Dài hạn: khung thời gian cụ thể nằm đầu "condition" ("6–12 tháng, tới hết Q3/2027").
  const period = (hk === "long" && String(plan.sc.condition || "").match(/\d+\s*[–-]\s*\d+\s*(?:tháng|months?)/i)?.[0]) || t(`signal.period.${hk}`);
  const trend = TREND_META[plan.dir];
  const alt = plan.sc.probability === "Thấp" && plan.dir !== "đi ngang";
  const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
  return (
    <div className={clsx("min-w-0 leading-snug", compact && "flex flex-wrap items-baseline gap-x-2")}>
      <div className="text-[13px] font-bold text-label">
        {cap(label)}
        <span className={clsx("text-[11px] font-normal text-muted-light", compact ? "ml-1.5" : "block")}>{period}</span>
      </div>
      <div className="text-[11.5px]">
        <span className={clsx("font-semibold whitespace-nowrap", trend?.className)}>{trend?.arrow} {cap(enumLabel(plan.dir))}</span>
        {plan.sc.probability && (
          <span className={clsx("text-muted-light", compact ? "ml-1.5" : "block")}>{t("signal.prob", { p: enumLabel(plan.sc.probability) })}</span>
        )}
        {alt && <span className="inline-block mt-0.5 rounded border border-border px-1 text-[10px] uppercase tracking-wide text-muted-light">{t("signal.alternative")}</span>}
      </div>
    </div>
  );
}

function ConditionCell({ sc }: { sc: Scenario }) {
  const t = useTranslations("reportDoc");
  return (
    <div className="text-[12px] leading-[1.45] text-body">
      {sc.condition ? <RichText text={sc.condition} /> : <span className="text-muted-light">—</span>}
      {sc.key_risk && (
        <div className="mt-1 text-down">
          <span className="font-semibold">{t("signal.invalidation")}</span> <RichText text={sc.key_risk} />
        </div>
      )}
    </div>
  );
}

function StrategyTable({ plans, last, minRr }: { plans: HorizonPlan[]; last?: number; minRr: number }) {
  const t = useTranslations("reportDoc");
  const locale = useLocale();
  // Thanh R:R mọi chân dùng chung 1 thang: chân có (rủi ro + lợi nhuận) lớn nhất = toàn bộ bề rộng.
  const rrScale = Math.max(0, ...plans.flatMap(p => p.legs).map(l => (l.risk && l.risk > 0 ? l.risk + Math.max(l.reward ?? 0, 0) : 0)));
  const ctx: LegCtx = { t, locale, last, minRr, rrScale };
  const th = "pb-1.5 text-[11px] font-semibold text-muted-light text-left";
  // Không tách được mức giá nào (vd "Đứng ngoài…", dữ liệu cũ chỉ có action_plan) → hiện nguyên văn.
  const fallback = (p: HorizonPlan): string => p.sc.trading_strategy || p.sc.action_plan || "";
  return (
    <>
      {/* sm+ và khi in/xuất PDF: bảng; mobile: mỗi khung 1 khối dọc */}
      <table className="hidden sm:table w-full table-fixed border-collapse text-[13px]">
        <colgroup>
          <col style={{ width: "14%" }} /><col style={{ width: "13%" }} /><col style={{ width: "14%" }} />
          <col style={{ width: "11%" }} /><col style={{ width: "8%" }} /><col style={{ width: "12%" }} /><col />
        </colgroup>
        <thead>
          <tr className="border-b border-border">
            <th className={th}>{t("signal.col.horizon")}</th>
            <th className={th}>{t("signal.col.order")}</th>
            <th className={clsx(th, "text-right! pr-3")}>{t("signal.col.entry")}</th>
            <th className={clsx(th, "text-right! pr-3")}>{t("signal.col.target")}</th>
            <th className={clsx(th, "text-right! pr-3")}>{t("signal.col.stop")}</th>
            <th className={th}>{t("signal.col.rr")}</th>
            <th className={th}>{t("signal.col.condition")}</th>
          </tr>
        </thead>
        <tbody>
          {plans.map(p => {
            const legs: (Leg | null)[] = p.legs.length ? p.legs : [null];
            return legs.map((leg, li) => {
              const n = leg ? legCells(leg, ctx) : null;
              return (
                <tr key={`${p.horizon}-${li}`} className={clsx("align-top print:break-inside-avoid", li === 0 && "border-t border-border first:border-t-0")}>
                  {li === 0 && <td rowSpan={legs.length} className="py-2 pr-2"><HorizonCell plan={p} /></td>}
                  {n && leg ? (
                    <>
                      <td className="py-2 pr-2"><OrderPill leg={leg} /></td>
                      <td className="py-2 pr-3 text-right">{n.entry}</td>
                      <td className="py-2 pr-3 text-right">{n.target}</td>
                      <td className="py-2 pr-3 text-right">{n.stop}</td>
                      <td className="py-2 pr-2">{n.rr}</td>
                    </>
                  ) : (
                    <td colSpan={5} className="py-2 pr-3 text-[12px] leading-[1.45] text-body">
                      {fallback(p) ? <RichText text={fallback(p)} /> : <span className="text-muted-light">—</span>}
                    </td>
                  )}
                  {li === 0 && <td rowSpan={legs.length} className="py-2"><ConditionCell sc={p.sc} /></td>}
                </tr>
              );
            });
          })}
        </tbody>
      </table>

      <div className="sm:hidden divide-y divide-border">
        {plans.map(p => (
          <div key={p.horizon} className="py-2.5 first:pt-0">
            <HorizonCell plan={p} compact />
            {p.legs.length ? p.legs.map((leg, li) => {
              const n = legCells(leg, ctx);
              return (
                <div key={li} className="mt-2 rounded-lg border border-border px-2.5 py-2">
                  <div className="flex items-start justify-between gap-2">
                    <OrderPill leg={leg} />
                    <div className="flex flex-col items-end text-[12.5px]">{n.rr}</div>
                  </div>
                  <div className="mt-1.5 grid grid-cols-[1.4fr_1fr_1fr] gap-2 text-[12.5px]">
                    <div><div className="text-[10.5px] text-muted-light">{t("signal.col.entry")}</div>{n.entry}</div>
                    <div><div className="text-[10.5px] text-muted-light">{t("signal.col.target")}</div>{n.target}</div>
                    <div><div className="text-[10.5px] text-muted-light">{t("signal.col.stop")}</div>{n.stop}</div>
                  </div>
                </div>
              );
            }) : (
              <p className="mt-1.5 text-[12.5px] leading-[1.45] text-body">
                {fallback(p) ? <RichText text={fallback(p)} /> : <span className="text-muted-light">—</span>}
              </p>
            )}
            <div className="mt-2"><ConditionCell sc={p.sc} /></div>
          </div>
        ))}
      </div>
    </>
  );
}

// ── Phòng hộ CBAM (content["2"].cbam_estimate — services/cbam_estimate.py) ─────────────────────────────
// Cấu trúc khóa chi phí = cbam_estimate.hedge_structure_text: MUA futures Dec ngay cho phần lõi, BÁN rải theo lịch
// đấu giá còn lại; phần chiến thuật (≤ tactical_max_pct % phần chưa chốt) theo nhãn signal.
function CbamHedgePlan({ est, hedgeAction }: { est: CbamEstimate; hedgeAction?: HedgeAction | null }) {
  const t = useTranslations("reportDoc");
  const locale = useLocale();
  const n = (x: number, d = 2) => fnum(x, d, locale);
  const dm = (iso: string) => `${iso.slice(8, 10)}/${iso.slice(5, 7)}`;
  const fixed = est.fixed_share_pct;
  const open = est.open_share_pct ?? 100 - fixed;
  const tmax = est.tactical_max_pct ?? 20;
  const core = est.core_share_of_quarter_pct ?? (open * (100 - tmax)) / 100;
  const tactical = est.tactical_share_of_quarter_pct ?? (open * tmax) / 100;
  const year = Number(est.quarter.split(" ").pop());
  const yy = (y: number) => String(y % 100).padStart(2, "0");
  const contract = Number.isFinite(year) ? `Dec-${yy(year)}` : "Dec";
  const nextContract = Number.isFinite(year) ? `Mar-${yy(year + 1)}` : "Mar";
  const lastAuction = est.last_auction_date, expiry = est.futures_expiry;
  const gap = est.spot_vs_qtd_pct;

  const segments = [
    { key: "fixed", pct: fixed, cls: "bg-surface-alt text-muted-light", label: t("hedge.segFixed", { pct: n(fixed, 1) }) },
    { key: "core", pct: core, cls: "bg-primary/15 text-primary-dark", label: t("hedge.segCore", { pct: n(core, 1), contract }) },
    {
      key: "tactical", pct: tactical,
      cls: est.signal === "increase" ? "bg-primary/30 text-primary-dark" : "bg-warn-tint text-warn border border-dashed border-warn/60",
      label: t(`hedge.segTactical.${est.signal}`, { pct: n(tactical, 1) }),
      short: t(`hedge.segTacticalShort.${est.signal}`, { pct: n(tactical, 1) }),
    },
  ].filter(s => s.pct > 0.05);

  const notes: { icon: typeof Clock; text: string }[] = [];
  if (open > 0.05) {
    notes.push({
      icon: ArrowLeftRight,
      text: (lastAuction
        ? t("hedge.sellPlan", { contract, left: est.auctions_total - est.auctions_done, last: dm(lastAuction) })
        : t("hedge.sellPlanNoDate", { contract, left: est.auctions_total - est.auctions_done }))
        + (est.upcoming?.count ? ` ${t("hedge.next7", { count: est.upcoming.count, volume: n(est.upcoming.volume_mt) })}` : ""),
    });
  }
  notes.push({
    icon: Crosshair,
    text: t(`hedge.tactical.${est.signal_reason}`, {
      tmax, spot: n(est.spot), qtd: est.qtd_vwap !== null ? n(est.qtd_vwap) : "—",
      gap: gap !== null ? (est.signal_reason === "spot_near_qtd" ? `${gap > 0 ? "+" : gap < 0 ? "−" : ""}${n(Math.abs(gap))}` : n(Math.abs(gap))) : "—",
      threshold: n(est.hedge_gap_pct, Number.isInteger(est.hedge_gap_pct) ? 0 : 1), fixed: n(fixed, 1),
    }),
  });
  if (lastAuction && open > 0.05) {
    const key = !expiry ? "noExpiry" : lastAuction === expiry ? "sameDay" : lastAuction > expiry ? "afterExpiry" : "beforeExpiry";
    notes.push({ icon: CalendarClock, text: t(`hedge.expiry.${key}`, { contract, next: nextContract, last: dm(lastAuction), expiry: expiry ? dm(expiry) : "" }) });
  }

  return (
    <div className="px-4 sm:px-5 py-3.5 border-t border-border print:break-inside-avoid">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 mb-2">
        <h3 className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13.5px] font-bold text-label">
          <ShieldCheck size={15} strokeWidth={2.5} className="text-primary-dark shrink-0" aria-hidden="true" />
          {t("hedge.title", { quarter: est.quarter })}
          <span className="rounded border border-primary/30 bg-tint px-1.5 py-px text-[10.5px] font-bold uppercase tracking-wider text-primary-dark">
            {t(`signal.hedgeAction.${est.signal}`)}
          </span>
        </h3>
        <span className="text-[11.5px] text-muted-light tabular-nums">
          {est.ci_low !== null && est.ci_high !== null
            ? t("hedge.estimateCi", { est: n(est.estimate), level: est.ci_level, low: n(est.ci_low), high: n(est.ci_high) })
            : t("hedge.estimate", { est: n(est.estimate) })}
        </span>
      </div>
      <div className="flex h-[26px] rounded-md overflow-hidden text-[11.5px] font-semibold [print-color-adjust:exact] [-webkit-print-color-adjust:exact]">
        {segments.map((s, i) => (
          <div
            key={s.key}
            className={clsx("flex items-center justify-center px-2 min-w-0", s.cls, i < segments.length - 1 ? "shrink-0" : "flex-1")}
            style={i < segments.length - 1 ? { width: `${s.pct}%` } : undefined}
            title={s.label}
          >
            {s.pct >= 7 && <span className="hidden sm:inline truncate">{"short" in s ? s.short : s.label}</span>}
          </div>
        ))}
      </div>
      {/* Mobile: thanh quá hẹp để ghi chữ trong từng đoạn → chú giải bên dưới. */}
      <ul className="sm:hidden mt-1.5 space-y-0.5 text-[11.5px] text-body [print-color-adjust:exact] [-webkit-print-color-adjust:exact]">
        {segments.map(s => (
          <li key={s.key} className="flex items-center gap-1.5">
            <span className={clsx("inline-block w-3 h-3 rounded-[3px] shrink-0", s.cls)} aria-hidden="true" />
            {s.label}
          </li>
        ))}
      </ul>
      <ul className="mt-2.5 grid sm:grid-cols-3 gap-x-4 gap-y-1.5 text-[11.5px] leading-[1.45] text-body">
        {notes.map((note, i) => (
          <li key={i} className="flex items-start gap-1.5">
            <note.icon size={13} className="shrink-0 mt-[2px] text-muted-light" aria-hidden="true" />
            <span className="tabular-nums">{note.text}</span>
          </li>
        ))}
      </ul>
      {/* Câu LLM của phần chiến thuật (chỉ khi nhãn increase/reduce — "hold" do Python viết, trùng ý dòng trên). */}
      {est.signal !== "hold" && hedgeAction?.text && (
        <p className="mt-1.5 text-[11.5px] leading-[1.45] text-muted-light">{stripMarkdown(hedgeAction.text)}</p>
      )}
    </div>
  );
}

// Báo cáo cũ chưa có cbam_estimate: hiện nguyên văn ô phòng hộ (hedge_action hoặc hedge_note của kịch bản ngắn hạn).
function LegacyHedgeNote({ hedgeAction, note }: { hedgeAction?: HedgeAction | null; note?: string }) {
  const t = useTranslations("reportDoc");
  return (
    <div className="px-4 sm:px-5 py-3 border-t border-border">
      <h3 className="flex flex-wrap items-center gap-2 text-[13.5px] font-bold text-label mb-1">
        <ShieldCheck size={15} strokeWidth={2.5} className="text-primary-dark shrink-0" aria-hidden="true" />
        {t("signal.hedge")}
        {hedgeAction && (
          <span className="rounded border border-primary/30 bg-tint px-1.5 py-px text-[10.5px] font-bold uppercase tracking-wider text-primary-dark">
            {t(`signal.hedgeAction.${hedgeAction.action}`)}
          </span>
        )}
      </h3>
      {hedgeAction?.core && <p className="text-[13px] leading-[1.55] text-body font-semibold"><HighlightNumbers text={hedgeAction.core} /></p>}
      {(hedgeAction?.text || note) && <p className="text-[13px] leading-[1.55] text-body"><HighlightNumbers text={hedgeAction?.text || note || ""} /></p>}
    </div>
  );
}

function KpiTile({ title, children, className, href }: { title: string; children: React.ReactNode; className?: string; href?: string }) {
  const cls = clsx("block rounded-lg border border-border bg-surface/40 px-3 py-2.5 min-w-0", href && "hover:border-primary/40 transition-colors", className);
  const body = (
    <>
      <div className="text-[11px] font-semibold leading-tight text-muted-light mb-1">{title}</div>
      {children}
    </>
  );
  return href ? <a href={href} className={cls}>{body}</a> : <div className={cls}>{body}</div>;
}

function PositionCard({ scenarios, chartData, euaRow, forecastReview, cbamEstimate, hedgeAction, openPositions }: {
  scenarios: Scenario[]; chartData: OhlcRow[]; euaRow?: PriceRow; forecastReview?: ForecastReview | null;
  cbamEstimate?: CbamEstimate | null; hedgeAction?: HedgeAction | null; openPositions?: OpenPosition[];
}) {
  const t = useTranslations("reportDoc");
  const locale = useLocale();
  const fp = (n: number) => fmtPrice(n, locale);
  const signed = (n: number, d = 2) => `${n > 0 ? "+" : n < 0 ? "−" : ""}${fnum(Math.abs(n), d, locale)}`;

  const lastRow = chartData.length ? chartData[chartData.length - 1] : undefined;
  const last: number | undefined = typeof lastRow?.close === "number" ? lastRow.close
    : typeof euaRow?.close === "number" ? euaRow.close : undefined;
  // Hỗ trợ/kháng cự = đáy/đỉnh 30 phiên OHLC thật (= report_generator._eua_technical_levels_summary).
  const range: [number, number] | null = chartData.length
    ? [Math.min(...chartData.map(c => c.low)), Math.max(...chartData.map(c => c.high))] : null;
  const plans = PLAN_HORIZONS
    .map(h => scenarios.find(sc => sc?.horizon === h))
    .filter((sc): sc is Scenario => !!sc)
    .map(sc => buildPlan(sc, last));
  const short = plans.find(p => p.horizon === "ngắn hạn")?.sc;
  const outlook = outlookOf(short, plans.find(p => p.horizon === "trung hạn")?.sc);
  const minRr = forecastReview?.rules?.min_rr ?? DEFAULT_MIN_RR;
  const marks = buildLadderMarks(plans, range, last, t);

  // Δ ngày/tuần: ưu tiên đúng số của Bảng giá nhanh (dòng EUA); thiếu thì tính từ chart_data cùng định nghĩa
  // (tuần = phiên gần nhất cách ≥ 7 ngày lịch, như EuaDashboardWidget).
  const prevClose = chartData.length > 1 ? chartData[chartData.length - 2].close : undefined;
  const dayPct: number | undefined = typeof euaRow?.day_change_pct === "number" ? euaRow.day_change_pct
    : last !== undefined && prevClose ? (last / prevClose - 1) * 100 : undefined;
  const weekRow = lastRow ? [...chartData].reverse().find(c =>
    c.date <= new Date(new Date(lastRow.date).getTime() - 7 * 86400000).toISOString().slice(0, 10)) : undefined;
  const weekPct: number | undefined = typeof euaRow?.week_change_pct === "number" ? euaRow.week_change_pct
    : last !== undefined && weekRow?.close ? (last / weekRow.close - 1) * 100 : undefined;

  const pos = range && last !== undefined ? rangePosition(last, range[0], range[1]) : null;
  const atr = atrTrueRange(chartData);
  const vol = lastRow?.volume ?? 0;
  // TB khối lượng 20 phiên LIỀN TRƯỚC, bỏ phiên volume <= 0 — cùng công thức với EuaDashboardWidget.
  const priorVols = chartData.slice(0, -1).map(c => c.volume).filter((v): v is number => typeof v === "number" && v > 0).slice(-VOL_AVG_SESSIONS);
  const avgVol = priorVols.length ? priorVols.reduce((a, b) => a + b, 0) / priorVols.length : 0;
  const volPct = avgVol && vol > 0 ? (vol / avgVol - 1) * 100 : undefined;

  const reviewItems = forecastReview?.items ?? [];
  const right = reviewItems.filter(i => i.verdict === "correct").map(i => i.report_date);
  const wrong = reviewItems.filter(i => i.verdict === "wrong").map(i => i.report_date);
  const pending = reviewItems.length - right.length - wrong.length;

  return (
    <section className="mb-6 rounded-xl border border-border bg-background shadow-[var(--shadow-soft)] print:shadow-none overflow-hidden">
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 px-4 sm:px-5 pt-3.5 pb-2.5 border-b border-border print:break-after-avoid">
        <h2 className="flex items-center gap-2 text-[16px] sm:text-[17px] font-extrabold text-primary-dark">
          <Crosshair size={18} strokeWidth={2.5} aria-hidden="true" /> {t("signal.title")}
        </h2>
        {lastRow?.date && (
          <span className="text-[11.5px] text-muted-light tabular-nums">{t("signal.dataAsOf", { date: formatFullDate(lastRow.date) })}</span>
        )}
      </header>

      <div className="px-3 sm:px-5 py-3.5 space-y-4">
        {/* ═══ Hàng chỉ số ═══ */}
        <div className="grid grid-cols-2 sm:grid-cols-[1fr_1fr_1.2fr_1fr_1fr] gap-2 print:break-inside-avoid [&_.kpi-sub]:[text-wrap:balance]">
          <KpiTile title={t("signal.outlook")} className="col-span-2 sm:col-span-1">
            <div className="relative mt-1.5 h-[6px] rounded-full bg-gradient-to-r from-down/45 via-border to-up/45 [print-color-adjust:exact] [-webkit-print-color-adjust:exact]">
              <span className="absolute top-[-4px] h-[14px] w-[3px] rounded-full bg-label" style={{ left: `${outlook.pos}%`, transform: "translateX(-50%)" }} />
            </div>
            <div className="mt-1 flex justify-between text-[10px] text-muted-light"><span>{t("signal.gaugeDown")}</span><span>{t("signal.gaugeUp")}</span></div>
            <div className={clsx("mt-0.5 text-[12.5px] font-bold leading-snug", outlook.tone)}>{t(`signal.outlookLabel.${outlook.key}`)}</div>
          </KpiTile>

          <KpiTile title={t("signal.refPrice")}>
            <div className="text-[19px] font-bold tabular-nums leading-tight text-label">{last !== undefined ? fp(last) : "—"}</div>
            <div className="kpi-sub text-[11px] tabular-nums leading-snug">
              {dayPct !== undefined && <div className={dayPct >= 0 ? "text-up" : "text-down"}>{t("signal.dayPct", { pct: signed(dayPct) })}</div>}
              {weekPct !== undefined && <div className={weekPct >= 0 ? "text-up" : "text-down"}>{t("signal.weekPct", { pct: signed(weekPct) })}</div>}
            </div>
          </KpiTile>

          <KpiTile title={t("signal.range30")}>
            <div className="text-[16px] font-bold tabular-nums leading-tight text-label whitespace-nowrap">{range ? fmtRange(range, locale) : "—"}</div>
            {pos && <div className="kpi-sub text-[11px] text-body leading-snug">{t(`signal.rangePos.${pos.pos}`, { pct: fnum(pos.pct, 0, locale) })}</div>}
            {/* Mục tiêu đo biên độ khi phá/thủng biên = report_generator._eua_technical_levels_summary */}
            {range && (
              <div className="kpi-sub text-[11px] text-muted-light tabular-nums leading-snug">
                {t("signal.measured", { down: fp(range[0] - (range[1] - range[0])), up: fp(range[1] + (range[1] - range[0])) })}
              </div>
            )}
          </KpiTile>

          <KpiTile title={t("signal.atr", { n: ATR_SESSIONS })}>
            <div className="text-[19px] font-bold tabular-nums leading-tight text-label">{atr !== undefined ? fp(atr) : "—"}</div>
            {atr !== undefined && last ? <div className="kpi-sub text-[11px] text-muted-light tabular-nums leading-snug">{t("signal.atrPct", { pct: fnum((atr / last) * 100, 1, locale) })}</div> : null}
            {volPct !== undefined && (
              <div className="kpi-sub text-[11px] text-muted-light tabular-nums leading-snug">{t("signal.volVsAvg", { pct: signed(volPct, 1), n: priorVols.length })}</div>
            )}
          </KpiTile>

          <KpiTile title={t("signal.track")} href={forecastReview ? "#section-forecast-review" : undefined}>
            <div className="text-[19px] font-bold tabular-nums leading-tight text-label">
              {right.length + wrong.length ? (
                <>
                  {right.length}/{right.length + wrong.length}
                  <span className="ml-1 text-[12px] font-semibold text-muted-light">{t("signal.trackUnit")}</span>
                </>
              ) : "—"}
            </div>
            <div className="kpi-sub text-[11px] leading-snug tabular-nums">
              {wrong.length > 0 && <div className="text-down">{t("signal.trackWrong", { dates: joinDayMonth(wrong) })}</div>}
              {right.length > 0 && <div className="text-up">{t("signal.trackRight", { dates: joinDayMonth(right) })}</div>}
              {pending > 0 && <div className="text-muted-light">{t("signal.trackPending", { n: pending })}</div>}
              {!reviewItems.length && <div className="text-muted-light">{t("signal.trackNone")}</div>}
            </div>
          </KpiTile>
        </div>

        {/* ═══ Thang giá: mọi mức của các kịch bản + giá hiện tại + biên 30 phiên ═══ */}
        {marks.length > 1 && (
          <div className="print:break-inside-avoid">
            <PriceLadder marks={marks} range={range} />
            <PriceLevelList marks={marks} />
          </div>
        )}

        {/* ═══ Bảng lệnh theo khung thời gian ═══ — KHÔNG giữ nguyên khối khi in (cả bảng bị đẩy sang trang sau, để trống
            1/3 trang); từng hàng đã không bị cắt đôi (tr) và tên cột lặp lại ở trang sau. */}
        <div>
          <StrategyTable plans={plans} last={last} minRr={minRr} />
          <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-light [print-color-adjust:exact] [-webkit-print-color-adjust:exact]">
            <span className="inline-flex items-center gap-1"><span className="inline-block w-3 h-[5px] rounded-[2px] bg-down" aria-hidden="true" />{t("signal.legendRisk")}</span>
            <span className="inline-flex items-center gap-1"><span className="inline-block w-3 h-[5px] rounded-[2px] bg-up" aria-hidden="true" />{t("signal.legendReward")}</span>
            <span>{t("signal.legendNote", { min: fnum(minRr, 1, locale) })}</span>
          </p>
        </div>
      </div>

      {/* ═══ Phòng hộ CBAM — sổ phòng hộ SIM, tách khỏi lệnh của bàn giao dịch ở trên ═══ */}
      {cbamEstimate ? (
        <CbamHedgePlan est={cbamEstimate} hedgeAction={hedgeAction} />
      ) : (hedgeAction || short?.hedge_note) ? (
        <LegacyHedgeNote hedgeAction={hedgeAction} note={short?.hedge_note} />
      ) : null}

      {/* ═══ Lệnh mô phỏng còn mở từ báo cáo trước — đối chiếu với mức giá mới ═══ */}
      {openPositions && openPositions.length > 0 && (
        <div className="px-4 sm:px-5 py-3 border-t border-border">
          <h3 className="text-[12.5px] font-bold text-label mb-1">{t("signal.openPositions")}</h3>
          <ul className="space-y-1 text-[12.5px] leading-[1.55] text-body">
            {openPositions.map(p => (
              <li key={p.report_date}>
                <span className={clsx(
                  "mr-2 inline-block rounded border px-1.5 py-px text-[10px] font-bold tracking-wider",
                  p.decision ? "border-primary/30 text-primary-dark" : "border-down/30 text-down",
                )}>
                  {t(`signal.decision.${p.decision ?? "none"}`)}
                </span>
                <span className="font-semibold tabular-nums">
                  {t("signal.openPosition", {
                    side: t(p.side === "buy" ? "signal.buy" : "signal.sell"),
                    low: fp(p.entry_low), high: fp(p.entry_high), stop: fp(p.stop),
                    date: p.report_date.split("-").reverse().slice(0, 2).join("/"),
                  })}
                </span>
                {p.text && <>{" — "}<HighlightNumbers text={p.text} /></>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

// Tách "trading_strategy" thành các phần Entry / Mục tiêu / Quản trị rủi ro theo tag
// in đậm backend sinh ra (xem report_generator.py). Không tách được → trả null để
// nơi gọi fallback về RichText nguyên khối.
function parseStrategy(text?: string): { label: string; body: string }[] | null {
  if (!text) return null;
  const parts = text
    .split(/\n+/)
    .map(l => l.trim())
    .filter(Boolean)
    .map(l => {
      const m = l.match(/^\*\*([^*]+?)\*\*\s*:?\s*(.*)$/);
      return m ? { label: m[1].replace(/:\s*$/, "").trim(), body: m[2].trim() } : null;
    });
  return parts.length > 0 && parts.every(Boolean) ? (parts as { label: string; body: string }[]) : null;
}

// Mobile: mỗi khung thời gian 1 tab (Ngắn/Trung/Dài hạn) — trong tab đọc liền mạch
// 1 kịch bản theo đúng mạch "Nếu X → vùng giá → chiến lược → rủi ro".
function ScenarioTabs({ horizons, byHorizon }: { horizons: string[]; byHorizon: Record<string, any> }) {
  const t = useTranslations("reportDoc");
  const enumLabel = useEnumLabel();
  const [active, setActive] = useState(horizons[0]);
  const current = horizons.includes(active) ? active : horizons[0];
  const sc = byHorizon[current];
  const dirMeta = DIRECTION_META[sc.direction];
  const DirIcon = dirMeta?.icon;
  const strategy = parseStrategy(sc.trading_strategy);

  return (
    <div className="sm:hidden border border-border rounded-lg overflow-hidden">
      <div role="tablist" className="grid bg-tint border-b border-border" style={{ gridTemplateColumns: `repeat(${horizons.length}, minmax(0, 1fr))` }}>
        {horizons.map(h => {
          const Icon = HORIZON_META[h].icon;
          const on = h === current;
          return (
            <button
              key={h}
              type="button"
              role="tab"
              aria-selected={on}
              onClick={() => setActive(h)}
              className={clsx(
                "flex items-center justify-center gap-1 min-h-[44px] px-1 text-[12px] font-bold uppercase tracking-wide border-b-2 -mb-px transition-colors",
                on ? "border-primary bg-white text-primary-dark" : "border-transparent text-muted-light"
              )}
            >
              <Icon size={13} className="shrink-0" /> {enumLabel(h)}
            </button>
          );
        })}
      </div>

      <div role="tabpanel" className="p-3 flex flex-col gap-3 text-[14.5px] leading-[1.55] text-body break-words">
        {(dirMeta || sc.probability) && (
          <div className="flex flex-wrap items-center gap-2">
            {sc.probability === "Thấp" && sc.direction !== "đi ngang" && (
              <span className="font-mono text-[11px] font-bold uppercase tracking-wider text-muted-light">{t("scenario.alternative")}</span>
            )}
            {dirMeta && (
              <span className={clsx("inline-flex items-center gap-1 font-mono text-[12px] font-bold uppercase tracking-wider rounded px-2 py-1 border [print-color-adjust:exact] [-webkit-print-color-adjust:exact]", dirMeta.className)}>
                <DirIcon size={13} className="shrink-0" /> {t("scenario.direction", { dir: enumLabel(sc.direction) })}
              </span>
            )}
            {sc.probability && (
              <span className="font-mono text-[12px] uppercase tracking-wider text-body">{t("scenario.probability")} <b>{enumLabel(sc.probability)}</b></span>
            )}
          </div>
        )}

        {sc.condition && (
          <div>
            <div className="text-[11px] font-bold uppercase tracking-wide text-muted-light mb-0.5">{t("scenario.condition")}</div>
            <RichText text={sc.condition} />
          </div>
        )}

        <div className="rounded-lg border-l-2 border-primary bg-primary/[0.08] px-3 py-2">
          <div className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wide text-primary-dark mb-0.5">
            <Target size={13} className="shrink-0" /> {t("scenario.priceZone")}
          </div>
          {sc.price_zone ? <span className="font-semibold text-primary-dark"><RichText text={sc.price_zone} /></span> : <span className="text-muted-light">—</span>}
        </div>

        <div className="rounded-lg border-l-2 border-primary bg-primary/[0.06] px-3 py-2">
          <div className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wide text-primary-dark mb-1">
            <Crosshair size={13} className="shrink-0" /> {t("scenario.strategy")}
          </div>
          {strategy ? (
            <dl className="flex flex-col gap-1.5">
              {strategy.map((it, i) => (
                <div key={i}>
                  <dt className="text-[12px] font-bold text-label">{it.label}</dt>
                  <dd><RichText text={it.body} /></dd>
                </div>
              ))}
            </dl>
          ) : sc.trading_strategy ? <RichText text={sc.trading_strategy} /> : <span className="text-muted-light">—</span>}
        </div>

        {sc.key_risk && (
          <div className="rounded-lg bg-red-50 px-3 py-2">
            <div className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wide text-down mb-0.5">
              <AlertTriangle size={13} className="shrink-0" /> {t("scenario.keyRisk")}
            </div>
            <span className="text-down"><RichText text={sc.key_risk} /></span>
          </div>
        )}

        {sc.hedge_note && (
          <div className="rounded-lg border-l-2 border-label/40 bg-tint px-3 py-2">
            <div className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wide text-label mb-0.5">
              <ShieldCheck size={13} className="shrink-0" /> {t("scenario.hedge")}
            </div>
            <RichText text={sc.hedge_note} />
          </div>
        )}
      </div>
    </div>
  );
}

function stripMarkdown(text: string) {
  return text.replace(/\*\*/g, "");
}

// Tách câu "chốt" (tổng hợp/kết luận) ra khỏi phần phân tích để tô nổi bật riêng
// trong 1 ô đậm — backend đánh dấu các câu này bằng tag "**Tổng hợp:**"/"**Kết
// luận:**" (xem report_generator.py), ở đây chỉ cần tìm tag đó và render khác đi.
// (Từ khi có GROUP_HEADLINE_PREFIXES bên dưới, kết luận của TỪNG NHÓM trong Mục
// 3 không còn dùng tag "**Kết luận:**" nữa — đã gộp thẳng vào dòng tiêu đề đầu
// nhóm, xem renderAnalysisLine — nhưng regex này vẫn giữ "Kết luận" để tương
// thích các nơi khác trong báo cáo còn dùng tag đó.)
// content_en: "**Summary:**"/"**Conclusion:**" (backend dịch tag theo bảng thuật ngữ cố định).
const CONCLUSION_TAG_RE = /\*\*(?:Tổng hợp|Kết luận|Summary|Conclusion)\s*:?\*\*/;

// Riêng dòng "**Tổng hợp:**" cuối mục 3 (kết luận chung về giá EUA — xem
// _prompt_section3/KẾT LUẬN CHUNG CHO CẢ MỤC "PHÂN TÍCH" trong report_generator.py)
// cần TÁCH RA khỏi các block phân tích để đưa lên đầu báo cáo, làm phần "Nhận
// định" trong khối "🚨 ĐIỂM NHẤN" gộp chung với "Tóm tắt điều hành".
const EUA_SUMMARY_TAG_RE = /\*\*(?:Tổng hợp|Summary)\s*:?\*\*/;

// Nhãn xu hướng giá EUA do backend ghi ngay sau "**Tổng hợp:**" (xem
// _prompt_section3 trong report_generator.py): [TÍCH CỰC]/[TRUNG LẬP]/[TIÊU CỰC]
// — quyết định màu khung "Nhận định". Báo cáo cũ (chưa có nhãn) mặc định trung lập.
type EuaSentiment = "positive" | "neutral" | "negative";
const EUA_SENTIMENT_RE = /^\s*\[\s*(TÍCH CỰC|TRUNG LẬP|TIÊU CỰC|POSITIVE|NEUTRAL|NEGATIVE)\s*\]\s*/i;
const EUA_SENTIMENT_MAP: Record<string, EuaSentiment> = {
  "TÍCH CỰC": "positive",
  "TRUNG LẬP": "neutral",
  "TIÊU CỰC": "negative",
  // content_en
  "POSITIVE": "positive",
  "NEUTRAL": "neutral",
  "NEGATIVE": "negative",
};
// Tiêu cực: nền đỏ chữ trắng; Trung lập: nền vàng chữ đen; Tích cực: nền xanh chữ trắng.
// print-color-adjust: exact — giữ màu nền khi in PDF (trình duyệt mặc định bỏ nền).
const EUA_SENTIMENT_STYLE: Record<EuaSentiment, { box: string }> = {
  negative: { box: "bg-[#FBE9E7] border border-[#EFB8B0] text-[#8A3B30]" },
  neutral: { box: "bg-[#FFF6D6] border border-[#EBD98F] text-[#7A6320]" },
  positive: { box: "bg-[#E6F3EA] border border-[#B5D8C0] text-[#2D6A44]" },
};

function parseEuaSentiment(summary: string): { sentiment: EuaSentiment; text: string } {
  const match = summary.match(EUA_SENTIMENT_RE);
  if (!match) return { sentiment: "neutral", text: summary };
  return {
    sentiment: EUA_SENTIMENT_MAP[match[1].toUpperCase()] ?? "neutral",
    text: summary.slice(match[0].length).trim(),
  };
}

// Dòng tiêu đề-kết luận đầu mỗi nhóm trong Mục 3, heading "Phân tích" (xem
// _prompt_section3::ĐỊNH DẠNG trong report_generator.py — dòng đầu tiên của
// mỗi nhóm PHẢI viết liền "Tên nhóm: <kết luận>") — đóng khung nổi bật NGUYÊN
// dòng này (tên nhóm + kết luận) thay vì hiện như 1 dòng chữ thường.
const GROUP_HEADLINE_PREFIXES = [
  "Năng lượng & nhiên liệu hóa thạch:",
  "Hạn ngạch & tín chỉ carbon:",
  "Chính sách:",
  // content_en (bảng thuật ngữ cố định của backend)
  "Energy & fossil fuels:",
  "Allowances & carbon credits:",
  "Policy:",
];

// Chỉ thay đổi VỊ TRÍ hiển thị, không đổi nội dung: rút đúng 1 dòng "**Tổng
// hợp:**" (nếu có) ra khỏi analysis_blocks, phần còn lại của block giữ nguyên.
function extractEuaSummary(blocks: any[] | undefined): { cleanedBlocks: any[]; summary: string | null } {
  if (!blocks || blocks.length === 0) return { cleanedBlocks: blocks || [], summary: null };
  let summary: string | null = null;
  const cleanedBlocks = blocks.map((block: any) => {
    const lines: string[] = (block.content || "").split("\n").filter((l: string) => l.trim());
    const keptLines: string[] = [];
    lines.forEach((line) => {
      const match = line.match(EUA_SUMMARY_TAG_RE);
      if (match && match.index !== undefined && !summary) {
        const before = line.slice(0, match.index).trim();
        if (before) keptLines.push(before);
        summary = line.slice(match.index).replace(EUA_SUMMARY_TAG_RE, "").trim();
      } else {
        keptLines.push(line);
      }
    });
    return { ...block, content: keptLines.join("\n") };
  });
  return { cleanedBlocks, summary };
}

// Chỉ thay đổi VỊ TRÍ hiển thị, không đổi nội dung: rút NGUYÊN block có
// heading "Cần theo dõi" ra khỏi analysis_blocks của Mục 3 để hiển thị thành
// mục riêng, đứng giữa "Kịch bản hành động" và "Gợi ý kinh doanh & giải pháp
// cho SIM" thay vì trong Mục 3 — nội dung watchpoint do backend sinh giữ
// nguyên (vẫn liệt kê "1.", "2."... không có ngày giờ cụ thể vì đây không
// phải sự kiện có lịch, chỉ là điểm cần theo dõi).
function extractWatchpoints(blocks: any[] | undefined): { cleanedBlocks: any[]; watchpoints: string | null } {
  if (!blocks || blocks.length === 0) return { cleanedBlocks: blocks || [], watchpoints: null };
  const watchBlock = blocks.find((b: any) => b.heading === "Cần theo dõi");
  const cleanedBlocks = blocks.filter((b: any) => b.heading !== "Cần theo dõi");
  return { cleanedBlocks, watchpoints: watchBlock?.content || null };
}

// Placeholder outcome mà backend cố tình ghi (xem _prompt_section8 /
// _split_prev_events trong services/report_generator.py) khi tin tức KHÔNG xác
// nhận được kết quả thực tế — dùng nội bộ để đánh dấu sự kiện "đã xử lý xong,
// không hỏi lại nữa" ở lượt sinh báo cáo kế tiếp. Với người đọc, dòng này
// không mang thông tin gì (EIA/API/Baker Hughes/đấu giá EUA hầu như không bao
// giờ có bài báo xác nhận số liệu cụ thể) nên ẩn hẳn khỏi hiển thị thay vì
// hiện "Kết quả: Chưa có thông tin kết quả xác nhận" gây nhiễu mỗi lần.
const UNCONFIRMED_OUTCOME_TEXT = "Chưa có thông tin kết quả xác nhận";

function ConclusionAware({ text, className }: { text: string; className?: string }) {
  const match = text.match(CONCLUSION_TAG_RE);
  if (!match || match.index === undefined) {
    return <p className={className}><RichText text={text} /></p>;
  }
  const before = text.slice(0, match.index).trim();
  const highlight = text.slice(match.index).trim();
  return (
    <div className="space-y-1.5">
      {before && <p className={className}><RichText text={before} /></p>}
      <div className="rounded-md border border-primary/30 bg-tint/60 px-3 py-2">
        <p className="text-[14.5px] leading-[1.5] font-bold text-primary-dark">
          <RichText text={highlight} />
        </p>
      </div>
    </div>
  );
}

// r.up chỉ phản ánh chiều biến động theo NGÀY — không thể dùng chung để tô màu cột
// Δ Tuần, vì tuần có thể ngược chiều với ngày. Suy màu trực tiếp từ dấu của chuỗi giá trị.
function isPositiveDelta(value: string) {
  return typeof value === "string" ? !value.trim().startsWith("-") : true;
}

// Bố cục báo cáo không còn đánh số thứ tự dạng "01"/"02" như trước — thay bằng
// 3 cấp heading rõ ràng theo phân cấp thị giác:
//  - PartHeading: đầu mục lớn nhất ("Phần 1/2/3", hoặc đứng riêng như "Tóm tắt
//    điều hành"/"Nguồn tham khảo" không cần nhãn "Phần").
//  - FramedHighlight: khung viền nổi bật, tiêu đề là dải nền màu nằm trong khung
//    — dùng cho các khối cần nhấn mạnh nhất (Tóm tắt điều hành, Khuyến nghị vị thế).
//  - SubHeading: đầu mục con trong 1 Phần, dải nền xanh dương full-width + in
//    đậm để dễ quét mắt mà không cần số thứ tự.
// Icon chỉ giữ lại ở PartHeading (Phần 1/2/3, Nguồn tham khảo) — FramedHighlight
// và SubHeading không có icon.
// Class "report-heading" dùng chung để CSS in ấn (globals.css) nhận diện, ép
// không ngắt trang ngay sau heading.

function PartHeading({ eyebrow, title, icon: Icon, className }: { eyebrow?: string; title: string; icon: typeof Sparkles; className?: string }) {
  const label = eyebrow ? `${eyebrow}: ${title}` : title;
  return (
    <div className={clsx("report-heading relative mb-6 print:break-after-avoid", className)}>
      <div className="flex items-center gap-3">
        <span className="flex items-center justify-center w-10 h-10 rounded-xl bg-primary-dark text-white shrink-0 shadow-[0_2px_10px_rgba(15,95,90,0.28)]">
          <Icon size={19} strokeWidth={2.25} />
        </span>
        <div className="text-[17px] sm:text-[20px] font-extrabold uppercase tracking-tight text-primary-dark leading-[1.3]">
          {label}
        </div>
      </div>
      <div className="mt-3 h-[3px] w-full bg-gradient-to-r from-primary via-primary/30 to-transparent rounded-full" />
    </div>
  );
}

// Khung viền nổi bật, tiêu đề nằm hẳn TRONG khung (dải nền màu trên cùng, chữ
// trắng) — không dùng nhãn bo tròn đè lên viền trên nữa. Dùng cho Tóm tắt điều
// hành (tone "blue", đầu báo cáo) và KHUYẾN NGHỊ VỊ THẾ (tone "primary", đầu Phần 2).
// Kiểu chữ DÙNG CHUNG cho tiêu đề các khối nổi bật (Nhận định tổng quan, Tóm tắt
// điều hành, Khuyến nghị vị thế) — cùng cỡ, in hoa, căn giữa.
const BLOCK_TITLE_CLASS = "text-center text-[15px] sm:text-[16px] font-bold uppercase tracking-wide leading-tight";

const FRAME_TONE = {
  primary: { frame: "border-primary-dark", band: "bg-primary-dark" },
  blue: { frame: "border-[#8fc7a5]", band: "bg-[#5fa57e]" },
};

function FramedHighlight({
  title,
  icon: Icon,
  children,
  className,
  tone = "primary",
}: {
  title: string;
  // Icon lucide (SVG) thay cho emoji — emoji không có trong Arial, khi in PDF
  // sẽ bị trình duyệt thay bằng font emoji khác, lệch font với phần còn lại.
  icon?: React.ElementType;
  children: React.ReactNode;
  className?: string;
  tone?: keyof typeof FRAME_TONE;
}) {
  return (
    <div
      className={clsx(
        "report-frame rounded-lg border-2 overflow-hidden shadow-[var(--shadow-soft)]",
        FRAME_TONE[tone].frame,
        className
      )}
    >
      {/* print-color-adjust: exact — giữ màu nền dải tiêu đề khi in PDF */}
      <div
        className={clsx(
          "report-heading flex items-center justify-center gap-2 px-3.5 sm:px-5 py-2 text-white [print-color-adjust:exact] [-webkit-print-color-adjust:exact]",
          FRAME_TONE[tone].band
        )}
      >
        {Icon && <Icon size={15} strokeWidth={2.5} aria-hidden="true" />}
        <h2 className={BLOCK_TITLE_CLASS}>{title}</h2>
      </div>
      <div className="px-3.5 sm:px-5 py-3">{children}</div>
    </div>
  );
}

// Đầu mục con (trong 1 Phần) — không dùng chấm/gạch đầu dòng hay icon (icon chỉ
// giữ lại ở PartHeading: Phần 1/2/3, Nguồn tham khảo), thay bằng dải nền xanh
// lá nhạt rộng bằng đúng chiều ngang nội dung (khớp với vạch gạch dưới của
// PartHeading, không tràn ra ngoài viền báo cáo) để tạo điểm nhấn riêng biệt
// với "Phần". Chữ màu xanh lá đậm trên nền xanh lá nhạt (không dùng tông xanh
// dương).
function SubHeading({ children }: { children: React.ReactNode }) {
  return (
    <div className="report-heading flex items-center w-full mb-4 px-3 py-2 bg-green-100 rounded-md print:break-after-avoid">
      <h3 className="text-[15.5px] sm:text-[16.5px] font-extrabold tracking-tight text-green-800">
        {children}
      </h3>
    </div>
  );
}

// Danh sách chấm tròn dùng chung cho các mục bullet trong Phần 2 (thay cho
// gạch đầu dòng "-").
// Nguồn tin đặt NGAY CUỐI nội dung (cùng dòng) — inline-block nên cả cụm "Nguồn: X ↗" chỉ xuống dòng khi phần còn lại
// của dòng không đủ chỗ (yêu cầu 21/10/2026; trước nằm riêng 1 dòng dưới nội dung). Đặt BÊN TRONG đoạn văn chứa nội dung.
function InlineSource({ name, url, className }: { name: string; url?: string | null; className?: string }) {
  const t = useTranslations("reportDoc");
  const cls = clsx("inline-block max-w-full font-mono text-[11px] leading-[1.4]", className);
  return (
    <>
      {" "}
      {url ? (
        <a href={url} target="_blank" rel="noopener noreferrer" className={clsx(cls, "text-primary hover:underline")}>
          {t("body.source")} {name} <ExternalLink size={10} className="inline -mt-0.5" aria-hidden="true" />
        </a>
      ) : (
        <span className={clsx(cls, "text-muted-light")}>{t("body.source")} {name}</span>
      )}
    </>
  );
}

// twoCol: màn rộng (xl) chia 2 cột để dòng chữ không quá dài khi báo cáo trải hết bề ngang — không áp dụng khi in.
function DotBullets({ items, render, twoCol }: { items: any[]; render: (item: any, i: number) => React.ReactNode; twoCol?: boolean }) {
  return (
    <ul className={clsx("list-none space-y-2.5", twoCol && "xl:grid xl:grid-cols-2 xl:gap-x-10 xl:gap-y-2.5 xl:space-y-0")}>
      {items.map((item, i) => (
        <li key={i} className="flex gap-2.5">
          <span className="mt-[9px] w-1.5 h-1.5 rounded-full bg-black shrink-0" aria-hidden="true" />
          <div className="flex-1 min-w-0">{render(item, i)}</div>
        </li>
      ))}
    </ul>
  );
}

// Timeline sự kiện Mục 8 — tách riêng để tái dùng cho cả 2 nhóm "Kết quả" (đã
// có outcome) và "Sự kiện sắp tới" (chưa diễn ra), thay vì 1 danh sách gộp lẫn
// lộn cả hai như trước.
function EventTimeline({ events }: { events: any[] }) {
  const t = useTranslations("reportDoc");
  const enumLabel = useEnumLabel();
  return (
    <div>
      {events.map((ev: any, i: number) => {
        const isLast = i === events.length - 1;
        const impactDot =
          ev.impact === "Cao" ? "border-down" :
            ev.impact === "Trung" ? "border-warn" : "border-muted-light";
        return (
          <div key={i} className="flex gap-3 sm:gap-4">
            <div className="w-[52px] sm:w-[60px] shrink-0 flex items-center justify-center rounded-lg border border-border bg-tint/50 py-1.5 mt-0.5">
              <span className="font-mono text-[11px] font-bold text-primary-dark leading-[1.3]">{ev.datetime_vn || "—"}</span>
            </div>
            <div className="flex flex-col items-center shrink-0">
              <span className={clsx("w-2.5 h-2.5 rounded-full border-2 bg-background mt-3.5 shrink-0", impactDot)} />
              {!isLast && <span className="w-px flex-1 bg-border mt-1" />}
            </div>
            <div className={clsx("flex-1 min-w-0", !isLast && "pb-4")}>
              <div className="flex items-start justify-between gap-3 pt-1">
                <span className="text-[14.5px] text-foreground leading-[1.3]">{ev.event}</span>
                <span className={clsx(
                  "shrink-0 font-mono text-[10px] uppercase px-1.5 py-0.5 rounded border",
                  ev.impact === "Cao" ? "text-down border-down/30 bg-red-50" :
                    ev.impact === "Trung" ? "text-warn border-warn/30 bg-warn-tint" :
                      "text-muted-light border-border"
                )}>{enumLabel(ev.impact)}</span>
              </div>
              {ev.outcome && (
                <p className="mt-1.5 text-[14.5px] leading-[1.5] text-body italic">
                  <span className="font-semibold not-italic text-label">{t("events.outcome")}</span>{ev.outcome}
                </p>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// Bảng gợi ý kinh doanh (Mục SIM) — mỗi gợi ý là 1 hàng, cột theo đúng cấu trúc
// nhân quả (kích hoạt → hành động → lý do / cơ hội → giải pháp → kỳ vọng) thay vì
// gộp thành 1 đoạn văn dài, để dễ quét theo hàng như các bảng chuẩn khác trong báo cáo.
// Nút gỡ gợi ý kinh doanh — chỉ hiện ở màn hình admin; type="button" nên tự ẩn khi in.
function DismissButton({ onClick, className }: { onClick: () => void; className?: string }) {
  const t = useTranslations("reportDoc");
  return (
    <button
      type="button"
      onClick={onClick}
      title={t("biz.dismiss")}
      aria-label={t("biz.dismiss")}
      className={clsx(
        "inline-flex items-center justify-center w-6 h-6 rounded-md border border-down/30 text-down bg-background hover:bg-red-50 transition-colors print:hidden",
        className
      )}
    >
      <Trash2 size={12} aria-hidden="true" />
    </button>
  );
}

const POLICY_IMPACT_TONE: Record<string, string> = {
  "tăng": "text-up border-up/30 bg-up/10",
  "giảm": "text-down border-down/30 bg-red-50",
  "trung lập": "text-muted-light border-border bg-surface",
};

// Tách nhánh "nếu hồ sơ bị BÁC" khỏi câu cơ chế (báo cáo cũ viết dính: "…→ NỚI cung; nếu Hội đồng BÁC đề nghị → … → hỗ trợ
// EUA", hiện ngay dưới nhãn "Áp lực EUA" nên trông như nhãn sai). Port 1-1 report_generator.split_policy_mechanism — sửa cả 2.
const REJECTED_BRANCH_RE = /[;.]\s*(?:còn\s+)?(?:nếu|khi)\s+(?=[^;.]*?(?:(?<![\p{L}])bác(?![\p{L}])|bị từ chối|không được (?:thông qua|chấp nhận)))/iu;

function splitPolicyMechanism(it: Record<string, any>): [string | null, string | null] {
  const text = String(it.mechanism || "").trim();
  const explicit = String(it.mechanism_if_rejected || "").trim() || null;
  const m = text ? REJECTED_BRANCH_RE.exec(text) : null;
  if (!m) return [text || null, explicit];
  const rest = text.slice(m.index + m[0].length).trim();
  return [text.slice(0, m.index).trim() || null, explicit || (rest ? rest[0].toUpperCase() + rest.slice(1) : null)];
}

function EtsPolicyTable({ items }: { items: Record<string, any>[] }) {
  const t = useTranslations("reportDoc");
  return (
    // Không giữ nguyên khối khi in: bảng nhiều hồ sơ không vừa phần còn lại của trang thì bị đẩy cả bảng sang trang sau,
    // để trống — từng hàng đã không bị cắt đôi (tr) và tên cột lặp lại ở trang sau.
    <div className="mb-5">
      <h4 className="font-mono text-[11.5px] font-bold uppercase tracking-widest text-primary-dark mb-2">{t("etsPolicy.title")}</h4>
      <div className="overflow-x-auto border border-border rounded-lg">
        <table className="w-full table-fixed border-collapse text-[13.5px]">
          <thead>
            <tr className="bg-tint">
              {["item", "status", "nextStep", "impact"].map((k) => (
                <th key={k} className={clsx(
                  "text-left font-mono text-[10px] uppercase tracking-wider text-primary-dark px-2 sm:px-3 py-2 border-b-2 border-primary/30",
                  k === "impact" ? "w-[110px] sm:w-[170px]" : k === "item" ? "w-[26%]" : "",
                )}>{t(`etsPolicy.${k}`)}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {items.map((it, i) => (
              <tr key={i} className="align-top print:break-inside-avoid">
                <td className="px-2 sm:px-3 py-2 font-semibold text-label break-words">
                  {it.item}
                  {/* Nguồn liền sau tên hồ sơ, chỉ xuống dòng khi hết chỗ (inline-block). */}
                  {it.source_url ? (
                    <>{" "}<a href={it.source_url} target="_blank" rel="noopener noreferrer"
                       className="inline-block max-w-full text-[11.5px] font-normal text-primary hover:underline break-words">
                      {it.source_name}{it.source_date ? ` · ${it.source_date}` : ""}
                    </a></>
                  ) : it.source_name && (
                    // Hồ sơ admin xác nhận (ets_policy_dossiers.yaml) có thể chưa có link.
                    <>{" "}<span className="inline-block max-w-full text-[11.5px] font-normal text-muted-light break-words">
                      {it.source_name}{it.source_date ? ` · ${it.source_date}` : ""}
                    </span></>
                  )}
                  {it.stage && <span className="block text-[11px] font-normal text-muted-light">{it.stage}</span>}
                  {it.pinned && it.verified === false && (
                    <span className="block text-[11px] font-normal italic text-muted-light">{t("etsPolicy.unverified")}</span>
                  )}
                </td>
                <td className="px-2 sm:px-3 py-2 text-body break-words">
                  <RichText text={it.status || ""} />
                  {it.cbam_effect && (
                    <span className="block mt-0.5 text-[12px] text-primary-dark">{t("etsPolicy.cbamEffect")} {it.cbam_effect}</span>
                  )}
                </td>
                <td className="px-2 sm:px-3 py-2 text-body break-words"><RichText text={it.next_step || ""} /></td>
                <td className="px-2 sm:px-3 py-2">
                  <span className={clsx("inline-block font-mono text-[10.5px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded border",
                    POLICY_IMPACT_TONE[it.impact] || POLICY_IMPACT_TONE["trung lập"])}>
                    {t(`etsPolicy.impactLabel.${it.kind === "event" ? "event" : it.impact === "tăng" ? "up" : it.impact === "giảm" ? "down" : "neutral"}`)}
                  </span>
                  {(() => {
                    const [main, rejected] = splitPolicyMechanism(it);
                    return (
                      <>
                        {main && (
                          <span className="block mt-1 text-[11.5px] leading-[1.4] text-muted-light">
                            {it.kind !== "event" && <span className="font-semibold text-label">{t("etsPolicy.ifAdopted")} </span>}
                            {main}
                          </span>
                        )}
                        {rejected && (
                          <span className="block mt-1 pt-1 border-t border-dashed border-border text-[11.5px] leading-[1.4] text-muted-light">
                            <span className="font-semibold text-label">{t("etsPolicy.ifRejected")} </span>{rejected}
                          </span>
                        )}
                      </>
                    );
                  })()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function BizRecommendationTable({
  heading,
  accent,
  rows,
  columns,
  infoKey,
  infoLabel,
  extras,
  onDismiss,
}: {
  heading: string;
  accent: string;
  rows: Record<string, any>[] | undefined;
  columns: { key: string; label: string }[];
  // Trường ẩn sau nút "i" ở ô cuối mỗi hàng (vd "reason" — Lý do), bấm mới mở
  // ra thành 1 dòng phụ ngay dưới hàng đó — bảng gọn hơn. Khi in luôn hiện.
  infoKey?: string;
  infoLabel?: string;
  // Trường phụ hiện cùng dòng "i" (KPI, quy mô thí điểm, đơn vị phụ trách...) — báo cáo cũ không có thì bỏ qua.
  extras?: { key: string; label: string }[];
  // Chỉ truyền ở màn hình admin: nút gỡ gợi ý (hàng phải có "id" — gợi ý ngắn hạn
  // lưu trong bộ nhớ biz_suggestions).
  onDismiss?: (id: number) => void;
}) {
  const t = useTranslations("reportDoc");
  const infoName = infoLabel || t("biz.detailsLower");
  const [openInfo, setOpenInfo] = useState<Set<number>>(new Set());
  const toggleInfo = (i: number) =>
    setOpenInfo((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  const colWidthCls = columns.length === 2 ? "w-1/2" : "w-1/3";
  return (
    <div>
      <h4 className={clsx("font-mono text-[11.5px] font-bold uppercase tracking-widest mb-3", accent)}>{heading}</h4>
      {rows && rows.length > 0 ? (
        <div className="overflow-x-auto border border-border rounded-lg">
          {/* table-fixed + w-1/3 trên 3 cột nội dung: mỗi trường trong prompt tối đa
              1 câu ngắn (xem _prompt_biz_recommendation) nên độ dài tương đương nhau
              — chia đều tránh cột auto-size lệch nhau theo độ dài chữ thực tế, giống
              cách "Bảng giá nhanh" đã làm ở trên. */}
          <table className="w-full table-fixed border-collapse text-[14.5px]">
            <thead>
              <tr>
                {/* Cột # rộng hơn khi có nút gỡ (admin) để số thứ tự + nút xếp dọc, căn giữa cân đối */}
                <th className={clsx(
                  "text-center font-mono text-[10px] uppercase tracking-wider text-primary-dark px-1 py-2 sm:py-2.5 border-b-2 border-primary/30 border-r border-border bg-tint",
                  onDismiss ? "w-[44px]" : "w-[32px] sm:w-[36px]"
                )}>#</th>
                {columns.map((col, i) => (
                  <th
                    key={col.key}
                    className={clsx(
                      colWidthCls,
                      "text-left font-mono text-[10px] uppercase tracking-wider text-primary-dark px-2 sm:px-3 py-2 sm:py-2.5 border-b-2 border-primary/30 bg-tint",
                      i < columns.length - 1 && "border-r border-border"
                    )}
                  >
                    {col.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map((row, ri) => {
                const extraRows = (extras || []).filter((x) => typeof row[x.key] === "string" && row[x.key].trim());
                const info = (infoKey ? row[infoKey] : "") || (extraRows.length ? " " : "");
                const isOpen = openInfo.has(ri);
                return (
                  <Fragment key={ri}>
                    <tr className="align-top even:bg-surface/60 hover:bg-tint/40 transition-colors">
                      <td className="px-1 py-2.5 sm:py-3 border-r border-border bg-surface font-mono text-[12px] text-muted-light">
                        <div className="flex flex-col items-center gap-2">
                          <span className="leading-6">{ri + 1}</span>
                          {onDismiss && typeof row.id === "number" && (
                            <DismissButton onClick={() => onDismiss(row.id)} />
                          )}
                        </div>
                      </td>
                      {columns.map((col, i) => {
                        const isLast = i === columns.length - 1;
                        return (
                          <td
                            key={col.key}
                            className={clsx("px-2 sm:px-3 py-2.5 sm:py-3 text-body leading-[1.5] break-words", !isLast && "border-r border-border")}
                          >
                            {isLast && info ? (
                              <div className="flex items-start gap-2">
                                <div className="flex-1 min-w-0"><RichText text={row[col.key] || ""} /></div>
                                <button
                                  type="button"
                                  onClick={() => toggleInfo(ri)}
                                  aria-expanded={isOpen}
                                  aria-label={isOpen ? t("biz.hideInfo", { label: infoName }) : t("biz.showInfo", { label: infoName })}
                                  title={isOpen ? t("biz.hideInfo", { label: infoName }) : t("biz.showInfo", { label: infoName })}
                                  className={clsx(
                                    "shrink-0 mt-0.5 inline-flex items-center justify-center w-5 h-5 rounded-full border transition-colors print:hidden",
                                    isOpen ? "bg-primary text-white border-primary" : "text-primary border-primary/40 hover:bg-tint"
                                  )}
                                >
                                  <Info size={12} strokeWidth={2.5} aria-hidden="true" />
                                </button>
                              </div>
                            ) : (
                              <RichText text={row[col.key] || ""} />
                            )}
                          </td>
                        );
                      })}
                    </tr>
                    {info && (
                      <tr className={clsx(isOpen ? "table-row" : "hidden print:table-row")}>
                        <td className="border-r border-border bg-surface" />
                        <td colSpan={columns.length} className="px-2 sm:px-3 py-2 bg-tint/50 text-[14.5px] leading-[1.5] text-body">
                          {infoKey && row[infoKey] && (
                            <div>
                              <span className="font-bold text-primary-dark mr-1">{infoLabel || t("biz.details")}:</span>
                              <RichText text={row[infoKey]} />
                            </div>
                          )}
                          {extraRows.map((x) => (
                            <div key={x.key}>
                              <span className="font-bold text-primary-dark mr-1">{x.label}:</span>
                              <RichText text={row[x.key]} />
                            </div>
                          ))}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-[14.5px] text-muted-light italic">{t("biz.empty")}</p>
      )}
    </div>
  );
}

// Bước chia trục "đẹp" (1/2/2.5/5 × 10^n) để mức giá trên trục là số tròn, dễ đọc.
function niceStep(range: number, targetTicks: number) {
  const raw = range / Math.max(1, targetTicks);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const nice = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10;
  return nice * mag;
}

function formatVolume(v: number) {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(v >= 10_000 ? 0 : 1)}K`;
  return String(Math.round(v));
}

function CandlestickChart({ report }: { report: Report }) {
  const locale = useLocale();
  const t = useTranslations("reportDoc");
  const rawData = report?.content["2"]?.chart_data;
  const candles = rawData && rawData.length > 0 ? rawData : [];
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  // Vẽ theo đúng bề rộng thật của khung (đo bằng ResizeObserver) thay vì kéo giãn 1
  // viewBox cố định bằng preserveAspectRatio="none" — kiểu cũ làm méo chữ và nến.
  // 640 là bề rộng mặc định khi chưa đo được (SSR, lần render đầu, in PDF).
  const wrapRef = useRef<HTMLDivElement>(null);
  const [measuredW, setMeasuredW] = useState(640);
  // Khi in: vẽ lại theo đúng bề rộng vùng in A4 (~640px sau khi trừ margin/padding)
  // thay vì bề rộng màn hình — in từ điện thoại (bề rộng ~340px) sẽ không còn ra
  // biểu đồ to tướng, chữ phóng to. flushSync để render xong TRƯỚC khi trình duyệt
  // chụp layout in.
  const [printing, setPrinting] = useState(false);
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(entries => {
      const w = Math.round(entries[0].contentRect.width);
      if (w > 0) setMeasuredW(w);
    });
    ro.observe(el);
    const before = () => flushSync(() => setPrinting(true));
    const after = () => setPrinting(false);
    window.addEventListener("beforeprint", before);
    window.addEventListener("afterprint", after);
    return () => {
      ro.disconnect();
      window.removeEventListener("beforeprint", before);
      window.removeEventListener("afterprint", after);
    };
  }, []);
  const W = printing ? 640 : measuredW;

  if (candles.length === 0) {
    return (
      <div className="w-full h-[300px] sm:h-[360px] flex items-center justify-center text-muted-light text-sm border border-border rounded-lg bg-background">
        {t("chart.updating")}
      </div>
    );
  }

  const compact = W < 480;
  const H = printing ? 290 : compact ? 300 : 360;
  // Trục giá đặt bên PHẢI (kiểu terminal giao dịch) để nhãn giá hiện tại nằm sát nến cuối.
  const padL = 4, padR = compact ? 50 : 58, padT = 8, padB = 24;
  const gap = 14;
  const volH = compact ? 48 : 64;
  const plotH = H - padT - padB - gap - volH;
  const plotW = W - padL - padR;
  const volTop = padT + plotH + gap;
  const volBottom = volTop + volH;

  const min = Math.min(...candles.map((c: any) => c.low));
  const max = Math.max(...candles.map((c: any) => c.high));
  const range = max - min || Math.abs(max) * 0.01 || 1;
  const pMin = min - range * 0.08;
  const pMax = max + range * 0.08;
  const step = niceStep(pMax - pMin, compact ? 4 : 5);
  const ticks: number[] = [];
  for (let v = Math.ceil(pMin / step) * step; v <= pMax + 1e-9; v += step) ticks.push(v);
  const tickDecimals = step >= 1 ? 0 : step >= 0.1 ? 1 : 2;

  const maxVol = Math.max(...candles.map((c: any) => c.volume || 0));
  const hasVolume = maxVol > 0;

  const yScale = (v: number) => padT + plotH - ((v - pMin) / (pMax - pMin)) * plotH;
  const cw = plotW / candles.length;
  const bodyW = Math.max(2, Math.min(cw * 0.66, 14));
  // +0.5 để đường 1px rơi đúng giữa pixel → nét sắc, không bị nhoè 2px.
  const xOf = (i: number) => Math.round(padL + i * cw + cw / 2) + 0.5;

  // Nhãn ngày: lấy mốc từ CUỐI về đầu để nhãn phiên gần nhất luôn có và không đè nhau.
  const maxLabels = Math.max(2, Math.floor(plotW / (compact ? 56 : 70)));
  const xLabelStep = Math.max(1, Math.ceil(candles.length / maxLabels));
  const xLabelIdx = new Set<number>();
  for (let i = candles.length - 1; i >= 0; i -= xLabelStep) xLabelIdx.add(i);

  const last = candles[candles.length - 1];
  const lastUp = last.close >= last.open;
  const lastY = yScale(last.close);

  const handlePointer = (e: React.MouseEvent<SVGSVGElement> | React.TouchEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const clientX = "touches" in e ? e.touches[0]?.clientX : e.clientX;
    if (clientX === undefined) return;
    const x = (clientX - rect.left) * (W / rect.width);
    setHoverIndex(Math.min(candles.length - 1, Math.max(0, Math.floor((x - padL) / cw))));
  };

  // Dải thông tin OHLC phía trên chart (kiểu TradingView) thay cho tooltip nổi — không
  // che nến, đọc được cả trên mobile. Mặc định hiện phiên gần nhất.
  const activeIdx = hoverIndex ?? candles.length - 1;
  const active = candles[activeIdx];
  const prevClose = activeIdx > 0 ? candles[activeIdx - 1].close : active.open;
  const chg = active.close - prevClose;
  const chgPct = prevClose ? (chg / prevClose) * 100 : 0;
  const activeUp = active.close >= active.open;
  const hoverX = hoverIndex !== null ? xOf(hoverIndex) : 0;
  const hoverY = hoverIndex !== null ? yScale(active.close) : 0;
  const dateTagW = 64;

  return (
    <div ref={wrapRef} className="relative w-full">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-1 mb-2 font-mono text-[11.5px] tabular-nums min-h-[18px]">
        <span className="text-label font-semibold">{formatFullDate(active.date)}</span>
        {(["open", "high", "low", "close"] as const).map((k, j) => (
          <span key={k} className="whitespace-nowrap">
            <span className="text-muted-light mr-1">{["O", "H", "L", "C"][j]}</span>
            <span className={clsx("font-semibold", activeUp ? "text-up" : "text-down")}>{fnum(active[k], 2, locale)}</span>
          </span>
        ))}
        <span className={clsx("font-semibold whitespace-nowrap", chg >= 0 ? "text-up" : "text-down")}>
          {chg >= 0 ? "+" : ""}{fnum(chg, 2, locale)} ({chg >= 0 ? "+" : ""}{fnum(chgPct, 2, locale)}%)
        </span>
        {hasVolume && active.volume !== undefined && (
          <span className="whitespace-nowrap">
            <span className="text-muted-light mr-1">Vol</span>
            <span className="text-foreground">{active.volume.toLocaleString(locale === "en" ? "en-US" : "vi-VN")}</span>
          </span>
        )}
      </div>

      <svg
        width="100%" viewBox={`0 0 ${W} ${H}`} className="candle-chart block w-full h-auto cursor-crosshair select-none touch-pan-y"
        role="img" aria-label={t("chart.aria", { count: candles.length, close: fnum(last.close, 2, locale) })}
        onMouseMove={handlePointer}
        onMouseLeave={() => setHoverIndex(null)}
        onTouchStart={handlePointer}
        onTouchMove={handlePointer}
        onTouchEnd={() => setHoverIndex(null)}
      >
        {/* Lưới ngang + trục giá (phải) */}
        {ticks.map(v => {
          const y = Math.round(yScale(v)) + 0.5;
          return (
            <g key={`grid-${v}`}>
              <line x1={padL} y1={y} x2={W - padR} y2={y} stroke="var(--color-border)" strokeWidth="1" />
              <text x={W - padR + 8} y={y + 3.5} className="font-mono text-[10.5px] fill-muted-light tabular-nums">
                {fnum(v, tickDecimals, locale)}
              </text>
            </g>
          );
        })}

        {/* Lưới dọc nhẹ tại các mốc ngày */}
        {[...xLabelIdx].map(i => (
          <line key={`vg-${i}`} x1={xOf(i)} y1={padT} x2={xOf(i)} y2={hasVolume ? volBottom : padT + plotH}
            stroke="var(--color-border)" strokeWidth="1" opacity={0.6} />
        ))}

        {/* Khung volume */}
        {hasVolume && (
          <>
            <line x1={padL} y1={volTop - gap / 2} x2={W - padR} y2={volTop - gap / 2} stroke="var(--color-border-soft)" strokeWidth="1" />
            <text x={W - padR + 8} y={volTop + 9} className="font-mono text-[10px] fill-muted-light tabular-nums">{formatVolume(maxVol)}</text>
            <text x={padL + 2} y={volTop + 9} className="font-mono text-[9.5px] fill-muted-light uppercase tracking-wide">Vol</text>
          </>
        )}
        <line x1={padL} y1={(hasVolume ? volBottom : padT + plotH) + 0.5} x2={W - padR} y2={(hasVolume ? volBottom : padT + plotH) + 0.5}
          stroke="var(--color-border-soft)" strokeWidth="1" />

        {/* Trục ngày (X) */}
        {[...xLabelIdx].map(i => (
          <text key={`xl-${i}`} x={xOf(i)} y={H - 7} textAnchor="middle" className="font-mono text-[10.5px] fill-muted-light tabular-nums">
            {formatShortDate(candles[i].date)}
          </text>
        ))}

        {/* Volume */}
        {hasVolume && candles.map((c: any, i: number) => {
          if (!c.volume) return null;
          const vH = Math.max(1, (c.volume / maxVol) * (volH - 12));
          const isUp = c.close >= c.open;
          const isHover = hoverIndex === i;
          return (
            <rect key={`v-${i}`} x={xOf(i) - bodyW / 2} y={volBottom - vH} width={bodyW} height={vH} rx={1}
              fill={isUp ? "var(--color-up)" : "var(--color-down)"}
              opacity={isHover ? 0.75 : hoverIndex !== null ? 0.18 : 0.32} />
          );
        })}

        {/* Đường giá đóng cửa gần nhất */}
        <line x1={padL} y1={Math.round(lastY) + 0.5} x2={W - padR} y2={Math.round(lastY) + 0.5}
          stroke={lastUp ? "var(--color-up)" : "var(--color-down)"} strokeWidth="1" strokeDasharray="3,3" opacity={0.7} />

        {/* Nến */}
        {candles.map((c: any, i: number) => {
          const x = xOf(i);
          const isUp = c.close >= c.open;
          const color = isUp ? "var(--color-up)" : "var(--color-down)";
          const yO = yScale(c.open), yC = yScale(c.close);
          const bodyTop = Math.min(yO, yC);
          const bodyH = Math.max(Math.abs(yC - yO), 1.5);
          return (
            <g key={`c-${i}`} opacity={hoverIndex !== null && hoverIndex !== i ? 0.45 : 1}>
              <line x1={x} y1={yScale(c.high)} x2={x} y2={yScale(c.low)} stroke={color} strokeWidth="1" />
              <rect x={x - bodyW / 2} y={bodyTop} width={bodyW} height={bodyH} rx={Math.min(1.5, bodyW / 6)} fill={color} />
            </g>
          );
        })}

        {/* Nhãn giá hiện tại trên trục phải */}
        <g>
          <rect x={W - padR + 2} y={lastY - 9} width={padR - 4} height={18} rx={3}
            fill={lastUp ? "var(--color-up)" : "var(--color-down)"} />
          <text x={W - padR / 2} y={lastY + 3.8} textAnchor="middle" className="font-mono text-[10.5px] font-bold tabular-nums" fill="#fff">
            {fnum(last.close, 2, locale)}
          </text>
        </g>

        {/* Crosshair khi hover */}
        {hoverIndex !== null && (
          <g pointerEvents="none">
            <line x1={hoverX} y1={padT} x2={hoverX} y2={hasVolume ? volBottom : padT + plotH}
              stroke="var(--color-muted-light)" strokeWidth="1" strokeDasharray="3,3" />
            <line x1={padL} y1={hoverY} x2={W - padR} y2={hoverY}
              stroke="var(--color-muted-light)" strokeWidth="1" strokeDasharray="3,3" />
            <rect x={W - padR + 2} y={hoverY - 9} width={padR - 4} height={18} rx={3} fill="var(--color-label)" />
            <text x={W - padR / 2} y={hoverY + 3.8} textAnchor="middle" className="font-mono text-[10.5px] font-bold tabular-nums" fill="#fff">
              {fnum(active.close, 2, locale)}
            </text>
            <rect x={Math.min(Math.max(hoverX - dateTagW / 2, padL), W - padR - dateTagW)} y={H - padB + 3} width={dateTagW} height={18} rx={3} fill="var(--color-label)" />
            <text x={Math.min(Math.max(hoverX, padL + dateTagW / 2), W - padR - dateTagW / 2)} y={H - 7} textAnchor="middle"
              className="font-mono text-[10.5px] font-bold tabular-nums" fill="#fff">
              {formatShortDate(active.date)}
            </text>
          </g>
        )}
      </svg>
    </div>
  );
}

// Tiếng Việt: "Ngày dd tháng MM năm yyyy"; tiếng Anh: "October 16, 2026" (date-fns).
function formatReportDate(dateStr: string, locale: string, t: ReportT) {
  if (!dateStr) return "";
  const parts = dateStr.split("-");
  if (parts.length !== 3) return dateStr;

  const d = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, parseInt(parts[2]));
  if (isNaN(d.getTime())) return dateStr;

  if (locale === "en") {
    return t("masthead.date", {
      day: String(d.getDate()),
      month: formatDateFns(d, "MMMM", { locale: dateFnsLocale(locale) }),
      year: parts[0],
    });
  }
  return t("masthead.date", { day: parts[2], month: parts[1], year: parts[0] });
}

const VIETNAMESE_DAYS = ["Chủ Nhật", "Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy"];

// "YYYY-MM-DD" -> "Thứ Ba"... (dựng Date theo giờ địa phương từ từng phần — tránh
// new Date("YYYY-MM-DD") parse theo UTC làm lệch 1 ngày ở múi giờ âm).
function reportWeekday(dateStr: string, locale: string) {
  const parts = (dateStr || "").split("-");
  if (parts.length !== 3) return "";
  const d = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, parseInt(parts[2]));
  if (isNaN(d.getTime())) return "";
  return locale === "en" ? formatDateFns(d, "EEEE", { locale: dateFnsLocale(locale) }) : VIETNAMESE_DAYS[d.getDay()];
}

function formatVietnameseDateFull(dateStr: string) {
  if (!dateStr) return "";
  const parts = dateStr.split("-");
  if (parts.length !== 3) return dateStr;

  const d = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, parseInt(parts[2]));
  if (isNaN(d.getTime())) return dateStr;

  const dayOfWeek = VIETNAMESE_DAYS[d.getDay()];
  return `${dayOfWeek}, ngày ${parts[2]} tháng ${parts[1]} năm ${parts[0]}`;
}

// Chấm icon tăng/giảm cho các dòng "Nhận định thị trường" — màu & mũi tên theo dữ liệu thật.
function TrendDot({ up }: { up: boolean }) {
  return (
    <div className={clsx("mt-0.5 shrink-0 w-[22px] h-[22px] rounded-full flex items-center justify-center text-white", up ? "bg-up" : "bg-down")}>
      {up ? <ChevronUp size={14} strokeWidth={3} /> : <ChevronDown size={14} strokeWidth={3} />}
    </div>
  );
}

// printHeading: tiêu đề Phần 1 CHỈ hiện khi in, nằm chung khối "không cắt" với biểu đồ — Chrome không dời tiêu đề
// (break-after: avoid) theo biểu đồ khi biểu đồ không vừa phần còn lại của trang, để tiêu đề trơ cuối trang 1.
function EuaDashboardWidget({ report, printHeading }: { report: Report; printHeading?: React.ReactNode }) {
  const locale = useLocale();
  const t = useTranslations("reportDoc");
  const chartData = report.content["2"]?.chart_data || [];
  if (chartData.length === 0) return null;

  const lastClose = chartData[chartData.length - 1].close;
  const lastDate = chartData[chartData.length - 1].date;
  const prevClose = chartData.length > 1 ? chartData[chartData.length - 2].close : lastClose;
  const dayChange = lastClose - prevClose;
  const dayChangePct = prevClose ? (dayChange / prevClose) * 100 : 0;
  
  const dayOpen = chartData[chartData.length - 1].open;
  const dayHigh = chartData[chartData.length - 1].high;
  const dayLow = chartData[chartData.length - 1].low;
  const dayRange = dayHigh - dayLow;
  const dayRangePct = lastClose ? (dayRange / lastClose) * 100 : 0;
  
  // Δ 1 tuần: phiên gần nhất cách ≥ 7 ngày LỊCH — cùng định nghĩa với "Δ tuần" của bảng giá (crawler/backend),
  // trước đây lấy lùi 7 PHIÊN (~9–11 ngày) nên có lúc ra chiều ngược với Δ tuần trong bài.
  const weekCutoff = new Date(new Date(lastDate).getTime() - 7 * 86400000).toISOString().slice(0, 10);
  const weekRow = [...chartData].reverse().find((c: { date: string }) => c.date <= weekCutoff);
  const sevenDaysAgo = weekRow ? weekRow.close : chartData[0].close;
  const sevenDayChange = lastClose - sevenDaysAgo;
  const sevenDayChangePct = sevenDaysAgo ? (sevenDayChange / sevenDaysAgo) * 100 : 0;

  const monthHigh = Math.max(...chartData.map((c: any) => c.high));
  const monthLow = Math.min(...chartData.map((c: any) => c.low));
  const firstClose = chartData[0].close;
  const monthChange = lastClose - firstClose;
  const monthChangePct = firstClose ? (monthChange / firstClose) * 100 : 0;

  const vol = chartData[chartData.length - 1].volume || 0;
  // TB khối lượng 20 phiên LIỀN TRƯỚC phiên mới nhất, bỏ phiên volume <= 0 (dữ liệu lỗi) — cùng công thức
  // với `_eua_volume_summary` (services/report_generator.py). Backend không lưu sẵn trường nào cho số này.
  const priorVols = chartData
    .slice(0, -1)
    .map((c: any) => c.volume)
    .filter((v: any) => typeof v === "number" && v > 0)
    .slice(-20);
  const avgVol = priorVols.length ? priorVols.reduce((a: number, b: number) => a + b, 0) / priorVols.length : 0;
  const volChangePct = avgVol && vol > 0 ? ((vol - avgVol) / avgVol) * 100 : 0;

  return (
    // Khung nền xám chỉ dùng trên màn hình >= sm. Mobile bỏ khung để các thẻ dùng hết
    // bề ngang; khi in bỏ hẳn (nền xám + break-inside-avoid của từng tầng từng để lại
    // 1 dải xám cuối trang khi biểu đồ bị đẩy sang trang sau).
    // In: block thay vì flex — Chrome không áp "break-after: avoid" của tiêu đề Phần 1 cho chỗ ngắt TRONG khung
    // flex, nên biểu đồ bị đẩy sang trang sau còn tiêu đề nằm trơ cuối trang trước.
    <div className="flex flex-col gap-3 sm:gap-4 sm:bg-surface-alt sm:border sm:border-border sm:rounded-xl sm:p-5 sm:shadow-[var(--shadow-soft)] print:bg-transparent! print:border-0! print:p-0! print:shadow-none! print:block! print:space-y-3">
      {/* Tầng 1 — Biểu đồ nến, full chiều ngang (khi in kèm tiêu đề Phần 1 trong cùng khối không cắt) */}
      <div className="min-w-0 print:break-inside-avoid">
      {printHeading && <div className="hidden print:block">{printHeading}</div>}
      <div className="bg-background border border-border rounded-xl p-3.5 sm:p-5 shadow-sm min-w-0 print:p-4! print:shadow-none! print:break-inside-avoid">
        <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3 mb-4 pb-4 border-b border-border">
          <div className="flex items-center gap-3 min-w-0">
            <div className="bg-primary text-white p-2 rounded-xl shadow-sm shrink-0">
              <Leaf size={24} />
            </div>
            <div className="min-w-0">
              <h3 className="font-bold text-label text-[17px] flex flex-wrap items-center gap-x-2 gap-y-1">
                EUA Dec-26
                {/* Chỉ có dữ liệu 1M (chart_data 30 phiên) — nhãn tĩnh, không phải nút chọn khung. */}
                <span className="px-2 py-0.5 rounded-md text-[11px] font-bold bg-primary text-white">1M</span>
                <span className="text-muted-light font-normal text-[13px]">{t("dash.candles")}</span>
              </h3>
              <p className="text-muted-light text-[13px] mt-0.5">{t("dash.marketDesc")}</p>
            </div>
          </div>
          <div className="flex flex-col items-end gap-1 ml-auto">
            <div className="flex items-baseline gap-2.5">
              <span className="text-[30px] sm:text-[34px] font-bold leading-none text-label tabular-nums">{fnum(lastClose, 2, locale)}</span>
              <span className={clsx("inline-flex items-center gap-0.5 text-white text-[13px] font-bold px-2.5 py-1 rounded-full tabular-nums", dayChange >= 0 ? "bg-up" : "bg-down")}>
                {dayChange >= 0 ? <ChevronUp size={15} strokeWidth={3} /> : <ChevronDown size={15} strokeWidth={3} />}
                {dayChange > 0 ? "+" : ""}{fnum(dayChange, 2, locale)} ({dayChangePct > 0 ? "+" : ""}{fnum(dayChangePct, 2, locale)}%)
              </span>
            </div>
            <div className="flex items-center text-[12px] text-muted-light gap-1.5 font-medium">
              <Calendar size={13} /> {t("dash.closeOf", { date: formatFullDate(lastDate) })}
            </div>
          </div>
        </div>

        <CandlestickChart report={report} />
      </div>
      </div>

      {/* Tầng 2 — Số liệu */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 print:break-inside-avoid">
        <StatTile icon={<RefreshCw size={14} />} label={t("dash.lastRange")}
          value={`${fnum(dayLow, 2, locale)} – ${fnum(dayHigh, 2, locale)}`}
          sub={t("dash.rangeSub", { range: fnum(dayRange, 2, locale), pct: fnum(dayRangePct, 1, locale) })} />
        <StatTile
          icon={sevenDayChange >= 0 ? <TrendingUp size={14} className="text-up" /> : <TrendingDown size={14} className="text-down" />}
          label={sevenDayChange >= 0 ? t("dash.up7") : t("dash.down7")}
          value={`${sevenDayChange > 0 ? "+" : ""}${fnum(sevenDayChange, 2, locale)}`}
          valueClass={sevenDayChange >= 0 ? "text-up" : "text-down"}
          sub={t("dash.vsPrice", { pct: `${sevenDayChangePct > 0 ? "+" : ""}${fnum(sevenDayChangePct, 2, locale)}`, price: fnum(sevenDaysAgo, 2, locale) })} />
        <StatTile icon={<CalendarRange size={14} />} label={t("dash.range30")}
          value={`${fnum(monthLow, 2, locale)} – ${fnum(monthHigh, 2, locale)}`}
          sub={t("dash.lowHigh")} />
        <StatTile
          icon={monthChange >= 0 ? <TrendingUp size={14} className="text-up" /> : <TrendingDown size={14} className="text-down" />}
          label={t("dash.change30")}
          value={`${fnum(firstClose, 2, locale)} → ${fnum(lastClose, 2, locale)}`}
          sub={`${monthChangePct >= 0 ? t("dash.up") : t("dash.down")} ${fnum(Math.abs(monthChangePct), 1, locale)}%`}
          subClass={monthChangePct >= 0 ? "text-up font-bold" : "text-down font-bold"} />
      </div>

      {/* Tầng 3 — Nhận định */}
      <div className="bg-background rounded-xl p-3.5 sm:p-5 border border-border shadow-sm print:p-4! print:shadow-none! print:break-inside-avoid">
        <div className="flex items-center gap-2 text-primary-dark font-bold text-[14px] mb-4">
          <div className="p-1.5 rounded-full bg-tint"><Info size={16} /></div> {t("dash.insight")}
        </div>
        {/* Cùng thứ tự với 4 ô số liệu phía trên: Phiên gần nhất → 7 ngày → 30 phiên → khối lượng. */}
        <div className="grid md:grid-cols-2 gap-x-8 gap-y-3.5 text-[13px] text-body">
          <div className="flex items-start gap-2.5">
            <TrendDot up={lastClose >= dayOpen} />
            <div className="leading-snug"><strong className="text-label font-bold">{t("dash.lastSessionLabel")}</strong> {t("dash.lastSession", { open: fnum(dayOpen, 2, locale), high: fnum(dayHigh, 2, locale), low: fnum(dayLow, 2, locale), close: fnum(lastClose, 2, locale), dir: lastClose >= dayOpen ? t("dash.up") : t("dash.down"), range: fnum(dayRange, 2, locale), pct: fnum(dayRangePct, 1, locale) })}</div>
          </div>
          <div className="flex items-start gap-2.5">
            <TrendDot up={sevenDayChange >= 0} />
            <div className="leading-snug"><strong className="text-label font-bold">{t("dash.trend7Label")}</strong> {t("dash.trend7", { dir: sevenDayChange >= 0 ? t("dash.up") : t("dash.down"), abs: fnum(Math.abs(sevenDayChange), 2, locale), pct: `${sevenDayChangePct > 0 ? "+" : ""}${fnum(sevenDayChangePct, 2, locale)}` })}</div>
          </div>
          <div className="flex items-start gap-2.5">
            <TrendDot up={monthChange >= 0} />
            <div className="leading-snug"><strong className="text-label font-bold">{t("dash.eua30Label")}</strong> {t("dash.eua30", { first: fnum(firstClose, 2, locale), last: fnum(lastClose, 2, locale), pct: `${monthChangePct > 0 ? "+" : ""}${fnum(monthChangePct, 1, locale)}`, high: fnum(monthHigh, 2, locale), low: fnum(monthLow, 2, locale) })}</div>
          </div>
          <div className="flex items-start gap-2.5">
            <TrendDot up={volChangePct >= 0} />
            <div className="leading-snug"><strong className="text-label font-bold">{t("dash.volumeLabel")}</strong> {t("dash.volume", { vol: vol.toLocaleString(locale === "en" ? "en-US" : "vi-VN"), date: formatFullDate(lastDate) })}{avgVol > 0 ? t("dash.volVsAvg", { dir: volChangePct >= 0 ? t("dash.higher") : t("dash.lower"), n: priorVols.length, avg: Math.round(avgVol).toLocaleString(locale === "en" ? "en-US" : "vi-VN"), pct: `${volChangePct > 0 ? "+" : ""}${fnum(volChangePct, 1, locale)}` }) : "."}</div>
          </div>
        </div>
      </div>
    </div>
  );
}

// Ô số liệu dưới biểu đồ EUA — cùng khung, cùng cỡ chữ để 4 ô thẳng hàng.
function StatTile({ icon, label, value, sub, valueClass, subClass }: {
  icon: React.ReactNode; label: string; value: string; sub: string; valueClass?: string; subClass?: string;
}) {
  return (
    <div className="bg-background border border-border rounded-xl p-3 sm:p-3.5 shadow-sm min-w-0 print:shadow-none!">
      <div className="text-[12px] text-muted-light font-bold mb-1.5 flex items-center gap-1.5">{icon} {label}</div>
      <div className={clsx("text-[16px] font-bold mb-0.5 tabular-nums", valueClass ?? "text-label")}>{value}</div>
      <div className={clsx("text-[11.5px] tabular-nums", subClass ?? "text-muted-light font-medium")}>{sub}</div>
    </div>
  );
}

// 1 gợi ý kinh doanh của báo cáo trước vừa tới tình huống kích hoạt (content["biz"].reminders — services/biz_memory.py).
interface BizReminder {
  id?: number; suggested_date: string; action: string; trigger: string;
  outcome?: string; evidence?: string; source_name?: string; source_url?: string;
}

/**
 * Toàn bộ nội dung "tờ báo cáo" (masthead → footer) — dùng chung cho cả màn
 * hình user (chỉ xem báo cáo đã published) và màn hình admin duyệt báo cáo,
 * để không lặp lại JSX giữa 2 nơi.
 */
export function ReportDocument({
  report,
  onDismissBizSuggestion,
}: {
  report: Report;
  // Chỉ màn hình admin truyền vào — hiện nút gỡ gợi ý kinh doanh (services/biz_memory.py).
  onDismissBizSuggestion?: (id: number) => void;
}) {
  const t = useTranslations("reportDoc");
  const locale = useLocale();
  const enumLabel = useEnumLabel();
  const priceRows = report?.content["2"]?.prices || [];
  // Báo cáo Chủ Nhật/Thứ Hai: ngày dữ liệu cuối tuần, không có phiên giao dịch — backend
  // (report_generator.py::is_no_price_report) chỉ phân tích tin tức; ẩn mọi phần giá.
  const noPriceData = !!report?.content["2"]?.no_price_data;
  // Menu hamburger điều hướng section (thanh dưới banner).
  const [navOpen, setNavOpen] = useState(false);
  const keyDevelopmentItems: any[] = [
    ...(report.content["2"]?.key_developments ?? []),
    ...((report.content["4"]?.bullets ?? [])
      .map((b: any) => (typeof b === "string" ? { text: b } : b))
      .filter((b: any) => b?.text && !/không có diễn biến|\bno (?:significant |notable |major |new )?developments?\b/i.test(b.text))),
  ];

  // Thanh trượt giá: tên hợp đồng + giá + Δ ngày, lấy thẳng từ Bảng giá nhanh.
  const tickerItems = priceRows
    .filter((r: any) => r.name && r.price)
    .map((r: any) => {
      // r.price dạng "<số> <đơn vị>" (vd "81.4400 EUR/tCO2") — tách để định dạng số, giữ đơn vị.
      const [rawNumber, ...unitParts] = String(r.price).split(" ");
      return {
        name: r.name as string,
        price: formatCompactPriceNumber(rawNumber, locale),
        unit: subCO2(unitParts.join(" ")),
        dday: r.dday && r.dday !== "-" ? formatDelta(String(r.dday), locale) : null,
      };
    });
  // Lặp danh sách cho đủ ≥ 12 mục mỗi bản — ít hợp đồng thì 1 bản có thể hẹp hơn
  // bề ngang thanh trên màn rộng, lộ khoảng trống khi vòng lặp chạy.
  const tickerLoop = tickerItems.length > 0
    ? Array.from({ length: Math.ceil(12 / tickerItems.length) }, () => tickerItems).flat()
    : [];
  // Mục 3 mặc định hiện đủ phân tích (Diễn biến chính -> Cần theo dõi); ẩn được để
  // user chỉ cần xem nhanh bảng Kịch bản hành động, không cần đọc hết phần phân tích.
  const [showAnalysis, setShowAnalysis] = useState(true);
  // Khung "Jenny cập nhật đề xuất trước đây" thu vào nút "i" — bấm mới mở (in PDF luôn hiện nội dung, ẩn nút).
  const [showReminders, setShowReminders] = useState(false);
  // Mục "Review dự báo và khuyến nghị": bảng chấm dự báo + gợi ý kinh doanh cũ vừa tới tình huống kích hoạt.
  const hasForecastReview = report.content["2"]?.forecast_review?.items?.length > 0;
  const bizReminders: BizReminder[] = report.content["biz"]?.reminders ?? [];
  const remindersContradicted = bizReminders.filter(r => r.outcome === "contradicted").length;
  const remindersHappened = bizReminders.length - remindersContradicted;

  // Rút dòng "**Tổng hợp:**" (kết luận chung giá EUA) ra khỏi các block phân
  // tích để đưa lên đầu báo cáo, làm phần "Nhận định" trong khối "🚨 ĐIỂM
  // NHẤN" gộp chung với "Tóm tắt điều hành" cũ — xem extractEuaSummary ở trên.
  const { cleanedBlocks: blocksAfterSummary, summary: euaSummary } = extractEuaSummary(report.content["3"]?.analysis_blocks);
  const euaVerdict = euaSummary ? parseEuaSentiment(euaSummary) : null;
  // "Cần theo dõi" giờ hiển thị thành mục riêng, giữa "Kịch bản hành động" và
  // "Gợi ý kinh doanh & giải pháp cho SIM" — xem extractWatchpoints ở trên.
  const { cleanedBlocks: analysisBlocks, watchpoints } = extractWatchpoints(blocksAfterSummary);
  const hasMarketDrivers =
    report.content["2"]?.market_drivers?.bullish?.length > 0 || report.content["2"]?.market_drivers?.bearish?.length > 0;
  // KHUYẾN NGHỊ VỊ THẾ (đầu Phần 2) tái dùng đúng 3 kịch bản đã có trong
  // trading_scenarios (Mục 3) + OHLC 30 phiên (chart_data) — không cần trường
  // dữ liệu riêng từ backend (xem PositionCard).
  const tradingScenarios: Scenario[] = report.content["3"]?.trading_scenarios ?? [];
  const chartData = report.content["2"]?.chart_data || [];


  return (
    // Màn hình: báo cáo trải hết bề ngang (yêu cầu 21/10/2026, trước giới hạn khổ A4 210mm); từ lg/xl các khối
    // xếp nhiều cột để dòng chữ không quá dài. Khi in/xuất PDF, Chrome dàn lại theo bề rộng trang A4 (~718px, xem
    // @page trong globals.css) nên mọi bố cục lg:/xl: không áp dụng — bản in vẫn 1 cột như cũ.
    // Mobile (< sm): báo cáo tràn sát 2 mép màn hình (-mx-4 bù đúng px-4 của <main>
    // trong app/layout.tsx), bỏ viền/bo góc/bóng của card — trước đây lề <main> +
    // viền card + lề nội dung + lề khung FramedHighlight cộng dồn, chữ trong khung
    // chỉ còn ~77% bề ngang màn hình 375px. Lề đọc giờ chỉ còn px-4 của nội dung.
    <div className="report-shell w-full max-sm:-mx-4 max-sm:w-auto bg-background text-foreground font-sans leading-[1.5] rounded-2xl max-sm:rounded-none border border-border max-sm:border-x-0 shadow-[var(--shadow-soft)] max-sm:shadow-none overflow-hidden mb-10">

      {/* Banner Stavian — 1 khối nền liền (không tách 2 nửa gradient ngược chiều,
          vốn tạo 1 đường nối lộ rõ ở giữa): hàng trên logo + ngày phát hành,
          vạch mảnh thụt theo lề nội dung, rồi eyebrow → tiêu đề → người báo cáo. */}
      <div className="relative overflow-hidden bg-gradient-to-br from-[#1B4D3E] via-[#1a5c4a] to-[#143d33] text-white">
        {/* Hoạ tiết chéo — "slice" (không "none") để hình không bị kéo méo theo
            tỉ lệ khung, 1 SVG chung cho cả banner nên các đường chéo liền mạch.
            .report-banner-art — bỏ giới hạn "svg { max-height: 200px }" (dành cho
            biểu đồ) khi in, xem globals.css, nếu không hoạ tiết bị cắt ngang trong PDF. */}
        <svg
          className="absolute inset-0 w-full h-full pointer-events-none report-banner-art"
          viewBox="0 0 800 300"
          preserveAspectRatio="xMaxYMid slice"
          aria-hidden="true"
        >
          <polygon points="520,0 800,0 800,300 680,300" fill="rgba(255,255,255,0.035)" />
          <polygon points="600,0 800,0 800,200 700,300 640,300" fill="rgba(255,255,255,0.025)" />
          <polygon points="0,190 260,300 0,300" fill="rgba(0,0,0,0.08)" />
          <line x1="470" y1="0" x2="630" y2="300" stroke="rgba(255,255,255,0.07)" strokeWidth="1" />
          <line x1="540" y1="0" x2="700" y2="300" stroke="rgba(255,255,255,0.045)" strokeWidth="1" />
        </svg>

        <div className="relative z-10 px-4 sm:px-10 pt-5 sm:pt-6 pb-6 sm:pb-7">
          {/* Hàng trên: logo trái, ngày phát hành phải — căn giữa theo chiều dọc */}
          <div className="flex items-center justify-between gap-4">
            <img
              src="/stavian_logo.png"
              alt="Stavian Industrial Metal"
              className="h-11 sm:h-14 w-auto object-contain shrink-0"
            />
            <div className="text-right">
              {/* Thứ trong tuần của ngày báo cáo (thay cho nhãn "Phát hành" cũ) */}
              <div className="text-[11px] sm:text-[12px] font-bold uppercase tracking-[0.12em] text-[#8fd9a8] mb-0.5">
                {reportWeekday(report.report_date, locale)}
              </div>
              <div className="text-[13px] sm:text-[15px] font-bold leading-snug">
                {formatReportDate(report.report_date, locale, t)}
              </div>
            </div>
          </div>

          {/* Vạch ngăn thụt theo lề nội dung (không tràn sát mép như đường nối cũ) */}
          <div className="h-px bg-white/15 mt-4 sm:mt-5 mb-4 sm:mb-5" />

          <div className="text-[11px] sm:text-[12px] font-semibold tracking-[0.2em] uppercase text-[#8fd9a8] mb-1.5">
            Carbon Market Daily News
          </div>
          <h1 className="text-[21px] sm:text-[28px] font-extrabold tracking-[0.2px] leading-[1.2] [text-wrap:balance]">
            {t("masthead.title")}
          </h1>
          {/* div (không phải p) — tránh rule ".report-shell p { text-align: justify }"
              làm giãn chữ khi dòng này xuống hàng trên mobile. Tên công ty đã có
              trong logo nên ẩn trên mobile cho gọn 1 dòng. */}
          <div className="mt-2 text-[11.5px] sm:text-[12.5px] leading-snug text-white/80">
            {t("masthead.reporter")}<span className="hidden sm:inline"> — Stavian Industrial Metal</span>
          </div>
        </div>

        {/* Viền nhấn dưới banner */}
        <div className="absolute bottom-0 left-0 right-0 h-[3px] bg-gradient-to-r from-[#2f8749] via-[#3da85e] to-[#2f8749]" />
      </div>

      {/* Thanh dưới banner: nút hamburger (menu điều hướng section) bên trái,
          phần còn lại là thanh trượt giá — giá + Δ ngày của từng hợp đồng trong
          Bảng giá nhanh chạy liên tục. Ẩn khi in/xuất PDF. */}
      <nav className="relative z-20 w-full flex items-stretch bg-[#f5f7f6] border-b border-border print:hidden">
        <div className="relative shrink-0 border-r border-border">
          <button
            type="button"
            onClick={() => setNavOpen((o) => !o)}
            aria-expanded={navOpen}
            aria-controls="report-section-menu"
            aria-label={navOpen ? t("nav.close") : t("nav.open")}
            className={clsx(
              "h-full flex items-center gap-2 px-3 sm:px-4 py-2.5 text-[#1B4D3E] transition-colors",
              navOpen ? "bg-[#e8efeb]" : "hover:bg-[#e8efeb]"
            )}
          >
            {navOpen ? <X size={18} strokeWidth={2.5} /> : <Menu size={18} strokeWidth={2.5} />}
            <span className="hidden sm:inline text-[12.5px] font-bold uppercase tracking-wide">{t("nav.toc")}</span>
          </button>

          {navOpen && (
            <>
              {/* Lớp phủ trong suốt — bấm ra ngoài menu thì đóng menu */}
              <div className="fixed inset-0 z-10" onClick={() => setNavOpen(false)} aria-hidden="true" />
              <ul
                id="report-section-menu"
                className="absolute left-0 top-full z-20 mt-px min-w-[220px] bg-background border border-border rounded-b-lg shadow-[var(--shadow-medium)] py-1"
              >
                {REPORT_SECTIONS.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      onClick={() => {
                        setNavOpen(false);
                        document.getElementById(item.id)?.scrollIntoView({ behavior: "smooth", block: "start" });
                      }}
                      className="w-full text-left px-4 py-2.5 text-[13.5px] font-semibold text-[#1B4D3E] hover:bg-[#e8efeb] hover:text-[#2f8749] transition-colors"
                    >
                      {t(`nav.${item.labelKey}`)}
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>

        {tickerItems.length > 0 && (
          <div className="ticker relative flex-1 min-w-0 overflow-hidden flex items-center">
            {/* Nội dung nhân đôi (2 bản liền nhau) + dịch đúng 50% để vòng lặp liền
                mạch không bị giật khi quay lại đầu. Di chuột vào thì tạm dừng. */}
            <div
              className="ticker-track ticker-track-ltr motion-reduce:[animation-play-state:paused]"
              style={{ animationDuration: `${tickerLoop.length * 5}s` }}
            >
              {[0, 1].map((copy) => (
                <div key={copy} className="flex shrink-0 items-center" aria-hidden={copy === 1}>
                  {tickerLoop.map((t: { name: string; price: string; unit: string; dday: string | null }, i: number) => (
                    <span key={i} className="flex items-center gap-2 px-4 sm:px-5 whitespace-nowrap text-[12.5px] sm:text-[13px] border-r border-border/60">
                      <span className="font-bold text-[#1B4D3E]">{t.name}</span>
                      <span className="tabular-nums text-label">
                        {t.price}
                        {t.unit && <span className="ml-1 text-[11px] text-muted-light">{t.unit}</span>}
                      </span>
                      {t.dday && (
                        <span className={clsx("tabular-nums font-semibold", isPositiveDelta(t.dday) ? "text-up" : "text-down")}>
                          {isPositiveDelta(t.dday) ? "▲" : "▼"} {t.dday}
                        </span>
                      )}
                    </span>
                  ))}
                </div>
              ))}
            </div>
          </div>
        )}
      </nav>

      <div className="px-4 sm:px-10 pt-3 pb-10">

        {/* Đầu báo cáo gồm 2 khối xếp dọc:
            1) Nhận định — kết luận "Tổng hợp về giá EUA" (dòng "**Tổng hợp:**"
               cuối Mục 3, xem extractEuaSummary ở trên), khung màu theo nhãn xu
               hướng (đỏ/vàng/xanh — xem EUA_SENTIMENT_STYLE).
            2) Tóm tắt điều hành — khung xanh dương, tiêu đề nằm ngay trong khung
               (dải tiêu đề trên cùng, không dùng nhãn bo tròn đè lên viền), bên
               dưới liệt kê các yếu tố nổi bật trong ngày. */}
        <section id="section-highlights" className="pt-2 pb-5 space-y-4">
          {euaVerdict && (
            <div
              className={clsx(
                // KHÔNG dùng "report-heading" (break-after: avoid khi in) — khung này dính vào bảng dự báo phía
                // sau, bảng dài không vừa trang thì cả 2 bị đẩy sang trang mới, để trống gần hết 1 trang.
                "print:break-inside-avoid rounded-lg px-3.5 py-3 shadow-sm [&_strong]:text-inherit [print-color-adjust:exact] [-webkit-print-color-adjust:exact]",
                EUA_SENTIMENT_STYLE[euaVerdict.sentiment].box
              )}
            >
              <h2 className={clsx(BLOCK_TITLE_CLASS, "mb-1.5")}>{t("body.overview")}</h2>
              {/* Nội dung nhận định: chữ thường, in đậm (KHÔNG in hoa — chỉ tiêu đề in hoa) */}
              <p className="text-[14.5px] sm:text-[15.5px] leading-[1.45] font-bold normal-case text-left">
                <RichText text={euaVerdict.text} />
              </p>
            </div>
          )}

          <FramedHighlight title={t("body.execSummary")} tone="blue">
            {/* Màn rộng: 2 cột (mỗi bullet không bị cắt giữa 2 cột); in/PDF giữ 1 cột. */}
            <ul className="list-none -my-0.5 xl:columns-2 xl:gap-x-10">
              {report.content["1"]?.bullets?.map((bullet: any, i: number) => {
                const b: string = typeof bullet === "string" ? bullet : bullet.text || "";
                const isMatch = b.includes(':');
                const tag = stripMarkdown(isMatch ? b.split(':')[0] : 'Note');
                const text = isMatch ? b.split(':').slice(1).join(':') : b;
                const sourceName = typeof bullet === "object" ? bullet.source_name : null;
                const sourceUrl = typeof bullet === "object" ? bullet.source_url : null;

                return (
                  <li key={i} className="py-1 text-[14.5px] break-inside-avoid">
                    <p className="text-foreground leading-[1.3]">
                      <span className={clsx("font-mono text-[10.5px] border rounded-[3px] px-1 py-px mr-2", tagColorClass(tag.trim()))}>
                        {tag.trim().substring(0, 15)}
                      </span>
                      <RichText text={text.trim()} />
                      {sourceName && <InlineSource name={sourceName} url={sourceUrl} />}
                    </p>
                  </li>
                );
              })}
            </ul>
          </FramedHighlight>
        </section>

        {/* PHẦN 1 — TIN TỨC CHÍNH / NỔI BẬT TRONG NGÀY */}
        {/* Không ép bất kỳ khối nào (chart, bảng giá...) phải nằm trọn 1 trang —
            in tuần tự, tràn trang tự nhiên tới đâu hay tới đó, tránh để lại
            khoảng trắng dài phía trên khi 1 khối lớn bị đẩy nguyên sang trang
            sau vì không đủ chỗ còn lại trên trang hiện tại. */}
        <section id="section-market" className="py-5">
          {/* Có biểu đồ: khi in, tiêu đề Phần 1 được in bên trong khối biểu đồ (EuaDashboardWidget.printHeading) để
              không bị bỏ lại cuối trang 1 — bản này chỉ hiện trên màn hình. */}
          <PartHeading
            eyebrow={t("body.part", { n: 1 })} title={t("body.marketTitle")} icon={LineChart}
            className={chartData.length > 0 ? "print:hidden" : undefined}
          />

          <EuaDashboardWidget
            report={report}
            printHeading={<PartHeading eyebrow={t("body.part", { n: 1 })} title={t("body.marketTitle")} icon={LineChart} />}
          />

          {noPriceData ? (
            <div className="mt-5 rounded-lg border border-border bg-tint/40 px-3.5 py-3 text-[14.5px] leading-[1.5] text-body">
              {report.content["2"]?.no_price_notice || t("body.noPriceFallback")}
            </div>
          ) : (
          <div className="mt-5">
            <SubHeading>{t("body.priceTable")}</SubHeading>
            {report.content["2"]?.price_timestamp && (
              <p className="font-mono text-[11px] text-muted-light -mt-3 mb-4">{report.content["2"].price_timestamp}</p>
            )}

            {/* Bảng giá nhanh, bên dưới là biểu đồ chỉ số giá (bấm → popup "So sánh hiệu suất") — xếp dọc ở mọi bề rộng
                (yêu cầu 21/10/2026, trước đặt song song từ md). Khi in/PDF chỉ giữ bảng giá (biểu đồ cần gọi API + tương tác). */}
            <div className="flex flex-col gap-4 print:block">
            <div className="overflow-x-auto border border-border rounded-lg self-start w-full">
              <table className="w-full sm:table-fixed border-collapse font-mono text-[14.5px]">
                <thead>
                  <tr>
                    <th className="w-[28%] sm:w-[30%] text-left text-primary-dark font-bold text-[11px] uppercase tracking-wider px-1.5 sm:px-2 py-1.5 border-b-2 border-primary/30 border-r border-primary/15 bg-tint">{t("body.contract")}</th>
                    <th className="w-[18%] sm:w-[18%] text-center text-primary-dark font-bold text-[11px] uppercase tracking-wider px-1 sm:px-2 py-1.5 border-b-2 border-primary/30 border-r border-primary/15 bg-tint">{t("body.price")}</th>
                    <th className="w-[54%] sm:w-[52%] text-left text-primary-dark font-bold text-[11px] uppercase tracking-wider px-1.5 sm:px-2 py-1.5 border-b-2 border-primary/30 bg-tint">{t("body.change")}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {priceRows.map((r: any, i: number) => {
                    const [rawPriceNumber, ...priceUnitParts] = String(r.price || "").split(" ");
                    const priceNumber = formatCompactPriceNumber(rawPriceNumber, locale);
                    const priceUnit = subCO2(priceUnitParts.join(" "));
                    return (
                      <tr key={i} className="even:bg-surface/60 hover:bg-tint/40 transition-colors">
                        <td className="px-1.5 sm:px-2 py-1.5 border-r border-border font-sans font-semibold text-label break-words">
                          {r.source_url ? (
                            // Link kiểu quen thuộc (xanh dương + luôn gạch chân + icon ↗)
                            // để tách bạch hẳn với tên cột (xanh ngọc, in hoa) — trước đây
                            // cùng tông xanh ngọc, chỉ gạch chân khi hover nên không nhận
                            // ra được là bấm được (nhất là trên mobile, không có hover).
                            <a
                              href={r.source_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              title={t("body.viewPriceAt", { name: r.name })}
                              className="text-blue-700 underline decoration-blue-700/40 underline-offset-2 hover:text-blue-900 hover:decoration-blue-900 transition-colors"
                            >
                              {r.name}
                              <ExternalLink size={11} className="inline-block ml-1 -mt-0.5 align-middle" aria-hidden="true" />
                            </a>
                          ) : (
                            r.name
                          )}
                        </td>
                        <td className="px-1 sm:px-2 py-1.5 border-r border-border text-center">
                          <div className="flex flex-col items-center leading-[1.3]">
                            <span className="break-words tabular-nums">{priceNumber}</span>
                            {priceUnit && <span className="text-[10px] text-muted-light break-words">{priceUnit}</span>}
                          </div>
                        </td>
                        <td className="px-1.5 sm:px-2 py-1.5 font-sans text-[14.5px] leading-[1.5] text-body">
                          {/* Chỉ hiện Δ Ngày/Δ Tuần; ghi chú giá chỉ giữ cho dòng CBAM
                              (giá chốt theo quý + ngày chốt tiếp theo). */}
                          <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 font-mono text-[10px] sm:text-[11px]">
                            {r.dday !== "-" && (
                              <span className={clsx("tabular-nums", isPositiveDelta(r.dday) ? "text-up" : "text-down")}>
                                {t("body.dDay")} {formatDelta(String(r.dday), locale)}
                              </span>
                            )}
                            {r.dweek !== "-" && (
                              <span className={clsx("tabular-nums", isPositiveDelta(r.dweek) ? "text-up" : "text-down")}>
                                {t("body.dWeek")} {formatDelta(String(r.dweek), locale)}
                              </span>
                            )}
                          </div>
                          {/* Chú thích chất lượng dữ liệu (report_generator.py): phiên không có giao dịch
                              (giá danh nghĩa) / nguồn điều chỉnh hồi tố giá phiên trước. */}
                          {(r.no_trade || r.revision) && (
                            <div className="mt-1 text-[11.5px] leading-[1.45] text-warn">
                              {r.no_trade && <div>⚠ {t("body.noTrade")}</div>}
                              {r.revision && <div>⚠ {r.revision}</div>}
                            </div>
                          )}
                          {r.code === "CBAM" && r.note && (
                            <div className="mt-1 rounded-md bg-tint/60 border border-primary/15 px-2 py-1.5">
                              {/* Báo cáo cũ có thể đã nối thêm ghi chú LLM phía sau — chỉ lấy phần "Giá chốt…" */}
                              {String(r.note).match(/^Giá chốt theo quý, tại ngày .*?\d{4}(?:, ngày chốt giá tiếp theo .*?\d{4})?|^Quarterly (?:settlement|closing|fixed) price.*?\d{4}(?:, next .*?\d{4})?/i)?.[0] ?? r.note}
                            </div>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <PriceIndexPanel
              reportDate={report.report_date}
              tableCodes={priceRows.map((r: any) => String(r.code || "")).filter(Boolean)}
              cbamEstimate={noPriceData ? null : report.content["2"]?.cbam_estimate}
            />
            </div>
          </div>
          )}

          {/* Ước tính giá CBAM quý (VWAP đấu giá EEX + lịch còn lại, tín hiệu phòng vệ) — ngay dưới
              Bảng giá nhanh, cạnh dòng CBAM Certificate. Báo cáo cũ/CN-T2 không có khối này → ẩn. */}
          {!noPriceData && report.content["2"]?.cbam_estimate && (
            <CbamEstimateCard estimate={report.content["2"].cbam_estimate} />
          )}

          {/* Diễn biến chính — tin tức nổi bật tác động cung/cầu EUA (trực tiếp
              hoặc gián tiếp), mỗi tin có nhãn chiều tác động + nguồn bài viết.
              Backend: content["2"].key_developments (report_generator.py::
              _prompt_key_developments). Báo cáo cũ chưa có trường này → ẩn. */}
          {/* Mục 4 cũ "Cập nhật tín chỉ carbon & CBAM" (VCM, thép xanh, CBAM) được gộp
              vào cuối danh sách này; bỏ dòng "không có diễn biến" vì không phải tin. */}
          {keyDevelopmentItems.length > 0 && (
            <div className="mt-6">
              <SubHeading>{t("body.keyDev")}</SubHeading>
              <DotBullets
                twoCol
                items={keyDevelopmentItems}
                render={(d: any) => (
                  // Định dạng: **Tiêu đề bài báo**: tóm tắt + tác động EUA (Nguồn, ngày).
                  // Báo cáo cũ chưa có d.title → chỉ hiện d.text như trước.
                  <p className="text-[14.5px] leading-[1.6] text-foreground">
                    {d.title && <strong className="font-bold text-label">{d.title}: </strong>}
                    <RichText text={d.text} />
                    {d.source_name && (
                      <>
                        {" ("}
                        <a
                          href={d.source_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-blue-700 underline decoration-blue-700/40 underline-offset-2 hover:text-blue-900 hover:decoration-blue-900"
                        >
                          {d.source_name}
                        </a>
                        {d.source_date ? `, ${d.source_date}` : ""})
                      </>
                    )}
                  </p>
                )}
              />
            </div>
          )}
        </section>

        {/* PHẦN 2 — PHÂN TÍCH VÀ KHUYẾN NGHỊ GIAO DỊCH */}
        <section id="section-analysis" className="py-5">
          <PartHeading
            eyebrow={t("body.part", { n: 2 })}
            title={noPriceData ? t("body.part2NoPrice") : t("body.part2")}
            icon={BarChart3}
          />

          {/* KHUYẾN NGHỊ VỊ THẾ — nội dung đầu tiên của Phần 2: lệnh của cả 3 khung thời gian
              (trading_scenarios Mục 3) trên 1 thang giá + phòng hộ CBAM — xem PositionCard.
              Giá hiện tại = giá đóng cửa phiên gần nhất trong chart_data. */}
          {tradingScenarios.some(sc => sc?.horizon === "ngắn hạn") && (
            <PositionCard
              scenarios={tradingScenarios}
              chartData={chartData}
              euaRow={priceRows.find((r: any) => r.code === "EUA")}
              forecastReview={report.content["2"]?.forecast_review}
              cbamEstimate={noPriceData ? null : report.content["2"]?.cbam_estimate}
              hedgeAction={report.content["3"]?.hedge_action}
              openPositions={report.content["3"]?.open_positions}
            />
          )}

          {/* Động lực thị trường — nằm dưới KHUYẾN NGHỊ VỊ THẾ, trên Phân tích
              (chuyển từ Bảng giá nhanh (Phần 1) sang đây trước đó). */}
          {hasMarketDrivers && (
            <div className="mb-6">
              <SubHeading>{t("body.drivers")}</SubHeading>
              <div className="grid sm:grid-cols-2 gap-3">
                <div className="border border-up/25 bg-up/[0.04] rounded-lg overflow-hidden">
                  <div className="flex items-center gap-1.5 bg-up/10 text-up font-mono text-[11px] font-bold uppercase tracking-wider px-3 py-2 border-b border-up/20">
                    <TrendingUp size={14} strokeWidth={2.5} /> {t("body.bullish")}
                  </div>
                  <div className="p-3 space-y-3">
                    {report.content["2"].market_drivers.bullish?.length > 0 ? (
                      report.content["2"].market_drivers.bullish.map((d: any, i: number) => (
                        <div key={i}>
                          <p className="text-[14.5px] leading-[1.5] text-body">
                            <span className={clsx(
                              "font-mono text-[9.5px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded mr-1.5 align-middle whitespace-nowrap",
                              d.tag === "FACT" ? "bg-up/10 text-up border border-up/30" : d.tag === "CLAIM" ? "bg-amber-50 text-amber-700 border border-amber-200" : "bg-blue-50 text-blue-700 border border-blue-200"
                            )}>{d.tag}</span>
                            <RichText text={d.text} />
                            {d.source_url && <InlineSource name={d.source_name} url={d.source_url} className="text-[10.5px]" />}
                          </p>
                        </div>
                      ))
                    ) : (
                      <p className="text-[14.5px] text-muted-light italic">{t("body.noBullish")}</p>
                    )}
                  </div>
                </div>
                <div className="border border-down/25 bg-down/[0.04] rounded-lg overflow-hidden">
                  <div className="flex items-center gap-1.5 bg-down/10 text-down font-mono text-[11px] font-bold uppercase tracking-wider px-3 py-2 border-b border-down/20">
                    <TrendingDown size={14} strokeWidth={2.5} /> {t("body.bearish")}
                  </div>
                  <div className="p-3 space-y-3">
                    {report.content["2"].market_drivers.bearish?.length > 0 ? (
                      report.content["2"].market_drivers.bearish.map((d: any, i: number) => (
                        <div key={i}>
                          <p className="text-[14.5px] leading-[1.5] text-body">
                            <span className={clsx(
                              "font-mono text-[9.5px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded mr-1.5 align-middle whitespace-nowrap",
                              d.tag === "FACT" ? "bg-up/10 text-up border border-up/30" : d.tag === "CLAIM" ? "bg-amber-50 text-amber-700 border border-amber-200" : "bg-blue-50 text-blue-700 border border-blue-200"
                            )}>{d.tag}</span>
                            <RichText text={d.text} />
                            {d.source_url && <InlineSource name={d.source_name} url={d.source_url} className="text-[10.5px]" />}
                          </p>
                        </div>
                      ))
                    ) : (
                      <p className="text-[14.5px] text-muted-light italic">{t("body.noBearish")}</p>
                    )}
                  </div>
                </div>
              </div>
            </div>
          )}

          {report.content["3"] && (
            <div className="mb-6">
              <SubHeading>{t("body.analysis")}</SubHeading>
              {report.content["3"].title && (
                <p className="-mt-2.5 mb-4 ml-[38px] text-[12px] text-muted-light italic print:break-after-avoid">{report.content["3"].title}</p>
              )}

              {(analysisBlocks?.length > 0 || report.content["3"].correlation_analysis) && (
                <button
                  type="button"
                  onClick={() => setShowAnalysis(v => !v)}
                  className="mb-4 flex items-center gap-1.5 font-mono text-[11px] font-bold uppercase tracking-wider text-primary border border-primary/30 bg-tint/50 hover:bg-tint rounded-md px-2.5 py-1.5 transition-colors"
                >
                  {showAnalysis ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
                  {showAnalysis ? t("body.hideAnalysis") : t("body.showAnalysis")}
                </button>
              )}

              {showAnalysis && (
                <>
                  {analysisBlocks?.map((block: any, i: number) => (
                    <div key={i} className="mb-5">
                      {/* Block "Phân tích" trùng với SubHeading "Phân tích" ngay phía trên → ẩn nhãn */}
                      {block.heading !== "Phân tích" && (
                        <h4 className="font-mono text-[11.5px] font-bold uppercase tracking-widest text-primary mb-1.5">{enumLabel(block.heading)}</h4>
                      )}
                      {(() => {
                        const lines: string[] = (block.content || "").split("\n").filter((line: string) => line.trim());
                        const isGroupHeadline = (line: string) =>
                          block.heading === "Phân tích" && GROUP_HEADLINE_PREFIXES.some((p) => line.trim().startsWith(p));
                        const renderLine = (line: string, j: number) => {
                          // Backend đôi khi tự chèn gạch đầu dòng thô ("- ...", có thể kèm
                          // dấu cách/tab thừa hoặc en-dash "–") cho từng mã/ý trong 1 nhóm,
                          // ở BẤT KỲ heading nào (Diễn biến chính, Phân tích...) — đổi hiển
                          // thị sang chấm tròn thay vì gạch ngang, không đổi nội dung chữ
                          // (chỉ bỏ đúng phần tiền tố gạch đầu dòng khớp được).
                          const trimmed = line.trim();
                          // Mục 3 / heading "Phân tích": dòng đầu mỗi nhóm gộp sẵn "Tên nhóm:
                          // <kết luận>" (xem ĐỊNH DẠNG trong _prompt_section3) — đóng khung nổi
                          // bật NGUYÊN dòng này thay vì hiện như chữ thường.
                          if (isGroupHeadline(line)) {
                            return (
                              <div key={j} className="rounded-md border border-primary/30 bg-tint/60 px-3 py-2 print:break-after-avoid">
                                <p className="text-[14.5px] leading-[1.5] font-bold text-primary-dark">
                                  <RichText text={trimmed} />
                                </p>
                              </div>
                            );
                          }
                          const dashMatch = trimmed.match(/^[-–—]\s+/);
                          if (!dashMatch) {
                            return <ConclusionAware key={j} text={line} className="text-[14.5px] leading-[1.5] text-body" />;
                          }
                          return (
                            <div key={j} className="flex gap-2.5">
                              <span className="mt-[9px] w-1.5 h-1.5 rounded-full bg-black shrink-0" aria-hidden="true" />
                              <div className="flex-1 min-w-0">
                                <ConclusionAware text={trimmed.slice(dashMatch[0].length)} className="text-[14.5px] leading-[1.5] text-body" />
                              </div>
                            </div>
                          );
                        };
                        // Nhóm theo dòng tiêu đề-kết luận (Năng lượng / Hạn ngạch / Chính sách): màn rộng (xl) mỗi nhóm
                        // 1 cột cạnh nhau; màn hẹp và khi in vẫn xếp dọc như cũ.
                        const groups: { start: number; lines: string[] }[] = [];
                        lines.forEach((line, j) => {
                          if (isGroupHeadline(line) || !groups.length) groups.push({ start: j, lines: [line] });
                          else groups[groups.length - 1].lines.push(line);
                        });
                        const headlineGroups = groups.filter((g) => isGroupHeadline(g.lines[0])).length;
                        if (headlineGroups < 2) {
                          return <div className="space-y-1.5">{lines.map(renderLine)}</div>;
                        }
                        return (
                          <div className={clsx(
                            "space-y-1.5 xl:space-y-0 xl:grid xl:gap-x-6 xl:gap-y-4 xl:items-start",
                            headlineGroups >= 3 ? "xl:grid-cols-3" : "xl:grid-cols-2",
                          )}>
                            {groups.map((g) => (
                              <div key={g.start} className={clsx("space-y-1.5", !isGroupHeadline(g.lines[0]) && "xl:col-span-full")}>
                                {g.lines.map((line, k) => renderLine(line, g.start + k))}
                              </div>
                            ))}
                          </div>
                        );
                      })()}
                    </div>
                  ))}

                  {/* Giả định mô hình chuyển đổi nhiên liệu — để người đọc kiểm chứng giá EUA hòa vốn
                      (report_generator.fuel_model_note). Báo cáo cũ không có → ẩn. */}
                  {report.content["3"].fuel_model_note && (
                    <p className="mb-5 text-[12.5px] leading-[1.55] text-muted-light italic">
                      <span className="not-italic font-semibold">{t("body.fuelModelNote")}</span>{" "}
                      <HighlightNumbers text={report.content["3"].fuel_model_note} />
                    </p>
                  )}

                  {/* Theo dõi chính sách ETS/MSR — khối cố định (report_generator._resolve_ets_policy, tin chính sách
                      21 ngày). Báo cáo cũ không có → ẩn. */}
                  {Array.isArray(report.content["3"].ets_policy) && report.content["3"].ets_policy.length > 0 && (
                    <EtsPolicyTable items={report.content["3"].ets_policy} />
                  )}

                  {report.content["3"].correlation_analysis && (
                    <div className="border-l-2 border-primary bg-tint/40 rounded-r-lg px-3 sm:px-4 py-3 my-5 space-y-2">
                      <h4 className="font-mono text-[11.5px] font-bold uppercase tracking-widest text-primary-dark mb-1.5">{t("body.correlationTitle")}</h4>
                      {(report.content["3"].correlation_analysis.gas_comment || report.content["3"].correlation_analysis.gas_coal_power) && (
                        <div className="space-y-2.5 mb-1">
                          {report.content["3"].correlation_analysis.gas_comment && (
                            <p className="text-[14.5px] leading-[1.5] text-body"><b className="text-primary-dark font-bold">{t("body.gas")}</b> <RichText text={report.content["3"].correlation_analysis.gas_comment} /></p>
                          )}
                          {report.content["3"].correlation_analysis.coal_comment && (
                            <p className="text-[14.5px] leading-[1.5] text-body"><b className="text-primary-dark font-bold">{t("body.coal")}</b> <RichText text={report.content["3"].correlation_analysis.coal_comment} /></p>
                          )}
                          {report.content["3"].correlation_analysis.power_comment && (
                            <p className="text-[14.5px] leading-[1.5] text-body"><b className="text-primary-dark font-bold">{t("body.power")}</b> <RichText text={report.content["3"].correlation_analysis.power_comment} /></p>
                          )}
                        </div>
                      )}
                      <ConclusionAware
                        text={report.content["3"].correlation_analysis.fuel_switching_chain || report.content["3"].correlation_analysis.gas_coal_power}
                        className="text-[14.5px] leading-[1.5] text-body"
                      />
                      <div className="rounded-md border border-primary/30 bg-tint/60 px-3 py-2">
                        <p className="text-[14.5px] leading-[1.5] font-bold text-primary-dark">
                          <RichText text={report.content["3"].correlation_analysis.eua_conclusion} />
                        </p>
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
          )}

          {/* Kịch bản hành động */}
          {report.content["3"]?.trading_scenarios?.length > 0 && (() => {
            const HORIZON_ORDER = ["ngắn hạn", "trung hạn", "dài hạn"];
            const byHorizon: Record<string, any> = {};
            report.content["3"].trading_scenarios.forEach((sc: any) => { byHorizon[sc.horizon] = sc; });
            const columns = HORIZON_ORDER.filter(h => byHorizon[h]);
            // Cột "Chỉ tiêu" cố định 16%, phần còn lại chia đều cho số khung thời gian
            // thực có (1-3 cột) — table-fixed + % (thay vì min-width theo px) để bảng
            // luôn vừa khít chiều rộng khung chứa, không tràn ra ngoài phải kéo ngang,
            // và fit được cả khi in/xuất PDF (khổ A4 trừ margin ~190mm).
            const dataColWidthPct = (100 - 16) / columns.length;
            // "highlight" đánh dấu 2 hàng quan trọng nhất (vùng giá tham chiếu + chiến
            // lược trading) để tô nổi bật riêng, giúp mắt người đọc bắt được ngay phần
            // thông tin actionable nhất thay vì phải đọc lướt cả bảng.
            const ROWS: { label: string; icon?: typeof Target; highlight?: boolean; render: (sc: any) => React.ReactNode }[] = [
              {
                // Tên hàng ngắn gọn ("Xác suất / Chiều giá" cũ dài, xuống 2 dòng khó
                // nhìn trên mobile) — thông tin nằm ngay trong nội dung, mỗi dòng
                // có nhãn riêng "Chiều giá: ..." / "Xác suất: ...".
                label: t("scenario.assessment"),
                render: (sc) => {
                  const dirMeta = DIRECTION_META[sc.direction];
                  const DirIcon = dirMeta?.icon;
                  return (
                    <div className="flex flex-col gap-1.5 items-start">
                      {sc.probability === "Thấp" && sc.direction !== "đi ngang" && (
                        <span className="font-mono text-[10px] font-bold uppercase tracking-wider text-muted-light">{t("scenario.alternative")}</span>
                      )}
                      {dirMeta && (
                        <span className={clsx("flex items-center gap-1 font-mono text-[10px] uppercase tracking-wider rounded px-1.5 py-0.5 border [print-color-adjust:exact] [-webkit-print-color-adjust:exact]", dirMeta.className)}>
                          <DirIcon size={11} className="shrink-0" /> {t("scenario.direction", { dir: enumLabel(sc.direction) })}
                        </span>
                      )}
                      {sc.probability && (
                        <span className="font-mono text-[10px] uppercase tracking-wider text-body">{t("scenario.probability")} {enumLabel(sc.probability)}</span>
                      )}
                    </div>
                  );
                },
              },
              { label: t("scenario.condition"), render: (sc) => <RichText text={sc.condition} /> },
              {
                label: t("scenario.priceZone"), icon: Target, highlight: true,
                render: (sc) => sc.price_zone ? <span className="font-semibold text-primary-dark"><RichText text={sc.price_zone} /></span> : <span className="text-muted-light">—</span>,
              },
              { label: t("scenario.keyRisk"), icon: AlertTriangle, render: (sc) => <span className="text-down"><RichText text={sc.key_risk} /></span> },
              {
                label: t("scenario.strategy"), icon: Crosshair, highlight: true,
                render: (sc) => sc.trading_strategy ? <RichText text={sc.trading_strategy} /> : <span className="text-muted-light">—</span>,
              },
              // Hàm ý cho sổ phòng hộ SIM (nghĩa vụ CBAM = short carbon tự nhiên → phòng hộ là mua EUA),
              // tách khỏi chiến lược bàn giao dịch. Báo cáo cũ không có trường này → ẩn hàng.
              ...(columns.some(h => byHorizon[h]?.hedge_note) ? [{
                label: t("scenario.hedge"), icon: ShieldCheck,
                render: (sc: any) => sc.hedge_note ? <RichText text={sc.hedge_note} /> : <span className="text-muted-light">—</span>,
              }] : []),
            ];

            return (
              <div className="mb-6">
                <SubHeading>{t("scenario.heading")}</SubHeading>

                {/* Mobile: mỗi khung thời gian 1 tab, đọc liền mạch 1 kịch bản (xem ScenarioTabs).
                      Từ sm+ (kể cả khi in/xuất PDF) dùng bảng % width bên dưới. */}
                <ScenarioTabs horizons={columns} byHorizon={byHorizon} />

                {/* sm+ trên màn hình VÀ khi in/xuất PDF: bảng so sánh nhiều cột.
                      table-fixed + width theo % (thay vì min-width theo px) để bảng luôn
                      co vừa khung chứa — kể cả khổ A4 lúc in — nên không còn cần kéo
                      ngang hay fallback card riêng cho print. */}
                <div className="hidden sm:block border border-border rounded-lg overflow-hidden">
                  <table className="w-full table-fixed border-collapse text-[14.5px]">
                    <thead>
                      <tr>
                        <th style={{ width: "16%" }} className="text-left font-mono text-[10px] uppercase tracking-wider text-primary-dark px-2 sm:px-3 py-2 sm:py-2.5 border-b-2 border-primary/30 border-r border-border bg-tint">{t("scenario.indicator")}</th>
                        {columns.map(h => {
                          const meta = HORIZON_META[h];
                          const Icon = meta.icon;
                          return (
                            <th key={h} style={{ width: `${dataColWidthPct}%` }} className="text-left px-2 sm:px-3 py-2 sm:py-2.5 border-b-2 border-primary/30 bg-tint text-primary-dark">
                              <span className="flex items-center gap-1.5 font-mono text-[11px] font-bold uppercase tracking-wider">
                                <Icon size={13} /> {enumLabel(h)}
                              </span>
                            </th>
                          );
                        })}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                      {ROWS.map((row, ri) => {
                        const RowIcon = row.icon;
                        return (
                          <tr key={ri} className={clsx(
                            "align-top transition-colors",
                            row.highlight ? "bg-primary/[0.06] hover:bg-primary/10" : "even:bg-surface/60 hover:bg-tint/40"
                          )}>
                            <td className={clsx(
                              "px-2 sm:px-3 py-2.5 sm:py-3 border-r text-[14.5px] break-words",
                              row.highlight ? "border-primary/20 bg-primary/[0.08] font-bold text-primary-dark" : "border-border bg-surface font-semibold text-label"
                            )}>
                              <span className="flex items-center gap-1.5">
                                {RowIcon && <RowIcon size={13} className="text-primary-dark shrink-0" />}
                                {row.label}
                              </span>
                            </td>
                            {columns.map(h => (
                              <td key={h} className={clsx(
                                "px-2 sm:px-3 py-2.5 sm:py-3 leading-[1.5] break-words",
                                row.highlight ? "text-label border-l border-primary/10" : "text-body"
                              )}>
                                {byHorizon[h] ? row.render(byHorizon[h]) : <span className="text-muted-light">—</span>}
                              </td>
                            ))}
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>

                {/* Lưu ý — chỉ mang tính tham khảo, không phải khuyến nghị đầu tư trực tiếp. */}
                <div className="mt-3 flex items-start gap-2 rounded-lg border border-warn/30 bg-warn-tint px-3 py-2.5">
                  <Info size={15} className="text-warn shrink-0 mt-0.5" />
                  <p className="text-[14.5px] leading-[1.5] text-body">
                    <b className="text-label">{t("scenario.noteLabel")}</b> {t("scenario.note")}
                  </p>
                </div>
              </div>
            );
          })()}

          {/* Cần theo dõi — tách khỏi Mục 3/8, đứng giữa "Kịch bản hành động" và
              "Gợi ý kinh doanh & giải pháp cho SIM" (theo yêu cầu bố cục báo cáo). */}
          {watchpoints && (
            <div className="mb-6">
              <SubHeading>{t("body.watch")}</SubHeading>
              <div className="space-y-1.5">
                {watchpoints.split("\n").filter((line: string) => line.trim()).map((line: string, j: number) => (
                  <ConclusionAware key={j} text={line} className="text-[14.5px] leading-[1.5] text-body" />
                ))}
              </div>
            </div>
          )}

          {/* Gợi ý kinh doanh & giải pháp cho SIM */}
          {report.content["biz"] && (
            <div className="mb-6">
              <SubHeading>{report.content["biz"].title}</SubHeading>
              {/* Màn rộng: Ngắn hạn | Dài hạn cạnh nhau; màn hẹp và khi in xếp dọc. */}
              <div className="flex flex-col gap-6 xl:grid xl:grid-cols-2 xl:items-start">
                <div>
                  <BizRecommendationTable
                    heading={t("biz.shortTerm")}
                    accent="text-label"
                    rows={report.content["biz"].short_term}
                    columns={[
                      { key: "trigger", label: t("biz.colTrigger") },
                      { key: "action", label: t("biz.colAction") },
                    ]}
                    infoKey="reason"
                    infoLabel={t("biz.reason")}
                    extras={[
                      { key: "kpi", label: t("biz.kpi") },
                      { key: "owner", label: t("biz.owner") },
                    ]}
                    onDismiss={onDismissBizSuggestion}
                  />
                </div>
                <BizRecommendationTable
                  heading={t("biz.longTerm")}
                  accent="text-primary"
                  rows={report.content["biz"].long_term}
                  columns={[
                    { key: "opportunity", label: t("biz.colOpportunity") },
                    { key: "solution", label: t("biz.colSolution") },
                  ]}
                  // Kỳ vọng thu vào nút "i" (như Lý do ở bảng Ngắn hạn) — 3 cột hẹp
                  // trên mobile làm mỗi ô chỉ 2–3 chữ/dòng, rất khó đọc.
                  infoKey="expectation"
                  infoLabel={t("biz.expectation")}
                  extras={[
                    { key: "pilot", label: t("biz.pilot") },
                    { key: "financials", label: t("biz.financials") },
                    { key: "kpi", label: t("biz.kpi") },
                    { key: "owner", label: t("biz.owner") },
                  ]}
                  onDismiss={onDismissBizSuggestion}
                />
              </div>
            </div>
          )}

          {/* Lịch sự kiện 7 ngày tới */}
          {report.content["8"] && (
            <div className="mb-2">
              <SubHeading>{report.content["8"].title}</SubHeading>
              {report.content["8"].events?.length > 0 ? (() => {
                // Tách rõ 2 nhóm thay vì 1 timeline gộp lẫn lộn: sự kiện ĐÃ có "outcome"
                // (đã diễn ra, đã cập nhật kết quả) đứng riêng khỏi sự kiện CHƯA diễn ra —
                // nhóm "sắp tới" sắp xếp theo "date" tăng dần để luôn đúng trình tự thời
                // gian dù thứ tự gốc trong content["8"].events là gì.
                //
                // Loại bỏ hẳn sự kiện chỉ có outcome placeholder "chưa xác nhận" — với
                // các mốc như EIA/API/Baker Hughes/đấu giá EUA, tin tức hầu như không
                // bao giờ xác nhận số liệu cụ thể (xem UNCONFIRMED_OUTCOME_TEXT), nên
                // hiện dòng này mỗi lần chỉ gây nhiễu, không có giá trị cho người đọc.
                const events: any[] = report.content["8"].events.filter(
                  (ev: any) => ev.outcome !== UNCONFIRMED_OUTCOME_TEXT
                );
                const results = events.filter((ev) => ev.outcome);
                const upcoming = events
                  .filter((ev) => !ev.outcome)
                  .slice()
                  .sort((a, b) => (a.date || "").localeCompare(b.date || ""));
                return (
                  <div className={clsx("space-y-5", results.length > 0 && upcoming.length > 0 && "xl:grid xl:grid-cols-2 xl:gap-x-8 xl:space-y-0 xl:items-start")}>
                    {results.length > 0 && (
                      <div>
                        <h4 className="font-mono text-[10.5px] font-bold uppercase tracking-widest text-label mb-2">{t("events.results")}</h4>
                        <EventTimeline events={results} />
                      </div>
                    )}
                    {upcoming.length > 0 && (
                      <div>
                        <h4 className="font-mono text-[10.5px] font-bold uppercase tracking-widest text-label mb-2">{t("events.upcoming")}</h4>
                        <EventTimeline events={upcoming} />
                      </div>
                    )}
                  </div>
                );
              })() : (
                report.content["8"].bullets?.map((b: string, i: number) => (
                  <p key={i} className="text-[14.5px] leading-[1.5] text-body"><RichText text={b} /></p>
                ))
              )}
            </div>
          )}
        </section>

        {/* PHẦN 3 — CHI TIẾT CÁC TIN TỨC CHÍNH */}
        {report.content["6"] && (
          <section id="section-news" className="py-5">
            <PartHeading eyebrow={t("body.part", { n: 3 })} title={report.content["6"].title || t("body.newsTitle")} icon={Newspaper} />
            {[
              { key: "international", label: t("body.intl"), empty: t("body.noNewsIntl") },
              { key: "vietnam", label: t("body.vn"), empty: t("body.noNewsVn") },
            ].map(({ key, label, empty }) => {
              const items = report.content["6"][key];
              // Có tin Việt Nam trong kỳ nhưng không bài nào đủ liên quan → nói đúng như vậy, không ghi "không có tin".
              const vnConsidered = key === "vietnam" ? report.content["6"].vietnam_considered : 0;
              const emptyText = vnConsidered > 0 ? t("body.noNewsVnFiltered", { n: vnConsidered }) : empty;
              return (
                <div key={key} className="mb-4 last:mb-0">
                  <h4 className="font-mono text-[11.5px] font-bold uppercase tracking-widest text-primary-dark mb-2">{label}</h4>
                  {items?.length > 0 ? (
                    // Màn rộng: tin xếp 2 cột. Khi in mỗi tin giữ nguyên khối (tiêu đề không tách khỏi tóm tắt).
                    <div className="space-y-2.5 xl:space-y-0 xl:grid xl:grid-cols-2 xl:gap-x-10 xl:gap-y-3">
                      {items.map((art: any, i: number) => (
                        <div key={i} className="pb-2.5 border-b border-border-soft last:border-b-0 last:pb-0 print:break-inside-avoid">
                          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                            <p className="text-[14.5px] font-semibold text-label leading-[1.3]">{i + 1}. {art.title}</p>
                            {art.topics?.map((topic: string, ti: number) => (
                              <span
                                key={ti}
                                className={clsx(
                                  "font-mono text-[9.5px] font-bold uppercase tracking-wide border rounded-[3px] px-1.5 py-0.5 whitespace-nowrap shrink-0",
                                  tagColorClass(topic)
                                )}
                              >
                                {topic}
                              </span>
                            ))}
                          </div>
                          {/* Nguồn nằm liền sau nội dung tin (cùng đoạn), chỉ xuống dòng khi hết chỗ */}
                          <p className="mt-0.5 text-[14.5px] leading-[1.4] text-body">
                            {/* Mọi tin đều có tóm tắt (bản chấm 17/10: chỉ còn tiêu đề thì Phần 3 mất giá trị). */}
                            <RichText text={art.summary} />{" "}
                            <a
                              href={art.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="font-mono text-[11.5px] text-primary hover:underline"
                            >
                              {t("body.source")} {art.source} <ExternalLink size={10} className="inline -mt-0.5" aria-hidden="true" />
                            </a>
                          </p>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-[14.5px] text-muted-light italic">{emptyText}</p>
                  )}
                </div>
              );
            })}
          </section>
        )}

        {/* REVIEW DỰ BÁO VÀ KHUYẾN NGHỊ — mục riêng sau Phần 3 (Chi tiết tin tức), trước Nguồn tham khảo (yêu cầu 21/10/2026),
            gồm 2 phần: (1) Dự báo trước: đúng hay sai (bảng chấm kịch bản ngắn hạn) và (2) Jenny cập nhật đề xuất trước đây
            (gợi ý kinh doanh của báo cáo trước vừa tới tình huống kích hoạt — trước nằm đầu khối Gợi ý kinh doanh, Phần 2). */}
        {(hasForecastReview || report.content["3"]?.vs_previous || bizReminders.length > 0) && (
          <section id="section-forecast-review" className="py-5">
            <PartHeading title={t("body.reviewTitle")} icon={ClipboardCheck} />

            {/* Màn rộng: 2 phần cạnh nhau; màn hẹp và khi in xếp dọc. */}
            <div className={clsx(
              (hasForecastReview || report.content["3"]?.vs_previous) && bizReminders.length > 0 && "xl:grid xl:grid-cols-2 xl:gap-x-6 xl:items-start",
            )}>
            {(hasForecastReview || report.content["3"]?.vs_previous) && (
              <div className="mb-6 last:mb-0 xl:mb-0">
                <SubHeading>{t("body.reviewForecast")}</SubHeading>
                {/* Dự báo trước đúng/sai — bảng tính bằng Python (content["2"].forecast_review) + đoạn vs_previous
                    làm phần "nguyên nhân & điều chỉnh". Báo cáo cũ chưa có bảng → chỉ hiện vs_previous như trước. */}
                {hasForecastReview ? (
                  <ForecastReviewCard review={report.content["2"].forecast_review} enumLabel={enumLabel} showTitle={false}>
                    {report.content["3"]?.vs_previous && <RichText text={report.content["3"].vs_previous} />}
                  </ForecastReviewCard>
                ) : (
                  <div className="rounded-lg border border-border bg-surface px-3.5 py-3 [print-color-adjust:exact] [-webkit-print-color-adjust:exact]">
                    <h3 className="text-[11px] sm:text-[12px] font-extrabold uppercase tracking-[0.15em] text-label mb-1">{t("body.vsPrevious")}</h3>
                    <p className="text-[14px] sm:text-[14.5px] leading-[1.55] text-body">
                      <RichText text={report.content["3"].vs_previous} />
                    </p>
                  </div>
                )}
              </div>
            )}

            {bizReminders.length > 0 && (
              <div>
                <SubHeading>{t("biz.remindersTitle")}</SubHeading>
                {/* Jenny nhắc lại — CHỈ gợi ý ở báo cáo trước (trong 10 ngày) mà tình huống
                    kích hoạt vừa xảy ra hôm nay (services/biz_memory.py). Gợi ý chưa kích hoạt
                    (`tracking`) vẫn được backend lưu/theo dõi nhưng không hiện ra báo cáo. */}
                <div className="rounded-lg border-2 border-warn/50 bg-warn-tint overflow-hidden [print-color-adjust:exact] [-webkit-print-color-adjust:exact]">
                  <div className="flex items-center gap-2.5 px-3.5 py-2.5">
                    <img src="/jenny.jpg" alt="" className="w-8 h-8 rounded-full object-cover shrink-0" />
                    {/* Tiêu đề đã nằm ở SubHeading phía trên → dòng này tóm tắt số đề xuất theo kết quả. */}
                    <div className="flex-1 min-w-0 flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5 text-[14px] sm:text-[14.5px]">
                      <span className="font-bold text-label">{t("biz.remindersSummary", { n: bizReminders.length })}</span>
                      {remindersHappened > 0 && <span className="font-semibold text-up">✓ {t("biz.remindersHappened", { n: remindersHappened })}</span>}
                      {remindersContradicted > 0 && <span className="font-semibold text-down">✗ {t("biz.remindersContradicted", { n: remindersContradicted })}</span>}
                    </div>
                    <button
                      type="button"
                      onClick={() => setShowReminders((v) => !v)}
                      aria-expanded={showReminders}
                      aria-label={showReminders ? t("biz.hideReminders") : t("biz.showReminders")}
                      title={showReminders ? t("biz.hide") : t("biz.viewDetails")}
                      className={clsx(
                        "shrink-0 inline-flex items-center justify-center w-6 h-6 rounded-full border transition-colors print:hidden",
                        showReminders
                          ? "bg-primary text-white border-primary"
                          : "text-primary border-primary/40 bg-background hover:bg-tint"
                      )}
                    >
                      <Info size={13} strokeWidth={2.5} aria-hidden="true" />
                    </button>
                  </div>
                  <ul className={clsx("list-none divide-y divide-warn/20 border-t border-warn/30", showReminders ? "block" : "hidden print:block")}>
                    {bizReminders.map((r, i) => (
                      <li key={i} className="px-3.5 py-3 space-y-1.5">
                        <div className="flex items-start gap-2">
                          <p className="flex-1 min-w-0 text-[14.5px] leading-[1.5] text-foreground">
                            {t("biz.suggestedOn")} <b>{formatFullDate(r.suggested_date)}</b> {t("biz.suggested")} <b><RichText text={r.action} /></b>
                          </p>
                          {onDismissBizSuggestion && typeof r.id === "number" && (
                            <DismissButton onClick={() => onDismissBizSuggestion(r.id as number)} className="shrink-0" />
                          )}
                        </div>
                        <p className="text-[14.5px] leading-[1.5] text-body">
                          <span className="font-semibold">{t("biz.trigger")}</span> <RichText text={r.trigger} />
                        </p>
                        {/* Nguồn nằm liền cuối câu kết quả (cùng dòng), chỉ xuống dòng khi hết chỗ. */}
                        <p className={clsx("text-[14.5px] leading-[1.5] font-semibold", r.outcome === "contradicted" ? "text-down" : "text-up")}>
                          {t(r.outcome === "contradicted" ? "biz.contradicted" : "biz.happened")}
                          {r.evidence ? <>: <span className="font-normal text-foreground"><RichText text={r.evidence} /></span></> : "."}
                          {r.source_name && <InlineSource name={r.source_name} url={r.source_url} className="font-normal" />}
                        </p>
                        <p className="text-[14.5px] italic text-warn font-semibold">
                          {r.outcome === "contradicted"
                            ? t("biz.contradictedFollow")
                            : t("biz.askDone")}
                        </p>
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            )}
            </div>
          </section>
        )}

        {/* NGUỒN THAM KHẢO — đứng độc lập cuối báo cáo, không thuộc Phần nào */}
        {report.content["9"] && (
          <section id="section-sources" className="py-5">
            <PartHeading title={report.content["9"].title || t("body.sourcesTitle")} icon={Link2} />
            {report.content["9"].items?.length > 0 ? (
              (() => {
                // Sắp theo ĐÚNG thứ tự Phần 3 (Quốc tế → Việt Nam, đánh số như trên); link
                // trùng URL thì khớp theo Phần 3, nguồn không nằm ở Phần 3 xếp cuối.
                const all: any[] = report.content["9"].items;
                const used = new Set<string>();
                const groups = [
                  { label: t("body.intl"), list: report.content["6"]?.international ?? [] },
                  { label: t("body.vn"), list: report.content["6"]?.vietnam ?? [] },
                ].map(({ label, list }) => ({
                  label,
                  entries: list
                    .map((art: any) => all.find((it: any) => it.url === art.url) ?? { source: art.source, title: art.title, url: art.url })
                    .filter((it: any) => it?.url && !used.has(it.url) && (used.add(it.url), true)),
                }));
                const others = all.filter((it: any) => !used.has(it.url));
                // Không hiện tiêu đề nhóm: 1 danh sách liền, đánh số liên tục theo thứ tự Phần 3.
                const entries = [...groups.flatMap((g) => g.entries), ...others];
                return (
                  <ul className="space-y-1.5 xl:columns-2 xl:gap-x-10">
                    {entries.map((it: any, i: number) => (
                      <li key={it.url ?? `kb-${it.doc_id ?? i}`} className="font-mono text-[12px] leading-[1.5] text-muted-light break-inside-avoid">
                        {i + 1}. [{it.source}]{" "}
                        {it.url ? (
                          <a
                            href={it.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-primary hover:underline"
                          >
                            {it.title}
                          </a>
                        ) : (
                          // Tài liệu tri thức admin tải lên (kind="knowledge") — không có link công khai.
                          <span className="text-body" title={t("body.knowledgeSource")}>{it.title}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                );
              })()
            ) : (
              <p className="font-mono text-[12px] text-muted-light">{t("body.noSources")}</p>
            )}
          </section>
        )}

        <footer className="pt-6 text-center border-t border-border">
          <p className="font-mono text-[11px] text-muted-light italic">{t("body.footer")}</p>
        </footer>

      </div>
    </div>
  );
}
