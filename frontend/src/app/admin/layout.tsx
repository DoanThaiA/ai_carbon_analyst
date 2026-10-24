"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { AdminSidebar } from "@/components/admin/AdminSidebar";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    api
      .get("/api/admin/auth/me")
      .then(() => setChecked(true))
      .catch(() => router.replace("/login?as=admin"));
  }, [router]);

  if (!checked) return null;

  // Desktop: sidebar sticky bên trái (thu gọn được) + nội dung bên phải. Dưới md, điều hướng +
  // đăng xuất nằm trong drawer của Header.tsx.
  return (
    <div className="flex flex-col md:flex-row gap-4 md:gap-6">
      {/* report-focus-hide: ẩn khi "Phóng to" báo cáo (components/ReportFocusToggle.tsx). */}
      <div className="report-focus-hide contents">
        <AdminSidebar />
      </div>
      <div className="flex-1 min-w-0">{children}</div>
    </div>
  );
}
