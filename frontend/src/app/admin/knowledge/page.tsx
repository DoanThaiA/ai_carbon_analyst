"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import clsx from "clsx";
import { format } from "date-fns";
import { useTranslations } from "next-intl";
import {
  AlertCircle, BookOpen, CheckCircle2, FileText, Loader2, RefreshCw, Search, Trash2, Upload, X,
} from "lucide-react";
import { api } from "@/lib/api";
import type { KnowledgeDocument } from "@/lib/types";
import {
  KNOWLEDGE_ACCEPT, apiErrorDetail, titleFromFileName, uploadKnowledgeFile, validateKnowledgeFile,
} from "@/lib/knowledgeUpload";
import {
  KnowledgeMetaForm, emptyKnowledgeMeta, metaPayload, type KnowledgeMeta,
} from "@/components/admin/KnowledgeMetaForm";
import { KnowledgeSearchPanel } from "@/components/admin/KnowledgeSearchPanel";
import { KnowledgeStatusBadge, formatBytes } from "@/components/admin/KnowledgeStatusBadge";

const POLL_MS = 3000;

export default function AdminKnowledgePage() {
  const t = useTranslations("adminKnowledge");
  const [docs, setDocs] = useState<KnowledgeDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [uploadOpen, setUploadOpen] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);

  const fetchDocs = useCallback(
    () =>
      api
        .get("/api/admin/knowledge")
        .then((res) => {
          setDocs(res.data);
          setError("");
        })
        .catch(() => setError(t("loadError")))
        .finally(() => setLoading(false)),
    [t],
  );

  useEffect(() => {
    api
      .get("/api/admin/knowledge")
      .then((res) => setDocs(res.data))
      .catch(() => setError(t("loadError")))
      .finally(() => setLoading(false));
  }, [t]);

  // Tự làm mới khi còn tài liệu đang xử lý nền.
  const processing = docs.some((d) => d.status === "pending" || d.status === "processing");
  useEffect(() => {
    if (!processing) return;
    const timer = setInterval(() => {
      fetchDocs();
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [processing, fetchDocs]);

  const patch = async (doc: KnowledgeDocument, body: Record<string, unknown>) => {
    setBusyId(doc.id);
    try {
      const res = await api.patch(`/api/admin/knowledge/${doc.id}`, body);
      setDocs((prev) => prev.map((d) => (d.id === doc.id ? res.data : d)));
    } catch (err) {
      alert(apiErrorDetail(err, t("saveError")));
    } finally {
      setBusyId(null);
    }
  };

  const reprocess = async (doc: KnowledgeDocument) => {
    setBusyId(doc.id);
    try {
      const res = await api.post(`/api/admin/knowledge/${doc.id}/reprocess`);
      setDocs((prev) => prev.map((d) => (d.id === doc.id ? res.data : d)));
    } catch (err) {
      alert(apiErrorDetail(err, t("reprocessError")));
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (doc: KnowledgeDocument) => {
    if (!confirm(t("confirmDelete", { title: doc.title }))) return;
    setBusyId(doc.id);
    try {
      await api.delete(`/api/admin/knowledge/${doc.id}`);
      setDocs((prev) => prev.filter((d) => d.id !== doc.id));
    } catch (err) {
      alert(apiErrorDetail(err, t("deleteError")));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-3xl">
          <h2 className="text-3xl font-bold uppercase tracking-tight text-heading mb-2">{t("title")}</h2>
          <p className="text-body">{t("subtitle")}</p>
        </div>
        <button type="button" onClick={() => setUploadOpen(true)} className="btn-pill py-2 px-5">
          <Upload size={16} />
          {t("uploadBtn")}
        </button>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg flex items-start gap-3">
          <AlertCircle size={20} className="shrink-0 mt-0.5" />
          <p>{error}</p>
        </div>
      )}

      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => <div key={i} className="bg-surface border border-border rounded-2xl h-24 animate-pulse" />)}
        </div>
      ) : docs.length === 0 ? (
        <div className="text-center py-16 bg-surface border border-border-soft border-dashed rounded-2xl">
          <BookOpen size={40} className="mx-auto text-muted mb-3" />
          <p className="text-body">{t("empty")}</p>
        </div>
      ) : (
        <div className="space-y-3">
          {docs.map((doc) => (
            <div key={doc.id} className="bg-background border border-border rounded-2xl p-4 sm:p-5">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2 mb-1">
                    <FileText size={16} className="text-primary shrink-0" />
                    <Link href={`/admin/knowledge/${doc.id}`} className="font-semibold text-heading hover:text-primary break-words">
                      {doc.title}
                    </Link>
                    <KnowledgeStatusBadge doc={doc} />
                    {doc.verified && (
                      <span className="inline-flex items-center gap-1 text-[11px] text-emerald-700">
                        <CheckCircle2 size={12} />{t("verified")}
                      </span>
                    )}
                  </div>
                  <p className="text-[12.5px] text-muted-light">
                    {[
                      doc.publisher,
                      t(`kind_${doc.doc_kind}`),
                      doc.issued_on && t("issuedOn", { date: format(new Date(doc.issued_on), "dd/MM/yyyy") }),
                      `${doc.media_type.toUpperCase()} · ${formatBytes(doc.size_bytes)}`,
                      doc.page_count != null && t("pages", { n: doc.page_count }),
                      doc.chunk_count != null && doc.status === "ready" && t("chunks", { n: doc.chunk_count }),
                    ].filter(Boolean).join(" · ")}
                  </p>
                  <p className="text-[12.5px] text-body mt-1">
                    {[
                      doc.use_in_report && t("scopeReport", {
                        sections: doc.report_sections?.length
                          ? doc.report_sections.map((s) => t(`sectionShort_${s}`)).join(", ")
                          : t("allSections"),
                      }),
                      doc.use_in_chat && t("scopeChat"),
                      !doc.use_in_report && !doc.use_in_chat && t("scopeNone"),
                    ].filter(Boolean).join(" · ")}
                    {" · "}
                    {t("validity", {
                      from: format(new Date(doc.valid_from), "dd/MM/yyyy"),
                      to: doc.valid_to ? format(new Date(doc.valid_to), "dd/MM/yyyy") : t("noEnd"),
                    })}
                  </p>
                  {doc.status === "failed" && doc.error_message && (
                    <p className="mt-2 text-[12.5px] text-red-700 flex items-start gap-1.5">
                      <AlertCircle size={14} className="shrink-0 mt-0.5" />{doc.error_message}
                    </p>
                  )}
                  {doc.status === "ready" && doc.warnings?.length ? (
                    <p className="mt-2 text-[12px] text-amber-700">{t("warningsCount", { n: doc.warnings.length })}</p>
                  ) : null}
                  <p className="text-[11px] text-muted-light mt-1">
                    {t("uploadedBy", { user: doc.uploaded_by, time: format(new Date(doc.created_at), "HH:mm dd/MM/yyyy") })}
                  </p>
                </div>
                <div className="flex items-center gap-1.5 shrink-0">
                  {doc.status === "ready" && (
                    <label className="flex items-center gap-1.5 text-[12px] font-semibold text-label mr-1 cursor-pointer">
                      <input
                        type="checkbox"
                        className="accent-[var(--color-primary)]"
                        checked={doc.is_active}
                        disabled={busyId === doc.id}
                        onChange={(e) => patch(doc, { is_active: e.target.checked })}
                      />
                      {t("activeToggle")}
                    </label>
                  )}
                  {(doc.status === "ready" || doc.status === "failed") && (
                    <button
                      type="button"
                      onClick={() => reprocess(doc)}
                      disabled={busyId === doc.id}
                      className="p-1.5 rounded hover:bg-surface text-body disabled:opacity-40"
                      title={t("reprocess")}
                    >
                      <RefreshCw size={16} />
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => remove(doc)}
                    disabled={busyId === doc.id}
                    className="p-1.5 rounded hover:bg-red-50 text-down disabled:opacity-40"
                    title={t("delete")}
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {docs.some((d) => d.status === "ready") && (
        <section className="bg-background border border-border rounded-2xl p-5">
          <h3 className="flex items-center gap-2 text-sm font-bold uppercase tracking-wider text-primary-dark mb-1">
            <Search size={15} />{t("searchTitle")}
          </h3>
          <p className="text-[12.5px] text-muted-light mb-3">{t("searchHint")}</p>
          <KnowledgeSearchPanel />
        </section>
      )}

      {uploadOpen && (
        <UploadModal
          onClose={() => setUploadOpen(false)}
          onCreated={(doc) => {
            setDocs((prev) => [doc, ...prev]);
            setUploadOpen(false);
          }}
        />
      )}
    </div>
  );
}

function UploadModal({ onClose, onCreated }: { onClose: () => void; onCreated: (doc: KnowledgeDocument) => void }) {
  const t = useTranslations("adminKnowledge");
  const [file, setFile] = useState<File | null>(null);
  const [meta, setMeta] = useState<KnowledgeMeta>(emptyKnowledgeMeta);
  const [step, setStep] = useState<"idle" | "uploading" | "saving">("idle");
  const [formError, setFormError] = useState("");
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const pick = (f: File | undefined | null) => {
    if (!f) return;
    const err = validateKnowledgeFile(f, t);
    if (err) {
      setFormError(err);
      return;
    }
    setFormError("");
    setFile(f);
    setMeta((m) => (m.title ? m : { ...m, title: titleFromFileName(f.name) }));
  };

  const submit = async () => {
    if (!file) return setFormError(t("errNoFile"));
    if (!meta.title.trim()) return setFormError(t("errNoTitle"));
    if (meta.valid_from && meta.valid_to && meta.valid_to < meta.valid_from) return setFormError(t("errValidity"));
    setFormError("");
    try {
      setStep("uploading");
      const fileKey = await uploadKnowledgeFile(file, t);
      setStep("saving");
      const res = await api.post("/api/admin/knowledge", { file_key: fileKey, file_name: file.name, ...metaPayload(meta) });
      onCreated(res.data);
    } catch (err) {
      setFormError(apiErrorDetail(err, err instanceof Error && err.message ? err.message : t("saveError")));
      setStep("idle");
    }
  };

  const busy = step !== "idle";
  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/40" onClick={busy ? undefined : onClose} />
      <div className="relative w-full max-w-2xl max-h-[90vh] overflow-y-auto bg-background border border-border rounded-2xl shadow-[var(--shadow-medium)] p-6">
        <button onClick={onClose} disabled={busy} className="absolute top-4 right-4 text-muted-light hover:text-label transition-colors disabled:opacity-40">
          <X size={18} />
        </button>
        <h3 className="text-lg font-bold text-heading mb-1">{t("uploadTitle")}</h3>
        <p className="text-[12.5px] text-muted-light mb-4">{t("uploadHint")}</p>

        <div
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => { e.preventDefault(); setDragging(false); pick(e.dataTransfer.files?.[0]); }}
          onClick={() => !busy && inputRef.current?.click()}
          className={clsx(
            "mb-4 cursor-pointer rounded-xl border-2 border-dashed px-4 py-6 text-center transition-colors",
            dragging ? "border-primary bg-tint/40" : "border-border-soft hover:border-primary/60 hover:bg-surface",
          )}
        >
          <input ref={inputRef} type="file" accept={KNOWLEDGE_ACCEPT} className="hidden" onChange={(e) => pick(e.target.files?.[0])} />
          <Upload size={22} className="mx-auto text-primary mb-2" />
          {file ? (
            <p className="text-sm font-semibold text-heading break-all">{file.name} <span className="font-normal text-muted-light">· {formatBytes(file.size)}</span></p>
          ) : (
            <p className="text-sm text-body">{t("dropHint")}</p>
          )}
        </div>

        <KnowledgeMetaForm value={meta} onChange={setMeta} />

        {formError && (
          <div className="mt-4 bg-red-50 border border-red-200 text-red-700 px-3 py-2 rounded-lg flex items-start gap-2 text-sm">
            <AlertCircle size={16} className="shrink-0 mt-0.5" />
            <p>{formError}</p>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-3 pt-5">
          <button type="button" onClick={submit} disabled={busy} className="btn-pill py-2 px-5">
            {busy ? <Loader2 size={16} className="animate-spin" /> : <Upload size={16} />}
            {step === "uploading" ? t("uploading") : step === "saving" ? t("saving") : t("uploadSubmit")}
          </button>
          <button type="button" onClick={onClose} disabled={busy} className="text-body hover:text-primary text-sm font-semibold disabled:opacity-40">
            {t("cancel")}
          </button>
        </div>
      </div>
    </div>
  );
}
