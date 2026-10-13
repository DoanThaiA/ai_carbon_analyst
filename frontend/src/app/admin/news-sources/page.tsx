"use client";

import { useEffect, useState } from "react";
import {
  Plus, Pencil, Trash2, Save, X, AlertCircle, FlaskConical,
  Loader2, CheckCircle2, XCircle, ExternalLink, BookOpen, ChevronDown,
} from "lucide-react";
import clsx from "clsx";
import { useTranslations } from "next-intl";
import { api } from "@/lib/api";

type Tier = "A" | "B" | "C";
type Region = "vietnam" | "international";
type SourceType = "html" | "rss" | "bloomberg_rss";

interface NewsSource {
  id: number;
  domain: string;
  name: string;
  category: string;
  tier: Tier;
  region: Region;
  source_type: SourceType;
  listing_url: string | null;
  rss_url: string | null;
  link_pattern: string | null;
  exclude_path_patterns: string[] | null;
  group: number[] | null;
  bloomberg_feeds: string[] | null;
  confidence: string | null;
  note: string | null;
  max_articles: number | null;
  use_playwright: boolean;
  is_active: boolean;
  is_noon_crawl: boolean;
}

// Form dùng string cho các field mảng (textarea/CSV) — chỉ convert sang mảng
// thật lúc build payload gửi API (xem buildPayload).
interface SourceForm {
  domain: string;
  name: string;
  tier: Tier;
  category: string;
  region: Region;
  source_type: SourceType;
  listing_url: string;
  rss_url: string;
  link_pattern: string;
  exclude_path_patterns_text: string; // 1 pattern / dòng
  group_text: string;                 // "1, 2, 3"
  bloomberg_feeds_text: string;       // 1 URL / dòng
  confidence: string;
  note: string;
  max_articles: string;
  use_playwright: boolean;
  is_active: boolean;
  is_noon_crawl: boolean;
}

const EMPTY_FORM: SourceForm = {
  domain: "",
  name: "",
  tier: "B",
  category: "",
  region: "international",
  source_type: "html",
  listing_url: "",
  rss_url: "",
  link_pattern: "",
  exclude_path_patterns_text: "",
  group_text: "",
  bloomberg_feeds_text: "",
  confidence: "",
  note: "",
  max_articles: "",
  use_playwright: false,
  is_active: true,
  is_noon_crawl: false,
};

function sourceToForm(s: NewsSource): SourceForm {
  return {
    domain: s.domain,
    name: s.name,
    tier: s.tier,
    category: s.category,
    region: s.region,
    source_type: s.source_type,
    listing_url: s.listing_url || "",
    rss_url: s.rss_url || "",
    link_pattern: s.link_pattern || "",
    exclude_path_patterns_text: (s.exclude_path_patterns || []).join("\n"),
    group_text: (s.group || []).join(", "),
    bloomberg_feeds_text: (s.bloomberg_feeds || []).join("\n"),
    confidence: s.confidence || "",
    note: s.note || "",
    max_articles: s.max_articles != null ? String(s.max_articles) : "",
    use_playwright: s.use_playwright,
    is_active: s.is_active,
    is_noon_crawl: s.is_noon_crawl,
  };
}

function buildPayload(f: SourceForm) {
  return {
    domain: f.domain.trim(),
    name: f.name.trim(),
    tier: f.tier,
    category: f.category.trim(),
    region: f.region,
    source_type: f.source_type,
    listing_url: f.listing_url.trim() || null,
    rss_url: f.rss_url.trim() || null,
    link_pattern: f.link_pattern.trim() || null,
    exclude_path_patterns: f.exclude_path_patterns_text.trim()
      ? f.exclude_path_patterns_text.split("\n").map((s) => s.trim()).filter(Boolean)
      : null,
    group: f.group_text.trim()
      ? f.group_text.split(",").map((s) => parseInt(s.trim(), 10)).filter((n) => !Number.isNaN(n))
      : [],
    bloomberg_feeds: f.bloomberg_feeds_text.trim()
      ? f.bloomberg_feeds_text.split("\n").map((s) => s.trim()).filter(Boolean)
      : [],
    confidence: f.confidence.trim() || null,
    note: f.note.trim() || null,
    max_articles: f.max_articles.trim() ? parseInt(f.max_articles.trim(), 10) : null,
    use_playwright: f.use_playwright,
    is_active: f.is_active,
    is_noon_crawl: f.is_noon_crawl,
  };
}

