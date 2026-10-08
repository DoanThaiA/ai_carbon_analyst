"use client";

import { useState } from "react";
import clsx from "clsx";
import { AlertCircle, AlertTriangle, Info, ChevronDown, ChevronUp, Sparkles, RefreshCw, X } from "lucide-react";
import type { QCCheck, QCIssue, QCResult, QCSeverity } from "@/lib/types";

// Lucy QC — chỉ hiện trên màn hình admin duyệt báo cáo draft (app/admin/reports/[date]/page.tsx).
// Backend: services/report_qc.py. Toàn bộ khối ở đây ẩn khi in PDF (print:hidden).

const SEVERITY_META: Record<QCSeverity, { icon: typeof AlertCircle; label: string; text: string; badge: string }> = {
  error: { icon: AlertCircle, label: "lỗi", text: "text-down", badge: "bg-red-50 text-down border-down/30" },
  warning: { icon: AlertTriangle, label: "cảnh báo", text: "text-warn", badge: "bg-warn-tint text-warn border-warn/30" },
  info: { icon: Info, label: "gợi ý", text: "text-blue-700", badge: "bg-blue-50 text-blue-700 border-blue-200" },
};
const SEVERITIES: QCSeverity[] = ["error", "warning", "info"];

// `ai`: check chạy bằng LLM (đợt 2) — điểm có thể null nếu gọi LLM lỗi (note lý do nằm ở Mục 3).
const CHECK_META: { key: QCCheck; label: string; field: keyof QCResult; ai?: boolean }[] = [
  { key: "price", label: "Giá số liệu", field: "price_accuracy_score" },
  { key: "scenario", label: "Kịch bản giao dịch", field: "scenario_score" },
  { key: "source", label: "Nguồn tin", field: "source_score" },
  { key: "calendar", label: "Lịch sự kiện", field: "calendar_score" },
  { key: "biz", label: "Gợi ý kinh doanh", field: "biz_score" },
  { key: "consistency", label: "Nhất quán nội bộ", field: "consistency_score", ai: true },
  { key: "causal", label: "Chuỗi nhân quả EUA", field: "causal_score", ai: true },
];

export function qcTone(qc: QCResult): "good" | "warn" | "bad" {
  if (qc.issues.some((i) => i.severity === "error")) return "bad";
  return (qc.overall_score ?? 0) >= 80 ? "good" : "warn";
}

function scoreClass(score: number | null) {
  if (score === null) return "text-muted-light";
  if (score >= 80) return "text-up";
  if (score >= 60) return "text-warn";
  return "text-down";
}

function SeverityCounts({ issues }: { issues: QCIssue[] }) {
  return (
    <span className="flex flex-wrap items-center gap-1.5">
      {SEVERITIES.map((sev) => {
        const n = issues.filter((i) => i.severity === sev).length;
        if (!n) return null;
        const meta = SEVERITY_META[sev];
        return (
          <span key={sev} className={clsx("inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[11.5px] font-semibold", meta.badge)}>
            <meta.icon size={12} />
            {n} {meta.label}
          </span>
        );
      })}
    </span>
  );
}

function IssueList({ issues }: { issues: QCIssue[] }) {
  return (
    <ul className="space-y-1.5">
      {issues.map((issue, i) => {
        const meta = SEVERITY_META[issue.severity];
        return (
          <li key={i} className="flex gap-2 text-[13px] leading-[1.45] text-body">
            <meta.icon size={14} className={clsx("mt-[3px] shrink-0", meta.text)} />
            <span className="min-w-0 break-words">{issue.message}</span>
          </li>
        );
      })}
    </ul>
  );
}

// Note Lucy gắn ngay đầu từng section của báo cáo — chỉ hiện khi section có issue.
// Mặc định mở nếu có lỗi (error), còn lại thu gọn.
export function QCSectionNotes({ issues, section }: { issues?: QCIssue[]; section: string }) {
  const sectionIssues = (issues ?? []).filter((i) => i.section === section);
  const [open, setOpen] = useState(() => sectionIssues.some((i) => i.severity === "error"));
  if (sectionIssues.length === 0) return null;

  return (
    <div className="print:hidden mb-4 rounded-lg border border-violet-200 bg-violet-50/60">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="w-full flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-left"
      >
        <span className="flex items-center gap-1.5 text-[12.5px] font-bold text-violet-700">
          <Sparkles size={14} />
          Lucy QC
        </span>
        <span className="flex items-center gap-2">
          <SeverityCounts issues={sectionIssues} />
          {open ? <ChevronUp size={15} className="text-violet-700" /> : <ChevronDown size={15} className="text-violet-700" />}
        </span>
      </button>
      {open && (
        <div className="px-3 pb-2.5 pt-0.5 border-t border-violet-200/70">
          <div className="pt-2">
            <IssueList issues={sectionIssues} />
          </div>
        </div>
      )}
    </div>
  );
}

