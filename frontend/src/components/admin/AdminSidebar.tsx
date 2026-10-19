"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { Pin, PinOff } from "lucide-react";
import clsx from "clsx";
import { ADMIN_HOME_HREF } from "@/lib/adminNav";
import { AdminNavList } from "./AdminNavList";

const PINNED_KEY = "jenny.adminSidebarPinned";
// Trễ mở/đóng khi rê chuột — lướt ngang qua dải icon không làm menu bật ra, rời khỏi menu một
// chút (vd trượt chuột ra mép) không làm nó đóng ngay.
const OPEN_DELAY_MS = 120;
const CLOSE_DELAY_MS = 220;

function readPinned(): boolean {
  try {
    return typeof window !== "undefined" && localStorage.getItem(PINNED_KEY) === "1";
  } catch {
    return false;
  }
}

/**
 * Sidebar admin desktop (từ md): mặc định là dải icon hẹp, RÊ CHUỘT (hoặc focus bàn phím) vào thì
 * mở rộng dạng lớp nổi đè lên nội dung — không đẩy layout, báo cáo giữ nguyên bề rộng. Nút ghim
 * giữ menu luôn mở (chiếm chỗ trong layout); lựa chọn ghim được nhớ theo trình duyệt
 * (localStorage, lỗi/không có thì mặc định không ghim). Dưới md dùng drawer trong Header.tsx.
 */
export function AdminSidebar() {
  const t = useTranslations("shellNav");
  // Đọc ngay lúc khởi tạo: admin/layout.tsx chỉ render sidebar sau khi xác thực ở client
  // (SSR trả null), nên không lệch hydration.
  const [pinned, setPinned] = useState(readPinned);
  const [hovered, setHovered] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const expanded = pinned || hovered;

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  const schedule = (next: boolean) => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setHovered(next), next ? OPEN_DELAY_MS : CLOSE_DELAY_MS);
  };

  const togglePin = () => {
    setPinned((prev) => {
      try {
        localStorage.setItem(PINNED_KEY, prev ? "0" : "1");
      } catch {}
      return !prev;
    });
    // Bỏ ghim khi chuột còn trên menu → vẫn mở tới khi rời chuột, không giật đóng.
    setHovered(true);
  };

  return (
    // Khung giữ chỗ trong layout: hẹp khi không ghim (menu mở rộng nổi đè lên nội dung).
    <div
      className={clsx(
        "hidden md:block print:hidden shrink-0 relative z-40 sticky top-20 self-start h-[calc(100vh-7rem)]",
        "transition-[width] duration-300 ease-[cubic-bezier(.4,0,.2,1)]",
        pinned ? "w-64" : "w-[68px]",
      )}
    >
      <aside
        onMouseEnter={() => schedule(true)}
        onMouseLeave={() => schedule(false)}
        onFocus={() => schedule(true)}
        onBlur={(e) => {
          if (!e.currentTarget.contains(e.relatedTarget as Node | null)) schedule(false);
        }}
        className={clsx(
          "absolute left-0 top-0 flex max-h-full flex-col overflow-hidden rounded-2xl border border-border bg-background",
          "transition-[width,box-shadow] duration-300 ease-[cubic-bezier(.4,0,.2,1)]",
          expanded ? "w-64" : "w-[68px]",
          expanded && !pinned
            ? "shadow-[0_16px_40px_-12px_rgba(16,24,40,0.28)]"
            : "shadow-[0_1px_2px_rgba(16,24,40,0.04)]",
        )}
        aria-label={t("navLabel")}
      >
        <Link
          href={ADMIN_HOME_HREF}
          className={clsx(
            "flex items-center gap-3 border-b border-border/70 rounded-t-2xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
            expanded ? "px-4 py-3" : "justify-center p-3",
          )}
        >
          <Image src="/jenny.jpg" alt="Jenny AI" width={36} height={36} className="h-9 w-9 rounded-full object-cover shrink-0 ring-2 ring-tint" />
          {expanded && (
            // Bề rộng cố định: chữ không bị bẻ dòng liên tục trong lúc menu đang giãn ra.
            <span className="min-w-0 w-44 shrink-0">
              <span className="block text-[15px] font-bold text-heading leading-snug">Trung tâm điều hành carbon / Phòng CLPT / SIM</span>
              <span className="block text-xs text-muted-light truncate">{t("brandSubtitle")}</span>
            </span>
          )}
        </Link>

        <nav className={clsx("flex-1 overflow-y-auto overflow-x-hidden", expanded ? "px-3 py-2.5" : "p-2")}>
          <AdminNavList collapsed={!expanded} />
        </nav>

        <div className={clsx("border-t border-border/70", expanded ? "px-3 py-1.5" : "p-2")}>
          <button
            type="button"
            onClick={togglePin}
            aria-pressed={pinned}
            title={pinned ? t("unpin") : t("pin")}
            className={clsx(
              "flex w-full items-center gap-3 rounded-xl text-xs font-semibold transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
              pinned ? "text-primary-dark hover:bg-tint" : "text-muted-light hover:bg-surface hover:text-heading",
              expanded ? "px-3 py-1.5" : "justify-center p-2",
            )}
          >
            {pinned ? <PinOff size={18} className="shrink-0" /> : <Pin size={18} className="shrink-0" />}
            {expanded && <span className="whitespace-nowrap">{pinned ? t("unpin") : t("pin")}</span>}
          </button>
        </div>
      </aside>
    </div>
  );
}
