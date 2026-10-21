"use client";

import { ReactNode, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";

// Biểu đồ chỉ số chất lượng của trang Thống kê hiệu suất (GET /api/admin/stats/performance → chat_quality,
// forecast, cbam — services/performance_stats.py). SVG thuần như 2 biểu đồ lượt xem/hỏi đáp ở page.tsx.
// Màu trạng thái (đúng/sai, tốt/không tốt) là bảng màu trạng thái cố định — luôn kèm chú giải chữ, không dùng
// màu một mình; xám trung tính cho "chưa đánh giá/chưa kết luận". Chữ/nhãn luôn dùng màu chữ.

export const STATUS_GOOD = "#0ca30c";
export const STATUS_BAD = "#d03b3b";
export const STATUS_WARN = "#eda100";
export const NEUTRAL = "#a3a29b";
export const SERIES_1 = "#2a78d6";
export const SERIES_2 = "#eb6834";
export const SERIES_3 = "#1baf7a";

export type ChatQualityItem = { report_date: string; good: number; bad: number; unrated: number; examples: number };
export type ChatQuality = {
  totals: {
    good: number; bad: number; unrated: number; sessions: number; examples: number; rated: number;
    good_rate_pct: number | null; rated_share_pct: number | null;
  };
  items: ChatQualityItem[];
  recent_bad: { report_date: string; reason: string | null; session_id: number }[];
};
export type ForecastItem = {
  report_date: string; direction: string; probability: string | null;
  verdict: "correct" | "wrong" | "direction_only" | "pending"; raw_verdict: string;
  change_pct: number; sessions: number; graded_by: string | null; trade_status: string | null;
};
export type RateCount = { correct: number; total: number };
export type ForecastQuality = {
  totals: {
    correct: number; wrong: number; direction_only: number; pending: number; reports: number;
    hit_rate_pct: number | null; high_confidence_miss: number; design_flaws: number; trades: number; trades_filled: number;
  };
  items: ForecastItem[];
  calibration: { probability: string; correct: number; total: number; direction_only: number }[];
  baseline: { model: RateCount; always_sideways: RateCount; momentum: RateCount & { sessions: number } };
  horizon_sessions: number;
};
export type CbamItem = {
  report_date: string; quarter: string; estimate: number; ci_low: number | null; ci_high: number | null;
  actual: number | null; actual_basis: "official" | "vwap" | "pending"; error_pct: number | null; in_ci: boolean | null;
  week_start: string; week_vwap: number | null;
};
export type CbamQuality = {
  totals: {
    reports: number; scored: number; mape_pct: number | null; bias_pct: number | null;
    in_ci: number; ci_total: number; in_ci_pct: number | null; ci_level: number;
  };
  items: CbamItem[];
  quarters: { quarter: string; actual: number | null; actual_basis: string; reports: number; mape_pct: number | null; last_error_pct: number | null }[];
};

export function fmtDay(iso: string) {
  const [, m, d] = iso.split("-");
  return `${d}/${m}`;
}

export function fmtFull(iso: string) {
  const [y, m, d] = iso.split("-");
  return `${d}/${m}/${y}`;
}

export function fmtNum(v: number | null | undefined, digits = 1, sign = false) {
  if (v === null || v === undefined) return "—";
  const s = v.toLocaleString("vi-VN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return sign && v > 0 ? `+${s}` : s;
}

// Bước "tròn" 1-2-2,5-5 × 10^n ≥ raw.
function niceStep(raw: number) {
  const r = Math.max(raw, 1e-9);
  const exp = Math.pow(10, Math.floor(Math.log10(r)));
  return [1, 2, 2.5, 5, 10].map((m) => m * exp).find((s) => s >= r) ?? 10 * exp;
}

function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

export function ChartCard({ title, subtitle, legend, children }: { title: string; subtitle?: string; legend?: { label: string; color: string; dashed?: boolean; dotted?: boolean }[]; children: ReactNode }) {
  return (
    <div className="bg-background border border-border rounded-2xl p-4 min-w-0">
      <h3 className="text-[15px] font-bold text-heading">{title}</h3>
      {subtitle && <p className="text-[12px] text-muted-light mt-0.5">{subtitle}</p>}
      {legend && legend.length > 0 && (
        <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2 text-[12px] text-body">
          {legend.map((l) => (
            <span key={l.label} className="inline-flex items-center gap-1.5">
              {l.dashed || l.dotted ? (
                <span className={`inline-block w-4 border-t-2 ${l.dotted ? "border-dotted" : "border-dashed"}`} style={{ borderColor: l.color }} aria-hidden="true" />
              ) : (
                <span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ background: l.color }} aria-hidden="true" />
              )}
              {l.label}
            </span>
          ))}
        </div>
      )}
      {children}
    </div>
  );
}

