"use client";

import type { ReactNode } from "react";
import clsx from "clsx";
import { useLocale, useTranslations } from "next-intl";
import {
  BadgeCheck, CalendarClock, Coins, Factory, FileText, Gavel, Info, Landmark, Leaf, Lightbulb, Percent, TrendingUp,
} from "lucide-react";
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

const UNIT = "EUR/tCO₂";
const PRINT_EXACT = "[print-color-adjust:exact] [-webkit-print-color-adjust:exact]";

// 1 chỉ số lớn: icon tròn cùng hàng nhãn, số + đơn vị bên dưới (thẻ chỉ rộng ~1/3 khổ A4 — icon đặt bên trái số
// thì "EUR/tCO₂" bị đẩy xuống dòng).
function Kpi({ icon, iconClass, label, value, sub, subClass }: {
  icon: ReactNode; iconClass: string; label: string; value: string; sub?: string; subClass?: string;
}) {
  return (
    <div className="rounded-xl border border-primary/15 bg-tint/50 px-3.5 py-3 min-w-0 print:break-inside-avoid">
      <div className="flex items-center gap-2.5">
        <span className={clsx("flex items-center justify-center w-8 h-8 rounded-full shrink-0 text-white", iconClass, PRINT_EXACT)} aria-hidden="true">
          {icon}
        </span>
        <span className="text-[10.5px] font-bold uppercase leading-tight tracking-wider text-primary-dark">{label}</span>
      </div>
      <div className="mt-2 flex flex-wrap items-baseline gap-x-1.5">
        <span className="font-black tabular-nums leading-none text-[28px] sm:text-[30px] text-[#123c38]">{value}</span>
        <span className="text-[11.5px] font-semibold text-muted-light">{UNIT}</span>
      </div>
      {sub && <div className={clsx("mt-1.5 text-[11.5px] leading-snug tabular-nums [text-wrap:balance]", subClass ?? "text-muted-light")}>{sub}</div>}
    </div>
  );
}

// 1 dòng trong khối "Thông tin chi tiết": icon | nhãn đậm | nội dung (mobile: nội dung xuống dưới nhãn).
function DetailRow({ icon, label, children }: { icon: ReactNode; label: string; children: ReactNode }) {
  return (
    <li className="flex items-start gap-3 py-2.5 border-t border-border first:border-t-0 first:pt-0 last:pb-0 print:break-inside-avoid">
      <span className="flex items-center justify-center w-8 h-8 rounded-full shrink-0 bg-tint text-primary-dark" aria-hidden="true">{icon}</span>
      <div className="min-w-0 flex-1 sm:flex sm:gap-4 sm:pt-[6px]">
        <div className="text-[13px] font-bold leading-snug text-label sm:w-[9.5rem] sm:shrink-0 max-sm:mt-[7px]">{label}</div>
        <div className="text-[13px] leading-[1.55] text-body tabular-nums">{children}</div>
      </div>
    </li>
  );
}

