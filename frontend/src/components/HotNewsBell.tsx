"use client";

import { useEffect, useRef, useState } from "react";
import { Bell, Flame, ExternalLink } from "lucide-react";
import { api, API_BASE_URL } from "@/lib/api";
import type { HotNewsItem } from "@/lib/types";

const LAST_SEEN_KEY = "hotNewsLastSeenAt";
const MAX_ITEMS = 20;

function timeAgo(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const minutes = Math.floor(diffMs / 60_000);
  if (minutes < 1) return "vừa xong";
  if (minutes < 60) return `${minutes} phút trước`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} giờ trước`;
  return `${Math.floor(hours / 24)} ngày trước`;
}

/**
 * Chuông thông báo Hot News trên header. Nạp danh sách ban đầu 1 lần khi mount
 * (GET /api/news/hot), sau đó KHÔNG polling — giữ 1 kết nối SSE (/api/news/hot/stream)
 * và chỉ nhận tin mới đúng lúc crawl pipeline thực sự phát hiện + lưu 1 bài
 * hot news (Postgres NOTIFY, xem services/hot_news_broadcast.py ở backend).
 * Trạng thái đã đọc lưu ở localStorage (per-browser).
 */
export function HotNewsBell() {
  const [items, setItems] = useState<HotNewsItem[]>([]);
  const [open, setOpen] = useState(false);
  const [lastSeenAt, setLastSeenAt] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    try {
      setLastSeenAt(Number(localStorage.getItem(LAST_SEEN_KEY)) || 0);
    } catch {
      // localStorage có thể ném lỗi ở 1 số trình duyệt/chế độ riêng tư — bỏ qua, coi như chưa xem gì.
    }
  }, []);

  // Nạp danh sách ban đầu 1 lần — SSE bên dưới chỉ mang tin MỚI phát sinh sau
  // khi kết nối, không phải lịch sử.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.get(`/api/news/hot?limit=${MAX_ITEMS}`);
        if (!cancelled) setItems(res.data.items || []);
      } catch {
        // Im lặng bỏ qua (chưa đăng nhập, mất mạng...) — chuông chỉ là tiện ích phụ, không nên làm hỏng trang.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Giữ 1 kết nối SSE — chỉ nhận event khi crawl pipeline thực sự lưu 1 bài hot
  // news mới (không phải hẹn giờ). withCredentials để trình duyệt gửi kèm cookie
  // session (access_token) cho endpoint yêu cầu đăng nhập. Trình duyệt tự động
  // reconnect nếu kết nối rớt (theo chuẩn SSE), không cần tự viết retry.
  useEffect(() => {
    const source = new EventSource(`${API_BASE_URL}/api/news/hot/stream`, { withCredentials: true });

    source.addEventListener("hot_news", (e: MessageEvent) => {
      try {
        const item: HotNewsItem = JSON.parse(e.data);
        setItems((prev) => [item, ...prev.filter((i) => i.id !== item.id)].slice(0, MAX_ITEMS));
      } catch {
        // bỏ qua payload lỗi định dạng
      }
    });

    source.onerror = () => {
      source.close();
    };

    return () => source.close();
  }, []);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const unreadCount = items.filter((i) => new Date(i.crawled_at).getTime() > lastSeenAt).length;

  function toggleOpen() {
    setOpen((prev) => {
      const next = !prev;
      if (next) {
        const now = Date.now();
        setLastSeenAt(now);
        try {
          localStorage.setItem(LAST_SEEN_KEY, String(now));
        } catch {
          // bỏ qua nếu localStorage không khả dụng
        }
      }
      return next;
    });
  }

  function openArticle(item: HotNewsItem) {
    window.open(item.url, "_blank", "noopener,noreferrer");
  }

  return (
    <div ref={containerRef} className="relative">
      <button
        onClick={toggleOpen}
        className="relative text-white/80 hover:text-white flex items-center justify-center w-9 h-9 rounded-full bg-white/10 hover:bg-white/20 transition-colors"
        title="Hot News"
      >
        <Bell size={16} />
        {unreadCount > 0 && (
          <span className="absolute -top-1 -right-1 min-w-[16px] h-[16px] px-1 rounded-full bg-down text-white text-[10px] font-bold leading-[16px] text-center">
            {unreadCount > 9 ? "9+" : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 mt-2 w-[360px] max-w-[calc(100vw-1rem)] max-h-[70vh] overflow-y-auto bg-background border border-border rounded-xl shadow-[var(--shadow-medium)] z-50">
          <div className="flex items-center gap-2 px-4 py-3 border-b border-border sticky top-0 bg-background">
            <Flame size={15} className="text-down" />
            <span className="font-semibold text-sm text-label">Hot News</span>
          </div>

          {items.length === 0 ? (
            <p className="px-4 py-6 text-[13px] text-muted-light text-center">Chưa có tin hot news nào.</p>
          ) : (
            <ul className="divide-y divide-border">
              {items.map((item) => (
                <li key={item.id}>
                  <button
                    onClick={() => openArticle(item)}
                    className="w-full text-left px-4 py-3 hover:bg-surface transition-colors flex flex-col gap-1"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <span className="text-[13.5px] font-medium text-label leading-snug">{item.title || item.url}</span>
                      <ExternalLink size={12} className="shrink-0 mt-0.5 text-muted-light" />
                    </div>
                    {item.hot_news_reason && (
                      <span className="text-[11.5px] text-down font-medium">{item.hot_news_reason}</span>
                    )}
                    <div className="flex items-center gap-2 text-[11px] text-muted-light font-mono">
                      <span>{item.source}</span>
                      <span>·</span>
                      <span>{timeAgo(item.crawled_at)}</span>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
