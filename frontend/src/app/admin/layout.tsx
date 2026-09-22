"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import Link from "next/link";
import { FileText, Radar, Users, LogOut, MessageSquareText, BrainCircuit, Sparkles, MessageSquareWarning, ClipboardList, Menu, X } from "lucide-react";
import clsx from "clsx";
import { api, setAuthRole } from "@/lib/api";

const NAV_ITEMS = [
  { href: "/admin/reports", label: "Báo cáo", icon: FileText },
  { href: "/admin/price-sources", label: "Nguồn giá", icon: Radar },
  { href: "/admin/users", label: "Người dùng", icon: Users },
  { href: "/admin/chat-reviews", label: "Đánh giá chat", icon: MessageSquareText },
  { href: "/admin/quote-chat-examples", label: "Đoạn chat tham khảo", icon: Sparkles },
  { href: "/admin/eua-framework", label: "Khung phân tích EUA", icon: BrainCircuit },
  { href: "/admin/feedback", label: "Phản ánh về Jenny", icon: MessageSquareWarning },
  { href: "/admin/release-notes", label: "Release Note", icon: ClipboardList },
];

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [checked, setChecked] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    api
      .get("/api/admin/auth/me")
      .then(() => setChecked(true))
      .catch(() => router.replace("/login?as=admin"));
  }, [router]);

  // Đóng menu mobile mỗi khi chuyển trang, tránh menu che nội dung trang mới.
  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  if (!checked) return null;

  const handleLogout = async () => {
    await api.post("/api/admin/auth/logout");
    setAuthRole(null);
    router.replace("/login?as=admin");
  };

  const currentLabel = NAV_ITEMS.find(({ href }) => pathname.startsWith(href))?.label || "Menu";

  const navLinks = (
    <ul className="space-y-1">
      {NAV_ITEMS.map(({ href, label, icon: Icon }) => (
        <li key={href}>
          <Link
            href={href}
            className={clsx(
              "flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm font-semibold transition-colors duration-300 ease-in-out",
              pathname.startsWith(href)
                ? "bg-tint text-primary-dark"
                : "text-body hover:bg-surface"
            )}
          >
            <Icon size={16} />
            {label}
          </Link>
        </li>
      ))}
    </ul>
  );

  const logoutButton = (
    <button
      onClick={handleLogout}
      className="mt-3 w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm font-semibold text-body hover:bg-surface transition-colors duration-300 ease-in-out"
    >
      <LogOut size={16} />
      Đăng xuất
    </button>
  );

  return (
    <div className="flex flex-col md:flex-row gap-4 md:gap-8">
      {/* Thanh menu mobile — burger toggle, chỉ hiện dưới breakpoint md (sidebar
          cố định bên dưới chỉ hiện từ md trở lên). */}
      <div className="md:hidden bg-background border border-border rounded-2xl p-3">
        <button
          onClick={() => setMobileOpen(v => !v)}
          className="w-full flex items-center justify-between gap-2.5 px-1 py-1 text-sm font-semibold text-label"
        >
          <span className="flex items-center gap-2.5">
            <Menu size={18} className={clsx(mobileOpen && "hidden")} />
            <X size={18} className={clsx(!mobileOpen && "hidden")} />
            {currentLabel}
          </span>
        </button>
        {mobileOpen && (
          <div className="mt-3 pt-3 border-t border-border">
            {navLinks}
            {logoutButton}
          </div>
        )}
      </div>

      {/* Sidebar cố định — chỉ hiện từ md trở lên. */}
      <nav className="hidden md:block md:w-56 shrink-0 bg-background border border-border rounded-2xl p-3 h-fit md:sticky md:top-20">
        {navLinks}
        {logoutButton}
      </nav>
      <div className="flex-1 min-w-0">{children}</div>
    </div>
  );
}