export function CbamEstimateCard({ estimate: e }: { estimate: CbamEstimate }) {
  const t = useTranslations("cbamEstimate");
  const locale = useLocale();
  const gap = e.spot_vs_qtd_pct;
  const fixedPct = Math.min(100, Math.max(0, e.fixed_share_pct));
  const products = e.product_exposure ?? [];

  return (
    <section className="mt-6 rounded-xl border border-border bg-background overflow-hidden shadow-[var(--shadow-soft)] print:shadow-none">
      {/* ═══ Dải tiêu đề + nhãn phòng vệ (1 nhãn duy nhất, dùng chung ô phòng hộ ở thẻ Khuyến nghị vị thế) ═══ */}
      <div className={clsx("relative overflow-hidden bg-gradient-to-r from-primary-dark to-[#0d7870] px-4 sm:px-5 py-3.5 text-white print:break-inside-avoid print:break-after-avoid", PRINT_EXACT)}>
        <Leaf size={120} strokeWidth={1.25} className="absolute -right-5 -bottom-9 text-white/10 pointer-events-none" aria-hidden="true" />
        <div className="relative flex flex-wrap items-center justify-between gap-x-4 gap-y-2.5">
          <div className="flex items-center gap-3 min-w-0">
            <span className="flex items-center justify-center w-10 h-10 rounded-full bg-white/15 shrink-0" aria-hidden="true">
              <Leaf size={20} strokeWidth={2.25} />
            </span>
            <div className="min-w-0">
              <h3 className="text-[18px] sm:text-[20px] font-extrabold leading-tight tracking-tight">{t("title", { quarter: e.quarter })}</h3>
              <p className="text-left mt-0.5 text-[12px] sm:text-[12.5px] leading-snug text-white/80">{t("subtitle", { date: dm(e.as_of) })}</p>
            </div>
          </div>
          <span className="inline-flex items-center gap-2 rounded-full border border-white/35 bg-white/10 px-3 py-1.5 text-[10.5px] sm:text-[11px] font-bold uppercase tracking-wider">
            <Landmark size={14} strokeWidth={2.25} aria-hidden="true" />
            {t("signalLabel")}: {t(`signal.${e.signal}`)}
          </span>
        </div>
      </div>

      <div className="p-3 sm:p-4 space-y-3">
        {/* ═══ 3 chỉ số chính ═══ */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
          <Kpi
            icon={<Leaf size={18} strokeWidth={2.25} />} iconClass="bg-primary-dark"
            label={t("estimate")} value={num(e.estimate, locale)}
            sub={e.ci_low !== null && e.ci_high !== null
              ? t("ci", { level: e.ci_level, low: num(e.ci_low, locale), high: num(e.ci_high, locale) }) : undefined}
          />
          <Kpi
            icon={<TrendingUp size={18} strokeWidth={2.25} />} iconClass="bg-[#3b8fd0]"
            label={t("qtdVwap")} value={num(e.qtd_vwap, locale)}
            sub={t("auctionsDone", { done: e.auctions_done, total: e.auctions_total })}
          />
          <Kpi
            icon={<Coins size={18} strokeWidth={2.25} />} iconClass="bg-up"
            label={t("spot")} value={num(e.spot, locale)}
            sub={gap !== null ? t("spotVsQtd", { pct: `${gap > 0 ? "+" : ""}${num(gap, locale)}` }) : undefined}
            subClass={gap !== null ? clsx("font-semibold", gap >= 0 ? "text-up" : "text-down") : undefined}
          />
        </div>

        {/* ═══ Tỷ lệ khối lượng quý đã chốt giá ═══ */}
        <div className="rounded-xl border border-border px-3.5 py-3 print:break-inside-avoid">
          <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 text-[12.5px] tabular-nums">
            <span className="text-body">
              {t.rich("fixedShare", { pct: num(e.fixed_share_pct, locale, 1), b: (chunks) => <b className="font-bold text-primary-dark">{chunks}</b> })}
            </span>
            <span className="text-muted-light">{t("volume", { done: num(e.volume_done_mt, locale), total: num(e.volume_total_mt, locale) })}</span>
          </div>
          <div className={clsx("mt-2 h-2.5 rounded-full bg-surface-alt overflow-hidden", PRINT_EXACT)}>
            <div className="h-full rounded-full bg-gradient-to-r from-primary-dark to-primary" style={{ width: `${fixedPct}%` }} />
          </div>
        </div>

        {/* Tới lg (và khi in khổ A4 ~700px): "Thông tin chi tiết" và chi phí sản phẩm xếp dọc — chia 2 cột ở bề rộng đó
            làm đoạn "Cách khóa" dài 8 dòng và công thức sản phẩm gãy dòng. Màn rộng (xl, báo cáo trải hết bề ngang):
            chi tiết | sản phẩm cạnh nhau, sản phẩm xếp 1 cột. */}
        <div className={clsx("space-y-3", products.length > 0 && "xl:space-y-0 xl:grid xl:grid-cols-[1.3fr_1fr] xl:gap-3")}>
          {/* ═══ Thông tin chi tiết ═══ */}
          <div className="rounded-xl border border-border px-3.5 py-3">
            <h4 className="flex items-center gap-2 mb-2.5 text-[14.5px] font-extrabold text-primary-dark">
              <FileText size={16} strokeWidth={2.25} aria-hidden="true" /> {t("detailsTitle")}
            </h4>
            <ul>
              <DetailRow icon={<Info size={15} strokeWidth={2.25} />} label={t("signalWhy")}>
                {t(`reason.${e.signal_reason}`, {
                  gap: num(Math.abs(gap ?? 0), locale),
                  threshold: num(e.hedge_gap_pct, locale, 0),
                  fixed: num(e.hedge_fixed_share_pct, locale, 0),
                })}
              </DetailRow>
              {e.last_auction && (
                <DetailRow icon={<Gavel size={15} strokeWidth={2.25} />} label={t("rowLastAuction")}>
                  {t("lastAuction", { date: dm(e.last_auction.date), platform: e.last_auction.platform, price: num(e.last_auction.price, locale) })}
                  {e.last_auction.cover_ratio !== null && ` · ${t("cover", { ratio: num(e.last_auction.cover_ratio, locale) })}`}
                </DetailRow>
              )}
              {e.upcoming && e.upcoming.count > 0 && (
                <DetailRow icon={<CalendarClock size={15} strokeWidth={2.25} />} label={t("rowUpcoming")}>
                  {t("upcoming", { count: e.upcoming.count, from: dm(e.upcoming.first_date), to: dm(e.upcoming.last_date), volume: num(e.upcoming.volume_mt, locale) })}
                </DetailRow>
              )}
              {e.open_share_pct !== undefined && e.core_share_of_quarter_pct !== undefined && (
                <DetailRow icon={<Percent size={15} strokeWidth={2.25} />} label={t("rowHedge")}>
                  {t("hedgeHow", {
                    open: num(e.open_share_pct, locale, 1),
                    core: num(e.core_share_of_quarter_pct, locale, 1),
                    corePct: 100 - (e.tactical_max_pct ?? 20),
                  })}
                </DetailRow>
              )}
              {e.prev_quarter && e.prev_quarter.official !== null && (
                <DetailRow icon={<BadgeCheck size={15} strokeWidth={2.25} />} label={t("rowMethodCheck")}>
                  {t("prevQuarter", { quarter: e.prev_quarter.quarter, vwap: num(e.prev_quarter.vwap, locale), official: num(e.prev_quarter.official, locale) })}
                </DetailRow>
              )}
            </ul>
          </div>

          {/* ═══ Chi phí CBAM / tấn sản phẩm (services/cbam_estimate.py::product_exposure — phát thải nộp là GIẢ ĐỊNH) ═══ */}
          {products.length > 0 && (
            <div>
              <div className="mb-2 flex flex-wrap items-baseline gap-x-2">
                <h4 className="text-[14.5px] font-extrabold text-primary-dark">{t("productTitle")}</h4>
                <span className="text-[11.5px] leading-snug text-muted-light">{t("productAssumed")}</span>
              </div>
              <div className={clsx("grid gap-2.5", products.length > 1 && "sm:grid-cols-2 xl:grid-cols-1")}>
              {products.map((p) => {
                const illustrative = (p.defaults ?? []).some((d) => !d.vn);
                const name = t(`product.${p.key}`);
                // Tên sản phẩm đã ghi công nghệ (vd "Thép lò cao (BF-BOF)") thì không lặp lại route trong ngoặc mã CN.
                const route = p.route && !name.includes(p.route) ? `, ${p.route}` : "";
                return (
                  <div key={p.key} className="rounded-xl border border-primary/15 bg-tint/50 px-3 py-3 print:break-inside-avoid">
                    <div className="flex items-center gap-2.5">
                      <span className={clsx("flex items-center justify-center w-8 h-8 rounded-full shrink-0 bg-primary-dark text-white", PRINT_EXACT)} aria-hidden="true">
                        <Factory size={15} strokeWidth={2.25} />
                      </span>
                      <div className="text-[13.5px] font-bold leading-snug text-primary-dark">
                        {name}{" "}
                        <span className="font-semibold text-muted-light whitespace-nowrap">(CN {p.cn}{route})</span>
                      </div>
                    </div>
                    <div className="mt-2 rounded-lg border border-primary/20 bg-background px-3 py-2 tabular-nums">
                      <div className="text-[14px] font-bold text-[#123c38]">
                        {num(p.embedded, locale)} − {num(p.free_allocation_factor_pct, locale, 1)}% × CSCF {num(p.cscf_pct ?? 100, locale, 0)}% × {num(p.benchmark, locale, 3)}
                      </div>
                      <div className="mt-0.5 text-[13px] text-body">
                        <span className="whitespace-nowrap">= <b>{num(p.net_tco2_per_t, locale, 3)} tCO₂/t</b></span>{" "}
                        <span className="whitespace-nowrap">→ <b className="text-[15px] text-primary-dark">{t("productCost", { cost: num(p.cost_eur_per_t, locale) })}</b></span>
                        {p.cost_low !== null && p.cost_high !== null && (
                          <span className="ml-1 text-[11.5px] text-muted-light whitespace-nowrap">{t("productRange", { low: num(p.cost_low, locale), high: num(p.cost_high, locale) })}</span>
                        )}
                      </div>
                      <div className="mt-0.5 text-[10.5px] text-muted-light">{t("formulaLegend")}</div>
                    </div>
                    {/* Kịch bản GIÁ TRỊ MẶC ĐỊNH (kèm markup) — so với số thực đã thẩm định. */}
                    {p.defaults && p.defaults.length > 0 && (
                      <p className="text-left mt-2 text-[12px] leading-[1.5] text-muted-light tabular-nums">
                        {t("productDefaults", {
                          markup: p.default_markup_pct ?? 10,
                          list: p.defaults.map((d) => `${d.origin} ${num(d.with_markup, locale, 3)} → ${num(d.cost_eur_per_t, locale)}`).join(" · "),
                        })}
                        {illustrative && <span className="block italic">{t("productDefaultsIllustrative")}</span>}
                      </p>
                    )}
                  </div>
                );
              })}
              </div>
            </div>
          )}
        </div>

        {/* ═══ Ghi chú: phương pháp ước tính + nguồn/giả định của khối chi phí sản phẩm ═══ */}
        <div className="rounded-xl border border-[#cfe3f2] bg-[#f3f8fc] px-3.5 py-3 print:break-inside-avoid">
          <h4 className="flex items-center gap-2 mb-1.5 text-[13.5px] font-bold text-[#1d5a85]">
            <span className={clsx("flex items-center justify-center w-6 h-6 rounded-full bg-[#3b8fd0] text-white", PRINT_EXACT)} aria-hidden="true">
              <Lightbulb size={13} strokeWidth={2.25} />
            </span>
            {t("notesTitle")}
          </h4>
          <ul className="list-disc pl-5 space-y-1 text-[12px] leading-[1.55] text-body">
            <li>{t("method", { vol: num(e.daily_vol_pct, locale) })}</li>
            {products.length > 0 && (
              <li>
                {t(products[0].benchmark_source ? "productNoteCbam" : "productNote", {
                  source: products[0].benchmark_source ?? "",
                  verified: products[0].benchmark_verified ? "" : t("productUnverified"),
                  factor: num(products[0].free_allocation_factor_pct, locale, 1),
                  year: e.quarter.split(" ").pop() ?? "",
                  per01: num(products[0].per_0_1_tco2_eur ?? 0.1 * e.estimate, locale),
                })}
              </li>
            )}
          </ul>
        </div>
      </div>
    </section>
  );
}
