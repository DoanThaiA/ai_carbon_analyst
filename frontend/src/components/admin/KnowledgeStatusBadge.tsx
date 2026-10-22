"use client";

import clsx from "clsx";
import { useTranslations } from "next-intl";
import { Loader2 } from "lucide-react";
import type { KnowledgeDocument } from "@/lib/types";

export function KnowledgeStatusBadge({ doc }: { doc: KnowledgeDocument }) {
  const t = useTranslations("adminKnowledge");
  const busy = doc.status === "pending" || doc.status === "processing";
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1",
        doc.status === "ready" && (doc.is_active ? "bg-emerald-50 text-emerald-700 ring-emerald-100" : "bg-surface text-muted-light ring-border"),
        busy && "bg-blue-50 text-blue-700 ring-blue-100",
        doc.status === "failed" && "bg-red-50 text-red-700 ring-red-100",
      )}
    >
      {busy && <Loader2 size={11} className="animate-spin" />}
      {doc.status === "ready" ? (doc.is_active ? t("status_active") : t("status_inactive")) : t(`status_${doc.status}`)}
    </span>
  );
}

export function formatBytes(n: number): string {
  return n >= 1024 * 1024 ? `${(n / (1024 * 1024)).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;
}
