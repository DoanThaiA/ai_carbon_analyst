"use client";

import { useState } from "react";
import { X, Send, CheckCircle2, AlertCircle } from "lucide-react";
import clsx from "clsx";
import { api } from "@/lib/api";

type ReporterRole = "user" | "admin" | "guest";

const ROLE_OPTIONS: { key: ReporterRole; label: string }[] = [
  { key: "guest", label: "Khách" },
  { key: "user", label: "User" },
  { key: "admin", label: "Admin" },
];

export function JennyFeedbackModal({
  open,
  onClose,
  fixedRole,
}: {
  open: boolean;
  onClose: () => void;
  // Khi mở từ 1 màn hình đã biết vai trò (vd đang đọc báo cáo với tư cách
  // user đã đăng nhập) — cố định role, không cho chọn lại, tránh user tự
  // nhận nhầm là "Admin"/"Khách".
  fixedRole?: ReporterRole;
}) {
  const [reporterName, setReporterName] = useState("");
  const [reporterRole, setReporterRole] = useState<ReporterRole>(fixedRole ?? "guest");
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [sent, setSent] = useState(false);

  if (!open) return null;

  const handleClose = () => {
    // Reset để lần mở tiếp theo là 1 form trống, không giữ lại nội dung đã gửi.
    setReporterName("");
    setReporterRole(fixedRole ?? "guest");
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
        reporter_role: reporterRole,
        content: content.trim(),
      });
      setSent(true);
    } catch (err: any) {
      setError(err.response?.data?.detail || "Không gửi được phản ánh, vui lòng thử lại.");
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
          title="Đóng"
        >
          <X size={18} />
        </button>

        <h2 className="text-lg font-bold text-heading mb-2">Phản ánh thái độ của Jenny</h2>

        {sent ? (
          <div className="py-6 text-center">
            <CheckCircle2 size={40} className="mx-auto text-primary mb-3" />
            <p className="text-body">Cảm ơn bạn đã phản ánh. Nội dung đã được gửi tới Sếp của Jenny.</p>
            <button onClick={handleClose} className="btn-pill mt-5 justify-center py-2 px-5">
              Đóng
            </button>
          </div>
        ) : (
          <>
            <p className="text-sm text-body mb-5">
              Nếu Jenny có thái độ không tốt, phục vụ không nhiệt tình, hiệu quả hợp tác không cao
              .v.v. hãy phản ánh trực tiếp lên Sếp của Jenny (em).
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
                  Tên <span className="text-muted-light font-normal">(không bắt buộc)</span>
                </label>
                <input
                  type="text"
                  value={reporterName}
                  onChange={(e) => setReporterName(e.target.value)}
                  maxLength={200}
                  className="w-full px-3 py-2.5 border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary"
                />
              </div>

              {!fixedRole && (
                <div>
                  <label className="block text-sm font-semibold text-label mb-1.5">Chức danh</label>
                  <div className="flex gap-2">
                    {ROLE_OPTIONS.map(({ key, label }) => (
                      <button
                        key={key}
                        type="button"
                        onClick={() => setReporterRole(key)}
                        className={clsx(
                          "flex-1 py-2 rounded-lg text-sm font-semibold border transition-colors",
                          reporterRole === key
                            ? "bg-primary text-white border-primary"
                            : "border-border text-body hover:border-primary/40"
                        )}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <div>
                <label className="block text-sm font-semibold text-label mb-1.5">Nội dung phản ánh</label>
                <textarea
                  required
                  value={content}
                  onChange={(e) => setContent(e.target.value)}
                  maxLength={4000}
                  rows={4}
                  placeholder="Mô tả cụ thể vấn đề bạn gặp phải..."
                  className="w-full px-3 py-2.5 border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary resize-none"
                />
              </div>

              <button
                type="submit"
                disabled={loading || !content.trim()}
                className="btn-pill w-full justify-center py-2.5 gap-2"
              >
                <Send size={15} />
                {loading ? "Đang gửi..." : "Gửi"}
              </button>
            </form>
          </>
        )}
      </div>
    </div>
  );
}
