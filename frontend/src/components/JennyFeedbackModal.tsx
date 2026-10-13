"use client";

import { useState } from "react";
import { X, Send, CheckCircle2, AlertCircle } from "lucide-react";
import { useTranslations } from "next-intl";
import { api } from "@/lib/api";

// Chỉ dùng trong màn hình đã đăng nhập (xem QuoteChat.tsx) — API yêu cầu
// cookie session hợp lệ, backend tự lấy email + role từ JWT (payload["sub"]),
// không nhận role/email từ client để tránh giả mạo.
export function JennyFeedbackModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const t = useTranslations("chatFeedback");
  const [reporterName, setReporterName] = useState("");
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [sent, setSent] = useState(false);

  if (!open) return null;

  const handleClose = () => {
    // Reset để lần mở tiếp theo là 1 form trống, không giữ lại nội dung đã gửi.
    setReporterName("");
    setContent("");
    setError("");
    setSent(false);
    onClose();
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!content.trim()) return;
    setLoading(true);
    setError("");
    try {
      await api.post("/api/feedback", {
        reporter_name: reporterName.trim() || null,
        content: content.trim(),
      });
      setSent(true);
    } catch (err: any) {
      setError(err.response?.data?.detail || t("sendFailed"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/40" onClick={handleClose} />
      <div className="relative w-full max-w-md bg-background border border-border rounded-2xl shadow-[var(--shadow-medium)] p-6">
        <button
          onClick={handleClose}
          className="absolute top-4 right-4 text-muted-light hover:text-label transition-colors"
          title={t("close")}
        >
          <X size={18} />
        </button>

        <h2 className="text-lg font-bold text-heading mb-2">{t("title")}</h2>

        {sent ? (
          <div className="py-6 text-center">
            <CheckCircle2 size={40} className="mx-auto text-primary mb-3" />
            <p className="text-body">{t("thanks")}</p>
            <button onClick={handleClose} className="btn-pill mt-5 justify-center py-2 px-5">
              {t("close")}
            </button>
          </div>
        ) : (
          <>
            <p className="text-sm text-body mb-5">
              {t("intro")}
            </p>

            {error && (
              <div className="mb-4 bg-red-50 border border-red-200 text-red-700 px-3 py-2 rounded-lg flex items-start gap-2 text-sm">
                <AlertCircle size={16} className="shrink-0 mt-0.5" />
                <p>{error}</p>
              </div>
            )}

            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="block text-sm font-semibold text-label mb-1.5">
                  {t("displayName")} <span className="text-muted-light font-normal">{t("optional")}</span>
                </label>
                <input
                  type="text"
                  value={reporterName}
                  onChange={(e) => setReporterName(e.target.value)}
                  maxLength={200}
                  className="w-full px-3 py-2.5 border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary"
                />
              </div>

              <div>
                <label className="block text-sm font-semibold text-label mb-1.5">{t("contentLabel")}</label>
                <textarea
                  required
                  value={content}
                  onChange={(e) => setContent(e.target.value)}
                  maxLength={4000}
                  rows={4}
                  placeholder={t("contentPlaceholder")}
                  className="w-full px-3 py-2.5 border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary resize-none"
                />
              </div>

              <button
                type="submit"
                disabled={loading || !content.trim()}
                className="btn-pill w-full justify-center py-2.5 gap-2"
              >
                <Send size={15} />
                {loading ? t("sending") : t("send")}
              </button>
            </form>
          </>
        )}
      </div>
    </div>
  );
}
