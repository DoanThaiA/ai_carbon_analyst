"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import {
  AlertCircle, ArrowRight, CheckCircle2, ClipboardCheck, FileClock, FileText, Loader2, Send,
} from "lucide-react";
import clsx from "clsx";
import { api } from "@/lib/api";
import { ADMIN_NAV_GROUPS, ADMIN_TONE_CLASSES, type AdminNavGroup } from "@/lib/adminNav";

// Trang Tổng quan admin: lời chào + 4 ô trạng thái vận hành (báo cáo hôm nay, nháp chờ duyệt,
// đã xuất bản 30 ngày, điểm QC gần đây) + 6 nhóm chức năng (cùng dữ liệu ADMIN_NAV_GROUPS với
// sidebar). Ô nào không tải được dữ liệu thì hiện "—", không chặn phần còn lại của trang.

type ReportRow = { report_date: string; status: "draft" | "published" | "generating" | "failed" };
type QcStats = { avg_score: number | null; trend: { report_date: string; score: number; verdict: string }[] };

const RECENT_DAYS = 30;
const QC_LAST = 7;

function todayVn(now: Date): string {
  // en-CA cho dạng YYYY-MM-DD, cùng định dạng với reports.report_date.
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Ho_Chi_Minh" }).format(now);
}

export default function AdminHome() {
  const t = useTranslations("adminHome");
  const locale = useLocale();
  const [reports, setReports] = useState<ReportRow[] | null>(null);
  const [qc, setQc] = useState<QcStats | null>(null);
  const [failed, setFailed] = useState({ reports: false, qc: false });
  // Mốc thời gian chốt 1 lần khi mở trang (render phải thuần — không gọi Date.now() mỗi lần render).
  const [now] = useState(() => new Date());
  const today = todayVn(now);
  const since = new Date(now.getTime() - RECENT_DAYS * 86400000).toISOString().slice(0, 10);
  const dateLabel = new Intl.DateTimeFormat(locale === "en" ? "en-GB" : "vi-VN", {
    weekday: "long", day: "2-digit", month: "2-digit", year: "numeric", timeZone: "Asia/Ho_Chi_Minh",
  }).format(now);

  useEffect(() => {
    api.get("/api/admin/reports")
      .then((r) => setReports(r.data))
      .catch(() => setFailed((f) => ({ ...f, reports: true })));
    api.get(`/api/admin/qc-reviews/stats?last=${QC_LAST}`)
      .then((r) => setQc(r.data))
      .catch(() => setFailed((f) => ({ ...f, qc: true })));
  }, []);

  const todayReport = reports?.find((r) => r.report_date === today) ?? null;
  const drafts = reports?.filter((r) => r.status === "draft") ?? [];
  const publishedRecent = reports?.filter((r) => r.status === "published" && r.report_date >= since).length ?? 0;

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <section className="relative overflow-hidden rounded-3xl bg-primary-dark px-6 py-7 sm:px-8 sm:py-8 text-white">
        <div aria-hidden="true" className="pointer-events-none absolute -right-16 -top-24 h-72 w-72 rounded-full bg-accent/20 blur-3xl" />
        <div aria-hidden="true" className="pointer-events-none absolute -bottom-28 left-1/3 h-64 w-64 rounded-full bg-primary/40 blur-3xl" />
        <div className="relative flex items-center gap-5 sm:gap-6">
          <div className="min-w-0 flex-1">
            <p className="text-xs font-semibold uppercase tracking-wider text-accent first-letter:uppercase">{dateLabel}</p>
            <h1 className="mt-2 text-2xl sm:text-3xl font-bold tracking-tight">{t("greeting")} {t("title")}</h1>
            <p className="mt-2 max-w-2xl text-sm sm:text-[15px] leading-relaxed text-white/75">{t("subtitle")}</p>
            <div className="mt-5 flex flex-wrap gap-2">
              <Link
                href={todayReport ? `/admin/reports/${today}` : "/admin/reports"}
                className="inline-flex items-center gap-1.5 rounded-full bg-white px-4 py-1.5 text-sm font-semibold text-primary-dark hover:bg-tint transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                <FileText size={15} />
                {todayReport ? t("openToday") : t("openReports")}
              </Link>
              <Link
                href="/admin/qc-results"
                className="inline-flex items-center gap-1.5 rounded-full border border-white/25 bg-white/10 px-4 py-1.5 text-sm font-semibold text-white hover:bg-white/20 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                <ClipboardCheck size={15} />
                {t("openQc")}
              </Link>
            </div>
          </div>
          <div className="hidden sm:block shrink-0 relative" aria-hidden="true">
            <div className="absolute -inset-2 rounded-full bg-accent/25 blur-lg" />
            <Image
              src="/jenny.jpg"
              alt=""
              width={112}
              height={112}
              priority
              className="relative h-20 w-20 lg:h-28 lg:w-28 rounded-full object-cover ring-4 ring-white/20"
            />
          </div>
        </div>
      </section>

      <section aria-label={t("statusLabel")} className="grid gap-3 grid-cols-2 lg:grid-cols-4">
        <TodayTile report={todayReport} loading={!reports && !failed.reports} failed={failed.reports} today={today} />
        <StatTile
          icon={FileClock}
          label={t("draftsLabel")}
          value={reports ? String(drafts.length) : null}
          failed={failed.reports}
          hint={drafts.length ? t("draftsHint", { date: drafts[0].report_date }) : t("draftsNone")}
          href={drafts.length ? `/admin/reports/${drafts[0].report_date}` : "/admin/reports"}
        />
        <StatTile
          icon={Send}
          label={t("publishedLabel", { days: RECENT_DAYS })}
          value={reports ? String(publishedRecent) : null}
          failed={failed.reports}
          hint={t("publishedHint")}
          href="/admin/reports"
        />
        <QcTile stats={qc} failed={failed.qc} />
      </section>

      <div className="flex items-baseline justify-between gap-3 pt-2">
        <h2 className="text-lg font-bold text-heading">{t("featuresTitle")}</h2>
        <p className="hidden sm:block text-sm text-muted-light">{t("featuresHint")}</p>
      </div>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {ADMIN_NAV_GROUPS.map((group, i) => (
          <GroupSection key={group.id} group={group} index={i + 1} />
        ))}
      </div>
    </div>
  );
}

