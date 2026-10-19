"use client";

// "Sửa báo cáo theo QC" — CHỈ admin, CHỈ bản nháp. Luồng 2 bước (backend: services/report_qc_fix.py):
// 1. fix-preview: backend neo từng lỗi QC vào đúng đoạn văn, AI đề xuất thay thế tối thiểu, Python kiểm (không bịa số,
//    không đụng nguồn, không sửa lan) + Jack rule-based trước/sau → modal hiện diff từng đoạn để admin chọn.
// 2. fix-apply: ghi các đoạn đã chọn (từ chối nếu báo cáo đã đổi sau lúc xem trước) và đánh dấu lỗi "Đã sửa".
//    Hoàn tác = áp lại theo chiều ngược.
import { useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { useTranslations } from "next-intl";
import { AlertTriangle, CheckCircle2, Loader2, Undo2, Wand2, X } from "lucide-react";
import { api } from "@/lib/api";
import type { QcFixApplyResult, QcFixChange, QcFixPreview, Report } from "@/lib/types";

type DiffPart = { text: string; kind: "same" | "del" | "ins" };

// Diff theo từ (LCS) — đoạn báo cáo ngắn (vài trăm từ) nên O(n·m) là đủ.
function wordDiff(a: string, b: string): DiffPart[] {
  const A = a.split(/(\s+)/);
  const B = b.split(/(\s+)/);
  const n = A.length;
  const m = B.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--) dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out: DiffPart[] = [];
  const push = (text: string, kind: DiffPart["kind"]) => {
    const last = out[out.length - 1];
    if (last && last.kind === kind) last.text += text;
    else out.push({ text, kind });
  };
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (A[i] === B[j]) { push(A[i], "same"); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) push(A[i++], "del");
    else push(B[j++], "ins");
  }
  while (i < n) push(A[i++], "del");
  while (j < m) push(B[j++], "ins");
  return out;
}

function Diff({ before, after }: { before: string; after: string }) {
  const parts = useMemo(() => wordDiff(before, after), [before, after]);
  return (
    <p className="text-sm text-body whitespace-pre-wrap break-words leading-relaxed">
      {parts.map((p, k) =>
        p.kind === "same" ? <span key={k}>{p.text}</span>
          : p.kind === "del" ? <del key={k} className="bg-red-50 text-down decoration-down/60">{p.text}</del>
            : <ins key={k} className="bg-tint text-primary-dark no-underline font-medium">{p.text}</ins>,
      )}
    </p>
  );
}

type Applied = { changes: QcFixChange[]; hash: string; runNo: number };