function Tip({ left, width, children }: { left: number; width: number; children: ReactNode }) {
  return (
    <div
      className="absolute top-0 z-10 pointer-events-none bg-background border border-border rounded-md shadow-[var(--shadow-medium)] px-3 py-2 text-[12px] whitespace-nowrap"
      style={{ left: Math.min(Math.max(left, 90), width - 90), transform: "translateX(-50%)" }}
    >
      {children}
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <div className="h-[200px] flex items-center justify-center text-sm text-muted-light text-center px-4">{text}</div>;
}

// Đường cột bo 4px ở đầu xa trục (lên hoặc xuống), chân vuông bám trục.
function barPath(cx: number, w: number, base: number, end: number) {
  const up = end < base;
  const h = Math.abs(base - end);
  const r = Math.min(4, w / 2, h);
  const x0 = cx - w / 2, x1 = cx + w / 2;
  return up
    ? `M${x0},${base} V${end + r} Q${x0},${end} ${x0 + r},${end} H${x1 - r} Q${x1},${end} ${x1},${end + r} V${base} Z`
    : `M${x0},${base} V${end - r} Q${x0},${end} ${x0 + r},${end} H${x1 - r} Q${x1},${end} ${x1},${end - r} V${base} Z`;
}

const H = 220;
const PAD = { l: 40, r: 8, t: 12, b: 26 };

function xLayout(n: number, width: number) {
  const plotW = Math.max(0, width - PAD.l - PAD.r);
  const slot = n > 0 ? plotW / n : 0;
  return {
    slot,
    barW: Math.max(2, Math.min(28, slot - 4)),
    labelStep: Math.max(1, Math.ceil(n / Math.max(1, Math.floor(plotW / 48)))),
    cx: (i: number) => PAD.l + slot * i + slot / 2,
  };
}

// ── Cột chồng (số đếm ≥ 0) ─────────────────────────────────────────────────────

export function StackedBars<T extends { report_date: string }>({
  title, subtitle, items, series, emptyText, tooltip,
}: {
  title: string;
  subtitle?: string;
  items: T[];
  series: { key: string; label: string; color: string; value: (it: T) => number }[];
  emptyText: string;
  tooltip?: (it: T) => ReactNode;
}) {
  const t = useTranslations("adminUsrPerformance");
  const [ref, width] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const n = items.length;
  const plotH = H - PAD.t - PAD.b;
  const total = (it: T) => series.reduce((s, se) => s + se.value(it), 0);
  const maxV = Math.max(1, Math.ceil(niceStep(Math.max(1, ...items.map(total)) / 4)) * 4);
  const y = (v: number) => PAD.t + plotH - (v / maxV) * plotH;
  const { slot, barW, labelStep, cx } = xLayout(n, width);
  const hasData = items.some((it) => total(it) > 0);

  return (
    <ChartCard title={title} subtitle={subtitle} legend={series.length > 1 ? series.map((s) => ({ label: s.label, color: s.color })) : undefined}>
      <div ref={ref} className="relative mt-3">
        {!hasData ? <Empty text={emptyText} /> : (
          <>
            <svg width={width} height={H} role="img" aria-label={title} onMouseLeave={() => setHover(null)}>
              {[0, 1, 2, 3, 4].map((k) => {
                const v = (maxV / 4) * k;
                return (
                  <g key={k}>
                    <line x1={PAD.l} x2={width - PAD.r} y1={y(v)} y2={y(v)} stroke="var(--color-border)" strokeWidth={1} />
                    <text x={PAD.l - 6} y={y(v) + 3.5} textAnchor="end" fontSize={10.5} fill="var(--color-muted-light)">{v}</text>
                  </g>
                );
              })}
              {items.map((it, i) => {
                let acc = 0;
                const segs = series.map((s) => {
                  const v = s.value(it);
                  const seg = { s, from: acc, to: acc + v };
                  acc += v;
                  return seg;
                }).filter((sg) => sg.to > sg.from);
                return (
                  <g key={it.report_date}>
                    <rect x={PAD.l + slot * i} y={PAD.t} width={slot} height={plotH}
                      fill={hover === i ? "var(--color-surface)" : "transparent"} onMouseEnter={() => setHover(i)} />
                    {segs.map((sg, k) => {
                      const top = y(sg.to);
                      // Khe 2px giữa các đoạn chồng; chỉ đoạn trên cùng bo góc.
                      const bottom = y(sg.from) - (k > 0 ? 2 : 0);
                      if (bottom - top <= 0) return null;
                      return k === segs.length - 1 ? (
                        <path key={sg.s.key} d={barPath(cx(i), barW, bottom, top)} fill={sg.s.color}
                          opacity={hover === null || hover === i ? 1 : 0.55} pointerEvents="none" />
                      ) : (
                        <rect key={sg.s.key} x={cx(i) - barW / 2} y={top} width={barW} height={bottom - top} fill={sg.s.color}
                          opacity={hover === null || hover === i ? 1 : 0.55} pointerEvents="none" />
                      );
                    })}
                    {i % labelStep === 0 && (
                      <text x={cx(i)} y={H - 8} textAnchor="middle" fontSize={10.5} fill="var(--color-muted-light)">{fmtDay(it.report_date)}</text>
                    )}
                  </g>
                );
              })}
              <line x1={PAD.l} x2={width - PAD.r} y1={PAD.t + plotH} y2={PAD.t + plotH} stroke="var(--color-border-soft)" strokeWidth={1} />
            </svg>
            {hover !== null && items[hover] && (
              <Tip left={cx(hover)} width={width}>
                <div className="font-semibold text-label">{t("tooltipReport", { date: fmtFull(items[hover].report_date) })}</div>
                {series.map((s) => (
                  <div key={s.key} className="flex items-center gap-1.5 text-body">
                    <span className="inline-block w-2 h-2 rounded-sm" style={{ background: s.color }} aria-hidden="true" />
                    {s.label}: <span className="tabular-nums font-semibold">{s.value(items[hover])}</span>
                  </div>
                ))}
                {tooltip?.(items[hover])}
              </Tip>
            )}
          </>
        )}
      </div>
    </ChartCard>
  );
}

// ── Cột có dấu quanh 0 (%Δ, sai số %) ──────────────────────────────────────────

export function SignedBars<T extends { report_date: string }>({
  title, subtitle, items, value, colorOf, legend, guides, emptyText, tooltip,
}: {
  title: string;
  subtitle?: string;
  items: T[];
  value: (it: T) => number;
  colorOf: (it: T) => string;
  legend: { label: string; color: string; dashed?: boolean }[];
  guides?: number[]; // đường mảnh nét đứt (vd ngưỡng ±1%)
  emptyText: string;
  tooltip: (it: T) => ReactNode;
}) {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const n = items.length;
  const plotH = H - PAD.t - PAD.b;
  const absMax = Math.max(0.5, ...items.map((it) => Math.abs(value(it))), ...(guides ?? []).map(Math.abs));
  const step = niceStep(absMax / 2);
  const maxV = step * Math.ceil(absMax / step);
  const ticks: number[] = [];
  for (let v = -maxV; v <= maxV + 1e-9; v += step) ticks.push(Math.round(v * 1000) / 1000);
  const y = (v: number) => PAD.t + plotH / 2 - (v / maxV) * (plotH / 2);
  const { slot, barW, labelStep, cx } = xLayout(n, width);

  return (
    <ChartCard title={title} subtitle={subtitle} legend={legend}>
      <div ref={ref} className="relative mt-3">
        {n === 0 ? <Empty text={emptyText} /> : (
          <>
            <svg width={width} height={H} role="img" aria-label={title} onMouseLeave={() => setHover(null)}>
              {ticks.map((v) => (
                <g key={v}>
                  <line x1={PAD.l} x2={width - PAD.r} y1={y(v)} y2={y(v)} stroke={v === 0 ? "var(--color-border-soft)" : "var(--color-border)"} strokeWidth={1} />
                  <text x={PAD.l - 6} y={y(v) + 3.5} textAnchor="end" fontSize={10.5} fill="var(--color-muted-light)">
                    {v > 0 ? "+" : ""}{fmtNum(v, Number.isInteger(step) ? 0 : 1)}%
                  </text>
                </g>
              ))}
              {(guides ?? []).map((g) => (
                <line key={g} x1={PAD.l} x2={width - PAD.r} y1={y(g)} y2={y(g)} stroke="var(--color-muted-light)" strokeWidth={1} strokeDasharray="3 3" />
              ))}
              {items.map((it, i) => {
                const v = value(it);
                return (
                  <g key={it.report_date}>
                    <rect x={PAD.l + slot * i} y={PAD.t} width={slot} height={plotH}
                      fill={hover === i ? "var(--color-surface)" : "transparent"} onMouseEnter={() => setHover(i)} />
                    {Math.abs(y(v) - y(0)) >= 1 ? (
                      <path d={barPath(cx(i), barW, y(0), y(v))} fill={colorOf(it)}
                        opacity={hover === null || hover === i ? 1 : 0.55} pointerEvents="none" />
                    ) : (
                      <circle cx={cx(i)} cy={y(0)} r={3} fill={colorOf(it)} pointerEvents="none" />
                    )}
                    {i % labelStep === 0 && (
                      <text x={cx(i)} y={H - 8} textAnchor="middle" fontSize={10.5} fill="var(--color-muted-light)">{fmtDay(it.report_date)}</text>
                    )}
                  </g>
                );
              })}
            </svg>
            {hover !== null && items[hover] && <Tip left={cx(hover)} width={width}>{tooltip(items[hover])}</Tip>}
          </>
        )}
      </div>
    </ChartCard>
  );
}

// ── Ước tính CBAM + khoảng tin cậy ↔ giá chốt quý ────────────────────────────────

export function CbamRangeChart({ data, title, subtitle, emptyText }: { data: CbamQuality; title: string; subtitle?: string; emptyText: string }) {
  const t = useTranslations("adminUsrPerformance");
  const [ref, width] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const items = data.items;
  const n = items.length;
  const plotH = H - PAD.t - PAD.b;
  const vals = items.flatMap((it) => [it.estimate, it.ci_low, it.ci_high, it.actual, it.week_vwap].filter((v): v is number => typeof v === "number"));
  const lo0 = vals.length ? Math.min(...vals) : 0, hi0 = vals.length ? Math.max(...vals) : 1;
  const step = niceStep(Math.max(0.5, (hi0 - lo0) / 4));
  const lo = Math.floor(lo0 / step) * step, hi = Math.max(lo + step, Math.ceil(hi0 / step) * step);
  const ticks: number[] = [];
  for (let v = lo; v <= hi + 1e-9; v += step) ticks.push(Math.round(v * 100) / 100);
  const y = (v: number) => PAD.t + plotH - ((v - lo) / (hi - lo)) * plotH;
  const { slot, labelStep, cx } = xLayout(n, width);
  // Giá chốt: 1 đoạn nét đứt ngang qua các báo cáo cùng quý.
  const runs: { quarter: string; actual: number; from: number; to: number }[] = [];
  items.forEach((it, i) => {
    if (it.actual === null) return;
    const last = runs[runs.length - 1];
    if (last && last.quarter === it.quarter && last.to === i - 1) last.to = i;
    else runs.push({ quarter: it.quarter, actual: it.actual, from: i, to: i });
  });
  // VWAP đấu giá EUA theo tuần: đường bậc thang nét đứt — mỗi tuần 1 đoạn ngang qua các báo cáo trong tuần đó,
  // nối dọc sang tuần kế tiếp nếu liền kề; tuần không có phiên đấu giá thì ngắt đường.
  const weekRuns: { week: string; v: number; from: number; to: number }[] = [];
  items.forEach((it, i) => {
    if (it.week_vwap === null) return;
    const last = weekRuns[weekRuns.length - 1];
    if (last && last.week === it.week_start && last.to === i - 1) last.to = i;
    else weekRuns.push({ week: it.week_start, v: it.week_vwap, from: i, to: i });
  });
  const weekPath = weekRuns.map((r, k) => {
    const x0 = PAD.l + slot * r.from, x1 = PAD.l + slot * (r.to + 1);
    const prev = weekRuns[k - 1];
    const joined = prev && prev.to === r.from - 1;
    return `${joined ? "L" : "M"}${x0},${y(r.v)} H${x1}`;
  }).join(" ");
  const h = hover !== null ? items[hover] : null;

  return (
    <ChartCard
      title={title}
      subtitle={subtitle}
      legend={[
        { label: t("cbamEstimate"), color: SERIES_1 },
        { label: t("cbamCi", { level: data.totals.ci_level }), color: "var(--color-muted-light)" },
        { label: t("cbamActual"), color: SERIES_2, dashed: true },
        ...(weekRuns.length ? [{ label: t("weekVwap"), color: SERIES_3, dotted: true }] : []),
      ]}
    >
      <div ref={ref} className="relative mt-3">
        {n === 0 ? <Empty text={emptyText} /> : (
          <>
            <svg width={width} height={H} role="img" aria-label={title} onMouseLeave={() => setHover(null)}>
              {ticks.map((v) => (
                <g key={v}>
                  <line x1={PAD.l} x2={width - PAD.r} y1={y(v)} y2={y(v)} stroke="var(--color-border)" strokeWidth={1} />
                  <text x={PAD.l - 6} y={y(v) + 3.5} textAnchor="end" fontSize={10.5} fill="var(--color-muted-light)">{fmtNum(v, Number.isInteger(step) ? 0 : 1)}</text>
                </g>
              ))}
              {items.map((it, i) => (
                <g key={it.report_date}>
                  <rect x={PAD.l + slot * i} y={PAD.t} width={slot} height={plotH}
                    fill={hover === i ? "var(--color-surface)" : "transparent"} onMouseEnter={() => setHover(i)} />
                  {i % labelStep === 0 && (
                    <text x={cx(i)} y={H - 8} textAnchor="middle" fontSize={10.5} fill="var(--color-muted-light)">{fmtDay(it.report_date)}</text>
                  )}
                </g>
              ))}
              {runs.map((r) => (
                <line key={`${r.quarter}-${r.from}`} x1={PAD.l + slot * r.from + 2} x2={PAD.l + slot * (r.to + 1) - 2}
                  y1={y(r.actual)} y2={y(r.actual)} stroke={SERIES_2} strokeWidth={2} strokeDasharray="5 3" pointerEvents="none" />
              ))}
              {weekPath && (
                <path d={weekPath} fill="none" stroke={SERIES_3} strokeWidth={2} strokeDasharray="2 3" strokeLinejoin="round" pointerEvents="none" />
              )}
              {items.map((it, i) => (
                <g key={`m-${it.report_date}`} pointerEvents="none" opacity={hover === null || hover === i ? 1 : 0.55}>
                  {it.ci_low !== null && it.ci_high !== null && (
                    <>
                      <line x1={cx(i)} x2={cx(i)} y1={y(it.ci_low)} y2={y(it.ci_high)} stroke="var(--color-muted-light)" strokeWidth={2} />
                      <line x1={cx(i) - 4} x2={cx(i) + 4} y1={y(it.ci_low)} y2={y(it.ci_low)} stroke="var(--color-muted-light)" strokeWidth={2} />
                      <line x1={cx(i) - 4} x2={cx(i) + 4} y1={y(it.ci_high)} y2={y(it.ci_high)} stroke="var(--color-muted-light)" strokeWidth={2} />
                    </>
                  )}
                  <circle cx={cx(i)} cy={y(it.estimate)} r={4.5} fill={SERIES_1} stroke="var(--color-background)" strokeWidth={2} />
                </g>
              ))}
            </svg>
            {h && hover !== null && (
              <Tip left={cx(hover)} width={width}>
                <div className="font-semibold text-label">{t("tooltipReport", { date: fmtFull(h.report_date) })} · {h.quarter}</div>
                <div className="text-body tabular-nums">{t("cbamEstimate")}: <b>{fmtNum(h.estimate, 2)}</b> EUR/t</div>
                {h.ci_low !== null && h.ci_high !== null && (
                  <div className="text-muted-light tabular-nums">{t("cbamCi", { level: data.totals.ci_level })}: {fmtNum(h.ci_low, 2)}–{fmtNum(h.ci_high, 2)}</div>
                )}
                <div className="text-body tabular-nums">
                  {t("cbamActual")}: {h.actual !== null ? <><b>{fmtNum(h.actual, 2)}</b> ({t(`cbamBasis.${h.actual_basis}`)})</> : t("cbamBasis.pending")}
                </div>
                {h.week_vwap !== null && (
                  <div className="text-body tabular-nums">{t("weekVwapOf", { date: fmtFull(h.week_start) })}: <b>{fmtNum(h.week_vwap, 2)}</b></div>
                )}
                {h.error_pct !== null && (
                  <div className="text-body tabular-nums">
                    {t("cbamError")}: <b>{fmtNum(h.error_pct, 2, true)}%</b> · {h.in_ci ? t("cbamInCi") : t("cbamOutCi")}
                  </div>
                )}
              </Tip>
            )}
          </>
        )}
      </div>
    </ChartCard>
  );
}

// ── Thanh ngang tỷ lệ đúng (theo mức xác suất / so với chuẩn ngây thơ) ─────────────

export function RateBars({ title, subtitle, rows, emptyText }: {
  title: string;
  subtitle?: string;
  rows: { label: string; correct: number; total: number; color: string; note?: string }[];
  emptyText: string;
}) {
  const t = useTranslations("adminUsrPerformance");
  const any = rows.some((r) => r.total > 0);
  return (
    <ChartCard title={title} subtitle={subtitle}>
      {!any ? <Empty text={emptyText} /> : (
        <div className="mt-4 space-y-3.5" role="list">
          {rows.map((r) => {
            const pct = r.total ? (r.correct / r.total) * 100 : null;
            return (
              <div key={r.label} role="listitem">
                <div className="flex items-baseline justify-between gap-3 text-[13px]">
                  <span className="font-semibold text-label">{r.label}</span>
                  <span className="tabular-nums text-body">
                    {pct === null ? "—" : <b className="text-heading">{fmtNum(pct, 0)}%</b>}{" "}
                    <span className="text-muted-light">{t("rateCount", { correct: r.correct, total: r.total })}</span>
                  </span>
                </div>
                <div className="mt-1 h-2.5 rounded-full bg-surface overflow-hidden" aria-hidden="true">
                  {pct !== null && pct > 0 && <div className="h-full rounded-full" style={{ width: `${pct}%`, background: r.color }} />}
                </div>
                {r.note && <div className="mt-0.5 text-[11.5px] text-muted-light">{r.note}</div>}
              </div>
            );
          })}
        </div>
      )}
    </ChartCard>
  );
}
