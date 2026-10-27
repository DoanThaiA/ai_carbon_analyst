"use client";

import type { ReactNode } from "react";
import clsx from "clsx";
import { useLocale, useTranslations } from "next-intl";
import type { ForecastReview, ForecastReviewItem } from "@/lib/types";

// Khối "Dự báo trước: đúng hay sai" ở trang đầu — dữ liệu tính bằng Python (services/forecast_review.py,
// content["2"].forecast_review): %Δ EUA luỹ kế + mô phỏng lệnh (khớp / chạm cắt lỗ / chạm mục tiêu) của
// kịch bản ngắn hạn các báo cáo gần đây. Mỗi dòng kèm thanh %Δ (gộp biểu đồ cột cũ vào cùng 1 khung).
// `children` = đoạn "vs_previous" LLM viết (nguyên nhân, điều chỉnh).

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
  showTitle = true,
}: {
  review: ForecastReview;
  enumLabel: (v: string | null | undefined) => string;
  children?: ReactNode;
  // false khi nơi gọi đã có tiêu đề riêng (mục "Review dự báo và khuyến nghị") — chỉ giữ cảnh báo sai liên tiếp.
  showTitle?: boolean;
}) {
  const t = useTranslations("forecastReview");
  const locale = useLocale();
  const wrongStreak = review.wrong_in_row >= 2;
  const scale = Math.max(1, ...review.items.map((i) => Math.abs(i.change_pct)));
  const lastDate = review.items.reduce((d, i) => (i.last_date > d ? i.last_date : d), "");
  const items = [...review.items].sort((a, b) => a.report_date.localeCompare(b.report_date));

  return (
    // Khi in: KHÔNG giữ nguyên khối (bảng dài → không vừa phần còn lại của trang thì bị đẩy
    // nguyên sang trang sau, để trống trang trước) — chỉ giữ không cắt đôi từng dòng/từng phần.
    <div className="rounded-lg border border-border bg-white overflow-hidden print:overflow-visible [print-color-adjust:exact] [-webkit-print-color-adjust:exact]">
      {(showTitle || wrongStreak) && (
        <div className="flex flex-wrap items-center justify-between gap-2 px-3.5 py-2 bg-surface border-b border-border">
          {showTitle && <h3 className="text-[11px] sm:text-[12px] font-extrabold uppercase tracking-[0.15em] text-label">{t("title")}</h3>}
          {wrongStreak && (
            <span className="text-[11px] font-bold text-down">{t("wrongInRow", { n: review.wrong_in_row })}</span>
          )}
        </div>
      )}

      {/* Mỗi báo cáo 1 cột, cũ → mới từ trái sang phải. Khung báo cáo ≥ @7xl (tối đa REVIEW_MAX_ITEMS = 8 báo cáo):
          tất cả trên 1 hàng; @3xl–@7xl và khi in: 4 cột/hàng; hẹp hơn: 2 cột. Mỗi cột chia 3 hàng con (subgrid) —
          tiêu đề + nhãn | cột %Δ | chi tiết — để cột %Δ của các báo cáo cùng hàng thẳng nhau dù tiêu đề dài ngắn khác
          nhau. Viền: mỗi ô kẻ trái + trên, lưới lùi -1px để viền ở mép bị khung cắt đi.
          Khi in: flex-wrap thay lưới (basis = bề rộng cột) — Chrome không ngắt trang giữa các hàng của lưới subgrid nên
          cả khung bị đẩy nguyên sang trang sau; flex-wrap ngắt được giữa 2 hàng thẻ. Tiêu đề cao tối thiểu 2 dòng để cột
          %Δ các thẻ cùng hàng vẫn thẳng nhau. */}
      <div className="overflow-x-auto print:overflow-hidden">
        <ul className="-ml-px -mt-px grid grid-cols-2 @3xl/report:grid-cols-4 print:flex print:flex-wrap not-print:@7xl/report:grid-flow-col not-print:@7xl/report:grid-cols-none not-print:@7xl/report:grid-rows-[auto_auto_1fr] not-print:@7xl/report:auto-cols-[minmax(9rem,1fr)]">
          {items.map((it) => {
            const tr = it.trade;
            const up = it.change_pct >= 0;
            const barPct = Math.min(Math.abs(it.change_pct) / scale, 1) * 50;
            return (
              <li key={it.report_date} className="row-span-3 grid min-w-0 basis-1/2 @3xl/report:basis-1/4 grid-rows-subgrid content-start gap-y-2 border-l border-t border-border px-3 py-2.5 print:break-inside-avoid">
                <div className="flex flex-wrap content-start items-center gap-x-1.5 gap-y-1 print:min-h-[2.6rem]">
                  <strong className="text-[13.5px] text-label tabular-nums">{t("column", { date: dm(it.report_date) })}</strong>
                  <span className={clsx("break-words font-mono text-[10px] leading-snug font-bold uppercase tracking-wider px-1.5 py-px rounded border", VERDICT_TONE[it.verdict])}>
                    {t(`verdict.${it.verdict}`)}
                  </span>
                </div>

                <div className="flex items-center gap-2.5">
                  <div className="relative h-14 w-3.5 shrink-0 rounded-sm bg-surface" aria-hidden="true">
                    <span className="absolute inset-x-[-3px] top-1/2 h-px bg-border" />
                    <span
                      className={clsx("absolute inset-x-0 min-h-[2px]", up ? "bottom-1/2 rounded-t-sm bg-up" : "top-1/2 rounded-b-sm bg-down")}
                      style={{ height: `${barPct}%` }}
                    />
                  </div>
                  <div className="min-w-0 leading-tight">
                    <div className={clsx("text-[16px] font-bold tabular-nums", up ? "text-up" : "text-down")}>
                      {num(it.change_pct, locale, true)}%
                    </div>
                    <div className="mt-0.5 text-[12px] text-muted-light">
                      {t("forecastShort", { direction: enumLabel(it.direction), probability: enumLabel(it.probability) })}
                    </div>
                  </div>
                </div>

                <div className="text-[12.5px] leading-[1.5] text-body min-w-0">
                  {it.high_confidence_miss && <div className="font-semibold text-down">{t("highConfidenceMiss")}</div>}
                  <div className="tabular-nums">
                    {t("move", {
                      from: num(it.ref_close, locale),
                      to: num(it.last_close, locale),
                      pct: num(it.change_pct, locale, true),
                      sessions: it.sessions,
                      date: dm(it.last_date),
                    })}
                    {it.max_excursion_pct !== undefined && Math.abs(it.max_excursion_pct - it.change_pct) >= 0.5 &&
                      ` · ${t("maxExcursion", { pct: num(it.max_excursion_pct, locale, true) })}`}
                    {it.graded_by && <span className="text-muted-light"> · {t(`gradedBy.${it.graded_by}`)}</span>}
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
                  {(it.design_flaws || []).map((f, fi) => (
                    <div key={fi} className="tabular-nums font-semibold text-down">
                      {f.kind === "low_rr"
                        ? t("designFlaw.low_rr", { entry: num(f.entry, locale), rr: num(f.rr, locale), min: num(review.rules?.min_rr ?? 1.5, locale) })
                        : t("designFlaw.tight_stop", { distance: num(f.distance, locale), mult: num(f.atr_multiple, locale), min: num(review.rules?.min_stop_atr ?? 0.5, locale) })}
                    </div>
                  ))}
                </div>
              </li>
            );
          })}
        </ul>
      </div>

      {lastDate && (
        <p className="px-3.5 py-2 border-t border-border text-[12px] leading-snug text-muted-light">
          {t("barNote", { date: lastDate.split("-").reverse().join("/") })}
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