export function QcFixModal({
  date, findingIds, onClose, onApplied,
}: {
  date: string;
  findingIds?: number[]; // trống = mọi lỗi còn mở của biên bản mới nhất
  onClose: () => void;
  onApplied: (content: Report["content"]) => void;
}) {
  const t = useTranslations("adminQcFix");
  const [preview, setPreview] = useState<QcFixPreview | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [applied, setApplied] = useState<Applied | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.post(`/api/admin/qc-reviews/report/${date}/fix-preview`, { finding_ids: findingIds ?? null })
      .then((res) => {
        if (cancelled) return;
        const data: QcFixPreview = res.data;
        setPreview(data);
        setSelected(new Set(data.changes.filter((c) => c.selected).map((c) => c.id)));
      })
      .catch((err) => !cancelled && setError(err.response?.data?.detail || t("previewError")));
    return () => { cancelled = true; };
  }, [date, findingIds, t]);

  const findingTitle = useMemo(() => {
    const m = new Map<number, string>();
    preview?.findings.forEach((f) => m.set(f.id, f.title));
    return m;
  }, [preview]);

  const toggle = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (!next.delete(id)) next.add(id);
      return next;
    });

  const send = async (changes: QcFixChange[], baseHash: string, undo: boolean): Promise<QcFixApplyResult> => {
    const res = await api.post(`/api/admin/qc-reviews/report/${date}/fix-apply`, {
      base_hash: baseHash,
      run_no: preview?.run_no ?? null,
      undo,
      changes: changes.map((c) => ({ path: c.path, before: c.before, after: c.after, finding_ids: c.finding_ids })),
    });
    return res.data;
  };

  const handleApply = async () => {
    if (!preview) return;
    const chosen = preview.changes.filter((c) => selected.has(c.id));
    if (!chosen.length) return;
    setBusy(true);
    try {
      const data = await send(chosen, preview.base_hash, false);
      onApplied(data.content);
      setApplied({ changes: chosen, hash: data.content_hash, runNo: preview.run_no });
    } catch (err: any) {
      alert(err.response?.data?.detail || t("applyError"));
    } finally {
      setBusy(false);
    }
  };

  const handleUndo = async () => {
    if (!applied) return;
    setBusy(true);
    try {
      const reversed = applied.changes.map((c) => ({ ...c, before: c.after, after: c.before }));
      const data = await send(reversed, applied.hash, true);
      onApplied(data.content);
      onClose();
    } catch (err: any) {
      alert(err.response?.data?.detail || t("undoError"));
    } finally {
      setBusy(false);
    }
  };

  const notFixed = preview?.findings.filter((f) => f.status !== "proposed") ?? [];
  const proposedCount = preview?.findings.filter((f) => f.status === "proposed").length ?? 0;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 print:hidden">
      <div className="absolute inset-0 bg-black/40" onClick={busy ? undefined : onClose} />
      <div className="relative w-full max-w-3xl max-h-[90vh] flex flex-col bg-background border border-border rounded-2xl shadow-[var(--shadow-medium)]">
        <div className="flex items-center justify-between gap-2 px-5 py-4 border-b border-border">
          <h3 className="flex items-center gap-2 font-bold text-heading">
            <Wand2 size={18} className="text-primary" />
            {t("title", { date })}
          </h3>
          <button type="button" onClick={onClose} disabled={busy} className="p-1 rounded hover:bg-surface text-muted-light" aria-label={t("close")}>
            <X size={18} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          {error ? (
            <p className="text-sm text-down">{error}</p>
          ) : !preview ? (
            <div className="flex flex-col items-center gap-3 py-12 text-sm text-muted-light">
              <Loader2 className="animate-spin text-primary" />
              {t("loading")}
            </div>
          ) : applied ? (
            <div className="rounded-xl border border-primary/20 bg-tint px-4 py-3 text-sm text-primary-dark flex items-start gap-2">
              <CheckCircle2 size={18} className="shrink-0 mt-0.5" />
              <span>{t("appliedNotice", { count: applied.changes.length })}</span>
            </div>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-body">
                <span>{t("summary", { changes: preview.changes.length, fixed: proposedCount, manual: notFixed.length })}</span>
                {preview.jack_score_before != null && preview.jack_score_after != null && (
                  <span className="text-xs text-muted-light">
                    {t("jackScore", { before: preview.jack_score_before, after: preview.jack_score_after })}
                  </span>
                )}
              </div>
              <p className="text-xs text-muted-light">{t("guarantee")}</p>
              {preview.review_is_stale && (
                <div className="rounded-lg border border-warn/30 bg-warn-tint text-warn text-xs px-3 py-2">{t("staleNotice")}</div>
              )}

              {preview.changes.length === 0 ? (
                <p className="text-sm text-muted-light">{t("noChanges")}</p>
              ) : (
                <ul className="space-y-3">
                  {preview.changes.map((c) => (
                    <li key={c.id} className={clsx("rounded-xl border p-3", selected.has(c.id) ? "border-primary/40 bg-background" : "border-border bg-surface opacity-80")}>
                      <label className="flex items-start gap-2 cursor-pointer">
                        <input type="checkbox" className="mt-1 accent-[var(--color-primary)]" checked={selected.has(c.id)} onChange={() => toggle(c.id)} />
                        <div className="min-w-0 flex-1 space-y-1.5">
                          <div className="flex flex-wrap gap-1.5 text-[11px]">
                            <span className="font-mono text-muted-light break-all">{c.path}</span>
                          </div>
                          <ul className="text-xs text-heading font-semibold space-y-0.5">
                            {c.finding_ids.map((id) => <li key={id}>• {findingTitle.get(id) ?? `#${id}`}</li>)}
                          </ul>
                          <Diff before={c.before} after={c.after} />
                          {c.edits.some((e) => e.note) && (
                            <p className="text-xs text-muted-light">{c.edits.map((e) => e.note).filter(Boolean).join(" · ")}</p>
                          )}
                          {c.jack_regressions.length > 0 && (
                            <div className="flex items-start gap-1.5 rounded-lg border border-warn/30 bg-warn-tint text-warn text-xs px-2 py-1.5">
                              <AlertTriangle size={13} className="shrink-0 mt-0.5" />
                              <span>{t("jackRegression")}: {c.jack_regressions.join(" · ")}</span>
                            </div>
                          )}
                          {c.rejected.length > 0 && (
                            <p className="text-[11px] text-muted-light">{t("rejectedEdits", { count: c.rejected.length })}: {c.rejected.map((r) => r.reason).join(" · ")}</p>
                          )}
                        </div>
                      </label>
                    </li>
                  ))}
                </ul>
              )}

              {notFixed.length > 0 && (
                <div>
                  <h4 className="text-xs font-bold uppercase tracking-wider text-muted-light mb-2">{t("manualTitle")}</h4>
                  <ul className="space-y-1.5">
                    {notFixed.map((f) => (
                      <li key={f.id} className="text-sm">
                        <span className="font-semibold text-heading">{f.title}</span>
                        <span className="ml-1.5 text-[11px] px-1.5 py-0.5 rounded bg-surface-alt text-muted-light">{t(`status_${f.status}`)}</span>
                        {f.reason && <p className="text-xs text-muted-light">{f.reason}</p>}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
        </div>

        <div className="flex flex-wrap justify-end gap-2 px-5 py-3 border-t border-border">
          {applied ? (
            <>
              <button type="button" onClick={handleUndo} disabled={busy} className="flex items-center gap-1.5 px-4 py-1.5 rounded-full border border-border text-sm font-semibold text-body hover:bg-surface disabled:opacity-50">
                {busy ? <Loader2 size={14} className="animate-spin" /> : <Undo2 size={14} />}
                {t("undo")}
              </button>
              <button type="button" onClick={onClose} className="px-4 py-1.5 rounded-full bg-primary hover:bg-primary-dark text-white text-sm font-semibold">
                {t("done")}
              </button>
            </>
          ) : (
            <>
              <button type="button" onClick={onClose} disabled={busy} className="px-4 py-1.5 rounded-full text-sm font-medium text-muted-light hover:text-foreground">
                {t("cancel")}
              </button>
              <button
                type="button"
                onClick={handleApply}
                disabled={busy || !preview || selected.size === 0}
                className="flex items-center gap-1.5 px-4 py-1.5 rounded-full bg-primary hover:bg-primary-dark text-white text-sm font-semibold disabled:opacity-50"
              >
                {busy ? <Loader2 size={14} className="animate-spin" /> : <CheckCircle2 size={14} />}
                {t("apply", { count: selected.size })}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
