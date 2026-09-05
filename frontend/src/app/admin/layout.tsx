"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import Link from "next/link";
import { FileText, Radar, Users, LogOut, MessageSquareText, BrainCircuit } from "lucide-react";
import clsx from "clsx";
import { api } from "@/lib/api";

const NAV_ITEMS = [
  { href: "/admin/reports", label: "Báo cáo", icon: FileText },
  { href: "/admin/price-sources", label: "Nguồn giá", icon: Radar },
  { href: "/admin/users", label: "Người dùng", icon: Users },
  { href: "/admin/chat-reviews", label: "Đánh giá chat", icon: MessageSquareText },
  { href: "/admin/eua-framework", label: "Khung phân tích EUA", icon: BrainCircuit },
];

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
    router.replace("/login?as=admin");
  };

  return (
    <div className="flex flex-col md:flex-row gap-8">
      <nav className="md:w-56 shrink-0 bg-background border border-border rounded-2xl p-3 h-fit md:sticky md:top-20">
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
