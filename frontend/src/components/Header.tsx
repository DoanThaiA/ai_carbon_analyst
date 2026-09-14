"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { LogoutButton } from "@/components/LogoutButton";
import { HotNewsBell } from "@/components/HotNewsBell";
import { Settings, LayoutDashboard } from "lucide-react";
import { api } from "@/lib/api";

export function Header() {
  const pathname = usePathname();
  const isAdmin = pathname?.startsWith("/admin");
  const isLogin = pathname === "/login";
  const [role, setRole] = useState<string | null>(null);
  const logoHref = isAdmin ? "/admin/reports" : role ? "/dashboard" : "/";

  useEffect(() => {
    if (!isLogin) {
      api.get("/api/auth/me")
        .then(res => setRole(res.data.role))
        .catch(() => setRole(null));
    }
  }, [isLogin]);

  // Chỉ hiện các nút dành cho người đã đăng nhập (Quản trị/Dashboard/Hot
  // News/Đăng xuất) sau khi xác nhận có role — landing page `/` công khai
  // (chưa đăng nhập) không nên hiện các nút này.
  const isAuthenticated = !isLogin && role !== null;

  return (
    <header className="bg-primary-dark px-6 py-4 flex items-center justify-between sticky top-0 z-50 print:hidden">
      <Link href={logoHref} className="flex items-center gap-3">
        <Image src="/stavian_logo.png" alt="Stavian" width={100} height={28} className="h-7 w-auto block" />
        <div className="w-[1px] h-5 bg-white/25 mx-1"></div>
        <h1 className="text-xl font-extrabold tracking-tight text-white">
          AI Carbon Analyst
        </h1>
      </Link>
      {isAuthenticated && (
        <div className="flex items-center gap-3">
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
          {isAdmin && (
            <Link
              href="/dashboard"
              className="text-white/80 hover:text-white flex items-center gap-1.5 text-sm font-medium transition-colors bg-white/10 hover:bg-white/20 px-3 py-1.5 rounded-full"
              title="Về trang báo cáo chính (giao diện người dùng)"
            >
              <LayoutDashboard size={14} />
              <span className="hidden sm:inline">Dashboard</span>
            </Link>
          )}
          <HotNewsBell />
          <LogoutButton />
        </div>
      )}
    </header>
  );
}
