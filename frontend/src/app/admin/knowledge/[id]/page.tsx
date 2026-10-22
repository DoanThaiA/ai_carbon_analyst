"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import clsx from "clsx";
import { format } from "date-fns";
import { useTranslations } from "next-intl";
import { AlertCircle, AlertTriangle, ArrowLeft, Download, Loader2, RefreshCw, Save, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import type { KnowledgeChunk, KnowledgeDocumentDetail } from "@/lib/types";
import {
  KnowledgeMetaForm, metaFromDocument, metaPayload, type KnowledgeMeta,
} from "@/components/admin/KnowledgeMetaForm";
import { KnowledgeSearchPanel } from "@/components/admin/KnowledgeSearchPanel";
import { KnowledgeStatusBadge, formatBytes } from "@/components/admin/KnowledgeStatusBadge";
import { apiErrorDetail } from "@/lib/knowledgeUpload";

const POLL_MS = 3000;
type Tab = "content" | "chunks" | "search";

function pageRange(from: number | null, to: number | null): string {
  if (!from) return "";
  return to && to !== from ? `${from}–${to}` : String(from);
}

export default function AdminKnowledgeDetailPage() {
  const t = useTranslations("adminKnowledge");
  const params = useParams();
  const router = useRouter();
  const id = params.id as string;
  const [doc, setDoc] = useState<KnowledgeDocumentDetail | null>(null);
  const [meta, setMeta] = useState<KnowledgeMeta | null>(null);
  const [chunks, setChunks] = useState<KnowledgeChunk[] | null>(null);
  const [tab, setTab] = useState<Tab>("content");
  const [error, setError] = useState("");
  const [formError, setFormError] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  const fail = useCallback(
    (err: unknown) => {
      const status = (err as { response?: { status?: number } } | null)?.response?.status;
      setError(status === 404 ? t("notFound") : t("loadError"));
    },
    [t],
  );

  // Làm mới trạng thái (khi đang xử lý) — không đụng form đang sửa.
  const refresh = useCallback(
    () =>
      api
        .get(`/api/admin/knowledge/${id}`)
        .then((res) => setDoc(res.data))
        .catch(fail),
    [id, fail],
  );

  useEffect(() => {
    api
      .get(`/api/admin/knowledge/${id}`)
      .then((res) => {
        setDoc(res.data);
        setMeta(metaFromDocument(res.data));
      })
      .catch(fail);
  }, [id, fail]);

  const processing = doc?.status === "pending" || doc?.status === "processing";
  useEffect(() => {
    if (!processing) return;
    const timer = setInterval(() => {
      refresh();
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [processing, refresh]);

  const processedAt = doc?.processed_at;
  const ready = doc?.status === "ready";
  useEffect(() => {
    if (tab !== "chunks" || !ready) return;
    let cancelled = false;
    api
      .get(`/api/admin/knowledge/${id}/chunks`)
      .then((r) => !cancelled && setChunks(r.data))
      .catch(() => !cancelled && setChunks([]));
    return () => {
      cancelled = true;
    };
  }, [tab, id, ready, processedAt]);

  const save = async () => {
    if (!meta) return;
    if (!meta.title.trim()) return setFormError(t("errNoTitle"));
    if (meta.valid_from && meta.valid_to && meta.valid_to < meta.valid_from) return setFormError(t("errValidity"));
    setSaving(true);
    setFormError("");
    try {
      const res = await api.patch(`/api/admin/knowledge/${id}`, metaPayload(meta));
      setDoc((d) => (d ? { ...d, ...res.data } : d));
      setMeta(metaFromDocument(res.data));
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err) {
      setFormError(apiErrorDetail(err, t("saveError")));
    } finally {
      setSaving(false);
    }
  };

  const toggleActive = async (active: boolean) => {
    setBusy(true);
    try {
      const res = await api.patch(`/api/admin/knowledge/${id}`, { is_active: active });
      setDoc((d) => (d ? { ...d, ...res.data } : d));
    } catch (err) {
      alert(apiErrorDetail(err, t("saveError")));
    } finally {
      setBusy(false);
    }
  };

  const reprocess = async () => {
    setBusy(true);
    try {
      const res = await api.post(`/api/admin/knowledge/${id}/reprocess`);
      setDoc((d) => (d ? { ...d, ...res.data } : d));
    } catch (err) {
      alert(apiErrorDetail(err, t("reprocessError")));
    } finally {
      setBusy(false);
    }
  };

  const download = async () => {
    try {
      const res = await api.get(`/api/admin/knowledge/${id}/download-url`);
      window.open(res.data.url, "_blank", "noopener,noreferrer");
    } catch (err) {
      alert(apiErrorDetail(err, t("downloadError")));
    }
  };

  const remove = async () => {
    if (!doc || !confirm(t("confirmDelete", { title: doc.title }))) return;
    setBusy(true);
    try {
      await api.delete(`/api/admin/knowledge/${id}`);
      router.push("/admin/knowledge");
    } catch (err) {
      alert(apiErrorDetail(err, t("deleteError")));
      setBusy(false);
    }
  };

  if (error) {
    return (
      <div className="max-w-4xl space-y-4">
        <Link href="/admin/knowledge" className="flex items-center text-body hover:text-primary text-sm">
          <ArrowLeft size={16} className="mr-2" />{t("backToList")}
        </Link>
        <p className="text-sm text-down">{error}</p>
      </div>
    );
  }
  if (!doc || !meta) return <p className="text-sm text-muted-light">…</p>;

  return (
    <div className="max-w-5xl space-y-5">
      <Link href="/admin/knowledge" className="flex items-center text-body hover:text-primary text-sm">
        <ArrowLeft size={16} className="mr-2" />{t("backToList")}
      </Link>

      <section className="bg-background border border-border rounded-2xl p-5 space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-xl font-bold text-heading break-words">{doc.title}</h1>
              <KnowledgeStatusBadge doc={doc} />
            </div>
            <p className="text-[12.5px] text-muted-light mt-1">
              {[
                doc.file_name,
                `${doc.media_type.toUpperCase()} · ${formatBytes(doc.size_bytes)}`,
                doc.page_count != null && t("pages", { n: doc.page_count }),
                doc.char_count != null && t("chars", { n: doc.char_count.toLocaleString() }),
                doc.chunk_count != null && doc.status === "ready" && t("chunks", { n: doc.chunk_count }),
                doc.processed_at && t("processedAt", { time: format(new Date(doc.processed_at), "HH:mm dd/MM/yyyy") }),
              ].filter(Boolean).join(" · ")}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {doc.status === "ready" && (
              <label className="flex items-center gap-1.5 text-[12px] font-semibold text-label cursor-pointer mr-1">
                <input type="checkbox" className="accent-[var(--color-primary)]" checked={doc.is_active} disabled={busy} onChange={(e) => toggleActive(e.target.checked)} />
                {t("activeToggle")}
              </label>
            )}
            <button type="button" onClick={download} className="flex items-center gap-1.5 py-1.5 px-3 rounded-full border border-border text-xs font-semibold text-body hover:bg-surface">
              <Download size={14} />{t("download")}
            </button>
            {(doc.status === "ready" || doc.status === "failed") && (
              <button type="button" onClick={reprocess} disabled={busy} className="flex items-center gap-1.5 py-1.5 px-3 rounded-full border border-border text-xs font-semibold text-body hover:bg-surface disabled:opacity-40">
                <RefreshCw size={14} />{t("reprocess")}
              </button>
            )}
            <button type="button" onClick={remove} disabled={busy} className="flex items-center gap-1.5 py-1.5 px-3 rounded-full border border-red-200 text-xs font-semibold text-down hover:bg-red-50 disabled:opacity-40">
              <Trash2 size={14} />{t("delete")}
            </button>
          </div>
        </div>

        {processing && (
          <p className="flex items-center gap-2 text-sm text-blue-700"><Loader2 size={15} className="animate-spin" />{t("processingHint")}</p>
        )}
        {doc.status === "failed" && doc.error_message && (
          <div className="bg-red-50 border border-red-200 text-red-700 px-3 py-2 rounded-lg flex items-start gap-2 text-sm">
            <AlertCircle size={16} className="shrink-0 mt-0.5" /><p>{doc.error_message}</p>
          </div>
        )}
        {doc.warnings?.length ? (
          <div className="bg-amber-50 border border-amber-200 text-amber-800 px-3 py-2 rounded-lg text-sm space-y-1">
            {doc.warnings.map((w, i) => (
              <p key={i} className="flex items-start gap-2"><AlertTriangle size={15} className="shrink-0 mt-0.5" />{w}</p>
            ))}
          </div>
        ) : null}
      </section>

      <section className="bg-background border border-border rounded-2xl p-5">
        <h2 className="text-xs font-bold uppercase tracking-wider text-primary-dark mb-3">{t("metaTitle")}</h2>
        <KnowledgeMetaForm value={meta} onChange={setMeta} />
        {formError && (
          <div className="mt-4 bg-red-50 border border-red-200 text-red-700 px-3 py-2 rounded-lg flex items-start gap-2 text-sm">
            <AlertCircle size={16} className="shrink-0 mt-0.5" /><p>{formError}</p>
          </div>
        )}
        <div className="flex flex-wrap items-center gap-3 pt-4">
          <button type="button" onClick={save} disabled={saving} className="btn-pill py-2 px-5">
            {saving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
            {saving ? t("saving") : t("save")}
          </button>
          {saved && <span className="text-sm text-emerald-700">{t("saved")}</span>}
          <span className="text-[11px] text-muted-light">{t("titleChangeHint")}</span>
        </div>
      </section>

      {doc.status === "ready" && (
        <section className="bg-background border border-border rounded-2xl p-5">
          <div className="flex flex-wrap gap-1 border-b border-border mb-4">
            {(["content", "chunks", "search"] as Tab[]).map((key) => (
              <button
                key={key}
                type="button"
                onClick={() => {
                  if (key === "chunks") setChunks(null);
                  setTab(key);
                }}
                className={clsx(
                  "px-3 py-2 text-sm font-semibold -mb-px border-b-2 transition-colors",
                  tab === key ? "border-primary text-primary-dark" : "border-transparent text-muted-light hover:text-heading",
                )}
              >
                {t(`tab_${key}`)}
              </button>
            ))}
          </div>

          {tab === "content" && (
            <div className="space-y-4 max-h-[70vh] overflow-y-auto pr-1">
              {doc.sections.map((s, i) => (
                <div key={i}>
                  {(s.heading || s.page_from) && (
                    <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-light mb-1">
                      {[s.heading, s.page_from && t("pageShort", { pages: pageRange(s.page_from, s.page_to) })].filter(Boolean).join(" · ")}
                    </p>
                  )}
                  <p className="text-[13px] leading-relaxed text-body whitespace-pre-wrap">{s.text}</p>
                </div>
              ))}
            </div>
          )}

          {tab === "chunks" && (
            chunks === null ? (
              <p className="flex items-center gap-2 text-sm text-muted-light"><Loader2 size={15} className="animate-spin" />…</p>
            ) : (
              <ol className="space-y-2 max-h-[70vh] overflow-y-auto pr-1">
                {chunks.map((c) => (
                  <li key={c.chunk_id} className="rounded-xl border border-border-soft bg-surface/60 p-3">
                    <p className="text-[11px] text-muted-light mb-1">
                      #{c.chunk_index + 1}
                      {c.page_from ? ` · ${t("pageShort", { pages: pageRange(c.page_from, c.page_to) })}` : ""}
                      {c.heading ? ` · ${c.heading}` : ""}
                      {` · ${t("chars", { n: c.content.length.toLocaleString() })}`}
                    </p>
                    <p className="text-[13px] leading-relaxed text-body whitespace-pre-wrap">{c.content}</p>
                  </li>
                ))}
              </ol>
            )
          )}

          {tab === "search" && <KnowledgeSearchPanel docId={doc.id} />}
        </section>
      )}
    </div>
  );
}
