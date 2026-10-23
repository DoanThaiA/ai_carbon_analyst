"use client";

// Xem trước PDF bằng pdf.js: vẽ từng trang thành canvas xếp dọc (cuộn như đọc tài liệu). Không dùng <iframe> trình xem
// PDF của trình duyệt — Chrome Android không hiện PDF nhúng (tải file về luôn), Safari iOS chỉ hiện trang đầu.

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";

// Bề rộng hiển thị tối đa 1 trang (px CSS) — trang dọc không phóng quá to trên màn hình rộng.
const MAX_PAGE_WIDTH = 820;
const SIDE_PADDING = 32;

export function PdfPagesPreview({ blob, className }: { blob: Blob; className?: string }) {
  const t = useTranslations("pdfExport");
  const hostRef = useRef<HTMLDivElement>(null);
  const [pages, setPages] = useState(0);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    let cancelled = false;
    let destroy: (() => void) | undefined;
    setFailed(false);
    (async () => {
      const pdfjs = await import("pdfjs-dist");
      pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();
      const task = pdfjs.getDocument({ data: new Uint8Array(await blob.arrayBuffer()) });
      destroy = () => void task.destroy();
      const doc = await task.promise;
      if (cancelled) return;
      host.replaceChildren();
      setPages(doc.numPages);
      const width = Math.min(host.clientWidth - SIDE_PADDING, MAX_PAGE_WIDTH);
      const dpr = window.devicePixelRatio || 1;
      for (let i = 1; i <= doc.numPages && !cancelled; i++) {
        const page = await doc.getPage(i);
        const scale = width / page.getViewport({ scale: 1 }).width;
        const viewport = page.getViewport({ scale: scale * dpr });
        const canvas = document.createElement("canvas");
        canvas.width = Math.floor(viewport.width);
        canvas.height = Math.floor(viewport.height);
        canvas.style.width = `${Math.floor(viewport.width / dpr)}px`;
        canvas.className = "mx-auto block bg-white shadow-[0_2px_10px_rgba(0,0,0,0.15)]";
        canvas.setAttribute("aria-label", t("pageOf", { page: i, total: doc.numPages }));
        host.appendChild(canvas);
        await page.render({ canvas, canvasContext: canvas.getContext("2d")!, viewport }).promise;
      }
    })().catch(() => {
      if (!cancelled) setFailed(true);
    });
    return () => {
      cancelled = true;
      destroy?.();
    };
  }, [blob, t]);

  return (
    <div className={className}>
      {pages > 0 && <div className="pb-2 text-center text-xs font-semibold text-muted-light">{t("pageCount", { count: pages })}</div>}
      {failed && <p className="text-center text-sm text-down">{t("previewError")}</p>}
      <div ref={hostRef} className="space-y-4" />
    </div>
  );
}
