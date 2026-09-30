"use client";

import { Suspense, useEffect, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  MessagesSquare,
  ThumbsUp,
  ThumbsDown,
  HelpCircle,
  AlertCircle,
  ChevronLeft,
  ChevronRight,
  ArrowLeft,
  Quote as QuoteIcon,
  Mail,
  Calendar,
  Loader2,
} from "lucide-react";
import { format } from "date-fns";
import clsx from "clsx";
import { api } from "@/lib/api";
import type {
  AdminChatSessionDetail,
  AdminChatSessionListResponse,
  AdminChatSessionSummary,
  ChatRating,
} from "@/lib/types";

// Lịch sử chat theo session: cột trái = danh sách phiên (lọc theo người dùng /
// đánh giá), cột phải = toàn bộ hội thoại của phiên đang chọn. Khác trang "Đánh
// giá chat" (bảng rà soát chất lượng, mỗi phiên mở sang trang chi tiết riêng) —
// ở đây đọc lần lượt từng phiên ngay trên 1 màn hình.
// Trạng thái lọc/phiên đang chọn nằm trên URL (?user=&rating=&session=) để
// copy link gửi cho người khác mở đúng phiên đó.

const PAGE_SIZE = 30;

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

interface AllowedUser {
  id: number;
  email: string;
}

function RatingBadge({ rating }: { rating: ChatRating | null }) {
  const badge = RATING_BADGE[rating ?? "none"];
  return (
    <span className={clsx("shrink-0 inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold", badge.cls)}>
      {badge.icon}
      {badge.label}
    </span>
  );
}

// useSearchParams cần bọc Suspense (giống login/page.tsx) — nếu không build sẽ
// báo lỗi khi prerender trang.
export default function AdminChatHistoryPage() {
  return (
    <Suspense fallback={null}>
      <ChatHistoryContent />
    </Suspense>
  );
}

