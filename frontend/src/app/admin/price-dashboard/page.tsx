"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, ArrowDownRight, ArrowUpRight, Minus, LineChart as LineChartIcon, Clock } from "lucide-react";
import clsx from "clsx";
import { api } from "@/lib/api";

// Dashboard giá — trực quan hóa bảng `prices` (GET /api/admin/prices/dashboard,
// api/routers/admin_prices.py): ô số từng instrument (giá mới nhất + Δ ngày/Δ tuần + sparkline),
// biểu đồ chi tiết 1 mã, biểu đồ so sánh quy về chỉ số 100 (KHÔNG dùng 2 trục y — các mã khác
// đơn vị/thang đo) và bảng số liệu. Biểu đồ vẽ bằng SVG thuần, giống trang Thống kê hiệu suất.

type SeriesPoint = { date: string; close: number; volume: number | null };
type Instrument = {
  code: string;
  name: string;
  category: string;
  unit: string | null;
  exchange: string | null;
  latest: { date: string; close: number; day_change_pct: number | null; week_change_pct: number | null; volume: number | null };
  series: SeriesPoint[];
};
type Dashboard = { days: number; latest_date: string | null; instruments: Instrument[] };

const RANGES = [30, 90, 180, 365];
const MAX_COMPARE = 4;

// Bảng màu phân loại đã kiểm định (khoảng cách mù màu, độ sáng) — gán theo THỨ TỰ CỐ ĐỊNH của
// instrument (không đổi theo bộ lọc, không xoay vòng). Mã thứ 9 trở đi dùng xám trung tính.
// Chữ/nhãn luôn dùng màu chữ; màu chuỗi chỉ nằm ở nét vẽ và chấm chú thích.
const SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];
const FALLBACK_COLOR = "#707070";
const UP = "var(--color-up)";
const DOWN = "var(--color-down)";

const CATEGORY_LABEL: Record<string, string> = {
  carbon: "Carbon", gas: "Khí đốt", oil: "Dầu", coal: "Than", power: "Điện", other: "Khác",
};

