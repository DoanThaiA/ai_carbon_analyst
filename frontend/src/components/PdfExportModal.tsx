"use client";

// Popup "Xuất PDF": chọn thiết bị xem → khổ → hướng; mỗi lựa chọn backend (api/routers/report_pdf.py) dàn lại trang và
// xuất PDF bằng Chromium phía server — không dùng hộp thoại in của trình duyệt — rồi hiện ngay trong khung xem trước (PdfPagesPreview, pdf.js);
// "Tải xuống" lưu đúng file đang xem. Kích thước trang/lề/tỉ lệ chữ thật nằm ở services/report_pdf.py (PAGE_SIZES,
// DEVICE_SCALES); ở đây chỉ để ghi kích thước trang.

import { useEffect, useRef, useState } from "react";
import clsx from "clsx";
import { useLocale, useTranslations } from "next-intl";
import { Download, FileText, Loader2, Monitor, Smartphone, Tv, X } from "lucide-react";
import { api } from "@/lib/api";
import { PdfPagesPreview } from "@/components/PdfPagesPreview";

type Device = "mobile" | "pc" | "tv";
type Size = "a4" | "square" | "wide";
type Orientation = "portrait" | "landscape";

// Khổ theo hướng DỌC (mm) — khớp services/report_pdf.py::PAGE_SIZES.
const PAGE_MM: Record<Size, [number, number]> = { a4: [210, 297], square: [210, 210], wide: [167, 297] };
// Gợi ý khi đổi thiết bị: điện thoại cuộn dọc màn hình 9:16, máy tính đọc A4 dọc, TV trình chiếu 16:9 ngang.
const DEVICE_DEFAULTS: Record<Device, { size: Size; orientation: Orientation }> = {
  mobile: { size: "wide", orientation: "portrait" },
  pc: { size: "a4", orientation: "portrait" },
  tv: { size: "wide", orientation: "landscape" },
};
const DEVICES: { key: Device; icon: typeof Monitor }[] = [
  { key: "mobile", icon: Smartphone },
  { key: "pc", icon: Monitor },
  { key: "tv", icon: Tv },
];
const SIZES: Size[] = ["a4", "square", "wide"];
const ORIENTATIONS: Orientation[] = ["portrait", "landscape"];

function pageMm(size: Size, orientation: Orientation): [number, number] {
  const [w, h] = PAGE_MM[size];
  return orientation === "landscape" && size !== "square" ? [h, w] : [w, h];
}

function Option({ active, disabled, onClick, children }: {
  active: boolean; disabled?: boolean; onClick: () => void; children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={clsx(
        "flex flex-1 flex-col items-center justify-center gap-1 rounded-xl border px-2 py-2.5 text-xs font-semibold transition-colors",
        active ? "border-primary bg-tint text-primary-dark" : "border-border text-body hover:border-primary/50",
        disabled && "opacity-40 cursor-not-allowed hover:border-border",
      )}
    >
      {children}
    </button>
  );
}

// Bản PDF đã render cho 1 tổ hợp lựa chọn — giữ lại để quay về lựa chọn cũ không phải render lại.
type Rendered = { blob: Blob; url: string; name: string };
// Chờ người dùng chọn xong (bấm liên tiếp nhiều nút) rồi mới gọi render.
const PREVIEW_DEBOUNCE_MS = 400;