const tileBase =
  "group flex flex-col rounded-2xl border border-border bg-background p-4 shadow-[0_1px_2px_rgba(16,24,40,0.04)] transition-[border-color,box-shadow] duration-200 hover:border-primary/30 hover:shadow-[0_8px_20px_-10px_rgba(16,24,40,0.18)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary";

function TileHead({ icon: Icon, label }: { icon: typeof FileText; label: string }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-xs font-semibold text-muted-light">{label}</span>
      <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-tint text-primary-dark">
        <Icon size={16} />
      </span>
    </div>
  );
}

function StatTile({ icon, label, value, hint, href, failed }: {
  icon: typeof FileText; label: string; value: string | null; hint: string; href: string; failed: boolean;
}) {
  return (
    <Link href={href} className={tileBase}>
      <TileHead icon={icon} label={label} />
      <span className="mt-2 text-3xl font-bold tabular-nums text-heading leading-none">
        {failed ? "—" : value ?? <Loader2 size={22} className="animate-spin text-muted-light" />}
      </span>
      <span className="mt-2 text-xs text-muted-light truncate">{hint}</span>
    </Link>
  );
}

const STATUS_STYLE: Record<ReportRow["status"], { cls: string; icon: typeof FileText }> = {
  published: { cls: "bg-tint text-primary-dark border-primary/20", icon: CheckCircle2 },
  draft: { cls: "bg-warn-tint text-warn border-warn/20", icon: FileText },
  generating: { cls: "bg-surface-alt text-muted-light border-border", icon: Loader2 },
  failed: { cls: "bg-red-50 text-down border-red-200", icon: AlertCircle },
};

function TodayTile({ report, loading, failed, today }: {
  report: ReportRow | null; loading: boolean; failed: boolean; today: string;
}) {
  const t = useTranslations("adminHome");
  const style = report ? STATUS_STYLE[report.status] : null;
  const Icon = style?.icon;
  return (
    <Link href={report ? `/admin/reports/${today}` : "/admin/reports"} className={tileBase}>
      <TileHead icon={FileText} label={t("todayLabel")} />
      <div className="mt-2 min-h-[30px] flex items-center">
        {failed ? (
          <span className="text-3xl font-bold text-heading leading-none">—</span>
        ) : loading ? (
          <Loader2 size={22} className="animate-spin text-muted-light" />
        ) : report && style && Icon ? (
          // Trạng thái luôn có icon + chữ, không chỉ dựa vào màu.
          <span className={clsx("inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-sm font-semibold", style.cls)}>
            <Icon size={14} className={report.status === "generating" ? "animate-spin" : undefined} />
            {t(`status_${report.status}`)}
          </span>
        ) : (
          <span className="text-sm font-semibold text-muted-light">{t("todayNone")}</span>
        )}
      </div>
      <span className="mt-2 text-xs text-muted-light truncate">{t("todayHint", { date: today })}</span>
    </Link>
  );
}

