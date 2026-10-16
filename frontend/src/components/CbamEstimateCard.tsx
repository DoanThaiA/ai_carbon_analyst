"use client";

import clsx from "clsx";
import { useLocale, useTranslations } from "next-intl";
import type { CbamEstimate } from "@/lib/types";

// Thẻ "Ước tính giá CBAM" — dữ liệu tính sẵn bằng Python (services/cbam_estimate.py, content["2"].cbam_estimate):
// giá CBAM EC công bố theo quý = VWAP giá khớp mọi phiên đấu giá EUA sơ cấp trên EEX. Toàn bộ là số + enum
// ASCII nên câu chữ dựng ở đây theo ngôn ngữ giao diện (bản dịch máy không chạm vào số).

function num(n: number | null | undefined, locale: string, digits = 2): string {
  if (n === null || n === undefined) return "—";
  const s = n.toFixed(digits);
  return locale === "en" ? s : s.replace(".", ",");
}

function dm(iso?: string | null): string {
  if (!iso) return "";
  const [, m, d] = iso.split("-");
  return `${d}/${m}`;
}

const SIGNAL_TONE: Record<CbamEstimate["signal"], string> = {
  increase: "bg-up/10 text-up border-up/30",
  hold: "bg-tint text-primary-dark border-primary/30",
  reduce: "bg-down/10 text-down border-down/30",
};

export function CbamEstimateCard({ estimate: e }: { estimate: CbamEstimate }) {
  const t = useTranslations("cbamEstimate");
  const locale = useLocale();
  const hasCi = e.ci_low !== null && e.ci_high !== null;
  const gap = e.spot_vs_qtd_pct;

  return (
    <div className="mt-6 border border-border rounded-lg overflow-hidden print:break-inside-avoid">
      <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 bg-tint border-b border-primary/30">
        <h3 className="text-[15px] sm:text-[16px] font-extrabold tracking-tight text-primary-dark">
          {t("title", { quarter: e.quarter })}
        </h3>
        <span className={clsx("font-mono text-[11px] font-bold uppercase tracking-wider px-2 py-0.5 rounded border", SIGNAL_TONE[e.signal])}>
          {t("signalLabel")}: {t(`signal.${e.signal}`)}
        </span>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 divide-y sm:divide-y-0 sm:divide-x divide-border">
        <div className="px-3 py-2.5">
          <div className="text-[10.5px] font-bold uppercase tracking-wider text-muted-light">{t("estimate")}</div>
          <div className="font-black tabular-nums leading-tight text-[26px] text-primary-dark">
            {num(e.estimate, locale)} <span className="text-[12px] font-semibold text-muted-light">EUR/tCO₂</span>
          </div>
          {hasCi && (
            <div className="text-[11.5px] text-muted-light tabular-nums">
              {t("ci", { level: e.ci_level, low: num(e.ci_low, locale), high: num(e.ci_high, locale) })}
            </div>
          )}
        </div>
        <div className="px-3 py-2.5">
          <div className="text-[10.5px] font-bold uppercase tracking-wider text-muted-light">{t("qtdVwap")}</div>
          <div className="font-black tabular-nums leading-tight text-[22px] text-label">{num(e.qtd_vwap, locale)}</div>
          <div className="text-[11.5px] text-muted-light tabular-nums">
            {t("auctionsDone", { done: e.auctions_done, total: e.auctions_total })}
          </div>
        </div>
        <div className="px-3 py-2.5">
          <div className="text-[10.5px] font-bold uppercase tracking-wider text-muted-light">{t("spot")}</div>
          <div className="font-black tabular-nums leading-tight text-[22px] text-label">{num(e.spot, locale)}</div>
          {gap !== null && (
            <div className={clsx("text-[11.5px] tabular-nums", gap >= 0 ? "text-up" : "text-down")}>
              {t("spotVsQtd", { pct: `${gap >= 0 ? "+" : ""}${num(gap, locale)}` })}
            </div>
          )}
        </div>
      </div>

      {/* Tỷ lệ khối lượng quý đã chốt giá */}
      <div className="px-3 pt-2.5">
        <div className="flex justify-between text-[11px] text-muted-light tabular-nums mb-1">
          <span>{t("fixedShare", { pct: num(e.fixed_share_pct, locale, 1) })}</span>
          <span>{t("volume", { done: num(e.volume_done_mt, locale), total: num(e.volume_total_mt, locale) })}</span>
        </div>
        <div className="h-2 rounded-full bg-surface border border-border overflow-hidden">
          <div className="h-full bg-primary/70" style={{ width: `${Math.min(100, Math.max(0, e.fixed_share_pct))}%` }} />
        </div>
      </div>

      <ul className="px-3 py-2.5 space-y-1 text-[13.5px] leading-[1.55] text-body list-disc pl-7">
        <li>
          <strong className="text-label">{t("signalWhy")}:</strong>{" "}
          {t(`reason.${e.signal_reason}`, {
            gap: num(Math.abs(gap ?? 0), locale),
            threshold: num(e.hedge_gap_pct, locale, 0),
            fixed: num(e.hedge_fixed_share_pct, locale, 0),
          })}
        </li>
        {e.last_auction && (
          <li>
            {t("lastAuction", {
              date: dm(e.last_auction.date),
              platform: e.last_auction.platform,
              price: num(e.last_auction.price, locale),
            })}
            {e.last_auction.cover_ratio !== null && ` · ${t("cover", { ratio: num(e.last_auction.cover_ratio, locale) })}`}
          </li>
        )}
        {e.upcoming && e.upcoming.count > 0 && (
          <li>
            {t("upcoming", {
              count: e.upcoming.count,
              from: dm(e.upcoming.first_date),
              to: dm(e.upcoming.last_date),
              volume: num(e.upcoming.volume_mt, locale),
            })}
          </li>
        )}
        {e.prev_quarter && e.prev_quarter.official !== null && (
          <li>
            {t("prevQuarter", {
              quarter: e.prev_quarter.quarter,
              vwap: num(e.prev_quarter.vwap, locale),
              official: num(e.prev_quarter.official, locale),
            })}
          </li>
        )}
      </ul>

      <p className="px-3 pb-2.5 text-[11.5px] leading-[1.5] text-muted-light">
        {t("method", { vol: num(e.daily_vol_pct, locale) })}
      </p>
    </div>
  );
}
