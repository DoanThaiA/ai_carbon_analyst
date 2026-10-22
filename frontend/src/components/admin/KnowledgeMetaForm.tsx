"use client";

import { useTranslations } from "next-intl";
import { KNOWLEDGE_SECTIONS, type KnowledgeDocKind, type KnowledgeDocument } from "@/lib/types";

export const KNOWLEDGE_DOC_KINDS: KnowledgeDocKind[] = ["regulation", "research", "market_report", "internal", "reference"];
// Mặc định gợi ý khi tải lên — khớp services/knowledge.py::DEFAULT_REPORT_SECTIONS.
export const DEFAULT_REPORT_SECTIONS = ["3", "4", "biz"];
// Mục chỉ dùng tài liệu làm bối cảnh (không trích) — khớp services/knowledge.py::CITABLE_REPORT_SECTIONS.
const BACKGROUND_ONLY_SECTIONS = new Set(["2", "dev", "8"]);

export interface KnowledgeMeta {
  title: string;
  publisher: string;
  issued_on: string;
  doc_kind: KnowledgeDocKind;
  language: "vi" | "en";
  use_in_report: boolean;
  report_sections: string[];
  use_in_chat: boolean;
  valid_from: string;
  valid_to: string;
  verified: boolean;
  note: string;
}

export function emptyKnowledgeMeta(): KnowledgeMeta {
  return {
    title: "", publisher: "", issued_on: "", doc_kind: "reference", language: "vi", use_in_report: true,
    report_sections: [...DEFAULT_REPORT_SECTIONS], use_in_chat: true, valid_from: "", valid_to: "", verified: false, note: "",
  };
}

export function metaFromDocument(doc: KnowledgeDocument): KnowledgeMeta {
  return {
    title: doc.title, publisher: doc.publisher ?? "", issued_on: doc.issued_on ?? "", doc_kind: doc.doc_kind,
    language: doc.language, use_in_report: doc.use_in_report, report_sections: doc.report_sections ?? [],
    use_in_chat: doc.use_in_chat, valid_from: doc.valid_from ?? "", valid_to: doc.valid_to ?? "",
    verified: doc.verified, note: doc.note ?? "",
  };
}

/** Payload cho POST/PATCH: chuỗi rỗng → null; không chọn mục nào → null (= mọi mục). valid_from trống khi tạo
 * mới → bỏ (DB mặc định = hôm nay). */
export function metaPayload(meta: KnowledgeMeta) {
  return {
    title: meta.title.trim(),
    publisher: meta.publisher.trim() || null,
    issued_on: meta.issued_on || null,
    doc_kind: meta.doc_kind,
    language: meta.language,
    use_in_report: meta.use_in_report,
    report_sections: meta.report_sections.length ? meta.report_sections : null,
    use_in_chat: meta.use_in_chat,
    ...(meta.valid_from ? { valid_from: meta.valid_from } : {}),
    valid_to: meta.valid_to || null,
    verified: meta.verified,
    note: meta.note.trim() || null,
  };
}

const inputCls = "w-full px-2.5 py-1.5 border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary";
const labelCls = "block text-xs font-semibold text-label mb-1";
const checkCls = "h-4 w-4 accent-[var(--color-primary)]";