// Bảng tổng kết QC (mở từ badge điểm trên header): điểm tổng + điểm 5 check + chạy lại.
export function QCSummaryPanel({
  qc,
  onRerun,
  onClose,
  rerunning,
}: {
  qc: QCResult;
  onRerun: () => void;
  onClose: () => void;
  rerunning: boolean;
}) {
  const checkedAt = new Date(qc.checked_at).toLocaleString("vi-VN");
  // Danh sách đầy đủ — phòng khi khối chứa note bị ẩn (vd "Diễn biến chính" trống → note Mục 4 không hiện).
  const [showAll, setShowAll] = useState(false);
  return (
    <div className="print:hidden mb-6 rounded-2xl border border-violet-200 bg-background shadow-[var(--shadow-soft)] overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 bg-gradient-to-r from-violet-600 to-fuchsia-500 text-white">
        <div className="flex items-center gap-2 font-bold">
          <Sparkles size={16} />
          Lucy QC báo cáo
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onRerun}
            disabled={rerunning}
            className="flex items-center gap-1.5 text-xs font-semibold px-3 py-1 rounded-full bg-white/20 hover:bg-white/30 transition-colors disabled:opacity-50"
          >
            <RefreshCw size={13} className={rerunning ? "animate-spin" : ""} />
            Chạy lại
          </button>
          <button type="button" onClick={onClose} aria-label="Đóng" className="p-1 rounded-full hover:bg-white/20">
            <X size={16} />
          </button>
        </div>
      </div>

      <div className="p-4 grid gap-4 sm:grid-cols-[auto_1fr] items-start">
        <div className="text-center sm:pr-4 sm:border-r border-border">
          <div className={clsx("text-4xl font-extrabold font-mono", scoreClass(qc.overall_score))}>{qc.overall_score ?? "—"}</div>
          <div className="text-[11px] text-muted-light uppercase tracking-wider">/ 100 điểm</div>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 xl:grid-cols-7 gap-2">
          {CHECK_META.map((c) => {
            const score = qc[c.field] as number | null;
            const issues = qc.issues.filter((i) => i.check === c.key);
            return (
              <div key={c.key} className="rounded-lg border border-border px-2.5 py-2">
                <div className="flex items-center gap-1 text-[11.5px] text-muted-light leading-tight">
                  {c.label}
                  {c.ai && (
                    <span className="px-1 rounded bg-violet-100 text-violet-700 text-[9.5px] font-bold" title="Kiểm tra bằng AI">AI</span>
                  )}
                </div>
                <div className={clsx("text-lg font-bold font-mono", scoreClass(score))}>{score ?? "—"}</div>
                {score === null ? (
                  <span className="text-[11.5px] text-muted-light">Không chạy được</span>
                ) : issues.length > 0 ? (
                  <SeverityCounts issues={issues} />
                ) : (
                  <span className="text-[11.5px] text-up">Không có lỗi</span>
                )}
              </div>
            );
          })}
        </div>
      </div>
      <div className="px-4 pb-3 flex flex-wrap items-center justify-between gap-2">
        <p className="text-[11.5px] text-muted-light">
          Kiểm tra lúc {checkedAt}{qc.checked_by ? ` · ${qc.checked_by}` : ""}. Chi tiết từng lỗi hiển thị ngay đầu mỗi mục trong báo cáo bên dưới.
        </p>
        {qc.issues.length > 0 && (
          <button
            type="button"
            onClick={() => setShowAll((v) => !v)}
            className="flex items-center gap-1 text-[12px] font-semibold text-violet-700 hover:text-violet-900"
          >
            {showAll ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            {showAll ? "Ẩn danh sách" : `Xem tất cả ${qc.issues.length} mục`}
          </button>
        )}
      </div>
      {showAll && (
        <div className="px-4 pb-4 pt-3 border-t border-border">
          <IssueList issues={qc.issues} />
        </div>
      )}
    </div>
  );
}
