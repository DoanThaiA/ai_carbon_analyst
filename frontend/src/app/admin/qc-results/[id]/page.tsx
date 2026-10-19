"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { ArrowLeft, FileText } from "lucide-react";
import { api } from "@/lib/api";
import type { QcReviewDetail } from "@/lib/types";
import { QcReviewView } from "@/components/QcReview";

export default function AdminQcResultDetailPage() {
  const t = useTranslations("adminQcReviews");
  const params = useParams();
  const id = params.id as string;
  const [review, setReview] = useState<QcReviewDetail | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .get(`/api/admin/qc-reviews/${id}`)
      .then((r) => setReview(r.data))
      .catch((err) => setError(err.response?.status === 404 ? t("notFound") : t("loadError")));
  }, [id, t]);

  return (
    <div className="max-w-4xl space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Link href="/admin/qc-results" className="flex items-center text-body hover:text-primary text-sm">
          <ArrowLeft size={16} className="mr-2" />{t("backToList")}
        </Link>
        {review && (
          <Link href={`/admin/reports/${review.report_date}`} className="inline-flex items-center gap-1.5 text-sm font-semibold text-primary hover:text-primary-dark">
            <FileText size={14} />{t("openReport", { date: review.report_date })}
          </Link>
        )}
      </div>
      {error ? (
        <p className="text-sm text-down">{error}</p>
      ) : !review ? (
        <p className="text-sm text-muted-light">…</p>
      ) : (
        <section className="bg-background border border-border rounded-2xl shadow-[var(--shadow-soft)] p-5">
          <h1 className="text-xl font-bold text-heading mb-4">{t("detailTitle", { date: review.report_date, run: review.run_no })}</h1>
          <QcReviewView review={review} onChange={setReview} />
        </section>
      )}
    </div>
  );
}
