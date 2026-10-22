"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Loader2, Search } from "lucide-react";
import { api } from "@/lib/api";
import { KNOWLEDGE_SECTIONS, type KnowledgeSearchHit } from "@/lib/types";
import { apiErrorDetail } from "@/lib/knowledgeUpload";

const inputCls = "px-2.5 py-1.5 border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary";

function pageText(h: KnowledgeSearchHit): string {
  if (!h.page_from) return "";
  return h.page_to && h.page_to !== h.page_from ? `${h.page_from}–${h.page_to}` : String(h.page_from);
}

/** Thử truy hồi giống hệt lúc Jenny dùng (POST /api/admin/knowledge/search-test — cùng ngưỡng rerank, cùng lọc
 * phạm vi/hiệu lực). `docId`: chỉ tìm trong 1 tài liệu (trang chi tiết). */
export function KnowledgeSearchPanel({ docId }: { docId?: number }) {
  const t = useTranslations("adminKnowledge");
  const [query, setQuery] = useState("");
  const [purpose, setPurpose] = useState<"chat" | "report">("chat");
  const [section, setSection] = useState("3");
  const [hits, setHits] = useState<KnowledgeSearchHit[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const run = async () => {
    if (!query.trim()) return;
    setLoading(true);
    setError("");
    try {
      const res = await api.post("/api/admin/knowledge/search-test", {
        query: query.trim(), purpose, section: purpose === "report" ? section : null, doc_id: docId ?? null,
      });
      setHits(res.data);
    } catch (err) {
      setError(apiErrorDetail(err, t("searchError")));
      setHits(null);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-3">
      <form
        onSubmit={(e) => { e.preventDefault(); run(); }}
        className="flex flex-wrap items-center gap-2"
      >
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("searchPlaceholder")}
          className={`${inputCls} flex-1 min-w-[220px]`}
          maxLength={1000}
        />
        <select value={purpose} onChange={(e) => setPurpose(e.target.value as "chat" | "report")} className={inputCls}>
          <option value="chat">{t("purposeChat")}</option>
          <option value="report">{t("purposeReport")}</option>
        </select>
        {purpose === "report" && (
          <select value={section} onChange={(e) => setSection(e.target.value)} className={inputCls}>
            {KNOWLEDGE_SECTIONS.map((s) => <option key={s} value={s}>{t(`section_${s}`)}</option>)}
          </select>
        )}
        <button type="submit" disabled={loading || !query.trim()} className="btn-pill py-1.5 px-4">
          {loading ? <Loader2 size={15} className="animate-spin" /> : <Search size={15} />}
          {t("searchBtn")}
        </button>
      </form>

      {error && <p className="text-sm text-down">{error}</p>}
      {hits && hits.length === 0 && <p className="text-sm text-muted-light">{t("searchEmpty")}</p>}
      {hits && hits.length > 0 && (
        <ol className="space-y-2">
          {hits.map((h, i) => (
            <li key={h.chunk_id} className="rounded-xl border border-border-soft bg-surface/60 p-3">
              <p className="text-[12px] text-muted-light mb-1">
                <span className="font-semibold text-label">{i + 1}. {h.title}</span>
                {pageText(h) && ` · ${t("pageShort", { pages: pageText(h) })}`}
                {h.heading && ` · ${h.heading}`}
                {" · "}{t("score", { score: h.score.toFixed(2) })}
              </p>
              <p className="text-[13px] leading-relaxed text-body whitespace-pre-wrap line-clamp-6">{h.content}</p>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
