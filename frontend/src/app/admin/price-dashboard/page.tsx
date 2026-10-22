"use client";

import { useEffect, useState } from "react";
import { AlertCircle, ArrowDownRight, ArrowUpRight, Minus, LineChart as LineChartIcon, Clock } from "lucide-react";
import clsx from "clsx";
import { useTranslations } from "next-intl";
import { api } from "@/lib/api";
import {
  CompareChart, CompareChips, Dashboard, Instrument, LineChart, MAX_COMPARE, fmtFull, fmtNum, useColorOf,
} from "@/components/PriceCharts";

// Dashboard giá — trực quan hóa bảng `prices` (GET /api/admin/prices/dashboard,
// api/routers/admin_prices.py): ô số từng instrument (giá mới nhất + Δ ngày/Δ tuần + sparkline),
// biểu đồ chi tiết 1 mã, biểu đồ so sánh quy về chỉ số 100 (KHÔNG dùng 2 trục y — các mã khác
// đơn vị/thang đo) và bảng số liệu. Biểu đồ vẽ bằng SVG thuần, giống trang Thống kê hiệu suất.

const RANGES = [30, 90, 180, 365];

const UP = "var(--color-up)";
const DOWN = "var(--color-down)";

// Nhóm instrument đã biết → khoá dịch `cat_<nhóm>`; nhóm lạ hiển thị nguyên giá trị.
const KNOWN_CATEGORIES = ["carbon", "gas", "oil", "coal", "power", "other"];

function fmtPct(v: number | null) {
  if (v === null || v === undefined) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;
}
function fmtVol(v: number | null) {
  if (v === null || v === undefined) return "—";
  return v >= 1000 ? `${(v / 1000).toFixed(1)}K` : String(Math.round(v));
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
  const t = useTranslations("adminSrcDash");
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
            <Clock size={11} aria-hidden="true" /> {t("stale")}
          </span>
        )}
      </div>
      <div className="mt-0.5 text-[12px] text-muted-light truncate" title={inst.name}>{inst.name}</div>
      <div className="mt-2 flex items-baseline gap-1.5">
        <span className="text-[26px] leading-none font-bold text-heading tabular-nums">{fmtNum(inst.latest.close)}</span>
        <span className="text-[11px] text-muted-light">{inst.unit}</span>
      </div>
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-0.5">
        <ChangeBadge pct={inst.latest.day_change_pct} label={t("day")} />
        <ChangeBadge pct={inst.latest.week_change_pct} label={t("week")} />
      </div>
      <div className="mt-2.5"><Sparkline points={closes} color={color} /></div>
      <div className="mt-1 text-[11px] text-muted-light">{t("session", { date: fmtFull(inst.latest.date) })}</div>
    </button>
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
  const t = useTranslations("adminSrcDash");
  const catLabel = (c: string) => (KNOWN_CATEGORIES.includes(c) ? t(`cat_${c}`) : c);
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
      .catch(() => !cancelled && setError(t("loadError")));
    return () => { cancelled = true; };
  }, [days]);

  const colorOf = useColorOf(data?.instruments);

  const sel = data?.instruments.find((i) => i.code === selected) ?? null;

  const toggleCompare = (code: string) =>
    setCompare((cur) => (cur.includes(code) ? cur.filter((c) => c !== code) : cur.length >= MAX_COMPARE ? cur : [...cur, code]));

  // Đang tải = chưa có dữ liệu lần đầu (đổi khoảng thời gian thì giữ dữ liệu cũ tới khi có dữ liệu mới).
  const loading = !data && !error;

  return (
    <div className="space-y-6">
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div>
          <h2 className="text-3xl font-bold uppercase tracking-tight text-heading mb-2">{t("title")}</h2>
          <p className="text-body">
            {t("subtitle")}
            {data?.latest_date && <> · {t("latestSession")} <span className="font-semibold">{fmtFull(data.latest_date)}</span></>}
          </p>
        </div>
        <div className="inline-flex rounded-full border border-border-soft p-0.5 bg-background self-start md:self-auto" role="group" aria-label={t("rangeAria")}>
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
              {t("days", { n: r })}
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
          <p className="text-body text-lg">{t("empty")}</p>
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
              subtitle={`${t("closePrice")}${sel.unit ? ` · ${sel.unit}` : ""}${sel.exchange ? ` · ${sel.exchange}` : ""} · ${catLabel(sel.category)}`}
            >
              <LineChart
                dates={sel.series.map((p) => p.date)}
                series={[{ key: sel.code, label: t("close"), color: colorOf[sel.code], points: sel.series.map((p) => p.close) }]}
                area
                formatY={(v) => fmtNum(v, v % 1 === 0 ? 0 : 2)}
                formatTip={(v) => fmtNum(v)}
              />
            </Card>
          )}

          <Card
            title={t("compareTitle")}
            subtitle={t("compareSubtitle", { max: MAX_COMPARE })}
            right={<CompareChips instruments={data.instruments} colorOf={colorOf} selected={compare} onToggle={toggleCompare} />}
          >
            <CompareChart instruments={data.instruments} colorOf={colorOf} selected={compare} vwap={data.auction_vwap} />
          </Card>

          <Card title={t("tableTitle")} subtitle={t("tableSubtitle")}>
            <div className="overflow-x-auto -mx-1">
              <table className="w-full text-[13px] tabular-nums">
                <thead>
                  <tr className="text-left text-[11.5px] uppercase tracking-wider text-muted-light border-b border-border">
                    <th className="py-2 px-1 font-semibold">{t("colContract")}</th>
                    <th className="py-2 px-1 font-semibold">{t("colGroup")}</th>
                    <th className="py-2 px-1 font-semibold text-right">{t("colClose")}</th>
                    <th className="py-2 px-1 font-semibold text-right">{t("colDayChg")}</th>
                    <th className="py-2 px-1 font-semibold text-right">{t("colWeekChg")}</th>
                    <th className="py-2 px-1 font-semibold text-right">{t("colVolume")}</th>
                    <th className="py-2 px-1 font-semibold text-right">{t("colSession")}</th>
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
                      <td className="py-2.5 px-1 text-body">{catLabel(i.category)}</td>
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