function fmtDay(iso: string) {
  const [, m, d] = iso.split("-");
  return `${d}/${m}`;
}
function fmtFull(iso: string) {
  const [y, m, d] = iso.split("-");
  return `${d}/${m}/${y}`;
}
function fmtNum(v: number, digits = 2) {
  return v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
function fmtPct(v: number | null) {
  if (v === null || v === undefined) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;
}
function fmtVol(v: number | null) {
  if (v === null || v === undefined) return "—";
  return v >= 1000 ? `${(v / 1000).toFixed(1)}K` : String(Math.round(v));
}

// Trần/sàn trục y "tròn" (bước 1-2-5 × 10^n) → 5 vạch lưới dễ đọc.
function niceScale(min: number, max: number, ticks = 4) {
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

function ChangeBadge({ pct, label }: { pct: number | null; label: string }) {
  // Trạng thái đi kèm icon + dấu +/- + chữ, không chỉ dựa vào màu.
  const dir = pct === null ? 0 : pct > 0 ? 1 : pct < 0 ? -1 : 0;
  const Icon = dir > 0 ? ArrowUpRight : dir < 0 ? ArrowDownRight : Minus;
  return (
    <span className="inline-flex items-center gap-1 text-[12px] tabular-nums">
      <span className="text-muted-light">{label}</span>
      <span
        className="inline-flex items-center gap-0.5 font-semibold"
        style={{ color: dir > 0 ? UP : dir < 0 ? DOWN : "var(--color-muted-light)" }}
      >
        <Icon size={13} aria-hidden="true" />
        {fmtPct(pct)}
      </span>
    </span>
  );
}

function Sparkline({ points, color }: { points: number[]; color: string }) {
  if (points.length < 2) return <div className="h-10" />;
  const W = 120, H = 40, P = 3;
  const min = Math.min(...points), max = Math.max(...points);
  const x = (i: number) => P + (i / (points.length - 1)) * (W - 2 * P);
  const y = (v: number) => (max === min ? H / 2 : H - P - ((v - min) / (max - min)) * (H - 2 * P));
  const d = points.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-10" preserveAspectRatio="none" aria-hidden="true">
      <path d={d} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function KpiCard({ inst, color, active, stale, onClick }: {
  inst: Instrument; color: string; active: boolean; stale: boolean; onClick: () => void;
}) {
  const closes = inst.series.map((p) => p.close);
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={clsx(
        "text-left bg-background border rounded-2xl px-4 py-3.5 transition-all duration-200 ease-in-out hover:shadow-[var(--shadow-soft)] focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/40",
        active ? "border-primary shadow-[var(--shadow-soft)]" : "border-border"
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: color }} aria-hidden="true" />
          <span className="text-[12px] font-bold uppercase tracking-wider text-muted-light truncate">{inst.code}</span>
        </div>
        {stale && (
          <span className="inline-flex items-center gap-1 text-[10.5px] font-semibold text-warn bg-warn-tint rounded-full px-2 py-0.5 shrink-0">
            <Clock size={11} aria-hidden="true" /> Chưa cập nhật
          </span>
        )}
      </div>
      <div className="mt-0.5 text-[12px] text-muted-light truncate" title={inst.name}>{inst.name}</div>
      <div className="mt-2 flex items-baseline gap-1.5">
        <span className="text-[26px] leading-none font-bold text-heading tabular-nums">{fmtNum(inst.latest.close)}</span>
        <span className="text-[11px] text-muted-light">{inst.unit}</span>
      </div>
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-0.5">
        <ChangeBadge pct={inst.latest.day_change_pct} label="Ngày" />
        <ChangeBadge pct={inst.latest.week_change_pct} label="Tuần" />
      </div>
      <div className="mt-2.5"><Sparkline points={closes} color={color} /></div>
      <div className="mt-1 text-[11px] text-muted-light">Phiên {fmtFull(inst.latest.date)}</div>
    </button>
  );
}

// Biểu đồ đường nhiều chuỗi dùng chung cho "chi tiết 1 mã" (có vùng tô + cột khối lượng) và
// "so sánh" (nhiều đường). Crosshair + tooltip theo ngày gần con trỏ nhất.
type LineSeries = { key: string; label: string; color: string; points: (number | null)[] };

function LineChart({ dates, series, area, formatY, formatTip }: {
  dates: string[]; series: LineSeries[]; area?: boolean;
  formatY: (v: number) => string; formatTip: (v: number, s: LineSeries) => string;
}) {
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

  const H = 300, ML = 56, MR = 16, MT = 12, MB = 28;
  const iw = Math.max(100, width - ML - MR), ih = H - MT - MB;
  const all = series.flatMap((s) => s.points.filter((v): v is number => v !== null));
  if (dates.length < 2 || all.length === 0) {
    return <div className="h-[300px] flex items-center justify-center text-sm text-muted-light">Chưa đủ dữ liệu để vẽ biểu đồ.</div>;
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
      <svg width={width} height={H} role="img" aria-label="Biểu đồ giá">
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

function Card({ title, subtitle, right, children }: { title: string; subtitle?: string; right?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="bg-background border border-border rounded-2xl p-5">
      <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
        <div>
          <h3 className="text-[15px] font-bold text-heading">{title}</h3>
          {subtitle && <p className="text-[12px] text-muted-light mt-0.5">{subtitle}</p>}
        </div>
        {right}
      </div>
      {children}
    </section>
  );
}

export default function PriceDashboardPage() {
  const [days, setDays] = useState(90);
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [compare, setCompare] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;
    api
      .get<Dashboard>(`/api/admin/prices/dashboard?days=${days}`)
      .then((res) => {
        if (cancelled) return;
        setData(res.data);
        setError("");
        const codes = res.data.instruments.map((i) => i.code);
        setSelected((cur) => (cur && codes.includes(cur) ? cur : codes.includes("EUA") ? "EUA" : codes[0] ?? null));
        setCompare((cur) => {
          const kept = cur.filter((c) => codes.includes(c));
          return kept.length ? kept : codes.filter((c) => c !== "CBAM").slice(0, 3);
        });
      })
      .catch(() => !cancelled && setError("Không thể tải dữ liệu giá."));
    return () => { cancelled = true; };
  }, [days]);

  // Màu theo thực thể: chỉ số thứ tự trong danh sách instrument (cố định), không phụ thuộc bộ lọc.
  const colorOf = useMemo(() => {
    const m: Record<string, string> = {};
    (data?.instruments ?? []).forEach((i, idx) => { m[i.code] = SERIES_COLORS[idx] ?? FALLBACK_COLOR; });
    return m;
  }, [data]);

  const sel = data?.instruments.find((i) => i.code === selected) ?? null;

  const compareChart = useMemo(() => {
    if (!data) return null;
    const insts = data.instruments.filter((i) => compare.includes(i.code) && i.series.length > 1);
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
  }, [data, compare, colorOf]);

  const toggleCompare = (code: string) =>
    setCompare((cur) => (cur.includes(code) ? cur.filter((c) => c !== code) : cur.length >= MAX_COMPARE ? cur : [...cur, code]));

  // Đang tải = chưa có dữ liệu lần đầu (đổi khoảng thời gian thì giữ dữ liệu cũ tới khi có dữ liệu mới).
  const loading = !data && !error;

  return (
    <div className="space-y-6">
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div>
          <h2 className="text-3xl font-bold uppercase tracking-tight text-heading mb-2">Dashboard giá</h2>
          <p className="text-body">
            Diễn biến giá đóng cửa các hợp đồng hệ thống theo dõi
            {data?.latest_date && <> · Phiên mới nhất <span className="font-semibold">{fmtFull(data.latest_date)}</span></>}
          </p>
        </div>
        <div className="inline-flex rounded-full border border-border-soft p-0.5 bg-background self-start md:self-auto" role="group" aria-label="Khoảng thời gian">
          {RANGES.map((r) => (
            <button
              key={r}
              onClick={() => setDays(r)}
              aria-pressed={days === r}
              className={clsx(
                "px-3.5 py-1.5 rounded-full text-[13px] font-semibold transition-colors duration-200",
                days === r ? "bg-primary text-white" : "text-body hover:bg-surface"
              )}
            >
              {r} ngày
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg flex items-start gap-3">
          <AlertCircle size={20} className="shrink-0 mt-0.5" />
          <p>{error}</p>
        </div>
      )}

      {loading && !data ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
          {Array.from({ length: 8 }).map((_, i) => <div key={i} className="h-44 bg-surface border border-border rounded-2xl animate-pulse" />)}
        </div>
      ) : data && data.instruments.length === 0 ? (
        <div className="text-center py-20 bg-surface border border-border-soft border-dashed rounded-2xl">
          <LineChartIcon size={48} className="mx-auto text-muted mb-4" />
          <p className="text-body text-lg">Chưa có dữ liệu giá nào trong hệ thống.</p>
        </div>
      ) : data && (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
            {data.instruments.map((inst) => (
              <KpiCard
                key={inst.code}
                inst={inst}
                color={colorOf[inst.code]}
                active={inst.code === selected}
                stale={inst.code !== "CBAM" && !!data.latest_date && inst.latest.date < data.latest_date}
                onClick={() => setSelected(inst.code)}
              />
            ))}
          </div>

          {sel && (
            <Card
              title={`${sel.name} (${sel.code})`}
              subtitle={`Giá đóng cửa${sel.unit ? ` · ${sel.unit}` : ""}${sel.exchange ? ` · ${sel.exchange}` : ""} · ${CATEGORY_LABEL[sel.category] ?? sel.category}`}
            >
              <LineChart
                dates={sel.series.map((p) => p.date)}
                series={[{ key: sel.code, label: "Đóng cửa", color: colorOf[sel.code], points: sel.series.map((p) => p.close) }]}
                area
                formatY={(v) => fmtNum(v, v % 1 === 0 ? 0 : 2)}
                formatTip={(v) => fmtNum(v)}
              />
            </Card>
          )}

          <Card
            title="So sánh hiệu suất"
            subtitle={`Quy về chỉ số 100 tại đầu kỳ — so sánh các mã khác đơn vị trên cùng 1 trục (chọn tối đa ${MAX_COMPARE})`}
            right={
              <div className="flex flex-wrap gap-1.5" role="group" aria-label="Chọn mã so sánh">
                {data.instruments.filter((i) => i.code !== "CBAM").map((i) => {
                  const on = compare.includes(i.code);
                  const disabled = !on && compare.length >= MAX_COMPARE;
                  return (
                    <button
                      key={i.code}
                      onClick={() => toggleCompare(i.code)}
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
            }
          >
            {compareChart && compareChart.series.length > 0 ? (
              <LineChart
                dates={compareChart.dates}
                series={compareChart.series}
                formatY={(v) => v.toFixed(0)}
                formatTip={(v) => `${v.toFixed(1)} (${v >= 100 ? "+" : ""}${(v - 100).toFixed(1)}%)`}
              />
            ) : (
              <div className="h-[200px] flex items-center justify-center text-sm text-muted-light">Chọn ít nhất 1 mã có đủ dữ liệu để so sánh.</div>
            )}
          </Card>

          <Card title="Bảng số liệu" subtitle="Phiên mới nhất của từng hợp đồng">
            <div className="overflow-x-auto -mx-1">
              <table className="w-full text-[13px] tabular-nums">
                <thead>
                  <tr className="text-left text-[11.5px] uppercase tracking-wider text-muted-light border-b border-border">
                    <th className="py-2 px-1 font-semibold">Hợp đồng</th>
                    <th className="py-2 px-1 font-semibold">Nhóm</th>
                    <th className="py-2 px-1 font-semibold text-right">Giá đóng cửa</th>
                    <th className="py-2 px-1 font-semibold text-right">Δ Ngày</th>
                    <th className="py-2 px-1 font-semibold text-right">Δ Tuần</th>
                    <th className="py-2 px-1 font-semibold text-right">Khối lượng</th>
                    <th className="py-2 px-1 font-semibold text-right">Phiên</th>
                  </tr>
                </thead>
                <tbody>
                  {data.instruments.map((i) => (
                    <tr key={i.code} onClick={() => setSelected(i.code)} className="border-b border-border/60 last:border-0 hover:bg-surface cursor-pointer">
                      <td className="py-2.5 px-1">
                        <span className="inline-flex items-center gap-2">
                          <span className="w-2 h-2 rounded-full" style={{ background: colorOf[i.code] }} aria-hidden="true" />
                          <span className="font-semibold text-heading">{i.code}</span>
                          <span className="text-muted-light hidden md:inline">{i.name}</span>
                        </span>
                      </td>
                      <td className="py-2.5 px-1 text-body">{CATEGORY_LABEL[i.category] ?? i.category}</td>
                      <td className="py-2.5 px-1 text-right text-heading font-semibold">{fmtNum(i.latest.close)} <span className="text-muted-light font-normal text-[11px]">{i.unit}</span></td>
                      <td className="py-2.5 px-1 text-right" style={{ color: (i.latest.day_change_pct ?? 0) > 0 ? UP : (i.latest.day_change_pct ?? 0) < 0 ? DOWN : undefined }}>{fmtPct(i.latest.day_change_pct)}</td>
                      <td className="py-2.5 px-1 text-right" style={{ color: (i.latest.week_change_pct ?? 0) > 0 ? UP : (i.latest.week_change_pct ?? 0) < 0 ? DOWN : undefined }}>{fmtPct(i.latest.week_change_pct)}</td>
                      <td className="py-2.5 px-1 text-right text-body">{fmtVol(i.latest.volume)}</td>
                      <td className="py-2.5 px-1 text-right text-body">{fmtFull(i.latest.date)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
