"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import clsx from "clsx";
import { LogoutButton } from "@/components/LogoutButton";
import { HotNewsBell } from "@/components/HotNewsBell";
import { Settings, Menu, X, LogOut } from "lucide-react";
import { api, setAuthRole } from "@/lib/api";
import { ADMIN_NAV_ITEMS } from "@/lib/adminNav";

export function Header() {
  const pathname = usePathname();
  const router = useRouter();
  const isAdmin = pathname?.startsWith("/admin");
  const isLogin = pathname === "/login";
  const [role, setRole] = useState<string | null>(null);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const logoHref = isAdmin ? "/admin/reports" : role ? "/dashboard" : "/";

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

  // Breadcrumb "Admin/<Tên trang>" — hiện ở thanh riêng NGAY DƯỚI header (không
  // còn thay chỗ tiêu đề "Jenny AI" nữa), cùng nguồn ADMIN_NAV_ITEMS với
  // sidebar/menu mobile bên dưới nên luôn khớp nhãn.
  const adminPageLabel = ADMIN_NAV_ITEMS.find(({ href }) => pathname?.startsWith(href))?.label;

  const handleAdminLogout = async () => {
    await api.post("/api/admin/auth/logout").catch(() => {});
    setAuthRole(null);
    router.replace("/login?as=admin");
  };

  return (
    <div className="sticky top-0 z-50 print:hidden">
      <header className="relative bg-primary-dark px-6 py-4 flex items-center justify-between">
        <Link href={logoHref} className="flex items-center gap-3 min-w-0">
          <Image src="/jenny.jpg" alt="Jenny AI" width={36} height={36} className="h-9 w-9 rounded-full object-cover shrink-0 border border-white/20" />
          <div className="w-[1px] h-5 bg-white/25 mx-1 shrink-0"></div>
          <h1 className="text-xl font-extrabold tracking-tight text-white truncate">
            Jenny AI
          </h1>
        </Link>
        {isAuthenticated && (
          <div className="flex items-center gap-3 shrink-0">
            {role === "admin" && !isAdmin && (
              <Link
                href="/admin/reports"
                className="text-white/80 hover:text-white flex items-center gap-1.5 text-sm font-medium transition-colors bg-white/10 hover:bg-white/20 px-3 py-1.5 rounded-full"
                title="Trang Quản Trị"
              >
                <Settings size={14} />
                <span className="hidden sm:inline">Quản Trị</span>
              </Link>
            )}
            <HotNewsBell />
            {isAdmin ? (
              <button
                onClick={() => setMobileNavOpen(v => !v)}
                className="md:hidden text-white/80 hover:text-white p-2 rounded-full hover:bg-white/10 transition-colors"
                aria-label="Menu"
              >
                {mobileNavOpen ? <X size={20} /> : <Menu size={20} />}
              </button>
            ) : (
              <LogoutButton />
            )}
          </div>
        )}

        {/* Menu mobile admin — điều hướng + đăng xuất, thay cho sidebar (chỉ
            hiện từ md trở lên, xem admin/layout.tsx). */}
        {isAdmin && mobileNavOpen && (
          <div className="md:hidden absolute top-full inset-x-0 bg-primary-dark border-t border-white/10 shadow-lg z-40">
            <ul className="p-3 space-y-1">
              {ADMIN_NAV_ITEMS.map(({ href, label, icon: Icon }) => (
                <li key={href}>
                  <Link
                    href={href}
                    className={clsx(
                      "flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm font-semibold transition-colors",
                      pathname?.startsWith(href) ? "bg-white/15 text-white" : "text-white/80 hover:bg-white/10"
                    )}
                  >
                    <Icon size={16} />
                    {label}
                  </Link>
                </li>
              ))}
            </ul>
            <div className="border-t border-white/10 p-3">
              <button
                onClick={handleAdminLogout}
                className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm font-semibold text-white/80 hover:bg-white/10 transition-colors"
              >
                <LogOut size={16} />
                Đăng xuất
              </button>
            </div>
          </div>
        )}
      </header>

      {/* Breadcrumb "Admin/<Tên trang>" — thanh riêng, kích thước nhỏ gọn, nằm
          ngay dưới header thay vì lấn vào tiêu đề chính "Jenny AI". */}
      {isAdmin && (
        <div className="bg-primary-dark/95 border-t border-white/10 px-6 py-1.5">
          <p className="text-white/70 text-xs sm:text-sm font-semibold tracking-wide truncate">
            Admin/{adminPageLabel ?? ""}
          </p>
        </div>
      )}
    </div>
  );
}
