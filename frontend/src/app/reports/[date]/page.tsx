"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, AlertCircle, CheckCircle2 } from "lucide-react";
import Link from "next/link";
import { api } from "@/lib/api";
import type { Report } from "@/lib/types";
import { ReportDocument } from "@/components/ReportDocument";
import { QuoteChat } from "@/components/QuoteChat";

export default function ReportDetail() {
  const params = useParams();
  const router = useRouter();
  const date = params.date as string;

  const [report, setReport] = useState<Report | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const fetchReport = async () => {
      try {
        const res = await api.get(`/api/reports/${date}`);
        setReport(res.data);
      } catch (err: any) {
        if (err.response?.status === 401) {
          router.replace("/login");
          return;
        }
        if (err.response?.status === 404) {
          setError("Không tìm thấy báo cáo cho ngày này.");
        } else {
          setError("Lỗi kết nối đến server.");
        }
      } finally {
        setLoading(false);
      }
    };

    if (date) fetchReport();
  }, [date, router]);

  if (loading) {
    return (
      <div className="flex flex-col space-y-6 animate-pulse max-w-[1080px] mx-auto">
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
        <Link href="/" className="text-primary hover:text-primary-dark font-semibold">
          &larr; Quay lại trang chủ
        </Link>
      </div>
    );
  }

  return (
    <div className="max-w-[1080px] mx-auto px-4 sm:px-6">
      <div className="flex items-center justify-between mb-6">
        <Link href="/" className="flex items-center text-body hover:text-primary transition-colors duration-300 ease-in-out text-sm">
          <ArrowLeft size={16} className="mr-2" />
          Quay lại Dashboard
        </Link>
        <div className="px-2.5 py-1 text-xs font-mono font-semibold rounded flex items-center gap-2 bg-tint text-primary-dark border border-primary/20">
          <CheckCircle2 size={14} />
          PUBLISHED
        </div>
      </div>

      <QuoteChat reportDate={date}>
        <ReportDocument report={report} />
      </QuoteChat>
    </div>
  );
}
