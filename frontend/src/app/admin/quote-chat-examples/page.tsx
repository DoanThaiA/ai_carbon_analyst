"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Sparkles, Trash2, AlertCircle, Quote as QuoteIcon } from "lucide-react";
import { format } from "date-fns";
import { api } from "@/lib/api";
import type { QuoteChatExample } from "@/lib/types";

export default function AdminQuoteChatExamplesPage() {
  const [examples, setExamples] = useState<QuoteChatExample[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const fetchExamples = async () => {
    try {
      const res = await api.get("/api/admin/quote-chat-examples");
      setExamples(res.data);
    } catch {
      setError("Không thể tải danh sách đoạn chat tham khảo.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchExamples();
  }, []);

  const handleDelete = async (id: number) => {
    if (!confirm("Xoá đoạn chat tham khảo này? Quote Chat sẽ không tham khảo nó nữa.")) return;
    setDeletingId(id);
    try {
      await api.delete(`/api/admin/quote-chat-examples/${id}`);
      setExamples((prev) => prev.filter((e) => e.id !== id));
    } catch (err: any) {
      alert(err.response?.data?.detail || "Lỗi khi xoá đoạn chat tham khảo.");
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-3xl font-bold tracking-tight text-heading mb-2">Đoạn Chat Tham Khảo</h2>
        <p className="text-body">
          Các cặp hỏi-đáp admin chọn từ mục{" "}
          <Link href="/admin/chat-reviews" className="text-primary-dark hover:underline font-semibold">
            Đánh giá chat
          </Link>{" "}
          — được tiêm vào system prompt của mọi phiên Quote Chat sau đó làm chuẩn tham khảo về cách suy luận/văn
          phong (không dùng để lấy lại số liệu cụ thể).
        </p>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg flex items-start gap-3">
          <AlertCircle size={20} className="shrink-0 mt-0.5" />
          <p>{error}</p>
        </div>
      )}

      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="bg-surface border border-border rounded-2xl h-28 animate-pulse" />
          ))}
        </div>
      ) : examples.length === 0 ? (
        <div className="text-center py-16 bg-surface border border-border-soft border-dashed rounded-2xl">
          <Sparkles size={40} className="mx-auto text-muted mb-3" />
          <p className="text-body">
            Chưa có đoạn chat tham khảo nào. Vào{" "}
            <Link href="/admin/chat-reviews" className="text-primary-dark hover:underline font-semibold">
              Đánh giá chat
            </Link>
            , mở 1 phiên và bấm &quot;Để tham khảo&quot; ở câu trả lời phù hợp.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {examples.map((ex) => (
            <div key={ex.id} className="bg-background border border-border rounded-2xl p-5 space-y-3">
              <div className="flex items-start justify-between gap-3">
                <div className="flex flex-wrap items-center gap-3 text-[12px] text-muted-light">
                  {ex.source_report_date && <span>Báo cáo ngày {ex.source_report_date}</span>}
                  {ex.created_by && <span>Thêm bởi {ex.created_by}</span>}
                  <span>{format(new Date(ex.created_at), "HH:mm dd/MM/yyyy")}</span>
                </div>
                <button
                  onClick={() => handleDelete(ex.id)}
                  disabled={deletingId === ex.id}
                  className="p-1.5 rounded hover:bg-red-50 text-down disabled:opacity-40 shrink-0"
                  title="Xoá đoạn chat tham khảo"
                >
                  <Trash2 size={16} />
                </button>
              </div>

              {ex.source_quote && (
                <div className="rounded-xl bg-tint/40 border border-border-soft px-3 py-2 flex gap-2 items-start">
                  <QuoteIcon size={13} className="text-primary shrink-0 mt-0.5" />
                  <p className="text-[12.5px] leading-relaxed text-body italic line-clamp-2">{ex.source_quote}</p>
                </div>
              )}

              <div>
                <p className="text-[11px] font-semibold text-muted-light uppercase tracking-wide mb-1">Câu hỏi</p>
                <p className="text-[13.5px] leading-relaxed text-label">{ex.question}</p>
              </div>
              <div>
                <p className="text-[11px] font-semibold text-muted-light uppercase tracking-wide mb-1">Câu trả lời mẫu</p>
                <p className="text-[13.5px] leading-relaxed text-body whitespace-pre-wrap">{ex.answer}</p>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
