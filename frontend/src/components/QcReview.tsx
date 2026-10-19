"use client";

// Biên bản QC báo cáo do Claude (vai Jack) gửi qua MCP — CHỈ dùng trong trang admin.
// - QcReviewView: nội dung 1 biên bản (điểm, tiêu chí, lỗi, ưu tiên, biên bản đầy đủ) — dùng ở trang
//   /admin/qc-results/[id] và trong panel cạnh báo cáo.
// - QcReviewPanel: panel trượt bên phải trang /admin/reports/[date], chỉ mở khi báo cáo CHƯA xuất bản. Có nút
//   "Sửa báo cáo theo QC" (QcFixModal) khi trang cho phép (`onReportChanged`).
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import clsx from "clsx";
import { useTranslations } from "next-intl";
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, ClipboardCheck, ExternalLink, Loader2, Repeat, Wand2, X, XCircle } from "lucide-react";
import { api } from "@/lib/api";
import { QcFixModal } from "@/components/QcFixModal";
import type { Report, QcFinding, QcFindingStatus, QcReportReviews, QcReviewDetail, QcSeverity, QcVerdict } from "@/lib/types";

const VERDICT_CLS: Record<QcVerdict, string> = {
  pass: "bg-tint text-primary-dark border border-primary/20",
  pass_with_fixes: "bg-warn-tint text-warn border border-warn/20",
  fail: "bg-red-50 text-down border border-red-200",
};
const SEVERITY_CLS: Record<QcSeverity, string> = {
  critical: "bg-red-50 text-down border border-red-200",
  major: "bg-warn-tint text-warn border border-warn/20",
  minor: "bg-surface-alt text-muted-light border border-border",
};
const SEVERITY_ORDER: QcSeverity[] = ["critical", "major", "minor"];

export function VerdictBadge({ verdict, className }: { verdict: QcVerdict; className?: string }) {
  const t = useTranslations("adminQcReviews");
  const Icon = verdict === "pass" ? CheckCircle2 : verdict === "fail" ? XCircle : AlertTriangle;
  return (
    <span className={clsx("inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-semibold whitespace-nowrap", VERDICT_CLS[verdict], className)}>
      <Icon size={12} />
      {t(`verdict_${verdict}`)}
    </span>
  );
}

export function scoreColor(score: number): string {
  return score >= 8 ? "text-primary-dark" : score >= 6 ? "text-warn" : "text-down";
}

function sectionLabel(t: ReturnType<typeof useTranslations>, section: string | null): string {
  if (!section || section === "general") return t("sectionGeneral");
  if (/^\d+$/.test(section)) return t("sectionNumber", { n: section });
  return t.has(`section_${section}`) ? t(`section_${section}`) : section;
}

