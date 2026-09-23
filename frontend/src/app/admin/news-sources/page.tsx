"use client";

import { useEffect, useState } from "react";
import {
  Plus, Pencil, Trash2, Save, X, AlertCircle, FlaskConical,
  Loader2, CheckCircle2, XCircle, ExternalLink, BookOpen, ChevronDown,
} from "lucide-react";
import clsx from "clsx";
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
function validateForm(f: SourceForm): string | null {
  if (!f.domain.trim() || !f.name.trim() || !f.category.trim()) {
    return "Vui lòng điền đủ Tên, Domain, Chuyên mục.";
  }
  if (f.source_type === "html" && !f.listing_url.trim()) {
    return "Loại nguồn 'html' bắt buộc phải có Listing URL.";
  }
  if (f.source_type === "rss" && !f.rss_url.trim()) {
    return "Loại nguồn 'rss' bắt buộc phải có RSS URL.";
  }
  if (f.source_type === "bloomberg_rss" && !f.bloomberg_feeds_text.trim()) {
    return "Loại nguồn 'bloomberg_rss' bắt buộc phải có ít nhất 1 Bloomberg feed URL.";
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
  const [testOpen, setTestOpen] = useState(false);
  const [testLoading, setTestLoading] = useState(false);
  const [testError, setTestError] = useState("");
  const [testArticles, setTestArticles] = useState<TestCrawlArticle[] | null>(null);
  const [formError, setFormError] = useState("");

  const set = <K extends keyof SourceForm>(key: K, value: SourceForm[K]) =>
    setForm({ ...form, [key]: value });

  const handleTestCrawl = async () => {
    const err = validateForm(form);
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
            : "Test crawl thất bại — không rõ nguyên nhân."
      );
    } finally {
      setTestLoading(false);
    }
  };

  const handleSaveClick = () => {
    const err = validateForm(form);
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
        <p className={sectionTitleCls}>Thông tin cơ bản</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
          <div>
            <label className={labelCls}>Tên nguồn *</label>
            <input required value={form.name} onChange={(e) => set("name", e.target.value)} className={inputCls} placeholder="vd: EIA – Today in Energy" />
          </div>
          <div>
            <label className={labelCls}>Domain *</label>
            <input required value={form.domain} onChange={(e) => set("domain", e.target.value)} className={inputCls} placeholder="vd: eia.gov" />
          </div>
          <div>
            <label className={labelCls}>Chuyên mục (category) *</label>
            <input required value={form.category} onChange={(e) => set("category", e.target.value)} className={inputCls} placeholder="vd: energy_official" />
          </div>
          <div>
            <label className={labelCls}>Tier *</label>
            <select value={form.tier} onChange={(e) => set("tier", e.target.value as Tier)} className={inputCls}>
              <option value="A">A — Tin cậy cao</option>
              <option value="B">B — Trung bình</option>
              <option value="C">C — Thấp</option>
            </select>
          </div>
          <div>
            <label className={labelCls}>Vùng</label>
            <select value={form.region} onChange={(e) => set("region", e.target.value as Region)} className={inputCls}>
              <option value="international">Quốc tế</option>
              <option value="vietnam">Việt Nam</option>
            </select>
          </div>
          <div className="flex items-end gap-4 pb-1">
            <label className="flex items-center gap-1.5 text-sm">
              <input type="checkbox" checked={form.is_active} onChange={(e) => set("is_active", e.target.checked)} />
              Đang hoạt động
            </label>
            <label className="flex items-center gap-1.5 text-sm">
              <input type="checkbox" checked={form.is_noon_crawl} onChange={(e) => set("is_noon_crawl", e.target.checked)} />
              Chạy lúc 12h
            </label>
          </div>
        </div>
      </div>

      {/* Nhóm 2: Cấu hình Crawl */}
      <div className="pt-5 border-t border-border">
        <p className={sectionTitleCls}>Cấu hình Crawl</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
          <div>
            <label className={labelCls}>Loại nguồn *</label>
            <select value={form.source_type} onChange={(e) => set("source_type", e.target.value as SourceType)} className={inputCls}>
              <option value="html">html (trang danh sách)</option>
              <option value="rss">rss</option>
              <option value="bloomberg_rss">bloomberg_rss</option>
            </select>
          </div>
          <div>
            <label className={labelCls}>Số bài tối đa / lần crawl</label>
            <input type="number" min={1} value={form.max_articles} onChange={(e) => set("max_articles", e.target.value)} className={inputCls} placeholder="mặc định 25" />
          </div>
          <div className="flex items-end pb-1">
            <label className="flex items-center gap-1.5 text-sm">
              <input type="checkbox" checked={form.use_playwright} onChange={(e) => set("use_playwright", e.target.checked)} />
              Dùng Playwright
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
              <label className={labelCls}>Bloomberg Feeds * <span className="font-normal text-muted-light">(1 URL / dòng)</span></label>
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
            Playwright: khuyên dùng cho HTML SPA/Next.js render bằng JS. Sẽ bị bỏ qua nếu nguồn là RSS thuần.
          </p>
        </div>
      </div>

      {/* Nhóm 3: Bộ lọc */}
      <div className="pt-5 border-t border-border">
        <p className={sectionTitleCls}>Bộ lọc</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className={labelCls}>Regex Link bài viết <span className="font-normal text-muted-light">(để trống = dùng heuristic mặc định)</span></label>
            <input value={form.link_pattern} onChange={(e) => set("link_pattern", e.target.value)} className={clsx(inputCls, "font-mono text-xs")} placeholder="vd: /todayinenergy/detail\.php" />
          </div>
          <div>
            <label className={labelCls}>Loại trừ path <span className="font-normal text-muted-light">(1 path / dòng, để trống = mặc định)</span></label>
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
            <label className={labelCls}>Nhóm (group) <span className="font-normal text-muted-light">(vd: 1, 2)</span></label>
            <input value={form.group_text} onChange={(e) => set("group_text", e.target.value)} className={inputCls} placeholder="1, 3" />
          </div>
          <div>
            <label className={labelCls}>Độ tin cậy</label>
            <select value={form.confidence} onChange={(e) => set("confidence", e.target.value)} className={inputCls}>
              <option value="">—</option>
              <option value="high">high</option>
              <option value="medium">medium</option>
              <option value="low">low</option>
            </select>
          </div>
          <div className="sm:col-span-2 md:col-span-3">
            <label className={labelCls}>Ghi chú nội bộ</label>
            <textarea rows={2} value={form.note} onChange={(e) => set("note", e.target.value)} className={clsx(inputCls, "resize-none")} />
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3 pt-2">
        <button type="button" onClick={handleSaveClick} disabled={saving} className="btn-pill py-2 px-5">
          <Save size={16} />
          {saving ? "Đang lưu..." : "Lưu"}
        </button>
        <button
          type="button"
          onClick={handleTestCrawl}
          disabled={testLoading}
          className="flex items-center gap-2 py-2 px-5 rounded-full border border-border text-sm font-semibold text-body hover:bg-surface transition-colors"
        >
          {testLoading ? <Loader2 size={16} className="animate-spin" /> : <FlaskConical size={16} />}
          {testLoading ? "Đang kiểm tra..." : "Kiểm tra cấu hình"}
        </button>
        <button type="button" onClick={onCancel} className="text-body hover:text-primary text-sm font-semibold">Huỷ</button>
      </div>

      {testOpen && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/40" onClick={() => setTestOpen(false)} />
          <div className="relative w-full max-w-lg max-h-[80vh] overflow-y-auto bg-background border border-border rounded-2xl shadow-[var(--shadow-medium)] p-6">
            <button onClick={() => setTestOpen(false)} className="absolute top-4 right-4 text-muted-light hover:text-label transition-colors">
              <X size={18} />
            </button>
            <h3 className="text-lg font-bold text-heading mb-4">Kết quả kiểm tra cấu hình</h3>

            {testLoading && (
              <div className="flex items-center gap-2 text-body text-sm py-6 justify-center">
                <Loader2 size={18} className="animate-spin" />
                Đang crawl thử (có thể mất 5–15 giây)...
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
                  Tìm được {testArticles.length} bài viết
                </div>
                <ul className="space-y-2">
                  {testArticles.map((a, i) => (
                    <li key={i} className="border border-border rounded-lg p-3 text-sm">
                      <p className="font-semibold text-label">{a.title || <span className="italic text-muted-light">(không có tiêu đề)</span>}</p>
                      <a href={a.url} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-primary hover:underline text-xs mt-1 break-all">
                        <ExternalLink size={12} className="shrink-0" />
                        {a.url}
                      </a>
                      <p className="text-muted-light text-xs mt-1">HTML length: {a.html_length.toLocaleString()}</p>
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
// SourceFormPanel thay đổi.
const GUIDE_SECTIONS: { title: string; body: React.ReactNode }[] = [
  {
    title: "Quy trình chuẩn khi thêm 1 nguồn mới",
    body: (
      <ol className="list-decimal list-inside space-y-1.5">
        <li>Điền nhóm <strong>Thông tin cơ bản</strong> (Tên, Domain, Tier, Vùng, Chuyên mục).</li>
        <li>Chọn đúng <strong>Loại nguồn</strong> ở nhóm Cấu hình Crawl rồi điền URL tương ứng.</li>
        <li>Bấm <strong>&quot;Kiểm tra cấu hình&quot;</strong> — bắt buộc làm bước này trước khi Lưu.</li>
        <li>Nếu kết quả test ra link rác/thiếu bài → quay lại chỉnh nhóm <strong>Bộ lọc</strong>, test lại.</li>
        <li>Khi danh sách bài trả về hợp lý → điền thêm <strong>Metadata</strong> (không bắt buộc) rồi bấm <strong>Lưu</strong>.</li>
      </ol>
    ),
  },
  {
    title: "Nhóm 1 — Thông tin cơ bản",
    body: (
      <ul className="space-y-2">
        <li><strong>Tên nguồn:</strong> đặt rõ ràng, phân biệt được với các mục khác cùng domain — vd &quot;EIA – Today in Energy&quot; chứ không chỉ &quot;EIA&quot; (1 domain có thể có nhiều dòng, mỗi dòng 1 trang/chuyên mục riêng). Tên phải duy nhất trong toàn hệ thống.</li>
        <li><strong>Domain:</strong> chỉ phần domain gốc, KHÔNG kèm <code>https://</code> hay <code>www.</code> — vd <code>eia.gov</code>, không phải <code>https://www.eia.gov</code>. Dùng để giới hạn crawl không lạc sang site khác và để giới hạn tốc độ request theo domain.</li>
        <li><strong>Chuyên mục (category):</strong> nhãn nội bộ để phân loại NGUỒN (khác với chủ đề của từng bài viết) — có thể đặt tên tự do, cố gắng tái dùng nhãn đã có (vd <code>energy_official</code>, <code>financial_press</code>, <code>vn_press</code>) để dễ lọc/thống kê sau này.</li>
        <li><strong>Tier:</strong> <strong>A</strong> = cơ quan chính thức/sàn giao dịch (EIA, OPEC, ICE...); <strong>B</strong> = báo chí/tổ chức uy tín; <strong>C</strong> = nguồn tổng hợp, độ tin cậy thấp hơn.</li>
        <li><strong>Vùng:</strong> Quốc tế hay Việt Nam — quyết định bài viết từ nguồn này rơi vào nhóm nào khi lên báo cáo.</li>
        <li><strong>Đang hoạt động:</strong> tắt để tạm dừng crawl nguồn này mà không cần xoá hẳn (giữ lại cấu hình để bật lại sau).</li>
        <li><strong>Chạy lúc 12h:</strong> chỉ bật cho nguồn Tier A thực sự quan trọng cần cập nhật thêm 1 lần vào buổi trưa — đa số nguồn nên để tắt, bật tràn lan sẽ làm đợt crawl 12h chạy lâu không cần thiết.</li>
      </ul>
    ),
  },
  {
    title: "Nhóm 2 — Cấu hình Crawl (chọn đúng Loại nguồn)",
    body: (
      <ul className="space-y-2">
        <li><strong>html:</strong> trang có danh sách bài viết (listing page) nhưng KHÔNG có RSS feed. Bắt buộc điền <strong>Listing URL</strong> — dán đúng URL trang danh sách (không phải trang chủ chung chung nếu có mục tin tức riêng).</li>
        <li><strong>rss:</strong> trang có sẵn RSS/Atom feed. Bắt buộc điền <strong>RSS URL</strong>. Ưu tiên chọn loại này khi nguồn có feed vì ổn định hơn và ít bị chặn hơn scraping HTML.</li>
        <li><strong>bloomberg_rss:</strong> chỉ dùng riêng cho Bloomberg (xử lý nhiều feed + resolve link Google News đặc thù) — bắt buộc điền danh sách <strong>Bloomberg Feeds</strong>, mỗi URL 1 dòng.</li>
        <li><strong>Số bài tối đa/lần crawl:</strong> để trống dùng mặc định (25). Đặt thấp hơn cho site ra bài rất nhiều để đỡ tốn request mỗi lần crawl.</li>
        <li><strong>Dùng Playwright:</strong> chỉ bật khi trang là SPA/React/Next.js cần chạy JS mới hiện nội dung, hoặc khi &quot;Kiểm tra cấu hình&quot; trả về 0 bài dù URL chắc chắn đúng. Không bật mặc định — tốn tài nguyên và chậm hơn nhiều so với fetch thường. Không có tác dụng với nguồn loại RSS.</li>
      </ul>
    ),
  },
  {
    title: "Nhóm 3 — Bộ lọc (chỉ cần khi kết quả test chưa sạch)",
    body: (
      <ul className="space-y-2">
        <li><strong>Regex Link bài viết:</strong> dùng khi bộ lọc mặc định (yêu cầu URL có từ 3 cấp path trở lên) bắt nhầm link không phải bài viết, hoặc bỏ sót bài thật. Viết theo đúng cấu trúc URL bài viết thật của trang — vd <code>/todayinenergy/detail\.php</code> hoặc <code>/pr-detail/</code>. Luôn Kiểm tra cấu hình lại sau khi sửa regex này.</li>
        <li><strong>Loại trừ path:</strong> liệt kê thêm các đoạn path chắc chắn KHÔNG phải bài viết (trang tag/category/author/login/search...) nếu bộ mặc định của hệ thống chưa đủ với trang này. Để trống nếu không có nhu cầu đặc biệt.</li>
      </ul>
    ),
  },
  {
    title: "Nhóm 4 — Metadata (không bắt buộc nhưng nên điền)",
    body: (
      <ul className="space-y-2">
        <li><strong>Nhóm (group):</strong> số nhóm chủ đề nội bộ, dùng để lọc/thống kê sau này — nhập dạng &quot;1, 3&quot;.</li>
        <li><strong>Độ tin cậy:</strong> đánh giá chủ quan mức ổn định của URL — nguồn đánh dấu &quot;low&quot; nên theo dõi log crawl thường xuyên hơn vì khả năng cao site sẽ đổi cấu trúc.</li>
        <li><strong>Ghi chú nội bộ:</strong> ghi lại lý do chọn URL này, các lần từng sửa URL trước đó, hoặc lưu ý đặc biệt — giúp người sau (kể cả chính bạn sau vài tháng) không phải đoán lại từ đầu.</li>
      </ul>
    ),
  },
  {
    title: "Cách đọc kết quả \"Kiểm tra cấu hình\" (Test Crawl)",
    body: (
      <ul className="space-y-2">
        <li>Luôn bấm nút này TRƯỚC khi Lưu — hệ thống sẽ thử crawl thật với cấu hình đang điền (KHÔNG lưu vào DB, không gọi Claude nên không tốn chi phí), trả về tối đa 5 bài tìm được.</li>
        <li>Thấy danh sách bài có tiêu đề/link hợp lý, trỏ đúng vào bài viết → cấu hình đúng, có thể bấm Lưu.</li>
        <li>Báo <strong>&quot;Không crawl được bài nào&quot;</strong>: mở thử Listing URL/RSS URL trực tiếp trên trình duyệt xem có đúng link không; nếu đúng mà vẫn không ra bài, thử bật <strong>Dùng Playwright</strong> (khả năng trang cần JS hoặc chặn bot).</li>
        <li>Test ra bài nhưng link không phải bài viết (link chuyên mục, trang tag...) → quay lại nhóm Bộ lọc, thêm Regex Link bài viết hoặc Loại trừ path rồi test lại.</li>
      </ul>
    ),
  },
  {
    title: "Lỗi thường gặp",
    body: (
      <ul className="space-y-2">
        <li>Chọn Loại nguồn không khớp với URL đang có — vd trang có sẵn RSS nhưng vẫn để loại <code>html</code> và dán RSS URL vào ô Listing URL.</li>
        <li>Đặt Tên nguồn trùng với nguồn đã có — hệ thống yêu cầu Tên là duy nhất, sẽ báo lỗi 409 khi Lưu.</li>
        <li>Nhập Domain kèm <code>https://</code>/<code>www.</code> — chỉ nhập domain gốc.</li>
        <li>Bật &quot;Chạy lúc 12h&quot; cho quá nhiều nguồn không thực sự cần thiết, khiến đợt crawl buổi trưa chạy lâu hơn mức cần.</li>
        <li>Bỏ qua bước &quot;Kiểm tra cấu hình&quot; rồi Lưu luôn — dễ tạo ra nguồn không bao giờ crawl được bài nào mà không hay biết cho tới lần crawl thật.</li>
      </ul>
    ),
  },
];

function NewsSourceGuideModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [openIndex, setOpenIndex] = useState<number | null>(0);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div className="relative w-full max-w-2xl max-h-[85vh] overflow-y-auto bg-background border border-border rounded-2xl shadow-[var(--shadow-medium)] p-6">
        <button onClick={onClose} className="absolute top-4 right-4 text-muted-light hover:text-label transition-colors" title="Đóng">
          <X size={18} />
        </button>

        <div className="flex items-center gap-2 mb-1">
          <BookOpen size={20} className="text-primary" />
          <h2 className="text-lg font-bold text-heading">Hướng dẫn thêm nguồn tin</h2>
        </div>
        <p className="text-sm text-body mb-5">
          Đọc qua trước khi thêm nguồn mới, để cấu hình đúng ngay từ lần đầu và tránh phải sửa lại nhiều lần.
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
                  <span className="font-semibold text-sm text-label">{section.title}</span>
                  <ChevronDown size={16} className={clsx("shrink-0 transition-transform", isOpen && "rotate-180")} />
                </button>
                {isOpen && (
                  <div className="px-4 py-3 text-sm text-body bg-background">{section.body}</div>
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
      setError("Không thể tải danh sách nguồn tin.");
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
      setError(err.response?.data?.detail || "Lỗi khi lưu nguồn tin.");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm("Xoá nguồn tin này? Crawler sẽ không lấy bài từ nguồn này nữa.")) return;
    setError("");
    try {
      await api.delete(`/api/admin/news-sources/${id}`);
      await fetchSources();
    } catch (err: any) {
      setError(err.response?.data?.detail || "Lỗi khi xoá nguồn tin.");
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4">
        <div>
          <h2 className="text-3xl font-bold uppercase tracking-tight text-heading mb-2">Nguồn Tin Tức</h2>
          <p className="text-body">Cấu hình các nguồn để crawl tin tức (thay cho sources.yaml)</p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={() => setShowGuide(true)}
            className="flex items-center gap-2 py-2.5 px-5 rounded-full border border-border text-sm font-semibold text-body hover:bg-surface transition-colors"
          >
            <BookOpen size={16} />
            Hướng dẫn thêm nguồn
          </button>
          {mode === "none" && (
            <button onClick={openAdd} className="btn-pill py-2.5">
              <Plus size={18} />
              Thêm nguồn
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
                  <th className="px-4 py-3 font-semibold">Tên</th>
                  <th className="px-4 py-3 font-semibold">Domain</th>
                  <th className="px-4 py-3 font-semibold">Tier</th>
                  <th className="px-4 py-3 font-semibold">Loại</th>
                  <th className="px-4 py-3 font-semibold">Vùng</th>
                  <th className="px-4 py-3 font-semibold">Trạng thái</th>
                  <th className="px-4 py-3 font-semibold">12h</th>
                  <th className="px-4 py-3 font-semibold text-right">Hành động</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {sources.map((s) => (
                  <tr key={s.id}>
                    <td className="px-4 py-2.5 font-semibold text-label max-w-[220px] truncate" title={s.name}>{s.name}</td>
                    <td className="px-4 py-2.5 font-mono text-muted-light">{s.domain}</td>
                    <td className="px-4 py-2.5">{s.tier}</td>
                    <td className="px-4 py-2.5 font-mono text-xs">{s.source_type}</td>
                    <td className="px-4 py-2.5">{s.region === "vietnam" ? "VN" : "Quốc tế"}</td>
                    <td className="px-4 py-2.5">
                      <span className={clsx(
                        "px-2 py-0.5 rounded-full text-xs font-semibold",
                        s.is_active ? "bg-tint text-primary-dark" : "bg-surface-alt text-muted-light"
                      )}>
                        {s.is_active ? "Active" : "Tắt"}
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
                    <p className="font-mono text-xs text-muted-light">{s.domain} · Tier {s.tier} · {s.source_type}</p>
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
                    {s.is_active ? "Active" : "Tắt"}
                  </span>
                  {s.is_noon_crawl && (
                    <span className="px-2 py-0.5 rounded-full text-xs font-semibold bg-tint text-primary-dark">Chạy 12h</span>
                  )}
                  <span className="text-xs text-muted-light">{s.region === "vietnam" ? "Việt Nam" : "Quốc tế"}</span>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
