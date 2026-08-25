"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { FileText, Clock, CheckCircle2, ChevronRight, PlusCircle, AlertCircle, Loader2, XCircle } from "lucide-react";
import { format } from "date-fns";
import clsx from "clsx";
import { api } from "@/lib/api";
import type { ReportSummary } from "@/lib/types";

const POLL_INTERVAL_MS = 5000;

const STATUS_STYLES: Record<ReportSummary["status"], { label: string; icon: React.ReactNode; badge: string; iconWrap: string }> = {
  published: {
    label: "Published",
    icon: <CheckCircle2 size={14} />,
    badge: "bg-tint text-primary-dark border border-primary/20",
    iconWrap: "bg-tint text-primary-dark",
  },
  draft: {
    label: "Draft",
    icon: <Clock size={14} />,
    badge: "bg-warn-tint text-warn border border-warn/20",
    iconWrap: "bg-warn-tint text-warn",
  },
  generating: {
    label: "Đang sinh...",
    icon: <Loader2 size={14} className="animate-spin" />,
    badge: "bg-surface-alt text-muted-light border border-border",
    iconWrap: "bg-surface-alt text-muted-light",
  },
  failed: {
    label: "Lỗi",
    icon: <XCircle size={14} />,
    badge: "bg-red-50 text-down border border-red-200",
    iconWrap: "bg-red-50 text-down",
  },
};

export default function AdminReportsPage() {
  const [reports, setReports] = useState<ReportSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState("");

  const fetchReports = async (): Promise<ReportSummary[]> => {
    try {
      const res = await api.get("/api/admin/reports");
      setReports(res.data);
      return res.data;
    } catch (err) {
      console.error(err);
      setError("Không thể tải danh sách báo cáo.");
      return [];
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchReports();
  }, []);

  // /generate giờ chạy nền (trả response ngay) — poll lại danh sách mỗi
  // POLL_INTERVAL_MS khi còn báo cáo nào đang "generating", tự dừng khi hết.
  useEffect(() => {
    if (!reports.some(r => r.status === "generating")) return;
    const timer = setTimeout(fetchReports, POLL_INTERVAL_MS);
    return () => clearTimeout(timer);
  }, [reports]);

  const handleGenerateToday = async () => {
    setGenerating(true);
    setError("");

    // Báo cáo chạy lúc 7h sáng sẽ lấy dữ liệu của ngày hôm qua (phiên đóng cửa)
    const yesterday = new Date();
    yesterday.setDate(yesterday.getDate() - 1);
    const targetDate = format(yesterday, "yyyy-MM-dd");

    try {
      await api.post(`/api/admin/reports/generate?date=${targetDate}`);
      await fetchReports();
    } catch (err: any) {
      setError(err.response?.data?.detail || "Lỗi khi tạo báo cáo mới");
    } finally {
      setGenerating(false);
    }
  };

  return (
    <div className="space-y-8">
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div>
          <h2 className="text-3xl font-bold tracking-tight text-heading mb-2">Duyệt Báo Cáo</h2>
          <p className="text-body">Tạo bản draft và duyệt để xuất bản cho user xem</p>
        </div>

        <button onClick={handleGenerateToday} disabled={generating} className="btn-pill">
          {generating ? (
            <div className="w-5 h-5 border-2 border-current/30 border-t-current rounded-full animate-spin" />
          ) : (
            <PlusCircle size={20} />
          )}
          Tạo báo cáo hôm nay
        </button>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg flex items-start gap-3">
          <AlertCircle size={20} className="shrink-0 mt-0.5" />
          <p>{error}</p>
        </div>
      )}

      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {[1, 2, 3].map(i => (
            <div key={i} className="bg-surface border border-border rounded-2xl h-32 animate-pulse" />
          ))}
        </div>
      ) : reports.length === 0 ? (
        <div className="text-center py-20 bg-surface border border-border-soft border-dashed rounded-2xl">
          <FileText size={48} className="mx-auto text-muted mb-4" />
          <p className="text-body text-lg">Chưa có báo cáo nào trong hệ thống.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {reports.map((report) => (
            <Link
              key={report.id}
              href={`/admin/reports/${report.report_date}`}
              className="group block bg-background hover:bg-surface border border-border hover:border-primary/30 rounded-2xl p-5 transition-all duration-500 ease-in-out"
            >
              <div className="flex justify-between items-start mb-4">
                <div className="flex items-center gap-3">
                  <div className={clsx("p-2 rounded-lg", STATUS_STYLES[report.status].iconWrap)}>
                    <FileText size={20} />
                  </div>
                  <h3 className="text-lg font-bold text-label">Báo cáo ngày {report.report_date}</h3>
                </div>
                <div className={clsx(
                  "px-2.5 py-1 text-xs font-semibold rounded-full flex items-center gap-1.5",
                  STATUS_STYLES[report.status].badge
                )}>
                  {STATUS_STYLES[report.status].icon}
                  {STATUS_STYLES[report.status].label}
                </div>
              </div>

              {report.status === 'failed' && report.error_message && (
                <p className="text-xs text-down bg-red-50 border border-red-200 rounded-md px-2.5 py-1.5 mb-3 line-clamp-2">
                  {report.error_message}
                </p>
              )}

              <div className="flex justify-between items-center text-sm text-body mt-6 pt-4 border-t border-border">
                <span>Tạo lúc: {format(new Date(report.created_at), "HH:mm dd/MM/yyyy")}</span>
                <span className="flex items-center text-primary opacity-0 group-hover:opacity-100 transition-all -translate-x-2 group-hover:translate-x-0 duration-500 ease-in-out">
                  {report.status === 'draft' ? 'Xem & Duyệt' : 'Xem chi tiết'} <ChevronRight size={16} className="ml-1" />
                </span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
