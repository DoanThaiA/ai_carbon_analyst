"use client";

import Image from "next/image";
import Link from "next/link";
import { useState } from "react";
import { useTranslations } from "next-intl";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import clsx from "clsx";
import { ADMIN_HOME_HREF } from "@/lib/adminNav";
import { AdminNavList } from "./AdminNavList";

const COLLAPSED_KEY = "jenny.adminSidebarCollapsed";

function readCollapsed(): boolean {
  try {
    return typeof window !== "undefined" && localStorage.getItem(COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

/**
 * Sidebar admin desktop (từ md): logo/thương hiệu → AdminNavList → nút thu gọn. Sticky dưới
 * header; thu gọn thành dải icon để nhường chỗ cho báo cáo — lựa chọn được nhớ theo trình
 * duyệt (localStorage, lỗi/không có thì mặc định mở rộng). Dưới md dùng drawer trong Header.tsx.
 */
export function AdminSidebar() {
  const t = useTranslations("shellNav");
  // Đọc ngay lúc khởi tạo: admin/layout.tsx chỉ render sidebar sau khi xác thực ở client
  // (SSR trả null), nên không lệch hydration.
  const [collapsed, setCollapsed] = useState(readCollapsed);

  const toggle = () => {
    setCollapsed((prev) => {
      try {
        localStorage.setItem(COLLAPSED_KEY, prev ? "0" : "1");
      } catch {}
      return !prev;
    });
  };

  return (
    <aside
      className={clsx(
        "hidden md:flex print:hidden shrink-0 flex-col sticky top-20 self-start max-h-[calc(100vh-7rem)]",
        "rounded-2xl border border-border bg-background shadow-[0_1px_2px_rgba(16,24,40,0.04)]",
        "transition-[width] duration-300 ease-[cubic-bezier(.4,0,.2,1)]",
        collapsed ? "w-[68px]" : "w-64",
      )}
      aria-label={t("navLabel")}
    >
      <Link
        href={ADMIN_HOME_HREF}
        className={clsx(
          "flex items-center gap-3 border-b border-border/70 rounded-t-2xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
          collapsed ? "justify-center p-3" : "px-4 py-3",
        )}
      >
        <Image src="/jenny.jpg" alt="Jenny AI" width={36} height={36} className="h-9 w-9 rounded-full object-cover shrink-0 ring-2 ring-tint" />
        {!collapsed && (
          <span className="min-w-0">
            <span className="block text-[15px] font-bold text-heading leading-tight truncate">Jenny AI</span>
            <span className="block text-xs text-muted-light truncate">{t("brandSubtitle")}</span>
          </span>
        )}
      </Link>

      <nav className={clsx("flex-1 overflow-y-auto overflow-x-hidden", collapsed ? "p-2" : "px-3 py-2.5")}>
        <AdminNavList collapsed={collapsed} />
      </nav>

      <div className={clsx("border-t border-border/70", collapsed ? "p-2" : "px-3 py-1.5")}>
        <button
          type="button"
          onClick={toggle}
          aria-expanded={!collapsed}
          title={collapsed ? t("expand") : t("collapse")}
          className={clsx(
            "flex w-full items-center gap-3 rounded-xl text-xs font-semibold text-muted-light hover:bg-surface hover:text-heading transition-colors",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
            collapsed ? "justify-center p-2" : "px-3 py-1.5",
          )}
        >
          {collapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}
          {!collapsed && <span>{t("collapse")}</span>}
        </button>
      </div>
    </aside>
  );
}
