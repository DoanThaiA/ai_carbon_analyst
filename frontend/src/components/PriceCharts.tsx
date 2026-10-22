"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import clsx from "clsx";
import { useTranslations } from "next-intl";
import type { CbamEstimate } from "@/lib/types";

// Biểu đồ giá dùng chung: Dashboard giá của admin (app/admin/price-dashboard/page.tsx) và biểu đồ chỉ số giá /
// popup "So sánh hiệu suất" cạnh Bảng giá nhanh trong báo cáo (PriceIndexPanel.tsx). Dữ liệu: GET
// /api/admin/prices/dashboard hoặc /api/prices/history (cùng định dạng — services/price_history.py).

export type SeriesPoint = { date: string; close: number; volume: number | null };
export type Instrument = {
  code: string;
  name: string;
  category: string;
  unit: string | null;
  exchange: string | null;
  latest: { date: string; close: number; day_change_pct: number | null; week_change_pct: number | null; volume: number | null };
  series: SeriesPoint[];
};
// VWAP đấu giá sơ cấp EEX lũy kế từ đầu quý tới hết ngày đó (services/cbam_estimate.py::auction_vwap_series).
export type AuctionVwapPoint = { date: string; vwap: number; quarter: string; sessions: number };
export type Dashboard = { days: number; latest_date: string | null; instruments: Instrument[]; auction_vwap?: AuctionVwapPoint[] };

// Bảng màu phân loại đã kiểm định (khoảng cách mù màu, độ sáng) — gán theo THỨ TỰ CỐ ĐỊNH của
// instrument (không đổi theo bộ lọc, không xoay vòng). Mã thứ 9 trở đi dùng xám trung tính.
// Chữ/nhãn luôn dùng màu chữ; màu chuỗi chỉ nằm ở nét vẽ và chấm chú thích.
export const SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];
export const FALLBACK_COLOR = "#707070";

export function fmtDay(iso: string) {
  const [, m, d] = iso.split("-");
  return `${d}/${m}`;
}
export function fmtFull(iso: string) {
  const [y, m, d] = iso.split("-");
  return `${d}/${m}/${y}`;
}
export function fmtNum(v: number, digits = 2) {
  return v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

// Trần/sàn trục y "tròn" (bước 1-2-5 × 10^n) → 5 vạch lưới dễ đọc.
export function niceScale(min: number, max: number, ticks = 4) {
  if (min === max) { min -= 1; max += 1; }
  const raw = (max - min) / ticks;
  const exp = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * exp).find((s) => s >= raw) ?? 10 * exp;
  const lo = Math.floor(min / step) * step;
  const hi = Math.ceil(max / step) * step;
  const out: number[] = [];
  for (let v = lo; v <= hi + step / 2; v += step) out.push(Number(v.toFixed(10)));
  return { lo, hi, ticks: out };
}

// Biểu đồ đường nhiều chuỗi dùng chung cho "chi tiết 1 mã" (có vùng tô + cột khối lượng) và
// "so sánh" (nhiều đường). Crosshair + tooltip theo ngày gần con trỏ nhất.
// `raw`: giá trị gốc (khi `points` đã quy đổi, vd chỉ số 100) để tooltip in đúng đơn vị. `breakAt`: chỉ số điểm
// bắt đầu đoạn mới (không nối với điểm trước — vd VWAP đặt lại khi sang quý).
export type LineSeries = {
  key: string; label: string; color: string; points: (number | null)[]; dashed?: boolean; raw?: (number | null)[]; breakAt?: number[];
};
// Mốc ước tính ở lề phải, SAU phiên cuối (thời điểm tương lai — vd giá CBAM cuối quý): chấm = giá trị ước tính,
// vạch đứng = khoảng tin cậy, `anchor` = giá trị tại phiên cuối để nối nét chấm tới mốc. Cùng trục y với các đường.
export type ChartMarker = {
  key: string; label: string; color: string; value: number; low: number | null; high: number | null;
  anchor?: number | null; axisLabel: string; tip: string[];
};

const MARKER_GAP = 24;