function FindingCard({ finding, onStatus, onFix }: {
  finding: QcFinding;
  onStatus?: (f: QcFinding, s: QcFindingStatus) => void;
  onFix?: (ids: number[]) => void;
}) {
  const t = useTranslations("adminQcReviews");
  return (
    <li className={clsx("rounded-xl border p-3 text-sm", finding.status === "open" ? "border-border bg-background" : "border-border-soft bg-surface opacity-75")}>
      <div className="flex flex-wrap items-center gap-1.5 mb-1.5">
        <span className={clsx("px-1.5 py-0.5 rounded text-[11px] font-semibold", SEVERITY_CLS[finding.severity])}>
          {t(`severity_${finding.severity}`)}
        </span>
        <span className="px-1.5 py-0.5 rounded text-[11px] font-medium bg-surface border border-border text-body">
          {sectionLabel(t, finding.section)}
          {finding.location ? ` · ${finding.location}` : ""}
        </span>
        <span className="text-[11px] text-muted-light">{finding.criterion_label}</span>
        {finding.repeat_count > 1 && (
          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] font-semibold bg-red-50 text-down border border-red-200">
            <Repeat size={11} />
            {t("repeat", { count: finding.repeat_count })}
          </span>
        )}
        <span className="text-[11px] text-muted-light">· {t(`verification_${finding.verification}`)}</span>
      </div>
      <p className="font-semibold text-heading">{finding.title}</p>
      {finding.current_text && (
        <p className="mt-1.5 text-body whitespace-pre-wrap break-words"><span className="font-semibold text-down">{t("currentText")}: </span>{finding.current_text}</p>
      )}
      {finding.expected_text && (
        <p className="mt-1 text-body whitespace-pre-wrap break-words"><span className="font-semibold text-primary-dark">{t("expectedText")}: </span>{finding.expected_text}</p>
      )}
      {finding.evidence && (
        <p className="mt-1 text-muted-light whitespace-pre-wrap break-words"><span className="font-semibold">{t("evidence")}: </span>{finding.evidence}</p>
      )}
      {finding.source_refs.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-2">
          {finding.source_refs.map((r, i) =>
            r.url ? (
              <a key={i} href={r.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-xs text-primary hover:underline break-all">
                <ExternalLink size={11} />{r.label}
              </a>
            ) : (
              <span key={i} className="text-xs text-muted-light">{r.label}</span>
            ),
          )}
        </div>
      )}
      {finding.field_path && <p className="mt-1 text-[11px] font-mono text-muted-light break-all">{finding.field_path}</p>}
      {onStatus && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {(["open", "fixed", "wont_fix"] as QcFindingStatus[]).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => s !== finding.status && onStatus(finding, s)}
              className={clsx(
                "px-2 py-0.5 rounded-full text-[11px] font-semibold border transition-colors",
                finding.status === s ? "bg-primary text-white border-primary" : "border-border-soft text-body hover:border-primary hover:text-primary-dark",
              )}
            >
              {t(`status_${s}`)}
            </button>
          ))}
          {onFix && finding.status === "open" && (
            <button
              type="button"
              onClick={() => onFix([finding.id])}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold border border-primary/30 text-primary-dark hover:bg-tint"
            >
              <Wand2 size={11} />
              {t("fixOne")}
            </button>
          )}
          {finding.status_note && <span className="text-[11px] text-muted-light">— {finding.status_note}</span>}
        </div>
      )}
    </li>
  );
}

