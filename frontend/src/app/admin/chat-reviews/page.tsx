"use client";

import { Suspense, useEffect, useLayoutEffect, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { MessageSquareText, ThumbsUp, ThumbsDown, HelpCircle, AlertCircle, ChevronLeft, ChevronRight, ArrowLeft } from "lucide-react";
import { format } from "date-fns";
import clsx from "clsx";
import { useTranslations, useLocale } from "next-intl";
import { dateFnsLocale } from "@/i18n/dateLocale";
import { api } from "@/lib/api";
import type { AdminChatSessionListResponse, AdminChatSessionSummary, ChatRating } from "@/lib/types";

const PAGE_SIZE = 20;

type RatingFilter = "all" | ChatRating | "none";

const FILTER_TABS: { value: RatingFilter; labelKey: string }[] = [
  { value: "all", labelKey: "filterAll" },
  { value: "good", labelKey: "ratingGood" },
  { value: "bad", labelKey: "ratingBad" },
  { value: "none", labelKey: "ratingNone" },
];

const RATING_BADGE: Record<string, { labelKey: string; icon: React.ReactNode; cls: string }> = {
  good: { labelKey: "ratingGood", icon: <ThumbsUp size={12} />, cls: "bg-tint text-primary-dark border border-primary/20" },
  bad: { labelKey: "ratingBad", icon: <ThumbsDown size={12} />, cls: "bg-red-50 text-down border border-red-200" },
  none: { labelKey: "ratingNone", icon: <HelpCircle size={12} />, cls: "bg-surface-alt text-muted-light border border-border" },
};

// Đoạn text dài (đoạn trích, lý do đánh giá) mặc định cắt còn vài dòng cho bảng
// gọn — nút "Xem chi tiết" chỉ hiện khi text thật sự bị cắt, bấm để mở hết
// nội dung ngay tại chỗ (không phải vào trang chi tiết phiên).
function ExpandableText({ text, className, clampClass = "line-clamp-2" }: { text: string; className?: string; clampClass?: string }) {
  const t = useTranslations("adminUsrChatReviews");
  const ref = useRef<HTMLParagraphElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [overflowing, setOverflowing] = useState(false);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || expanded) return;
    const check = () => setOverflowing(el.scrollHeight > el.clientHeight + 1);
    check();
    const ro = new ResizeObserver(check);
    ro.observe(el);
    return () => ro.disconnect();
  }, [text, expanded]);

  return (
    <div>
      <p ref={ref} className={clsx("whitespace-pre-wrap break-words", className, !expanded && clampClass)}>
        {text}
      </p>
      {(overflowing || expanded) && (
        <button
          type="button"
          onClick={(e) => {
            // Card mobile bọc trong <Link> — chặn điều hướng khi bấm nút này.
            e.preventDefault();
            e.stopPropagation();
            setExpanded((v) => !v);
          }}
          className="mt-1 text-[12px] font-semibold not-italic text-primary hover:text-primary-dark hover:underline"
        >
          {expanded ? t("collapse") : t("expand")}
        </button>
      )}
    </div>
  );
}

// useSearchParams cần bọc Suspense (giống login/page.tsx) — nếu không build sẽ
// báo lỗi khi prerender trang.
export default function AdminChatReviewsPage() {
  return (
    <Suspense fallback={null}>
      <ChatReviewsContent />
    </Suspense>
  );
}