function ChatHistoryContent() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const userEmail = searchParams.get("user") || "";
  const ratingParam = searchParams.get("rating");
  const filter: RatingFilter = FILTER_TABS.some((t) => t.value === ratingParam) ? (ratingParam as RatingFilter) : "all";
  const selectedId = Number(searchParams.get("session")) || null;

  const [users, setUsers] = useState<AllowedUser[]>([]);
  const [page, setPage] = useState(0);
  const [list, setList] = useState<AdminChatSessionListResponse>({ items: [], total: 0 });
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState("");

  const [detail, setDetail] = useState<AdminChatSessionDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");

  function updateParams(changes: Record<string, string | null>) {
    const next = new URLSearchParams(searchParams.toString());
    for (const [k, v] of Object.entries(changes)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    const qs = next.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  }

  useEffect(() => {
    api
      .get("/api/admin/users")
      .then((res) => setUsers(res.data))
      .catch(() => {
        // Không lấy được danh sách user thì vẫn xem được mọi phiên — chỉ mất dropdown lọc.
      });
  }, []);

  useEffect(() => {
    let cancelled = false;
    setListLoading(true);
    setListError("");
    const params: Record<string, string | number> = { limit: PAGE_SIZE, offset: page * PAGE_SIZE };
    if (filter !== "all") params.rating = filter;
    if (userEmail) params.user_email = userEmail;

    api
      .get("/api/admin/chat-sessions", { params })
      .then((res) => {
        if (!cancelled) setList(res.data);
      })
      .catch(() => {
        if (!cancelled) setListError("Không thể tải danh sách phiên chat.");
      })
      .finally(() => {
        if (!cancelled) setListLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [filter, page, userEmail]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    setDetailError("");
    api
      .get(`/api/admin/chat-sessions/${selectedId}`)
      .then((res) => {
        if (!cancelled) setDetail(res.data);
      })
      .catch((err) => {
        if (!cancelled) {
          setDetail(null);
          setDetailError(err.response?.status === 404 ? "Không tìm thấy phiên chat này." : "Không thể tải nội dung phiên chat.");
        }
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  const totalPages = Math.max(1, Math.ceil(list.total / PAGE_SIZE));

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-3xl font-bold uppercase tracking-tight text-heading mb-2">Lịch Sử Giao Tiếp</h2>
        <p className="text-body">Xem lại từng phiên hỏi đáp AI của người dùng — chọn 1 phiên bên trái để đọc toàn bộ hội thoại</p>
      </div>

      <div className="flex flex-col md:flex-row md:items-center gap-3">
        <select
          value={userEmail}
          onChange={(e) => {
            setPage(0);
            updateParams({ user: e.target.value || null, session: null });
          }}
          className="w-full md:w-72 text-sm px-3 py-2 rounded-lg border border-border-soft bg-background focus:outline-none focus:border-primary"
        >
          <option value="">Tất cả người dùng</option>
          {/* Giữ được lựa chọn khi mở link ?user= của email không còn trong danh sách user (đã xoá). */}
          {userEmail && !users.some((u) => u.email === userEmail) && <option value={userEmail}>{userEmail}</option>}
          {users.map((u) => (
            <option key={u.id} value={u.email}>
              {u.email}
            </option>
          ))}
        </select>

        <div className="flex flex-wrap gap-2">
          {FILTER_TABS.map((tab) => (
            <button
              key={tab.value}
              onClick={() => {
                setPage(0);
                updateParams({ rating: tab.value === "all" ? null : tab.value, session: null });
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
      </div>

      <div className="grid md:grid-cols-[340px_minmax(0,1fr)] gap-4 items-start">
        {/* Cột trái: danh sách phiên. Dưới md chỉ hiện khi chưa chọn phiên. */}
        <div className={clsx("bg-background border border-border rounded-2xl overflow-hidden", selectedId && "hidden md:block")}>
          {listError ? (
            <div className="m-3 bg-red-50 border border-red-200 text-red-700 px-3 py-2.5 rounded-lg flex items-start gap-2 text-sm">
              <AlertCircle size={16} className="shrink-0 mt-0.5" />
              <p>{listError}</p>
            </div>
          ) : listLoading ? (
            <div className="h-64 animate-pulse bg-surface" />
          ) : list.items.length === 0 ? (
            <div className="text-center py-14 px-4">
              <MessagesSquare size={32} className="mx-auto text-muted mb-2" />
              <p className="text-sm text-body">Không có phiên chat nào khớp bộ lọc.</p>
            </div>
          ) : (
            <>
              <ul className="divide-y divide-border md:max-h-[calc(100vh-280px)] md:overflow-y-auto">
                {list.items.map((s: AdminChatSessionSummary) => (
                  <li key={s.id}>
                    <button
                      onClick={() => updateParams({ session: String(s.id) })}
                      className={clsx(
                        "w-full text-left px-4 py-3 border-l-2 transition-colors space-y-1.5",
                        s.id === selectedId ? "border-primary bg-tint/50" : "border-transparent hover:bg-tint/30"
                      )}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <p className="text-[13px] font-semibold text-label truncate min-w-0">{s.user_email}</p>
                        <RatingBadge rating={s.rating} />
                      </div>
                      <p className="text-[12.5px] leading-snug text-body italic line-clamp-2 break-words">{s.quote || "Chat trực tiếp với Jenny (không có đoạn trích)"}</p>
                      <p className="text-[11px] text-muted-light">
                        Báo cáo {s.report_date} · {s.message_count} tin nhắn · {format(new Date(s.updated_at), "HH:mm dd/MM/yyyy")}
                      </p>
                    </button>
                  </li>
                ))}
              </ul>
              <div className="flex items-center justify-between px-4 py-2.5 border-t border-border text-[12px] text-body">
                <span>
                  Trang {page + 1}/{totalPages} — {list.total} phiên
                </span>
                <div className="flex gap-1.5">
                  <button
                    onClick={() => setPage((p) => Math.max(0, p - 1))}
                    disabled={page === 0}
                    className="p-1 rounded-lg border border-border disabled:opacity-40 hover:bg-surface transition-colors"
                    aria-label="Trang trước"
                  >
                    <ChevronLeft size={15} />
                  </button>
                  <button
                    onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                    disabled={page >= totalPages - 1}
                    className="p-1 rounded-lg border border-border disabled:opacity-40 hover:bg-surface transition-colors"
                    aria-label="Trang sau"
                  >
                    <ChevronRight size={15} />
                  </button>
                </div>
              </div>
            </>
          )}
        </div>

        {/* Cột phải: nội dung phiên đang chọn. Dưới md chỉ hiện khi đã chọn phiên. */}
        <div className={clsx("bg-background border border-border rounded-2xl p-5 min-h-64", !selectedId && "hidden md:block")}>
          {selectedId && (
            <button
              onClick={() => updateParams({ session: null })}
              className="md:hidden mb-4 inline-flex items-center gap-2 text-sm font-semibold text-body hover:text-primary-dark transition-colors"
            >
              <ArrowLeft size={16} />
              Danh sách phiên
            </button>
          )}

          {!selectedId ? (
            <div className="h-56 flex flex-col items-center justify-center text-center gap-2">
              <MessagesSquare size={32} className="text-muted" />
              <p className="text-sm text-muted-light">Chọn 1 phiên bên trái để xem toàn bộ hội thoại.</p>
            </div>
          ) : detailLoading ? (
            <div className="h-56 flex items-center justify-center text-muted-light">
              <Loader2 size={20} className="animate-spin" />
            </div>
          ) : detailError ? (
            <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg flex items-start gap-3">
              <AlertCircle size={18} className="shrink-0 mt-0.5" />
              <p>{detailError}</p>
            </div>
          ) : detail ? (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
                  <span className="inline-flex items-center gap-1.5 font-semibold text-label break-all">
                    <Mail size={14} className="text-primary shrink-0" />
                    {detail.user_email}
                  </span>
                  <span className="inline-flex items-center gap-1.5 text-body">
                    <Calendar size={14} className="text-primary shrink-0" />
                    Báo cáo {detail.report_date}
                  </span>
                  <span className="text-muted-light">Bắt đầu {format(new Date(detail.created_at), "HH:mm dd/MM/yyyy")}</span>
                </div>
                <RatingBadge rating={detail.rating} />
              </div>

              <div className="rounded-xl bg-tint/40 border border-border-soft px-4 py-3 flex gap-2 items-start">
                <QuoteIcon size={14} className="text-primary shrink-0 mt-0.5" />
                <p className="text-[13.5px] leading-relaxed text-body italic whitespace-pre-wrap break-words min-w-0">{detail.quote || "Chat trực tiếp với Jenny (không có đoạn trích)"}</p>
              </div>

              {detail.rating === "bad" && detail.rating_reason && (
                <div className="rounded-xl bg-red-50 border border-red-200 px-4 py-3">
                  <p className="text-xs font-semibold text-down mb-1">Lý do đánh giá không tốt</p>
                  <p className="text-[13.5px] leading-relaxed text-body whitespace-pre-wrap break-words">{detail.rating_reason}</p>
                </div>
              )}

              <div className="space-y-3 pt-2 border-t border-border">
                {detail.messages.length === 0 ? (
                  <p className="text-sm text-muted-light pt-2">Phiên này chưa có tin nhắn nào.</p>
                ) : (
                  detail.messages.map((m) => (
                    <div key={m.id} className={clsx("flex flex-col pt-1", m.role === "user" ? "items-end" : "items-start")}>
                      <span className="text-[11px] text-muted-light mb-1 px-1">{m.role === "user" ? "Người dùng" : "AI"}</span>
                      <div
                        className={clsx(
                          "max-w-[85%] rounded-2xl px-3.5 py-2.5 text-[13.5px] leading-relaxed whitespace-pre-wrap break-words",
                          m.role === "user"
                            ? "bg-primary text-white rounded-br-sm"
                            : "bg-surface text-body rounded-bl-sm border border-border"
                        )}
                      >
                        {m.content}
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
