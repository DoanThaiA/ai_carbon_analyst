"use client";

// Trang dàn trang để xuất PDF — CHỈ do Chromium phía server mở (services/report_pdf.py), không dành cho người dùng.
// Backend tính sẵn khổ trang/lề/tỉ lệ chữ theo lựa chọn ở popup "Xuất PDF" và truyền qua query:
//   w, h   — khổ trang (mm)        mv, mh — lề trên-dưới / trái-phải (mm)
//   zoom   — tỉ lệ chữ theo thiết bị xem   src=admin — đọc qua API admin (xuất được bản nháp)
// Trang đặt @page theo khổ đó, ép khung báo cáo đúng bề rộng vùng in (chia cho zoom — container query dàn lại số cột
// theo bề rộng này), chờ font/biểu đồ ổn định và media print, dàn ngắt trang theo chiều cao trang thật
// (tunePrintBreaks) rồi mới gắn data-pdf-ready cho backend xuất.

import { Suspense, useEffect, useRef, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { useLocale } from "next-intl";
import { api } from "@/lib/api";
import type { Report } from "@/lib/types";
import { ReportDocument } from "@/components/ReportDocument";
import { localizeReport } from "@/lib/reportLocale";
import { tunePrintBreaks } from "@/lib/usePrintBreakTuning";

const MM_TO_PX = 96 / 25.4;
// Chờ biểu đồ (ResizeObserver, vẽ lại theo bề rộng thật) ổn định sau khi chuyển sang media print.
const SETTLE_MS = 800;
// Biểu đồ nến cao tối đa ~55% vùng in 1 trang (giữ tỉ lệ, thu vào giữa) — trang ngang 16:9 thấp vẫn đủ chỗ cho tiêu
// đề khung + biểu đồ trên cùng 1 trang. A4 dọc không bị ảnh hưởng (biểu đồ thấp hơn mức này).
const CHART_MAX_PAGE_RATIO = 0.55;

function num(value: string | null, fallback: number, min: number, max: number): number {
  const n = Number(value);
  return Number.isFinite(n) && n >= min && n <= max ? n : fallback;
}

export default function ReportExportPage() {
  return (
    <Suspense fallback={null}>
      <ReportExport />
    </Suspense>
  );
}

function ReportExport() {
  const date = useParams().date as string;
  const search = useSearchParams();
  const locale = useLocale();
  const w = num(search.get("w"), 210, 50, 1000);
  const h = num(search.get("h"), 297, 50, 1000);
  const mv = num(search.get("mv"), 12, 0, 50);
  const mh = num(search.get("mh"), 10, 0, 50);
  const zoom = num(search.get("zoom"), 1, 0.5, 3);
  const fromAdmin = search.get("src") === "admin";
  const contentW = (w - 2 * mh) * MM_TO_PX;
  const contentH = (h - 2 * mv) * MM_TO_PX;

  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  const frameRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.get(fromAdmin ? `/api/admin/reports/${date}` : `/api/reports/${date}`)
      .then((res) => setReport(res.data))
      .catch((err) => setError(String(err.response?.status ?? err.message)));
  }, [date, fromAdmin]);

  useEffect(() => {
    if (!report) return;
    let cancelled = false;
    const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
    (async () => {
      // Backend gọi emulate_media("print") sau khi trang tải xong — đợi tới lúc đó để đo đúng style in.
      const mql = window.matchMedia("print");
      while (!mql.matches && !cancelled) await sleep(100);
      await document.fonts.ready;
      await sleep(SETTLE_MS);
      if (cancelled) return;
      tunePrintBreaks(frameRef.current?.querySelector<HTMLElement>(".report-shell") ?? null, contentH);
      setReady(true);
    })();
    return () => { cancelled = true; };
  }, [report, contentH]);

  if (error) return <p data-pdf-error={error}>Không tải được báo cáo ({error}).</p>;
  if (!report) return null;
  const { report: shown } = localizeReport(report, locale);

  return (
    <>
      {/* Khổ/lề theo lựa chọn — đè @page A4 trong globals.css (header/footer trang khai báo ở đó vẫn giữ). */}
      {/* Khổ hẹp (9:16 điện thoại < 640px) bật các class max-sm: của khung báo cáo (lề âm, bỏ viền) — giữ khung khít vùng in. */}
      <style>{`@page { size: ${w}mm ${h}mm; margin: ${mv}mm ${mh}mm; }
html, body { background: #fff !important; }
.pdf-export .report-shell { margin: 0 !important; width: 100% !important; }
.pdf-export .candle-chart { max-height: ${Math.round((contentH * CHART_MAX_PAGE_RATIO) / zoom)}px !important; }`}</style>
      <div
        ref={frameRef}
        className="pdf-export"
        data-pdf-ready={ready ? "" : undefined}
        style={{ width: `${contentW / zoom}px`, zoom }}
      >
        <ReportDocument report={shown} pdfExport />
      </div>
    </>
  );
}
