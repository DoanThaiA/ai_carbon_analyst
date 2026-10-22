"use client";

import { useEffect, useState } from "react";
import { Maximize2, X } from "lucide-react";
import clsx from "clsx";
import { useTranslations } from "next-intl";
import { api } from "@/lib/api";
import { CompareChart, CompareChips, Dashboard, MAX_COMPARE, fmtFull, useColorOf } from "@/components/PriceCharts";
import type { CbamEstimate } from "@/lib/types";

// Biểu đồ chỉ số giá cạnh Bảng giá nhanh trong báo cáo (admin + user — ReportDocument.tsx). Dữ liệu GET
// /api/prices/history?before=<ngày báo cáo> (chỉ phiên trước ngày phát hành, như bảng giá). Bấm vào biểu đồ
// mở popup "So sánh hiệu suất" (chọn mã, đổi khoảng thời gian) — cùng biểu đồ với Dashboard giá của admin.

const PANEL_DAYS = 90;
const RANGES = [30, 90, 180, 365];
// Mặc định: các mã dẫn dắt EUA chính (khí, than, dầu) — thiếu mã nào thì lấp bằng thứ tự Bảng giá nhanh.
const DEFAULT_CODES = ["EUA", "TTF", "API2", "BRENT"];

function defaultSelection(available: string[], tableCodes: string[]) {
  const pick = DEFAULT_CODES.filter((c) => available.includes(c));
  for (const c of [...tableCodes, ...available]) {
    if (pick.length >= MAX_COMPARE) break;
    if (c !== "CBAM" && available.includes(c) && !pick.includes(c)) pick.push(c);
  }
  return pick;
}

function useHistory(reportDate: string, days: number) {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let cancelled = false;
    api
      .get<Dashboard>("/api/prices/history", { params: { days, before: reportDate } })
      .then((res) => {
        if (cancelled) return;
        // Phản hồi sai định dạng (proxy lỗi, phiên hết hạn trả trang khác…) → coi như lỗi tải, không làm sập cả báo cáo.
        if (Array.isArray(res.data?.instruments)) { setData(res.data); setError(false); } else setError(true);
      })
      .catch(() => { if (!cancelled) setError(true); });
    return () => { cancelled = true; };
  }, [reportDate, days]);
  return { data, error };
}