function ChatReviewsContent() {
  const t = useTranslations("adminUsrChatReviews");
  const locale = useLocale();
  // ?user=<email> — "Lịch sử chat" của 1 người dùng (mở từ trang Người dùng):
  // cùng danh sách/bộ lọc đánh giá như trên, chỉ giới hạn phiên của user đó.
  const userEmail = useSearchParams().get("user") || "";
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
    if (userEmail) params.user_email = userEmail;

    api
      .get("/api/admin/chat-sessions", { params })
      .then((res) => {
        if (!cancelled) setData(res.data);
      })
      .catch(() => {
        if (!cancelled) setError(t("listError"));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [filter, page, userEmail]);

  const totalPages = Math.max(1, Math.ceil(data.total / PAGE_SIZE));

  return (
    <div className="space-y-6">
      {userEmail ? (
        <div>
          <Link href="/admin/users" className="inline-flex items-center gap-2 text-sm font-semibold text-body hover:text-primary-dark transition-colors mb-3">
            <ArrowLeft size={16} />
            {t("backToUsers")}
          </Link>
          <h2 className="text-3xl font-bold uppercase tracking-tight text-heading mb-2">{t("historyTitle")}</h2>
          <p className="text-body break-words">
            {t.rich("historyOf", { email: userEmail, b: (c) => <span className="font-semibold text-label">{c}</span> })}
          </p>
        </div>
      ) : (
        <div>
          <h2 className="text-3xl font-bold uppercase tracking-tight text-heading mb-2">{t("title")}</h2>
          <p className="text-body">{t("subtitle")}</p>
        </div>
      )}

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
            {t(tab.labelKey)}
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
          <p className="text-body">{userEmail ? t("noMatchUser") : t("noMatch")}</p>
        </div>
      ) : (
        <>
          {/* Bảng đầy đủ — chỉ hiện từ md trở lên. */}
          <div className="hidden md:block bg-background border border-border rounded-2xl overflow-hidden overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-muted-light text-xs uppercase tracking-wider">
                  <th className="px-4 py-3 font-semibold">{t("colUser")}</th>
                  <th className="px-4 py-3 font-semibold">{t("colReport")}</th>
                  <th className="px-4 py-3 font-semibold">{t("colQuote")}</th>
                  <th className="px-4 py-3 font-semibold">{t("colRating")}</th>
                  <th className="px-4 py-3 font-semibold text-right">{t("colMessages")}</th>
                  <th className="px-4 py-3 font-semibold">{t("colUpdated")}</th>
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
                        <ExpandableText text={s.quote || t("directChat")} className="italic" />
                      </td>
                      <td className="px-4 py-2.5">
                        <span className={clsx("inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-semibold", badge.cls)}>
                          {badge.icon}
                          {t(badge.labelKey)}
                        </span>
                        {s.rating === "bad" && s.rating_reason && (
                          <div className="mt-1 max-w-xs">
                            <ExpandableText text={s.rating_reason} className="text-[12px] text-muted-light" />
                          </div>
                        )}
                      </td>
                      <td className="px-4 py-2.5 text-right text-body">{s.message_count}</td>
                      <td className="px-4 py-2.5 text-body whitespace-nowrap">{format(new Date(s.updated_at), t("dateTimeFmt"), { locale: dateFnsLocale(locale) })}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Dạng card — chỉ hiện dưới md. */}
          <div className="md:hidden space-y-3">
            {data.items.map((s: AdminChatSessionSummary) => {
              const badge = RATING_BADGE[s.rating ?? "none"];
              return (
                <Link
                  key={s.id}
                  href={`/admin/chat-reviews/${s.id}`}
                  className="block bg-background border border-border rounded-2xl p-4 space-y-2"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="font-semibold text-label truncate">{s.user_email}</p>
                      <p className="text-xs text-muted-light">{t("reportMeta", { date: s.report_date, time: format(new Date(s.updated_at), t("dateTimeFmt"), { locale: dateFnsLocale(locale) }) })}</p>
                    </div>
                    <span className={clsx("shrink-0 inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-semibold", badge.cls)}>
                      {badge.icon}
                      {t(badge.labelKey)}
                    </span>
                  </div>
                  {s.quote && <ExpandableText text={s.quote} className="text-sm text-body italic" />}
                  {s.rating === "bad" && s.rating_reason && (
                    <ExpandableText text={s.rating_reason} className="text-[12px] text-muted-light" />
                  )}
                  <p className="text-xs text-muted-light">{t("messageCount", { count: s.message_count })}</p>
                </Link>
              );
            })}
          </div>

          <div className="flex items-center justify-between text-sm text-body">
            <span>
              {t("pageInfo", { page: page + 1, pages: totalPages, total: data.total })}
            </span>
            <div className="flex gap-2">
              <button
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                aria-label={t("prevPage")}
                disabled={page === 0}
                className="p-1.5 rounded-lg border border-border disabled:opacity-40 hover:bg-surface transition-colors"
              >
                <ChevronLeft size={16} />
              </button>
              <button
                onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                aria-label={t("nextPage")}
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
