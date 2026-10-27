"use client";

import { Fragment, useEffect, useId, useRef, useState } from "react";
import { flushSync } from "react-dom";
import clsx from "clsx";
import {
  Clock, CalendarRange, TrendingUp, TrendingDown, Minus, Target, AlertTriangle,
  Sparkles, LineChart, BarChart3, Newspaper, Link2,
  Crosshair, ChevronDown, ChevronUp, Info, ShieldCheck, ExternalLink, Trash2,
  Leaf, Calendar, RefreshCw, ArrowLeftRight, ArrowRight, CalendarClock, ClipboardCheck, ArrowDownUp
} from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { format as formatDateFns } from "date-fns";
import type { CbamEstimate, ForecastReview, HedgeAction, OpenPosition, Report } from "@/lib/types";
import PriceIndexPanel from "@/components/PriceIndexPanel";
import { CbamEstimateCard } from "@/components/CbamEstimateCard";
import { ForecastReviewCard } from "@/components/ForecastReviewCard";
import { dateFnsLocale } from "@/i18n/dateLocale";
import { usePrintBreakTuning } from "@/lib/usePrintBreakTuning";

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

function formatFullDate(dateStr: string) {
  const parts = dateStr.split("-");
  return parts.length === 3 ? `${parts[2]}/${parts[1]}/${parts[0]}` : dateStr;
}

const DIRECTION_META: Record<string, { icon: typeof TrendingUp; className: string; accent: string }> = {
  // Chiều giá: tăng xanh lá, giảm đỏ, đi ngang xanh dương (nhãn đặc, chữ trắng; accent = viền trên thẻ kịch bản).
  // "Xác suất" KHÔNG dùng màu — chỉ là chữ thường (xem dòng "Nhận định" bảng Kịch bản).
  "tăng": { icon: TrendingUp, className: "bg-emerald-600 text-white border-emerald-600", accent: "border-t-emerald-600" },
  "giảm": { icon: TrendingDown, className: "bg-red-600 text-white border-red-600", accent: "border-t-red-600" },
  "đi ngang": { icon: Minus, className: "bg-blue-600 text-white border-blue-600", accent: "border-t-blue-600" },
};

// Mũi tên + màu chiều giá của từng kịch bản trong thẻ KHUYẾN NGHỊ VỊ THẾ (Phần 2) — chỉ là
// cách hiển thị khác của cùng "direction" (tăng/giảm/đi ngang) đã có sẵn trong
// trading_scenarios, KHÔNG phải trường dữ liệu mới/suy diễn thêm.
const TREND_META: Record<string, { arrow: string; className: string }> = {
  "tăng": { arrow: "▲", className: "text-up" },
  "giảm": { arrow: "▼", className: "text-down" },
  "đi ngang": { arrow: "↔", className: "text-blue-700" },
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

// nums: tô đậm số liệu trong văn xuôi (xem emphasizeNumbers) — dùng cho đoạn văn dài (tóm tắt, phân tích, tin tức...).
function RichText({ text, nums }: { text: string; nums?: boolean }) {
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
          <span key={i}>{nums ? emphasizeNumbers(part.replace(/\*\*/g, "")) : part.replace(/\*\*/g, "")}</span>
        )
      )}
    </>
  );
}

// Số trong văn xuôi: số có phần thập phân, %, dấu +/− hoặc đi kèm đơn vị (EUR, USD, tCO₂, MWh, triệu, tấn, phiên…) được
// tô đậm để mắt bắt ngay; số có dấu tô màu theo chiều (+ xanh, − đỏ). Số nguyên trần (năm, ngày "21/10/2026", mã văn
// bản "COM(2026) 616", số thứ tự) giữ nguyên. Chỉ bọc thẻ, không đổi chữ — bôi đen hỏi Jenny vẫn lấy đúng nguyên văn.
const NUM_TOKEN_RE = /(?<![\p{L}\d.,/])([+\-−]?\d+(?:[.,]\d+)*(?:\s?[–-]\s?\d+(?:[.,]\d+)*)?\s?%?)(?![\p{L}\d])/gu;
const NUM_UNIT_AFTER_RE = /^\s?(?:EUR|USD|tCO|CO₂|MWh|MMBtu|bbl|triệu|tỷ|nghìn|tấn|t\b|phiên|ngày|tuần|tháng|ATR|lần|hợp đồng|contracts?|sessions?|million|billion|tonnes?)/i;

function emphasizeNumbers(text: string): React.ReactNode {
  const out: React.ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(NUM_TOKEN_RE)) {
    const tok = m[1];
    const start = m.index ?? 0;
    const signed = /^[+\-−]/.test(tok);
    const strong = signed || /\d[.,]\d/.test(tok) || tok.endsWith("%") || NUM_UNIT_AFTER_RE.test(text.slice(start + tok.length));
    if (!strong) continue;
    if (start > last) out.push(text.slice(last, start));
    const range = /\d\s?[–-]\s?\d/.test(tok);
    const tone = signed && !range ? (tok.startsWith("+") ? "text-up" : "text-down") : "text-label";
    out.push(<strong key={start} className={clsx("font-bold tabular-nums", tone)}>{tok}</strong>);
    last = start + tok.length;
  }
  if (!out.length) return text;
  if (last < text.length) out.push(text.slice(last));
  return out;
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
    // Tới 4 hàng nhãn mỗi phía — vùng giá dày (mức cắt lỗ trung hạn sát vùng Entry ngắn hạn) vẫn đủ chỗ cho mọi mức.
    const slots: [LadderSide, number][] = [[own, 0], [own, 1], [other, 0], [other, 1], [own, 2], [other, 2], [own, 3], [other, 3]];
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
      "inline-block rounded-full border px-2 py-[1px] text-[12px] font-semibold leading-[1.5] whitespace-nowrap [print-color-adjust:exact] [-webkit-print-color-adjust:exact]",
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

// Ô số liệu của 1 chân lệnh (thẻ kịch bản — LegBlock).
function legCells(leg: Leg, { t, locale, last, minRr, rrScale }: LegCtx) {
  const fp = (n: number) => fmtPrice(n, locale);
  const dist = leg.entry !== undefined && last ? ((leg.entry - last) / last) * 100 : undefined;
  const weak = leg.rr !== undefined && leg.rr < minRr;
  return {
    entry: (
      <>
        <div className="font-bold tabular-nums text-label whitespace-nowrap">{legEntryText(leg, t, locale)}</div>
        {dist !== undefined && (
          <div className="text-[12px] text-muted-light tabular-nums">{t("signal.vsPrice", { pct: `${dist > 0 ? "+" : dist < 0 ? "−" : ""}${fnum(Math.abs(dist), 1, locale)}` })}</div>
        )}
      </>
    ),
    target: leg.target !== undefined
      ? <span className="font-bold tabular-nums text-up">{fp(leg.target)}</span>
      : leg.targetUnquantified
        ? <span className="text-[12px] italic text-muted-light">{t("signal.unquantified")}</span>
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

// Vùng Entry của 1 chân lệnh dạng chữ: lệnh phá vỡ "ĐC < 82,35" (ĐC = theo giá đóng cửa), còn lại "84,60–85,20".
function legEntryText(leg: Leg, t: ReportT, locale: string): string {
  if (!leg.zone.length) return "—";
  return leg.kind === "breakout"
    ? `${leg.onClose ? `${t("signal.onClose")} ` : ""}${leg.side === "buy" ? ">" : "<"} ${fmtPrice(leg.zone[0], locale)}`
    : fmtRange(leg.zone, locale);
}

const capFirst = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

// Khung thời gian hiển thị: dài hạn lấy cụm "6–12 tháng" ở đầu condition nếu có ("6–12 tháng, tới hết Q3/2027"),
// còn lại nhãn mặc định của khung.
function horizonPeriod(plan: HorizonPlan, t: ReportT): string {
  const hk = HORIZON_KEY[plan.horizon] ?? "short";
  return (hk === "long" && String(plan.sc.condition || "").match(/\d+\s*[–-]\s*\d+\s*(?:tháng|months?)/i)?.[0]) || t(`signal.period.${hk}`);
}

// Xác suất → số vạch (Thấp 1 · Trung bình 2 · Cao 3); giá trị lạ chỉ hiện chữ.
const PROB_LEVEL: Record<string, number> = { "Thấp": 1, "Trung bình": 2, "Cao": 3 };

// 1 chân lệnh: nhãn lệnh + R:R, rồi Entry / Mục tiêu / Cắt lỗ — cùng 1 bố cục ở mọi bề rộng và khi in.
function LegBlock({ leg, ctx }: { leg: Leg; ctx: LegCtx }) {
  const n = legCells(leg, ctx);
  const { t } = ctx;
  return (
    <div className="rounded-lg border border-border bg-background px-3 py-2.5 print:break-inside-avoid">
      <div className="flex items-start justify-between gap-2">
        <OrderPill leg={leg} />
        <div className="flex flex-col items-end text-[13px]">{n.rr}</div>
      </div>
      <div className="mt-2 grid grid-cols-[1.4fr_1fr_1fr] gap-2 text-[13.5px]">
        <div className="min-w-0"><div className="text-[11.5px] text-muted-light">{t("signal.col.entry")}</div>{n.entry}</div>
        <div className="min-w-0"><div className="text-[11.5px] text-muted-light">{t("signal.col.target")}</div>{n.target}</div>
        <div className="min-w-0"><div className="text-[11.5px] text-muted-light">{t("signal.col.stop")}</div>{n.stop}</div>
      </div>
    </div>
  );
}

// KỊCH BẢN HÀNH ĐỘNG — mỗi khung thời gian 1 thẻ: chiều + xác suất → vùng giá → lệnh (Entry/Mục tiêu/Cắt lỗ/R:R tách từ
// trading_strategy) → điều kiện kích hoạt, rủi ro → nguyên văn chiến lược bàn giao dịch. Gộp bảng lệnh của thẻ vị thế và
// bảng "Kịch bản hành động" cũ (2 bảng trình bày cùng 3 kịch bản) — mọi trường của kịch bản vẫn hiện đủ. Báo cáo hẹp
// (< 1024px): tab theo khung thời gian; in PDF: đủ 3 thẻ xếp dọc.
function HorizonCards({ plans, last, minRr }: { plans: HorizonPlan[]; last?: number; minRr: number }) {
  const t = useTranslations("reportDoc");
  const locale = useLocale();
  const enumLabel = useEnumLabel();
  const [active, setActive] = useState<string | undefined>(plans[0]?.horizon);
  const current = plans.some(p => p.horizon === active) ? active : plans[0]?.horizon;
  // Thanh R:R mọi chân dùng chung 1 thang: chân có (rủi ro + lợi nhuận) lớn nhất = toàn bộ bề rộng.
  const rrScale = Math.max(0, ...plans.flatMap(p => p.legs).map(l => (l.risk && l.risk > 0 ? l.risk + Math.max(l.reward ?? 0, 0) : 0)));
  const ctx: LegCtx = { t, locale, last, minRr, rrScale };
  // print:break-after-avoid — nhãn nhỏ (Điều kiện, Chiến lược…) không bị bỏ lại 1 mình cuối trang PDF.
  const label = "text-[11.5px] font-bold uppercase tracking-[0.06em] print:break-after-avoid";
  return (
    <div>
      {plans.length > 1 && (
        <div
          role="tablist"
          aria-label={t("scenario.heading")}
          className="@5xl/report:hidden print:hidden mb-3 grid gap-1 rounded-lg border border-border bg-surface p-1"
          style={{ gridTemplateColumns: `repeat(${plans.length}, minmax(0, 1fr))` }}
        >
          {plans.map(p => {
            const on = p.horizon === current;
            const trend = TREND_META[p.dir];
            return (
              <button
                key={p.horizon}
                type="button"
                role="tab"
                aria-selected={on}
                onClick={() => setActive(p.horizon)}
                className={clsx(
                  "min-h-[46px] rounded-md px-1.5 py-1 text-[13.5px] font-bold leading-tight transition-colors",
                  on ? "bg-background text-primary-dark shadow-sm" : "text-muted-light hover:text-label",
                )}
              >
                {capFirst(enumLabel(p.horizon))}
                <span className={clsx("block text-[12px] font-semibold", trend?.className)}>{trend?.arrow} {capFirst(enumLabel(p.dir))}</span>
              </button>
            );
          })}
        </div>
      )}
      <div className="grid gap-3 @5xl/report:grid-cols-3">
        {plans.map(p => {
          const sc = p.sc;
          const dirMeta = DIRECTION_META[sc.direction ?? ""];
          const DirIcon = dirMeta?.icon;
          const level = PROB_LEVEL[sc.probability ?? ""];
          const alt = sc.probability === "Thấp" && p.dir !== "đi ngang";
          const strategy = parseStrategy(sc.trading_strategy);
          const rawStrategy = sc.trading_strategy || sc.action_plan || "";
          return (
            <article
              key={p.horizon}
              className={clsx(
                "@container min-w-0 rounded-xl border border-t-[3px] border-border bg-background p-3.5 sm:p-4",
                dirMeta?.accent ?? "border-t-border-soft",
                p.horizon !== current && "hidden @5xl/report:block print:block!",
              )}
            >
              {/* Thẻ rộng (tab trên tablet, bản in A4): 2 cột — mức giá & lệnh | điều kiện, rủi ro, nguyên văn chiến
                  lược; thẻ hẹp (3 thẻ cạnh nhau, điện thoại): 1 cột. */}
              <div className="grid gap-x-5 gap-y-3 @xl:grid-cols-2">
                <div className="min-w-0 space-y-3">
                  <div>
                    <header className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1.5">
                      <div className="min-w-0">
                        <h4 className="text-[16.5px] font-bold leading-tight text-label">{capFirst(enumLabel(p.horizon))}</h4>
                        <div className="mt-0.5 text-[12.5px] text-muted-light">{horizonPeriod(p, t)}</div>
                      </div>
                      {sc.direction && (
                        <span className={clsx(
                          "inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[12px] font-bold uppercase tracking-wide",
                          dirMeta?.className ?? "border-border text-label",
                        )}>
                          {DirIcon && <DirIcon size={13} className="shrink-0" aria-hidden="true" />} {enumLabel(sc.direction)}
                        </span>
                      )}
                    </header>

                    {(sc.probability || alt) && (
                      <div className="mt-2 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[13px] text-body">
                        {sc.probability && (
                          <span className="inline-flex items-center gap-1.5">
                            {level !== undefined && (
                              <span className="flex gap-0.5" aria-hidden="true">
                                {[1, 2, 3].map(i => <span key={i} className={clsx("h-[6px] w-3 rounded-sm", i <= level ? "bg-label" : "bg-border-soft")} />)}
                              </span>
                            )}
                            {t("scenario.probability")} <b className="font-bold">{enumLabel(sc.probability)}</b>
                          </span>
                        )}
                        {alt && (
                          <span className="rounded border border-border px-1.5 text-[11.5px] font-semibold uppercase tracking-wide text-muted-light">{t("scenario.alternative")}</span>
                        )}
                      </div>
                    )}
                  </div>

                  <div className="rounded-lg bg-primary/[0.07] px-3 py-2">
                    <div className={clsx(label, "flex items-center gap-1.5 text-primary-dark")}>
                      <Target size={13} className="shrink-0" aria-hidden="true" /> {t("scenario.priceZone")}
                    </div>
                    <div className="mt-0.5 text-[14.5px] font-semibold leading-snug text-primary-dark">
                      {sc.price_zone ? <RichText text={sc.price_zone} /> : <span className="text-muted-light">—</span>}
                    </div>
                  </div>

                  <div className="space-y-2">
                    {p.legs.length > 0
                      ? p.legs.map((leg, i) => <LegBlock key={i} leg={leg} ctx={ctx} />)
                      : (
                        // Không tách được mức giá nào (vd "Đứng ngoài…", dữ liệu cũ chỉ có action_plan) → nguyên văn.
                        <p className="text-[14px] leading-[1.6] text-body">
                          {rawStrategy ? <RichText text={rawStrategy} nums /> : <span className="text-muted-light">—</span>}
                        </p>
                      )}
                  </div>
                </div>

                <div className="min-w-0 space-y-3">
                  {(sc.condition || sc.key_risk) && (
                    <dl className="space-y-2.5 text-[14px] leading-[1.55]">
                      {sc.condition && (
                        <div>
                          <dt className={clsx(label, "text-muted-light")}>{t("scenario.condition")}</dt>
                          <dd className="mt-0.5 text-body"><RichText text={sc.condition} nums /></dd>
                        </div>
                      )}
                      {sc.key_risk && (
                        <div>
                          <dt className={clsx(label, "flex items-center gap-1 text-down")}>
                            <AlertTriangle size={12} className="shrink-0" aria-hidden="true" /> {t("scenario.keyRisk")}
                          </dt>
                          <dd className="mt-0.5 text-down"><RichText text={sc.key_risk} /></dd>
                        </div>
                      )}
                    </dl>
                  )}

                  {/* Nguyên văn chiến lược (các mức giá bên cạnh được tách từ đây — giữ để đọc đủ ghi chú kèm mức giá). */}
                  {p.legs.length > 0 && rawStrategy && (
                    <div className="border-t border-border pt-2.5">
                      <div className={clsx(label, "flex items-center gap-1.5 text-primary-dark")}>
                        <Crosshair size={12} className="shrink-0" aria-hidden="true" /> {t("scenario.strategy")}
                      </div>
                      {strategy ? (
                        <dl className="mt-1 space-y-1 text-[13.5px] leading-[1.55] text-body">
                          {strategy.map((it, i) => (
                            <div key={i}>
                              <dt className="inline font-semibold text-label">{it.label}: </dt>
                              <dd className="inline"><RichText text={it.body} nums /></dd>
                            </div>
                          ))}
                        </dl>
                      ) : (
                        <p className="mt-1 text-[13.5px] leading-[1.55] text-body"><RichText text={rawStrategy} nums /></p>
                      )}
                    </div>
                  )}

                  {/* Báo cáo cũ: hàm ý cho sổ phòng hộ SIM ghi theo từng kịch bản (báo cáo mới dùng 1 hedge_action chung). */}
                  {sc.hedge_note && (
                    <div className="rounded-lg bg-tint px-3 py-2">
                      <div className={clsx(label, "flex items-center gap-1.5 text-label")}>
                        <ShieldCheck size={12} className="shrink-0" aria-hidden="true" /> {t("scenario.hedge")}
                      </div>
                      <p className="mt-0.5 text-[13.5px] leading-[1.55] text-body"><RichText text={sc.hedge_note} /></p>
                    </div>
                  )}
                </div>
              </div>
            </article>
          );
        })}
      </div>
    </div>
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
  const { fixed, open, tmax, core, tactical } = hedgeShares(est);
  const { contract, next: nextContract } = hedgeContract(est);
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
    <div className="px-4 sm:px-5 py-4 border-t border-border print:break-inside-avoid">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 mb-2.5">
        <h3 className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[15px] font-bold text-label">
          <ShieldCheck size={16} strokeWidth={2.5} className="text-primary-dark shrink-0" aria-hidden="true" />
          {t("hedge.title", { quarter: est.quarter })}
          <span className="rounded border border-primary/30 bg-tint px-1.5 py-px text-[11.5px] font-bold uppercase tracking-wider text-primary-dark">
            {t(`signal.hedgeAction.${est.signal}`)}
          </span>
        </h3>
        <span className="text-[12.5px] text-muted-light tabular-nums">
          {est.ci_low !== null && est.ci_high !== null
            ? t("hedge.estimateCi", { est: n(est.estimate), level: est.ci_level, low: n(est.ci_low), high: n(est.ci_high) })
            : t("hedge.estimate", { est: n(est.estimate) })}
        </span>
      </div>
      <div className="flex h-[30px] rounded-md overflow-hidden text-[12.5px] font-semibold [print-color-adjust:exact] [-webkit-print-color-adjust:exact]">
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
      <ul className="sm:hidden mt-2 space-y-1 text-[12.5px] text-body [print-color-adjust:exact] [-webkit-print-color-adjust:exact]">
        {segments.map(s => (
          <li key={s.key} className="flex items-center gap-1.5">
            <span className={clsx("inline-block w-3 h-3 rounded-[3px] shrink-0", s.cls)} aria-hidden="true" />
            {s.label}
          </li>
        ))}
      </ul>
      <ul className="mt-3 grid @3xl/report:grid-cols-3 gap-x-5 gap-y-2 text-[13px] leading-[1.5] text-body">
        {notes.map((note, i) => (
          <li key={i} className="flex items-start gap-1.5">
            <note.icon size={14} className="shrink-0 mt-[3px] text-muted-light" aria-hidden="true" />
            <span className="text-left tabular-nums">{note.text}</span>
          </li>
        ))}
      </ul>
      {/* Câu LLM của phần chiến thuật (chỉ khi nhãn increase/reduce — "hold" do Python viết, trùng ý dòng trên). */}
      {est.signal !== "hold" && hedgeAction?.text && (
        <p className="text-left mt-2 text-[13px] leading-[1.5] text-muted-light">{stripMarkdown(hedgeAction.text)}</p>
      )}
    </div>
  );
}

// Báo cáo cũ chưa có cbam_estimate: hiện nguyên văn ô phòng hộ (hedge_action hoặc hedge_note của kịch bản ngắn hạn).
function LegacyHedgeNote({ hedgeAction, note }: { hedgeAction?: HedgeAction | null; note?: string }) {
  const t = useTranslations("reportDoc");
  return (
    <div className="px-4 sm:px-5 py-3.5 border-t border-border">
      <h3 className="flex flex-wrap items-center gap-2 text-[15px] font-bold text-label mb-1">
        <ShieldCheck size={16} strokeWidth={2.5} className="text-primary-dark shrink-0" aria-hidden="true" />
        {t("signal.hedge")}
        {hedgeAction && (
          <span className="rounded border border-primary/30 bg-tint px-1.5 py-px text-[11.5px] font-bold uppercase tracking-wider text-primary-dark">
            {t(`signal.hedgeAction.${hedgeAction.action}`)}
          </span>
        )}
      </h3>
      {hedgeAction?.core && <p className="text-left text-[14px] leading-[1.55] text-body font-semibold"><HighlightNumbers text={hedgeAction.core} /></p>}
      {(hedgeAction?.text || note) && <p className="text-left text-[14px] leading-[1.55] text-body"><HighlightNumbers text={hedgeAction?.text || note || ""} /></p>}
    </div>
  );
}

// ── Số liệu EUA dùng chung khối mở đầu + thẻ vị thế ──────────────────────────────────────────────────────────
// Mọi con số lấy thẳng từ chart_data (OHLC 30 phiên) / dòng EUA của Bảng giá nhanh, đúng công thức backend ghi ở
// từng hàm (biên 30 phiên = đáy/đỉnh OHLC, ATR = atrTrueRange, TB khối lượng 20 phiên liền trước) — không suy diễn thêm.
interface EuaStats {
  last?: number;
  lastDate?: string;
  dayPct?: number;
  weekPct?: number;
  range: [number, number] | null;
  pos: { pct: number; pos: RangePos } | null;
  atr?: number;
  vol: number;
  avgVol: number;
  volPct?: number;
  volN: number;
}

function computeEuaStats(chartData: OhlcRow[], euaRow?: PriceRow): EuaStats {
  const lastRow = chartData.length ? chartData[chartData.length - 1] : undefined;
  const last = typeof lastRow?.close === "number" ? lastRow.close : typeof euaRow?.close === "number" ? euaRow.close : undefined;
  const lows = chartData.map(c => c.low).filter((v): v is number => typeof v === "number");
  const highs = chartData.map(c => c.high).filter((v): v is number => typeof v === "number");
  const range: [number, number] | null = lows.length && highs.length ? [Math.min(...lows), Math.max(...highs)] : null;
  // Δ ngày/tuần: ưu tiên đúng số của Bảng giá nhanh (dòng EUA); thiếu thì tính từ chart_data cùng định nghĩa
  // (tuần = phiên gần nhất cách ≥ 7 ngày lịch).
  const prevClose = chartData.length > 1 ? chartData[chartData.length - 2].close : undefined;
  const dayPct = typeof euaRow?.day_change_pct === "number" ? euaRow.day_change_pct
    : last !== undefined && prevClose ? (last / prevClose - 1) * 100 : undefined;
  const weekRow = lastRow ? [...chartData].reverse().find(c =>
    c.date <= new Date(new Date(lastRow.date).getTime() - 7 * 86400000).toISOString().slice(0, 10)) : undefined;
  const weekPct = typeof euaRow?.week_change_pct === "number" ? euaRow.week_change_pct
    : last !== undefined && weekRow?.close ? (last / weekRow.close - 1) * 100 : undefined;
  const vol = lastRow?.volume ?? 0;
  // TB khối lượng 20 phiên LIỀN TRƯỚC, bỏ phiên volume <= 0 — cùng công thức với EuaDashboardWidget.
  const priorVols = chartData.slice(0, -1).map(c => c.volume).filter((v): v is number => typeof v === "number" && v > 0).slice(-VOL_AVG_SESSIONS);
  const avgVol = priorVols.length ? priorVols.reduce((a, b) => a + b, 0) / priorVols.length : 0;
  return {
    last, lastDate: lastRow?.date, dayPct, weekPct, range,
    pos: range && last !== undefined ? rangePosition(last, range[0], range[1]) : null,
    atr: atrTrueRange(chartData),
    vol, avgVol, volPct: avgVol && vol > 0 ? (vol / avgVol - 1) * 100 : undefined, volN: priorVols.length,
  };
}

// "+0.30% (+0.26)" → 0.30 (dữ liệu cũ thiếu day/week_change_pct dạng số).
function deltaPct(value: unknown, text: unknown): number | undefined {
  if (typeof value === "number") return value;
  const m = String(text ?? "").match(/([+\-−]?\d+(?:[.,]\d+)?)\s*%/);
  return m ? parseFloat(m[1].replace("−", "-").replace(",", ".")) : undefined;
}

// Hợp đồng Dec của năm quý CBAM ("Q4 2026" → "Dec-26") và tỷ trọng đã chốt / lõi / chiến thuật theo khối lượng quý
// (báo cáo trước 18/10/2026 không có các % theo quý → suy từ phần chưa chốt như services/cbam_estimate.py).
function hedgeContract(est: CbamEstimate): { contract: string; next: string } {
  const year = Number(est.quarter.split(" ").pop());
  const yy = (y: number) => String(y % 100).padStart(2, "0");
  return Number.isFinite(year) ? { contract: `Dec-${yy(year)}`, next: `Mar-${yy(year + 1)}` } : { contract: "Dec", next: "Mar" };
}
function hedgeShares(est: CbamEstimate) {
  const fixed = est.fixed_share_pct;
  const open = est.open_share_pct ?? 100 - fixed;
  const tmax = est.tactical_max_pct ?? 20;
  return {
    fixed, open, tmax,
    core: est.core_share_of_quarter_pct ?? (open * (100 - tmax)) / 100,
    tactical: est.tactical_share_of_quarter_pct ?? (open * tmax) / 100,
  };
}

// ── KHỐI MỞ ĐẦU (Tổng quan) ──────────────────────────────────────────────────────────────────────────────
// Báo cáo rộng: Tóm tắt điều hành | Nhận định tổng quan + giá EUA | cột Lệnh ngắn hạn · Phòng hộ CBAM · Dự báo trước —
// 3 ô cao bằng nhau. Số liệu chính (giá CBAM ước tính, khí, than, điện, dầu) chạy dọc trên thanh menu (PriceTicker).

// Khung 1 ô của khối mở đầu: tiêu đề nhỏ in hoa (+ phần phụ bên phải) rồi nội dung. Khi in, ô luôn giữ trọn
// (data-print-whole — không cắt ô sang 2 trang, kể cả khi cao hơn ngưỡng ngắt của usePrintBreakTuning); riêng Tóm tắt
// điều hành (`splittable`) được ngắt giữa các ý vì báo cáo cũ có thể dài gần 1 trang.
function HeroCard({ title, href, aside, compact, splittable, backdrop, className, children }: {
  title: React.ReactNode; aside?: React.ReactNode; compact?: boolean; splittable?: boolean; className?: string;
  // Neo mục chi tiết bên dưới (vd "#section-market") — bấm vào bất kỳ chỗ nào trong ô thì cuộn tới; ô nền trắng, rê
  // chuột thì đổi nền xanh (bg-tint) + con trỏ bàn tay. Tiêu đề vẫn là <a> để dùng được bằng bàn phím.
  href?: string;
  // Lớp nền trang trí (vd EuaBackdrop) — nằm sau nội dung (isolate + -z-10), bị cắt theo góc bo của ô.
  backdrop?: React.ReactNode;
  children: React.ReactNode;
}) {
  const onClick = href ? (e: React.MouseEvent<HTMLDivElement>) => {
    // Bấm vào link bên trong ô (tiêu đề, nguồn tin, "Xem khuyến nghị") → để link tự xử lý; đang bôi đen chữ (Hỏi AI /
    // Hỏi Claude) → không nhảy trang.
    if ((e.target as HTMLElement).closest("a, button")) return;
    if (window.getSelection()?.toString().trim()) return;
    const el = document.getElementById(href.slice(1));
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "start" });
    window.history.replaceState(null, "", href);
  } : undefined;
  return (
    <div data-print-whole={splittable ? undefined : ""} onClick={onClick} className={clsx(
      // Ô được ngắt trang: khi in chảy dạng khối — khung flex bị ngắt trang trong Chrome đè lên khối phía sau.
      "@container flex min-w-0 flex-col rounded-xl border border-border bg-background print:p-3.5!",
      splittable ? "print:block" : "print:break-inside-avoid",
      compact ? "px-4 py-3.5" : "p-4 sm:p-5",
      href && "cursor-pointer transition-colors hover:border-primary/20 hover:bg-tint",
      backdrop && "relative isolate overflow-hidden",
      className,
    )}>
      {backdrop}
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <h2 className="report-heading flex items-center gap-1.5 text-[13px] font-bold uppercase tracking-[0.08em] text-primary-dark">
          {href ? (
            <a href={href} className="group/hero-link inline-flex items-center gap-1.5 hover:text-primary hover:underline underline-offset-2">
              {title}
              <ArrowRight size={13} strokeWidth={2.5} aria-hidden="true" className="opacity-60 transition-transform group-hover/hero-link:translate-x-0.5 group-hover/hero-link:opacity-100 print:hidden" />
            </a>
          ) : title}
        </h2>
        {aside}
      </div>
      {children}
    </div>
  );
}