export function QcReviewView({ review, onChange, onFix }: {
  review: QcReviewDetail;
  onChange?: (r: QcReviewDetail) => void;
  onFix?: (ids?: number[]) => void; // có = trang cho phép sửa báo cáo theo QC (bản nháp, không đang sửa tay)
}) {
  const t = useTranslations("adminQcReviews");
  const [showBody, setShowBody] = useState(false);
  const [severity, setSeverity] = useState<QcSeverity | "all">("all");

  const handleStatus = async (finding: QcFinding, status: QcFindingStatus) => {
    let note: string | null = null;
    if (status === "wont_fix") {
      const v = window.prompt(t("wontFixPrompt"), "");
      if (v === null) return;
      note = v.trim() || null;
    }
    try {
      const res = await api.patch(`/api/admin/qc-reviews/findings/${finding.id}`, { status, note });
      onChange?.({ ...review, findings: review.findings.map((f) => (f.id === finding.id ? res.data : f)) });
    } catch (err: any) {
      alert(err.response?.data?.detail || t("updateError"));
    }
  };

  const openCount = review.findings.filter((f) => f.status === "open").length;
  const findings = review.findings
    .filter((f) => severity === "all" || f.severity === severity)
    .sort((a, b) => SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity) || a.position - b.position);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end gap-4">
        <div>
          <div className="text-xs text-muted-light">{t("overallScore")}</div>
          <div className={clsx("text-3xl font-bold leading-none", scoreColor(review.overall_score))}>
            {review.overall_score.toFixed(2)}<span className="text-base text-muted-light font-semibold">/10</span>
          </div>
        </div>
        <VerdictBadge verdict={review.verdict} className="mb-1" />
        {review.claude_verdict && review.claude_verdict !== review.verdict && (
          <span className="mb-1 text-xs text-muted-light">{t("claudeProposed", { verdict: t(`verdict_${review.claude_verdict}`) })}</span>
        )}
        <div className="mb-1 text-xs text-muted-light">
          {t("jackRule")}: <b className="text-body">{review.jack_rule_score ?? "—"}/100</b>
          {review.jack_rule_issue_count != null && ` · ${t("issues", { count: review.jack_rule_issue_count })}`}
        </div>
      </div>
      <div className="text-xs text-muted-light">
        {t("runMeta", {
          run: review.run_no,
          reviewer: review.reviewer || "Claude",
          time: review.created_at ? new Date(review.created_at).toLocaleString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh" }) : "—",
          status: review.report_status,
        })}
      </div>
      {review.is_stale && (
        <div className="rounded-lg border border-warn/30 bg-warn-tint text-warn text-xs px-3 py-2">{t("staleNotice")}</div>
      )}

      <p className="text-sm text-body whitespace-pre-wrap">{review.summary}</p>

      {onFix && openCount > 0 && (
        <button
          type="button"
          onClick={() => onFix()}
          className="w-full flex items-center justify-center gap-2 px-4 py-2 rounded-full bg-primary hover:bg-primary-dark text-white text-sm font-semibold transition-colors"
        >
          <Wand2 size={15} />
          {t("fixAll", { count: openCount })}
        </button>
      )}

      <div>
        <h4 className="text-xs font-bold uppercase tracking-wider text-muted-light mb-2">{t("criteriaTitle")}</h4>
        <ul className="space-y-2">
          {review.criteria.map((c) => (
            <li key={c.key} className="text-sm">
              <div className="flex items-center justify-between gap-2">
                <span className="font-semibold text-heading">{c.label} <span className="text-[11px] font-normal text-muted-light">({c.weight}%)</span></span>
                <span className={clsx("font-bold tabular-nums", scoreColor(c.score))}>{c.score.toFixed(1)}</span>
              </div>
              <div className="h-1.5 rounded-full bg-surface-alt overflow-hidden my-1">
                <div className={clsx("h-full rounded-full", c.score >= 8 ? "bg-primary" : c.score >= 6 ? "bg-warn" : "bg-down")} style={{ width: `${c.score * 10}%` }} />
              </div>
              <p className="text-xs text-muted-light whitespace-pre-wrap">{c.comment}</p>
            </li>
          ))}
        </ul>
      </div>

      <div>
        <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
          <h4 className="text-xs font-bold uppercase tracking-wider text-muted-light">
            {t("findingsTitle", { critical: review.critical_count, major: review.major_count, minor: review.minor_count })}
          </h4>
          <div className="flex gap-1">
            {(["all", ...SEVERITY_ORDER] as const).map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => setSeverity(s)}
                className={clsx("px-2 py-0.5 rounded-full text-[11px] font-semibold border", severity === s ? "bg-primary text-white border-primary" : "border-border-soft text-body hover:border-primary")}
              >
                {s === "all" ? t("filterAll") : t(`severity_${s}`)}
              </button>
            ))}
          </div>
        </div>
        {findings.length === 0 ? (
          <p className="text-sm text-muted-light">{t("noFindings")}</p>
        ) : (
          <ul className="space-y-2">
            {findings.map((f) => <FindingCard key={f.id} finding={f} onStatus={onChange ? handleStatus : undefined} onFix={onFix} />)}
          </ul>
        )}
      </div>

      {review.priorities.length > 0 && (
        <div>
          <h4 className="text-xs font-bold uppercase tracking-wider text-muted-light mb-2">{t("prioritiesTitle")}</h4>
          <ol className="list-decimal pl-5 space-y-1 text-sm text-body">
            {review.priorities.map((p) => (
              <li key={p.rank}>
                {p.text}
                {p.expected_gain != null && <span className="ml-1 text-xs font-semibold text-primary-dark">+{p.expected_gain}</span>}
              </li>
            ))}
          </ol>
        </div>
      )}

      {review.strengths.length > 0 && (
        <div>
          <h4 className="text-xs font-bold uppercase tracking-wider text-muted-light mb-2">{t("strengthsTitle")}</h4>
          <ul className="list-disc pl-5 space-y-1 text-sm text-body">
            {review.strengths.map((s, i) => <li key={i}>{s}</li>)}
          </ul>
        </div>
      )}

      {review.body_markdown && (
        <div>
          <button type="button" onClick={() => setShowBody((v) => !v)} className="inline-flex items-center gap-1 text-sm font-semibold text-primary hover:text-primary-dark">
            {showBody ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            {t("fullBody")}
          </button>
          {showBody && (
            <pre className="mt-2 max-h-[60vh] overflow-auto whitespace-pre-wrap break-words rounded-xl border border-border bg-surface p-3 text-xs text-body font-sans">
              {review.body_markdown}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

export function QcReviewPanel({ date, onClose, onReportChanged }: {
  date: string;
  onClose: () => void;
  onReportChanged?: (content: Report["content"]) => void; // không truyền = ẩn nút sửa theo QC (vd đang sửa tay)
}) {
  const t = useTranslations("adminQcReviews");
  const [data, setData] = useState<QcReportReviews | null>(null);
  const [error, setError] = useState("");
  // undefined = modal đóng; null = sửa mọi lỗi còn mở; [id] = chỉ lỗi đó (giữ trong state để mảng ổn định).
  const [fixIds, setFixIds] = useState<number[] | null | undefined>(undefined);

  const load = useCallback(async () => {
    try {
      const res = await api.get(`/api/admin/qc-reviews/report/${date}`);
      setData(res.data);
    } catch (err: any) {
      setError(err.response?.data?.detail || t("loadError"));
    }
  }, [date, t]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <aside className="fixed inset-y-0 right-0 z-50 w-full sm:w-[480px] bg-background border-l border-border shadow-[var(--shadow-soft)] flex flex-col print:hidden">
      <div className="flex items-center justify-between gap-2 px-4 py-3 border-b border-border">
        <h3 className="flex items-center gap-2 font-bold text-heading">
          <ClipboardCheck size={18} className="text-primary" />
          {t("panelTitle", { date })}
        </h3>
        <button type="button" onClick={onClose} className="p-1 rounded hover:bg-surface text-muted-light" aria-label={t("close")}>
          <X size={18} />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto px-4 py-4">
        {error ? (
          <p className="text-sm text-down">{error}</p>
        ) : !data ? (
          <div className="flex justify-center py-10"><Loader2 className="animate-spin text-primary" /></div>
        ) : (
          <div className="space-y-4">
            {data.auto_publish.time && data.report_status === "draft" && (
              <div className={clsx("rounded-lg border px-3 py-2 text-xs", data.auto_publish.allowed ? "border-primary/20 bg-tint text-primary-dark" : "border-red-200 bg-red-50 text-down")}>
                {data.auto_publish.allowed
                  ? t("autoPublishOn", { time: data.auto_publish.time, reason: data.auto_publish.reason })
                  : t("autoPublishBlocked", { time: data.auto_publish.time, reason: data.auto_publish.reason })}
              </div>
            )}
            {data.latest ? (
              <QcReviewView
                review={data.latest}
                onChange={(r) => setData({ ...data, latest: r })}
                onFix={onReportChanged && data.report_status === "draft" ? (ids) => setFixIds(ids ?? null) : undefined}
              />
            ) : (
              <p className="text-sm text-muted-light">{t("noReviewYet")}</p>
            )}
            {data.history.length > 0 && (
              <div>
                <h4 className="text-xs font-bold uppercase tracking-wider text-muted-light mb-2">{t("historyTitle")}</h4>
                <ul className="space-y-1">
                  {data.history.map((h) => (
                    <li key={h.id}>
                      <Link href={`/admin/qc-results/${h.id}`} className="flex items-center justify-between gap-2 text-sm rounded-lg px-2 py-1.5 hover:bg-surface">
                        <span>#{h.run_no} · {h.created_at ? new Date(h.created_at).toLocaleString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh" }) : ""}</span>
                        <span className="flex items-center gap-2">
                          <b className={scoreColor(h.overall_score)}>{h.overall_score.toFixed(2)}</b>
                          <VerdictBadge verdict={h.verdict} />
                        </span>
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>
      {fixIds !== undefined && onReportChanged && (
        <QcFixModal
          date={date}
          findingIds={fixIds ?? undefined}
          onClose={() => setFixIds(undefined)}
          onApplied={(content) => {
            onReportChanged(content);
            load(); // trạng thái lỗi + "đã sửa sau QC" đổi theo
          }}
        />
      )}
    </aside>
  );
}
