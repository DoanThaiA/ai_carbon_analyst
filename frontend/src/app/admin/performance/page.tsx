"use client";

import { useEffect, useRef, useState } from "react";
import { AlertCircle, BarChart3 } from "lucide-react";
import clsx from "clsx";
import { api } from "@/lib/api";

// Thống kê hiệu suất Jenny — theo từng báo cáo ngày đã phát hành: lượt xem và
// lượt hỏi đáp (GET /api/admin/stats/performance, api/routers/admin_stats.py).
// 2 số đo khác thang đo → 2 biểu đồ cột RIÊNG cùng trục ngày (không dùng biểu đồ
// 2 trục y), kèm hàng ô số tổng quan và bảng số liệu.

type PerfItem = { report_date: string; views: number; unique_viewers: number; questions: number; sessions: number };
type PerfResponse = {
  days: number;
  totals: { reports: number; views: number; unique_viewers: number; questions: number; sessions: number };
  items: PerfItem[];
};

const RANGES = [14, 30, 90];

// Màu chuỗi số liệu — slot 1 (xanh dương) & 2 (cam) của bảng màu phân loại đã
// kiểm định độ tương phản/mù màu. Chữ/nhãn luôn dùng màu chữ, không dùng màu chuỗi.
const COLOR_VIEWS = "#2a78d6";
const COLOR_QUESTIONS = "#eb6834";

function fmtDay(iso: string) {
  const [, m, d] = iso.split("-");
  return `${d}/${m}`;
}

function fmtFull(iso: string) {
  const [y, m, d] = iso.split("-");
  return `${d}/${m}/${y}`;
}

// Trần trục y = 4 × bước "tròn" (1-2-5 × 10^n, tối thiểu 1) → 5 vạch lưới luôn là
// số nguyên dễ đọc (0, 5, 10, 15, 20...).
function niceMax(v: number) {
  const raw = Math.max(1, v / 4);
  const exp = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * exp).find((s) => s >= raw) ?? 10 * exp;
  return Math.max(1, Math.ceil(step)) * 4;
}

function StatTile({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="bg-background border border-border rounded-2xl px-4 py-3.5">
      <div className="text-[12px] font-semibold uppercase tracking-wider text-muted-light">{label}</div>
      <div className="mt-1 text-[28px] leading-none font-bold text-heading tabular-nums">{value}</div>
      {hint && <div className="mt-1.5 text-[12px] text-muted-light">{hint}</div>}
    </div>
  );
}

