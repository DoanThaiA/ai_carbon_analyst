"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, AlertCircle, CheckCircle2, Download } from "lucide-react";
import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import { api } from "@/lib/api";
import type { Report } from "@/lib/types";
import { ReportDocument } from "@/components/ReportDocument";
import { QuoteChat } from "@/components/QuoteChat";
import { EnglishFallbackNotice } from "@/components/EnglishFallbackNotice";
import { PdfExportModal } from "@/components/PdfExportModal";
import { localizeReport } from "@/lib/reportLocale";

export default function ReportDetail() {
  const t = useTranslations("userReport");
  const locale = useLocale();
  const params = useParams();
  const router = useRouter();
  const date = params.date as string;

  const [report, setReport] = useState<Report | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showPdfExport, setShowPdfExport] = useState(false);

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
          setError(t("notFound"));
        } else {
          setError(t("serverError"));
        }
      } finally {
        setLoading(false);
      }
    };

    if (date) fetchReport();
  }, [date, router, t]);

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
        <Link href="/dashboard" className="text-primary hover:text-primary-dark font-semibold">
          &larr; {t("backHome")}
        </Link>
      </div>
    );
  }

  // Giao diện EN → hiển thị content_en (nếu đã dịch); không có thì giữ tiếng Việt + thông báo.
  const { report: shown, fallback } = localizeReport(report, locale);

  return (
    <div>
      <div className="flex items-center justify-between mb-6 print:hidden">
        <Link href="/dashboard" className="flex items-center text-body hover:text-primary transition-colors duration-300 ease-in-out text-sm">
          <ArrowLeft size={16} className="mr-2" />
          {t("backDashboard")}
        </Link>
        <div className="flex items-center gap-3">
          <button
            onClick={() => setShowPdfExport(true)}
            className="flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-full border border-border-soft text-body hover:border-primary hover:text-primary-dark transition-colors"
          >
            <Download size={14} />
            {t("downloadPdf")}
          </button>
          <div className="px-2.5 py-1 text-xs font-mono font-semibold rounded flex items-center gap-2 bg-tint text-primary-dark border border-primary/20">
            <CheckCircle2 size={14} />
            PUBLISHED
          </div>
        </div>
      </div>

      {fallback && <EnglishFallbackNotice />}
      <QuoteChat reportDate={date}>
        <ReportDocument report={shown} />
      </QuoteChat>
      {showPdfExport && <PdfExportModal date={date} onClose={() => setShowPdfExport(false)} />}
    </div>
  );
}
