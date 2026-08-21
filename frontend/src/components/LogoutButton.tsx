"use client";

import { LogOut } from "lucide-react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

export function LogoutButton() {
  const router = useRouter();

  const handleLogout = async () => {
    try {
      // Both admin and user logout endpoints can be called, or just clear cookies via API
      await api.post("/api/auth/logout").catch(() => {});
      await api.post("/api/admin/auth/logout").catch(() => {});
      router.push("/login");
    } catch (err) {
      console.error(err);
    }
  };

  return (
    <button
      onClick={handleLogout}
      className="text-white/80 hover:text-white flex items-center gap-1.5 text-sm font-medium transition-colors bg-white/10 hover:bg-white/20 px-3 py-1.5 rounded-full"
      title="Đăng xuất"
    >
      <LogOut size={14} />
      <span className="hidden sm:inline">Đăng xuất</span>
    </button>
  );
}
