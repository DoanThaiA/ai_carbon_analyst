"use client";

import { Fragment, useEffect, useRef, useState } from "react";
import clsx from "clsx";
import {
  Clock, CalendarRange, Compass, TrendingUp, TrendingDown, Minus, Target, AlertTriangle,
  Sparkles, LineChart, BarChart3, Newspaper, Link2,
  Crosshair, ChevronDown, ChevronUp, Info, ShieldCheck, ExternalLink, Menu, X, Trash2,
  Leaf, Database, Calendar, RefreshCw
} from "lucide-react";
import type { Report } from "@/lib/types";

// "YYYY-MM-DD" -> "dd/MM" (nhãn trục X) — tránh phụ thuộc date-fns chỉ cho 1 format đơn giản.
function formatShortDate(dateStr: string) {
  const parts = dateStr.split("-");
  return parts.length === 3 ? `${parts[2]}/${parts[1]}` : dateStr;
}

// Backend luôn trả 4 số thập phân (vd "72.4000") — rút gọn còn 2 số khi hiển thị
// trong bảng giá để chuỗi số ngắn lại, đủ nằm gọn trong cột hẹp trên mobile mà
// không cần ngắt dòng giữa chừng. Không đổi dữ liệu gốc, chỉ rút gọn phần hiển thị.
function formatCompactPriceNumber(numStr: string): string {
  const n = Number(numStr.replace(/,/g, ""));
  if (Number.isNaN(n)) return numStr;
  return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// Các mục trong menu hamburger điều hướng (id khớp với <section id> trong báo cáo).
const REPORT_SECTIONS = [
  { id: "section-highlights", label: "Tóm tắt điều hành" },
  { id: "section-market", label: "Diễn biến thị trường" },
  { id: "section-analysis", label: "Phân tích & Giao dịch" },
  { id: "section-news", label: "Tin tức chi tiết" },
  { id: "section-sources", label: "Nguồn tham khảo" },
];

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

// Nhãn xu hướng dạng mũi tên + chữ cho "Bảng tín hiệu nhanh" (Phần 2) — chỉ là
// cách gọi tên khác của cùng "direction" (tăng/giảm/đi ngang) đã có sẵn trong
// trading_scenarios, KHÔNG phải trường dữ liệu mới/suy diễn thêm.
const TREND_META: Record<string, { arrow: string; label: string; className: string }> = {
  "tăng": { arrow: "▲", label: "NGHIÊNG TĂNG", className: "text-up" },
  "giảm": { arrow: "▼", label: "NGHIÊNG GIẢM", className: "text-down" },
  "đi ngang": { arrow: "↔", label: "GIẰNG CO", className: "text-blue-700" },
};

// Nhãn tác động của từng SỰ KIỆN TIN TỨC trong "Diễn biến chính" lên cán cân
// cung–cầu EUA (mục này chỉ là tin tức, không phải diễn biến giá hợp đồng).
const IMPACT_META: Record<string, { arrow: string; label: string; badge: string; bar: string }> = {
  "tăng": { arrow: "▲", label: "Hỗ trợ EUA", badge: "text-up border-up/30 bg-up/10", bar: "border-l-up" },
  "giảm": { arrow: "▼", label: "Áp lực EUA", badge: "text-down border-down/30 bg-red-50", bar: "border-l-down" },
  "trung lập": { arrow: "●", label: "Chờ xác nhận", badge: "text-blue-700 border-blue-200 bg-blue-50", bar: "border-l-blue-400" },
};

// "Khuyến nghị vị thế" cũng suy ra trực tiếp từ "direction" có sẵn của kịch
// bản "ngắn hạn" — không phải khuyến nghị đầu tư mới do LLM tự sinh riêng.
const POSITION_META: Record<string, string> = {
  "tăng": "MUA (LONG) — theo xu hướng ngắn hạn",
  "giảm": "BÁN (SHORT) — theo xu hướng ngắn hạn",
  "đi ngang": "CHỜ — không mở mới, không đóng vị thế đang có",
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
// TÍN HIỆU HÔM NAY — thẻ chiến lược trading của phiên (kịch bản "ngắn hạn")
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
    .replace(/\d+(?:[.,]\d+)?\s*(?:%|phiên|ngày|tuần|tháng)/gi, "")
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
    const isBuy = /mua/i.test(c), isSell = /bán/i.test(c);
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
  const below = riskBody.match(/dưới[^\d]{0,15}(\d+(?:[.,]\d+)?)/i);
  const above = riskBody.match(/trên[^\d]{0,15}(\d+(?:[.,]\d+)?)/i);
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

// 84.8 → "84,80" (quy ước dấu phẩy thập phân như phần còn lại của báo cáo)
function fmtPrice(n: number): string {
  return n.toFixed(2).replace(".", ",");
}
const fmtRange = (r: number[]) => r.map(fmtPrice).join("–");
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

// Thang giá: Cắt lỗ — Vào lệnh — Mục tiêu trên 1 trục, tô đỏ vùng rủi ro / xanh
// vùng lợi nhuận, vạch "Hiện tại" là giá đóng cửa gần nhất của chart_data.
function SignalLadder({ stop, entry, target, last }: { stop: number; entry: number; target: number; last?: number }) {
  const pts = [stop, entry, target, ...(last !== undefined ? [last] : [])];
  const lo = Math.min(...pts), hi = Math.max(...pts);
  const pad = (hi - lo) * 0.08 || 1;
  const min = lo - pad, span = hi + pad - min;
  const pct = (n: number) => ((n - min) / span) * 100;
  const zone = (a: number, b: number) => ({ left: `${pct(Math.min(a, b))}%`, width: `${Math.abs(pct(a) - pct(b))}%` });
  const marks = [
    { key: "Cắt lỗ", v: stop, color: "bg-red-500", text: "text-red-600" },
    { key: "Vào lệnh", v: entry, color: "bg-primary", text: "text-primary-dark" },
    { key: "Mục tiêu", v: target, color: "bg-emerald-600", text: "text-emerald-700" },
  ];
  return (
    <div className="relative h-[66px] mt-4 select-none [print-color-adjust:exact] [-webkit-print-color-adjust:exact]" aria-hidden="true">
      <div className="absolute top-[20px] left-0 right-0 h-[8px] rounded-full bg-gray-200" />
      <div className="absolute top-[20px] h-[8px] bg-red-400/70" style={zone(stop, entry)} />
      <div className="absolute top-[20px] h-[8px] bg-emerald-500/70" style={zone(entry, target)} />
      {last !== undefined && (
        <div className="absolute top-0 flex flex-col items-center" style={{ left: `${pct(last)}%`, transform: "translateX(-50%)" }}>
          <span className="text-[10px] font-bold uppercase tracking-wide text-label whitespace-nowrap">Hiện tại {fmtPrice(last)}</span>
          <span className="w-[2px] h-[22px] bg-label" />
        </div>
      )}
      {marks.map(m => (
        <div key={m.key} className="absolute top-[16px] flex flex-col items-center" style={{ left: `${pct(m.v)}%`, transform: "translateX(-50%)" }}>
          <span className={clsx("w-[16px] h-[16px] rounded-full border-2 border-white shadow", m.color)} />
          <span className={clsx("mt-1 text-[10px] sm:text-[11px] font-bold whitespace-nowrap tabular-nums", m.text)}>{m.key} {fmtPrice(m.v)}</span>
        </div>
      ))}
    </div>
  );
}

// 1 ô số liệu lớn (Entry / Mục tiêu / Cắt lỗ): tiêu đề + 1–2 dòng "nhãn — số to".
function LevelTile({ icon: Icon, title, tone, rows }: {
  icon: typeof Target; title: string; tone: string;
  rows: { label: string; value: string; className: string }[];
}) {
  return (
    <div className="p-4 sm:p-5 flex flex-col">
      <div className={clsx("flex items-center gap-2 mb-3", tone)}>
        <Icon size={16} strokeWidth={2.5} aria-hidden="true" />
        <h3 className="text-[12px] sm:text-[13px] font-extrabold uppercase tracking-wider">{title}</h3>
      </div>
      {rows.length ? (
        <div className="flex flex-col gap-2.5">
          {rows.map(r => (
            <div key={r.label}>
              <div className="text-[10px] sm:text-[11px] font-bold uppercase tracking-wider text-muted-light">{r.label}</div>
              <p className={clsx("font-black tabular-nums tracking-tight leading-none text-[30px] sm:text-[36px]", r.className)}>{r.value}</p>
            </div>
          ))}
        </div>
      ) : (
        <span className="text-muted-light">—</span>
      )}
    </div>
  );
}

// 1 chỉ số tổng hợp (R:R, % lợi nhuận, % rủi ro) — số rất lớn.
function KpiBox({ label, value, sub, className }: { label: string; value: string; sub?: string; className: string }) {
  return (
    <div className="flex-1 min-w-[96px] px-3 py-2.5 text-center">
      <div className="text-[10px] sm:text-[11px] font-bold uppercase tracking-wider text-muted-light">{label}</div>
      <div className={clsx("font-black tabular-nums leading-tight text-[24px] sm:text-[30px]", className)}>{value}</div>
      {sub && <div className="text-[10px] sm:text-[11px] text-muted-light tabular-nums">{sub}</div>}
    </div>
  );
}

function TodaySignalCard({ signal, lastClose }: { signal: any; lastClose?: number }) {
  const dir: Dir = (["tăng", "giảm", "đi ngang"] as const).includes(signal.direction) ? signal.direction : "đi ngang";
  const trendMeta = TREND_META[dir];
  const strategy = parseStrategy(signal.trading_strategy);
  const body = (re: RegExp) => {
    const p = strategy?.find(s => re.test(s.label));
    return p ? subCO2(stripMarkdown(p.body)) : "";
  };
  const entryBody = body(/entry/i), targetBody = body(/mục tiêu/i), riskBody = body(/quản trị rủi ro|cắt lỗ|stop/i);
  const lv = parseSignalLevels(dir, entryBody, targetBody, riskBody);

  const hasEntry = lv.entryBuy.length > 0 || lv.entrySell.length > 0;
  const hasLevels = hasEntry || lv.targetBuy !== undefined || lv.targetSell !== undefined
    || lv.stopLower !== undefined || lv.stopUpper !== undefined;

  const buyCls = "text-primary-dark", sellCls = "text-[#a67520]";
  const entryRows = [
    ...(lv.entryBuy.length ? [{ label: "Mua quanh", value: fmtRange(lv.entryBuy), className: buyCls }] : []),
    ...(lv.entrySell.length ? [{ label: "Bán quanh", value: fmtRange(lv.entrySell), className: sellCls }] : []),
  ];
  const targetRows = [
    ...(lv.targetBuy !== undefined ? [{ label: "Chốt vị thế mua", value: fmtPrice(lv.targetBuy), className: "text-emerald-700" }] : []),
    ...(lv.targetSell !== undefined ? [{ label: "Chốt vị thế bán", value: fmtPrice(lv.targetSell), className: "text-red-600" }] : []),
  ];
  const stopRows = [
    ...(lv.stopLower !== undefined ? [{ label: "Cắt lỗ nếu dưới", value: fmtPrice(lv.stopLower), className: "text-red-600" }] : []),
    ...(lv.stopUpper !== undefined ? [{ label: "Cắt lỗ nếu trên", value: fmtPrice(lv.stopUpper), className: "text-red-600" }] : []),
  ];

  // KPI tính TỪ chính các mức giá đã tách (không qua LLM): chỉ khi kịch bản có
  // hướng rõ ràng và đủ Entry/Mục tiêu/Cắt lỗ đúng thứ tự.
  let kpis: { label: string; value: string; sub?: string; className: string }[] = [];
  let ladder: { stop: number; entry: number; target: number } | null = null;
  if (dir !== "đi ngang" && hasEntry) {
    const long = dir === "tăng";
    const entry = long ? mid(lv.entryBuy) : mid(lv.entrySell);
    const target = long ? lv.targetBuy : lv.targetSell;
    const stop = long ? lv.stopLower : lv.stopUpper;
    if (entry !== undefined && !Number.isNaN(entry) && target !== undefined && stop !== undefined) {
      const reward = long ? target - entry : entry - target;
      const risk = long ? entry - stop : stop - entry;
      if (reward > 0 && risk > 0) {
        ladder = { stop, entry, target };
        kpis = [
          { label: "Risk / Reward", value: `1 : ${(reward / risk).toFixed(1)}`, sub: "rủi ro : lợi nhuận", className: "text-primary-dark" },
          { label: "Lợi nhuận mục tiêu", value: `+${((reward / entry) * 100).toFixed(1)}%`, sub: `${fmtPrice(reward)} EUR/tCO₂`, className: "text-emerald-700" },
          { label: "Rủi ro tối đa", value: `−${((risk / entry) * 100).toFixed(1)}%`, sub: `${fmtPrice(risk)} EUR/tCO₂`, className: "text-red-600" },
        ];
      }
    }
  } else if (dir === "đi ngang" && lv.entryBuy.length && lv.entrySell.length) {
    const lo = mid(lv.entryBuy), hi = mid(lv.entrySell);
    if (hi > lo) kpis = [{ label: "Biên độ giao dịch", value: `${(((hi - lo) / lo) * 100).toFixed(1)}%`, sub: `${fmtPrice(lo)} → ${fmtPrice(hi)}`, className: "text-blue-700" }];
  }

  // Dữ liệu cũ (chưa có trading_strategy) chỉ có action_plan dạng câu — vẫn hiển thị.
  const fallbackText: string = signal.trading_strategy || signal.action_plan || "";

  return (
    <div className="mb-6">
      <div className="rounded-xl border border-primary/20 overflow-hidden shadow-[var(--shadow-soft)]">
        {/* ═══ Header: tiêu đề + Độ tin cậy · Thị trường · Xu hướng ═══ */}
        <div className="bg-gradient-to-r from-primary-dark to-[#0d7870] px-4 sm:px-6 py-3.5 [print-color-adjust:exact] [-webkit-print-color-adjust:exact]">
          <div className="flex items-center justify-between flex-wrap gap-y-2">
            <div className="flex items-center gap-2.5 text-white">
              <LineChart size={20} strokeWidth={2.5} aria-hidden="true" />
              <h2 className="text-[18px] sm:text-[20px] font-extrabold uppercase tracking-wide">TÍN HIỆU HÔM NAY</h2>
            </div>
            <div className="flex items-center gap-3 sm:gap-4 text-white/90 text-[12px] sm:text-[13px]">
              {signal.probability && (
                <div className="flex flex-col items-center leading-tight">
                  <span className="text-[9px] sm:text-[10px] uppercase tracking-wider text-white/60 font-medium">Độ tin cậy:</span>
                  <strong className="font-bold text-white">{signal.probability}</strong>
                </div>
              )}
              <div className="w-px h-6 bg-white/25 hidden sm:block" />
              <div className="flex flex-col items-center leading-tight">
                <span className="text-[9px] sm:text-[10px] uppercase tracking-wider text-white/60 font-medium">Thị trường:</span>
                <strong className="font-bold text-white uppercase">Carbon</strong>
              </div>
              {trendMeta && (
                <>
                  <div className="w-px h-6 bg-white/25 hidden sm:block" />
                  <div className="flex flex-col items-center leading-tight">
                    <span className="text-[9px] sm:text-[10px] uppercase tracking-wider text-white/60 font-medium">Xu hướng:</span>
                    <strong className="font-extrabold text-white uppercase text-[14px] sm:text-[16px]">{trendMeta.arrow} {trendMeta.label}</strong>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>

        {/* ═══ Vùng giá tham chiếu (price_zone) ═══ */}
        {signal.price_zone && (
          <div className="flex items-center gap-2 px-4 sm:px-6 py-2.5 bg-tint border-b border-primary/10 text-[13px] sm:text-[14px] text-body [print-color-adjust:exact] [-webkit-print-color-adjust:exact]">
            <Compass size={15} strokeWidth={2.5} className="shrink-0 text-primary-dark" aria-hidden="true" />
            <span className="text-[10px] sm:text-[11px] font-extrabold uppercase tracking-wider text-primary-dark shrink-0">Vùng giá</span>
            <span><HighlightNumbers text={signal.price_zone} /></span>
          </div>
        )}

        {hasLevels ? (
          <>
            {/* ═══ 3 ô số liệu lớn: Entry · Mục tiêu · Cắt lỗ ═══ */}
            <div className="grid grid-cols-1 sm:grid-cols-3 divide-y sm:divide-y-0 sm:divide-x divide-primary/10">
              <LevelTile icon={Crosshair} title="Vào lệnh (Entry)" tone="text-primary-dark" rows={entryRows} />
              <LevelTile icon={Target} title="Mục tiêu (Target)" tone="text-emerald-700" rows={targetRows} />
              <LevelTile icon={ShieldCheck} title="Cắt lỗ (Stop Loss)" tone="text-red-600" rows={stopRows} />
            </div>

            {/* ═══ KPI + thang giá ═══ */}
            {kpis.length > 0 && (
              <div className="border-t border-primary/10 bg-[#f8faf9] px-4 sm:px-6 py-3 [print-color-adjust:exact] [-webkit-print-color-adjust:exact]">
                <div className="flex flex-wrap justify-center divide-x divide-primary/10">
                  {kpis.map(k => <KpiBox key={k.label} {...k} />)}
                </div>
                {ladder && <SignalLadder {...ladder} last={lastClose} />}
              </div>
            )}
          </>
        ) : fallbackText ? (
          <div className="px-4 sm:px-6 py-4 text-[14px] leading-[1.7] text-body whitespace-pre-line">
            <HighlightNumbers text={fallbackText} />
          </div>
        ) : null}

        {/* ═══ Cơ sở (condition) + Rủi ro chính (key_risk) ═══ */}
        <div className="grid grid-cols-1 sm:grid-cols-2 border-t border-primary/10 bg-[#f8faf9] [print-color-adjust:exact] [-webkit-print-color-adjust:exact]">
          <div className="px-4 sm:px-6 py-3.5 flex items-start gap-2.5">
            <div className="mt-0.5 shrink-0 w-7 h-7 rounded-full bg-primary/10 flex items-center justify-center">
              <Sparkles size={14} strokeWidth={2.5} className="text-primary" aria-hidden="true" />
            </div>
            <div className="min-w-0">
              <h4 className="text-[11px] sm:text-[12px] font-extrabold uppercase tracking-[0.15em] text-primary-dark mb-0.5">Cơ sở</h4>
              <p className="text-[13px] sm:text-[14px] leading-[1.65] text-body">
                {signal.condition ? <HighlightNumbers text={signal.condition} /> : <span className="text-muted-light">—</span>}
              </p>
            </div>
          </div>
          <div className="px-4 sm:px-6 py-3.5 flex items-start gap-2.5 border-t sm:border-t-0 sm:border-l border-primary/10">
            <div className="mt-0.5 shrink-0 w-7 h-7 rounded-full bg-red-50 flex items-center justify-center">
              <AlertTriangle size={14} strokeWidth={2.5} className="text-red-500" aria-hidden="true" />
            </div>
            <div className="min-w-0">
              <h4 className="text-[11px] sm:text-[12px] font-extrabold uppercase tracking-[0.15em] text-red-600 mb-0.5">Rủi ro chính</h4>
              <p className="text-[13px] sm:text-[14px] leading-[1.65] text-body">
                {signal.key_risk ? <HighlightNumbers text={signal.key_risk} /> : <span className="text-muted-light">—</span>}
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
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
              <Icon size={13} className="shrink-0" /> {h}
            </button>
          );
        })}
      </div>

      <div role="tabpanel" className="p-3 flex flex-col gap-3 text-[14.5px] leading-[1.55] text-body break-words">
        {(dirMeta || sc.probability) && (
          <div className="flex flex-wrap items-center gap-2">
            {dirMeta && (
              <span className={clsx("inline-flex items-center gap-1 font-mono text-[12px] font-bold uppercase tracking-wider rounded px-2 py-1 border [print-color-adjust:exact] [-webkit-print-color-adjust:exact]", dirMeta.className)}>
                <DirIcon size={13} className="shrink-0" /> Chiều giá: {sc.direction}
              </span>
            )}
            {sc.probability && (
              <span className="font-mono text-[12px] uppercase tracking-wider text-body">Xác suất: <b>{sc.probability}</b></span>
            )}
          </div>
        )}

        {sc.condition && (
          <div>
            <div className="text-[11px] font-bold uppercase tracking-wide text-muted-light mb-0.5">Điều kiện kích hoạt</div>
            <RichText text={sc.condition} />
          </div>
        )}

        <div className="rounded-lg border-l-2 border-primary bg-primary/[0.08] px-3 py-2">
          <div className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wide text-primary-dark mb-0.5">
            <Target size={13} className="shrink-0" /> Vùng giá tham chiếu
          </div>
          {sc.price_zone ? <span className="font-semibold text-primary-dark"><RichText text={sc.price_zone} /></span> : <span className="text-muted-light">—</span>}
        </div>

        <div className="rounded-lg border-l-2 border-primary bg-primary/[0.06] px-3 py-2">
          <div className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wide text-primary-dark mb-1">
            <Crosshair size={13} className="shrink-0" /> Chiến lược Trading
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
              <AlertTriangle size={13} className="shrink-0" /> Rủi ro chính
            </div>
            <span className="text-down"><RichText text={sc.key_risk} /></span>
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
const CONCLUSION_TAG_RE = /\*\*(?:Tổng hợp|Kết luận)\s*:?\*\*/;

// Riêng dòng "**Tổng hợp:**" cuối mục 3 (kết luận chung về giá EUA — xem
// _prompt_section3/KẾT LUẬN CHUNG CHO CẢ MỤC "PHÂN TÍCH" trong report_generator.py)
// cần TÁCH RA khỏi các block phân tích để đưa lên đầu báo cáo, làm phần "Nhận
// định" trong khối "🚨 ĐIỂM NHẤN" gộp chung với "Tóm tắt điều hành".
const EUA_SUMMARY_TAG_RE = /\*\*Tổng hợp\s*:?\*\*/;

// Nhãn xu hướng giá EUA do backend ghi ngay sau "**Tổng hợp:**" (xem
// _prompt_section3 trong report_generator.py): [TÍCH CỰC]/[TRUNG LẬP]/[TIÊU CỰC]
// — quyết định màu khung "Nhận định". Báo cáo cũ (chưa có nhãn) mặc định trung lập.
type EuaSentiment = "positive" | "neutral" | "negative";
const EUA_SENTIMENT_RE = /^\s*\[\s*(TÍCH CỰC|TRUNG LẬP|TIÊU CỰC)\s*\]\s*/i;
const EUA_SENTIMENT_MAP: Record<string, EuaSentiment> = {
  "TÍCH CỰC": "positive",
  "TRUNG LẬP": "neutral",
  "TIÊU CỰC": "negative",
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
//    — dùng cho các khối cần nhấn mạnh nhất (Tóm tắt điều hành, Tín hiệu hôm nay).
//  - SubHeading: đầu mục con trong 1 Phần, dải nền xanh dương full-width + in
//    đậm để dễ quét mắt mà không cần số thứ tự.
// Icon chỉ giữ lại ở PartHeading (Phần 1/2/3, Nguồn tham khảo) — FramedHighlight
// và SubHeading không có icon.
// Class "report-heading" dùng chung để CSS in ấn (globals.css) nhận diện, ép
// không ngắt trang ngay sau heading.

function PartHeading({ eyebrow, title, icon: Icon }: { eyebrow?: string; title: string; icon: typeof Sparkles }) {
  const label = eyebrow ? `${eyebrow}: ${title}` : title;
  return (
    <div className="report-heading relative mb-6 print:break-after-avoid">
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
// hành (tone "blue", đầu báo cáo) và TÍN HIỆU HÔM NAY (tone "primary", đầu Phần 2).
// Kiểu chữ DÙNG CHUNG cho tiêu đề các khối nổi bật (Nhận định tổng quan, Tóm tắt
// điều hành, Tín hiệu hôm nay) — cùng cỡ, in hoa, căn giữa.
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
function DotBullets({ items, render }: { items: any[]; render: (item: any, i: number) => React.ReactNode }) {
  return (
    <ul className="list-none space-y-2.5">
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
                )}>{ev.impact}</span>
              </div>
              {ev.outcome && (
                <p className="mt-1.5 text-[14.5px] leading-[1.5] text-body italic">
                  <span className="font-semibold not-italic text-label">Kết quả: </span>{ev.outcome}
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
  return (
    <button
      type="button"
      onClick={onClick}
      title="Gỡ gợi ý này khỏi báo cáo"
      aria-label="Gỡ gợi ý này khỏi báo cáo"
      className={clsx(
        "inline-flex items-center justify-center w-6 h-6 rounded-md border border-down/30 text-down bg-background hover:bg-red-50 transition-colors print:hidden",
        className
      )}
    >
      <Trash2 size={12} aria-hidden="true" />
    </button>
  );
}

function BizRecommendationTable({
  heading,
  accent,
  rows,
  columns,
  infoKey,
  infoLabel,
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
  // Chỉ truyền ở màn hình admin: nút gỡ gợi ý (hàng phải có "id" — gợi ý ngắn hạn
  // lưu trong bộ nhớ biz_suggestions).
  onDismiss?: (id: number) => void;
}) {
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
              cách "Bảng giá nhanh"/"Bảng tín hiệu nhanh" đã làm ở trên. */}
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
                const info = infoKey ? row[infoKey] : "";
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
                                  aria-label={isOpen ? `Ẩn ${infoLabel || "chi tiết"}` : `Xem ${infoLabel || "chi tiết"}`}
                                  title={isOpen ? `Ẩn ${infoLabel || "chi tiết"}` : `Xem ${infoLabel || "chi tiết"}`}
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
                          <span className="font-bold text-primary-dark mr-1">{infoLabel || "Chi tiết"}:</span>
                          <RichText text={info} />
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
        <p className="text-[14.5px] text-muted-light italic">Không có gợi ý nào đủ căn cứ trong kỳ này.</p>
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
  const rawData = report?.content["2"]?.chart_data;
  const candles = rawData && rawData.length > 0 ? rawData : [];
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  // Vẽ theo đúng bề rộng thật của khung (đo bằng ResizeObserver) thay vì kéo giãn 1
  // viewBox cố định bằng preserveAspectRatio="none" — kiểu cũ làm méo chữ và nến.
  // 640 là bề rộng mặc định khi chưa đo được (SSR, lần render đầu, in PDF).
  const wrapRef = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(640);
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(entries => {
      const w = Math.round(entries[0].contentRect.width);
      if (w > 0) setW(w);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  if (candles.length === 0) {
    return (
      <div className="w-full h-[300px] sm:h-[360px] flex items-center justify-center text-muted-light text-sm border border-border rounded-lg bg-background">
        Đang cập nhật dữ liệu...
      </div>
    );
  }

  const compact = W < 480;
  const H = compact ? 300 : 360;
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
            <span className={clsx("font-semibold", activeUp ? "text-up" : "text-down")}>{active[k].toFixed(2)}</span>
          </span>
        ))}
        <span className={clsx("font-semibold whitespace-nowrap", chg >= 0 ? "text-up" : "text-down")}>
          {chg >= 0 ? "+" : ""}{chg.toFixed(2)} ({chg >= 0 ? "+" : ""}{chgPct.toFixed(2)}%)
        </span>
        {hasVolume && active.volume !== undefined && (
          <span className="whitespace-nowrap">
            <span className="text-muted-light mr-1">Vol</span>
            <span className="text-foreground">{active.volume.toLocaleString("en-US")}</span>
          </span>
        )}
      </div>

      <svg
        width="100%" viewBox={`0 0 ${W} ${H}`} className="block w-full h-auto cursor-crosshair select-none touch-pan-y"
        role="img" aria-label={`Biểu đồ nến ${candles.length} phiên, đóng cửa gần nhất ${last.close.toFixed(2)}`}
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
                {v.toFixed(tickDecimals)}
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
            {last.close.toFixed(2)}
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
              {active.close.toFixed(2)}
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

function formatVietnameseDate(dateStr: string) {
  if (!dateStr) return "";
  const parts = dateStr.split("-");
  if (parts.length !== 3) return dateStr;

  const d = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, parseInt(parts[2]));
  if (isNaN(d.getTime())) return dateStr;

  return `Ngày ${parts[2]} tháng ${parts[1]} năm ${parts[0]}`;
}

const VIETNAMESE_DAYS = ["Chủ Nhật", "Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy"];

// "YYYY-MM-DD" -> "Thứ Ba"... (dựng Date theo giờ địa phương từ từng phần — tránh
// new Date("YYYY-MM-DD") parse theo UTC làm lệch 1 ngày ở múi giờ âm).
function vietnameseWeekday(dateStr: string) {
  const parts = (dateStr || "").split("-");
  if (parts.length !== 3) return "";
  const d = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, parseInt(parts[2]));
  return isNaN(d.getTime()) ? "" : VIETNAMESE_DAYS[d.getDay()];
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

function EuaDashboardWidget({ report }: { report: Report }) {
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
  
  const sevenDaysAgo = chartData.length > 7 ? chartData[chartData.length - 8].close : chartData[0].close;
  const sevenDayChange = lastClose - sevenDaysAgo;
  const sevenDayChangePct = sevenDaysAgo ? (sevenDayChange / sevenDaysAgo) * 100 : 0;

  const monthHigh = Math.max(...chartData.map((c: any) => c.high));
  const monthLow = Math.min(...chartData.map((c: any) => c.low));
  const firstClose = chartData[0].close;
  const monthChange = lastClose - firstClose;
  const monthChangePct = firstClose ? (monthChange / firstClose) * 100 : 0;

  const vol = chartData[chartData.length - 1].volume || 0;
  const avgVol = report.content["2"]?.avg_volume_20 || 0;
  const volChangePct = avgVol ? ((vol - avgVol) / avgVol) * 100 : 0;

  return (
    <div className="bg-surface-alt border border-border rounded-xl p-3 sm:p-5 shadow-[var(--shadow-soft)] print:break-inside-avoid">
      <div className="flex flex-col lg:flex-row gap-5 items-stretch">
        {/* Cột trái (Chart) */}
        <div className="lg:flex-[1.6] flex flex-col min-w-0 bg-background border border-border rounded-xl p-4 shadow-sm">
          {/* Header chart */}
          <div className="flex flex-wrap items-center justify-between mb-4 gap-2">
            <div className="flex items-center gap-3">
              <div className="bg-primary text-white p-2 rounded-xl shadow-sm">
                <Leaf size={24} />
              </div>
              <div>
                <h3 className="font-bold text-label text-[17px]">EUA Dec-26 <span className="text-muted-light font-normal text-[14px]">· Nến 30 ngày</span></h3>
                <p className="text-muted text-[13px] mt-0.5">Thị trường quyền phát thải CO₂ châu Âu</p>
              </div>
            </div>
            <div className="flex flex-col items-end gap-1.5">
              <div className="flex items-center text-[12px] text-muted gap-1.5 font-medium">
                <Calendar size={14} /> Cập nhật: {lastDate}
              </div>
              <div className="bg-tint text-primary-dark text-[11px] font-bold px-2.5 py-1 rounded-full border border-primary/20">
                EUR/tCO₂e
              </div>
            </div>
          </div>
          
          {/* Controls */}
          <div className="flex items-center justify-between mb-3 border-b border-border pb-3">
            {/* Chỉ có dữ liệu 1M (chart_data 30 phiên) — nhãn tĩnh, không phải nút chọn khung. */}
            <div className="px-3 py-1.5 rounded-md text-[12px] font-bold bg-primary text-white shadow-sm">1M</div>
            <div className="flex items-center text-[12px] font-bold text-primary-dark gap-1.5">
              <TrendingUp size={16} /> EUR/tCO₂e
            </div>
          </div>
          
          {/* Chart */}
          <div className="-mx-1">
            <CandlestickChart report={report} />
          </div>
        </div>

        {/* Cột phải (Stats & Insights) */}
        <div className="lg:flex-1 flex flex-col gap-4">
          {/* Last Close Card */}
          <div className="bg-primary-dark text-white rounded-xl p-5 shadow-sm relative overflow-hidden">
             <div className="absolute -right-4 -bottom-4 opacity-10"><Database size={100} /></div>
             <div className="flex items-center text-[13px] font-semibold mb-3 opacity-90 gap-2">
               <Database size={16} /> Giá đóng cửa phiên trước ({lastDate})
             </div>
             <div className="flex items-baseline gap-2 mb-1">
               <span className="text-[40px] font-bold leading-none">{lastClose.toFixed(2)}</span>
               <span className="text-[14px] font-medium opacity-90">EUR/tCO₂e</span>
             </div>
             <div className="flex items-center justify-end mt-2">
               <div className={clsx("inline-flex items-center gap-1 text-white text-[14px] font-bold px-3 py-1.5 rounded-full shadow-sm", dayChange >= 0 ? "bg-up" : "bg-down")}>
                 {dayChange >= 0 ? <ChevronUp size={18} strokeWidth={3} /> : <ChevronDown size={18} strokeWidth={3} />}
                 {dayChange > 0 ? "+" : ""}{dayChange.toFixed(2)} ({dayChangePct > 0 ? "+" : ""}{dayChangePct.toFixed(2)}%)
               </div>
             </div>
          </div>
          
          {/* Grid Stats */}
          <div className="grid grid-cols-2 gap-3">
            <div className="bg-background border border-border rounded-xl p-3.5 shadow-sm">
              <div className="text-[12px] text-muted-light font-bold mb-1.5 flex items-center gap-1.5"><RefreshCw size={14}/> Biến động ngày</div>
              <div className="text-[15px] font-bold text-label mb-1">{dayLow.toFixed(2)} — {dayHigh.toFixed(2)}</div>
              <div className="text-[11px] text-muted-light font-medium">(biên độ {dayRange.toFixed(2)}, ~{dayRangePct.toFixed(1)}%)</div>
            </div>
            <div className="bg-background border border-border rounded-xl p-3.5 shadow-sm">
              <div className="text-[12px] text-muted-light font-bold mb-1.5 flex items-center gap-1.5">{sevenDayChange >= 0 ? <TrendingUp size={14} className="text-up"/> : <TrendingDown size={14} className="text-down"/>} {sevenDayChange >= 0 ? "Tăng" : "Giảm"} 7 ngày</div>
              <div className={clsx("text-[15px] font-bold mb-1", sevenDayChange >= 0 ? "text-up" : "text-down")}>
                {sevenDayChange > 0 ? "+" : ""}{sevenDayChange.toFixed(2)} ({sevenDayChangePct > 0 ? "+" : ""}{sevenDayChangePct.toFixed(2)}%)
              </div>
              <div className="text-[11px] text-muted-light font-medium">so với 7 ngày trước</div>
            </div>
            <div className="bg-background border border-border rounded-xl p-3.5 shadow-sm">
              <div className="text-[12px] text-muted-light font-bold mb-1.5 flex items-center gap-1.5"><CalendarRange size={14}/> EUA 30 ngày</div>
              <div className="text-[15px] font-bold text-label mb-1">{monthLow.toFixed(2)} → {monthHigh.toFixed(2)}</div>
              <div className={clsx("text-[11px] font-bold", monthChangePct >= 0 ? "text-up" : "text-down")}>
                ({monthChangePct > 0 ? "+" : ""}{monthChangePct.toFixed(1)}%)
              </div>
            </div>
            <div className="bg-background border border-border rounded-xl p-3.5 shadow-sm">
              <div className="text-[12px] text-muted-light font-bold mb-1.5 flex items-center gap-1.5">{monthChange >= 0 ? <TrendingUp size={14} className="text-up"/> : <TrendingDown size={14} className="text-down"/>} Biến động 30 ngày</div>
              <div className="text-[15px] font-bold text-label mb-1">{firstClose.toFixed(2)} → {lastClose.toFixed(2)}</div>
              <div className={clsx("text-[11px] font-bold", monthChangePct >= 0 ? "text-up" : "text-down")}>
                ({monthChangePct >= 0 ? "tăng" : "giảm"} {Math.abs(monthChangePct).toFixed(1)}%)
              </div>
            </div>
          </div>
          
          {/* Insights */}
          <div className="bg-background rounded-xl p-4 border border-border shadow-sm flex-1 flex flex-col">
            <div className="flex items-center gap-2 text-primary-dark font-bold text-[14px] mb-4">
              <div className="p-1.5 rounded-full bg-tint"><Info size={16} /></div> Nhận định thị trường
            </div>
            <div className="space-y-3.5 text-[13px] text-body">
              <div className="flex items-start gap-2.5">
                <TrendDot up={sevenDayChange >= 0} />
                <div className="leading-snug"><strong className="text-label font-bold">Xu hướng 7 ngày:</strong> {sevenDayChange >= 0 ? "Tăng so với 7 ngày trước" : "Giảm so với 7 ngày trước"}, với mức {sevenDayChange >= 0 ? "tăng" : "giảm"} <br/>{Math.abs(sevenDayChange).toFixed(2)} ({sevenDayChangePct > 0 ? "+" : ""}{sevenDayChangePct.toFixed(2)}%) so với 7 ngày trước.</div>
              </div>
              <div className="flex items-start gap-2.5">
                <TrendDot up={lastClose >= dayOpen} />
                <div className="leading-snug"><strong className="text-label font-bold">Biến động trong ngày:</strong> mở {dayOpen.toFixed(2)} — cao {dayHigh.toFixed(2)} — thấp {dayLow.toFixed(2)} — đóng cửa {lastClose.toFixed(2)} EUR/tCO₂e, <br/>(biên độ {dayRange.toFixed(2)}, ~{dayRangePct.toFixed(1)}% so với giá đóng cửa).</div>
              </div>
              <div className="flex items-start gap-2.5">
                <div className="mt-0.5 shrink-0 w-[22px] h-[22px] rounded-full bg-[#9ca3af] flex items-center justify-center text-white"><Minus size={14} strokeWidth={3} /></div>
                <div className="leading-snug"><strong className="text-label font-bold">EUA 30 ngày:</strong> {firstClose.toFixed(2)} → {lastClose.toFixed(2)}, đóng gần nhất {lastClose.toFixed(2)} ({monthChangePct > 0 ? "+" : ""}{monthChangePct.toFixed(1)}%); 30-ngày-cao {monthHigh.toFixed(2)}, 30-ngày-thấp {monthLow.toFixed(2)}.</div>
              </div>
              <div className="flex items-start gap-2.5">
                <TrendDot up={volChangePct >= 0} />
                <div className="leading-snug"><strong className="text-label font-bold">Khối lượng giao dịch:</strong> EUA phiên gần nhất ({lastDate}): {vol.toLocaleString("en-US")} hợp đồng, so với TB 20 phiên gần nhất ({avgVol.toLocaleString("en-US")} hợp đồng) — ở mức {volChangePct >= 0 ? "cao hơn" : "thấp hơn"} trung bình ({volChangePct > 0 ? "+" : ""}{volChangePct.toFixed(1)}%).</div>
              </div>
              <div className="flex items-start gap-2.5 pt-1">
                <div className="mt-0.5 shrink-0 w-[22px] h-[22px] rounded-full bg-muted-light/20 flex items-center justify-center text-muted"><Info size={14} strokeWidth={3} /></div>
                <div className="leading-snug text-muted-light font-medium italic">Giá phiên đó {lastClose >= dayOpen ? "tăng" : "giảm"} so với giá mở cửa cùng phiên.</div>
              </div>
            </div>
          </div>
        </div>
      </div>
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
}: {
  report: Report;
  // Chỉ màn hình admin truyền vào — hiện nút gỡ gợi ý kinh doanh (services/biz_memory.py).
  onDismissBizSuggestion?: (id: number) => void;
}) {
  const priceRows = report?.content["2"]?.prices || [];
  // Menu hamburger điều hướng section (thanh dưới banner).
  const [navOpen, setNavOpen] = useState(false);
  const keyDevelopmentItems: any[] = [
    ...(report.content["2"]?.key_developments ?? []),
    ...((report.content["4"]?.bullets ?? [])
      .map((b: any) => (typeof b === "string" ? { text: b } : b))
      .filter((b: any) => b?.text && !/không có diễn biến/i.test(b.text))),
  ];

  // Thanh trượt giá: tên hợp đồng + giá + Δ ngày, lấy thẳng từ Bảng giá nhanh.
  const tickerItems = priceRows
    .filter((r: any) => r.name && r.price)
    .map((r: any) => {
      // r.price dạng "<số> <đơn vị>" (vd "81.4400 EUR/tCO2") — tách để định dạng số, giữ đơn vị.
      const [rawNumber, ...unitParts] = String(r.price).split(" ");
      return {
        name: r.name as string,
        price: formatCompactPriceNumber(rawNumber),
        unit: subCO2(unitParts.join(" ")),
        dday: r.dday && r.dday !== "-" ? String(r.dday) : null,
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
  // TÍN HIỆU HÔM NAY (đầu Phần 2) tái dùng đúng kịch bản "ngắn hạn" đã có
  // trong trading_scenarios (Mục 3) — không cần trường dữ liệu riêng cho
  // "hôm nay" từ backend.
  const todaySignal = report.content["3"]?.trading_scenarios?.find((sc: any) => sc.horizon === "ngắn hạn");
  // Bảng tín hiệu nhanh (ngay dưới TÍN HIỆU HÔM NAY) tái dùng kịch bản "trung
  // hạn" cho dòng xu hướng 1-3 tháng.
  const midTermSignal = report.content["3"]?.trading_scenarios?.find((sc: any) => sc.horizon === "trung hạn");
  // Hỗ trợ/Kháng cự/Mục tiêu — tính trực tiếp từ OHLC thật 30 phiên
  // (report.content["2"].chart_data), ĐÚNG công thức với backend
  // services/report_generator.py::_eua_technical_levels_summary() (đỉnh/đáy
  // 30 phiên + đo biên độ dao động cho mục tiêu breakout) — không qua LLM suy
  // diễn. "Cắt lỗ" không có công thức xác nhận từ dữ liệu thật nên để trống
  // (hiển thị "—") thay vì tự bịa mốc.
  const chartData = report.content["2"]?.chart_data || [];
  const technicalLevels = chartData.length > 0 ? (() => {
    const resistance = Math.max(...chartData.map((c: any) => c.high));
    const support = Math.min(...chartData.map((c: any) => c.low));
    return { support, resistance, target: resistance + (resistance - support) };
  })() : null;
  const fmtEua = (n: number) => `${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} EUR/tCO\u2082`;
  const NO_DATA = <span className="text-muted-light">—</span>;
  const quickSignalRows: { label: string; value: React.ReactNode }[] = [
    {
      label: "Xu hướng ngắn hạn (1–2 tuần)",
      value: todaySignal ? (
        <span className={TREND_META[todaySignal.direction]?.className}>
          {TREND_META[todaySignal.direction]?.arrow} {TREND_META[todaySignal.direction]?.label}
        </span>
      ) : NO_DATA,
    },
    {
      label: "Xu hướng trung hạn (1–3 tháng)",
      value: midTermSignal ? (
        <span className={TREND_META[midTermSignal.direction]?.className}>
          {TREND_META[midTermSignal.direction]?.arrow} {TREND_META[midTermSignal.direction]?.label}
        </span>
      ) : NO_DATA,
    },
    {
      label: "Khuyến nghị vị thế",
      value: todaySignal ? POSITION_META[todaySignal.direction] ?? NO_DATA : NO_DATA,
    },
    {
      label: todaySignal?.direction === "giảm" ? "Vùng bán tham chiếu"
        : todaySignal?.direction === "đi ngang" ? "Vùng giá tham chiếu" : "Vùng mua tham chiếu",
      value: todaySignal?.price_zone ? <RichText text={todaySignal.price_zone} /> : NO_DATA,
    },
    {
      label: "Hỗ trợ",
      value: technicalLevels ? `${fmtEua(technicalLevels.support)} (đáy 30 phiên gần nhất)` : NO_DATA,
    },
    {
      label: "Kháng cự",
      value: technicalLevels ? `${fmtEua(technicalLevels.resistance)} (đỉnh 30 phiên gần nhất)` : NO_DATA,
    },
    {
      label: "Mục tiêu kỹ thuật",
      value: technicalLevels ? `${fmtEua(technicalLevels.target)} (đo biên độ nếu phá kháng cự)` : NO_DATA,
    },
  ];


  return (
    // max-w-[210mm]: khổ A4 — trên màn hình rộng báo cáo hiển thị như 1 trang PDF
    // thật (căn giữa, có viền/bóng đổ), thay vì kéo giãn hết chiều ngang trình
    // duyệt. Khi in (@media print trong globals.css), khổ A4 thật (@page) luôn
    // hẹp hơn 210mm (đã trừ margin) nên max-width này không co hẹp thêm nội dung in.
    // Mobile (< sm): báo cáo tràn sát 2 mép màn hình (-mx-4 bù đúng px-4 của <main>
    // trong app/layout.tsx), bỏ viền/bo góc/bóng của card — trước đây lề <main> +
    // viền card + lề nội dung + lề khung FramedHighlight cộng dồn, chữ trong khung
    // chỉ còn ~77% bề ngang màn hình 375px. Lề đọc giờ chỉ còn px-4 của nội dung.
    <div className="report-shell max-w-[210mm] mx-auto max-sm:-mx-4 bg-background text-foreground font-sans leading-[1.5] rounded-2xl max-sm:rounded-none border border-border max-sm:border-x-0 shadow-[var(--shadow-soft)] max-sm:shadow-none overflow-hidden mb-10">

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
                {vietnameseWeekday(report.report_date)}
              </div>
              <div className="text-[13px] sm:text-[15px] font-bold leading-snug">
                {formatVietnameseDate(report.report_date)}
              </div>
            </div>
          </div>

          {/* Vạch ngăn thụt theo lề nội dung (không tràn sát mép như đường nối cũ) */}
          <div className="h-px bg-white/15 mt-4 sm:mt-5 mb-4 sm:mb-5" />

          <div className="text-[11px] sm:text-[12px] font-semibold tracking-[0.2em] uppercase text-[#8fd9a8] mb-1.5">
            Carbon Market Daily News
          </div>
          <h1 className="text-[21px] sm:text-[28px] font-extrabold tracking-[0.2px] leading-[1.2] [text-wrap:balance]">
            TIN TỨC HÀNG NGÀY THỊ TRƯỜNG CARBON
          </h1>
          {/* div (không phải p) — tránh rule ".report-shell p { text-align: justify }"
              làm giãn chữ khi dòng này xuống hàng trên mobile. Tên công ty đã có
              trong logo nên ẩn trên mobile cho gọn 1 dòng. */}
          <div className="mt-2 text-[11.5px] sm:text-[12.5px] leading-snug text-white/80">
            Người báo cáo: Jenny AI · Phòng CLPT<span className="hidden sm:inline"> — Stavian Industrial Metal</span>
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
            aria-label={navOpen ? "Đóng menu điều hướng" : "Mở menu điều hướng"}
            className={clsx(
              "h-full flex items-center gap-2 px-3 sm:px-4 py-2.5 text-[#1B4D3E] transition-colors",
              navOpen ? "bg-[#e8efeb]" : "hover:bg-[#e8efeb]"
            )}
          >
            {navOpen ? <X size={18} strokeWidth={2.5} /> : <Menu size={18} strokeWidth={2.5} />}
            <span className="hidden sm:inline text-[12.5px] font-bold uppercase tracking-wide">Mục lục</span>
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
                      {item.label}
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
                "report-heading rounded-lg px-3.5 py-3 shadow-sm [&_strong]:text-inherit [print-color-adjust:exact] [-webkit-print-color-adjust:exact]",
                EUA_SENTIMENT_STYLE[euaVerdict.sentiment].box
              )}
            >
              <h2 className={clsx(BLOCK_TITLE_CLASS, "mb-1.5")}>NHẬN ĐỊNH TỔNG QUAN</h2>
              {/* Nội dung nhận định: chữ thường, in đậm (KHÔNG in hoa — chỉ tiêu đề in hoa) */}
              <p className="text-[14.5px] sm:text-[15.5px] leading-[1.45] font-bold normal-case text-left">
                <RichText text={euaVerdict.text} />
              </p>
            </div>
          )}

          <FramedHighlight title="Tóm tắt điều hành" tone="blue">
            <ul className="list-none -my-0.5">
              {report.content["1"]?.bullets?.map((bullet: any, i: number) => {
                const b: string = typeof bullet === "string" ? bullet : bullet.text || "";
                const isMatch = b.includes(':');
                const tag = stripMarkdown(isMatch ? b.split(':')[0] : 'Note');
                const text = isMatch ? b.split(':').slice(1).join(':') : b;
                const sourceName = typeof bullet === "object" ? bullet.source_name : null;
                const sourceUrl = typeof bullet === "object" ? bullet.source_url : null;

                return (
                  <li key={i} className="py-1 text-[14.5px]">
                    <p className="text-foreground leading-[1.3]">
                      <span className={clsx("font-mono text-[10.5px] border rounded-[3px] px-1 py-px mr-2", tagColorClass(tag.trim()))}>
                        {tag.trim().substring(0, 15)}
                      </span>
                      <RichText text={text.trim()} />
                    </p>
                    {sourceName && (
                      <a
                        href={sourceUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="mt-1 inline-block font-mono text-[11px] text-primary hover:underline"
                      >
                        Nguồn: {sourceName} <ExternalLink size={10} className="inline -mt-0.5" aria-hidden="true" />
                      </a>
                    )}
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
          <PartHeading eyebrow="Phần 1" title="Diễn biến thị trường" icon={LineChart} />

          <EuaDashboardWidget report={report} />

          <div className="mt-5">
            <SubHeading>Bảng giá nhanh</SubHeading>
            {report.content["2"]?.price_timestamp && (
              <p className="font-mono text-[11px] text-muted-light -mt-3 mb-4">{report.content["2"].price_timestamp}</p>
            )}

            {/* Bảng giá full-width */}
            <div className="overflow-x-auto border border-border rounded-lg">
              <table className="w-full sm:table-fixed border-collapse font-mono text-[14.5px]">
                <thead>
                  <tr>
                    <th className="w-[28%] sm:w-[30%] text-left text-primary-dark font-bold text-[11px] uppercase tracking-wider px-1.5 sm:px-2 py-1.5 border-b-2 border-primary/30 border-r border-primary/15 bg-tint">Hợp đồng</th>
                    <th className="w-[18%] sm:w-[18%] text-center text-primary-dark font-bold text-[11px] uppercase tracking-wider px-1 sm:px-2 py-1.5 border-b-2 border-primary/30 border-r border-primary/15 bg-tint">Giá</th>
                    <th className="w-[54%] sm:w-[52%] text-left text-primary-dark font-bold text-[11px] uppercase tracking-wider px-1.5 sm:px-2 py-1.5 border-b-2 border-primary/30 bg-tint">Biến động</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {priceRows.map((r: any, i: number) => {
                    const [rawPriceNumber, ...priceUnitParts] = String(r.price || "").split(" ");
                    const priceNumber = formatCompactPriceNumber(rawPriceNumber);
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
                              title={`Xem giá ${r.name} tại nguồn`}
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
                                Δ Ngày: {r.dday}
                              </span>
                            )}
                            {r.dweek !== "-" && (
                              <span className={clsx("tabular-nums", isPositiveDelta(r.dweek) ? "text-up" : "text-down")}>
                                Δ Tuần: {r.dweek}
                              </span>
                            )}
                          </div>
                          {r.code === "CBAM" && r.note && (
                            <div className="mt-1 rounded-md bg-tint/60 border border-primary/15 px-2 py-1.5">
                              {/* Báo cáo cũ có thể đã nối thêm ghi chú LLM phía sau — chỉ lấy phần "Giá chốt…" */}
                              {String(r.note).match(/^Giá chốt theo quý, tại ngày .*?\d{4}(?:, ngày chốt giá tiếp theo .*?\d{4})?/)?.[0] ?? r.note}
                            </div>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* Diễn biến chính — tin tức nổi bật tác động cung/cầu EUA (trực tiếp
              hoặc gián tiếp), mỗi tin có nhãn chiều tác động + nguồn bài viết.
              Backend: content["2"].key_developments (report_generator.py::
              _prompt_key_developments). Báo cáo cũ chưa có trường này → ẩn. */}
          {/* Mục 4 cũ "Cập nhật tín chỉ carbon & CBAM" (VCM, thép xanh, CBAM) được gộp
              vào cuối danh sách này; bỏ dòng "không có diễn biến" vì không phải tin. */}
          {keyDevelopmentItems.length > 0 && (
            <div className="mt-6">
              <SubHeading>Diễn biến chính</SubHeading>
              <DotBullets
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
          <PartHeading eyebrow="Phần 2" title="Phân tích và khuyến nghị giao dịch" icon={BarChart3} />

          {/* TÍN HIỆU HÔM NAY — nội dung đầu tiên của Phần 2: chiến lược trading cho
              phiên hôm đó, tái dùng kịch bản "ngắn hạn" đã có ở Mục 3
              (trading_scenarios) — xem TodaySignalCard. Giá hiện tại = giá đóng
              cửa phiên gần nhất trong chart_data. */}
          {todaySignal && <TodaySignalCard signal={todaySignal} lastClose={chartData.length ? chartData[chartData.length - 1].close : undefined} />}

          {/* Bảng tín hiệu nhanh — nằm dưới TÍN HIỆU HÔM NAY, trên Phân tích:
              các chỉ số nào có sẵn từ dữ liệu (trading_scenarios, OHLC 30
              phiên) thì lấy đúng giá trị thật; chỉ số nào chưa có công thức
              xác nhận (Cắt lỗ) thì để trống, không suy diễn qua LLM. */}
          <div className="mb-6">
            <SubHeading>Bảng tín hiệu nhanh</SubHeading>
            <div className="overflow-x-auto border border-border rounded-lg">
              <table className="w-full border-collapse text-[14.5px]">
                <thead>
                  <tr>
                    <th className="text-left font-mono text-[10px] uppercase tracking-wider text-primary-dark px-1.5 sm:px-3 py-2.5 border-b-2 border-primary/30 border-r border-border bg-tint w-[42%] sm:w-[34%]">Chỉ số</th>
                    <th className="text-left font-mono text-[10px] uppercase tracking-wider text-primary-dark px-1.5 sm:px-3 py-2.5 border-b-2 border-primary/30 bg-tint">Giá trị</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {quickSignalRows.map((row, i) => (
                    <tr key={i} className="align-top even:bg-surface/60">
                      <td className="px-1.5 sm:px-3 py-1.5 border-r border-border font-sans font-semibold text-label">{row.label}</td>
                      <td className="px-1.5 sm:px-3 py-1.5 leading-[1.5] text-body">{row.value}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Động lực thị trường — nằm dưới Bảng tín hiệu nhanh, trên Phân tích
              (chuyển từ Bảng giá nhanh (Phần 1) sang đây trước đó). */}
          {hasMarketDrivers && (
            <div className="mb-6">
              <SubHeading>Động lực thị trường</SubHeading>
              <div className="grid sm:grid-cols-2 gap-3">
                <div className="border border-up/25 bg-up/[0.04] rounded-lg overflow-hidden">
                  <div className="flex items-center gap-1.5 bg-up/10 text-up font-mono text-[11px] font-bold uppercase tracking-wider px-3 py-2 border-b border-up/20">
                    <TrendingUp size={14} strokeWidth={2.5} /> Động lực tăng
                  </div>
                  <div className="p-3 space-y-3">
                    {report.content["2"].market_drivers.bullish?.length > 0 ? (
                      report.content["2"].market_drivers.bullish.map((d: any, i: number) => (
                        <div key={i}>
                          <p className="text-[14.5px] leading-[1.5] text-body">
                            <span className={clsx(
                              "font-mono text-[9.5px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded mr-1.5 align-middle whitespace-nowrap",
                              d.tag === "FACT" ? "bg-up/10 text-up border border-up/30" : "bg-blue-50 text-blue-700 border border-blue-200"
                            )}>{d.tag}</span>
                            <RichText text={d.text} />
                          </p>
                          {d.source_url && (
                            <a
                              href={d.source_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="mt-1 inline-block font-mono text-[10.5px] text-primary hover:underline"
                            >
                              Nguồn: {d.source_name} <ExternalLink size={10} className="inline -mt-0.5" aria-hidden="true" />
                            </a>
                          )}
                        </div>
                      ))
                    ) : (
                      <p className="text-[14.5px] text-muted-light italic">Không có động lực tăng đáng chú ý.</p>
                    )}
                  </div>
                </div>
                <div className="border border-down/25 bg-down/[0.04] rounded-lg overflow-hidden">
                  <div className="flex items-center gap-1.5 bg-down/10 text-down font-mono text-[11px] font-bold uppercase tracking-wider px-3 py-2 border-b border-down/20">
                    <TrendingDown size={14} strokeWidth={2.5} /> Động lực giảm
                  </div>
                  <div className="p-3 space-y-3">
                    {report.content["2"].market_drivers.bearish?.length > 0 ? (
                      report.content["2"].market_drivers.bearish.map((d: any, i: number) => (
                        <div key={i}>
                          <p className="text-[14.5px] leading-[1.5] text-body">
                            <span className={clsx(
                              "font-mono text-[9.5px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded mr-1.5 align-middle whitespace-nowrap",
                              d.tag === "FACT" ? "bg-up/10 text-up border border-up/30" : "bg-blue-50 text-blue-700 border border-blue-200"
                            )}>{d.tag}</span>
                            <RichText text={d.text} />
                          </p>
                          {d.source_url && (
                            <a
                              href={d.source_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="mt-1 inline-block font-mono text-[10.5px] text-primary hover:underline"
                            >
                              Nguồn: {d.source_name} <ExternalLink size={10} className="inline -mt-0.5" aria-hidden="true" />
                            </a>
                          )}
                        </div>
                      ))
                    ) : (
                      <p className="text-[14.5px] text-muted-light italic">Không có động lực giảm đáng chú ý.</p>
                    )}
                  </div>
                </div>
              </div>
            </div>
          )}

          {report.content["3"] && (
            <div className="mb-6">
              <SubHeading>Phân tích</SubHeading>
              {report.content["3"].title && (
                <p className="-mt-2.5 mb-4 ml-[38px] text-[12px] text-muted-light italic">{report.content["3"].title}</p>
              )}

              {(analysisBlocks?.length > 0 || report.content["3"].correlation_analysis) && (
                <button
                  type="button"
                  onClick={() => setShowAnalysis(v => !v)}
                  className="mb-4 flex items-center gap-1.5 font-mono text-[11px] font-bold uppercase tracking-wider text-primary border border-primary/30 bg-tint/50 hover:bg-tint rounded-md px-2.5 py-1.5 transition-colors"
                >
                  {showAnalysis ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
                  {showAnalysis ? "Ẩn phân tích" : "Hiện phân tích"}
                </button>
              )}

              {showAnalysis && (
                <>
                  {analysisBlocks?.map((block: any, i: number) => (
                    <div key={i} className="mb-5">
                      {/* Block "Phân tích" trùng với SubHeading "Phân tích" ngay phía trên → ẩn nhãn */}
                      {block.heading !== "Phân tích" && (
                        <h4 className="font-mono text-[11.5px] font-bold uppercase tracking-widest text-primary mb-1.5">{block.heading}</h4>
                      )}
                      <div className="space-y-1.5">
                        {(block.content || "").split("\n").filter((line: string) => line.trim()).map((line: string, j: number) => {
                          // Backend đôi khi tự chèn gạch đầu dòng thô ("- ...", có thể kèm
                          // dấu cách/tab thừa hoặc en-dash "–") cho từng mã/ý trong 1 nhóm,
                          // ở BẤT KỲ heading nào (Diễn biến chính, Phân tích...) — đổi hiển
                          // thị sang chấm tròn thay vì gạch ngang, không đổi nội dung chữ
                          // (chỉ bỏ đúng phần tiền tố gạch đầu dòng khớp được).
                          const trimmed = line.trim();
                          // Mục 3 / heading "Phân tích": dòng đầu mỗi nhóm gộp sẵn "Tên nhóm:
                          // <kết luận>" (xem ĐỊNH DẠNG trong _prompt_section3) — đóng khung nổi
                          // bật NGUYÊN dòng này thay vì hiện như chữ thường.
                          if (block.heading === "Phân tích" && GROUP_HEADLINE_PREFIXES.some((p) => trimmed.startsWith(p))) {
                            return (
                              <div key={j} className="rounded-md border border-primary/30 bg-tint/60 px-3 py-2">
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
                        })}
                      </div>
                    </div>
                  ))}

                  {report.content["3"].correlation_analysis && (
                    <div className="border-l-2 border-primary bg-tint/40 rounded-r-lg px-3 sm:px-4 py-3 my-5 space-y-2">
                      <h4 className="font-mono text-[11.5px] font-bold uppercase tracking-widest text-primary-dark mb-1.5">Chuỗi Logic: Gas + Coal + Power → EUA</h4>
                      {(report.content["3"].correlation_analysis.gas_comment || report.content["3"].correlation_analysis.gas_coal_power) && (
                        <div className="space-y-2.5 mb-1">
                          {report.content["3"].correlation_analysis.gas_comment && (
                            <p className="text-[14.5px] leading-[1.5] text-body"><b className="text-primary-dark font-bold">Gas:</b> <RichText text={report.content["3"].correlation_analysis.gas_comment} /></p>
                          )}
                          {report.content["3"].correlation_analysis.coal_comment && (
                            <p className="text-[14.5px] leading-[1.5] text-body"><b className="text-primary-dark font-bold">Than:</b> <RichText text={report.content["3"].correlation_analysis.coal_comment} /></p>
                          )}
                          {report.content["3"].correlation_analysis.power_comment && (
                            <p className="text-[14.5px] leading-[1.5] text-body"><b className="text-primary-dark font-bold">Điện Đức:</b> <RichText text={report.content["3"].correlation_analysis.power_comment} /></p>
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
                label: "Nhận định",
                render: (sc) => {
                  const dirMeta = DIRECTION_META[sc.direction];
                  const DirIcon = dirMeta?.icon;
                  return (
                    <div className="flex flex-col gap-1.5 items-start">
                      {dirMeta && (
                        <span className={clsx("flex items-center gap-1 font-mono text-[10px] uppercase tracking-wider rounded px-1.5 py-0.5 border [print-color-adjust:exact] [-webkit-print-color-adjust:exact]", dirMeta.className)}>
                          <DirIcon size={11} className="shrink-0" /> Chiều giá: {sc.direction}
                        </span>
                      )}
                      {sc.probability && (
                        <span className="font-mono text-[10px] uppercase tracking-wider text-body">Xác suất: {sc.probability}</span>
                      )}
                    </div>
                  );
                },
              },
              { label: "Điều kiện kích hoạt", render: (sc) => <RichText text={sc.condition} /> },
              {
                label: "Vùng giá tham chiếu", icon: Target, highlight: true,
                render: (sc) => sc.price_zone ? <span className="font-semibold text-primary-dark"><RichText text={sc.price_zone} /></span> : <span className="text-muted-light">—</span>,
              },
              { label: "Rủi ro chính", icon: AlertTriangle, render: (sc) => <span className="text-down"><RichText text={sc.key_risk} /></span> },
              {
                label: "Chiến lược Trading", icon: Crosshair, highlight: true,
                render: (sc) => sc.trading_strategy ? <RichText text={sc.trading_strategy} /> : <span className="text-muted-light">—</span>,
              },
            ];

            return (
              <div className="mb-6">
                <SubHeading>Kịch bản hành động</SubHeading>

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
                        <th style={{ width: "16%" }} className="text-left font-mono text-[10px] uppercase tracking-wider text-primary-dark px-2 sm:px-3 py-2 sm:py-2.5 border-b-2 border-primary/30 border-r border-border bg-tint">Chỉ tiêu</th>
                        {columns.map(h => {
                          const meta = HORIZON_META[h];
                          const Icon = meta.icon;
                          return (
                            <th key={h} style={{ width: `${dataColWidthPct}%` }} className="text-left px-2 sm:px-3 py-2 sm:py-2.5 border-b-2 border-primary/30 bg-tint text-primary-dark">
                              <span className="flex items-center gap-1.5 font-mono text-[11px] font-bold uppercase tracking-wider">
                                <Icon size={13} /> {h}
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
                    <b className="text-label">Lưu ý:</b> Đây là các chiến lược dựa trên phân tích của Jenny, chỉ mang tính chất tham khảo.
                    Người đọc cần xem xét kỹ, đối chiếu với bối cảnh thực tế và khẩu vị rủi ro của mình trước khi ra quyết định —
                    không phải khuyến nghị đầu tư/giao dịch trực tiếp.
                  </p>
                </div>
              </div>
            );
          })()}

          {/* Cần theo dõi — tách khỏi Mục 3/8, đứng giữa "Kịch bản hành động" và
              "Gợi ý kinh doanh & giải pháp cho SIM" (theo yêu cầu bố cục báo cáo). */}
          {watchpoints && (
            <div className="mb-6">
              <SubHeading>Cần theo dõi</SubHeading>
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
              <div className="flex flex-col gap-6">
                {/* Jenny nhắc lại — gợi ý ở báo cáo trước (trong 10 ngày) mà tình huống
                    kích hoạt vừa xảy ra hôm nay (services/biz_memory.py). */}
                {report.content["biz"].reminders?.length > 0 && (
                  <div className="rounded-lg border-2 border-warn/50 bg-warn-tint overflow-hidden [print-color-adjust:exact] [-webkit-print-color-adjust:exact]">
                    <div className="flex items-center gap-2.5 px-3.5 py-2.5">
                      <img src="/jenny.jpg" alt="" className="w-8 h-8 rounded-full object-cover shrink-0" />
                      <div className="flex-1 min-w-0 text-[14px] sm:text-[15px] font-bold text-label">Jenny cập nhật đề xuất trước đây</div>
                      <button
                        type="button"
                        onClick={() => setShowReminders((v) => !v)}
                        aria-expanded={showReminders}
                        aria-label={showReminders ? "Ẩn cập nhật đề xuất" : "Xem cập nhật đề xuất"}
                        title={showReminders ? "Ẩn" : "Xem chi tiết"}
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
                      {report.content["biz"].reminders.map((r: any, i: number) => (
                        <li key={i} className="px-3.5 py-3 space-y-1.5">
                          <div className="flex items-start gap-2">
                            <p className="flex-1 min-w-0 text-[14.5px] leading-[1.5] text-foreground">
                              Ngày <b>{formatFullDate(r.suggested_date)}</b> Jenny đã đề xuất: <b><RichText text={r.action} /></b>
                            </p>
                            {onDismissBizSuggestion && typeof r.id === "number" && (
                              <DismissButton onClick={() => onDismissBizSuggestion(r.id)} className="shrink-0" />
                            )}
                          </div>
                          <p className="text-[14.5px] leading-[1.5] text-body">
                            <span className="font-semibold">Tình huống kích hoạt:</span> <RichText text={r.trigger} />
                          </p>
                          {r.outcome === "contradicted" ? (
                            <p className="text-[14.5px] leading-[1.5] font-semibold text-down">
                              ✗ Thực tế diễn ra ngược với đề xuất{r.evidence ? <>: <span className="font-normal text-foreground"><RichText text={r.evidence} /></span></> : "."}
                            </p>
                          ) : (
                            <p className="text-[14.5px] leading-[1.5] font-semibold text-up">
                              ✓ Tình huống đã xảy ra{r.evidence ? <>: <span className="font-normal text-foreground"><RichText text={r.evidence} /></span></> : "."}
                            </p>
                          )}
                          {r.source_name && (
                            r.source_url ? (
                              <a
                                href={r.source_url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="inline-flex items-center gap-1 text-[11.5px] text-blue-700 underline decoration-blue-700/40 underline-offset-2 hover:text-blue-900"
                              >
                                Nguồn: {r.source_name} <ExternalLink size={10} aria-hidden="true" />
                              </a>
                            ) : (
                              <span className="text-[11.5px] text-muted-light">Nguồn: {r.source_name}</span>
                            )
                          )}
                          <p className="text-[14.5px] italic text-warn font-semibold">
                            {r.outcome === "contradicted"
                              ? "Đề xuất này không còn phù hợp — Jenny sẽ thôi theo dõi."
                              : "Anh/chị đã thực hiện theo đề xuất này chưa?"}
                          </p>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                <div>
                  <BizRecommendationTable
                    heading="Ngắn hạn"
                    accent="text-label"
                    rows={report.content["biz"].short_term}
                    columns={[
                      { key: "trigger", label: "Tình huống kích hoạt" },
                      { key: "action", label: "Hành động đề xuất" },
                    ]}
                    infoKey="reason"
                    infoLabel="Cơ sở của đề xuất"
                    onDismiss={onDismissBizSuggestion}
                  />
                </div>
                <BizRecommendationTable
                  heading="Dài hạn"
                  accent="text-primary"
                  rows={report.content["biz"].long_term}
                  columns={[
                    { key: "opportunity", label: "Cơ hội" },
                    { key: "solution", label: "Giải pháp đề xuất" },
                  ]}
                  // Kỳ vọng thu vào nút "i" (như Lý do ở bảng Ngắn hạn) — 3 cột hẹp
                  // trên mobile làm mỗi ô chỉ 2–3 chữ/dòng, rất khó đọc.
                  infoKey="expectation"
                  infoLabel="Kỳ vọng"
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
                  <div className="space-y-5">
                    {results.length > 0 && (
                      <div>
                        <h4 className="font-mono text-[10.5px] font-bold uppercase tracking-widest text-label mb-2">Kết quả</h4>
                        <EventTimeline events={results} />
                      </div>
                    )}
                    {upcoming.length > 0 && (
                      <div>
                        <h4 className="font-mono text-[10.5px] font-bold uppercase tracking-widest text-label mb-2">Sự kiện sắp tới</h4>
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
            <PartHeading eyebrow="Phần 3" title={report.content["6"].title || "Chi tiết các tin tức chính"} icon={Newspaper} />
            {[
              { key: "international", label: "Quốc tế" },
              { key: "vietnam", label: "Việt Nam" },
            ].map(({ key, label }) => {
              const items = report.content["6"][key];
              return (
                <div key={key} className="mb-4 last:mb-0">
                  <h4 className="font-mono text-[11.5px] font-bold uppercase tracking-widest text-primary-dark mb-2">{label}</h4>
                  {items?.length > 0 ? (
                    <div className="space-y-2.5">
                      {items.map((art: any, i: number) => (
                        <div key={i} className="pb-2.5 border-b border-border-soft last:border-b-0 last:pb-0">
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
                            <RichText text={art.summary} />{" "}
                            <a
                              href={art.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="font-mono text-[11.5px] text-primary hover:underline"
                            >
                              Nguồn: {art.source} <ExternalLink size={10} className="inline -mt-0.5" aria-hidden="true" />
                            </a>
                          </p>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-[14.5px] text-muted-light italic">Không có tin tức {label.toLowerCase()} trong kỳ này.</p>
                  )}
                </div>
              );
            })}
          </section>
        )}

        {/* NGUỒN THAM KHẢO — đứng độc lập cuối báo cáo, không thuộc Phần nào */}
        {report.content["9"] && (
          <section id="section-sources" className="py-5">
            <PartHeading title={report.content["9"].title || "Nguồn tham khảo"} icon={Link2} />
            {report.content["9"].items?.length > 0 ? (
              (() => {
                // Sắp theo ĐÚNG thứ tự Phần 3 (Quốc tế → Việt Nam, đánh số như trên); link
                // trùng URL thì khớp theo Phần 3, nguồn không nằm ở Phần 3 xếp cuối.
                const all: any[] = report.content["9"].items;
                const used = new Set<string>();
                const groups = [
                  { label: "Quốc tế", list: report.content["6"]?.international ?? [] },
                  { label: "Việt Nam", list: report.content["6"]?.vietnam ?? [] },
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
                  <ul className="space-y-1.5">
                    {entries.map((it: any, i: number) => (
                      <li key={it.url} className="font-mono text-[12px] leading-[1.5] text-muted-light">
                        {i + 1}. [{it.source}]{" "}
                        <a
                          href={it.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-primary hover:underline"
                        >
                          {it.title}
                        </a>
                      </li>
                    ))}
                  </ul>
                );
              })()
            ) : (
              <p className="font-mono text-[12px] text-muted-light">Không có nguồn tin tức trong 48h qua.</p>
            )}
          </section>
        )}

        <footer className="pt-6 text-center border-t border-border">
          <p className="font-mono text-[11px] text-muted-light italic">Báo cáo nội bộ, tổng hợp tự động có kiểm duyệt. Không phải khuyến nghị đầu tư.</p>
        </footer>

      </div>
    </div>
  );
}
