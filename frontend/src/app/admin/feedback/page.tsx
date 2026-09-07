"use client";

import { useEffect, useState } from "react";
import { MessageSquareWarning, AlertCircle, ChevronLeft, ChevronRight } from "lucide-react";
import { format } from "date-fns";
import clsx from "clsx";
import { api } from "@/lib/api";
import type { FeedbackListResponse } from "@/lib/types";

const PAGE_SIZE = 20;

const ROLE_BADGE: Record<string, string> = {
  user: "bg-tint text-primary-dark border border-primary/20",
  admin: "bg-red-50 text-down border border-red-200",
  guest: "bg-surface-alt text-muted-light border border-border",
};

const ROLE_LABEL: Record<string, string> = {
  user: "User",
  admin: "Admin",
  guest: "Khách",
};

export default function AdminFeedbackPage() {
  const [page, setPage] = useState(0);
  const [data, setData] = useState<FeedbackListResponse>({ items: [], total: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");

    api
      .get("/api/admin/feedbacks", { params: { limit: PAGE_SIZE, offset: page * PAGE_SIZE } })
      .then((res) => {
        if (!cancelled) setData(res.data);
      })
      .catch(() => {
        if (!cancelled) setError("Không thể tải danh sách phản ánh.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [page]);

  const totalPages = Math.max(1, Math.ceil(data.total / PAGE_SIZE));

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-3xl font-bold tracking-tight text-heading mb-2">Phản Ánh Về Jenny</h2>
        <p className="text-body">Phản ánh của người dùng về thái độ/hiệu quả phục vụ của AI assistant</p>
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
          <MessageSquareWarning size={40} className="mx-auto text-muted mb-3" />
          <p className="text-body">Chưa có phản ánh nào.</p>
        </div>
      ) : (
        <>
          <div className="bg-background border border-border rounded-2xl overflow-hidden overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-muted-light text-xs uppercase tracking-wider">
                  <th className="px-4 py-3 font-semibold">Tên</th>
                  <th className="px-4 py-3 font-semibold">Chức danh</th>
                  <th className="px-4 py-3 font-semibold">Nội dung</th>
                  <th className="px-4 py-3 font-semibold">Thời gian</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {data.items.map((f) => (
                  <tr key={f.id}>
                    <td className="px-4 py-2.5 font-semibold text-label whitespace-nowrap">
                      {f.reporter_name || <span className="text-muted-light font-normal">Ẩn danh</span>}
                    </td>
                    <td className="px-4 py-2.5">
                      <span className={clsx("inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold", ROLE_BADGE[f.reporter_role])}>
                        {ROLE_LABEL[f.reporter_role] ?? f.reporter_role}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-body max-w-md">
                      <p className="whitespace-pre-wrap">{f.content}</p>
                    </td>
                    <td className="px-4 py-2.5 text-body whitespace-nowrap">{format(new Date(f.created_at), "HH:mm dd/MM/yyyy")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex items-center justify-between text-sm text-body">
            <span>
              Trang {page + 1}/{totalPages} — {data.total} phản ánh
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
