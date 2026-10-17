"use client";

import type { ReactNode } from "react";
import clsx from "clsx";
import { useLocale, useTranslations } from "next-intl";
import type { ForecastReview, ForecastReviewItem } from "@/lib/types";

// Khối "Dự báo trước: đúng hay sai" ở trang đầu — dữ liệu tính bằng Python (services/forecast_review.py,
// content["2"].forecast_review): %Δ EUA luỹ kế + mô phỏng lệnh (khớp / chạm cắt lỗ / chạm mục tiêu) của
// kịch bản ngắn hạn các báo cáo gần đây. `children` = đoạn "vs_previous" LLM viết (nguyên nhân, điều chỉnh).

function num(n: number | null | undefined, locale: string, sign = false): string {
  if (n === null || n === undefined) return "—";
  const s = `${sign && n > 0 ? "+" : ""}${n.toFixed(2)}`;
  return locale === "en" ? s : s.replace(".", ",");
}

function zone(lo: number, hi: number, locale: string): string {
  return Math.abs(hi - lo) < 0.005 ? num(lo, locale) : `${num(lo, locale)}–${num(hi, locale)}`;
}

function dm(iso?: string | null): string {
  if (!iso) return "";
  const [, m, d] = iso.split("-");
  return `${d}/${m}`;
}

const VERDICT_TONE: Record<ForecastReviewItem["verdict"], string> = {
  correct: "bg-up/10 text-up border-up/30",
  wrong: "bg-down/10 text-down border-down/30",
  unclear: "bg-surface text-muted-light border-border",
  on_track: "bg-up/5 text-up border-up/20 border-dashed",
  diverging: "bg-down/5 text-down border-down/20 border-dashed",
  direction_only: "bg-surface text-up border-up/30 border-dashed",
};

export function ForecastReviewCard({
  review,
  enumLabel,
  children,
}: {
  review: ForecastReview;
  enumLabel: (v: string | null | undefined) => string;
  children?: ReactNode;
}) {
  const t = useTranslations("forecastReview");
  const locale = useLocale();

  return (
    // Khi in: KHÔNG giữ nguyên khối (bảng dài, có calibration/quy tắc chấm → không vừa phần còn lại của trang thì bị đẩy
    // nguyên sang trang sau, để trống trang trước) — chỉ giữ không cắt đôi từng dòng/từng phần.
    <div className="rounded-lg border border-border bg-white overflow-hidden print:overflow-visible [print-color-adjust:exact] [-webkit-print-color-adjust:exact]">
      <div className="flex flex-wrap items-center justify-between gap-2 px-3.5 py-2 bg-surface border-b border-border">
        <h3 className="text-[11px] sm:text-[12px] font-extrabold uppercase tracking-[0.15em] text-label">{t("title")}</h3>
        {review.wrong_in_row >= 2 && (
          <span className="text-[11px] font-bold text-down">{t("wrongInRow", { n: review.wrong_in_row })}</span>
        )}
      </div>

      <ul className="divide-y divide-border">
        {review.items.map((it) => {
          const tr = it.trade;
          return (
            <li key={it.report_date} className="print:break-inside-avoid px-3.5 py-2.5 flex flex-col sm:flex-row sm:items-start gap-2 sm:gap-3">
              {/* Nhãn rộng cố định (vừa nhãn dài nhất "Đang đúng hướng") để cột nội dung các dòng thẳng hàng. */}
              <span className={clsx("self-start shrink-0 sm:w-[8.75rem] text-center whitespace-nowrap font-mono text-[11px] font-bold uppercase tracking-wider px-2 py-0.5 rounded border", VERDICT_TONE[it.verdict])}>
                {t(`verdict.${it.verdict}`)}
              </span>
              <div className="text-[13.5px] leading-[1.55] text-body min-w-0">
                <div>
                  <strong className="text-label">{t("report", { date: dm(it.report_date) })}</strong>{" "}
                  {t("forecast", { direction: enumLabel(it.direction), probability: enumLabel(it.probability) })}
                  {it.high_confidence_miss && <span className="ml-1 font-semibold text-down">{t("highConfidenceMiss")}</span>}
                </div>
                <div className="tabular-nums">
                  {t("move", {
                    from: num(it.ref_close, locale),
                    to: num(it.last_close, locale),
                    pct: num(it.change_pct, locale, true),
                    sessions: it.sessions,
                    date: dm(it.last_date),
                  })}
                </div>
                {tr && (
                  <div className="tabular-nums text-muted-light">
                    {t(`trade.${tr.side}${tr.order_type === "stop" ? "Stop" : ""}`, { zone: zone(tr.entry_low, tr.entry_high, locale), stop: num(tr.stop, locale) })}
                    {tr.target !== null && ` · ${t("trade.target", { target: num(tr.target, locale) })}`}
                    {" → "}
                    <span className={clsx("font-semibold", tr.status === "stopped" ? "text-down" : tr.status === "target_hit" || tr.status === "cancelled_target" ? "text-up" : "text-label")}>
                      {t(`trade.status.${tr.status}`, { date: dm(tr.exit_date || tr.filled_date) })}
                    </span>
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ul>

      {review.calibration?.some((c) => c.total > 0 || (c.direction_only ?? 0) > 0) && (
        <div className="print:break-inside-avoid px-3.5 py-2 border-t border-border text-[12.5px] text-body tabular-nums">
          <span className="font-semibold text-label">{t("calibration")}</span>{" "}
          {review.calibration
            .filter((c) => c.total > 0 || (c.direction_only ?? 0) > 0)
            .map((c) =>
              t("calibrationItem", { probability: enumLabel(c.probability), correct: c.correct, total: c.total })
              + (c.direction_only ? ` ${t("calibrationUnfilled", { n: c.direction_only })}` : ""),
            )
            .join(" · ")}
        </div>
      )}

      {review.rules && (
        <p className="print:break-inside-avoid px-3.5 py-2 border-t border-border text-[11.5px] leading-[1.5] text-muted-light">
          {t("rules", {
            horizon: review.rules.horizon_sessions,
            dir: num(review.rules.direction_threshold_pct, locale),
            side: num(review.rules.sideways_max_pct, locale),
            valid: review.rules.order_valid_sessions,
          })}
        </p>
      )}

      {children && (
        <div className="px-3.5 py-2.5 border-t border-border bg-surface/60">
          <div className="text-[10.5px] font-bold uppercase tracking-wider text-muted-light mb-0.5">{t("lesson")}</div>
          <div className="text-[14px] sm:text-[14.5px] leading-[1.55] text-body">{children}</div>
        </div>
      )}
    </div>
  );
}
