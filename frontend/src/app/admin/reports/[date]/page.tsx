"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, Send, AlertCircle, FileText, CheckCircle2, Trash2, Edit2, X, Save, Loader2, RefreshCw } from "lucide-react";
import Link from "next/link";
import clsx from "clsx";
import { api } from "@/lib/api";
import type { Report } from "@/lib/types";
import { ReportDocument } from "@/components/ReportDocument";

const POLL_INTERVAL_MS = 5000;

export default function AdminReportReview() {
  const params = useParams();
  const router = useRouter();
  const date = params.date as string;

  const [report, setReport] = useState<Report | null>(null);
  const [loading, setLoading] = useState(true);
  const [publishing, setPublishing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [error, setError] = useState("");

  const [isEditing, setIsEditing] = useState(false);
  const [editContent, setEditContent] = useState("");
  const [saving, setSaving] = useState(false);

  const fetchReport = useCallback(async () => {
    try {
      const res = await api.get(`/api/admin/reports/${date}`);
      setReport(res.data);
    } catch (err: any) {
      setError(err.response?.status === 404 ? "Không tìm thấy báo cáo cho ngày này." : "Lỗi kết nối đến server.");
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
      alert(err.response?.data?.detail || "Lỗi khi thử sinh lại báo cáo");
    } finally {
      setRetrying(false);
    }
  };

  const handlePublish = async () => {
    if (!confirm("Sau khi duyệt, báo cáo sẽ hiện ra cho user xem và không thể chỉnh sửa lại. Bạn có chắc chắn?")) return;

    setPublishing(true);
    try {
      await api.post(`/api/admin/reports/${date}/publish`);
      setReport(prev => prev ? { ...prev, status: "published" } : null);
      setIsEditing(false);
    } catch (err: any) {
      alert(err.response?.data?.detail || "Lỗi khi duyệt báo cáo");
    } finally {
      setPublishing(false);
    }
  };

  const handleDelete = async () => {
    if (!confirm("Bạn có chắc chắn muốn xóa báo cáo này vĩnh viễn không?")) return;
    setDeleting(true);
    try {
      await api.delete(`/api/admin/reports/${date}`);
      router.push("/admin/reports");
    } catch (err: any) {
      alert(err.response?.data?.detail || "Lỗi khi xóa báo cáo");
      setDeleting(false);
    }
  };

  const handleEditClick = () => {
    if (!report) return;
    setEditContent(JSON.stringify(report.content, null, 2));
    setIsEditing(true);
  };

  const handleSaveEdit = async () => {
    let parsedContent;
    try {
      parsedContent = JSON.parse(editContent);
    } catch (e) {
      alert("JSON không hợp lệ! Vui lòng kiểm tra lại cấu trúc (dấu ngoặc, dấu phẩy...).");
      return;
    }

    setSaving(true);
    try {
      await api.put(`/api/admin/reports/${date}`, { content: parsedContent });
      setReport(prev => prev ? { ...prev, content: parsedContent } : null);
      setIsEditing(false);
    } catch (err: any) {
      alert(err.response?.data?.detail || "Lỗi khi lưu báo cáo");
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
          &larr; Quay lại danh sách
        </Link>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <Link href="/admin/reports" className="flex items-center text-body hover:text-primary transition-colors duration-300 ease-in-out text-sm">
          <ArrowLeft size={16} className="mr-2" />
          Quay lại danh sách
        </Link>
        <div className="flex items-center gap-3">
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
              {retrying ? "Đang thử lại..." : "Thử sinh lại"}
            </button>
          )}

          <button
            onClick={handleDelete}
            disabled={deleting}
            className="flex items-center gap-1.5 bg-red-50 text-red-600 hover:bg-red-100 px-3 py-1.5 rounded-md font-medium text-sm transition-colors border border-red-200"
          >
            <Trash2 size={16} />
            {deleting ? "Đang xóa..." : "Xóa"}
          </button>

          {report.status === 'draft' && !isEditing && (
            <button
              onClick={handleEditClick}
              className="flex items-center gap-1.5 bg-surface hover:bg-surface-alt border border-border text-foreground px-4 py-1.5 rounded-full font-semibold text-sm transition-colors duration-500 ease-in-out"
            >
              <Edit2 size={14} />
              Sửa Raw JSON
            </button>
          )}

          {report.status === 'draft' && !isEditing && (
            <button
              onClick={handlePublish}
              disabled={publishing}
              className="flex items-center gap-1.5 bg-primary hover:bg-primary-dark text-white px-4 py-1.5 rounded-full font-semibold text-sm transition-colors duration-500 ease-in-out disabled:opacity-50"
            >
              <Send size={14} />
              {publishing ? "Đang xử lý..." : "Duyệt & Xuất bản"}
            </button>
          )}
        </div>
      </div>

      {isEditing ? (
        <div className="bg-background rounded-2xl border border-border shadow-[var(--shadow-soft)] p-6 mb-10">
          <div className="flex justify-between items-center mb-4">
            <h3 className="font-bold text-heading">Edit Report JSON</h3>
            <div className="flex gap-2">
              <button
                onClick={() => setIsEditing(false)}
                className="flex items-center gap-1.5 text-muted-light hover:text-foreground px-3 py-1.5 rounded-md text-sm font-medium transition-colors"
              >
                <X size={16} />
                Hủy
              </button>
              <button
                onClick={handleSaveEdit}
                disabled={saving}
                className="flex items-center gap-1.5 bg-primary hover:bg-primary-dark text-white px-4 py-1.5 rounded-md text-sm font-semibold transition-colors disabled:opacity-50"
              >
                <Save size={16} />
                {saving ? "Đang lưu..." : "Lưu thay đổi"}
              </button>
            </div>
          </div>
          <div className="bg-yellow-50 text-yellow-800 text-xs px-3 py-2 rounded mb-4 border border-yellow-200">
            <strong>Cảnh báo:</strong> Đảm bảo định dạng JSON hợp lệ. Dùng dấu ngoặc kép <code>" "</code> cho các khóa (keys).
          </div>
          <textarea
            value={editContent}
            onChange={(e) => setEditContent(e.target.value)}
            className="w-full h-[600px] font-mono text-sm p-4 bg-surface border border-border rounded-lg outline-none focus:border-primary focus:ring-1 focus:ring-primary/20 resize-y"
          />
        </div>
      ) : report.status === 'generating' ? (
        <div className="text-center py-20 bg-background border border-border rounded-2xl shadow-[var(--shadow-soft)]">
          <Loader2 size={40} className="mx-auto text-primary animate-spin mb-4" />
          <h2 className="text-lg font-bold text-heading mb-2">Đang sinh báo cáo...</h2>
          <p className="text-body">Quá trình này có thể mất vài phút. Trang sẽ tự cập nhật khi xong, không cần tải lại.</p>
        </div>
      ) : report.status === 'failed' ? (
        <div className="text-center py-20 bg-background border border-red-200 rounded-2xl shadow-[var(--shadow-soft)]">
          <AlertCircle size={40} className="mx-auto text-red-500 mb-4" />
          <h2 className="text-lg font-bold text-heading mb-2">Sinh báo cáo thất bại</h2>
          <p className="text-red-600 mb-6 max-w-xl mx-auto">{report.error_message || "Không rõ nguyên nhân."}</p>
          <button
            onClick={handleRetry}
            disabled={retrying}
            className="inline-flex items-center gap-1.5 bg-primary hover:bg-primary-dark text-white px-4 py-2 rounded-full font-semibold text-sm transition-colors disabled:opacity-50"
          >
            <RefreshCw size={14} className={retrying ? "animate-spin" : ""} />
            {retrying ? "Đang thử lại..." : "Thử sinh lại"}
          </button>
        </div>
      ) : (
        <ReportDocument report={report} />
      )}
    </div>
  );
}
