"use client";

import { usePathname } from "next/navigation";
import clsx from "clsx";

/**
 * Bọc {children} của root layout — khu vực admin dùng hết chiều rộng màn hình
 * (sidebar sát lề trái, nội dung chính rộng hơn cho bảng dữ liệu), còn lại
 * (trang báo cáo cho user, dashboard, login) giữ nguyên chiều rộng giới hạn
 * cũ để dễ đọc nội dung dài.
 */
export function PageShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isAdmin = pathname?.startsWith("/admin");

  return (
    <main className={clsx("flex-1", isAdmin ? "px-4 py-6 md:px-6 md:py-8" : "p-6 md:p-12")}>
      <div className={isAdmin ? "w-full" : "max-w-5xl mx-auto"}>{children}</div>
    </main>
  );
}
