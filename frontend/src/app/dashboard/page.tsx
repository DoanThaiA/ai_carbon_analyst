"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { FileText, CheckCircle2, ChevronRight, AlertCircle } from "lucide-react";
import { format } from "date-fns";
import { api } from "@/lib/api";
import type { ReportSummary } from "@/lib/types";

export default function Dashboard() {
  const router = useRouter();
  const [reports, setReports] = useState<ReportSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const fetchReports = async () => {
      try {
        const res = await api.get("/api/reports");
        setReports(res.data);
      } catch (err: any) {
        if (err.response?.status === 401) {
          router.replace("/login");
          return;
        }
        console.error(err);
        setError("Không thể tải danh sách báo cáo. Backend đã chạy chưa?");
      } finally {
        setLoading(false);
      }
    };

    fetchReports();
  }, [router]);

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-3xl font-bold tracking-tight text-heading mb-2">Báo Cáo Hàng Ngày</h2>
        <p className="text-body">Danh sách các báo cáo Daily Carbon Intelligence đã được duyệt</p>
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
            <div key={i} className="bg-background border border-border rounded-2xl h-32 animate-pulse" />
          ))}
        </div>
      ) : reports.length === 0 ? (
        <div className="text-center py-20 bg-background border border-border-soft border-dashed rounded-2xl">
          <FileText size={48} className="mx-auto text-muted mb-4" />
          <p className="text-body text-lg">Chưa có báo cáo nào được duyệt.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {reports.map((report) => (
            <Link
              key={report.id}
              href={`/reports/${report.report_date}`}
              className="group block bg-background border border-border hover:border-primary/30 rounded-2xl p-5 transition-all duration-500 ease-in-out hover:shadow-[var(--shadow-soft)]"
            >
              <div className="flex justify-between items-start mb-4">
                <div className="flex items-center gap-3">
                  <div className="p-2 rounded-lg bg-tint text-primary-dark">
                    <FileText size={20} />
                  </div>
                  <div>
                    <h3 className="text-lg font-bold text-label">Báo cáo ngày {report.report_date}</h3>
                  </div>
                </div>
                <div className="px-2.5 py-1 text-xs font-semibold rounded-full flex items-center gap-1.5 bg-tint text-primary-dark border border-primary/20">
                  <CheckCircle2 size={14} /> Published
                </div>
              </div>

              <div className="flex justify-between items-center text-sm text-body mt-6 pt-4 border-t border-border">
                <span>Duyệt lúc: {report.published_at ? format(new Date(report.published_at), "HH:mm dd/MM/yyyy") : "-"}</span>
                <span className="flex items-center text-primary opacity-0 group-hover:opacity-100 transition-all -translate-x-2 group-hover:translate-x-0 duration-500 ease-in-out">
                  Xem chi tiết <ChevronRight size={16} className="ml-1" />
                </span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
