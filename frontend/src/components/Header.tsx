"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { LogoutButton } from "@/components/LogoutButton";
import { HotNewsBell } from "@/components/HotNewsBell";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { Settings, Menu, X, Plug, ChevronRight } from "lucide-react";
import { api } from "@/lib/api";
import { ADMIN_HOME_HREF, activeAdminItem } from "@/lib/adminNav";
import { AdminNavList } from "@/components/admin/AdminNavList";

export function Header() {
  const t = useTranslations("shellHeader");
  const tNav = useTranslations("shellNav");
  const pathname = usePathname();
  const isAdmin = pathname?.startsWith("/admin");
  const isLogin = pathname === "/login";
  const [role, setRole] = useState<string | null>(null);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const logoHref = isAdmin ? ADMIN_HOME_HREF : role ? "/dashboard" : "/";

  useEffect(() => {
    if (!isLogin) {
      api.get("/api/auth/me")
        .then(res => setRole(res.data.role))
        .catch(() => setRole(null));
    }
  }, [isLogin]);

  // Đóng menu mobile mỗi khi chuyển trang, tránh menu che nội dung trang mới.
  useEffect(() => {
    setMobileNavOpen(false);
  }, [pathname]);

  // Chỉ hiện các nút dành cho người đã đăng nhập (Quản trị/Dashboard/Hot
  // News/Đăng xuất) sau khi xác nhận có role — landing page `/` công khai
  // (chưa đăng nhập) không nên hiện các nút này.
  const isAuthenticated = !isLogin && role !== null;

  // Breadcrumb "Quản trị › <Nhóm> › <Trang>" — NGAY DƯỚI header, cùng nguồn ADMIN_NAV_GROUPS
  // với sidebar/drawer nên luôn khớp nhãn.
  const adminActive = activeAdminItem(pathname);

  // Drawer mobile mở thì khoá cuộn trang phía sau + Esc để đóng.
  useEffect(() => {
    if (!mobileNavOpen) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMobileNavOpen(false);
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey);
    };
  }, [mobileNavOpen]);

  return (
    <>
      <header className="relative bg-primary-dark px-6 py-4 flex items-center justify-between sticky top-0 z-50 print:hidden">
        <Link href={logoHref} className="flex items-center gap-3 min-w-0">
          <Image src="/jenny.jpg" alt="Jenny AI" width={36} height={36} className="h-9 w-9 rounded-full object-cover shrink-0 border border-white/20" />
          <div className="w-[1px] h-5 bg-white/25 mx-1 shrink-0"></div>
          <h1 className="text-base sm:text-xl font-extrabold tracking-tight text-white truncate">
            Trung tâm điều hành carbon / Phòng CLPT / SIM
          </h1>
        </Link>
        <div className="flex items-center gap-3 shrink-0">
          <LanguageSwitcher />
          {isAuthenticated && (
            <>
            {role === "admin" && !isAdmin && (
              <Link
                href={ADMIN_HOME_HREF}
                className="text-white/80 hover:text-white flex items-center gap-1.5 text-sm font-medium transition-colors bg-white/10 hover:bg-white/20 px-3 py-1.5 rounded-full"
                title={t("adminTitle")}
              >
                <Settings size={14} />
                <span className="hidden sm:inline">{t("admin")}</span>
              </Link>
            )}
            {role === "user" && (
              <Link
                href="/connect-claude"
                className="text-white/80 hover:text-white flex items-center gap-1.5 text-sm font-medium transition-colors bg-white/10 hover:bg-white/20 px-3 py-1.5 rounded-full"
                title={t("connectClaudeTitle")}
              >
                <Plug size={14} />
                <span className="hidden md:inline">{t("connectClaude")}</span>
              </Link>
            )}
            <HotNewsBell />
            {isAdmin ? (
              <button
                onClick={() => setMobileNavOpen(v => !v)}
                className="md:hidden text-white/80 hover:text-white p-2 rounded-full hover:bg-white/10 transition-colors"
                aria-label={t("menu")}
                aria-expanded={mobileNavOpen}
              >
                {mobileNavOpen ? <X size={20} /> : <Menu size={20} />}
              </button>
            ) : (
              <LogoutButton />
            )}
            </>
          )}
        </div>

      </header>

      {/* Drawer điều hướng admin dưới md — cùng AdminNavList với sidebar desktop. */}
      {isAdmin && mobileNavOpen && (
        <div className="md:hidden fixed inset-0 z-[60] print:hidden" role="dialog" aria-modal="true" aria-label={tNav("navLabel")}>
          <button
            type="button"
            className="absolute inset-0 bg-black/30 animate-[fadeIn_200ms_ease-out]"
            onClick={() => setMobileNavOpen(false)}
            aria-label={t("closeMenu")}
          />
          <div className="absolute inset-y-0 left-0 flex w-[min(20rem,85vw)] flex-col bg-background shadow-[var(--shadow-medium)] animate-[slideInLeft_250ms_cubic-bezier(.4,0,.2,1)]">
            <div className="flex items-center gap-3 border-b border-border px-4 py-3.5">
              <Image src="/jenny.jpg" alt="Jenny AI" width={36} height={36} className="h-9 w-9 rounded-full object-cover ring-2 ring-tint" />
              <span className="min-w-0 flex-1">
                <span className="block text-[15px] font-bold text-heading leading-tight">Jenny AI</span>
                <span className="block text-xs text-muted-light truncate">{tNav("brandSubtitle")}</span>
              </span>
              <button
                type="button"
                onClick={() => setMobileNavOpen(false)}
                className="rounded-full p-2 text-muted-light hover:bg-surface hover:text-heading focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                aria-label={t("closeMenu")}
              >
                <X size={18} />
              </button>
            </div>
            <nav className="flex-1 overflow-y-auto p-3">
              <AdminNavList />
            </nav>
          </div>
        </div>
      )}

      {isAdmin && (
        <nav aria-label="Breadcrumb" className="px-4 md:px-6 pt-3 print:hidden">
          <ol className="flex items-center gap-1.5 text-xs sm:text-sm font-semibold text-muted-light min-w-0">
            <li className="shrink-0">
              <Link href={ADMIN_HOME_HREF} className="hover:text-primary transition-colors">{t("admin")}</Link>
            </li>
            {adminActive ? (
              <>
                {tNav(adminActive.group.labelKey) !== tNav(adminActive.item.labelKey) && (
                  <>
                    <li aria-hidden="true" className="hidden sm:block"><ChevronRight size={14} /></li>
                    <li className="hidden sm:block shrink-0">{tNav(adminActive.group.labelKey)}</li>
                  </>
                )}
                <li aria-hidden="true"><ChevronRight size={14} /></li>
                <li className="truncate text-heading" aria-current="page">{tNav(adminActive.item.labelKey)}</li>
              </>
            ) : (
              <>
                <li aria-hidden="true"><ChevronRight size={14} /></li>
                <li className="truncate text-heading" aria-current="page">{tNav("home")}</li>
              </>
            )}
          </ol>
        </nav>
      )}
    </>
  );
}