// Validate tối thiểu ở FE khớp với model_validator phía BE
// (api/routers/admin_news_sources.py::NewsSourceCrawlConfig) — tránh round-trip
// lỗi 422 cho các trường hợp rõ ràng thiếu field bắt buộc theo loại nguồn.
function validateForm(f: SourceForm, t: (key: string) => string): string | null {
  if (!f.domain.trim() || !f.name.trim() || !f.category.trim()) {
    return t("valRequired");
  }
  if (f.source_type === "html" && !f.listing_url.trim()) {
    return t("valHtml");
  }
  if (f.source_type === "rss" && !f.rss_url.trim()) {
    return t("valRss");
  }
  if (f.source_type === "bloomberg_rss" && !f.bloomberg_feeds_text.trim()) {
    return t("valBloomberg");
  }
  return null;
}

interface TestCrawlArticle {
  title: string | null;
  url: string;
  html_length: number;
}

const inputCls = "w-full px-2.5 py-1.5 border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary";
const labelCls = "block text-xs font-semibold text-label mb-1";
const sectionTitleCls = "text-xs font-bold uppercase tracking-wider text-primary-dark mb-3";

function SourceFormPanel({
  form,
  setForm,
  onCancel,
  onSave,
  saving,
}: {
  form: SourceForm;
  setForm: (f: SourceForm) => void;
  onCancel: () => void;
  onSave: () => void;
  saving: boolean;
}) {
  const t = useTranslations("adminSrcNews");
  const [testOpen, setTestOpen] = useState(false);
  const [testLoading, setTestLoading] = useState(false);
  const [testError, setTestError] = useState("");
  const [testArticles, setTestArticles] = useState<TestCrawlArticle[] | null>(null);
  const [formError, setFormError] = useState("");

  const set = <K extends keyof SourceForm>(key: K, value: SourceForm[K]) =>
    setForm({ ...form, [key]: value });

  const handleTestCrawl = async () => {
    const err = validateForm(form, t);
    if (err) {
      setFormError(err);
      return;
    }
    setFormError("");
    setTestOpen(true);
    setTestLoading(true);
    setTestError("");
    setTestArticles(null);
    try {
      const res = await api.post("/api/admin/news-sources/test-crawl", buildPayload(form));
      setTestArticles(res.data.articles);
    } catch (err: any) {
      const detail = err.response?.data?.detail;
      setTestError(
        typeof detail === "string"
          ? detail
          : Array.isArray(detail)
            ? detail.map((d: any) => d.msg).join("; ")
            : t("testFailed")
      );
    } finally {
      setTestLoading(false);
    }
  };

  const handleSaveClick = () => {
    const err = validateForm(form, t);
    if (err) {
      setFormError(err);
      return;
    }
    setFormError("");
    onSave();
  };

  return (
    <div className="bg-background border border-border rounded-2xl p-5 space-y-6">
      {formError && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-3 py-2 rounded-lg flex items-start gap-2 text-sm">
          <AlertCircle size={16} className="shrink-0 mt-0.5" />
          <p>{formError}</p>
        </div>
      )}

      {/* Nhóm 1: Thông tin cơ bản */}
      <div>
        <p className={sectionTitleCls}>{t("secBasic")}</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
          <div>
            <label className={labelCls}>{t("lblName")}</label>
            <input required value={form.name} onChange={(e) => set("name", e.target.value)} className={inputCls} placeholder={t("phName")} />
          </div>
          <div>
            <label className={labelCls}>{t("lblDomain")}</label>
            <input required value={form.domain} onChange={(e) => set("domain", e.target.value)} className={inputCls} placeholder={t("phDomain")} />
          </div>
          <div>
            <label className={labelCls}>{t("lblCategory")}</label>
            <input required value={form.category} onChange={(e) => set("category", e.target.value)} className={inputCls} placeholder={t("phCategory")} />
          </div>
          <div>
            <label className={labelCls}>{t("lblTier")}</label>
            <select value={form.tier} onChange={(e) => set("tier", e.target.value as Tier)} className={inputCls}>
              <option value="A">{t("tierA")}</option>
              <option value="B">{t("tierB")}</option>
              <option value="C">{t("tierC")}</option>
            </select>
          </div>
          <div>
            <label className={labelCls}>{t("lblRegion")}</label>
            <select value={form.region} onChange={(e) => set("region", e.target.value as Region)} className={inputCls}>
              <option value="international">{t("regionIntl")}</option>
              <option value="vietnam">{t("regionVn")}</option>
            </select>
          </div>
          <div className="flex items-end gap-4 pb-1">
            <label className="flex items-center gap-1.5 text-sm">
              <input type="checkbox" checked={form.is_active} onChange={(e) => set("is_active", e.target.checked)} />
              {t("lblActive")}
            </label>
            <label className="flex items-center gap-1.5 text-sm">
              <input type="checkbox" checked={form.is_noon_crawl} onChange={(e) => set("is_noon_crawl", e.target.checked)} />
              {t("lblNoon")}
            </label>
          </div>
        </div>
      </div>

      {/* Nhóm 2: Cấu hình Crawl */}
      <div className="pt-5 border-t border-border">
        <p className={sectionTitleCls}>{t("secCrawl")}</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
          <div>
            <label className={labelCls}>{t("lblSourceType")}</label>
            <select value={form.source_type} onChange={(e) => set("source_type", e.target.value as SourceType)} className={inputCls}>
              <option value="html">{t("typeHtml")}</option>
              <option value="rss">rss</option>
              <option value="bloomberg_rss">bloomberg_rss</option>
            </select>
          </div>
          <div>
            <label className={labelCls}>{t("lblMaxArticles")}</label>
            <input type="number" min={1} value={form.max_articles} onChange={(e) => set("max_articles", e.target.value)} className={inputCls} placeholder={t("phMaxArticles")} />
          </div>
          <div className="flex items-end pb-1">
            <label className="flex items-center gap-1.5 text-sm">
              <input type="checkbox" checked={form.use_playwright} onChange={(e) => set("use_playwright", e.target.checked)} />
              {t("lblPlaywright")}
            </label>
          </div>

          {form.source_type !== "bloomberg_rss" && (
            <div className="sm:col-span-2 md:col-span-3">
              <label className={labelCls}>
                Listing URL {form.source_type === "html" && "*"}
              </label>
              <input
                required={form.source_type === "html"}
                value={form.listing_url}
                onChange={(e) => set("listing_url", e.target.value)}
                className={inputCls}
                placeholder="https://..."
              />
            </div>
          )}

          {form.source_type === "rss" && (
            <div className="sm:col-span-2 md:col-span-3">
              <label className={labelCls}>RSS URL *</label>
              <input required value={form.rss_url} onChange={(e) => set("rss_url", e.target.value)} className={inputCls} placeholder="https://.../rss" />
            </div>
          )}

          {form.source_type === "bloomberg_rss" && (
            <div className="sm:col-span-2 md:col-span-3">
              <label className={labelCls}>Bloomberg Feeds * <span className="font-normal text-muted-light">{t("hintOneUrlPerLine")}</span></label>
              <textarea
                required
                rows={3}
                value={form.bloomberg_feeds_text}
                onChange={(e) => set("bloomberg_feeds_text", e.target.value)}
                className={clsx(inputCls, "resize-none font-mono text-xs")}
                placeholder={"https://www.bloomberg.com/feeds/markets/news.rss\nhttps://www.bloomberg.com/feeds/politics/news.rss"}
              />
            </div>
          )}

          <p className="sm:col-span-2 md:col-span-3 text-xs text-muted-light -mt-1">
            {t("playwrightHint")}
          </p>
        </div>
      </div>

      {/* Nhóm 3: Bộ lọc */}
      <div className="pt-5 border-t border-border">
        <p className={sectionTitleCls}>{t("secFilter")}</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className={labelCls}>{t("lblLinkPattern")} <span className="font-normal text-muted-light">{t("hintLinkPattern")}</span></label>
            <input value={form.link_pattern} onChange={(e) => set("link_pattern", e.target.value)} className={clsx(inputCls, "font-mono text-xs")} placeholder={t("phLinkPattern")} />
          </div>
          <div>
            <label className={labelCls}>{t("lblExclude")} <span className="font-normal text-muted-light">{t("hintExclude")}</span></label>
            <textarea
              rows={2}
              value={form.exclude_path_patterns_text}
              onChange={(e) => set("exclude_path_patterns_text", e.target.value)}
              className={clsx(inputCls, "resize-none font-mono text-xs")}
              placeholder={"/tag/\n/category/"}
            />
          </div>
        </div>
      </div>

      {/* Nhóm 4: Metadata */}
      <div className="pt-5 border-t border-border">
        <p className={sectionTitleCls}>Metadata</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
          <div>
            <label className={labelCls}>{t("lblGroup")} <span className="font-normal text-muted-light">{t("hintGroup")}</span></label>
            <input value={form.group_text} onChange={(e) => set("group_text", e.target.value)} className={inputCls} placeholder="1, 3" />
          </div>
          <div>
            <label className={labelCls}>{t("lblConfidence")}</label>
            <select value={form.confidence} onChange={(e) => set("confidence", e.target.value)} className={inputCls}>
              <option value="">—</option>
              <option value="high">high</option>
              <option value="medium">medium</option>
              <option value="low">low</option>
            </select>
          </div>
          <div className="sm:col-span-2 md:col-span-3">
            <label className={labelCls}>{t("lblNote")}</label>
            <textarea rows={2} value={form.note} onChange={(e) => set("note", e.target.value)} className={clsx(inputCls, "resize-none")} />
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3 pt-2">
        <button type="button" onClick={handleSaveClick} disabled={saving} className="btn-pill py-2 px-5">
          <Save size={16} />
          {saving ? t("saving") : t("save")}
        </button>
        <button
          type="button"
          onClick={handleTestCrawl}
          disabled={testLoading}
          className="flex items-center gap-2 py-2 px-5 rounded-full border border-border text-sm font-semibold text-body hover:bg-surface transition-colors"
        >
          {testLoading ? <Loader2 size={16} className="animate-spin" /> : <FlaskConical size={16} />}
          {testLoading ? t("testing") : t("testBtn")}
        </button>
        <button type="button" onClick={onCancel} className="text-body hover:text-primary text-sm font-semibold">{t("cancel")}</button>
      </div>

      {testOpen && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/40" onClick={() => setTestOpen(false)} />
          <div className="relative w-full max-w-lg max-h-[80vh] overflow-y-auto bg-background border border-border rounded-2xl shadow-[var(--shadow-medium)] p-6">
            <button onClick={() => setTestOpen(false)} className="absolute top-4 right-4 text-muted-light hover:text-label transition-colors">
              <X size={18} />
            </button>
            <h3 className="text-lg font-bold text-heading mb-4">{t("testResultTitle")}</h3>

            {testLoading && (
              <div className="flex items-center gap-2 text-body text-sm py-6 justify-center">
                <Loader2 size={18} className="animate-spin" />
                {t("testCrawling")}
              </div>
            )}

            {!testLoading && testError && (
              <div className="bg-red-50 border border-red-200 text-red-700 px-3 py-3 rounded-lg flex items-start gap-2 text-sm">
                <XCircle size={18} className="shrink-0 mt-0.5" />
                <p>{testError}</p>
              </div>
            )}

            {!testLoading && !testError && testArticles && (
              <div className="space-y-3">
                <div className="flex items-center gap-2 text-primary-dark text-sm font-semibold">
                  <CheckCircle2 size={18} />
                  {t("testFound", { count: testArticles.length })}
                </div>
                <ul className="space-y-2">
                  {testArticles.map((a, i) => (
                    <li key={i} className="border border-border rounded-lg p-3 text-sm">
                      <p className="font-semibold text-label">{a.title || <span className="italic text-muted-light">{t("noTitle")}</span>}</p>
                      <a href={a.url} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-primary hover:underline text-xs mt-1 break-all">
                        <ExternalLink size={12} className="shrink-0" />
                        {a.url}
                      </a>
                      <p className="text-muted-light text-xs mt-1">{t("htmlLength", { n: a.html_length.toLocaleString() })}</p>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// Nội dung hướng dẫn thêm nguồn — mở dạng modal từ nút "Hướng dẫn thêm nguồn"
// trên trang, giúp admin (không nhất thiết là dev) tự điền form đúng mà không
// cần hỏi lại. Cập nhật nội dung này song song mỗi khi field/validation của
// SourceFormPanel thay đổi. Chữ nằm trong messages (adminSrcNews: guide_<key>_title / guide_<key>_i<k>).
const GUIDE_SECTIONS: { key: string; ordered?: boolean; items: number }[] = [
  { key: "s1", ordered: true, items: 5 },
  { key: "s2", items: 7 },
  { key: "s3", items: 5 },
  { key: "s4", items: 2 },
  { key: "s5", items: 3 },
  { key: "s6", items: 4 },
  { key: "s7", items: 5 },
];

const GUIDE_RICH = {
  b: (c: React.ReactNode) => <strong>{c}</strong>,
  code: (c: React.ReactNode) => <code>{c}</code>,
};

function NewsSourceGuideModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const t = useTranslations("adminSrcNews");
  const [openIndex, setOpenIndex] = useState<number | null>(0);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div className="relative w-full max-w-2xl max-h-[85vh] overflow-y-auto bg-background border border-border rounded-2xl shadow-[var(--shadow-medium)] p-6">
        <button onClick={onClose} className="absolute top-4 right-4 text-muted-light hover:text-label transition-colors" title={t("close")}>
          <X size={18} />
        </button>

        <div className="flex items-center gap-2 mb-1">
          <BookOpen size={20} className="text-primary" />
          <h2 className="text-lg font-bold text-heading">{t("guideTitle")}</h2>
        </div>
        <p className="text-sm text-body mb-5">
          {t("guideIntro")}
        </p>

        <div className="space-y-2">
          {GUIDE_SECTIONS.map((section, i) => {
            const isOpen = openIndex === i;
            return (
              <div key={i} className="border border-border rounded-xl overflow-hidden">
                <button
                  type="button"
                  onClick={() => setOpenIndex(isOpen ? null : i)}
                  className="w-full flex items-center justify-between gap-3 px-4 py-3 text-left bg-surface hover:bg-surface-alt transition-colors"
                >
                  <span className="font-semibold text-sm text-label">{t(`guide_${section.key}_title`)}</span>
                  <ChevronDown size={16} className={clsx("shrink-0 transition-transform", isOpen && "rotate-180")} />
                </button>
                {isOpen && (
                  <div className="px-4 py-3 text-sm text-body bg-background">{(() => {
                      const items = Array.from({ length: section.items }, (_, k) => (
                        <li key={k}>{t.rich(`guide_${section.key}_i${k + 1}`, GUIDE_RICH)}</li>
                      ));
                      return section.ordered
                        ? <ol className="list-decimal list-inside space-y-1.5">{items}</ol>
                        : <ul className="space-y-2">{items}</ul>;
                    })()}</div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

export default function NewsSourcesPage() {
  const t = useTranslations("adminSrcNews");
  const [sources, setSources] = useState<NewsSource[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const [mode, setMode] = useState<"none" | "add" | number>("none");
  const [form, setForm] = useState<SourceForm>(EMPTY_FORM);
  const [showGuide, setShowGuide] = useState(false);

  const fetchSources = async () => {
    try {
      const res = await api.get("/api/admin/news-sources");
      setSources(res.data);
    } catch {
      setError(t("loadError"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchSources();
  }, []);

  const openAdd = () => {
    setForm(EMPTY_FORM);
    setMode("add");
  };

  const openEdit = (s: NewsSource) => {
    setForm(sourceToForm(s));
    setMode(s.id);
  };

  const closeForm = () => setMode("none");

  const handleSave = async () => {
    setError("");
    setSaving(true);
    try {
      const payload = buildPayload(form);
      if (mode === "add") {
        await api.post("/api/admin/news-sources", payload);
      } else if (typeof mode === "number") {
        await api.put(`/api/admin/news-sources/${mode}`, payload);
      }
      setMode("none");
      await fetchSources();
    } catch (err: any) {
      setError(err.response?.data?.detail || t("saveError"));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm(t("deleteConfirm"))) return;
    setError("");
    try {
      await api.delete(`/api/admin/news-sources/${id}`);
      await fetchSources();
    } catch (err: any) {
      setError(err.response?.data?.detail || t("deleteError"));
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4">
        <div>
          <h2 className="text-3xl font-bold uppercase tracking-tight text-heading mb-2">{t("title")}</h2>
          <p className="text-body">{t("subtitle")}</p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={() => setShowGuide(true)}
            className="flex items-center gap-2 py-2.5 px-5 rounded-full border border-border text-sm font-semibold text-body hover:bg-surface transition-colors"
          >
            <BookOpen size={16} />
            {t("guideBtn")}
          </button>
          {mode === "none" && (
            <button onClick={openAdd} className="btn-pill py-2.5">
              <Plus size={18} />
              {t("add")}
            </button>
          )}
        </div>
      </div>

      <NewsSourceGuideModal open={showGuide} onClose={() => setShowGuide(false)} />

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg flex items-start gap-3">
          <AlertCircle size={20} className="shrink-0 mt-0.5" />
          <p>{error}</p>
        </div>
      )}

      {mode !== "none" && (
        <SourceFormPanel form={form} setForm={setForm} onCancel={closeForm} onSave={handleSave} saving={saving} />
      )}

      {loading ? (
        <div className="bg-background border border-border rounded-2xl h-40 animate-pulse" />
      ) : (
        <>
          {/* Bảng đầy đủ — chỉ hiện từ md trở lên. */}
          <div className="hidden md:block bg-background border border-border rounded-2xl overflow-hidden overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-muted-light text-xs uppercase tracking-wider">
                  <th className="px-4 py-3 font-semibold">{t("colName")}</th>
                  <th className="px-4 py-3 font-semibold">Domain</th>
                  <th className="px-4 py-3 font-semibold">Tier</th>
                  <th className="px-4 py-3 font-semibold">{t("colType")}</th>
                  <th className="px-4 py-3 font-semibold">{t("lblRegion")}</th>
                  <th className="px-4 py-3 font-semibold">{t("colStatus")}</th>
                  <th className="px-4 py-3 font-semibold">{t("col12h")}</th>
                  <th className="px-4 py-3 font-semibold text-right">{t("colActions")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {sources.map((s) => (
                  <tr key={s.id}>
                    <td className="px-4 py-2.5 font-semibold text-label max-w-[220px] truncate" title={s.name}>{s.name}</td>
                    <td className="px-4 py-2.5 font-mono text-muted-light">{s.domain}</td>
                    <td className="px-4 py-2.5">{s.tier}</td>
                    <td className="px-4 py-2.5 font-mono text-xs">{s.source_type}</td>
                    <td className="px-4 py-2.5">{s.region === "vietnam" ? t("regionVnShort") : t("regionIntl")}</td>
                    <td className="px-4 py-2.5">
                      <span className={clsx(
                        "px-2 py-0.5 rounded-full text-xs font-semibold",
                        s.is_active ? "bg-tint text-primary-dark" : "bg-surface-alt text-muted-light"
                      )}>
                        {s.is_active ? t("active") : t("off")}
                      </span>
                    </td>
                    <td className="px-4 py-2.5">{s.is_noon_crawl ? "✓" : ""}</td>
                    <td className="px-4 py-2.5">
                      <div className="flex items-center justify-end gap-2">
                        <button onClick={() => openEdit(s)} className="p-1.5 rounded hover:bg-surface text-body"><Pencil size={16} /></button>
                        <button onClick={() => handleDelete(s.id)} className="p-1.5 rounded hover:bg-red-50 text-down"><Trash2 size={16} /></button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Dạng card — chỉ hiện dưới md. */}
          <div className="md:hidden space-y-3">
            {sources.map((s) => (
              <div key={s.id} className="bg-background border border-border rounded-2xl p-4 space-y-2.5">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="font-semibold text-label truncate">{s.name}</p>
                    <p className="font-mono text-xs text-muted-light">{s.domain} · {t("tierShort", { tier: s.tier })} · {s.source_type}</p>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <button onClick={() => openEdit(s)} className="p-1.5 rounded hover:bg-surface text-body"><Pencil size={16} /></button>
                    <button onClick={() => handleDelete(s.id)} className="p-1.5 rounded hover:bg-red-50 text-down"><Trash2 size={16} /></button>
                  </div>
                </div>
                <div className="flex items-center gap-2 flex-wrap">
                  <span className={clsx(
                    "px-2 py-0.5 rounded-full text-xs font-semibold",
                    s.is_active ? "bg-tint text-primary-dark" : "bg-surface-alt text-muted-light"
                  )}>
                    {s.is_active ? t("active") : t("off")}
                  </span>
                  {s.is_noon_crawl && (
                    <span className="px-2 py-0.5 rounded-full text-xs font-semibold bg-tint text-primary-dark">{t("noonShort")}</span>
                  )}
                  <span className="text-xs text-muted-light">{s.region === "vietnam" ? t("regionVn") : t("regionIntl")}</span>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