export function PdfExportModal({ date, admin = false, onClose }: {
  date: string;
  // true: xuất qua API admin (được cả bản nháp).
  admin?: boolean;
  onClose: () => void;
}) {
  const t = useTranslations("pdfExport");
  const locale = useLocale();
  const lang = locale === "en" ? "en" : "vi";
  const [device, setDevice] = useState<Device>("pc");
  const [size, setSize] = useState<Size>("a4");
  const [orientation, setOrientation] = useState<Orientation>("portrait");
  const [preview, setPreview] = useState<(Rendered & { key: string }) | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const cache = useRef(new Map<string, Rendered>());

  const pickDevice = (d: Device) => {
    setDevice(d);
    setSize(DEVICE_DEFAULTS[d].size);
    setOrientation(DEVICE_DEFAULTS[d].orientation);
  };

  const key = `${size}|${size === "square" ? "-" : orientation}|${device}|${lang}`;
  const [wMm, hMm] = pageMm(size, orientation);

  // Xem trước tự cập nhật theo lựa chọn: backend dàn trang + xuất PDF thật (cùng file sẽ tải về), hiện trong khung.
  useEffect(() => {
    const hit = cache.current.get(key);
    if (hit) {
      setPreview({ key, ...hit });
      setError("");
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError("");
    const timer = setTimeout(async () => {
      try {
        const res = await api.get(`${admin ? "/api/admin" : "/api"}/reports/${date}/pdf`, {
          params: { size, orientation, device, lang },
          responseType: "blob",
          timeout: 180_000,
          signal: controller.signal,
        });
        const name = /filename="([^"]+)"/.exec(res.headers["content-disposition"] ?? "")?.[1] ?? `bao-cao-carbon-${date}.pdf`;
        const rendered = { blob: res.data as Blob, url: URL.createObjectURL(res.data), name };
        cache.current.set(key, rendered);
        setPreview({ key, ...rendered });
        setLoading(false);
      } catch (err: any) {
        if (controller.signal.aborted) return;
        // responseType blob → chi tiết lỗi nằm trong blob JSON.
        let detail = "";
        try { detail = JSON.parse(await err.response?.data?.text?.())?.detail ?? ""; } catch { /* không phải JSON */ }
        setError(detail || t("error"));
        setLoading(false);
      }
    }, PREVIEW_DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [key, size, orientation, device, lang, admin, date, t]);

  useEffect(() => {
    const rendered = cache.current;
    return () => rendered.forEach((r) => URL.revokeObjectURL(r.url));
  }, []);

  const ready = preview?.key === key && !loading;

  const handleDownload = () => {
    if (!ready || !preview) return;
    const a = document.createElement("a");
    a.href = preview.url;
    a.download = preview.name;
    document.body.appendChild(a);
    a.click();
    a.remove();
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-2 sm:p-4 print:hidden">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div className="relative flex h-[92vh] w-full max-w-6xl flex-col bg-background border border-border rounded-2xl shadow-[var(--shadow-medium)]">
        <div className="flex items-center justify-between gap-2 px-5 py-3.5 border-b border-border">
          <h3 className="flex items-center gap-2 font-bold text-heading">
            <FileText size={18} className="text-primary" />
            {t("title")}
          </h3>
          <button type="button" onClick={onClose} className="text-muted-light hover:text-body" aria-label={t("close")}>
            <X size={18} />
          </button>
        </div>

        {/* Màn hình nhỏ: cả thân (tuỳ chọn + xem trước) cuộn chung, hàng nút luôn ở đáy; từ md: 2 cột, xem trước tự cuộn. */}
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto md:flex-row md:overflow-hidden">
          <div className="shrink-0 space-y-4 border-b border-border px-5 py-4 md:w-[340px] md:overflow-y-auto md:border-b-0 md:border-r">
            <div>
              <div className="text-xs font-bold uppercase tracking-wider text-muted-light mb-1.5">{t("deviceLabel")}</div>
              <div className="flex gap-2">
                {DEVICES.map(({ key: d, icon: Icon }) => (
                  <Option key={d} active={device === d} onClick={() => pickDevice(d)}>
                    <Icon size={18} />
                    {t(`device.${d}`)}
                  </Option>
                ))}
              </div>
            </div>
            <div>
              <div className="text-xs font-bold uppercase tracking-wider text-muted-light mb-1.5">{t("sizeLabel")}</div>
              <div className="flex gap-2">
                {SIZES.map((s) => (
                  <Option key={s} active={size === s} onClick={() => setSize(s)}>
                    {t(`size.${s}`)}
                  </Option>
                ))}
              </div>
            </div>
            <div>
              <div className="text-xs font-bold uppercase tracking-wider text-muted-light mb-1.5">{t("orientationLabel")}</div>
              <div className="flex gap-2">
                {ORIENTATIONS.map((o) => (
                  <Option key={o} active={orientation === o} disabled={size === "square"} onClick={() => setOrientation(o)}>
                    {t(`orientation.${o}`)}
                  </Option>
                ))}
              </div>
            </div>
            <div className="text-xs text-muted-light tabular-nums">{t("pageSize", { w: wMm, h: hMm })}</div>
            <p className="text-xs text-muted-light">{t(`hint.${device}`)}</p>
          </div>

          <div className="relative min-h-[60vh] flex-1 bg-surface md:min-h-0">
            {preview && (
              <PdfPagesPreview
                blob={preview.blob}
                className={clsx("px-4 py-4 transition-opacity md:h-full md:overflow-y-auto", !ready && "opacity-40")}
              />
            )}
            {loading && (
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-sm font-semibold text-body">
                <Loader2 size={26} className="animate-spin text-primary" />
                {t("exporting")}
              </div>
            )}
            {error && !loading && (
              <div className="absolute inset-0 flex items-center justify-center p-6">
                <p className="rounded-lg border border-down/30 bg-red-50 px-3 py-2 text-sm text-down">{error}</p>
              </div>
            )}
          </div>
        </div>

        <div className="flex justify-end gap-2 px-5 py-3.5 border-t border-border">
          <button type="button" onClick={onClose} className="px-4 py-2 rounded-full text-sm font-semibold text-body hover:bg-surface">
            {t("cancel")}
          </button>
          <button
            type="button"
            onClick={handleDownload}
            disabled={!ready}
            className="flex items-center gap-2 px-4 py-2 rounded-full bg-primary hover:bg-primary-dark text-white text-sm font-semibold transition-colors disabled:opacity-60"
          >
            {ready ? <Download size={15} /> : <Loader2 size={15} className="animate-spin" />}
            {t("download")}
          </button>
        </div>
      </div>
    </div>
  );
}
