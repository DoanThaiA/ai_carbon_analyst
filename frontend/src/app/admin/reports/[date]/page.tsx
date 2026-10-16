"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, Send, AlertCircle, FileText, CheckCircle2, Trash2, Edit2, X, Save, Loader2, RefreshCw, Download, Undo2 } from "lucide-react";
import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import clsx from "clsx";
import { api } from "@/lib/api";
import type { Report } from "@/lib/types";
import { ReportDocument } from "@/components/ReportDocument";
import { ReportEditor } from "@/components/ReportEditor";
import { QuoteChat } from "@/components/QuoteChat";
import { EnglishFallbackNotice } from "@/components/EnglishFallbackNotice";
import { localizeReport, removeBizFromContentEn } from "@/lib/reportLocale";

const POLL_INTERVAL_MS = 5000;

export default function AdminReportReview() {
  const t = useTranslations("adminMiscReportDetail");
  const locale = useLocale();
  const params = useParams();
  const router = useRouter();
  const date = params.date as string;

  const [report, setReport] = useState<Report | null>(null);
  const [loading, setLoading] = useState(true);
  const [publishing, setPublishing] = useState(false);
  const [unpublishing, setUnpublishing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [error, setError] = useState("");

  const [isEditing, setIsEditing] = useState(false);
  const [editContent, setEditContent] = useState<any>(null);
  const [saving, setSaving] = useState(false);

  const fetchReport = useCallback(async () => {
    try {
      const res = await api.get(`/api/admin/reports/${date}`);
      setReport(res.data);
    } catch (err: any) {
      setError(err.response?.status === 404 ? t("notFound") : t("connectionError"));
    } finally {
      setLoading(false);
    }
  }, [date]);

  useEffect(() => {
    if (date) fetchReport();
  }, [date, fetchReport]);

  // /generate chạy nền — trong lúc report.status === 'generating', poll lại
  // mỗi POLL_INTERVAL_MS để tự cập nhật khi job xong (hoặc lỗi).
  useEffect(() => {
    if (report?.status !== "generating") return;
    const timer = setTimeout(fetchReport, POLL_INTERVAL_MS);
    return () => clearTimeout(timer);
  }, [report, fetchReport]);

  const handleRetry = async () => {
    setRetrying(true);
    try {
      await api.post(`/api/admin/reports/generate?date=${date}`);
      await fetchReport();
    } catch (err: any) {
      alert(err.response?.data?.detail || t("retryError"));
    } finally {
      setRetrying(false);
    }
  };

  const handlePublish = async () => {
    if (!confirm(t("confirmPublish"))) return;

    setPublishing(true);
    try {
      await api.post(`/api/admin/reports/${date}/publish`);
      setReport(prev => prev ? { ...prev, status: "published" } : null);
      setIsEditing(false);
    } catch (err: any) {
      alert(err.response?.data?.detail || t("publishError"));
    } finally {
      setPublishing(false);
    }
  };

  const handleUnpublish = async () => {
    if (!confirm(t("confirmUnpublish"))) return;

    setUnpublishing(true);
    try {
      await api.post(`/api/admin/reports/${date}/unpublish`);
      setReport(prev => prev ? { ...prev, status: "draft", published_at: null } : null);
    } catch (err: any) {
      alert(err.response?.data?.detail || t("unpublishError"));
    } finally {
      setUnpublishing(false);
    }
  };

  const handleDelete = async () => {
    if (!confirm(t("confirmDelete"))) return;
    setDeleting(true);
    try {
      await api.delete(`/api/admin/reports/${date}`);
      router.push("/admin/reports");
    } catch (err: any) {
      alert(err.response?.data?.detail || t("deleteError"));
      setDeleting(false);
    }
  };

  // Gỡ 1 gợi ý kinh doanh khỏi báo cáo (đề xuất phi thực tế...) — Jenny cũng thôi
  // theo dõi/nhắc lại gợi ý đó ở các báo cáo sau (services/biz_memory.py).
  const handleDismissBizSuggestion = async (id: number) => {
    const reason = window.prompt(t("dismissPrompt"), "");
    if (reason === null) return; // bấm Huỷ
    try {
      const res = await api.post(`/api/admin/reports/${date}/biz-suggestions/${id}/dismiss`, {
        reason: reason.trim() || null,
      });
      setReport((prev) => (prev ? {
        ...prev,
        content: { ...prev.content, biz: res.data.biz },
        content_en: prev.content_en ? removeBizFromContentEn(prev.content_en, id) : prev.content_en,
      } : prev));
    } catch (err: any) {
      alert(err.response?.data?.detail || t("dismissError"));
    }
  };

  const handleEditClick = () => {
    if (!report) return;
    // Deep clone để không sửa trực tiếp state `report` trong lúc đang edit.
    setEditContent(JSON.parse(JSON.stringify(report.content)));
    setIsEditing(true);
  };

  const handleSaveEdit = async () => {
    setSaving(true);
    try {
      await api.put(`/api/admin/reports/${date}`, { content: editContent });
      // Bản dịch cũ không còn khớp nội dung vừa sửa — backend dịch lại nền; tới khi tải lại
      // trang, giao diện EN hiện bản tiếng Việt kèm thông báo.
      setReport(prev => prev ? { ...prev, content: editContent, content_en: null } : null);
      setIsEditing(false);
    } catch (err: any) {
      alert(err.response?.data?.detail || t("saveError"));
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex flex-col space-y-6 animate-pulse">
        <div className="h-10 w-1/3 bg-background border border-border rounded"></div>
        <div className="h-64 w-full bg-background border border-border rounded-2xl"></div>
      </div>
    );
  }

  if (error || !report) {
    return (
      <div className="text-center py-20 bg-background border border-red-200 rounded-2xl shadow-[var(--shadow-soft)]">
        <AlertCircle size={48} className="mx-auto text-red-500 mb-4" />
        <h2 className="text-xl font-bold text-heading mb-2">Oops!</h2>
        <p className="text-red-600 mb-6">{error}</p>
        <Link href="/admin/reports" className="text-primary hover:text-primary-dark font-semibold">
          &larr; {t("backToList")}
        </Link>
      </div>
    );
  }

  return (
    <div>
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-6 print:hidden">
        <Link href="/admin/reports" className="flex items-center text-body hover:text-primary transition-colors duration-300 ease-in-out text-sm">
          <ArrowLeft size={16} className="mr-2" />
          {t("backToList")}
        </Link>
        <div className="flex flex-wrap items-center gap-2 sm:gap-3">
          {!isEditing && report.status !== 'generating' && report.status !== 'failed' && (
            <button
              onClick={() => window.print()}
              className="flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-full border border-border-soft text-body hover:border-primary hover:text-primary-dark transition-colors"
            >
              <Download size={14} />
              {t("downloadPdf")}
            </button>
          )}

          <div className={clsx(
            "px-2.5 py-1 text-xs font-mono font-semibold rounded flex items-center gap-2",
            report.status === 'published' ? "bg-tint text-primary-dark border border-primary/20" :
            report.status === 'generating' ? "bg-surface-alt text-muted-light border border-border" :
            report.status === 'failed' ? "bg-red-50 text-down border border-red-200" :
            "bg-warn-tint text-warn border border-warn/20"
          )}>
            {report.status === 'published' ? <CheckCircle2 size={14} /> :
             report.status === 'generating' ? <Loader2 size={14} className="animate-spin" /> :
             report.status === 'failed' ? <AlertCircle size={14} /> :
             <FileText size={14} />}
            {report.status.toUpperCase()}
          </div>

          {report.status === 'failed' && (
            <button
              onClick={handleRetry}
              disabled={retrying}
              className="flex items-center gap-1.5 bg-primary hover:bg-primary-dark text-white px-3 py-1.5 rounded-md font-medium text-sm transition-colors disabled:opacity-50"
            >
              <RefreshCw size={14} className={retrying ? "animate-spin" : ""} />
              {retrying ? t("retrying") : t("retry")}
            </button>
          )}

          <button
            onClick={handleDelete}
            disabled={deleting}
            className="flex items-center gap-1.5 bg-red-50 text-red-600 hover:bg-red-100 px-3 py-1.5 rounded-md font-medium text-sm transition-colors border border-red-200"
          >
            <Trash2 size={16} />
            {deleting ? t("deleting") : t("delete")}
          </button>

          {report.status === 'published' && (
            <button
              onClick={handleUnpublish}
              disabled={unpublishing}
              className="flex items-center gap-1.5 bg-surface hover:bg-surface-alt border border-border text-foreground px-4 py-1.5 rounded-full font-semibold text-sm transition-colors duration-500 ease-in-out disabled:opacity-50"
            >
              <Undo2 size={14} />
              {unpublishing ? t("processing") : t("backToDraft")}
            </button>
          )}

          {report.status === 'draft' && !isEditing && (
            <button
              onClick={handleEditClick}
              className="flex items-center gap-1.5 bg-surface hover:bg-surface-alt border border-border text-foreground px-4 py-1.5 rounded-full font-semibold text-sm transition-colors duration-500 ease-in-out"
            >
              <Edit2 size={14} />
              {t("editContent")}
            </button>
          )}

          {report.status === 'draft' && !isEditing && (
            <button
              onClick={handlePublish}
              disabled={publishing}
              className="flex items-center gap-1.5 bg-primary hover:bg-primary-dark text-white px-4 py-1.5 rounded-full font-semibold text-sm transition-colors duration-500 ease-in-out disabled:opacity-50"
            >
              <Send size={14} />
              {publishing ? t("processing") : t("publish")}
            </button>
          )}
        </div>
      </div>

      {isEditing ? (
        <div className="bg-background rounded-2xl border border-border shadow-[var(--shadow-soft)] p-6 mb-10">
          <div className="flex flex-wrap justify-between items-center gap-3 mb-4 sticky top-0 z-10 bg-background pb-2">
            <h3 className="font-bold text-heading">{t("editTitle")}</h3>
            <div className="flex flex-wrap gap-2">
              <button
                onClick={() => setIsEditing(false)}
                className="flex items-center gap-1.5 text-muted-light hover:text-foreground px-3 py-1.5 rounded-md text-sm font-medium transition-colors"
              >
                <X size={16} />
                {t("cancel")}
              </button>
              <button
                onClick={handleSaveEdit}
                disabled={saving}
                className="flex items-center gap-1.5 bg-primary hover:bg-primary-dark text-white px-4 py-1.5 rounded-md text-sm font-semibold transition-colors disabled:opacity-50"
              >
                <Save size={16} />
                {saving ? t("saving") : t("saveChanges")}
              </button>
            </div>
          </div>
          <ReportEditor content={editContent} onChange={setEditContent} />
        </div>
      ) : report.status === 'generating' ? (
        <div className="text-center py-20 bg-background border border-border rounded-2xl shadow-[var(--shadow-soft)]">
          <Loader2 size={40} className="mx-auto text-primary animate-spin mb-4" />
          <h2 className="text-lg font-bold text-heading mb-2">{t("generatingTitle")}</h2>
          <p className="text-body">{t("generatingDesc")}</p>
        </div>
      ) : report.status === 'failed' ? (
        <div className="text-center py-20 bg-background border border-red-200 rounded-2xl shadow-[var(--shadow-soft)]">
          <AlertCircle size={40} className="mx-auto text-red-500 mb-4" />
          <h2 className="text-lg font-bold text-heading mb-2">{t("failedTitle")}</h2>
          <p className="text-red-600 mb-6 max-w-xl mx-auto">{report.error_message || t("unknownCause")}</p>
          <button
            onClick={handleRetry}
            disabled={retrying}
            className="inline-flex items-center gap-1.5 bg-primary hover:bg-primary-dark text-white px-4 py-2 rounded-full font-semibold text-sm transition-colors disabled:opacity-50"
          >
            <RefreshCw size={14} className={retrying ? "animate-spin" : ""} />
            {retrying ? t("retrying") : t("retry")}
          </button>
        </div>
      ) : (
        (() => {
          // Giao diện EN → xem content_en; sửa nội dung (ReportEditor) luôn trên bản tiếng Việt.
          const { report: shown, fallback } = localizeReport(report, locale);
          return (
            <>
              {fallback && <EnglishFallbackNotice />}
              <QuoteChat reportDate={date}>
                <ReportDocument report={shown} onDismissBizSuggestion={handleDismissBizSuggestion} />
              </QuoteChat>
            </>
          );
        })()
      )}
    </div>
  );
}
