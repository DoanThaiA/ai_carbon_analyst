"use client";

import { useEffect, useState } from "react";
import { FileText, Image as ImageIcon, Loader2, X } from "lucide-react";
import clsx from "clsx";
import type { Attachment } from "@/lib/types";
import { fetchAttachmentViewUrl } from "@/lib/minioUpload";

// `previewUrl`: objectURL cục bộ (client-side) của file ẢNH vừa upload trong
// PHIÊN HIỆN TẠI — cho hiện thumbnail ngay không cần round-trip xin view-url.
// Dữ liệu tải lại từ server (lịch sử chat / release note đã lưu) không có
// field này — AttachmentBadge tự fetch qua fetchAttachmentViewUrl khi cần.
export interface DisplayAttachment extends Attachment {
  previewUrl?: string;
}

/** 1 file đính kèm (ảnh/PDF/Word) — dùng chung cho Quote Chat và Release Note
 * (đính kèm minh chứng): ảnh hiện thumbnail (lazy-load view URL nếu không có
 * sẵn `previewUrl` cục bộ), bấm vào mở lightbox xem full trong CÙNG tab (đóng
 * bằng nút X hoặc bấm ra ngoài ảnh/nhấn Esc). PDF/Word vẫn mở tab mới (không
 * xem trực tiếp trong trang được) — hiện icon + tên.*/
export function AttachmentBadge({ attachment }: { attachment: DisplayAttachment }) {
  const [viewUrl, setViewUrl] = useState<string | null>(attachment.previewUrl || null);
  const [opening, setOpening] = useState(false);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const isImage = attachment.media_type.startsWith("image/");

  useEffect(() => {
    if (!isImage || viewUrl) return;
    let cancelled = false;
    fetchAttachmentViewUrl(attachment.file_key)
      .then((url) => {
        if (!cancelled) setViewUrl(url);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [isImage, viewUrl, attachment.file_key]);

  useEffect(() => {
    if (!lightboxOpen) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setLightboxOpen(false);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [lightboxOpen]);

  async function openFile() {
    if (isImage) {
      if (!viewUrl) {
        setOpening(true);
        try {
          setViewUrl(await fetchAttachmentViewUrl(attachment.file_key));
        } catch {
          setOpening(false);
          return;
        }
        setOpening(false);
      }
      setLightboxOpen(true);
      return;
    }
    if (viewUrl) {
      window.open(viewUrl, "_blank", "noopener,noreferrer");
      return;
    }
    setOpening(true);
    try {
      const url = await fetchAttachmentViewUrl(attachment.file_key);
      window.open(url, "_blank", "noopener,noreferrer");
    } catch {
      // Bỏ qua — có thể URL đã hết hạn hoặc file không còn tồn tại.
    } finally {
      setOpening(false);
    }
  }

  if (isImage) {
    return (
      <>
        <button
          type="button"
          onClick={openFile}
          title={attachment.file_name}
          className="block w-16 h-16 rounded-lg overflow-hidden border border-border-soft shrink-0 bg-tint/30"
        >
          {viewUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={viewUrl} alt={attachment.file_name} className="w-full h-full object-cover" />
          ) : (
            <span className="w-full h-full flex items-center justify-center">
              {opening ? <Loader2 size={18} className="animate-spin text-muted-light" /> : <ImageIcon size={18} className="text-muted-light" />}
            </span>
          )}
        </button>

        {lightboxOpen && viewUrl && (
          <div
            className="fixed inset-0 z-[100] bg-black/80 flex items-center justify-center p-4 sm:p-8"
            onClick={() => setLightboxOpen(false)}
          >
            <button
              type="button"
              onClick={() => setLightboxOpen(false)}
              aria-label="Đóng"
              className="absolute top-4 right-4 text-white/80 hover:text-white p-2 rounded-full hover:bg-white/10 transition-colors"
            >
              <X size={24} />
            </button>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={viewUrl}
              alt={attachment.file_name}
              className="max-w-full max-h-full object-contain rounded-lg shadow-2xl"
              onClick={(e) => e.stopPropagation()}
            />
          </div>
        )}
      </>
    );
  }

  const isPdf = attachment.media_type === "application/pdf";
  return (
    <button
      type="button"
      onClick={openFile}
      disabled={opening}
      title={attachment.file_name}
      className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-border-soft bg-surface text-[11.5px] max-w-[170px] hover:border-primary transition-colors disabled:opacity-60"
    >
      <FileText size={14} className={clsx("shrink-0", isPdf ? "text-red-500" : "text-blue-500")} />
      <span className="truncate">{attachment.file_name}</span>
      {opening && <Loader2 size={11} className="animate-spin shrink-0" />}
    </button>
  );
}
