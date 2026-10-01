"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import Link from "next/link";
import { LogOut } from "lucide-react";
import clsx from "clsx";
import { api, setAuthRole } from "@/lib/api";
import { ADMIN_NAV_GROUPS } from "@/lib/adminNav";

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
    <div className="flex flex-col md:flex-row gap-4 md:gap-6">
      {/* Sidebar dạng tab ẩn: mặc định chỉ là dải icon hẹp (w-12) để dành chỗ cho báo
          cáo; rê chuột vào (hoặc focus bàn phím) thì mở rộng đè lên nội dung (absolute,
          không đẩy layout). Dưới md, điều hướng + đăng xuất ở menu mobile trong Header.tsx. */}
      <div className="hidden md:block print:hidden w-12 shrink-0 relative">
        <nav className="group sticky top-20 z-30 w-12 hover:w-60 focus-within:w-60 overflow-hidden bg-background border border-border rounded-2xl p-1.5 h-fit transition-[width,box-shadow] duration-200 ease-in-out hover:shadow-[var(--shadow-soft)] focus-within:shadow-[var(--shadow-soft)]">
          {ADMIN_NAV_GROUPS.map((group, gi) => {
            const isLast = gi === ADMIN_NAV_GROUPS.length - 1;
            return (
              <div key={group.id}>
                {gi > 0 && <div className="my-1.5 mx-2 h-px bg-border" aria-hidden="true" />}
                <div className="px-3 pt-1 pb-0.5 text-[10.5px] font-bold uppercase tracking-wider text-muted-light whitespace-nowrap opacity-0 h-0 group-hover:opacity-100 group-hover:h-auto group-focus-within:opacity-100 group-focus-within:h-auto transition-opacity">
                  {group.label}
                </div>
                <ul className="space-y-0.5">
                  {group.items.map(({ href, label, icon: Icon }) => (
                    <li key={href}>
                      <Link
                        href={href}
                        title={label}
                        className={clsx(
                          "flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-sm font-semibold whitespace-nowrap transition-colors duration-300 ease-in-out",
                          pathname.startsWith(href)
                            ? "bg-tint text-primary-dark"
                            : "text-body hover:bg-surface"
                        )}
                      >
                        <Icon size={16} className="shrink-0" />
                        {label}
                      </Link>
                    </li>
                  ))}
                  {isLast && (
                    <li>
                      <button
                        onClick={handleLogout}
                        title="Đăng xuất"
                        className="w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-sm font-semibold whitespace-nowrap text-body hover:bg-surface transition-colors duration-300 ease-in-out"
                      >
                        <LogOut size={16} className="shrink-0" />
                        Đăng xuất
                      </button>
                    </li>
                  )}
                </ul>
              </div>
            );
          })}
        </nav>
      </div>
      <div className="flex-1 min-w-0">{children}</div>
    </div>
  );
}