function QcTile({ stats, failed }: { stats: QcStats | null; failed: boolean }) {
  const t = useTranslations("adminHome");
  const trend = stats?.trend ?? [];
  const avg = stats?.avg_score;
  return (
    <Link href="/admin/qc-results" className={tileBase}>
      <TileHead icon={ClipboardCheck} label={t("qcLabel", { count: QC_LAST })} />
      <div className="mt-2 flex items-end justify-between gap-3">
        <span className="text-3xl font-bold tabular-nums text-heading leading-none">
          {failed || (stats && avg == null) ? "—" : avg != null ? avg.toFixed(1) : <Loader2 size={22} className="animate-spin text-muted-light" />}
          {avg != null && <span className="text-sm font-semibold text-muted-light">/10</span>}
        </span>
        {trend.length > 1 && (
          // Thanh điểm từng báo cáo (cũ → mới), 1 màu; giá trị cụ thể ở tooltip từng thanh.
          <span className="flex h-9 items-end gap-[3px]" role="img" aria-label={t("qcTrendLabel")}>
            {trend.map((p) => (
              <span
                key={p.report_date}
                title={t("qcBarTitle", { date: p.report_date, score: p.score.toFixed(2) })}
                className="w-[6px] rounded-t-[3px] bg-primary/70 group-hover:bg-primary transition-colors"
                style={{ height: `${Math.max(8, p.score * 10)}%` }}
              />
            ))}
          </span>
        )}
      </div>
      <span className="mt-2 text-xs text-muted-light truncate">
        {trend.length ? t("qcHint", { date: trend[trend.length - 1].report_date }) : t("qcNone")}
      </span>
    </Link>
  );
}

function GroupSection({ group, index }: { group: AdminNavGroup; index: number }) {
  const tNav = useTranslations("shellNav");
  const tone = ADMIN_TONE_CLASSES[group.tone];
  const GroupIcon = group.icon;

  return (
    <section
      aria-labelledby={`admin-group-${group.id}`}
      className="relative flex flex-col overflow-hidden rounded-2xl border border-border bg-background shadow-[0_1px_2px_rgba(16,24,40,0.04)]"
    >
      <span aria-hidden="true" className={clsx("absolute inset-x-0 top-0 h-1", tone.dot)} />
      <header className="flex items-start gap-3 px-5 pt-5 pb-3">
        <span className={clsx("flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ring-1", tone.iconWrap)}>
          <GroupIcon size={19} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span className={clsx("text-xs font-bold tabular-nums", tone.text)}>{String(index).padStart(2, "0")}</span>
            <h3 id={`admin-group-${group.id}`} className="min-w-0 text-[15px] font-bold leading-snug text-heading">
              {tNav(group.labelKey)}
            </h3>
          </div>
          <p className="mt-0.5 text-xs leading-snug text-muted-light">{tNav(`groupDesc_${group.id}`)}</p>
        </div>
      </header>

      <ul className="flex-1 border-t border-border/70 divide-y divide-border/70">
        {group.items.map(({ href, labelKey, icon: Icon }) => (
          <li key={href}>
            <Link
              href={href}
              className={clsx(
                "group flex items-center gap-3 px-5 py-3 transition-colors duration-150",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset",
                tone.cardHover, tone.focusRing,
              )}
            >
              <Icon size={17} className={clsx("shrink-0", tone.text)} />
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-semibold leading-snug text-heading">{tNav(labelKey)}</span>
                <span className="block text-xs leading-snug text-muted-light line-clamp-2">{tNav(`desc_${labelKey}`)}</span>
              </span>
              <ArrowRight
                size={15}
                aria-hidden="true"
                className={clsx("shrink-0 text-muted transition-[color,transform] group-hover:translate-x-0.5", tone.arrowHover)}
              />
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