export function LineChart({ dates, series, area, formatY, formatTip, markers = [], height = 300 }: {
  dates: string[]; series: LineSeries[]; area?: boolean; height?: number; markers?: ChartMarker[];
  formatY: (v: number) => string; formatTip: (v: number, s: LineSeries, i: number) => string;
}) {
  const t = useTranslations("adminSrcDash");
  const wrapRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(720);
  const [hover, setHover] = useState<number | null>(null);
  const [hoverMarker, setHoverMarker] = useState<number | null>(null);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  // Lề phải nới ra cho mốc ước tính + nhãn ("CBAM") bên phải chấm.
  const H = height, ML = 56, MR = markers.length ? 30 + markers.length * MARKER_GAP + 12 : 16, MT = 12, MB = 28;
  const iw = Math.max(100, width - ML - MR), ih = H - MT - MB;
  const all = series.flatMap((s) => s.points.filter((v): v is number => v !== null));
  if (dates.length < 2 || all.length === 0) {
    return <div className="flex items-center justify-center text-sm text-muted-light" style={{ height: H }}>{t("notEnoughData")}</div>;
  }
  const markerVals = markers.flatMap((m) => [m.value, m.low, m.high].filter((v): v is number => v !== null));
  const sc = niceScale(Math.min(...all, ...markerVals), Math.max(...all, ...markerVals));
  const x = (i: number) => ML + (i / (dates.length - 1)) * iw;
  const mx = (k: number) => ML + iw + MARKER_GAP * (k + 1);
  const y = (v: number) => MT + ih - ((v - sc.lo) / (sc.hi - sc.lo)) * ih;

  const xTickEvery = Math.max(1, Math.ceil(dates.length / Math.max(2, Math.floor(iw / 80))));

  const onMove = (e: React.MouseEvent<SVGRectElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const rel = (e.clientX - rect.left) / rect.width;
    setHover(Math.max(0, Math.min(dates.length - 1, Math.round(rel * (dates.length - 1)))));
  };

  const tipLeft = hover !== null ? x(hover) : 0;
  const flip = hover !== null && tipLeft > width * 0.6;

  return (
    <div ref={wrapRef} className="relative w-full">
      <svg width={width} height={H} role="img" aria-label={t("chartAria")}>
        {sc.ticks.map((t) => (
          <g key={t}>
            <line x1={ML} x2={ML + iw} y1={y(t)} y2={y(t)} stroke="var(--color-border)" strokeWidth={1} />
            <text x={ML - 8} y={y(t) + 4} textAnchor="end" fontSize={11} fill="var(--color-muted-light)">{formatY(t)}</text>
          </g>
        ))}
        {dates.map((d, i) => i % xTickEvery === 0 && (
          <text key={d} x={x(i)} y={H - 8} textAnchor="middle" fontSize={11} fill="var(--color-muted-light)">{fmtDay(d)}</text>
        ))}
        {series.map((s) => {
          const segs: string[] = [];
          let pen = false;
          s.points.forEach((v, i) => {
            if (v === null) { pen = false; return; }
            if (s.breakAt?.includes(i)) pen = false;
            segs.push(`${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`);
            pen = true;
          });
          const line = segs.join(" ");
          const firstIdx = s.points.findIndex((v) => v !== null);
          let lastIdx = -1;
          s.points.forEach((v, i) => { if (v !== null) lastIdx = i; });
          return (
            <g key={s.key}>
              {area && series.length === 1 && firstIdx >= 0 && (
                <>
                  <defs>
                    <linearGradient id={`area-${s.key}`} x1="0" x2="0" y1="0" y2="1">
                      <stop offset="0%" stopColor={s.color} stopOpacity={0.22} />
                      <stop offset="100%" stopColor={s.color} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <path d={`${line} L${x(lastIdx).toFixed(1)},${y(sc.lo)} L${x(firstIdx).toFixed(1)},${y(sc.lo)} Z`} fill={`url(#area-${s.key})`} />
                </>
              )}
              <path d={line} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" strokeDasharray={s.dashed ? "6 4" : undefined} />
              {lastIdx >= 0 && <circle cx={x(lastIdx)} cy={y(s.points[lastIdx] as number)} r={4} fill={s.color} stroke="var(--color-background)" strokeWidth={2} />}
            </g>
          );
        })}
        {markers.length > 0 && (
          <line x1={ML + iw + 10} x2={ML + iw + 10} y1={MT} y2={MT + ih} stroke="var(--color-border)" strokeWidth={1} strokeDasharray="2 3" />
        )}
        {markers.map((m, k) => {
          const cx = mx(k);
          return (
            <g key={m.key} onMouseEnter={() => setHoverMarker(k)} onMouseLeave={() => setHoverMarker(null)} onClick={() => setHoverMarker(k)}>
              {m.anchor !== undefined && m.anchor !== null && (
                <line x1={x(dates.length - 1)} y1={y(m.anchor)} x2={cx} y2={y(m.value)} stroke={m.color} strokeWidth={1.5} strokeDasharray="1 3" strokeLinecap="round" />
              )}
              {m.low !== null && m.high !== null && (
                <>
                  <line x1={cx} x2={cx} y1={y(m.high)} y2={y(m.low)} stroke={m.color} strokeWidth={4} strokeLinecap="round" opacity={0.35} />
                  <line x1={cx - 5} x2={cx + 5} y1={y(m.high)} y2={y(m.high)} stroke={m.color} strokeWidth={2} strokeLinecap="round" />
                  <line x1={cx - 5} x2={cx + 5} y1={y(m.low)} y2={y(m.low)} stroke={m.color} strokeWidth={2} strokeLinecap="round" />
                </>
              )}
              <circle cx={cx} cy={y(m.value)} r={hoverMarker === k ? 6 : 5} fill={m.color} stroke="var(--color-background)" strokeWidth={2} />
              <text x={cx + 9} y={y(m.value) + 4} fontSize={10.5} fontWeight={600} fill="var(--color-heading)">{m.label}</text>
              <text x={cx} y={H - 8} textAnchor="middle" fontSize={11} fill="var(--color-muted-light)">{m.axisLabel}</text>
              {/* Vùng bắt hover rộng hơn mốc */}
              <rect x={cx - 12} y={MT} width={24} height={ih} fill="transparent" />
            </g>
          );
        })}
        {hover !== null && (
          <>
            <line x1={x(hover)} x2={x(hover)} y1={MT} y2={MT + ih} stroke="var(--color-border-soft)" strokeWidth={1} strokeDasharray="3 3" />
            {series.map((s) => {
              const v = s.points[hover];
              return v === null || v === undefined ? null : (
                <circle key={s.key} cx={x(hover)} cy={y(v)} r={4.5} fill={s.color} stroke="var(--color-background)" strokeWidth={2} />
              );
            })}
          </>
        )}
        <rect x={ML} y={MT} width={iw} height={ih} fill="transparent" onMouseMove={onMove} onMouseLeave={() => setHover(null)} />
      </svg>
      {hover !== null && (
        <div
          className="pointer-events-none absolute top-2 z-10 bg-background border border-border rounded-xl shadow-[var(--shadow-soft)] px-3 py-2 text-[12px]"
          style={flip ? { right: width - tipLeft + 12 } : { left: tipLeft + 12 }}
        >
          <div className="font-semibold text-heading mb-1">{fmtFull(dates[hover])}</div>
          {series.map((s) => {
            const v = s.points[hover];
            return (
              <div key={s.key} className="flex items-center gap-2 text-body tabular-nums whitespace-nowrap">
                <span className="w-2 h-2 rounded-full" style={{ background: s.color }} aria-hidden="true" />
                <span className="text-muted-light">{s.label}</span>
                <span className="ml-auto pl-3 font-semibold">{v === null || v === undefined ? "—" : formatTip(v, s, hover)}</span>
              </div>
            );
          })}
        </div>
      )}
      {hoverMarker !== null && markers[hoverMarker] && (
        <div
          className="pointer-events-none absolute top-2 z-10 max-w-[280px] bg-background border border-border rounded-xl shadow-[var(--shadow-soft)] px-3 py-2 text-[12px]"
          style={{ right: width - mx(hoverMarker) + 14 }}
        >
          {markers[hoverMarker].tip.map((line, i) => (
            <div key={i} className={i === 0 ? "flex items-center gap-2 font-semibold text-heading mb-1" : "text-body tabular-nums"}>
              {i === 0 && <span className="w-2 h-2 rounded-full shrink-0" style={{ background: markers[hoverMarker].color }} aria-hidden="true" />}
              {line}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export const MAX_COMPARE = 4;

// Màu theo thực thể: chỉ số thứ tự trong danh sách instrument (cố định), không phụ thuộc bộ lọc.
export function useColorOf(instruments: Instrument[] | undefined) {
  return useMemo(() => {
    const m: Record<string, string> = {};
    (instruments ?? []).forEach((i, idx) => { m[i.code] = SERIES_COLORS[idx] ?? FALLBACK_COLOR; });
    return m;
  }, [instruments]);
}

// So sánh hiệu suất: mỗi mã quy về chỉ số 100 tại phiên đầu kỳ của chính nó.
export function compareSeries(instruments: Instrument[], codes: string[], colorOf: Record<string, string>) {
  const insts = instruments.filter((i) => codes.includes(i.code) && i.series.length > 1);
  const dates = Array.from(new Set(insts.flatMap((i) => i.series.map((p) => p.date)))).sort();
  const series: LineSeries[] = insts.map((i) => {
    const byDate = new Map(i.series.map((p) => [p.date, p.close]));
    const base = i.series[0].close;
    return {
      key: i.code, label: i.code, color: colorOf[i.code],
      points: dates.map((d) => (byDate.has(d) ? ((byDate.get(d) as number) / base) * 100 : null)),
    };
  });
  return { dates, series };
}

export function CompareChips({ instruments, colorOf, selected, onToggle }: {
  instruments: Instrument[]; colorOf: Record<string, string>; selected: string[]; onToggle: (code: string) => void;
}) {
  const t = useTranslations("adminSrcDash");
  return (
    <div className="flex flex-wrap gap-1.5" role="group" aria-label={t("compareAria")}>
      {instruments.filter((i) => i.code !== "CBAM").map((i) => {
        const on = selected.includes(i.code);
        const disabled = !on && selected.length >= MAX_COMPARE;
        return (
          <button
            key={i.code}
            type="button"
            onClick={() => onToggle(i.code)}
            aria-pressed={on}
            disabled={disabled}
            className={clsx(
              "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-[12px] font-semibold transition-colors duration-200",
              on ? "border-primary bg-tint text-primary-dark" : "border-border-soft text-body hover:bg-surface",
              disabled && "opacity-40 cursor-not-allowed"
            )}
          >
            <span className="w-2 h-2 rounded-full" style={{ background: colorOf[i.code] }} aria-hidden="true" />
            {i.code}
          </button>
        );
      })}
    </div>
  );
}

// Lớp phủ giá CBAM — chỉ khi đang so sánh EUA (giá CBAM = VWAP đấu giá EUA, phải chung gốc chỉ số với EUA).
// Đỏ = VWAP đấu giá (nét đứt), xanh dương đậm = mốc ước tính cuối quý — tách khỏi xanh của đường EUA.
export const VWAP_COLOR = "#e34948";
export const CBAM_MARKER_COLOR = "#1f4fa3";

function quarterOfIso(iso: string) {
  const [y, m] = iso.split("-").map(Number);
  return `Q${Math.floor((m - 1) / 3) + 1} ${y}`;
}

// "Q4 2026" → "2026-12-31"
function quarterEndIso(label: string) {
  const [q, y] = label.replace("Q", "").split(" ").map(Number);
  const end = new Date(Date.UTC(y, 3 * q, 0));
  return end.toISOString().slice(0, 10);
}

// VWAP lũy kế quý theo từng ngày của biểu đồ, quy về chỉ số theo gốc của EUA (cùng trục, so trực tiếp được với đường
// EUA). Ngày không có phiên đấu giá giữ VWAP gần nhất của CÙNG quý; sang quý mới thì ngắt nét cho tới phiên đầu quý.
export function vwapIndexSeries(dates: string[], vwap: AuctionVwapPoint[], base: number) {
  const sorted = [...vwap].sort((a, b) => a.date.localeCompare(b.date));
  const raw: (number | null)[] = [];
  const breakAt: number[] = [];
  let j = -1;
  dates.forEach((d, i) => {
    while (j + 1 < sorted.length && sorted[j + 1].date <= d) j++;
    const p = j >= 0 ? sorted[j] : null;
    raw.push(p && p.quarter === quarterOfIso(d) ? p.vwap : null);
    if (i > 0 && quarterOfIso(d) !== quarterOfIso(dates[i - 1])) breakAt.push(i);
  });
  return { raw, breakAt, points: raw.map((v) => (v === null ? null : (v / base) * 100)) };
}

export function CompareChart({ instruments, colorOf, selected, vwap, cbam, height = 300 }: {
  instruments: Instrument[]; colorOf: Record<string, string>; selected: string[]; height?: number;
  vwap?: AuctionVwapPoint[]; cbam?: CbamEstimate | null;
}) {
  const t = useTranslations("adminSrcDash");
  const chart = useMemo(() => {
    const base = compareSeries(instruments, selected, colorOf);
    const eua = instruments.find((i) => i.code === "EUA" && selected.includes("EUA") && i.series.length > 1);
    let series = [...base.series];
    const markers: ChartMarker[] = [];
    let vwapLast: number | null = null;
    if (eua && base.dates.length > 1) {
      const euaBase = eua.series[0].close;
      if (vwap?.length) {
        const v = vwapIndexSeries(base.dates, vwap, euaBase);
        if (v.raw.some((x) => x !== null)) {
          // Tooltip EUA kèm giá EUR/t để đọc thẳng chênh lệch với VWAP.
          const closes = new Map(eua.series.map((p) => [p.date, p.close]));
          series = series.map((s) => (s.key === "EUA" ? { ...s, raw: base.dates.map((d) => closes.get(d) ?? null) } : s));
          series.push({ key: "__vwap", label: t("vwapLabel"), color: VWAP_COLOR, dashed: true, ...v });
          vwapLast = v.points[v.points.length - 1];
        }
      }
      // Ước tính CBAM cùng quý với phiên cuối của biểu đồ — báo cáo cũ / khoảng thời gian khác quý thì không vẽ.
      const lastDate = base.dates[base.dates.length - 1];
      if (cbam && Number.isFinite(cbam.estimate) && cbam.quarter === quarterOfIso(lastDate)) {
        const idx = (v: number | null) => (v === null ? null : (v / euaBase) * 100);
        const hasCi = cbam.ci_low !== null && cbam.ci_high !== null;
        markers.push({
          key: "cbam",
          label: t("cbamMarkerLabel"),
          color: CBAM_MARKER_COLOR,
          value: (cbam.estimate / euaBase) * 100,
          low: idx(cbam.ci_low),
          high: idx(cbam.ci_high),
          anchor: vwapLast,
          axisLabel: fmtDay(quarterEndIso(cbam.quarter)),
          tip: [
            t("cbamTipTitle", { quarter: cbam.quarter }),
            `${fmtNum(cbam.estimate)} EUR/t`,
            ...(hasCi ? [t("cbamTipCi", { level: cbam.ci_level, low: fmtNum(cbam.ci_low as number), high: fmtNum(cbam.ci_high as number) })] : []),
            t("cbamTipFixed", { done: cbam.auctions_done, total: cbam.auctions_total, pct: cbam.fixed_share_pct.toFixed(0) }),
            ...(cbam.qtd_vwap !== null ? [t("cbamTipVwap", { v: fmtNum(cbam.qtd_vwap) })] : []),
            t("cbamTipMethod"),
          ],
        });
      }
    }
    return { dates: base.dates, series, markers };
  }, [instruments, selected, colorOf, vwap, cbam, t]);

  if (chart.series.length === 0) {
    return <div className="flex items-center justify-center text-sm text-muted-light" style={{ height }}>{t("compareEmpty")}</div>;
  }
  const hasVwap = chart.series.some((s) => s.key === "__vwap");
  const marker = chart.markers[0];
  return (
    <>
      <LineChart
        dates={chart.dates}
        series={chart.series}
        markers={chart.markers}
        height={height}
        formatY={(v) => v.toFixed(0)}
        formatTip={(v, s, i) => {
          const raw = s.raw?.[i];
          const abs = raw !== undefined && raw !== null ? ` · ${fmtNum(raw)} EUR/t` : "";
          // VWAP quy theo gốc EUA nên "%" so với 100 không có nghĩa riêng — chỉ in chỉ số + giá.
          return s.key === "__vwap" ? `${v.toFixed(1)}${abs}` : `${v.toFixed(1)} (${v >= 100 ? "+" : ""}${(v - 100).toFixed(1)}%)${abs}`;
        }}
      />
      {(hasVwap || marker) && (
        <div className="flex flex-wrap gap-x-4 gap-y-1 mt-1 pl-14 text-[11px] text-muted-light">
          {hasVwap && (
            <span className="inline-flex items-center gap-1.5">
              <svg width="18" height="6" aria-hidden="true"><line x1="1" x2="17" y1="3" y2="3" stroke={VWAP_COLOR} strokeWidth="2" strokeDasharray="5 3" /></svg>
              {t("vwapLegend")}
            </span>
          )}
          {marker && cbam && (
            <span className="inline-flex items-center gap-1.5">
              <svg width="10" height="12" aria-hidden="true">
                <line x1="5" x2="5" y1="1" y2="11" stroke={CBAM_MARKER_COLOR} strokeWidth="3" strokeLinecap="round" opacity="0.35" />
                <circle cx="5" cy="6" r="3" fill={CBAM_MARKER_COLOR} />
              </svg>
              {t("cbamLegend", { quarter: cbam.quarter, value: fmtNum(cbam.estimate) })}
              {marker.low !== null && marker.high !== null &&
                t("cbamLegendCi", { level: cbam.ci_level, low: fmtNum(cbam.ci_low as number), high: fmtNum(cbam.ci_high as number) })}
            </span>
          )}
        </div>
      )}
    </>
  );
}