export function KnowledgeMetaForm({ value, onChange }: { value: KnowledgeMeta; onChange: (next: KnowledgeMeta) => void }) {
  const t = useTranslations("adminKnowledge");
  const set = <K extends keyof KnowledgeMeta>(key: K, v: KnowledgeMeta[K]) => onChange({ ...value, [key]: v });
  const toggleSection = (s: string) =>
    set("report_sections", value.report_sections.includes(s) ? value.report_sections.filter((x) => x !== s) : [...value.report_sections, s]);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div className="sm:col-span-2">
          <label className={labelCls}>{t("lblTitle")} *</label>
          <input required maxLength={300} value={value.title} onChange={(e) => set("title", e.target.value)} className={inputCls} placeholder={t("phTitle")} />
          <p className="text-[11px] text-muted-light mt-1">{t("hintTitle")}</p>
        </div>
        <div>
          <label className={labelCls}>{t("lblPublisher")}</label>
          <input maxLength={200} value={value.publisher} onChange={(e) => set("publisher", e.target.value)} className={inputCls} placeholder={t("phPublisher")} />
        </div>
        <div>
          <label className={labelCls}>{t("lblIssuedOn")}</label>
          <input type="date" value={value.issued_on} onChange={(e) => set("issued_on", e.target.value)} className={inputCls} />
        </div>
        <div>
          <label className={labelCls}>{t("lblKind")}</label>
          <select value={value.doc_kind} onChange={(e) => set("doc_kind", e.target.value as KnowledgeDocKind)} className={inputCls}>
            {KNOWLEDGE_DOC_KINDS.map((k) => <option key={k} value={k}>{t(`kind_${k}`)}</option>)}
          </select>
        </div>
        <div>
          <label className={labelCls}>{t("lblLanguage")}</label>
          <select value={value.language} onChange={(e) => set("language", e.target.value as "vi" | "en")} className={inputCls}>
            <option value="vi">{t("langVi")}</option>
            <option value="en">{t("langEn")}</option>
          </select>
        </div>
        <div>
          <label className={labelCls}>{t("lblValidFrom")}</label>
          <input type="date" value={value.valid_from} onChange={(e) => set("valid_from", e.target.value)} className={inputCls} />
          <p className="text-[11px] text-muted-light mt-1">{t("hintValidFrom")}</p>
        </div>
        <div>
          <label className={labelCls}>{t("lblValidTo")}</label>
          <input type="date" value={value.valid_to} onChange={(e) => set("valid_to", e.target.value)} className={inputCls} />
          <p className="text-[11px] text-muted-light mt-1">{t("hintValidTo")}</p>
        </div>
      </div>

      <div className="rounded-xl border border-border-soft bg-surface/60 p-3 space-y-3">
        <label className="flex items-center gap-2 text-sm font-semibold text-label">
          <input type="checkbox" className={checkCls} checked={value.use_in_report} onChange={(e) => set("use_in_report", e.target.checked)} />
          {t("lblUseInReport")}
        </label>
        {value.use_in_report && (
          <div className="pl-6 space-y-1.5">
            <div className="flex flex-wrap gap-x-4 gap-y-1.5">
              {KNOWLEDGE_SECTIONS.map((s) => (
                <label key={s} className="flex items-center gap-1.5 text-[13px] text-body">
                  <input type="checkbox" className={checkCls} checked={value.report_sections.includes(s)} onChange={() => toggleSection(s)} />
                  {t(`section_${s}`)}
                  {BACKGROUND_ONLY_SECTIONS.has(s) && <span className="text-[11px] text-muted-light">({t("backgroundOnly")})</span>}
                </label>
              ))}
            </div>
            <p className="text-[11px] text-muted-light">
              {value.report_sections.length ? t("hintSections") : t("hintAllSections")}
            </p>
          </div>
        )}
        <label className="flex items-center gap-2 text-sm font-semibold text-label">
          <input type="checkbox" className={checkCls} checked={value.use_in_chat} onChange={(e) => set("use_in_chat", e.target.checked)} />
          {t("lblUseInChat")}
        </label>
        <p className="pl-6 text-[11px] text-muted-light -mt-2">{t("hintUseInChat")}</p>
        <label className="flex items-center gap-2 text-sm font-semibold text-label">
          <input type="checkbox" className={checkCls} checked={value.verified} onChange={(e) => set("verified", e.target.checked)} />
          {t("lblVerified")}
        </label>
        <p className="pl-6 text-[11px] text-muted-light -mt-2">{t("hintVerified")}</p>
      </div>

      <div>
        <label className={labelCls}>{t("lblNote")}</label>
        <textarea maxLength={2000} rows={2} value={value.note} onChange={(e) => set("note", e.target.value)} className={inputCls} />
      </div>
    </div>
  );
}
