"use client";

// Popup "Xuất PDF": chọn thiết bị xem → khổ → hướng, rồi backend (api/routers/report_pdf.py) dàn lại trang theo lựa
// chọn và xuất PDF bằng Chromium phía server — không dùng hộp thoại in của trình duyệt. Kích thước trang/lề/tỉ lệ
// chữ thật nằm ở services/report_pdf.py (PAGE_SIZES, DEVICE_SCALES); ở đây chỉ để vẽ ô xem trước.

import { useState } from "react";
import clsx from "clsx";
import { useLocale, useTranslations } from "next-intl";
import { Download, FileText, Loader2, Monitor, Smartphone, Tv, X } from "lucide-react";
import { api } from "@/lib/api";

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

export function PdfExportModal({ date, admin = false, onClose }: {
  date: string;
  // true: xuất qua API admin (được cả bản nháp).
  admin?: boolean;
  onClose: () => void;
}) {
  const t = useTranslations("pdfExport");
  const locale = useLocale();
  const [device, setDevice] = useState<Device>("pc");
  const [size, setSize] = useState<Size>("a4");
  const [orientation, setOrientation] = useState<Orientation>("portrait");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const pickDevice = (d: Device) => {
    setDevice(d);
    setSize(DEVICE_DEFAULTS[d].size);
    setOrientation(DEVICE_DEFAULTS[d].orientation);
  };

  const [wMm, hMm] = pageMm(size, orientation);
  // Ô xem trước: cạnh dài nhất 120px.
  const scale = 120 / Math.max(wMm, hMm);

  const handleExport = async () => {
    setBusy(true);
    setError("");
    try {
      const res = await api.get(`${admin ? "/api/admin" : "/api"}/reports/${date}/pdf`, {
        params: { size, orientation, device, lang: locale === "en" ? "en" : "vi" },
        responseType: "blob",
        timeout: 180_000,
      });
      const name = /filename="([^"]+)"/.exec(res.headers["content-disposition"] ?? "")?.[1] ?? `bao-cao-carbon-${date}.pdf`;
      const url = URL.createObjectURL(res.data);
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
      onClose();
    } catch (err: any) {
      // responseType blob → chi tiết lỗi nằm trong blob JSON.
      let detail = "";
      try { detail = JSON.parse(await err.response?.data?.text?.())?.detail ?? ""; } catch { /* không phải JSON */ }
      setError(detail || t("error"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 print:hidden">
      <div className="absolute inset-0 bg-black/40" onClick={busy ? undefined : onClose} />
      <div className="relative w-full max-w-lg bg-background border border-border rounded-2xl shadow-[var(--shadow-medium)]">
        <div className="flex items-center justify-between gap-2 px-5 py-4 border-b border-border">
          <h3 className="flex items-center gap-2 font-bold text-heading">
            <FileText size={18} className="text-primary" />
            {t("title")}
          </h3>
          <button type="button" onClick={onClose} disabled={busy} className="text-muted-light hover:text-body disabled:opacity-40" aria-label={t("close")}>
            <X size={18} />
          </button>
        </div>

        <div className="px-5 py-4 space-y-4">
          <div>
            <div className="text-xs font-bold uppercase tracking-wider text-muted-light mb-1.5">{t("deviceLabel")}</div>
            <div className="flex gap-2">
              {DEVICES.map(({ key, icon: Icon }) => (
                <Option key={key} active={device === key} disabled={busy} onClick={() => pickDevice(key)}>
                  <Icon size={18} />
                  {t(`device.${key}`)}
                </Option>
              ))}
            </div>
          </div>

          <div className="flex gap-4">
            <div className="flex-1 space-y-4">
              <div>
                <div className="text-xs font-bold uppercase tracking-wider text-muted-light mb-1.5">{t("sizeLabel")}</div>
                <div className="flex gap-2">
                  {SIZES.map((s) => (
                    <Option key={s} active={size === s} disabled={busy} onClick={() => setSize(s)}>
                      {t(`size.${s}`)}
                    </Option>
                  ))}
                </div>
              </div>
              <div>
                <div className="text-xs font-bold uppercase tracking-wider text-muted-light mb-1.5">{t("orientationLabel")}</div>
                <div className="flex gap-2">
                  {ORIENTATIONS.map((o) => (
                    <Option key={o} active={orientation === o} disabled={busy || size === "square"} onClick={() => setOrientation(o)}>
                      {t(`orientation.${o}`)}
                    </Option>
                  ))}
                </div>
              </div>
            </div>
            <div className="flex w-[132px] shrink-0 flex-col items-center justify-center gap-1.5">
              <div
                className="rounded-sm border-2 border-primary/60 bg-tint/60 transition-all"
                style={{ width: wMm * scale, height: hMm * scale }}
              />
              <div className="text-[11px] text-muted-light tabular-nums">{wMm} × {hMm} mm</div>
            </div>
          </div>

          <p className="text-xs text-muted-light">{t(`hint.${device}`)}</p>
          {error && <p className="rounded-lg border border-down/30 bg-red-50 px-3 py-2 text-xs text-down">{error}</p>}
        </div>

        <div className="flex justify-end gap-2 px-5 py-4 border-t border-border">
          <button type="button" onClick={onClose} disabled={busy} className="px-4 py-2 rounded-full text-sm font-semibold text-body hover:bg-surface disabled:opacity-40">
            {t("cancel")}
          </button>
          <button
            type="button"
            onClick={handleExport}
            disabled={busy}
            className="flex items-center gap-2 px-4 py-2 rounded-full bg-primary hover:bg-primary-dark text-white text-sm font-semibold transition-colors disabled:opacity-60"
          >
            {busy ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />}
            {busy ? t("exporting") : t("export")}
          </button>
        </div>
      </div>
    </div>
  );
}
