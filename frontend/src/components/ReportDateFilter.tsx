"use client";

import { ArrowRight, CalendarDays, X } from "lucide-react";
import clsx from "clsx";

// weekdays: các thứ được giữ lại (0 = Chủ nhật … 6 = Thứ 7, theo Date.getDay()); rỗng/bỏ trống = mọi thứ.
export type DateRange = { from: string; to: string; weekdays?: number[] };

// Thứ của "YYYY-MM-DD" theo lịch địa phương (tránh lệch múi giờ của new Date("YYYY-MM-DD") = UTC).
const weekdayOf = (iso: string) => {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).getDay();
};

// Lọc theo khoảng ngày (YYYY-MM-DD so sánh được trực tiếp theo chuỗi). Bỏ trống 1 đầu = không chặn đầu đó.
export function filterByDateRange<T extends { report_date: string }>(items: T[], range: DateRange): T[] {
  const days = range.weekdays ?? [];
  return items.filter(
    (r) =>
      (!range.from || r.report_date >= range.from) &&
      (!range.to || r.report_date <= range.to) &&
      (days.length === 0 || days.includes(weekdayOf(r.report_date)))
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

// Thứ hiển thị từ Thứ 2 → Chủ nhật (giá trị = Date.getDay()).
const WEEKDAYS: { day: number; label: string; full: string }[] = [
  { day: 1, label: "T2", full: "Thứ Hai" },
  { day: 2, label: "T3", full: "Thứ Ba" },
  { day: 3, label: "T4", full: "Thứ Tư" },
  { day: 4, label: "T5", full: "Thứ Năm" },
  { day: 5, label: "T6", full: "Thứ Sáu" },
  { day: 6, label: "T7", full: "Thứ Bảy" },
  { day: 0, label: "CN", full: "Chủ nhật" },
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
  const weekdays = value.weekdays ?? [];
  const active = Boolean(value.from || value.to || weekdays.length);
  const toggleWeekday = (day: number) =>
    onChange({
      ...value,
      weekdays: weekdays.includes(day) ? weekdays.filter((d) => d !== day) : [...weekdays, day],
    });
  const activePreset = PRESETS.find((p) => {
    const r = p.range();
    return r.from === value.from && r.to === value.to && weekdays.length === 0;
  })?.key;

  return (
    <div className="bg-primary/[0.04] border border-primary/15 rounded-2xl p-3 sm:p-4 space-y-3 print:hidden">
      <div className="flex flex-col lg:flex-row lg:items-center gap-3">
        <div className="flex items-center gap-2 text-sm font-bold text-label shrink-0">
          <span className="p-1.5 rounded-lg bg-primary/10 text-primary-dark">
            <CalendarDays size={16} aria-hidden="true" />
          </span>
          Lọc theo ngày
        </div>

        {/* Khoảng ngày: 1 khung liền có viền, focus-within đổi màu viền cả khung */}
        <div className="flex items-center rounded-xl border border-primary/20 bg-background/70 px-2 focus-within:border-primary/60 focus-within:ring-2 focus-within:ring-primary/10 transition-shadow lg:w-[340px]">
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
              onClick={() => onChange({ ...p.range(), weekdays })}
              aria-pressed={activePreset === p.key}
              className={clsx(
                "h-8 rounded-full border px-3 text-[13px] font-semibold transition-colors",
                activePreset === p.key
                  ? "bg-primary/15 text-primary-dark border-primary/40"
                  : "bg-transparent text-body border-primary/20 hover:bg-primary/10 hover:text-primary-dark"
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
              onClick={() => onChange({ from: "", to: "", weekdays: [] })}
              className="h-8 inline-flex items-center gap-1 rounded-full px-2.5 text-[13px] font-semibold text-down hover:bg-down/10 transition-colors"
            >
              <X size={14} aria-hidden="true" /> Xoá lọc
            </button>
          )}
        </div>
      </div>

      {/* Lọc theo thứ: chọn nhiều thứ cùng lúc; không chọn = tất cả các thứ */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-bold text-label shrink-0">Lọc theo thứ</span>
        <div className="flex flex-wrap items-center gap-1.5">
          {WEEKDAYS.map(({ day, label, full }) => {
            const on = weekdays.includes(day);
            return (
              <button
                key={day}
                type="button"
                title={full}
                aria-label={full}
                aria-pressed={on}
                onClick={() => toggleWeekday(day)}
                className={clsx(
                  "h-8 min-w-[2.5rem] rounded-full border px-3 text-[13px] font-semibold transition-colors",
                  on
                    ? "bg-primary/15 text-primary-dark border-primary/40"
                    : "bg-transparent text-body border-primary/20 hover:bg-primary/10 hover:text-primary-dark"
                )}
              >
                {label}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
