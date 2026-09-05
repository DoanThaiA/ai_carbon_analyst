"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { MessageSquareText, ThumbsUp, ThumbsDown, HelpCircle, AlertCircle, ChevronLeft, ChevronRight } from "lucide-react";
import { format } from "date-fns";
import clsx from "clsx";
import { api } from "@/lib/api";
import type { AdminChatSessionListResponse, AdminChatSessionSummary, ChatRating } from "@/lib/types";

const PAGE_SIZE = 20;

type RatingFilter = "all" | ChatRating | "none";

const FILTER_TABS: { value: RatingFilter; label: string }[] = [
  { value: "all", label: "Tất cả" },
  { value: "good", label: "Tốt" },
  { value: "bad", label: "Không tốt" },
  { value: "none", label: "Chưa đánh giá" },
];

const RATING_BADGE: Record<string, { label: string; icon: React.ReactNode; cls: string }> = {
  good: { label: "Tốt", icon: <ThumbsUp size={12} />, cls: "bg-tint text-primary-dark border border-primary/20" },
  bad: { label: "Không tốt", icon: <ThumbsDown size={12} />, cls: "bg-red-50 text-down border border-red-200" },
  none: { label: "Chưa đánh giá", icon: <HelpCircle size={12} />, cls: "bg-surface-alt text-muted-light border border-border" },
};

export default function AdminChatReviewsPage() {
  const [filter, setFilter] = useState<RatingFilter>("all");
  const [page, setPage] = useState(0);
  const [data, setData] = useState<AdminChatSessionListResponse>({ items: [], total: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    const params: Record<string, string | number> = { limit: PAGE_SIZE, offset: page * PAGE_SIZE };
    if (filter !== "all") params.rating = filter;

    api
      .get("/api/admin/chat-sessions", { params })
      .then((res) => {
        if (!cancelled) setData(res.data);
      })
      .catch(() => {
        if (!cancelled) setError("Không thể tải danh sách phiên chat.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [filter, page]);

  const totalPages = Math.max(1, Math.ceil(data.total / PAGE_SIZE));

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-3xl font-bold tracking-tight text-heading mb-2">Đánh Giá Chat</h2>
        <p className="text-body">Lịch sử các phiên hỏi đáp AI của người dùng và đánh giá tốt/không tốt kèm lý do</p>
      </div>

      <div className="flex flex-wrap gap-2">
        {FILTER_TABS.map((tab) => (
          <button
            key={tab.value}
            onClick={() => {
              setFilter(tab.value);
              setPage(0);
            }}
            className={clsx(
              "px-3.5 py-1.5 rounded-full text-sm font-semibold transition-colors",
              filter === tab.value ? "bg-primary text-white" : "bg-surface text-body hover:bg-surface-alt"
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg flex items-start gap-3">
          <AlertCircle size={20} className="shrink-0 mt-0.5" />
          <p>{error}</p>
        </div>
      )}

      {loading ? (
        <div className="bg-background border border-border rounded-2xl h-64 animate-pulse" />
      ) : data.items.length === 0 ? (
        <div className="text-center py-16 bg-surface border border-border-soft border-dashed rounded-2xl">
          <MessageSquareText size={40} className="mx-auto text-muted mb-3" />
          <p className="text-body">Không có phiên chat nào khớp bộ lọc.</p>
        </div>
      ) : (
        <>
          <div className="bg-background border border-border rounded-2xl overflow-hidden overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-muted-light text-xs uppercase tracking-wider">
                  <th className="px-4 py-3 font-semibold">Người dùng</th>
                  <th className="px-4 py-3 font-semibold">Báo cáo</th>
                  <th className="px-4 py-3 font-semibold">Đoạn trích</th>
                  <th className="px-4 py-3 font-semibold">Đánh giá</th>
                  <th className="px-4 py-3 font-semibold text-right">Số tin nhắn</th>
                  <th className="px-4 py-3 font-semibold">Cập nhật</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {data.items.map((s: AdminChatSessionSummary) => {
                  const badge = RATING_BADGE[s.rating ?? "none"];
                  return (
                    <tr key={s.id}>
                      <td className="px-4 py-2.5">
                        <Link href={`/admin/chat-reviews/${s.id}`} className="font-semibold text-label hover:text-primary-dark hover:underline">
                          {s.user_email}
                        </Link>
                      </td>
                      <td className="px-4 py-2.5 text-body whitespace-nowrap">{s.report_date}</td>
                      <td className="px-4 py-2.5 text-body max-w-xs">
                        <p className="line-clamp-2 italic">{s.quote}</p>
                      </td>
                      <td className="px-4 py-2.5">
                        <span className={clsx("inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-semibold", badge.cls)}>
                          {badge.icon}
                          {badge.label}
                        </span>
                        {s.rating === "bad" && s.rating_reason && (
                          <p className="mt-1 text-[12px] text-muted-light line-clamp-2 max-w-xs">{s.rating_reason}</p>
                        )}
                      </td>
                      <td className="px-4 py-2.5 text-right text-body">{s.message_count}</td>
                      <td className="px-4 py-2.5 text-body whitespace-nowrap">{format(new Date(s.updated_at), "HH:mm dd/MM/yyyy")}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="flex items-center justify-between text-sm text-body">
            <span>
              Trang {page + 1}/{totalPages} — {data.total} phiên
            </span>
            <div className="flex gap-2">
              <button
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0}
                className="p-1.5 rounded-lg border border-border disabled:opacity-40 hover:bg-surface transition-colors"
              >
                <ChevronLeft size={16} />
              </button>
              <button
                onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                disabled={page >= totalPages - 1}
                className="p-1.5 rounded-lg border border-border disabled:opacity-40 hover:bg-surface transition-colors"
              >
                <ChevronRight size={16} />
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
