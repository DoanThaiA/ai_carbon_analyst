"use client";

import Image from "next/image";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ArrowUpRight } from "lucide-react";
import clsx from "clsx";
import { ADMIN_NAV_GROUPS, ADMIN_TONE_CLASSES, type AdminNavGroup } from "@/lib/adminNav";

// Trang Tổng quan admin: lời chào + 6 nhóm chức năng (cùng dữ liệu ADMIN_NAV_GROUPS với
// sidebar), mỗi chức năng là 1 card dẫn tới route có sẵn.
export default function AdminHome() {
  const t = useTranslations("adminHome");

  return (
    <div className="max-w-6xl mx-auto space-y-8">
      <section className="relative overflow-hidden rounded-3xl border border-border bg-gradient-to-br from-tint via-background to-background px-6 py-7 sm:px-8 sm:py-9">
        <div className="flex items-center gap-5 sm:gap-6">
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-primary">{t("greeting")}</p>
            <h1 className="mt-1.5 text-2xl sm:text-3xl font-bold tracking-tight text-heading">{t("title")}</h1>
            <p className="mt-2.5 max-w-2xl text-sm sm:text-[15px] leading-relaxed text-muted-light">{t("subtitle")}</p>
          </div>
          <div className="hidden sm:block shrink-0 relative" aria-hidden="true">
            <div className="absolute -inset-3 rounded-full bg-accent/15 blur-xl" />
            <Image
              src="/jenny.jpg"
              alt=""
              width={96}
              height={96}
              priority
              className="relative h-20 w-20 lg:h-24 lg:w-24 rounded-full object-cover ring-4 ring-background shadow-[var(--shadow-soft)]"
            />
          </div>
        </div>
      </section>

      <div className="grid gap-5 xl:grid-cols-2">
        {ADMIN_NAV_GROUPS.map((group, i) => (
          <GroupSection key={group.id} group={group} index={i + 1} />
        ))}
      </div>
    </div>
  );
}

function GroupSection({ group, index }: { group: AdminNavGroup; index: number }) {
  const tNav = useTranslations("shellNav");
  const t = useTranslations("adminHome");
  const tone = ADMIN_TONE_CLASSES[group.tone];
  const GroupIcon = group.icon;

  return (
    <section
      aria-labelledby={`admin-group-${group.id}`}
      className="flex flex-col rounded-2xl border border-border bg-background p-5 shadow-[0_1px_2px_rgba(16,24,40,0.04)]"
    >
      <header className="flex items-start gap-3.5">
        <span className={clsx("flex h-11 w-11 shrink-0 items-center justify-center rounded-xl ring-1", tone.iconWrap)}>
          <GroupIcon size={20} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span className={clsx("text-xs font-bold tabular-nums", tone.text)}>{String(index).padStart(2, "0")}</span>
            <h2 id={`admin-group-${group.id}`} className="min-w-0 text-base font-bold leading-snug text-heading">
              {tNav(group.labelKey)}
            </h2>
            <span className="ml-auto hidden sm:inline shrink-0 text-xs text-muted-light">{t("groupCount", { count: group.items.length })}</span>
          </div>
          <p className="mt-1 text-sm leading-snug text-muted-light">{tNav(`groupDesc_${group.id}`)}</p>
        </div>
      </header>

      <ul className="mt-4 grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-2">
        {group.items.map(({ href, labelKey, icon: Icon }) => (
          <li key={href} className="min-w-0">
            <Link
              href={href}
              className={clsx(
                "group flex h-full items-start gap-3 rounded-xl border border-border bg-background p-3.5",
                "transition-[border-color,background-color,box-shadow,transform] duration-200 ease-[cubic-bezier(.4,0,.2,1)]",
                "hover:-translate-y-px hover:shadow-[0_6px_16px_-6px_rgba(16,24,40,0.12)] motion-reduce:hover:translate-y-0",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2",
                tone.cardHover, tone.focusRing,
              )}
            >
              <span className={clsx("flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ring-1", tone.iconWrap)}>
                <Icon size={18} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-semibold leading-snug text-heading">{tNav(labelKey)}</span>
                <span className="mt-0.5 block text-xs leading-snug text-muted-light">{tNav(`desc_${labelKey}`)}</span>
              </span>
              <ArrowUpRight
                size={16}
                aria-hidden="true"
                className={clsx("mt-0.5 shrink-0 text-muted transition-colors", tone.arrowHover)}
              />
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
