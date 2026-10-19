"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { LayoutDashboard, LogOut } from "lucide-react";
import clsx from "clsx";
import { api, setAuthRole } from "@/lib/api";
import { ADMIN_HOME_HREF, ADMIN_NAV_GROUPS, ADMIN_TONE_CLASSES, activeAdminItem } from "@/lib/adminNav";

export function useAdminLogout() {
  const router = useRouter();
  return async () => {
    await api.post("/api/admin/auth/logout").catch(() => {});
    setAuthRole(null);
    router.replace("/login?as=admin");
  };
}

const itemBase =
  "flex items-center gap-3 rounded-xl text-sm font-semibold transition-colors duration-150 ease-[cubic-bezier(.4,0,.2,1)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-1";

/**
 * Danh sách điều hướng admin: Tổng quan → 6 nhóm (nhãn nhóm + chấm màu, vạch mờ giữa nhóm) → Đăng xuất.
 * Dùng chung cho sidebar desktop (`collapsed` = dải icon) và drawer mobile trong Header.tsx.
 */
export function AdminNavList({ collapsed = false }: { collapsed?: boolean }) {
  const t = useTranslations("shellNav");
  const tHeader = useTranslations("shellHeader");
  const pathname = usePathname();
  const logout = useAdminLogout();
  const active = activeAdminItem(pathname);
  const homeActive = pathname === ADMIN_HOME_HREF;
  const pad = collapsed ? "justify-center p-2" : "px-3 py-1.5";

  return (
    <div className="flex flex-col gap-0.5">
      <Link
        href={ADMIN_HOME_HREF}
        title={collapsed ? t("home") : undefined}
        aria-current={homeActive ? "page" : undefined}
        className={clsx(
          itemBase, pad, "focus-visible:ring-primary",
          homeActive ? "bg-tint text-primary-dark ring-1 ring-primary/15" : "text-body/80 hover:bg-surface hover:text-heading",
        )}
      >
        <LayoutDashboard size={18} className="shrink-0" />
        {!collapsed && <span className="truncate">{t("home")}</span>}
      </Link>

      {ADMIN_NAV_GROUPS.map((group) => {
        const tone = ADMIN_TONE_CLASSES[group.tone];
        return (
          <div key={group.id} className="pt-1.5 mt-1 border-t border-border/70">
            {collapsed ? (
              <span className={clsx("block mx-auto mb-1.5 h-1 w-4 rounded-full", tone.dot)} aria-hidden="true" />
            ) : (
              <div className="flex items-center gap-2 px-3 pb-1 text-[11px] font-bold uppercase tracking-wider text-muted-light">
                <span className={clsx("h-1.5 w-1.5 rounded-full shrink-0", tone.dot)} aria-hidden="true" />
                <span className="truncate">{t(group.labelKey)}</span>
              </div>
            )}
            <ul className="space-y-0.5">
              {group.items.map(({ href, labelKey, icon: Icon }) => {
                const isActive = active?.item.href === href;
                return (
                  <li key={href}>
                    <Link
                      href={href}
                      title={collapsed ? t(labelKey) : undefined}
                      aria-current={isActive ? "page" : undefined}
                      className={clsx(
                        itemBase, pad, tone.focusRing,
                        isActive ? clsx(tone.activeItem, "ring-1") : "text-body/80 hover:bg-surface hover:text-heading",
                      )}
                    >
                      <Icon size={18} className={clsx("shrink-0", isActive ? tone.text : "text-muted-light")} />
                      {!collapsed && <span className="truncate">{t(labelKey)}</span>}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}

      <div className="pt-1.5 mt-1 border-t border-border/70">
        <button
          type="button"
          onClick={logout}
          title={collapsed ? tHeader("logout") : undefined}
          className={clsx(itemBase, pad, "w-full text-body/80 hover:bg-red-50 hover:text-down focus-visible:ring-down")}
        >
          <LogOut size={18} className="shrink-0" />
          {!collapsed && <span className="truncate">{tHeader("logout")}</span>}
        </button>
      </div>
    </div>
  );
}
