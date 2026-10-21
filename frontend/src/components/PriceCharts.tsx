"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import clsx from "clsx";
import { useTranslations } from "next-intl";

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
export type Dashboard = { days: number; latest_date: string | null; instruments: Instrument[] };

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
export type LineSeries = { key: string; label: string; color: string; points: (number | null)[] };

export function LineChart({ dates, series, area, formatY, formatTip, height = 300 }: {
  dates: string[]; series: LineSeries[]; area?: boolean; height?: number;
  formatY: (v: number) => string; formatTip: (v: number, s: LineSeries) => string;
}) {
  const t = useTranslations("adminSrcDash");
  const wrapRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(720);
  const [hover, setHover] = useState<number | null>(null);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  const H = height, ML = 56, MR = 16, MT = 12, MB = 28;
  const iw = Math.max(100, width - ML - MR), ih = H - MT - MB;
  const all = series.flatMap((s) => s.points.filter((v): v is number => v !== null));
  if (dates.length < 2 || all.length === 0) {
    return <div className="flex items-center justify-center text-sm text-muted-light" style={{ height: H }}>{t("notEnoughData")}</div>;
  }
  const sc = niceScale(Math.min(...all), Math.max(...all));
  const x = (i: number) => ML + (i / (dates.length - 1)) * iw;
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
            <line x1={ML} x2={width - MR} y1={y(t)} y2={y(t)} stroke="var(--color-border)" strokeWidth={1} />
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
              <path d={line} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
              {lastIdx >= 0 && <circle cx={x(lastIdx)} cy={y(s.points[lastIdx] as number)} r={4} fill={s.color} stroke="var(--color-background)" strokeWidth={2} />}
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
                <span className="ml-auto pl-3 font-semibold">{v === null || v === undefined ? "—" : formatTip(v, s)}</span>
              </div>
            );
          })}
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

export function CompareChart({ instruments, colorOf, selected, height = 300 }: {
  instruments: Instrument[]; colorOf: Record<string, string>; selected: string[]; height?: number;
}) {
  const t = useTranslations("adminSrcDash");
  const chart = useMemo(() => compareSeries(instruments, selected, colorOf), [instruments, selected, colorOf]);
  return chart.series.length > 0 ? (
    <LineChart
      dates={chart.dates}
      series={chart.series}
      height={height}
      formatY={(v) => v.toFixed(0)}
      formatTip={(v) => `${v.toFixed(1)} (${v >= 100 ? "+" : ""}${(v - 100).toFixed(1)}%)`}
    />
  ) : (
    <div className="flex items-center justify-center text-sm text-muted-light" style={{ height }}>{t("compareEmpty")}</div>
  );
}
