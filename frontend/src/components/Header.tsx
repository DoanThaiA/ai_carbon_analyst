"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import { LogoutButton } from "@/components/LogoutButton";
import { HotNewsBell } from "@/components/HotNewsBell";

export function Header() {
  const pathname = usePathname();
  const isAdmin = pathname?.startsWith("/admin");
  const isLogin = pathname === "/login";
  const logoHref = isAdmin ? "/admin/reports" : "/";

  return (
    <header className="bg-primary-dark px-6 py-4 flex items-center justify-between sticky top-0 z-50">
      <Link href={logoHref} className="flex items-center gap-3">
        <Image src="/stavian_logo.png" alt="Stavian" width={100} height={28} className="h-7 w-auto block" />
        <div className="w-[1px] h-5 bg-white/25 mx-1"></div>
        <h1 className="text-xl font-extrabold tracking-tight text-white">
          AI Carbon Analyst
        </h1>
      </Link>
      {!isLogin && (
        <div className="flex items-center gap-3">
          <HotNewsBell />
          <LogoutButton />
        </div>
      )}
    </header>
  );
}
