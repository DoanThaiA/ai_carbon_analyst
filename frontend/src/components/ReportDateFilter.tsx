"use client";

import { ArrowRight, CalendarDays, X } from "lucide-react";
import clsx from "clsx";

export type DateRange = { from: string; to: string };

// Lọc theo khoảng ngày (YYYY-MM-DD so sánh được trực tiếp theo chuỗi). Bỏ trống 1 đầu = không chặn đầu đó.
export function filterByDateRange<T extends { report_date: string }>(items: T[], range: DateRange): T[] {
  return items.filter(
    (r) => (!range.from || r.report_date >= range.from) && (!range.to || r.report_date <= range.to)
  );
}

const pad = (n: number) => String(n).padStart(2, "0");
const toIso = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

// Lối tắt chọn nhanh — tính theo ngày hiện tại của máy người dùng.
const PRESETS: { key: string; label: string; range: () => DateRange }[] = [
  { key: "all", label: "Tất cả", range: () => ({ from: "", to: "" }) },
  {
    key: "7d",
    label: "7 ngày qua",
    range: () => {
      const t = new Date();
      const f = new Date(t);
      f.setDate(t.getDate() - 6);
      return { from: toIso(f), to: toIso(t) };
    },
  },
  {
    key: "30d",
    label: "30 ngày qua",
    range: () => {
      const t = new Date();
      const f = new Date(t);
      f.setDate(t.getDate() - 29);
      return { from: toIso(f), to: toIso(t) };
    },
  },
];

const dateInputCls =
  "h-9 w-full min-w-0 bg-transparent px-2 text-sm text-body outline-none [color-scheme:light] cursor-pointer";

export function ReportDateFilter({
  value,
  onChange,
  shown,
  total,
}: {
  value: DateRange;
  onChange: (next: DateRange) => void;
  shown: number;
  total: number;
}) {
  const active = Boolean(value.from || value.to);
  const activePreset = PRESETS.find((p) => {
    const r = p.range();
    return r.from === value.from && r.to === value.to;
  })?.key;

  return (
    <div className="bg-background border border-border rounded-2xl p-3 sm:p-4 shadow-[var(--shadow-soft)] space-y-3 print:hidden">
      <div className="flex flex-col lg:flex-row lg:items-center gap-3">
        <div className="flex items-center gap-2 text-sm font-bold text-label shrink-0">
          <span className="p-1.5 rounded-lg bg-tint text-primary-dark">
            <CalendarDays size={16} aria-hidden="true" />
          </span>
          Lọc theo ngày
        </div>

        {/* Khoảng ngày: 1 khung liền có viền, focus-within đổi màu viền cả khung */}
        <div className="flex items-center rounded-xl border border-border bg-surface px-2 focus-within:border-primary focus-within:ring-2 focus-within:ring-primary/20 transition-shadow lg:w-[340px]">
          <input
            type="date"
            aria-label="Từ ngày"
            value={value.from}
            max={value.to || undefined}
            onChange={(e) => onChange({ ...value, from: e.target.value })}
            className={dateInputCls}
          />
          <ArrowRight size={15} className="shrink-0 text-muted-light" aria-hidden="true" />
          <input
            type="date"
            aria-label="Đến ngày"
            value={value.to}
            min={value.from || undefined}
            onChange={(e) => onChange({ ...value, to: e.target.value })}
            className={dateInputCls}
          />
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          {PRESETS.map((p) => (
            <button
              key={p.key}
              type="button"
              onClick={() => onChange(p.range())}
              aria-pressed={activePreset === p.key}
              className={clsx(
                "h-8 rounded-full border px-3 text-[13px] font-semibold transition-colors",
                activePreset === p.key
                  ? "bg-primary text-white border-primary"
                  : "bg-background text-body border-border hover:border-primary hover:text-primary-dark"
              )}
            >
              {p.label}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-2 lg:ml-auto">
          <span className="text-[13px] text-muted-light whitespace-nowrap">
            Hiển thị <b className="text-label">{shown}</b>
            {active && <> / {total}</>} báo cáo
          </span>
          {active && (
            <button
              type="button"
              onClick={() => onChange({ from: "", to: "" })}
              className="h-8 inline-flex items-center gap-1 rounded-full px-2.5 text-[13px] font-semibold text-down hover:bg-red-50 transition-colors"
            >
              <X size={14} aria-hidden="true" /> Xoá lọc
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
