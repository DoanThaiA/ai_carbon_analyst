"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import Link from "next/link";
import { LogOut } from "lucide-react";
import clsx from "clsx";
import { api, setAuthRole } from "@/lib/api";
import { ADMIN_NAV_ITEMS } from "@/lib/adminNav";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    api
      .get("/api/admin/auth/me")
      .then(() => setChecked(true))
      .catch(() => router.replace("/login?as=admin"));
  }, [router]);

  if (!checked) return null;

  const handleLogout = async () => {
    await api.post("/api/admin/auth/logout");
    setAuthRole(null);
    router.replace("/login?as=admin");
  };

  return (
    <div className="flex flex-col md:flex-row gap-4 md:gap-8">
      {/* Sidebar cố định — chỉ hiện từ md trở lên. Dưới md, điều hướng + đăng
          xuất chuyển lên menu mobile trong Header.tsx (icon burger cạnh chuông
          thông báo), không lặp lại ở đây nữa. */}
      <nav className="hidden md:block md:w-56 shrink-0 bg-background border border-border rounded-2xl p-3 h-fit md:sticky md:top-20">
        <ul className="space-y-1">
          {ADMIN_NAV_ITEMS.map(({ href, label, icon: Icon }) => (
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
        <button
          onClick={handleLogout}
          className="mt-3 w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm font-semibold text-body hover:bg-surface transition-colors duration-300 ease-in-out"
        >
          <LogOut size={16} />
          Đăng xuất
        </button>
      </nav>
      <div className="flex-1 min-w-0">{children}</div>
    </div>
  );
}