// `cbamEstimate`: thẻ CBAM của chính báo cáo (content["2"].cbam_estimate) — vẽ mốc ước tính cuối quý cạnh đường VWAP.
export default function PriceIndexPanel({ reportDate, tableCodes, cbamEstimate }: {
  reportDate: string; tableCodes: string[]; cbamEstimate?: CbamEstimate | null;
}) {
  const t = useTranslations("priceIndex");
  const { data, error } = useHistory(reportDate, PANEL_DAYS);
  const colorOf = useColorOf(data?.instruments);
  const [selected, setSelected] = useState<string[] | null>(null);
  const [open, setOpen] = useState(false);

  const available = (data?.instruments ?? []).filter((i) => i.series.length > 1).map((i) => i.code);
  const codes = selected ?? defaultSelection(available, tableCodes);

  if (error) {
    return (
      <div className="border border-border rounded-lg p-4 text-[13px] text-muted-light print:hidden">{t("loadError")}</div>
    );
  }

  return (
    <>
      <div
        role="button"
        tabIndex={0}
        onClick={() => data && setOpen(true)}
        onKeyDown={(e) => { if (data && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); setOpen(true); } }}
        aria-label={t("openCompare")}
        className="group h-full flex flex-col border border-border rounded-lg p-3 cursor-pointer transition-colors hover:border-primary/50 hover:bg-tint/20 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 print:hidden"
      >
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="text-primary-dark font-bold text-[11px] uppercase tracking-wider">{t("title")}</div>
            {data?.latest_date && (
              <div className="font-mono text-[10.5px] text-muted-light mt-0.5">
                {t("subtitle", { n: PANEL_DAYS, date: fmtFull(data.latest_date) })}
              </div>
            )}
          </div>
          <span className="shrink-0 inline-flex items-center gap-1 text-[11px] font-semibold text-primary-dark group-hover:underline">
            <Maximize2 size={12} aria-hidden="true" /> {t("compare")}
          </span>
        </div>
        {/* Chú giải tĩnh — đổi mã trong popup */}
        <div className="flex flex-wrap gap-x-3 gap-y-1 mt-2 text-[11.5px] text-body">
          {codes.map((c) => (
            <span key={c} className="inline-flex items-center gap-1.5 font-semibold">
              <span className="w-2 h-2 rounded-full" style={{ background: colorOf[c] }} aria-hidden="true" />
              {c}
            </span>
          ))}
        </div>
        <div className="mt-1 flex-1 min-h-[240px]">
          {data ? (
            <CompareChart instruments={data.instruments} colorOf={colorOf} selected={codes} vwap={data.auction_vwap} cbam={cbamEstimate} height={260} />
          ) : (
            <div className="h-[260px] rounded-md bg-surface animate-pulse" />
          )}
        </div>
      </div>
      {open && (
        <CompareModal
          reportDate={reportDate}
          cbamEstimate={cbamEstimate}
          selected={codes}
          onChange={setSelected}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}

function CompareModal({ reportDate, cbamEstimate, selected, onChange, onClose }: {
  reportDate: string; cbamEstimate?: CbamEstimate | null; selected: string[]; onChange: (codes: string[]) => void; onClose: () => void;
}) {
  const t = useTranslations("priceIndex");
  const td = useTranslations("adminSrcDash");
  const [days, setDays] = useState(PANEL_DAYS);
  const { data, error } = useHistory(reportDate, days);
  const colorOf = useColorOf(data?.instruments);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { window.removeEventListener("keydown", onKey); document.body.style.overflow = prev; };
  }, [onClose]);

  const toggle = (code: string) =>
    onChange(selected.includes(code) ? selected.filter((c) => c !== code) : selected.length >= MAX_COMPARE ? selected : [...selected, code]);

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 print:hidden" role="dialog" aria-modal="true" aria-label={td("compareTitle")}>
      <div className="absolute inset-0 bg-black/40" onClick={onClose} aria-hidden="true" />
      <div className="relative w-full max-w-5xl max-h-[90vh] overflow-y-auto bg-background border border-border rounded-2xl shadow-[var(--shadow-medium)] p-5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="text-[18px] font-bold text-heading">{td("compareTitle")}</h3>
            <p className="text-[12.5px] text-muted-light mt-0.5">
              {td("compareSubtitle", { max: MAX_COMPARE })}
              {data?.latest_date && <> · {t("until", { date: fmtFull(data.latest_date) })}</>}
            </p>
          </div>
          <button type="button" onClick={onClose} aria-label={t("close")} className="shrink-0 p-1.5 rounded-md text-muted-light hover:bg-surface hover:text-heading">
            <X size={18} />
          </button>
        </div>
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          {data ? <CompareChips instruments={data.instruments} colorOf={colorOf} selected={selected} onToggle={toggle} /> : <span />}
          <div className="inline-flex rounded-full border border-border-soft p-0.5 bg-background" role="group" aria-label={td("rangeAria")}>
            {RANGES.map((r) => (
              <button
                key={r}
                type="button"
                onClick={() => setDays(r)}
                aria-pressed={days === r}
                className={clsx(
                  "px-3 py-1 rounded-full text-[12.5px] font-semibold transition-colors duration-200",
                  days === r ? "bg-primary text-white" : "text-body hover:bg-surface"
                )}
              >
                {td("days", { n: r })}
              </button>
            ))}
          </div>
        </div>
        <div className="mt-4">
          {error ? (
            <div className="h-[380px] flex items-center justify-center text-sm text-muted-light">{t("loadError")}</div>
          ) : data ? (
            <CompareChart instruments={data.instruments} colorOf={colorOf} selected={selected} vwap={data.auction_vwap} cbam={cbamEstimate} height={380} />
          ) : (
            <div className="h-[380px] rounded-md bg-surface animate-pulse" />
          )}
        </div>
      </div>
    </div>
  );
}