function BarChart({
  title,
  unit,
  color,
  items,
  valueOf,
  detailOf,
}: {
  title: string;
  unit: string;
  color: string;
  items: PerfItem[];
  valueOf: (it: PerfItem) => number;
  detailOf: (it: PerfItem) => string;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  const [hover, setHover] = useState<number | null>(null);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  const H = 220;
  const padL = 36, padR = 8, padT = 12, padB = 26;
  const plotW = Math.max(0, width - padL - padR);
  const plotH = H - padT - padB;
  const n = items.length;
  const maxV = niceMax(Math.max(0, ...items.map(valueOf)));
  const ticks = [0, 1, 2, 3, 4].map((k) => (maxV / 4) * k);
  const slot = n > 0 ? plotW / n : 0;
  // Cột mảnh: tối đa 28px, luôn chừa khoảng hở giữa các cột.
  const barW = Math.max(2, Math.min(28, slot - 4));
  // Nhãn ngày: thưa ra để không đè nhau (~48px mỗi nhãn).
  const labelStep = Math.max(1, Math.ceil(n / Math.max(1, Math.floor(plotW / 48))));
  const y = (v: number) => padT + plotH - (v / maxV) * plotH;

  return (
    <div className="bg-background border border-border rounded-2xl p-4">
      <h3 className="text-[15px] font-bold text-heading">{title}</h3>
      <div ref={wrapRef} className="relative mt-3">
        {n === 0 ? (
          <div className="h-[220px] flex items-center justify-center text-sm text-muted-light">Chưa có báo cáo nào trong kỳ.</div>
        ) : (
          <>
            <svg width={width} height={H} role="img" aria-label={title} onMouseLeave={() => setHover(null)}>
              {/* Lưới + trục y — mờ, lùi về sau */}
              {ticks.map((t) => (
                <g key={t}>
                  <line x1={padL} x2={width - padR} y1={y(t)} y2={y(t)} stroke="var(--color-border)" strokeWidth={1} />
                  <text x={padL - 6} y={y(t) + 3.5} textAnchor="end" fontSize={10.5} fill="var(--color-muted-light)">
                    {t}
                  </text>
                </g>
              ))}
              {items.map((it, i) => {
                const v = valueOf(it);
                const cx = padL + slot * i + slot / 2;
                const top = y(v);
                const h = padT + plotH - top;
                const r = Math.min(4, barW / 2, h);
                return (
                  <g key={it.report_date}>
                    {/* Vùng bắt chuột rộng hơn cột */}
                    <rect
                      x={padL + slot * i}
                      y={padT}
                      width={slot}
                      height={plotH}
                      fill={hover === i ? "var(--color-surface)" : "transparent"}
                      onMouseEnter={() => setHover(i)}
                    />
                    {v > 0 && (
                      // Cột bo 4px ở đầu, chân cột vuông bám trục
                      <path
                        d={`M${cx - barW / 2},${padT + plotH} V${top + r} Q${cx - barW / 2},${top} ${cx - barW / 2 + r},${top} H${cx + barW / 2 - r} Q${cx + barW / 2},${top} ${cx + barW / 2},${top + r} V${padT + plotH} Z`}
                        fill={color}
                        opacity={hover === null || hover === i ? 1 : 0.55}
                        pointerEvents="none"
                      />
                    )}
                    {i % labelStep === 0 && (
                      <text x={cx} y={H - 8} textAnchor="middle" fontSize={10.5} fill="var(--color-muted-light)">
                        {fmtDay(it.report_date)}
                      </text>
                    )}
                  </g>
                );
              })}
              <line x1={padL} x2={width - padR} y1={padT + plotH} y2={padT + plotH} stroke="var(--color-border-soft)" strokeWidth={1} />
            </svg>
            {hover !== null && items[hover] && (
              <div
                className="absolute top-0 pointer-events-none bg-background border border-border rounded-md shadow-[var(--shadow-medium)] px-3 py-2 text-[12px] whitespace-nowrap"
                style={{
                  left: Math.min(Math.max(padL + slot * hover + slot / 2, 70), width - 70),
                  transform: "translateX(-50%)",
                }}
              >
                <div className="font-semibold text-label">Báo cáo {fmtFull(items[hover].report_date)}</div>
                <div className="flex items-center gap-1.5 text-body">
                  <span className="inline-block w-2 h-2 rounded-sm" style={{ background: color }} aria-hidden="true" />
                  <span className="tabular-nums font-semibold">{valueOf(items[hover])}</span> {unit}
                </div>
                <div className="text-muted-light">{detailOf(items[hover])}</div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export default function AdminPerformancePage() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<PerfResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    api
      .get("/api/admin/stats/performance", { params: { days } })
      .then((res) => {
        if (!cancelled) setData(res.data);
      })
      .catch(() => {
        if (!cancelled) setError("Không thể tải số liệu thống kê.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [days]);

  const t = data?.totals;
  const items = data?.items ?? [];
  const avgQuestions = t && t.reports > 0 ? (t.questions / t.reports).toFixed(1) : "0";

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-3xl font-bold uppercase tracking-tight text-heading mb-2">Thống Kê Hiệu Suất</h2>
          <p className="text-body">Mức độ đồng nghiệp đọc báo cáo và hỏi đáp với Jenny trên từng báo cáo ngày</p>
        </div>
        {/* Bộ lọc khoảng thời gian — 1 hàng phía trên biểu đồ */}
        <div className="inline-flex rounded-lg border border-border bg-background p-0.5" role="group" aria-label="Khoảng thời gian">
          {RANGES.map((r) => (
            <button
              key={r}
              type="button"
              onClick={() => setDays(r)}
              aria-pressed={days === r}
              className={clsx(
                "px-3 py-1.5 rounded-md text-sm font-semibold transition-colors",
                days === r ? "bg-tint text-primary-dark" : "text-body hover:bg-surface"
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
        <div className="bg-background border border-border rounded-2xl h-64 animate-pulse" />
      ) : t ? (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <StatTile label="Báo cáo đã phát hành" value={t.reports} hint={`Trong ${days} ngày gần nhất`} />
            <StatTile label="Lượt xem" value={t.views} hint={`${t.unique_viewers} đồng nghiệp đã xem`} />
            <StatTile label="Lượt hỏi đáp" value={t.questions} hint={`${t.sessions} phiên hỏi đáp`} />
            <StatTile label="Hỏi đáp / báo cáo" value={avgQuestions} hint="Trung bình mỗi báo cáo" />
          </div>

          <div className={clsx("grid grid-cols-1 xl:grid-cols-2 gap-4 transition-opacity", loading && "opacity-60")}>
            <BarChart
              title="Lượt xem theo báo cáo"
              unit="lượt xem"
              color={COLOR_VIEWS}
              items={items}
              valueOf={(it) => it.views}
              detailOf={(it) => `${it.unique_viewers} người xem`}
            />
            <BarChart
              title="Lượt hỏi đáp theo báo cáo"
              unit="câu hỏi"
              color={COLOR_QUESTIONS}
              items={items}
              valueOf={(it) => it.questions}
              detailOf={(it) => `${it.sessions} phiên hỏi đáp`}
            />
          </div>

          {/* Bảng số liệu — đọc chính xác từng con số (mới nhất lên đầu) */}
          <div className="bg-background border border-border rounded-2xl overflow-hidden overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-muted-light text-xs uppercase tracking-wider">
                  <th className="px-4 py-3 font-semibold">Báo cáo ngày</th>
                  <th className="px-4 py-3 font-semibold text-right">Lượt xem</th>
                  <th className="px-4 py-3 font-semibold text-right">Người xem</th>
                  <th className="px-4 py-3 font-semibold text-right">Lượt hỏi đáp</th>
                  <th className="px-4 py-3 font-semibold text-right">Phiên hỏi đáp</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {items.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="px-4 py-10 text-center text-muted-light">
                      <BarChart3 size={32} className="mx-auto mb-2 text-muted" />
                      Chưa có báo cáo nào được phát hành trong kỳ này.
                    </td>
                  </tr>
                ) : (
                  [...items].reverse().map((it) => (
                    <tr key={it.report_date}>
                      <td className="px-4 py-2.5 font-semibold text-label whitespace-nowrap">{fmtFull(it.report_date)}</td>
                      <td className="px-4 py-2.5 text-right tabular-nums">{it.views}</td>
                      <td className="px-4 py-2.5 text-right tabular-nums">{it.unique_viewers}</td>
                      <td className="px-4 py-2.5 text-right tabular-nums">{it.questions}</td>
                      <td className="px-4 py-2.5 text-right tabular-nums">{it.sessions}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>

          <p className="text-[12px] text-muted-light">
            Lượt xem chỉ tính đồng nghiệp (không tính admin); cùng 1 người mở lại cùng báo cáo trong 30 phút chỉ tính 1 lượt.
            Số liệu lượt xem bắt đầu được ghi nhận từ khi cập nhật tính năng này.
          </p>
        </>
      ) : null}
    </div>
  );
}
