"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  ArrowLeft,
  AlertCircle,
  ThumbsUp,
  ThumbsDown,
  HelpCircle,
  Quote as QuoteIcon,
  Mail,
  Calendar,
  BookmarkPlus,
  BookmarkCheck,
  Loader2,
} from "lucide-react";
import { format } from "date-fns";
import clsx from "clsx";
import { api } from "@/lib/api";
import type { AdminChatMessage, AdminChatSessionDetail } from "@/lib/types";

const RATING_BADGE: Record<string, { label: string; icon: React.ReactNode; cls: string }> = {
  good: { label: "Tốt", icon: <ThumbsUp size={13} />, cls: "bg-tint text-primary-dark border border-primary/20" },
  bad: { label: "Không tốt", icon: <ThumbsDown size={13} />, cls: "bg-red-50 text-down border border-red-200" },
  none: { label: "Chưa đánh giá", icon: <HelpCircle size={13} />, cls: "bg-surface-alt text-muted-light border border-border" },
};

export default function AdminChatReviewDetailPage() {
  const params = useParams();
  const sessionId = params.id as string;

  const [session, setSession] = useState<AdminChatSessionDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyMessageId, setBusyMessageId] = useState<number | null>(null);

  const fetchSession = useCallback(async () => {
    try {
      const res = await api.get(`/api/admin/chat-sessions/${sessionId}`);
      setSession(res.data);
    } catch (err: any) {
      setError(err.response?.status === 404 ? "Không tìm thấy phiên chat này." : "Lỗi kết nối đến server.");
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    if (sessionId) fetchSession();
  }, [sessionId, fetchSession]);

  async function toggleExample(m: AdminChatMessage) {
    if (!session || busyMessageId) return;
    setBusyMessageId(m.id);
    try {
      if (m.example_id) {
        await api.delete(`/api/admin/quote-chat-examples/${m.example_id}`);
        setSession((prev) =>
          prev ? { ...prev, messages: prev.messages.map((x) => (x.id === m.id ? { ...x, example_id: null } : x)) } : prev
        );
      } else {
        const res = await api.post("/api/admin/quote-chat-examples", {
          session_id: session.id,
          answer_message_id: m.id,
        });
        setSession((prev) =>
          prev
            ? { ...prev, messages: prev.messages.map((x) => (x.id === m.id ? { ...x, example_id: res.data.id } : x)) }
            : prev
        );
      }
    } catch (err: any) {
      alert(err.response?.data?.detail || "Lỗi khi cập nhật ví dụ mẫu.");
    } finally {
      setBusyMessageId(null);
    }
  }

  return (
    <div className="space-y-6">
      <Link href="/admin/chat-reviews" className="inline-flex items-center gap-2 text-sm font-semibold text-body hover:text-primary-dark transition-colors">
        <ArrowLeft size={16} />
        Quay lại danh sách
      </Link>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg flex items-start gap-3">
          <AlertCircle size={20} className="shrink-0 mt-0.5" />
          <p>{error}</p>
        </div>
      )}

      {loading ? (
        <div className="bg-background border border-border rounded-2xl h-96 animate-pulse" />
      ) : session ? (
        <>
          <div className="bg-background border border-border rounded-2xl p-5 space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap items-center gap-4 text-sm text-body">
                <span className="flex items-center gap-1.5 font-semibold text-label">
                  <Mail size={14} className="text-muted-light" />
                  {session.user_email}
                </span>
                <span className="flex items-center gap-1.5">
                  <Calendar size={14} className="text-muted-light" />
                  Báo cáo ngày {session.report_date}
                </span>
                <span className="text-muted-light">Cập nhật {format(new Date(session.updated_at), "HH:mm dd/MM/yyyy")}</span>
              </div>
              {(() => {
                const badge = RATING_BADGE[session.rating ?? "none"];
                return (
                  <span className={clsx("inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold", badge.cls)}>
                    {badge.icon}
                    {badge.label}
                  </span>
                );
              })()}
            </div>

            <div className="rounded-xl bg-tint/40 border border-border-soft px-4 py-3 flex gap-2 items-start">
              <QuoteIcon size={14} className="text-primary shrink-0 mt-0.5" />
              <p className="text-[13.5px] leading-relaxed text-body italic">{session.quote}</p>
            </div>

            {session.rating === "bad" && session.rating_reason && (
              <div className="rounded-xl bg-red-50 border border-red-200 px-4 py-3">
                <p className="text-xs font-semibold text-down mb-1">Lý do đánh giá không tốt</p>
                <p className="text-[13.5px] leading-relaxed text-body">{session.rating_reason}</p>
              </div>
            )}
          </div>

          <div className="bg-background border border-border rounded-2xl p-5">
            <h3 className="text-sm font-semibold text-label mb-4">Nội dung hội thoại</h3>
            <div className="space-y-3">
              {session.messages.length === 0 ? (
                <p className="text-sm text-muted-light">Phiên này chưa có tin nhắn nào.</p>
              ) : (
                session.messages.map((m) => (
                  <div key={m.id} className={clsx("flex flex-col", m.role === "user" ? "items-end" : "items-start")}>
                    <span className="text-[11px] text-muted-light mb-1 px-1">{m.role === "user" ? "Người dùng" : "AI"}</span>
                    <div
                      className={clsx(
                        "max-w-[80%] rounded-2xl px-3.5 py-2.5 text-[13.5px] leading-relaxed whitespace-pre-wrap",
                        m.role === "user"
                          ? "bg-primary text-white rounded-br-sm"
                          : "bg-surface text-body rounded-bl-sm border border-border"
                      )}
                    >
                      {m.content}
                    </div>

                    {m.role === "assistant" && (
                      <button
                        onClick={() => toggleExample(m)}
                        disabled={busyMessageId === m.id}
                        title={
                          m.example_id
                            ? "Đang dùng làm ví dụ mẫu cho Quote Chat — bấm để bỏ"
                            : "Dùng câu trả lời này làm ví dụ mẫu (few-shot) cho Quote Chat"
                        }
                        className={clsx(
                          "mt-1.5 flex items-center gap-1.5 text-[11.5px] font-semibold px-2 py-1 rounded-full transition-colors disabled:opacity-50",
                          m.example_id
                            ? "bg-tint text-primary-dark border border-primary/20"
                            : "text-muted-light border border-border-soft hover:border-primary hover:text-primary-dark"
                        )}
                      >
                        {busyMessageId === m.id ? (
                          <Loader2 size={12} className="animate-spin" />
                        ) : m.example_id ? (
                          <BookmarkCheck size={12} />
                        ) : (
                          <BookmarkPlus size={12} />
                        )}
                        {m.example_id ? "Đã thêm ví dụ mẫu" : "Dùng làm ví dụ mẫu"}
                      </button>
                    )}
                  </div>
                ))
              )}
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}
