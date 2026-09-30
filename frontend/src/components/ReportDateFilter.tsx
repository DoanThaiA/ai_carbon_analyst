"use client";

import { CalendarDays, X } from "lucide-react";

export type DateRange = { from: string; to: string };

// Lọc theo khoảng ngày (YYYY-MM-DD so sánh được trực tiếp theo chuỗi). Bỏ trống 1 đầu = không chặn đầu đó.
export function filterByDateRange<T extends { report_date: string }>(items: T[], range: DateRange): T[] {
  return items.filter(
    (r) => (!range.from || r.report_date >= range.from) && (!range.to || r.report_date <= range.to)
  );
}

const inputCls =
  "h-10 rounded-lg border border-border bg-background px-3 text-sm text-body focus:outline-none focus:border-primary focus:ring-2 focus:ring-primary/20";

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
  return (
    <div className="flex flex-col sm:flex-row sm:items-end gap-3 bg-background border border-border rounded-2xl p-4">
      <div className="flex items-center gap-2 text-sm font-semibold text-label sm:pb-2.5">
        <CalendarDays size={18} className="text-primary" aria-hidden="true" />
        Lọc theo ngày
      </div>
      <label className="flex flex-col gap-1 text-xs font-semibold text-muted-light">
        Từ ngày
        <input
          type="date"
          value={value.from}
          max={value.to || undefined}
          onChange={(e) => onChange({ ...value, from: e.target.value })}
          className={inputCls}
        />
      </label>
      <label className="flex flex-col gap-1 text-xs font-semibold text-muted-light">
        Đến ngày
        <input
          type="date"
          value={value.to}
          min={value.from || undefined}
          onChange={(e) => onChange({ ...value, to: e.target.value })}
          className={inputCls}
        />
      </label>
      {active && (
        <button
          type="button"
          onClick={() => onChange({ from: "", to: "" })}
          className="h-10 inline-flex items-center justify-center gap-1.5 rounded-lg border border-border px-3 text-sm font-semibold text-body hover:border-primary hover:text-primary-dark transition-colors"
        >
          <X size={15} aria-hidden="true" /> Xoá lọc
        </button>
      )}
      <span className="text-sm text-muted-light sm:ml-auto sm:pb-2.5">
        {active ? `${shown} / ${total} báo cáo` : `${total} báo cáo`}
      </span>
    </div>
  );
}