// Nền ô Nhận định tổng quan: đường giá đóng cửa EUA 30 phiên (chart_data) dạng vùng tô mờ — trang trí, không trục/nhãn
// (số liệu thật nằm ở Phần 1). Chiếm ~60% chiều cao dưới đáy ô, giãn theo mọi bề rộng (preserveAspectRatio="none", nét
// không co giãn); mờ dần lên trên để chữ phía trên vẫn đọc rõ.
function EuaBackdrop({ data }: { data: OhlcRow[] }) {
  const id = useId();
  const closes = data.map(d => d.close).filter((v): v is number => typeof v === "number");
  if (closes.length < 2) return null;
  const min = Math.min(...closes), max = Math.max(...closes);
  const span = max - min || 1;
  const W = 300, H = 100;
  const x = (i: number) => (i / (closes.length - 1)) * W;
  const y = (v: number) => 8 + (1 - (v - min) / span) * (H - 16);
  const line = closes.map((c, i) => `${i ? "L" : "M"}${x(i).toFixed(2)},${y(c).toFixed(2)}`).join(" ");
  const up = closes[closes.length - 1] >= closes[0];
  const color = up ? "var(--color-up)" : "var(--color-down)";
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true"
      className="pointer-events-none absolute inset-x-0 bottom-0 -z-10 h-[62%] w-full opacity-[0.22] [print-color-adjust:exact] [-webkit-print-color-adjust:exact]"
    >
      <defs>
        <linearGradient id={`${id}-fill`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.55" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
        <linearGradient id={`${id}-fade`} x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="white" stopOpacity="0.35" />
          <stop offset="100%" stopColor="white" stopOpacity="1" />
        </linearGradient>
        <mask id={`${id}-mask`}><rect width={W} height={H} fill={`url(#${id}-fade)`} /></mask>
      </defs>
      <g mask={`url(#${id}-mask)`}>
        <path d={`${line} L${W},${H} L0,${H} Z`} fill={`url(#${id}-fill)`} />
        <path d={line} fill="none" stroke={color} strokeWidth="2.5" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
      </g>
    </svg>
  );
}

function MiniStat({ label, value, lines }: { label: string; value: string; lines: (string | null | undefined | false)[] }) {
  return (
    <div className="min-w-0">
      <div className="text-[12px] font-semibold leading-tight text-muted-light">{label}</div>
      <div className="mt-0.5 text-[15.5px] font-bold leading-tight tabular-nums text-label whitespace-nowrap">{value}</div>
      {lines.filter(Boolean).map((l, i) => (
        <div key={i} className="mt-0.5 text-[12px] leading-snug tabular-nums text-body [text-wrap:balance]">{l}</div>
      ))}
    </div>
  );
}

// Tóm tắt điều hành (ô trái): mỗi ý = nhãn chủ đề (đủ chữ, không cắt) + nguyên văn, số liệu tô đậm. Prompt Mục 1 giữ cả
// mục 60–90 chữ (report_generator._prompt_section1) để vừa ô; báo cáo cũ dài hơn thì ô tự cao theo.
function HeroSummary({ bullets, className }: { bullets: any[]; className?: string }) {
  const t = useTranslations("reportDoc");
  return (
    <HeroCard splittable title={t("body.execSummary")} className={className}>
      {/* Các ý chia đều chiều cao ô (ô giãn bằng ô cao nhất hàng): mỗi ý 1 hàng bằng nhau, chữ căn giữa theo chiều dọc —
          Tóm tắt ít ý không bị trống cả mảng dưới đáy. In: chảy dạng khối như cũ. */}
      <ul className="mt-3 flex flex-1 list-none flex-col divide-y divide-border print:block">
        {bullets.map((bullet: any, i: number) => {
          const b: string = typeof bullet === "string" ? bullet : bullet?.text || "";
          const isMatch = b.includes(":");
          const tag = stripMarkdown(isMatch ? b.split(":")[0] : "Note").trim();
          const text = (isMatch ? b.split(":").slice(1).join(":") : b).trim();
          const sourceName = typeof bullet === "object" ? bullet?.source_name : null;
          const sourceUrl = typeof bullet === "object" ? bullet?.source_url : null;
          return (
            <li key={i} className="flex flex-1 flex-col justify-center py-2.5 first:pt-0 last:pb-0 print:block print:break-inside-avoid">
              {/* Nhãn chủ đề đứng đầu dòng, cùng dòng với chữ (không chiếm 1 dòng riêng) — ô thấp hơn, bớt trống ở 2 ô cạnh. */}
              <p className="text-left text-[15px] leading-[1.55] text-foreground">
                <span className={clsx("mr-1.5 inline-block rounded-md border px-1.5 py-px align-[1px] text-[11.5px] font-bold leading-snug", tagColorClass(tag))}>{tag}</span>
                <RichText text={text} nums />
                {sourceName && <InlineSource name={sourceName} url={sourceUrl} />}
              </p>
            </li>
          );
        })}
      </ul>
    </HeroCard>
  );
}

const OUTLOOK_SENTIMENT: Record<string, EuaSentiment> = { "text-up": "positive", "text-down": "negative", "text-warn": "neutral" };
const SENTIMENT_CHIP: Record<EuaSentiment, string> = {
  positive: "bg-up/10 text-up border-up/30",
  neutral: "bg-warn-tint text-warn border-warn/35",
  negative: "bg-down/10 text-down border-down/30",
};

// Nhận định tổng quan (ô giữa): giá EUA (số lớn nhất trang) + Δ ngày/tuần (đúng chuỗi của Bảng giá nhanh), thước đo
// chiều kịch bản (chip góc trên) xếp gọn; câu "Tổng hợp" của Mục 3 (nguyên văn) trải ngang bên dưới; biên 30 phiên / ATR /
// khối lượng phiên ở đáy ô.
function HeroVerdict({ verdict, outlook, stats, euaRow, chartData, className }: {
  verdict: { sentiment: EuaSentiment; text: string } | null;
  outlook: { pos: number; key: string; tone: string } | null;
  stats: EuaStats; euaRow?: any; chartData: OhlcRow[]; className?: string;
}) {
  const t = useTranslations("reportDoc");
  const locale = useLocale();
  const fp = (n: number) => fmtPrice(n, locale);
  const signed = (n: number, d = 2) => `${n > 0 ? "+" : n < 0 ? "−" : ""}${fnum(Math.abs(n), d, locale)}`;
  const { last, range, pos, atr } = stats;
  const deltas = ([
    ["day", euaRow?.dday, stats.dayPct],
    ["week", euaRow?.dweek, stats.weekPct],
  ] as const).map(([key, text, pct]) => {
    const value = text && text !== "-" ? formatDelta(String(text), locale) : pct !== undefined ? `${signed(pct)}%` : null;
    const up = text && text !== "-" ? isPositiveDelta(String(text)) : (pct ?? 0) >= 0;
    return { key, value, up };
  }).filter(d => d.value);
  // Biên 30 phiên / ATR / khối lượng: 1 hàng hết bề ngang ở đáy ô (cả ô rộng lẫn ô hẹp).
  const statsBlock = (cls: string) => last !== undefined && (
    <div className={clsx("grid grid-cols-2 gap-x-4 gap-y-3 border-t border-primary/15 pt-3 @sm:grid-cols-3", cls)}>
      <MiniStat
        label={t("signal.range30")}
        value={range ? fmtRange(range, locale) : "—"}
        lines={[
          pos && t(`signal.rangePos.${pos.pos}`, { pct: fnum(pos.pct, 0, locale) }),
          // Mục tiêu đo biên độ khi phá/thủng biên = report_generator._eua_technical_levels_summary
          range && t("signal.measured", { down: fp(range[0] - (range[1] - range[0])), up: fp(range[1] + (range[1] - range[0])) }),
        ]}
      />
      <MiniStat
        label={t("signal.atr", { n: ATR_SESSIONS })}
        value={atr !== undefined ? fp(atr) : "—"}
        lines={[atr !== undefined && last ? t("signal.atrPct", { pct: fnum((atr / last) * 100, 1, locale) }) : null]}
      />
      <MiniStat
        label={t("hero.volume")}
        value={stats.vol > 0 ? stats.vol.toLocaleString(locale === "en" ? "en-US" : "vi-VN") : "—"}
        lines={[stats.volPct !== undefined ? t("signal.volVsAvg", { pct: signed(stats.volPct, 1), n: stats.volN }) : null]}
      />
    </div>
  );
  return (
    <HeroCard
      title={t("hero.verdictTitle")}
      href="#section-market"
      backdrop={last !== undefined && chartData.length > 1 ? <EuaBackdrop data={chartData} /> : undefined}
      // Góc trên chỉ dùng khi không có kịch bản: nhãn của câu "Tổng hợp". Có kịch bản thì nhãn chiều + thanh Giảm–Tăng
      // nằm ở hàng giá bên dưới.
      aside={!outlook && verdict && (
        <span className={clsx("rounded-full border px-2.5 py-0.5 text-[12px] font-bold uppercase tracking-wide", SENTIMENT_CHIP[verdict.sentiment])}>
          {t(`hero.sentiment.${verdict.sentiment}`)}
        </span>
      )}
      className={className}
    >
      {/* Hàng giá: cột trái = giá + đơn vị, Δ ngày/tuần ngay dưới giá; cột phải = nhãn chiều kịch bản (ngắn hạn + trung
          hạn, outlookOf — vd "Trung lập · nghiêng giảm") với thanh Giảm–Tăng ngay dưới nhãn (hẹp thì xuống dòng). Câu
          nhận định trải hết bề ngang bên dưới. */}
      {(last !== undefined || outlook) && (
        <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-3">
          {last !== undefined && (
            <div className="min-w-0">
              <div className="flex items-end gap-x-2">
                <span className="text-[38px] sm:text-[40px] font-bold leading-none tracking-[-0.02em] tabular-nums text-label">{fp(last)}</span>
                <span className="mb-1 text-[13px] font-bold text-muted-light">EUR/tCO₂</span>
              </div>
              <div className="mt-1 text-[12px] leading-snug text-muted-light">
                {euaRow?.name && <span>{euaRow.name} · </span>}
                {stats.lastDate && <span className="tabular-nums">{t("dash.closeOf", { date: formatFullDate(stats.lastDate) })}</span>}
              </div>
              {deltas.length > 0 && (
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  {deltas.map(d => (
                    <span key={d.key} className={clsx(
                      "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[12.5px] font-bold tabular-nums",
                      d.up ? "bg-up/10 text-up" : "bg-down/10 text-down",
                    )}>
                      {d.up ? "▲" : "▼"} {d.value}
                      <span className="font-semibold opacity-80">{t(`hero.${d.key}`)}</span>
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}
          {outlook && (
            <div className="min-w-[190px] flex-1">
              <span className={clsx("inline-block rounded-full border px-2.5 py-0.5 text-[12px] font-bold uppercase tracking-wide", SENTIMENT_CHIP[OUTLOOK_SENTIMENT[outlook.tone] ?? "neutral"])}>
                {t(`signal.outlookLabel.${outlook.key}`)}
              </span>
              <div className="relative mt-3 h-[6px] rounded-full bg-gradient-to-r from-down/55 via-border to-up/55 [print-color-adjust:exact] [-webkit-print-color-adjust:exact]">
                <span className="absolute top-[-5px] h-4 w-[3px] rounded-full bg-label" style={{ left: `${outlook.pos}%`, transform: "translateX(-50%)" }} />
              </div>
              <div className="mt-1.5 flex items-baseline justify-between gap-2 text-[11.5px] text-muted-light">
                <span>{t("signal.gaugeDown")}</span>
                <span>{t("signal.gaugeUp")}</span>
              </div>
            </div>
          )}
        </div>
      )}
      {verdict && (
        <p className="mt-3 text-left text-[16px] leading-[1.6] text-label">
          <RichText text={verdict.text} nums />
        </p>
      )}

      {/* mt-auto: hàng chỉ số luôn nằm ở đáy ô (ô giãn theo hàng lưới) — không để khoảng trống dưới đáy. */}
      {last !== undefined && <div className="mt-auto pt-4">{statsBlock("")}</div>}
    </HeroCard>
  );
}

// Lệnh ngắn hạn (cột phải, ô 1): đúng các chân lệnh của thẻ kịch bản ngắn hạn (cùng số với Khuyến nghị vị thế).
function HeroOrders({ shortPlan, className }: { shortPlan: HorizonPlan; className?: string }) {
  const t = useTranslations("reportDoc");
  const locale = useLocale();
  const enumLabel = useEnumLabel();
  const fp = (n: number) => fmtPrice(n, locale);
  const trend = TREND_META[shortPlan.dir];
  return (
    <HeroCard
      compact
      title={t("hero.todayOrders")}
      href="#section-analysis"
      aside={trend && <span className={clsx("text-[13px] font-bold", trend.className)}>{trend.arrow} {capFirst(enumLabel(shortPlan.dir))}</span>}
      className={className}
    >
      <ul className="mt-2.5 space-y-2.5">
        {shortPlan.legs.map((leg, i) => (
          <li key={i} className="grid grid-cols-[auto_minmax(0,1fr)] items-start gap-x-2.5 tabular-nums">
            <OrderPill leg={leg} />
            <div className="min-w-0 leading-snug">
              <b className="text-[15px] font-bold text-label">{legEntryText(leg, t, locale)}</b>
              <div className="mt-0.5 flex flex-wrap gap-x-2.5 gap-y-0.5 text-[13px] text-body">
                {leg.target !== undefined && <span>{t("signal.col.target")} <b className="text-up">{fp(leg.target)}</b></span>}
                {leg.stop !== undefined && <span>{t("signal.col.stop")} <b className="text-down">{fp(leg.stop)}</b></span>}
                {leg.rr !== undefined && <span className="text-muted-light">R:R 1 : {fnum(leg.rr, 1, locale)}</span>}
              </div>
            </div>
          </li>
        ))}
      </ul>
      <a href="#section-recommendation" className="mt-auto inline-flex items-center gap-1 pt-3 text-[12.5px] font-bold text-primary hover:underline print:hidden">
        {t("hero.seeRecommendation")} <ArrowRight size={14} aria-hidden="true" />
      </a>
    </HeroCard>
  );
}

// Phòng hộ CBAM (cột phải, ô 2): cùng nhãn với thẻ CBAM (cbam_estimate.signal) và tỷ trọng lõi/chiến thuật/đã chốt.
function HeroHedge({ cbamEstimate, hedgeAction, className }: {
  cbamEstimate?: CbamEstimate | null; hedgeAction?: HedgeAction | null; className?: string;
}) {
  const t = useTranslations("reportDoc");
  const locale = useLocale();
  const n1 = (x: number) => fnum(x, 1, locale);
  const shares = cbamEstimate ? hedgeShares(cbamEstimate) : null;
  const contract = cbamEstimate ? hedgeContract(cbamEstimate).contract : "";
  const label = cbamEstimate ? t(`signal.hedgeAction.${cbamEstimate.signal}`) : hedgeAction ? t(`signal.hedgeAction.${hedgeAction.action}`) : null;
  return (
    <HeroCard
      compact
      title={<><ShieldCheck size={14} strokeWidth={2.5} aria-hidden="true" /> {cbamEstimate ? t("hedge.title", { quarter: cbamEstimate.quarter }) : t("signal.hedge")}</>}
      // Có ước tính → thẻ "Ước tính giá CBAM" ở Phần 1; báo cáo cũ không có thẻ đó → khuyến nghị vị thế (ô phòng hộ).
      href={cbamEstimate ? "#section-cbam-estimate" : "#section-analysis"}
      className={className}
    >
      {label && (
        <span className="mt-2 self-start rounded border border-primary/30 bg-tint px-1.5 py-px text-[11px] font-bold uppercase tracking-wide text-primary-dark">{label}</span>
      )}
      {cbamEstimate && shares && (
        <ul className="mt-2 space-y-1 text-[13.5px] leading-[1.45] text-body tabular-nums">
          {shares.core > 0.05 && <li>{t("hedge.segCore", { pct: n1(shares.core), contract })}</li>}
          {shares.tactical > 0.05 && <li>{t(`hedge.segTactical.${cbamEstimate.signal}`, { pct: n1(shares.tactical) })}</li>}
          {shares.fixed > 0.05 && <li className="text-muted-light">{t("hedge.segFixed", { pct: n1(shares.fixed) })}</li>}
        </ul>
      )}
    </HeroCard>
  );
}

// Mã dẫn dắt EUA trong thanh số liệu chạy (khí, than, điện Đức, dầu — cùng nhóm với biểu đồ chỉ số giá); thiếu mã nào
// thì lấp bằng thứ tự Bảng giá nhanh. Đủ 10 mã vẫn nằm ở Bảng giá nhanh (Phần 1).
const DRIVER_CODES = ["TTF", "API2", "DEBY1", "BRENT"];
function pickDrivers(rows: any[]): any[] {
  const picked: any[] = DRIVER_CODES.map(c => rows.find(r => r?.code === c)).filter(Boolean);
  for (const r of rows) {
    if (picked.length >= 4) break;
    if (r?.code !== "EUA" && r?.code !== "CBAM" && r?.price && !picked.includes(r)) picked.push(r);
  }
  return picked;
}

// Kết quả chấm dự báo → ô màu: ✓ đúng/đúng hướng · ✕ sai · ! đang lệch · ? chưa rõ.
const VERDICT_MARK: Record<string, { cls: string; icon: string }> = {
  correct: { cls: "bg-up text-white", icon: "✓" },
  on_track: { cls: "bg-up/10 text-up ring-1 ring-inset ring-up/50", icon: "✓" },
  direction_only: { cls: "bg-up/10 text-up ring-1 ring-inset ring-up/50", icon: "✓" },
  wrong: { cls: "bg-down text-white", icon: "✕" },
  diverging: { cls: "bg-warn-tint text-warn ring-1 ring-inset ring-warn/50", icon: "!" },
  unclear: { cls: "bg-surface text-muted-light ring-1 ring-inset ring-border-soft", icon: "?" },
};

function ForecastStrip({ review }: { review: ForecastReview }) {
  const tf = useTranslations("forecastReview");
  const items = [...review.items].sort((a, b) => a.report_date.localeCompare(b.report_date));
  return (
    <ol className="mt-2.5 flex gap-1.5">
      {items.map(it => {
        const m = VERDICT_MARK[it.verdict] ?? VERDICT_MARK.unclear;
        return (
          <li key={it.report_date} className="flex min-w-0 max-w-[46px] flex-1 flex-col items-center gap-1" title={`${formatShortDate(it.report_date)} · ${tf(`verdict.${it.verdict}`)}`}>
            <span className={clsx("grid h-7 w-full place-items-center rounded-md text-[13px] font-bold", m.cls)} aria-hidden="true">{m.icon}</span>
            <span className="text-[10.5px] tabular-nums text-muted-light">{formatShortDate(it.report_date)}</span>
            <span className="sr-only">{tf(`verdict.${it.verdict}`)}</span>
          </li>
        );
      })}
    </ol>
  );
}

// Dự báo trước (cột phải, ô 3): dải kết quả chấm các báo cáo gần nhất — chỉ liệt kê ngày Sai / Đúng / Đang theo dõi,
// không hiện tỷ lệ (Sai chốt ngay khi chạm cắt lỗ còn Đúng phải chờ chạm mục tiêu/hết khung, nên tỷ lệ giữa khung luôn
// nghiêng về Sai, dễ hiểu nhầm). "direction_only" (đúng hướng, lệnh không khớp) xếp vào Đúng; còn lại = Đang theo dõi.
function HeroForecast({ review, className }: { review?: ForecastReview | null; className?: string }) {
  const t = useTranslations("reportDoc");
  const items = review?.items ?? [];
  const right = items.filter(i => i.verdict === "correct" || i.verdict === "direction_only").map(i => i.report_date);
  const wrong = items.filter(i => i.verdict === "wrong").map(i => i.report_date);
  const watching = items.filter(i => !right.includes(i.report_date) && !wrong.includes(i.report_date)).map(i => i.report_date);
  return (
    <HeroCard
      compact
      title={t("signal.track")}
      href="#section-forecast-review"
      className={className}
    >
      {items.length > 0 && review && <ForecastStrip review={review} />}
      <div className="mt-2 space-y-0.5 text-[12.5px] leading-snug tabular-nums">
        {!items.length && <div className="text-muted-light">{t("signal.trackNone")}</div>}
        {wrong.length > 0 && <div className="font-semibold text-down">{t("signal.trackWrong", { dates: joinDayMonth(wrong) })}</div>}
        {right.length > 0 && <div className="font-semibold text-up">{t("signal.trackRight", { dates: joinDayMonth(right) })}</div>}
        {watching.length > 0 && <div className="text-muted-light">{t("signal.trackWatching", { dates: joinDayMonth(watching) })}</div>}
      </div>
    </HeroCard>
  );
}

// Chia cột khối mở đầu theo các ô có dữ liệu (s = Tóm tắt, v = Nhận định + giá EUA, d = cột lệnh/phòng hộ/dự báo).
// grid: lưới ngoài; s/v/d: class của từng ô. Màn hình: 3 ô cạnh nhau từ khung 1024px (WIDE), 768–1023px Tóm tắt hết
// hàng + 2 ô bên dưới (A2). In (mọi khổ từ 576px — A4 dọc/ngang, 16:9): Tóm tắt và Nhận định (giá | câu nhận định cạnh
// nhau) đều hết hàng, 3 ô lệnh/phòng hộ/dự báo thành 1 hàng 3 cột — ô hẹp cạnh Tóm tắt dài sẽ cao hơn 1 trang và bị cắt.
const A2 = "@3xl/report:grid-cols-2 print:@xl/report:grid-cols-2";
const A2_FULL = "@3xl/report:col-span-2 print:@xl/report:col-span-2";
const PRINT_FULL = "print:@xl/report:col-span-2";
const PRINT_ROW = "print:@xl/report:grid print:@xl/report:grid-cols-3";
const HERO_SPANS: Record<string, { grid: string; s?: string; v?: string; d?: string }> = {
  svd: {
    // Nhận định to nhất, Tóm tắt = cột phải: 3/4/3 (báo cáo 1024–1279px) → 3/6/3 (≥ 1280px).
    grid: `${A2} not-print:@5xl/report:grid-cols-10 not-print:@7xl/report:grid-cols-12`,
    s: `${A2_FULL} not-print:@5xl/report:col-span-3`,
    // In: Nhận định (giữ trọn ô) đứng TRƯỚC Tóm tắt (ngắt được giữa các ý) — Tóm tắt chảy tiếp lấp chỗ trống, không để ô
    // Nhận định bị đẩy nguyên sang trang 2 và bỏ trắng nửa trang 1.
    v: `not-print:@5xl/report:col-span-4 not-print:@7xl/report:col-span-6 ${PRINT_FULL} print:order-first`,
    d: `not-print:@5xl/report:col-span-3 ${PRINT_FULL} ${PRINT_ROW}`,
  },
  sv: { grid: A2, s: "", v: "" },
  sd: { grid: `${A2} not-print:@5xl/report:grid-cols-12`, s: "not-print:@5xl/report:col-span-8", d: "not-print:@5xl/report:col-span-4" },
  vd: { grid: `${A2} not-print:@5xl/report:grid-cols-12`, v: "not-print:@5xl/report:col-span-8", d: "not-print:@5xl/report:col-span-4" },
  s: { grid: "", s: "" },
  v: { grid: "", v: "" },
  d: { grid: "", d: "" },
};

// Thanh số liệu chính chạy dọc (thay ô "giá EUA + nhãn nhận định" cũ trên thanh menu): giá CBAM quý ước tính rồi lần
// lượt các mã dẫn dắt EUA — mỗi lần 1 mục, mục mới trượt từ trên xuống sau TICKER_MS, chạy liên tục. Không in.
interface TickerItem { key: string; label: string; value: string; unit?: string; delta?: { text: string; up: boolean }; sub?: string }
const TICKER_MS = 3500;

function useTickerItems(cbamEstimate: CbamEstimate | null | undefined, rows: any[]): TickerItem[] {
  const t = useTranslations("reportDoc");
  const locale = useLocale();
  const n2 = (x: number) => fnum(x, 2, locale);
  const items: TickerItem[] = [];
  if (cbamEstimate) {
    items.push({
      key: "CBAM",
      label: t("hero.cbamEstimate", { quarter: cbamEstimate.quarter }),
      value: n2(cbamEstimate.estimate),
      unit: "EUR/tCO₂",
      sub: cbamEstimate.ci_low !== null && cbamEstimate.ci_high !== null
        ? t("hero.ci", { level: cbamEstimate.ci_level, low: n2(cbamEstimate.ci_low), high: n2(cbamEstimate.ci_high) })
        : undefined,
    });
  }
  for (const r of pickDrivers(rows)) {
    const [raw, ...unitParts] = String(r.price).split(" ");
    const pct = deltaPct(r.day_change_pct, r.dday);
    items.push({
      key: String(r.code ?? r.name),
      label: r.name,
      value: formatCompactPriceNumber(raw, locale),
      unit: subCO2(unitParts.join(" ")),
      delta: pct === undefined ? undefined : {
        up: pct >= 0,
        text: `${pct >= 0 ? "▲" : "▼"} ${pct > 0 ? "+" : pct < 0 ? "−" : ""}${n2(Math.abs(pct))}% ${t("hero.day")}`,
      },
    });
  }
  return items;
}

function PriceTicker({ items, label, className }: { items: TickerItem[]; label?: string; className?: string }) {
  const [index, setIndex] = useState(0);
  useEffect(() => {
    if (items.length < 2) return;
    const id = window.setInterval(() => setIndex(i => (i + 1) % items.length), TICKER_MS);
    return () => window.clearInterval(id);
  }, [items.length]);
  if (!items.length) return null;
  const cur = items[index % items.length];
  return (
    <div className={clsx("flex w-full min-w-0 items-center gap-2.5", className)}>
      {label && (
        <span className="flex shrink-0 items-center gap-1 text-[11.5px] font-bold uppercase tracking-[0.08em] text-primary-dark">
          <ArrowDownUp size={13} aria-hidden="true" /> <span className="hidden sm:inline">{label}</span>
        </span>
      )}
      {/* Danh sách đầy đủ cho trình đọc màn hình; phần chạy chỉ là hình ảnh. */}
      <ul className="sr-only">
        {items.map(it => <li key={it.key}>{it.label}: {it.value} {it.unit} {it.delta?.text} {it.sub}</li>)}
      </ul>
      <div className="relative h-9 min-w-0 flex-1 overflow-hidden" aria-hidden="true">
        <div key={`${cur.key}-${index}`} className="report-ticker-down absolute inset-0 flex items-center gap-x-2 whitespace-nowrap text-[13px] tabular-nums">
          <span className="min-w-0 truncate font-semibold text-body">{cur.label}</span>
          <b className="shrink-0 text-[14px] text-label">{cur.value}</b>
          {cur.unit && <span className="shrink-0 text-[11.5px] text-muted-light">{cur.unit}</span>}
          {cur.delta && <span className={clsx("shrink-0 font-semibold", cur.delta.up ? "text-up" : "text-down")}>{cur.delta.text}</span>}
          {cur.sub && <span className="shrink-0 text-[11.5px] text-muted-light">{cur.sub}</span>}
        </div>
      </div>
    </div>
  );
}

// "Điều đáng quan tâm nhất hôm nay" (hết bề rộng, đầu phần Tổng quan; thay dải "Tin nóng" cũ, giữ tông đỏ của dải đó): 1–2 câu LLM chọn
// SAU KHI đủ các mục — điều quan trọng nhất cả báo cáo: biến động giá, tin nóng/tin trực tiếp, kịch bản xác suất Cao, phòng hộ CBAM,
// gợi ý kinh doanh (content["1"].highlight, report_generator.HIGHLIGHT_TYPES). Chỉ hiện nội dung, không nhãn loại. Báo cáo cũ không có → ẩn.
const HIGHLIGHT_TYPES = ["price", "news", "scenario", "hedge", "biz"] as const;
interface ReportHighlight { type: (typeof HIGHLIGHT_TYPES)[number]; text: string; source_name?: string | null; source_url?: string | null }

function TodayHighlight({ highlight }: { highlight: ReportHighlight }) {
  const t = useTranslations("reportDoc");
  return (
    <div className="flex min-w-0 items-stretch overflow-hidden rounded-xl border border-down/30 bg-down/[0.04] print:break-inside-avoid">
      <div className="w-1.5 shrink-0 bg-down [print-color-adjust:exact] [-webkit-print-color-adjust:exact]" aria-hidden="true" />
      <div className="min-w-0 flex-1 px-4 py-3 sm:px-5">
        <h2 className="report-heading flex items-center gap-1.5 text-[13px] font-bold uppercase tracking-[0.08em] text-down">
          <Sparkles size={15} aria-hidden="true" /> {t("highlight.title")}
        </h2>
        <p className="mt-1.5 text-left text-[16px] font-semibold leading-[1.55] text-label">
          <RichText text={highlight.text} nums />
          {highlight.source_name && <InlineSource name={highlight.source_name} url={highlight.source_url} />}
        </p>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// KHUYẾN NGHỊ VỊ THẾ (đầu Phần 2) — thang giá chung 3 khung thời gian → thẻ kịch bản từng khung (Kịch bản hành
// động) → phòng hộ CBAM → lệnh mô phỏng đang mở. Hàng chỉ số cũ (nhận định, giá, biên 30 phiên, ATR, dự báo trước)
// chuyển lên khối mở đầu.
// ═══════════════════════════════════════════════════════════════════════════
function PositionCard({ plans, stats, minRr, cbamEstimate, hedgeAction, openPositions }: {
  plans: HorizonPlan[]; stats: EuaStats; minRr: number;
  cbamEstimate?: CbamEstimate | null; hedgeAction?: HedgeAction | null; openPositions?: OpenPosition[];
}) {
  const t = useTranslations("reportDoc");
  const locale = useLocale();
  const fp = (n: number) => fmtPrice(n, locale);
  const marks = buildLadderMarks(plans, stats.range, stats.last, t);

  return (
    <section id="section-recommendation" className="scroll-mt-32 rounded-2xl border border-border bg-background shadow-[var(--shadow-soft)] print:shadow-none">
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-border px-4 sm:px-5 pt-4 pb-3 print:break-after-avoid">
        <h3 className="report-heading flex items-center gap-2 text-[18px] sm:text-[19px] font-bold text-primary-dark">
          <Crosshair size={19} strokeWidth={2.5} aria-hidden="true" /> {t("signal.title")}
        </h3>
        {stats.lastDate && (
          <span className="text-[12.5px] text-muted-light tabular-nums">{t("signal.dataAsOf", { date: formatFullDate(stats.lastDate) })}</span>
        )}
      </header>

      <div className="space-y-5 px-3 sm:px-5 py-4">
        {/* ═══ Thang giá: mọi mức của các kịch bản + giá hiện tại + biên 30 phiên ═══ */}
        {marks.length > 1 && (
          <div className="print:break-inside-avoid">
            <PriceLadder marks={marks} range={stats.range} />
            <PriceLevelList marks={marks} />
          </div>
        )}

        <div>
          <h4 className="report-heading mb-2.5 text-[13px] font-bold uppercase tracking-[0.08em] text-label">{t("scenario.heading")}</h4>
          <HorizonCards plans={plans} last={stats.last} minRr={minRr} />
          <p className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-muted-light">
            <span className="inline-flex items-center gap-1"><span className="inline-block h-[5px] w-3 rounded-[2px] bg-down" aria-hidden="true" />{t("signal.legendRisk")}</span>
            <span className="inline-flex items-center gap-1"><span className="inline-block h-[5px] w-3 rounded-[2px] bg-up" aria-hidden="true" />{t("signal.legendReward")}</span>
            <span>{t("signal.legendNote", { min: fnum(minRr, 1, locale) })}</span>
          </p>
          {/* Lưu ý — chỉ mang tính tham khảo, không phải khuyến nghị đầu tư trực tiếp. */}
          <div className="mt-3 flex items-start gap-2 rounded-lg border border-warn/30 bg-warn-tint px-3 py-2.5">
            <Info size={15} className="mt-0.5 shrink-0 text-warn" aria-hidden="true" />
            <p className="text-left text-[13.5px] leading-[1.55] text-body">
              <b className="text-label">{t("scenario.noteLabel")}</b> {t("scenario.note")}
            </p>
          </div>
        </div>
      </div>

      {/* ═══ Phòng hộ CBAM — sổ phòng hộ SIM, tách khỏi lệnh của bàn giao dịch ở trên ═══ */}
      {cbamEstimate ? (
        <CbamHedgePlan est={cbamEstimate} hedgeAction={hedgeAction} />
      ) : hedgeAction ? (
        <LegacyHedgeNote hedgeAction={hedgeAction} />
      ) : null}

      {/* ═══ Lệnh mô phỏng còn mở từ báo cáo trước — đối chiếu với mức giá mới ═══ */}
      {openPositions && openPositions.length > 0 && (
        <div className="border-t border-border px-4 sm:px-5 py-3.5">
          <h3 className="mb-1.5 text-[13.5px] font-bold text-label">{t("signal.openPositions")}</h3>
          <ul className="space-y-1.5 text-[13.5px] leading-[1.55] text-body">
            {openPositions.map(p => (
              <li key={p.report_date}>
                <span className={clsx(
                  "mr-2 inline-block rounded border px-1.5 py-px text-[11px] font-bold tracking-wider",
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

function ConclusionAware({ text, className, nums }: { text: string; className?: string; nums?: boolean }) {
  const match = text.match(CONCLUSION_TAG_RE);
  if (!match || match.index === undefined) {
    return <p className={className}><RichText text={text} nums={nums} /></p>;
  }
  const before = text.slice(0, match.index).trim();
  const highlight = text.slice(match.index).trim();
  return (
    <div className="space-y-1.5">
      {before && <p className={className}><RichText text={before} nums={nums} /></p>}
      <div className="rounded-md border border-primary/30 bg-tint/60 px-3 py-2">
        <p className="text-left text-[15px] leading-[1.5] font-bold text-primary-dark">
          <RichText text={highlight} nums={nums} />
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

// 2 cấp tiêu đề, không đánh số "01"/"02":
//  - PartHeading: đầu Phần — dải màu đậm cùng tông banner (nhãn "Phần n" nhỏ phía trên + tiêu đề chữ trắng), hoặc
//    đứng riêng như "Review dự báo", "Nguồn tham khảo".
//  - SubHeading: đầu khối trong 1 Phần — chữ đậm màu chủ đạo + vạch màu nhỏ, `aside` cho thông tin phụ cùng hàng.
// Class "report-heading" dùng chung để CSS in ấn (globals.css) nhận diện, ép không ngắt trang ngay sau heading; trình
// render PDF (services/report_pdf.py) cũng chờ class này xuất hiện mới in.

// Khung của 2 khối chữ dài đứng cạnh nhau trong 1 Phần (Diễn biến chính | Lịch sự kiện): lưới để mặc định "stretch"
// nên 2 khung luôn cao bằng nhau. Khi in (1 cột, khối dài chảy qua nhiều trang) bỏ khung.
const PANEL = "min-w-0 rounded-xl border border-border bg-background p-4 sm:p-5 print:rounded-none print:border-0 print:bg-transparent print:p-0";

function PartHeading({ eyebrow, title, icon: Icon, className }: { eyebrow?: string; title: string; icon: typeof Sparkles; className?: string }) {
  return (
    <div className={clsx(
      "report-heading mb-6 flex items-center gap-3.5 rounded-xl bg-gradient-to-r from-[#1B4D3E] via-[#1a5c4a] to-[#0f5f5a] px-4 sm:px-5 py-3 text-white print:mb-4 print:break-after-avoid [print-color-adjust:exact] [-webkit-print-color-adjust:exact]",
      className,
    )}>
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-white/15 ring-1 ring-white/25">
        <Icon size={20} strokeWidth={2.25} aria-hidden="true" />
      </span>
      <div className="min-w-0">
        {eyebrow && <div className="text-[12px] font-bold uppercase tracking-[0.16em] text-[#8fd9a8]">{eyebrow}</div>}
        <h2 className="text-[20px] sm:text-[23px] font-bold leading-tight text-white [text-wrap:balance]">{title}</h2>
      </div>
    </div>
  );
}

function SubHeading({ children, aside, className }: { children: React.ReactNode; aside?: React.ReactNode; className?: string }) {
  return (
    <div className={clsx("report-heading mb-3 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 print:break-after-avoid", className)}>
      <h3 className="flex items-center gap-2 text-[17px] sm:text-[18px] font-bold leading-snug text-primary-dark">
        <span className="h-[18px] w-[4px] shrink-0 rounded-full bg-primary" aria-hidden="true" />
        {children}
      </h3>
      {aside}
    </div>
  );
}

// Nguồn tin đặt NGAY CUỐI nội dung (cùng dòng) — inline-block nên cả cụm "Nguồn: X ↗" chỉ xuống dòng khi phần còn lại
// của dòng không đủ chỗ (yêu cầu 21/10/2026; trước nằm riêng 1 dòng dưới nội dung). Đặt BÊN TRONG đoạn văn chứa nội dung.
function InlineSource({ name, url, className }: { name: string; url?: string | null; className?: string }) {
  const t = useTranslations("reportDoc");
  const cls = clsx("inline-block max-w-full text-[12.5px] font-normal leading-[1.4]", className);
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
          <div key={i} className="flex gap-3 print:break-inside-avoid">
            <div className="mt-0.5 flex w-[58px] shrink-0 items-center justify-center self-start rounded-lg border border-border bg-tint/60 py-1.5">
              <span className="text-[12.5px] font-bold leading-[1.3] tabular-nums text-primary-dark">{ev.datetime_vn || "—"}</span>
            </div>
            <div className="flex shrink-0 flex-col items-center">
              <span className={clsx("mt-3.5 h-2.5 w-2.5 shrink-0 rounded-full border-2 bg-background", impactDot)} />
              {!isLast && <span className="mt-1 w-px flex-1 bg-border" />}
            </div>
            <div className={clsx("min-w-0 flex-1", !isLast && "pb-4")}>
              <div className="flex items-start justify-between gap-3 pt-1">
                <span className="text-[14.5px] leading-[1.45] text-foreground">{ev.event}</span>
                <span className={clsx(
                  "shrink-0 rounded border px-1.5 py-0.5 text-[11px] font-bold uppercase",
                  ev.impact === "Cao" ? "text-down border-down/30 bg-red-50" :
                    ev.impact === "Trung" ? "text-warn border-warn/30 bg-warn-tint" :
                      "text-muted-light border-border"
                )}>{enumLabel(ev.impact)}</span>
              </div>
              {ev.outcome && (
                <p className="mt-1.5 text-[14px] leading-[1.55] text-body">
                  <span className="font-semibold text-label">{t("events.outcome")}</span><RichText text={ev.outcome} nums />
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

// Theo dõi chính sách ETS/MSR — báo cáo rộng: bảng 4 cột Hồ sơ | Lập trường/tiến độ | Bước tiếp theo | Tác động; báo
// cáo hẹp VÀ khi in: mỗi hồ sơ 1 thẻ xếp dọc (bảng 4 cột ở khổ A4/375px chỉ còn vài chữ mỗi dòng, mỗi hồ sơ cao gần nửa
// trang). Cùng nội dung ở 2 dạng.
function EtsPolicyTable({ items }: { items: Record<string, any>[] }) {
  const t = useTranslations("reportDoc");
  const impactKey = (it: Record<string, any>) =>
    it.kind === "event" ? "event" : it.impact === "tăng" ? "up" : it.impact === "giảm" ? "down" : "neutral";
  const chip = (it: Record<string, any>) => (
    <span className={clsx("inline-block rounded border px-1.5 py-0.5 text-[11.5px] font-bold uppercase tracking-wider",
      POLICY_IMPACT_TONE[it.impact] || POLICY_IMPACT_TONE["trung lập"])}>
      {t(`etsPolicy.impactLabel.${impactKey(it)}`)}
    </span>
  );
  const source = (it: Record<string, any>) => it.source_url ? (
    <a href={it.source_url} target="_blank" rel="noopener noreferrer"
      className="inline-block max-w-full break-words text-[12.5px] font-normal text-primary hover:underline">
      {it.source_name}{it.source_date ? ` · ${it.source_date}` : ""}
    </a>
  ) : it.source_name ? (
    // Hồ sơ admin xác nhận (ets_policy_dossiers.yaml) có thể chưa có link.
    <span className="inline-block max-w-full break-words text-[12.5px] font-normal text-muted-light">
      {it.source_name}{it.source_date ? ` · ${it.source_date}` : ""}
    </span>
  ) : null;
  const meta = (it: Record<string, any>) => (
    <>
      {it.stage && <span className="block text-[12px] font-normal text-muted-light">{it.stage}</span>}
      {it.pinned && it.verified === false && (
        <span className="block text-[12px] font-normal italic text-muted-light">{t("etsPolicy.unverified")}</span>
      )}
    </>
  );
  const mechanism = (it: Record<string, any>) => {
    const [main, rejected] = splitPolicyMechanism(it);
    return (
      <>
        {main && (
          <span className="mt-1.5 block text-[13px] leading-[1.45] text-body">
            {it.kind !== "event" && <span className="font-semibold text-label">{t("etsPolicy.ifAdopted")} </span>}
            {main}
          </span>
        )}
        {rejected && (
          <span className="mt-1.5 block border-t border-dashed border-border pt-1.5 text-[13px] leading-[1.45] text-body">
            <span className="font-semibold text-label">{t("etsPolicy.ifRejected")} </span>{rejected}
          </span>
        )}
      </>
    );
  };
  const cbam = (it: Record<string, any>) => it.cbam_effect && (
    <span className="mt-1 block text-[13px] text-primary-dark">{t("etsPolicy.cbamEffect")} {it.cbam_effect}</span>
  );
  return (
    <div>
      <SubHeading>{t("etsPolicy.title")}</SubHeading>
      {/* Báo cáo hẹp: thẻ xếp dọc (cả khi in) */}
      <ul className="list-none space-y-3 @2xl/report:hidden">
        {items.map((it, i) => (
          <li key={i} className="rounded-xl border border-border bg-background p-3.5">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0 flex-1 text-[14.5px] font-bold leading-snug text-label">
                {it.item} {source(it)}
                {meta(it)}
              </div>
              {chip(it)}
            </div>
            <p className="text-left mt-2 text-[14px] leading-[1.55] text-body"><RichText text={it.status || ""} nums /></p>
            {cbam(it)}
            {it.next_step && (
              <p className="text-left mt-2 text-[14px] leading-[1.55] text-body">
                <span className="mb-0.5 block text-[11.5px] font-bold uppercase tracking-wide text-muted-light">{t("etsPolicy.nextStep")}</span>
                <RichText text={it.next_step} nums />
              </p>
            )}
            <div className="mt-2 rounded-lg bg-surface px-3 py-2">
              <span className="block text-[11.5px] font-bold uppercase tracking-wide text-muted-light">{t("etsPolicy.impact")}</span>
              {mechanism(it)}
            </div>
          </li>
        ))}
      </ul>
      {/* Báo cáo rộng: bảng — cả khi in (PDF bản máy tính đủ rộng), từng hàng không bị cắt ngang. */}
      <div className="hidden overflow-x-auto rounded-xl border border-border @2xl/report:block">
        <table className="w-full table-fixed border-collapse text-[14px]">
          <thead>
            <tr className="bg-tint">
              {["item", "status", "nextStep", "impact"].map((k) => (
                <th key={k} className={clsx(
                  "border-b-2 border-primary/30 px-3 py-2.5 text-left text-[11.5px] font-bold uppercase tracking-wider text-primary-dark",
                  k === "impact" ? "w-[30%]" : k === "item" ? "w-[24%]" : k === "nextStep" ? "w-[19%]" : "",
                )}>{t(`etsPolicy.${k}`)}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border-soft">
            {items.map((it, i) => (
              <tr key={i} className="align-top even:bg-surface/70 print:break-inside-avoid">
                <td className="break-words px-3 py-3 font-semibold text-label">
                  {it.item}
                  {/* Nguồn liền sau tên hồ sơ, chỉ xuống dòng khi hết chỗ (inline-block). */}
                  {source(it) && <>{" "}{source(it)}</>}
                  {meta(it)}
                </td>
                <td className="break-words px-3 py-3 leading-[1.55] text-body">
                  <RichText text={it.status || ""} nums />
                  {cbam(it)}
                </td>
                <td className="break-words px-3 py-3 leading-[1.55] text-body"><RichText text={it.next_step || ""} nums /></td>
                <td className="px-3 py-3">
                  {chip(it)}
                  {mechanism(it)}
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
  // Cơ sở / KPI / đơn vị phụ trách… của 1 gợi ý — thu gọn sau nút, bấm mới mở; khi in luôn hiện.
  const details = (row: Record<string, any>) => {
    const extraRows = (extras || []).filter((x) => typeof row[x.key] === "string" && row[x.key].trim());
    return { extraRows, has: !!(infoKey && row[infoKey]) || extraRows.length > 0 };
  };
  const detailBody = (row: Record<string, any>, extraRows: { key: string; label: string }[]) => (
    <>
      {infoKey && row[infoKey] && (
        <div>
          <span className="mr-1 font-bold text-primary-dark">{infoLabel || t("biz.details")}:</span>
          <RichText text={row[infoKey]} nums />
        </div>
      )}
      {extraRows.map((x) => (
        <div key={x.key}>
          <span className="mr-1 font-bold text-primary-dark">{x.label}:</span>
          <RichText text={row[x.key]} nums />
        </div>
      ))}
    </>
  );
  const toggle = (ri: number) => {
    const isOpen = openInfo.has(ri);
    return (
      <button
        type="button"
        onClick={() => toggleInfo(ri)}
        aria-expanded={isOpen}
        aria-label={isOpen ? t("biz.hideInfo", { label: infoName }) : t("biz.showInfo", { label: infoName })}
        className={clsx(
          "mt-2 inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[12px] font-semibold transition-colors print:hidden",
          isOpen ? "border-primary bg-primary text-white" : "border-primary/40 text-primary hover:bg-tint",
        )}
      >
        <Info size={12} strokeWidth={2.5} aria-hidden="true" />
        {infoName}
        {isOpen ? <ChevronUp size={12} aria-hidden="true" /> : <ChevronDown size={12} aria-hidden="true" />}
      </button>
    );
  };
  return (
    <div>
      <h4 className={clsx("mb-2.5 text-[13px] font-bold uppercase tracking-[0.1em]", accent)}>{heading}</h4>
      {rows && rows.length > 0 ? (
        <>
          {/* Báo cáo hẹp: mỗi gợi ý 1 thẻ (bảng 2 cột ở 375px chỉ còn 3–5 chữ mỗi dòng). */}
          <ol className="list-none space-y-3 @2xl/report:hidden print:hidden">
            {rows.map((row, ri) => {
              const { extraRows, has } = details(row);
              return (
                <li key={ri} className="rounded-xl border border-border bg-background p-3.5">
                  <div className="flex items-start gap-3">
                    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-surface text-[12.5px] font-bold text-muted-light">{ri + 1}</span>
                    <div className="min-w-0 flex-1 space-y-2">
                      {columns.map((col) => (
                        <div key={col.key}>
                          <div className="text-[11.5px] font-bold uppercase tracking-wide text-muted-light">{col.label}</div>
                          <p className="text-left text-[14.5px] leading-[1.55] text-body"><RichText text={row[col.key] || ""} nums /></p>
                        </div>
                      ))}
                      {has && (
                        <div>
                          {toggle(ri)}
                          {openInfo.has(ri) && (
                            <div className="mt-2 space-y-1 rounded-lg bg-tint/60 px-3 py-2 text-[14px] leading-[1.55] text-body">{detailBody(row, extraRows)}</div>
                          )}
                        </div>
                      )}
                    </div>
                    {onDismiss && typeof row.id === "number" && <DismissButton onClick={() => onDismiss(row.id)} className="shrink-0" />}
                  </div>
                </li>
              );
            })}
          </ol>
          {/* Báo cáo rộng + khi in: bảng — mỗi gợi ý 1 hàng, cột theo cấu trúc nhân quả; chi tiết ở dòng phụ ngay dưới. */}
          <div className="hidden overflow-x-auto rounded-xl border border-border @2xl/report:block print:block">
            {/* table-fixed + chia đều cột nội dung: mỗi trường trong prompt tối đa 1 câu ngắn (xem
                _prompt_biz_recommendation) nên độ dài tương đương nhau. */}
            <table className="w-full table-fixed border-collapse text-[14.5px]">
              <thead>
                <tr>
                  {/* Cột # rộng hơn khi có nút gỡ (admin) để số thứ tự + nút xếp dọc, căn giữa cân đối */}
                  <th className={clsx(
                    "border-b-2 border-r border-b-primary/30 border-r-border bg-tint px-1 py-2.5 text-center text-[11.5px] font-bold uppercase tracking-wider text-primary-dark",
                    onDismiss ? "w-[48px]" : "w-[40px]"
                  )}>#</th>
                  {columns.map((col, i) => (
                    <th
                      key={col.key}
                      className={clsx(
                        colWidthCls,
                        "border-b-2 border-primary/30 bg-tint px-3 py-2.5 text-left text-[11.5px] font-bold uppercase tracking-wider text-primary-dark",
                        i < columns.length - 1 && "border-r border-r-border"
                      )}
                    >
                      {col.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {rows.map((row, ri) => {
                  const { extraRows, has } = details(row);
                  const isOpen = openInfo.has(ri);
                  return (
                    <Fragment key={ri}>
                      {/* Chỉ dính với hàng chi tiết ngay sau khi có — hàng không có chi tiết mà vẫn "không ngắt sau" thì cả bảng
                          thành 1 khối liền (cùng tên cột) và bị đẩy nguyên sang trang sau. */}
                      <tr className={clsx("align-top even:bg-surface/50", has && "print:break-after-avoid")}>
                        <td className="border-r border-border bg-surface px-1 py-3 text-[12.5px] text-muted-light">
                          <div className="flex flex-col items-center gap-2">
                            <span className="font-bold leading-6">{ri + 1}</span>
                            {onDismiss && typeof row.id === "number" && (
                              <DismissButton onClick={() => onDismiss(row.id)} />
                            )}
                          </div>
                        </td>
                        {columns.map((col, i) => {
                          const isLast = i === columns.length - 1;
                          return (
                            <td key={col.key} className={clsx("break-words px-3 py-3 leading-[1.55] text-body", !isLast && "border-r border-border")}>
                              <RichText text={row[col.key] || ""} nums />
                              {isLast && has && <div>{toggle(ri)}</div>}
                            </td>
                          );
                        })}
                      </tr>
                      {has && (
                        <tr className={clsx(isOpen ? "table-row" : "hidden print:table-row")}>
                          <td className="border-r border-border bg-surface" />
                          <td colSpan={columns.length} className="bg-tint/50 px-3 py-2.5 text-[14px] leading-[1.55] text-body">
                            <div className="space-y-1">{detailBody(row, extraRows)}</div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <p className="text-[14.5px] italic text-muted-light">{t("biz.empty")}</p>
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

// Biểu đồ EUA (đầu Phần 1): nến 30 phiên | `aside` (biểu đồ chỉ số giá — 2 khối cao bằng nhau), bên dưới 1 hàng 5 ô số
// liệu (phiên gần nhất, 1 tuần, biên 30 phiên, thay đổi 30 phiên, khối lượng) cao bằng nhau. Mỗi ô ghi đủ các số của câu
// "Nhận định thị trường" cũ (câu đó chỉ nhắc lại đúng các số này). Báo cáo hẹp/in: xếp dọc, ô số 2 cột.
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
  const loc = locale === "en" ? "en-US" : "vi-VN";
  const sign = (n: number) => (n > 0 ? "+" : "");

  // Biểu đồ nến | 5 ô tóm tắt nằm ngang từ khung báo cáo ≥ 768px (@3xl), tỷ lệ 6,5 : 3,5 — ô tóm tắt xếp dọc, giãn đều
  // theo chiều cao biểu đồ, mỗi dòng 1 hàng (StatTile cắt "…" + title khi quá hẹp). Hẹp hơn: ô tóm tắt xuống dưới (2 cột).
  // Bản in theo cùng bố cục (PDF A4 dọc bản máy tính rộng ~875px ≥ @3xl → biểu đồ | ô tóm tắt như màn hình).
  return (
    <div className="grid gap-4 @3xl/report:grid-cols-[minmax(0,13fr)_minmax(0,7fr)]">
      <div data-print-whole className="flex min-w-0 flex-col print:break-inside-avoid">
        {printHeading && <div className="hidden print:block">{printHeading}</div>}
        <div className="flex-1 rounded-xl border border-border bg-background p-3.5 sm:p-5 print:p-4!">
          <div className="mb-4 flex flex-wrap items-start justify-between gap-x-6 gap-y-3 border-b border-border pb-4">
            <div className="flex min-w-0 items-center gap-3">
              <div className="shrink-0 rounded-xl bg-primary p-2 text-white">
                <Leaf size={22} aria-hidden="true" />
              </div>
              <div className="min-w-0">
                <h3 className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[17px] font-bold text-label">
                  EUA Dec-26
                  {/* Chỉ có dữ liệu 1M (chart_data 30 phiên) — nhãn tĩnh, không phải nút chọn khung. */}
                  <span className="rounded-md bg-primary px-2 py-0.5 text-[11.5px] font-bold text-white">1M</span>
                  <span className="text-[13px] font-normal text-muted-light">{t("dash.candles")}</span>
                </h3>
                <p className="text-left mt-0.5 text-[13px] text-muted-light">{t("dash.marketDesc")}</p>
              </div>
            </div>
            <div className="ml-auto flex flex-col items-end gap-1">
              <div className="flex items-baseline gap-2.5">
                <span className="text-[26px] sm:text-[28px] font-bold leading-none tabular-nums text-label">{fnum(lastClose, 2, locale)}</span>
                <span className={clsx("inline-flex items-center gap-0.5 rounded-full px-2.5 py-1 text-[13px] font-bold tabular-nums text-white", dayChange >= 0 ? "bg-up" : "bg-down")}>
                  {dayChange >= 0 ? <ChevronUp size={15} strokeWidth={3} /> : <ChevronDown size={15} strokeWidth={3} />}
                  {sign(dayChange)}{fnum(dayChange, 2, locale)} ({sign(dayChangePct)}{fnum(dayChangePct, 2, locale)}%)
                </span>
              </div>
              <div className="flex items-center gap-1.5 text-[12px] font-medium text-muted-light">
                <Calendar size={13} aria-hidden="true" /> {t("dash.closeOf", { date: formatFullDate(lastDate) })}
              </div>
            </div>
          </div>

          <CandlestickChart report={report} />
        </div>
      </div>

      <div className="grid min-w-0 gap-2 @2xl/report:grid-cols-2 @3xl/report:grid-cols-1 @3xl/report:auto-rows-fr print:@xl/report:@max-3xl/report:grid-cols-2 print:break-inside-avoid">
        <StatTile
          icon={<RefreshCw size={14} />}
          label={t("dash.lastRange")}
          value={`${fnum(dayLow, 2, locale)} – ${fnum(dayHigh, 2, locale)}`}
          // Cao/thấp đã là số chính của ô — dòng phụ chỉ còn mở → đóng + biên độ.
          lines={[capFirst(t("dash.lastSessionShort", {
            open: fnum(dayOpen, 2, locale), close: fnum(lastClose, 2, locale),
            range: fnum(dayRange, 2, locale), pct: fnum(dayRangePct, 1, locale),
          }))]}
        />
        <StatTile
          icon={sevenDayChange >= 0 ? <TrendingUp size={14} className="text-up" /> : <TrendingDown size={14} className="text-down" />}
          label={sevenDayChange >= 0 ? t("dash.up7") : t("dash.down7")}
          value={`${sign(sevenDayChange)}${fnum(sevenDayChange, 2, locale)}`}
          valueClass={sevenDayChange >= 0 ? "text-up" : "text-down"}
          lines={[t("dash.vsPrice", { pct: `${sign(sevenDayChangePct)}${fnum(sevenDayChangePct, 2, locale)}`, price: fnum(sevenDaysAgo, 2, locale) })]}
        />
        <StatTile
          icon={<CalendarRange size={14} />}
          label={t("dash.range30")}
          value={`${fnum(monthLow, 2, locale)} – ${fnum(monthHigh, 2, locale)}`}
          lines={[t("dash.lowHigh")]}
        />
        <StatTile
          icon={monthChange >= 0 ? <TrendingUp size={14} className="text-up" /> : <TrendingDown size={14} className="text-down" />}
          label={t("dash.change30")}
          value={`${fnum(firstClose, 2, locale)} → ${fnum(lastClose, 2, locale)}`}
          lines={[`${capFirst(monthChangePct >= 0 ? t("dash.up") : t("dash.down"))} ${fnum(Math.abs(monthChangePct), 1, locale)}%`]}
          linesClass={monthChangePct >= 0 ? "font-bold text-up" : "font-bold text-down"}
        />
        <StatTile
          icon={<BarChart3 size={14} />}
          label={t("dash.volumeTitle")}
          value={vol.toLocaleString(loc)}
          unit={t("dash.contracts")}
          // Ngày phiên đã có ở đầu khung biểu đồ ("Đóng cửa phiên …") — không lặp ở đây.
          lines={[
            avgVol > 0 ? capFirst(t("dash.volTileSub", {
              dir: volChangePct >= 0 ? t("dash.higher") : t("dash.lower"),
              n: priorVols.length,
              avg: Math.round(avgVol).toLocaleString(loc),
              pct: `${sign(volChangePct)}${fnum(volChangePct, 1, locale)}`,
            })) : null,
          ]}
          className="@2xl/report:col-span-2 @3xl/report:col-span-1 print:@xl/report:@max-3xl/report:col-span-2"
        />
      </div>
    </div>
  );
}

// Ô số liệu dưới biểu đồ EUA — nhãn, số chính, rồi các dòng diễn giải (cùng cỡ chữ để các ô thẳng hàng).
// Gọn: mỗi dòng đúng 1 hàng — quá hẹp thì cắt "…" (rê chuột xem đủ qua title), không xuống dòng; khi in thì xuống dòng
// bình thường (giấy không rê chuột được).
function StatTile({ icon, label, value, unit, lines, valueClass, linesClass, className }: {
  icon: React.ReactNode; label: string; value: string; unit?: string; lines: (string | null)[]; valueClass?: string;
  linesClass?: string; className?: string;
}) {
  return (
    <div className={clsx("flex min-w-0 flex-col justify-center rounded-xl border border-border bg-background px-3 py-2", className)}>
      <div className="flex min-w-0 items-center gap-1.5 text-[12px] font-bold text-primary-dark">
        <span className="shrink-0">{icon}</span><span className="truncate print:whitespace-normal" title={label}>{label}</span>
      </div>
      <div className={clsx("mt-0.5 truncate text-[17px] print:whitespace-normal font-bold leading-tight tabular-nums", valueClass ?? "text-label")} title={value}>
        {value}{unit && <span className="ml-1 text-[12px] font-semibold text-muted-light">{unit}</span>}
      </div>
      {lines.filter((l): l is string => !!l).map((l, i) => (
        <div key={i} className={clsx("mt-0.5 truncate text-[12px] print:whitespace-normal leading-snug tabular-nums", linesClass ?? "text-body")} title={l}>{l}</div>
      ))}
    </div>
  );
}

// 1 gợi ý kinh doanh của báo cáo trước vừa tới tình huống kích hoạt (content["biz"].reminders — services/biz_memory.py).
interface BizReminder {
  id?: number; suggested_date: string; action: string; trigger: string;
  outcome?: string; evidence?: string; source_name?: string; source_url?: string;
}

// ═══════════════════════════════════════════════════════════════════════════
// BỐ CỤC TRANG BÁO CÁO — đọc theo tầng: Tổng quan (giá EUA, nhận định & việc cần làm, số liệu chính, tóm tắt điều
// hành) → Phần 1 thị trường → Phần 2 khuyến nghị & phân tích → Phần 3 tin tức → review dự báo → nguồn. Bố cục nhiều
// cột theo BỀ RỘNG THẬT của báo cáo (container query "report", không theo viewport — màn admin có sidebar vẫn đúng);
// khi in PDF khổ A4 (~650px) mọi khối xếp 1 cột như trước.
// ═══════════════════════════════════════════════════════════════════════════

// Thanh mục lục dính ngay dưới header của app (đo chiều cao header thật) — tô sáng phần đang đọc khi cuộn, bấm để
// nhảy tới phần đó. Bên phải (báo cáo rộng): số liệu chính chạy dọc (PriceTicker), luôn thấy khi đọc phần chi tiết.
// Thay cho menu hamburger + thanh chạy giá cũ (đủ 10 mã nằm ở Bảng giá nhanh). Ẩn khi in.
function SectionNav({ tabs, quote }: { tabs: { id: string; label: string }[]; quote?: React.ReactNode }) {
  const t = useTranslations("reportDoc");
  const [active, setActive] = useState(tabs[0]?.id);
  const [top, setTop] = useState(0);
  const navRef = useRef<HTMLElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const ids = tabs.map(x => x.id).join("|");

  useEffect(() => {
    const header = document.querySelector<HTMLElement>("header.sticky");
    if (!header) return;
    const measure = () => setTop(header.offsetHeight);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(header);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const els = ids.split("|").map(id => document.getElementById(id)).filter((el): el is HTMLElement => !!el);
    if (!els.length) return;
    let frame = 0;
    const update = () => {
      frame = 0;
      const line = top + (navRef.current?.offsetHeight ?? 48) + 24;
      let cur = els[0].id;
      for (const el of els) if (el.getBoundingClientRect().top <= line) cur = el.id;
      // Cuộn chạm đáy trang: phần cuối là phần đang đọc dù tiêu đề chưa qua vạch.
      if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4) cur = els[els.length - 1].id;
      setActive(cur);
    };
    const onScroll = () => { if (!frame) frame = requestAnimationFrame(update); };
    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      if (frame) cancelAnimationFrame(frame);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, [ids, top]);

  // Màn hẹp: giữ tab đang chọn luôn nằm trong vùng nhìn thấy của dải tab cuộn ngang.
  useEffect(() => {
    const list = listRef.current;
    const btn = list?.querySelector<HTMLElement>(`[data-target="${active}"]`);
    if (!list || !btn) return;
    if (btn.offsetLeft < list.scrollLeft || btn.offsetLeft + btn.offsetWidth > list.scrollLeft + list.clientWidth) {
      list.scrollTo({ left: Math.max(0, btn.offsetLeft - 16), behavior: "smooth" });
    }
  }, [active]);

  const go = (id: string) => {
    const el = document.getElementById(id);
    if (!el) return;
    const offset = top + (navRef.current?.offsetHeight ?? 48) + 12;
    window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - offset, behavior: "smooth" });
  };

  return (
    <nav
      ref={navRef}
      aria-label={t("tabs.label")}
      className="sticky z-30 border-b border-border bg-background/95 backdrop-blur-sm print:hidden"
      style={{ top }}
    >
      <div className="flex items-stretch gap-3 px-1.5 sm:px-6">
        <div ref={listRef} className="flex min-w-0 flex-1 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {tabs.map(tab => {
            const on = tab.id === active;
            return (
              <button
                key={tab.id}
                type="button"
                data-target={tab.id}
                onClick={() => go(tab.id)}
                aria-current={on ? "location" : undefined}
                // Báo cáo rộng (≥ 768px): các tab giãn ra lấp đầy thanh (không để trống giữa tab và ô giá EUA);
                // hẹp hơn: giữ bề rộng tự nhiên + cuộn ngang.
                className={clsx(
                  "relative shrink-0 whitespace-nowrap px-2 sm:px-2.5 py-2.5 text-center text-[13px] font-semibold transition-colors @3xl/report:flex-auto",
                  on ? "text-primary-dark" : "text-muted-light hover:text-label",
                )}
              >
                {tab.label}
                <span className={clsx("absolute inset-x-2 bottom-0 h-[3px] rounded-t-full", on ? "bg-primary" : "bg-transparent")} aria-hidden="true" />
              </button>
            );
          })}
        </div>
        {quote && <div className="hidden w-[460px] max-w-[45%] shrink-0 items-center border-l border-border pl-4 @5xl/report:flex">{quote}</div>}
      </div>
    </nav>
  );
}

// Δ ngày / Δ tuần của 1 mã: chỉ số — chuỗi gốc của Bảng giá nhanh ("+0,30% (+0,26)"), xanh tăng / đỏ giảm.
function DeltaCell({ text, label }: { text?: string; label?: string }) {
  const locale = useLocale();
  if (!text || text === "-") return <span className="text-muted-light">—</span>;
  return (
    <div className={clsx("text-[13.5px] font-bold tabular-nums whitespace-nowrap", isPositiveDelta(text) ? "text-up" : "text-down")}>
      {label && <span className="mr-1 text-[12px] font-semibold text-muted-light">{label}</span>}
      {formatDelta(text, locale)}
    </div>
  );
}

// Ô tên hợp đồng: tên (link nguồn giá) + dấu "!" sát ngay bên phải khi mã có lưu ý (phiên không giao dịch, điều chỉnh
// hồi tố, giá chốt CBAM) — BẤM mới hiện nội dung dưới tên, bấm lần nữa để ẩn; bản in luôn hiện nội dung.
function ContractName({ row, notes }: { row: any; notes: string[] }) {
  const t = useTranslations("reportDoc");
  const [open, setOpen] = useState(false);
  return (
    <>
      <div className="flex items-start gap-1.5">
        {row.source_url ? (
          // Link kiểu quen thuộc (xanh dương + gạch chân + icon ↗) để nhận ra được là bấm được (cả trên mobile).
          <a
            href={row.source_url}
            target="_blank"
            rel="noopener noreferrer"
            title={t("body.viewPriceAt", { name: row.name })}
            className="min-w-0 text-blue-700 underline decoration-blue-700/30 underline-offset-2 hover:text-blue-900 hover:decoration-blue-900 transition-colors"
          >
            {row.name}
            <ExternalLink size={11} className="ml-1 -mt-0.5 inline-block align-middle" aria-hidden="true" />
          </a>
        ) : (
          <span className="min-w-0">{row.name}</span>
        )}
        {notes.length > 0 && (
          <button
            type="button"
            onClick={() => setOpen(v => !v)}
            aria-expanded={open}
            aria-label={t("market.note")}
            className={clsx(
              "mt-[2px] grid h-[18px] w-[18px] shrink-0 place-items-center rounded-full bg-warn text-[11.5px] font-extrabold leading-none text-white transition-opacity hover:opacity-80 print:hidden",
              open && "ring-2 ring-black/15",
            )}
          >
            !
          </button>
        )}
      </div>
      {notes.length > 0 && (
        <div className={clsx("mt-1 text-[12.5px] font-normal leading-[1.45] text-body", open ? "block" : "hidden print:block")}>
          {notes.map((n, i) => <div key={i}>{n}</div>)}
        </div>
      )}
    </>
  );
}

// Bảng giá nhanh: mỗi mã 1 dòng theo đúng thứ tự bảng gốc (không chia nhóm) — dòng kẻ sọc xen kẽ + vạch ngăn rõ để mắt
// không nhảy dòng; Δ chỉ hiện số (không vẽ thanh). Ghi chú chất lượng dữ liệu + giá chốt CBAM nằm ở
// sau dấu "!" cạnh tên hợp đồng (bấm mới hiện).
function MarketBoard({ rows }: { rows: any[] }) {
  const t = useTranslations("reportDoc");
  const locale = useLocale();
  const th = "px-3 py-2.5 text-left text-[12px] font-bold uppercase tracking-wider text-primary-dark";

  return (
    <div className="@container overflow-x-auto rounded-xl border border-border">
      <table className="w-full border-collapse text-[14px]">
        <thead>
          <tr className="border-b-2 border-primary/25 bg-tint">
            <th className={clsx(th, "w-[34%] @xl:w-[30%]")}>{t("body.contract")}</th>
            <th className={clsx(th, "text-center!")}>{t("body.price")}</th>
            <th className={clsx(th, "hidden w-[27%] text-center! @xl:table-cell")}>{t("market.dDay")}</th>
            <th className={clsx(th, "hidden w-[27%] text-center! @xl:table-cell")}>{t("market.dWeek")}</th>
            <th className={clsx(th, "text-center! @xl:hidden")}>{t("body.change")}</th>
          </tr>
        </thead>
        {rows.map((r: any, idx: number) => {
          const [rawPriceNumber, ...priceUnitParts] = String(r.price || "").split(" ");
          const priceUnit = subCO2(priceUnitParts.join(" "));
          const cbamNote = r.code === "CBAM" && r.note
            // Báo cáo cũ có thể đã nối thêm ghi chú LLM phía sau — chỉ lấy phần "Giá chốt…"
            ? String(r.note).match(/^Giá chốt theo quý, tại ngày .*?\d{4}(?:, ngày chốt giá tiếp theo .*?\d{4})?|^Quarterly (?:settlement|closing|fixed) price.*?\d{4}(?:, next .*?\d{4})?/i)?.[0] ?? r.note
            : null;
          const notes = [r.no_trade && t("body.noTrade"), r.revision, cbamNote].filter(Boolean) as string[];
          // Mỗi mã 1 <tbody> (nền sọc + vạch ngăn); lưu ý của mã nằm sau dấu "!" cạnh tên (ContractName).
          return (
            <tbody
              key={idx}
              className={clsx(
                "border-b border-border-soft last:border-b-0 transition-colors hover:bg-tint/60 print:break-inside-avoid",
                r.code === "EUA" ? "bg-tint/70" : idx % 2 === 1 && "bg-surface",
              )}
            >
              <tr className="align-middle">
                <td className="px-3 py-3 text-[15px] font-semibold leading-snug text-label">
                  <ContractName row={r} notes={notes} />
                </td>
                <td className="px-3 py-3 text-center whitespace-nowrap">
                  <div className="text-[16px] font-bold leading-tight tabular-nums text-label">{formatCompactPriceNumber(rawPriceNumber, locale)}</div>
                  {priceUnit && <div className="text-[12px] text-muted-light">{priceUnit}</div>}
                </td>
                <td className="hidden px-3 py-3 text-center @xl:table-cell"><DeltaCell text={r.dday} /></td>
                <td className="hidden px-3 py-3 text-center @xl:table-cell"><DeltaCell text={r.dweek} /></td>
                <td className="px-3 py-3 text-center @xl:hidden">
                  {r.dday === "-" && r.dweek === "-" ? <span className="text-muted-light">—</span> : (
                    <div className="space-y-1.5">
                      <DeltaCell text={r.dday} label={t("market.dayLabel")} />
                      <DeltaCell text={r.dweek} label={t("market.weekLabel")} />
                    </div>
                  )}
                </td>
              </tr>
            </tbody>
          );
        })}
      </table>
    </div>
  );
}

// Nhãn tác động RÒNG lên EUA của từng tin "Diễn biến chính" (content["2"].key_developments[].impact).
const KEYDEV_IMPACT: Record<string, { key: "up" | "down" | "neutral"; chip: string; bar: string }> = {
  "tăng": { key: "up", chip: "text-up border-up/30 bg-up/10", bar: "bg-up" },
  "giảm": { key: "down", chip: "text-down border-down/30 bg-down/10", bar: "bg-down" },
  "trung lập": { key: "neutral", chip: "text-muted-light border-border bg-surface", bar: "bg-border-soft" },
};

// Diễn biến chính — tin tức nổi bật tác động cung/cầu EUA, mỗi tin: vạch màu + nhãn chiều tác động, tiêu đề, tóm tắt và
// nguồn. Mục 4 cũ (VCM, CBAM…) gộp cuối danh sách, không có nhãn chiều.
function KeyDevList({ items }: { items: any[] }) {
  const t = useTranslations("reportDoc");
  return (
    <ol className="list-none space-y-3">
      {items.map((d: any, i: number) => {
        const meta = KEYDEV_IMPACT[d.impact ?? ""];
        return (
          <li key={i} className="grid grid-cols-[3px_minmax(0,1fr)] gap-3 print:break-inside-avoid">
            <span className={clsx("rounded-full", meta?.bar ?? "bg-border-soft")} aria-hidden="true" />
            <div className="min-w-0 py-0.5">
              {(meta || d.title) && (
                <div className="flex flex-wrap items-start gap-x-2 gap-y-1">
                  {meta && (
                    <span className={clsx("mt-0.5 shrink-0 rounded border px-1.5 py-px text-[11.5px] font-bold", meta.chip)}>
                      {t(`keyDev.impact.${meta.key}`)}
                    </span>
                  )}
                  {d.title && <h4 className="min-w-0 flex-1 text-[15px] font-bold leading-snug text-label">{d.title}</h4>}
                </div>
              )}
              <p className="text-left mt-1 text-[15px] leading-[1.6] text-foreground">
                <RichText text={d.text} nums />
                {d.source_name && (
                  <>
                    {" "}
                    <span className="inline-block text-[12.5px] text-muted-light">
                      (
                      {d.source_url ? (
                        <a href={d.source_url} target="_blank" rel="noopener noreferrer" className="text-blue-700 underline decoration-blue-700/40 underline-offset-2 hover:text-blue-900">
                          {d.source_name}
                        </a>
                      ) : d.source_name}
                      {d.source_date ? `, ${d.source_date}` : ""})
                    </span>
                  </>
                )}
              </p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

// Lịch sự kiện 7 ngày tới — tách rõ 2 nhóm: sự kiện ĐÃ có "outcome" (kết quả) và sự kiện CHƯA diễn ra (sắp xếp theo
// ngày tăng dần). Bỏ hẳn outcome placeholder "chưa xác nhận" (xem UNCONFIRMED_OUTCOME_TEXT).
function EventsPanel({ section }: { section: any }) {
  const t = useTranslations("reportDoc");
  if (!(section?.events?.length > 0)) {
    return (
      <div className="space-y-1.5">
        {(section?.bullets ?? []).map((b: string, i: number) => (
          <p key={i} className="text-left text-[15px] leading-[1.55] text-body"><RichText text={b} nums /></p>
        ))}
      </div>
    );
  }
  const events: any[] = section.events.filter((ev: any) => ev.outcome !== UNCONFIRMED_OUTCOME_TEXT);
  const results = events.filter(ev => ev.outcome);
  const upcoming = events.filter(ev => !ev.outcome).slice().sort((a, b) => (a.date || "").localeCompare(b.date || ""));
  return (
    <div className="space-y-5">
      {results.length > 0 && (
        <div>
          <h4 className="mb-2 text-[12px] font-bold uppercase tracking-[0.1em] text-label">{t("events.results")}</h4>
          <EventTimeline events={results} />
        </div>
      )}
      {upcoming.length > 0 && (
        <div>
          <h4 className="mb-2 text-[12px] font-bold uppercase tracking-[0.1em] text-label">{t("events.upcoming")}</h4>
          <EventTimeline events={upcoming} />
        </div>
      )}
    </div>
  );
}

// Động lực thị trường: thanh cán cân (số yếu tố hỗ trợ / gây áp lực) + 2 cột song song tăng | giảm.
function DriversPanel({ drivers }: { drivers: any }) {
  const t = useTranslations("reportDoc");
  const bull: any[] = drivers?.bullish ?? [];
  const bear: any[] = drivers?.bearish ?? [];
  const tagCls = (tag: string) => clsx(
    "mr-1.5 inline-block rounded px-1.5 py-0.5 align-middle text-[11px] font-bold uppercase tracking-wider whitespace-nowrap",
    tag === "FACT" ? "bg-up/10 text-up border border-up/30" : tag === "CLAIM" ? "bg-amber-50 text-amber-700 border border-amber-200" : "bg-blue-50 text-blue-700 border border-blue-200",
  );
  const column = (items: any[], tone: "up" | "down") => (
    <div className={clsx("overflow-hidden rounded-xl border", tone === "up" ? "border-up/25 bg-up/[0.035]" : "border-down/25 bg-down/[0.035]")}>
      <div className={clsx(
        "flex items-center gap-1.5 border-b px-3.5 py-2 text-[12px] font-bold uppercase tracking-wider print:break-after-avoid",
        tone === "up" ? "border-up/20 bg-up/10 text-up" : "border-down/20 bg-down/10 text-down",
      )}>
        {tone === "up" ? <TrendingUp size={14} strokeWidth={2.5} /> : <TrendingDown size={14} strokeWidth={2.5} />}
        {t(tone === "up" ? "body.bullish" : "body.bearish")}
        <span className="ml-auto tabular-nums">{items.length}</span>
      </div>
      <ul className="list-none space-y-3 p-3.5">
        {items.length > 0 ? items.map((d: any, i: number) => (
          <li key={i} className="text-left text-[14.5px] leading-[1.55] text-body print:break-inside-avoid">
            {d.tag && <span className={tagCls(d.tag)}>{d.tag}</span>}
            <RichText text={d.text} nums />
            {d.source_url && <InlineSource name={d.source_name} url={d.source_url} />}
          </li>
        )) : (
          <li className="text-[14.5px] italic text-muted-light">{t(tone === "up" ? "body.noBullish" : "body.noBearish")}</li>
        )}
      </ul>
    </div>
  );
  return (
    <div>
      <SubHeading aside={<span className="text-[12.5px] text-muted-light">{t("drivers.balance", { up: bull.length, down: bear.length })}</span>}>
        {t("body.drivers")}
      </SubHeading>
      {bull.length + bear.length > 0 && (
        <div className="mb-3 flex h-2 gap-0.5 overflow-hidden rounded-full print:break-after-avoid [print-color-adjust:exact] [-webkit-print-color-adjust:exact]" aria-hidden="true">
          {bull.length > 0 && <span className="bg-up" style={{ flexGrow: bull.length }} />}
          {bear.length > 0 && <span className="bg-down" style={{ flexGrow: bear.length }} />}
        </div>
      )}
      <div className="grid gap-3 @3xl/report:grid-cols-2 print:block print:space-y-3">
        {column(bull, "up")}
        {column(bear, "down")}
      </div>
    </div>
  );
}

// Cần theo dõi — mỗi điểm 1 thẻ đánh số (nguyên văn backend "1. …", "2. …"). Cụm đầu (tới dấu ":"/"." đầu tiên) là
// ĐIỀU cần theo dõi → in đậm, phần sau là các nhánh "Nếu … →" (chỉ bọc **, không đổi chữ). Báo cáo rộng: lưới 2 cột,
// thẻ cùng hàng cao bằng nhau, thẻ lẻ cuối trải hết hàng.
function WatchList({ text }: { text: string }) {
  const lines = text.split("\n").map(l => l.trim()).filter(Boolean);
  return (
    <ol className="list-none grid gap-3 @3xl/report:grid-cols-2 print:block print:space-y-3">
      {lines.map((line, i) => {
        const m = line.match(/^(\d+)[.)]\s+/);
        const body = m ? line.slice(m[0].length) : line;
        const lead = body.includes("**") ? null : body.match(/^(.{8,180}?[.:])\s/u);
        const balanced = !!lead && (lead[1].match(/\(/g)?.length ?? 0) === (lead[1].match(/\)/g)?.length ?? 0);
        return (
          <li
            key={i}
            className={clsx(
              "flex gap-3 rounded-xl border border-border bg-background p-3.5 print:break-inside-avoid",
              lines.length % 2 === 1 && i === lines.length - 1 && "@3xl/report:col-span-2",
            )}
          >
            {m && (
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary text-[12.5px] font-bold text-white">{m[1]}</span>
            )}
            <div className="min-w-0 flex-1">
              <ConclusionAware
                text={lead && balanced ? `**${lead[1]}** ${body.slice(lead[0].length)}` : body}
                className="text-left text-[14.5px] leading-[1.6] text-body"
                nums
              />
            </div>
          </li>
        );
      })}
    </ol>
  );
}

// 1 dòng của Mục 3 "Phân tích": gạch đầu dòng thô "- …" (backend đôi khi tự chèn, có thể kèm en-dash "–") đổi thành chấm
// tròn — chỉ bỏ đúng phần tiền tố gạch đầu dòng, không đổi chữ.
function AnalysisLine({ text }: { text: string }) {
  const trimmed = text.trim();
  const dashMatch = trimmed.match(/^[-–—]\s+/);
  if (!dashMatch) {
    return <ConclusionAware text={text} className="text-left text-[15px] leading-[1.6] text-body" nums />;
  }
  return (
    <div className="flex gap-2.5">
      <span className="mt-[10px] h-1.5 w-1.5 shrink-0 rounded-full bg-primary" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <ConclusionAware text={trimmed.slice(dashMatch[0].length)} className="text-left text-[15px] leading-[1.6] text-body" nums />
      </div>
    </div>
  );
}

// Nội dung Mục 3 "Phân tích": block "Phân tích" chia theo nhóm — dòng đầu mỗi nhóm "Tên nhóm: <kết luận>"
// (_prompt_section3::ĐỊNH DẠNG). KHÔNG chia khối/cột (yêu cầu 26/10/2026): mỗi nhóm = nhãn nhóm + kết luận đậm rồi các ý
// ngay bên dưới, tất cả trải hết bề rộng nội dung, các nhóm nối tiếp nhau. Dòng đứng trước nhãn nhóm đầu tiên và block khác
// (không có nhãn nhóm) giữ dạng đoạn.
function AnalysisBlocks({ blocks }: { blocks: any[] }) {
  const enumLabel = useEnumLabel();
  return (
    <>
      {blocks.map((block: any, i: number) => {
        const lines: string[] = (block.content || "").split("\n").filter((line: string) => line.trim());
        const lead: string[] = [];
        const groups: { prefix: string; headline: string; lines: string[] }[] = [];
        for (const line of lines) {
          const prefix = block.heading === "Phân tích" ? GROUP_HEADLINE_PREFIXES.find((p) => line.trim().startsWith(p)) : undefined;
          if (prefix) groups.push({ prefix, headline: line.trim().slice(prefix.length).trim(), lines: [] });
          else if (groups.length > 0) groups[groups.length - 1].lines.push(line);
          else lead.push(line);
        }
        return (
          <div key={i} className="mb-5 last:mb-0">
            {/* Block "Phân tích" trùng với SubHeading "Phân tích" ngay phía trên → ẩn nhãn */}
            {block.heading !== "Phân tích" && (
              <h4 className="mb-2 text-[12.5px] font-bold uppercase tracking-[0.12em] text-primary">{enumLabel(block.heading)}</h4>
            )}
            {lead.length > 0 && (
              <div className={clsx("space-y-2", groups.length > 0 && "mb-4")}>
                {lead.map((line, j) => <AnalysisLine key={j} text={line} />)}
              </div>
            )}
            {groups.length > 0 && (
              <div className="space-y-5">
                {groups.map((g, gi) => (
                  <section key={gi} className="min-w-0 space-y-2">
                    {/* Khi in: nhãn nhóm + kết luận không tách nhau, không trơ cuối trang tách khỏi các ý bên dưới. */}
                    <div className="min-w-0 print:break-inside-avoid print:break-after-avoid">
                      <div className="text-[12px] font-bold uppercase tracking-[0.1em] text-primary">{g.prefix.replace(/:\s*$/, "")}</div>
                      <p className="text-left mt-0.5 text-[15.5px] font-bold leading-[1.45] text-primary-dark">
                        <RichText text={g.headline} nums />
                      </p>
                    </div>
                    {g.lines.length > 0 && (
                      <div className="min-w-0 space-y-2">
                        {g.lines.map((line, j) => <AnalysisLine key={j} text={line} />)}
                      </div>
                    )}
                  </section>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}

// Phần 3 — tin chi tiết theo NƠI ĐĂNG: Quốc tế rồi Việt Nam (2 nhóm nối tiếp — số tin 2 nhóm thường lệch nhau nên không
// đặt 2 cột cạnh nhau). Mỗi nhóm: danh sách DỌC trải hết bề rộng trang, các tin ngăn bằng vạch kẻ (không chia ô/thẻ).
// `heading` (tiêu đề Phần 3) + tên nhóm + tin đầu tiên nằm chung 1 khối không tách khi in — Chrome không giữ được
// "break-after: avoid" qua nhiều tầng lồng nhau, tiêu đề hay bị bỏ lại 1 mình cuối trang.
function NewsColumns({ section, heading }: { section: any; heading?: React.ReactNode }) {
  const t = useTranslations("reportDoc");
  const regions = [
    { key: "international", label: t("body.intl"), empty: t("body.noNewsIntl") },
    { key: "vietnam", label: t("body.vn"), empty: t("body.noNewsVn") },
  ];
  return (
    <div className="space-y-8">
      {regions.map(({ key, label, empty }, ri) => {
        const items: any[] = section?.[key] ?? [];
        // Có tin Việt Nam trong kỳ nhưng không bài nào đủ liên quan → nói đúng như vậy, không ghi "không có tin".
        const vnConsidered = key === "vietnam" ? section?.vietnam_considered : 0;
        const emptyText = vnConsidered > 0 ? t("body.noNewsVnFiltered", { n: vnConsidered }) : empty;
        const item = (art: any, i: number) => (
          // Khi in mỗi tin giữ nguyên khối (tiêu đề không tách khỏi tóm tắt).
          <li
            key={i}
            className={clsx(
              "grid grid-cols-[1.75rem_minmax(0,1fr)] gap-x-2.5 py-4 print:break-inside-avoid",
              i === 0 && "pt-1",
              i < items.length - 1 && "border-b border-border",
            )}
          >
            <span className="flex h-6 w-6 items-center justify-center rounded-full bg-primary/10 text-[12.5px] font-bold tabular-nums text-primary [print-color-adjust:exact] [-webkit-print-color-adjust:exact]">{i + 1}</span>
            <div className="min-w-0">
              {/* Tag chủ đề nằm NGAY CẠNH tiêu đề (cùng dòng chữ), chỉ xuống dòng khi hết chỗ. */}
              <div className="leading-snug">
                <h4 className="inline text-[15px] font-bold text-label">{art.title}</h4>
                {art.topics?.map((topic: string, ti: number) => (
                  <span key={ti} className={clsx("ml-1.5 inline-block rounded-[4px] border px-1.5 py-px align-[2px] text-[11px] font-bold uppercase leading-normal tracking-wide whitespace-nowrap", tagColorClass(topic))}>
                    {topic}
                  </span>
                ))}
              </div>
              {/* Mọi tin đều có tóm tắt (bản chấm 17/10: chỉ còn tiêu đề thì Phần 3 mất giá trị). */}
              <p className="text-left mt-1.5 text-[14.5px] leading-[1.6] text-body">
                <RichText text={art.summary} nums />{" "}
                <a href={art.url} target="_blank" rel="noopener noreferrer" className="inline-block text-[12.5px] font-semibold text-primary hover:underline">
                  {t("body.source")} {art.source} <ExternalLink size={10} className="-mt-0.5 inline" aria-hidden="true" />
                </a>
              </p>
            </div>
          </li>
        );
        return (
          <div key={key} className="min-w-0">
            <div data-print-whole className="print:break-inside-avoid">
              {ri === 0 && heading}
              <div className="report-heading mb-3 flex items-baseline justify-between gap-3 border-b-2 border-primary/20 pb-2 print:break-after-avoid">
                <h3 className="text-[14px] font-bold uppercase tracking-[0.1em] text-primary-dark">{label}</h3>
                {items.length > 0 && <span className="text-[12.5px] text-muted-light">{t("news.count", { n: items.length })}</span>}
              </div>
              {items.length > 0 ? (
                <ol className="list-none">{item(items[0], 0)}</ol>
              ) : (
                <p className="text-[14.5px] italic text-muted-light">{emptyText}</p>
              )}
            </div>
            {items.length > 1 && <ol className="list-none" start={2}>{items.slice(1).map((art, k) => item(art, k + 1))}</ol>}
          </div>
        );
      })}
    </div>
  );
}

/**
 * Toàn bộ nội dung "tờ báo cáo" (masthead → footer) — dùng chung cho cả màn
 * hình user (chỉ xem báo cáo đã published) và màn hình admin duyệt báo cáo,
 * để không lặp lại JSX giữa 2 nơi.
 */
export function ReportDocument({
  report,
  onDismissBizSuggestion,
  pdfExport = false,
}: {
  report: Report;
  // true trên trang xuất PDF (/reports/[date]/export) — trang đó tự dàn ngắt trang theo khổ người dùng chọn.
  pdfExport?: boolean;
  // Chỉ màn hình admin truyền vào — hiện nút gỡ gợi ý kinh doanh (services/biz_memory.py).
  onDismissBizSuggestion?: (id: number) => void;
}) {
  const t = useTranslations("reportDoc");
  const locale = useLocale();
  const enumLabel = useEnumLabel();
  // Khi in: khối lớn được phép ngắt bên trong thay vì bị đẩy nguyên sang trang sau (xem usePrintBreakTuning).
  const shellRef = useRef<HTMLDivElement>(null);
  usePrintBreakTuning(shellRef, !pdfExport);
  const content = report?.content ?? {};
  const priceRows: any[] = content["2"]?.prices || [];
  // Báo cáo Chủ Nhật/Thứ Hai: ngày dữ liệu cuối tuần, không có phiên giao dịch — backend
  // (report_generator.py::is_no_price_report) chỉ phân tích tin tức; ẩn mọi phần giá.
  const noPriceData = !!content["2"]?.no_price_data;
  const keyDevelopmentItems: any[] = [
    ...(content["2"]?.key_developments ?? []),
    // Mục 4 cũ "Cập nhật tín chỉ carbon & CBAM" (VCM, thép xanh, CBAM) được gộp vào cuối danh sách này; bỏ dòng
    // "không có diễn biến" vì không phải tin.
    ...((content["4"]?.bullets ?? [])
      .map((b: any) => (typeof b === "string" ? { text: b } : b))
      .filter((b: any) => b?.text && !/không có diễn biến|\bno (?:significant |notable |major |new )?developments?\b/i.test(b.text))),
  ];
  // Mục 3 mặc định hiện đủ phân tích; ẩn được để user chỉ cần xem nhanh kịch bản, không cần đọc hết phần phân tích.
  const [showAnalysis, setShowAnalysis] = useState(true);
  // Khung "Jenny cập nhật đề xuất trước đây" thu vào nút "i" — bấm mới mở (in PDF luôn hiện nội dung, ẩn nút).
  const [showReminders, setShowReminders] = useState(false);
  // Mục "Review dự báo và khuyến nghị": bảng chấm dự báo + gợi ý kinh doanh cũ vừa tới tình huống kích hoạt.
  const forecastReview: ForecastReview | undefined = content["2"]?.forecast_review;
  const hasForecastReview = (forecastReview?.items?.length ?? 0) > 0;
  const bizReminders: BizReminder[] = content["biz"]?.reminders ?? [];
  const remindersContradicted = bizReminders.filter(r => r.outcome === "contradicted").length;
  const remindersHappened = bizReminders.length - remindersContradicted;

  // Rút dòng "**Tổng hợp:**" (kết luận chung giá EUA) ra khỏi các block phân tích để đưa lên khối mở đầu, và
  // block "Cần theo dõi" thành khối riêng cạnh phần phân tích — chỉ đổi VỊ TRÍ, không đổi nội dung.
  const { cleanedBlocks: blocksAfterSummary, summary: euaSummary } = extractEuaSummary(content["3"]?.analysis_blocks);
  const euaVerdict = euaSummary ? parseEuaSentiment(euaSummary) : null;
  const { cleanedBlocks: analysisBlocks, watchpoints } = extractWatchpoints(blocksAfterSummary);
  const hasMarketDrivers =
    content["2"]?.market_drivers?.bullish?.length > 0 || content["2"]?.market_drivers?.bearish?.length > 0;

  // Kịch bản 3 khung thời gian (trading_scenarios Mục 3) + OHLC 30 phiên (chart_data) — dùng chung khối mở đầu, thang
  // giá và thẻ kịch bản. Giá hiện tại = giá đóng cửa phiên gần nhất trong chart_data.
  const tradingScenarios: Scenario[] = content["3"]?.trading_scenarios ?? [];
  const chartData: OhlcRow[] = content["2"]?.chart_data || [];
  const euaRow = priceRows.find((r: any) => r?.code === "EUA");
  const stats = computeEuaStats(chartData, euaRow);
  // Bản tiếng Anh: mức giá/loại lệnh (thang giá, lệnh ngắn hạn, thẻ kịch bản) tách từ kịch bản TIẾNG VIỆT cùng khung
  // (`content_vi` — lib/reportLocale.ts) — câu chiến lược tiếng Anh do Claude viết tự do, parser từ khoá đọc sai; câu
  // chữ hiển thị vẫn lấy từ bản tiếng Anh (plan.sc).
  const levelScenarios: Scenario[] | undefined = report.content_vi?.["3"]?.trading_scenarios;
  const plans = PLAN_HORIZONS
    .map(h => tradingScenarios.find(sc => sc?.horizon === h))
    .filter((sc): sc is Scenario => !!sc)
    .map(sc => ({ ...buildPlan(levelScenarios?.find(v => v?.horizon === sc.horizon) ?? sc, stats.last), sc }));
  const shortPlan = plans.find(p => p.horizon === "ngắn hạn");
  const outlook = shortPlan ? outlookOf(shortPlan.sc, plans.find(p => p.horizon === "trung hạn")?.sc) : null;
  const minRr = forecastReview?.rules?.min_rr ?? DEFAULT_MIN_RR;
  const cbamEstimate: CbamEstimate | null = noPriceData ? null : content["2"]?.cbam_estimate ?? null;
  const hedgeAction: HedgeAction | null = content["3"]?.hedge_action ?? null;
  const execBullets: any[] = content["1"]?.bullets ?? [];
  const rawHighlight = content["1"]?.highlight;
  const highlight: ReportHighlight | null =
    rawHighlight?.text && HIGHLIGHT_TYPES.includes(rawHighlight.type) ? rawHighlight : null;
  // Biểu đồ chỉ số giá (bấm → popup "So sánh hiệu suất") — cần gọi API + tương tác nên không in. Có biểu đồ nến EUA
  // thì đặt cạnh biểu đồ nến (2 biểu đồ cao bằng nhau), không thì cạnh bảng giá.
  const indexPanel = !noPriceData && priceRows.length > 0 ? (
    <PriceIndexPanel
      reportDate={report.report_date}
      tableCodes={priceRows.map((r: any) => String(r.code || "")).filter(Boolean)}
      cbamEstimate={cbamEstimate}
    />
  ) : null;

  // Khối mở đầu: Tóm tắt điều hành | Nhận định tổng quan + giá EUA | cột Lệnh ngắn hạn · Phòng hộ CBAM —
  // bỏ ô thiếu dữ liệu, các ô còn lại chia cột (HERO_SPANS). Báo cáo vừa (768–1023px) và in A4 dọc (khung 576–767px): Tóm
  // tắt hết hàng, 2 ô kia cạnh nhau bên dưới; khổ hẹp hơn 1 cột — xem trang export /reports/[date]/export.
  const showSnapshot = !noPriceData && stats.last !== undefined;
  const tickerItems = useTickerItems(noPriceData ? null : cbamEstimate, noPriceData ? [] : priceRows);
  const showOrders = !!shortPlan && shortPlan.legs.length > 0;
  const showHedge = !noPriceData && (!!cbamEstimate || !!hedgeAction);
  const showTrack = !noPriceData && (plans.length > 0 || hasForecastReview);
  const heroKey = [
    execBullets.length > 0 && "s",
    (showSnapshot || !!euaVerdict || !!outlook) && "v",
    (showOrders || showHedge || showTrack) && "d",
  ].filter(Boolean).join("") as keyof typeof HERO_SPANS | "";
  const heroSpans = heroKey ? HERO_SPANS[heroKey] : null;

  const tabs = [
    { id: "section-highlights", label: t("tabs.overview") },
    { id: "section-market", label: t("tabs.market") },
    plans.length > 0 ? { id: "section-recommendation", label: t("tabs.recommendation") } : null,
    { id: "section-analysis-detail", label: t("tabs.analysis") },
    content["biz"] ? { id: "section-biz", label: t("tabs.biz") } : null,
    content["6"] ? { id: "section-news", label: t("tabs.news") } : null,
    hasForecastReview || content["3"]?.vs_previous || bizReminders.length > 0 ? { id: "section-forecast-review", label: t("tabs.review") } : null,
    content["9"] ? { id: "section-sources", label: t("tabs.sources") } : null,
  ].filter((x): x is { id: string; label: string } => !!x);

  return (
    // Màn hình: báo cáo trải hết bề ngang; bố cục nhiều cột theo bề rộng thật của báo cáo (container "report"). Khi in/
    // xuất PDF, Chrome dàn lại theo bề rộng trang A4 (~650px nội dung) nên mọi khối xếp 1 cột như cũ.
    // overflow-clip (không phải hidden): vẫn cắt góc bo của banner nhưng không tạo vùng cuộn — thanh mục lục bên trong
    // mới "dính" được khi cuộn trang.
    // Mobile (< sm): báo cáo tràn sát 2 mép màn hình (-mx-4 bù đúng px-4 của <main> trong app/layout.tsx), bỏ viền/bo
    // góc/bóng của card — lề đọc chỉ còn px-4 của nội dung.
    <div ref={shellRef} className="report-shell @container/report w-full max-sm:-mx-4 max-sm:w-auto bg-background text-foreground font-sans leading-[1.55] rounded-2xl max-sm:rounded-none border border-border max-sm:border-x-0 shadow-[var(--shadow-soft)] max-sm:shadow-none overflow-clip mb-10">

      {/* Banner Stavian — 1 hàng gọn: logo | nhãn + tiêu đề + người báo cáo | thứ + ngày phát hành. */}
      <div className="relative overflow-hidden bg-gradient-to-br from-[#1B4D3E] via-[#1a5c4a] to-[#143d33] text-white">
        {/* Hoạ tiết chéo — "slice" (không "none") để hình không bị kéo méo theo tỉ lệ khung. .report-banner-art — bỏ
            giới hạn "svg { max-height: 200px }" (dành cho biểu đồ) khi in, xem globals.css. */}
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

        <div className="relative z-10 flex flex-wrap items-center gap-x-5 gap-y-3 px-4 sm:px-8 py-4 sm:py-5">
          <img
            src="/stavian_logo.png"
            alt="Stavian Industrial Metal"
            className="h-10 sm:h-12 w-auto object-contain shrink-0"
          />
          <span className="hidden sm:block w-px self-stretch bg-white/20" aria-hidden="true" />
          <div className="min-w-0 flex-1 basis-[16rem]">
            <div className="text-[11px] sm:text-[12px] font-semibold tracking-[0.2em] uppercase text-[#8fd9a8]">
              Daily carbon market news
            </div>
            <h1 className="mt-0.5 text-[19px] sm:text-[24px] font-bold tracking-[0.2px] leading-[1.2] [text-wrap:balance]">
              {t("masthead.title")}
            </h1>
            {/* div (không phải p) — tránh rule căn đều chữ của .report-shell p khi in. */}
            <div className="mt-1 text-[12px] sm:text-[13px] leading-snug text-white/80">
              {t("masthead.reporter")}<span className="hidden sm:inline"> — Stavian Industrial Metal</span>
            </div>
          </div>
          {/* Thứ + ngày phát hành: màn rộng căn phải cuối hàng; điện thoại xuống 1 dòng riêng, căn trái. */}
          <div className="shrink-0 text-right max-sm:flex max-sm:basis-full max-sm:items-baseline max-sm:gap-2 max-sm:text-left">
            <div className="text-[11px] sm:text-[12px] font-bold uppercase tracking-[0.12em] text-[#8fd9a8]">
              {reportWeekday(report.report_date, locale)}
            </div>
            <div className="text-[14px] sm:text-[16px] font-bold leading-snug">
              {formatReportDate(report.report_date, locale, t)}
            </div>
          </div>
        </div>

        {/* Viền nhấn dưới banner */}
        <div className="absolute bottom-0 left-0 right-0 h-[3px] bg-gradient-to-r from-[#2f8749] via-[#3da85e] to-[#2f8749]" />
      </div>

      <SectionNav tabs={tabs} quote={tickerItems.length > 0 ? <PriceTicker items={tickerItems} /> : undefined} />

      <div className="px-4 sm:px-8 pt-6 pb-10">

        {/* ═══ TỔNG QUAN — giá EUA, nhận định & việc cần làm hôm nay, số liệu chính, tóm tắt điều hành ═══ */}
        <section id="section-highlights" className="report-section scroll-mt-32 space-y-4 pb-8">
          {/* Báo cáo hẹp: số liệu chính chạy dọc ở đầu trang (báo cáo rộng nằm trên thanh menu). */}
          {tickerItems.length > 0 && (
            <PriceTicker
              items={tickerItems}
              label={t("hero.keyFigures")}
              className="rounded-xl border border-border bg-surface/60 px-3.5 @5xl/report:hidden print:hidden"
            />
          )}
          {highlight && <TodayHighlight highlight={highlight} />}
          {heroSpans && (
            // In: ô không giãn theo ô cao nhất hàng (print:items-start) — Tóm tắt dài ngắt sang trang sau thì ô Nhận định
            // giãn theo sẽ cao hơn 1 trang và bị cắt đôi.
            <div className={clsx("grid gap-4 print:items-start", heroSpans.grid)}>
              {execBullets.length > 0 && <HeroSummary bullets={execBullets} className={heroSpans.s} />}
              {heroSpans.v !== undefined && (
                <HeroVerdict verdict={euaVerdict} outlook={outlook} stats={showSnapshot ? stats : { ...stats, last: undefined }} euaRow={euaRow} chartData={chartData} className={heroSpans.v} />
              )}
              {heroSpans.d !== undefined && (
                <div className={clsx("flex min-w-0 flex-col gap-3", heroSpans.d)}>
                  {showOrders && shortPlan && <HeroOrders shortPlan={shortPlan} className="flex-1" />}
                  {showHedge && <HeroHedge cbamEstimate={cbamEstimate} hedgeAction={hedgeAction} className="flex-1" />}
                  {showTrack && <HeroForecast review={forecastReview} className="flex-1" />}
                </div>
              )}
            </div>
          )}
        </section>

        {/* ═══ PHẦN 1 — DIỄN BIẾN THỊ TRƯỜNG ═══ */}
        <section id="section-market" className="report-section scroll-mt-32 pt-4 pb-8">
          {/* Có biểu đồ: khi in, tiêu đề Phần 1 được in bên trong khối biểu đồ (EuaDashboardWidget.printHeading) để
              không bị bỏ lại cuối trang 1 — bản này chỉ hiện trên màn hình. */}
          <PartHeading
            eyebrow={t("body.part", { n: 1 })} title={t("body.marketTitle")} icon={LineChart}
            className={chartData.length > 0 ? "print:hidden" : undefined}
          />
          <div className="space-y-8">
            <EuaDashboardWidget
              report={report}
              printHeading={<PartHeading eyebrow={t("body.part", { n: 1 })} title={t("body.marketTitle")} icon={LineChart} />}
            />

            {noPriceData ? (
              <div className="rounded-xl border border-border bg-tint/40 px-4 py-3 text-[15px] leading-[1.55] text-body">
                {content["2"]?.no_price_notice || t("body.noPriceFallback")}
              </div>
            ) : priceRows.length > 0 && (
              // Bảng giá nhanh hẹp (5/12, dạng gọn: 1 cột Δ ngày/tuần) | biểu đồ chỉ số giá rộng (7/12) — ngang từ khung báo
              // cáo ≥ 768px (@3xl). Khi in/PDF chỉ giữ bảng giá → bảng trải hết bề rộng (đủ cột Δ ngày + Δ tuần), không
              // bị bó trong 5/12 để trống phần còn lại.
              <div className="grid gap-5 @3xl/report:grid-cols-12">
                <div className="min-w-0 not-print:@3xl/report:col-span-5 print:col-span-full">
                  <SubHeading aside={content["2"]?.price_timestamp && (
                    <span className="text-[12px] text-muted-light">{content["2"].price_timestamp}</span>
                  )}>
                    {t("body.priceTable")}
                  </SubHeading>
                  <MarketBoard rows={priceRows} />
                </div>
                <div className="flex min-w-0 flex-col @3xl/report:col-span-7 print:hidden">{indexPanel}</div>
              </div>
            )}

            {/* Ước tính giá CBAM quý (VWAP đấu giá EEX + lịch còn lại, tín hiệu phòng vệ). Báo cáo cũ/CN-T2 → ẩn. */}
            {cbamEstimate && <div id="section-cbam-estimate" className="scroll-mt-32"><CbamEstimateCard estimate={cbamEstimate} /></div>}

            {/* Diễn biến chính (tin tức tác động cung/cầu EUA — content["2"].key_developments) | lịch sự kiện 7 ngày
                tới (content["8"]): việc đã xảy ra cạnh việc sắp tới. */}
            {(keyDevelopmentItems.length > 0 || content["8"]) && (
              <div className="grid gap-5 @5xl/report:grid-cols-12 print:block print:space-y-8">
                {keyDevelopmentItems.length > 0 && (
                  <div className={clsx(PANEL, content["8"] ? "@5xl/report:col-span-7 @7xl/report:col-span-8" : "@5xl/report:col-span-12")}>
                    <SubHeading>{t("body.keyDev")}</SubHeading>
                    <KeyDevList items={keyDevelopmentItems} />
                  </div>
                )}
                {content["8"] && (
                  <aside className={clsx(PANEL, "bg-surface/50", keyDevelopmentItems.length > 0 ? "@5xl/report:col-span-5 @7xl/report:col-span-4" : "@5xl/report:col-span-12")}>
                    <SubHeading>{content["8"].title}</SubHeading>
                    <EventsPanel section={content["8"]} />
                  </aside>
                )}
              </div>
            )}
          </div>
        </section>

        {/* ═══ PHẦN 2 — PHÂN TÍCH VÀ KHUYẾN NGHỊ GIAO DỊCH ═══ */}
        <section id="section-analysis" className="report-section scroll-mt-32 pt-4 pb-8">
          <PartHeading
            eyebrow={t("body.part", { n: 2 })}
            title={noPriceData ? t("body.part2NoPrice") : t("body.part2")}
            icon={BarChart3}
          />
          <div className="space-y-10">
            {/* KHUYẾN NGHỊ VỊ THẾ + KỊCH BẢN HÀNH ĐỘNG — xem PositionCard. */}
            {plans.length > 0 && (
              <PositionCard
                plans={plans}
                stats={stats}
                minRr={minRr}
                cbamEstimate={cbamEstimate}
                hedgeAction={hedgeAction}
                openPositions={content["3"]?.open_positions}
              />
            )}

            <div id="section-analysis-detail" className="scroll-mt-32 space-y-8">
              {hasMarketDrivers && <DriversPanel drivers={content["2"].market_drivers} />}

              {/* Phân tích (không chia khối, trải hết bề rộng) rồi Cần theo dõi (lưới thẻ) — mỗi hàng thẻ cao bằng nhau. */}
              {content["3"] && (
                <div>
                  <SubHeading
                    aside={(analysisBlocks?.length > 0 || content["3"].correlation_analysis) ? (
                      <button
                        type="button"
                        onClick={() => setShowAnalysis(v => !v)}
                        aria-expanded={showAnalysis}
                        className="flex items-center gap-1.5 rounded-md border border-primary/30 bg-tint/50 px-2.5 py-1 text-[12px] font-bold text-primary transition-colors hover:bg-tint"
                      >
                        {showAnalysis ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
                        {showAnalysis ? t("body.hideAnalysis") : t("body.showAnalysis")}
                      </button>
                    ) : undefined}
                  >
                    {t("body.analysis")}
                  </SubHeading>
                  {content["3"].title && (
                    <p className="text-left -mt-1.5 mb-4 text-[13px] italic text-muted-light print:break-after-avoid">{content["3"].title}</p>
                  )}

                  {showAnalysis && (
                    <>
                      <AnalysisBlocks blocks={analysisBlocks ?? []} />

                      {/* Giả định mô hình chuyển đổi nhiên liệu — để người đọc kiểm chứng giá EUA hòa vốn
                          (report_generator.fuel_model_note). Báo cáo cũ không có → ẩn. */}
                      {content["3"].fuel_model_note && (
                        <p className="text-left mt-5 rounded-lg bg-surface px-3.5 py-2.5 text-[13px] leading-[1.6] text-muted-light">
                          <span className="font-semibold text-body">{t("body.fuelModelNote")}</span>{" "}
                          <HighlightNumbers text={content["3"].fuel_model_note} />
                        </p>
                      )}

                      {content["3"].correlation_analysis && (
                        <div className="my-5 space-y-2 rounded-r-lg border-l-2 border-primary bg-tint/40 px-3 sm:px-4 py-3">
                          <h4 className="mb-1.5 text-[12.5px] font-bold uppercase tracking-widest text-primary-dark">{t("body.correlationTitle")}</h4>
                          {(content["3"].correlation_analysis.gas_comment || content["3"].correlation_analysis.gas_coal_power) && (
                            <div className="mb-1 space-y-2.5">
                              {content["3"].correlation_analysis.gas_comment && (
                                <p className="text-[15px] leading-[1.55] text-body"><b className="font-bold text-primary-dark">{t("body.gas")}</b> <RichText text={content["3"].correlation_analysis.gas_comment} nums /></p>
                              )}
                              {content["3"].correlation_analysis.coal_comment && (
                                <p className="text-[15px] leading-[1.55] text-body"><b className="font-bold text-primary-dark">{t("body.coal")}</b> <RichText text={content["3"].correlation_analysis.coal_comment} nums /></p>
                              )}
                              {content["3"].correlation_analysis.power_comment && (
                                <p className="text-[15px] leading-[1.55] text-body"><b className="font-bold text-primary-dark">{t("body.power")}</b> <RichText text={content["3"].correlation_analysis.power_comment} nums /></p>
                              )}
                            </div>
                          )}
                          <ConclusionAware
                            text={content["3"].correlation_analysis.fuel_switching_chain || content["3"].correlation_analysis.gas_coal_power}
                            className="text-[15px] leading-[1.55] text-body"
                            nums
                          />
                          <div className="rounded-md border border-primary/30 bg-tint/60 px-3 py-2">
                            <p className="text-[15px] font-bold leading-[1.5] text-primary-dark">
                              <RichText text={content["3"].correlation_analysis.eua_conclusion} nums />
                            </p>
                          </div>
                        </div>
                      )}
                    </>
                  )}
                </div>
              )}

              {/* Cần theo dõi — tách khỏi Mục 3 (điều kiện nào sẽ đổi nhận định). */}
              {watchpoints && (
                <div>
                  <SubHeading>{t("body.watch")}</SubHeading>
                  <WatchList text={watchpoints} />
                </div>
              )}

              {/* Theo dõi chính sách ETS/MSR — khối cố định (report_generator._resolve_ets_policy, tin chính sách
                  21 ngày). Báo cáo cũ không có → ẩn. */}
              {Array.isArray(content["3"]?.ets_policy) && content["3"].ets_policy.length > 0 && (
                <EtsPolicyTable items={content["3"].ets_policy} />
              )}
            </div>

            {/* Gợi ý kinh doanh & giải pháp cho SIM — Ngắn hạn rồi Dài hạn, mỗi bảng rộng hết khung (2 bảng cạnh nhau
                làm mỗi ô chỉ còn 3–5 chữ/dòng). */}
            {content["biz"] && (
              <div id="section-biz" className="scroll-mt-32">
                <SubHeading>{content["biz"].title}</SubHeading>
                <div className="space-y-6">
                  <BizRecommendationTable
                    heading={t("biz.shortTerm")}
                    accent="text-label"
                    rows={content["biz"].short_term}
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
                  <BizRecommendationTable
                    heading={t("biz.longTerm")}
                    accent="text-primary"
                    rows={content["biz"].long_term}
                    columns={[
                      { key: "opportunity", label: t("biz.colOpportunity") },
                      { key: "solution", label: t("biz.colSolution") },
                    ]}
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
          </div>
        </section>

        {/* ═══ PHẦN 3 — CHI TIẾT CÁC TIN TỨC CHÍNH ═══ */}
        {content["6"] && (
          <section id="section-news" className="report-section scroll-mt-32 pt-4 pb-8">
            <NewsColumns
              section={content["6"]}
              heading={<PartHeading eyebrow={t("body.part", { n: 3 })} title={content["6"].title || t("body.newsTitle")} icon={Newspaper} />}
            />
          </section>
        )}

        {/* ═══ REVIEW DỰ BÁO VÀ KHUYẾN NGHỊ — (1) Dự báo trước: đúng hay sai (biểu đồ + bảng chấm kịch bản ngắn hạn,
            "nguyên nhân & điều chỉnh") và (2) Jenny cập nhật đề xuất trước đây (gợi ý kinh doanh cũ vừa tới tình huống
            kích hoạt). ═══ */}
        {(hasForecastReview || content["3"]?.vs_previous || bizReminders.length > 0) && (
          <section id="section-forecast-review" className="report-section scroll-mt-32 pt-4 pb-8">
            <PartHeading title={t("body.reviewTitle")} icon={ClipboardCheck} />
            <div className="space-y-8">
              {(hasForecastReview || content["3"]?.vs_previous) && (
                <div>
                  <SubHeading>{t("body.reviewForecast")}</SubHeading>
                  {hasForecastReview && forecastReview ? (
                    <ForecastReviewCard review={forecastReview} enumLabel={enumLabel} showTitle={false}>
                      {content["3"]?.vs_previous && <RichText text={content["3"].vs_previous} nums />}
                    </ForecastReviewCard>
                  ) : (
                    <div className="rounded-xl border border-border bg-surface px-4 py-3 [print-color-adjust:exact] [-webkit-print-color-adjust:exact]">
                      <h3 className="mb-1 text-[12px] font-bold uppercase tracking-[0.15em] text-label">{t("body.vsPrevious")}</h3>
                      <p className="text-left text-[15px] leading-[1.6] text-body">
                        <RichText text={content["3"].vs_previous} nums />
                      </p>
                    </div>
                  )}
                </div>
              )}

              {bizReminders.length > 0 && (
                <div>
                  <SubHeading>{t("biz.remindersTitle")}</SubHeading>
                  {/* Jenny nhắc lại — CHỈ gợi ý ở báo cáo trước (trong 10 ngày) mà tình huống kích hoạt vừa xảy ra
                      hôm nay (services/biz_memory.py). */}
                  <div className="overflow-hidden rounded-xl border-2 border-warn/50 bg-warn-tint [print-color-adjust:exact] [-webkit-print-color-adjust:exact]">
                    <div className="flex items-center gap-2.5 px-4 py-3">
                      <img src="/jenny.jpg" alt="" className="h-8 w-8 shrink-0 rounded-full object-cover" />
                      <div className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-2.5 gap-y-0.5 text-[14.5px]">
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
                          "inline-flex shrink-0 items-center gap-1 rounded-full border px-2.5 py-1 text-[12.5px] font-bold transition-colors print:hidden",
                          showReminders ? "border-primary bg-primary text-white" : "border-primary/40 bg-background text-primary hover:bg-tint",
                        )}
                      >
                        <Info size={13} strokeWidth={2.5} aria-hidden="true" />
                        {showReminders ? t("biz.hide") : t("biz.viewDetails")}
                      </button>
                    </div>
                    <ul className={clsx("list-none divide-y divide-warn/20 border-t border-warn/30", showReminders ? "block" : "hidden print:block")}>
                      {bizReminders.map((r, i) => (
                        <li key={i} className="space-y-1.5 px-4 py-3">
                          <div className="flex items-start gap-2">
                            <p className="min-w-0 flex-1 text-[15px] leading-[1.55] text-foreground">
                              {t("biz.suggestedOn")} <b>{formatFullDate(r.suggested_date)}</b> {t("biz.suggested")} <b><RichText text={r.action} /></b>
                            </p>
                            {onDismissBizSuggestion && typeof r.id === "number" && (
                              <DismissButton onClick={() => onDismissBizSuggestion(r.id as number)} className="shrink-0" />
                            )}
                          </div>
                          <p className="text-[15px] leading-[1.55] text-body">
                            <span className="font-semibold">{t("biz.trigger")}</span> <RichText text={r.trigger} nums />
                          </p>
                          {/* Nguồn nằm liền cuối câu kết quả (cùng dòng), chỉ xuống dòng khi hết chỗ. */}
                          <p className={clsx("text-[15px] font-semibold leading-[1.55]", r.outcome === "contradicted" ? "text-down" : "text-up")}>
                            {t(r.outcome === "contradicted" ? "biz.contradicted" : "biz.happened")}
                            {r.evidence ? <>: <span className="font-normal text-foreground"><RichText text={r.evidence} nums /></span></> : "."}
                            {r.source_name && <InlineSource name={r.source_name} url={r.source_url} className="font-normal" />}
                          </p>
                          <p className="text-[15px] font-semibold italic text-warn">
                            {r.outcome === "contradicted" ? t("biz.contradictedFollow") : t("biz.askDone")}
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

        {/* ═══ NGUỒN THAM KHẢO — đứng độc lập cuối báo cáo, không thuộc Phần nào ═══ */}
        {content["9"] && (
          <section id="section-sources" className="report-section scroll-mt-32 pt-4 pb-6">
            <PartHeading title={content["9"].title || t("body.sourcesTitle")} icon={Link2} />
            {content["9"].items?.length > 0 ? (
              (() => {
                // Sắp theo ĐÚNG thứ tự Phần 3 (Quốc tế → Việt Nam, đánh số như trên); link trùng URL thì khớp theo
                // Phần 3, nguồn không nằm ở Phần 3 xếp cuối.
                const all: any[] = content["9"].items;
                const used = new Set<string>();
                const groups = [
                  { label: t("body.intl"), list: content["6"]?.international ?? [] },
                  { label: t("body.vn"), list: content["6"]?.vietnam ?? [] },
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
                  <ol className="list-none space-y-1.5 @5xl/report:columns-2 @5xl/report:gap-x-10 print:space-y-1">
                    {entries.map((it: any, i: number) => (
                      <li key={it.url ?? `kb-${it.doc_id ?? i}`} className="break-inside-avoid text-[13px] leading-[1.55] text-muted-light">
                        <span className="tabular-nums">{i + 1}.</span> [{it.source}]{" "}
                        {it.url ? (
                          <a href={it.url} target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">
                            {it.title}
                          </a>
                        ) : (
                          // Tài liệu tri thức admin tải lên (kind="knowledge") — không có link công khai.
                          <span className="text-body" title={t("body.knowledgeSource")}>{it.title}</span>
                        )}
                      </li>
                    ))}
                  </ol>
                );
              })()
            ) : (
              <p className="text-[13px] text-muted-light">{t("body.noSources")}</p>
            )}
          </section>
        )}

        <footer className="border-t border-border pt-6 text-center print:pt-2">
          <p className="text-[12px] italic text-muted-light">{t("body.footer")}</p>
        </footer>

      </div>
    </div>
  );
}
