"use client";

import { Fragment, useState } from "react";
import clsx from "clsx";
import {
  Clock, CalendarRange, Compass, TrendingUp, TrendingDown, Minus, Target, AlertTriangle,
  Sparkles, LineChart, BarChart3, Newspaper, Link2,
  Crosshair, ChevronDown, ChevronUp, Info, ShieldCheck, ExternalLink, Menu, X, Trash2,
} from "lucide-react";
import type { Report } from "@/lib/types";

// "YYYY-MM-DD" -> "dd/MM" (nhãn trục X) — tránh phụ thuộc date-fns chỉ cho 1 format đơn giản.
function formatShortDate(dateStr: string) {
  const parts = dateStr.split("-");
  return parts.length === 3 ? `${parts[2]}/${parts[1]}` : dateStr;
}

// Backend luôn trả 4 số thập phân (vd "72.4000") — rút gọn còn 2 số khi hiển thị
// trong bảng giá để chuỗi số ngắn lại, đủ nằm gọn trong cột hẹp trên mobile mà
// không cần ngắt dòng giữa chừng. Không đổi dữ liệu gốc, chỉ rút gọn phần hiển thị.
function formatCompactPriceNumber(numStr: string): string {
  const n = Number(numStr.replace(/,/g, ""));
  if (Number.isNaN(n)) return numStr;
  return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// Các mục trong menu hamburger điều hướng (id khớp với <section id> trong báo cáo).
const REPORT_SECTIONS = [
  { id: "section-highlights", label: "Tóm tắt điều hành" },
  { id: "section-market", label: "Diễn biến thị trường" },
  { id: "section-analysis", label: "Phân tích & Giao dịch" },
  { id: "section-news", label: "Tin tức chi tiết" },
  { id: "section-sources", label: "Nguồn tham khảo" },
];

function formatFullDate(dateStr: string) {
  const parts = dateStr.split("-");
  return parts.length === 3 ? `${parts[2]}/${parts[1]}/${parts[0]}` : dateStr;
}

const HORIZON_META: Record<string, { icon: typeof Clock; accent: string; iconBg: string }> = {
  "ngắn hạn": { icon: Clock, accent: "border-l-warn", iconBg: "bg-warn-tint text-warn" },
  "trung hạn": { icon: CalendarRange, accent: "border-l-primary", iconBg: "bg-tint text-primary-dark" },
  "dài hạn": { icon: Compass, accent: "border-l-muted-light", iconBg: "bg-surface-alt text-muted-light" },
};

const DIRECTION_META: Record<string, { icon: typeof TrendingUp; className: string }> = {
  "tăng": { icon: TrendingUp, className: "text-up border-up/30 bg-up/10" },
  "giảm": { icon: TrendingDown, className: "text-down border-down/30 bg-red-50" },
  "đi ngang": { icon: Minus, className: "text-blue-700 border-blue-300 bg-blue-50" },
};

// Nhãn xu hướng dạng mũi tên + chữ cho "Bảng tín hiệu nhanh" (Phần 2) — chỉ là
// cách gọi tên khác của cùng "direction" (tăng/giảm/đi ngang) đã có sẵn trong
// trading_scenarios, KHÔNG phải trường dữ liệu mới/suy diễn thêm.
const TREND_META: Record<string, { arrow: string; label: string; className: string }> = {
  "tăng": { arrow: "▲", label: "NGHIÊNG TĂNG", className: "text-up" },
  "giảm": { arrow: "▼", label: "NGHIÊNG GIẢM", className: "text-down" },
  "đi ngang": { arrow: "↔", label: "GIẰNG CO", className: "text-blue-700" },
};

// Nhãn tác động của từng SỰ KIỆN TIN TỨC trong "Diễn biến chính" lên cán cân
// cung–cầu EUA (mục này chỉ là tin tức, không phải diễn biến giá hợp đồng).
const IMPACT_META: Record<string, { arrow: string; label: string; badge: string; bar: string }> = {
  "tăng": { arrow: "▲", label: "Hỗ trợ EUA", badge: "text-up border-up/30 bg-up/10", bar: "border-l-up" },
  "giảm": { arrow: "▼", label: "Áp lực EUA", badge: "text-down border-down/30 bg-red-50", bar: "border-l-down" },
  "trung lập": { arrow: "●", label: "Chờ xác nhận", badge: "text-blue-700 border-blue-200 bg-blue-50", bar: "border-l-blue-400" },
};

// "Khuyến nghị vị thế" cũng suy ra trực tiếp từ "direction" có sẵn của kịch
// bản "ngắn hạn" — không phải khuyến nghị đầu tư mới do LLM tự sinh riêng.
const POSITION_META: Record<string, string> = {
  "tăng": "MUA (LONG) — theo xu hướng ngắn hạn",
  "giảm": "BÁN (SHORT) — theo xu hướng ngắn hạn",
  "đi ngang": "CHỜ — không mở mới, không đóng vị thế đang có",
};

// Mục 1: mỗi bullet mở đầu bằng 1 tag tự do do LLM đặt tên (EUA, Chính sách, Địa
// chính trị...) — không phải enum cố định nên không thể map tay từng giá trị.
// Băm tên tag thành 1 màu trong bảng màu cố định để CÙNG 1 tag luôn ra cùng màu
// giữa các bullet/báo cáo, còn tag khác nhau nhìn tách bạch nhau ngay.
const TAG_COLOR_PALETTE = [
  "bg-tint text-primary-dark border-primary/20",
  "bg-warn-tint text-warn border-warn/30",
  "bg-red-50 text-down border-down/30",
  "bg-indigo-50 text-indigo-700 border-indigo-200",
  "bg-sky-50 text-sky-700 border-sky-200",
  "bg-violet-50 text-violet-700 border-violet-200",
];

function tagColorClass(tag: string): string {
  if (!tag) return "bg-surface-alt text-muted-light border-border";
  let hash = 0;
  for (let i = 0; i < tag.length; i++) hash = (hash * 31 + tag.charCodeAt(i)) >>> 0;
  return TAG_COLOR_PALETTE[hash % TAG_COLOR_PALETTE.length];
}

// Nội dung từ backend đôi khi chứa markdown **bold** thô (đôi khi cả dấu ** lẻ, không cặp đôi)
// — render thành <strong> và luôn dọn sạch mọi dấu * còn sót lại thay vì hiện literal.
// tCO2 -> tCO₂ (số 2 viết dạng subscript)
const subCO2 = (t: string) => t.replace(/CO2/g, "CO\u2082");

function RichText({ text }: { text: string }) {
  if (!text) return null;
  text = subCO2(text);
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return (
    <>
      {parts.map((part, i) =>
        part.startsWith("**") && part.endsWith("**") ? (
          <strong key={i} className="font-semibold text-label">
            {part.slice(2, -2)}
          </strong>
        ) : (
          <span key={i}>{part.replace(/\*\*/g, "")}</span>
        )
      )}
    </>
  );
}

function stripMarkdown(text: string) {
  return text.replace(/\*\*/g, "");
}

// Tách câu "chốt" (tổng hợp/kết luận) ra khỏi phần phân tích để tô nổi bật riêng
// trong 1 ô đậm — backend đánh dấu các câu này bằng tag "**Tổng hợp:**"/"**Kết
// luận:**" (xem report_generator.py), ở đây chỉ cần tìm tag đó và render khác đi.
// (Từ khi có GROUP_HEADLINE_PREFIXES bên dưới, kết luận của TỪNG NHÓM trong Mục
// 3 không còn dùng tag "**Kết luận:**" nữa — đã gộp thẳng vào dòng tiêu đề đầu
// nhóm, xem renderAnalysisLine — nhưng regex này vẫn giữ "Kết luận" để tương
// thích các nơi khác trong báo cáo còn dùng tag đó.)
const CONCLUSION_TAG_RE = /\*\*(?:Tổng hợp|Kết luận)\s*:?\*\*/;

// Riêng dòng "**Tổng hợp:**" cuối mục 3 (kết luận chung về giá EUA — xem
// _prompt_section3/KẾT LUẬN CHUNG CHO CẢ MỤC "PHÂN TÍCH" trong report_generator.py)
// cần TÁCH RA khỏi các block phân tích để đưa lên đầu báo cáo, làm phần "Nhận
// định" trong khối "🚨 ĐIỂM NHẤN" gộp chung với "Tóm tắt điều hành".
const EUA_SUMMARY_TAG_RE = /\*\*Tổng hợp\s*:?\*\*/;

// Nhãn xu hướng giá EUA do backend ghi ngay sau "**Tổng hợp:**" (xem
// _prompt_section3 trong report_generator.py): [TÍCH CỰC]/[TRUNG LẬP]/[TIÊU CỰC]
// — quyết định màu khung "Nhận định". Báo cáo cũ (chưa có nhãn) mặc định trung lập.
type EuaSentiment = "positive" | "neutral" | "negative";
const EUA_SENTIMENT_RE = /^\s*\[\s*(TÍCH CỰC|TRUNG LẬP|TIÊU CỰC)\s*\]\s*/i;
const EUA_SENTIMENT_MAP: Record<string, EuaSentiment> = {
  "TÍCH CỰC": "positive",
  "TRUNG LẬP": "neutral",
  "TIÊU CỰC": "negative",
};
// Tiêu cực: nền đỏ chữ trắng; Trung lập: nền vàng chữ đen; Tích cực: nền xanh chữ trắng.
// print-color-adjust: exact — giữ màu nền khi in PDF (trình duyệt mặc định bỏ nền).
const EUA_SENTIMENT_STYLE: Record<EuaSentiment, { box: string }> = {
  negative: { box: "bg-[#FBE9E7] border border-[#EFB8B0] text-[#8A3B30]" },
  neutral: { box: "bg-[#FFF6D6] border border-[#EBD98F] text-[#7A6320]" },
  positive: { box: "bg-[#E6F3EA] border border-[#B5D8C0] text-[#2D6A44]" },
};

function parseEuaSentiment(summary: string): { sentiment: EuaSentiment; text: string } {
  const match = summary.match(EUA_SENTIMENT_RE);
  if (!match) return { sentiment: "neutral", text: summary };
  return {
    sentiment: EUA_SENTIMENT_MAP[match[1].toUpperCase()] ?? "neutral",
    text: summary.slice(match[0].length).trim(),
  };
}

// Dòng tiêu đề-kết luận đầu mỗi nhóm trong Mục 3, heading "Phân tích" (xem
// _prompt_section3::ĐỊNH DẠNG trong report_generator.py — dòng đầu tiên của
// mỗi nhóm PHẢI viết liền "Tên nhóm: <kết luận>") — đóng khung nổi bật NGUYÊN
// dòng này (tên nhóm + kết luận) thay vì hiện như 1 dòng chữ thường.
const GROUP_HEADLINE_PREFIXES = [
  "Năng lượng & nhiên liệu hóa thạch:",
  "Hạn ngạch & tín chỉ carbon:",
  "Chính sách:",
];

// Chỉ thay đổi VỊ TRÍ hiển thị, không đổi nội dung: rút đúng 1 dòng "**Tổng
// hợp:**" (nếu có) ra khỏi analysis_blocks, phần còn lại của block giữ nguyên.
function extractEuaSummary(blocks: any[] | undefined): { cleanedBlocks: any[]; summary: string | null } {
  if (!blocks || blocks.length === 0) return { cleanedBlocks: blocks || [], summary: null };
  let summary: string | null = null;
  const cleanedBlocks = blocks.map((block: any) => {
    const lines: string[] = (block.content || "").split("\n").filter((l: string) => l.trim());
    const keptLines: string[] = [];
    lines.forEach((line) => {
      const match = line.match(EUA_SUMMARY_TAG_RE);
      if (match && match.index !== undefined && !summary) {
        const before = line.slice(0, match.index).trim();
        if (before) keptLines.push(before);
        summary = line.slice(match.index).replace(EUA_SUMMARY_TAG_RE, "").trim();
      } else {
        keptLines.push(line);
      }
    });
    return { ...block, content: keptLines.join("\n") };
  });
  return { cleanedBlocks, summary };
}

// Chỉ thay đổi VỊ TRÍ hiển thị, không đổi nội dung: rút NGUYÊN block có
// heading "Cần theo dõi" ra khỏi analysis_blocks của Mục 3 để hiển thị thành
// mục riêng, đứng giữa "Kịch bản hành động" và "Gợi ý kinh doanh & giải pháp
// cho SIM" thay vì trong Mục 3 — nội dung watchpoint do backend sinh giữ
// nguyên (vẫn liệt kê "1.", "2."... không có ngày giờ cụ thể vì đây không
// phải sự kiện có lịch, chỉ là điểm cần theo dõi).
function extractWatchpoints(blocks: any[] | undefined): { cleanedBlocks: any[]; watchpoints: string | null } {
  if (!blocks || blocks.length === 0) return { cleanedBlocks: blocks || [], watchpoints: null };
  const watchBlock = blocks.find((b: any) => b.heading === "Cần theo dõi");
  const cleanedBlocks = blocks.filter((b: any) => b.heading !== "Cần theo dõi");
  return { cleanedBlocks, watchpoints: watchBlock?.content || null };
}

// Placeholder outcome mà backend cố tình ghi (xem _prompt_section8 /
// _split_prev_events trong services/report_generator.py) khi tin tức KHÔNG xác
// nhận được kết quả thực tế — dùng nội bộ để đánh dấu sự kiện "đã xử lý xong,
// không hỏi lại nữa" ở lượt sinh báo cáo kế tiếp. Với người đọc, dòng này
// không mang thông tin gì (EIA/API/Baker Hughes/đấu giá EUA hầu như không bao
// giờ có bài báo xác nhận số liệu cụ thể) nên ẩn hẳn khỏi hiển thị thay vì
// hiện "Kết quả: Chưa có thông tin kết quả xác nhận" gây nhiễu mỗi lần.
const UNCONFIRMED_OUTCOME_TEXT = "Chưa có thông tin kết quả xác nhận";

function ConclusionAware({ text, className }: { text: string; className?: string }) {
  const match = text.match(CONCLUSION_TAG_RE);
  if (!match || match.index === undefined) {
    return <p className={className}><RichText text={text} /></p>;
  }
  const before = text.slice(0, match.index).trim();
  const highlight = text.slice(match.index).trim();
  return (
    <div className="space-y-1.5">
      {before && <p className={className}><RichText text={before} /></p>}
      <div className="rounded-md border border-primary/30 bg-tint/60 px-3 py-2">
        <p className="text-[13.5px] leading-[1.5] font-bold text-primary-dark">
          <RichText text={highlight} />
        </p>
      </div>
    </div>
  );
}

// r.up chỉ phản ánh chiều biến động theo NGÀY — không thể dùng chung để tô màu cột
// Δ Tuần, vì tuần có thể ngược chiều với ngày. Suy màu trực tiếp từ dấu của chuỗi giá trị.
function isPositiveDelta(value: string) {
  return typeof value === "string" ? !value.trim().startsWith("-") : true;
}

// Bố cục báo cáo không còn đánh số thứ tự dạng "01"/"02" như trước — thay bằng
// 3 cấp heading rõ ràng theo phân cấp thị giác:
//  - PartHeading: đầu mục lớn nhất ("Phần 1/2/3", hoặc đứng riêng như "Tóm tắt
//    điều hành"/"Nguồn tham khảo" không cần nhãn "Phần").
//  - FramedHighlight: khung viền nổi bật, tiêu đề là dải nền màu nằm trong khung
//    — dùng cho các khối cần nhấn mạnh nhất (Tóm tắt điều hành, Tín hiệu hôm nay).
//  - SubHeading: đầu mục con trong 1 Phần, dải nền xanh dương full-width + in
//    đậm để dễ quét mắt mà không cần số thứ tự.
// Icon chỉ giữ lại ở PartHeading (Phần 1/2/3, Nguồn tham khảo) — FramedHighlight
// và SubHeading không có icon.
// Class "report-heading" dùng chung để CSS in ấn (globals.css) nhận diện, ép
// không ngắt trang ngay sau heading.

function PartHeading({ eyebrow, title, icon: Icon }: { eyebrow?: string; title: string; icon: typeof Sparkles }) {
  const label = eyebrow ? `${eyebrow}: ${title}` : title;
  return (
    <div className="report-heading relative mb-6 print:break-after-avoid">
      <div className="flex items-center gap-3">
        <span className="flex items-center justify-center w-10 h-10 rounded-xl bg-primary-dark text-white shrink-0 shadow-[0_2px_10px_rgba(15,95,90,0.28)]">
          <Icon size={19} strokeWidth={2.25} />
        </span>
        <div className="text-[17px] sm:text-[20px] font-extrabold uppercase tracking-tight text-primary-dark leading-[1.3]">
          {label}
        </div>
      </div>
      <div className="mt-3 h-[3px] w-full bg-gradient-to-r from-primary via-primary/30 to-transparent rounded-full" />
    </div>
  );
}

// Khung viền nổi bật, tiêu đề nằm hẳn TRONG khung (dải nền màu trên cùng, chữ
// trắng) — không dùng nhãn bo tròn đè lên viền trên nữa. Dùng cho Tóm tắt điều
// hành (tone "blue", đầu báo cáo) và TÍN HIỆU HÔM NAY (tone "primary", đầu Phần 2).
// Kiểu chữ DÙNG CHUNG cho tiêu đề các khối nổi bật (Nhận định tổng quan, Tóm tắt
// điều hành, Tín hiệu hôm nay) — cùng cỡ, in hoa, căn giữa.
const BLOCK_TITLE_CLASS = "text-center text-[15px] sm:text-[16px] font-bold uppercase tracking-wide leading-tight";

const FRAME_TONE = {
  primary: { frame: "border-primary-dark", band: "bg-primary-dark" },
  blue: { frame: "border-[#8fc7a5]", band: "bg-[#5fa57e]" },
};

function FramedHighlight({
  title,
  icon: Icon,
  children,
  className,
  tone = "primary",
}: {
  title: string;
  // Icon lucide (SVG) thay cho emoji — emoji không có trong Arial, khi in PDF
  // sẽ bị trình duyệt thay bằng font emoji khác, lệch font với phần còn lại.
  icon?: React.ElementType;
  children: React.ReactNode;
  className?: string;
  tone?: keyof typeof FRAME_TONE;
}) {
  return (
    <div
      className={clsx(
        "report-frame rounded-lg border-2 overflow-hidden shadow-[var(--shadow-soft)]",
        FRAME_TONE[tone].frame,
        className
      )}
    >
      {/* print-color-adjust: exact — giữ màu nền dải tiêu đề khi in PDF */}
      <div
        className={clsx(
          "report-heading flex items-center justify-center gap-2 px-3.5 sm:px-5 py-2 text-white [print-color-adjust:exact] [-webkit-print-color-adjust:exact]",
          FRAME_TONE[tone].band
        )}
      >
        {Icon && <Icon size={15} strokeWidth={2.5} aria-hidden="true" />}
        <h2 className={BLOCK_TITLE_CLASS}>{title}</h2>
      </div>
      <div className="px-3.5 sm:px-5 py-3">{children}</div>
    </div>
  );
}

// Đầu mục con (trong 1 Phần) — không dùng chấm/gạch đầu dòng hay icon (icon chỉ
// giữ lại ở PartHeading: Phần 1/2/3, Nguồn tham khảo), thay bằng dải nền xanh
// lá nhạt rộng bằng đúng chiều ngang nội dung (khớp với vạch gạch dưới của
// PartHeading, không tràn ra ngoài viền báo cáo) để tạo điểm nhấn riêng biệt
// với "Phần". Chữ màu xanh lá đậm trên nền xanh lá nhạt (không dùng tông xanh
// dương).
function SubHeading({ children }: { children: React.ReactNode }) {
  return (
    <div className="report-heading flex items-center w-full mb-4 px-3 py-2 bg-green-100 rounded-md print:break-after-avoid">
      <h3 className="text-[15.5px] sm:text-[16.5px] font-extrabold tracking-tight text-green-800">
        {children}
      </h3>
    </div>
  );
}

// Danh sách chấm tròn dùng chung cho các mục bullet trong Phần 2 (thay cho
// gạch đầu dòng "-").
function DotBullets({ items, render }: { items: any[]; render: (item: any, i: number) => React.ReactNode }) {
  return (
    <ul className="list-none space-y-2.5">
      {items.map((item, i) => (
        <li key={i} className="flex gap-2.5">
          <span className="mt-[9px] w-1.5 h-1.5 rounded-full bg-black shrink-0" aria-hidden="true" />
          <div className="flex-1 min-w-0">{render(item, i)}</div>
        </li>
      ))}
    </ul>
  );
}

// Timeline sự kiện Mục 8 — tách riêng để tái dùng cho cả 2 nhóm "Kết quả" (đã
// có outcome) và "Sự kiện sắp tới" (chưa diễn ra), thay vì 1 danh sách gộp lẫn
// lộn cả hai như trước.
function EventTimeline({ events }: { events: any[] }) {
  return (
    <div>
      {events.map((ev: any, i: number) => {
        const isLast = i === events.length - 1;
        const impactDot =
          ev.impact === "Cao" ? "border-down" :
            ev.impact === "Trung" ? "border-warn" : "border-muted-light";
        return (
          <div key={i} className="flex gap-3 sm:gap-4">
            <div className="w-[52px] sm:w-[60px] shrink-0 flex items-center justify-center rounded-lg border border-border bg-tint/50 py-1.5 mt-0.5">
              <span className="font-mono text-[11px] font-bold text-primary-dark leading-[1.3]">{ev.datetime_vn || "—"}</span>
            </div>
            <div className="flex flex-col items-center shrink-0">
              <span className={clsx("w-2.5 h-2.5 rounded-full border-2 bg-background mt-3.5 shrink-0", impactDot)} />
              {!isLast && <span className="w-px flex-1 bg-border mt-1" />}
            </div>
            <div className={clsx("flex-1 min-w-0", !isLast && "pb-4")}>
              <div className="flex items-start justify-between gap-3 pt-1">
                <span className="text-[13.5px] text-foreground leading-[1.3]">{ev.event}</span>
                <span className={clsx(
                  "shrink-0 font-mono text-[10px] uppercase px-1.5 py-0.5 rounded border",
                  ev.impact === "Cao" ? "text-down border-down/30 bg-red-50" :
                    ev.impact === "Trung" ? "text-warn border-warn/30 bg-warn-tint" :
                      "text-muted-light border-border"
                )}>{ev.impact}</span>
              </div>
              {ev.outcome && (
                <p className="mt-1.5 text-[12.5px] leading-[1.5] text-body italic">
                  <span className="font-semibold not-italic text-label">Kết quả: </span>{ev.outcome}
                </p>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// Bảng gợi ý kinh doanh (Mục SIM) — mỗi gợi ý là 1 hàng, cột theo đúng cấu trúc
// nhân quả (kích hoạt → hành động → lý do / cơ hội → giải pháp → kỳ vọng) thay vì
// gộp thành 1 đoạn văn dài, để dễ quét theo hàng như các bảng chuẩn khác trong báo cáo.
// Nút gỡ gợi ý kinh doanh — chỉ hiện ở màn hình admin; type="button" nên tự ẩn khi in.
function DismissButton({ onClick, className }: { onClick: () => void; className?: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title="Gỡ gợi ý này khỏi báo cáo"
      aria-label="Gỡ gợi ý này khỏi báo cáo"
      className={clsx(
        "inline-flex items-center justify-center w-6 h-6 rounded-md border border-down/30 text-down bg-background hover:bg-red-50 transition-colors print:hidden",
        className
      )}
    >
      <Trash2 size={12} aria-hidden="true" />
    </button>
  );
}

function BizRecommendationTable({
  heading,
  accent,
  rows,
  columns,
  infoKey,
  infoLabel,
  onDismiss,
}: {
  heading: string;
  accent: string;
  rows: Record<string, any>[] | undefined;
  columns: { key: string; label: string }[];
  // Trường ẩn sau nút "i" ở ô cuối mỗi hàng (vd "reason" — Lý do), bấm mới mở
  // ra thành 1 dòng phụ ngay dưới hàng đó — bảng gọn hơn. Khi in luôn hiện.
  infoKey?: string;
  infoLabel?: string;
  // Chỉ truyền ở màn hình admin: nút gỡ gợi ý (hàng phải có "id" — gợi ý ngắn hạn
  // lưu trong bộ nhớ biz_suggestions).
  onDismiss?: (id: number) => void;
}) {
  const [openInfo, setOpenInfo] = useState<Set<number>>(new Set());
  const toggleInfo = (i: number) =>
    setOpenInfo((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  const colWidthCls = columns.length === 2 ? "w-1/2" : "w-1/3";
  return (
    <div>
      <h4 className={clsx("font-mono text-[11.5px] font-bold uppercase tracking-widest mb-3", accent)}>{heading}</h4>
      {rows && rows.length > 0 ? (
        <div className="overflow-x-auto border border-border rounded-lg">
          {/* table-fixed + w-1/3 trên 3 cột nội dung: mỗi trường trong prompt tối đa
              1 câu ngắn (xem _prompt_biz_recommendation) nên độ dài tương đương nhau
              — chia đều tránh cột auto-size lệch nhau theo độ dài chữ thực tế, giống
              cách "Bảng giá nhanh"/"Bảng tín hiệu nhanh" đã làm ở trên. */}
          <table className="w-full table-fixed border-collapse text-[13px]">
            <thead>
              <tr>
                {/* Cột # rộng hơn khi có nút gỡ (admin) để số thứ tự + nút xếp dọc, căn giữa cân đối */}
                <th className={clsx(
                  "text-center font-mono text-[10px] uppercase tracking-wider text-primary-dark px-1 py-2 sm:py-2.5 border-b-2 border-primary/30 border-r border-border bg-tint",
                  onDismiss ? "w-[44px]" : "w-[32px] sm:w-[36px]"
                )}>#</th>
                {columns.map((col, i) => (
                  <th
                    key={col.key}
                    className={clsx(
                      colWidthCls,
                      "text-left font-mono text-[10px] uppercase tracking-wider text-primary-dark px-2 sm:px-3 py-2 sm:py-2.5 border-b-2 border-primary/30 bg-tint",
                      i < columns.length - 1 && "border-r border-border"
                    )}
                  >
                    {col.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map((row, ri) => {
                const info = infoKey ? row[infoKey] : "";
                const isOpen = openInfo.has(ri);
                return (
                  <Fragment key={ri}>
                    <tr className="align-top even:bg-surface/60 hover:bg-tint/40 transition-colors">
                      <td className="px-1 py-2.5 sm:py-3 border-r border-border bg-surface font-mono text-[12px] text-muted-light">
                        <div className="flex flex-col items-center gap-2">
                          <span className="leading-6">{ri + 1}</span>
                          {onDismiss && typeof row.id === "number" && (
                            <DismissButton onClick={() => onDismiss(row.id)} />
                          )}
                        </div>
                      </td>
                      {columns.map((col, i) => {
                        const isLast = i === columns.length - 1;
                        return (
                          <td
                            key={col.key}
                            className={clsx("px-2 sm:px-3 py-2.5 sm:py-3 text-body leading-[1.5] break-words", !isLast && "border-r border-border")}
                          >
                            {isLast && info ? (
                              <div className="flex items-start gap-2">
                                <div className="flex-1 min-w-0"><RichText text={row[col.key] || ""} /></div>
                                <button
                                  type="button"
                                  onClick={() => toggleInfo(ri)}
                                  aria-expanded={isOpen}
                                  aria-label={isOpen ? `Ẩn ${infoLabel || "chi tiết"}` : `Xem ${infoLabel || "chi tiết"}`}
                                  title={isOpen ? `Ẩn ${infoLabel || "chi tiết"}` : `Xem ${infoLabel || "chi tiết"}`}
                                  className={clsx(
                                    "shrink-0 mt-0.5 inline-flex items-center justify-center w-5 h-5 rounded-full border transition-colors print:hidden",
                                    isOpen ? "bg-primary text-white border-primary" : "text-primary border-primary/40 hover:bg-tint"
                                  )}
                                >
                                  <Info size={12} strokeWidth={2.5} aria-hidden="true" />
                                </button>
                              </div>
                            ) : (
                              <RichText text={row[col.key] || ""} />
                            )}
                          </td>
                        );
                      })}
                    </tr>
                    {info && (
                      <tr className={clsx(isOpen ? "table-row" : "hidden print:table-row")}>
                        <td className="border-r border-border bg-surface" />
                        <td colSpan={columns.length} className="px-2 sm:px-3 py-2 bg-tint/50 text-[12.5px] leading-[1.5] text-body">
                          <span className="font-bold text-primary-dark mr-1">{infoLabel || "Chi tiết"}:</span>
                          <RichText text={info} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-[13.5px] text-muted-light italic">Không có gợi ý nào đủ căn cứ trong kỳ này.</p>
      )}
    </div>
  );
}

function CandlestickChart({ report }: { report: Report }) {
  const rawData = report?.content["2"]?.chart_data;
  const candles = rawData && rawData.length > 0 ? rawData : [];
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  if (candles.length === 0) {
    return (
      <div className="w-full h-[200px] sm:h-[260px] flex items-center justify-center text-muted-light text-sm border border-border rounded-lg bg-background">
        Đang cập nhật dữ liệu...
      </div>
    );
  }

  // padT/padB nhỏ + biên độ giá 5% (thay vì 8%) để nến lấp gần hết chiều cao khung,
  // tránh khoảng trống thừa phía trên/dưới khi thu nhỏ khung trên mobile.
  const W = 640, H = 260, padL = 48, padR = 12, padT = 6, padB = 20;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const allVals = candles.flatMap((c: any) => [c.high, c.low]);
  const min = Math.min(...allVals), max = Math.max(...allVals);
  const range = max - min || 1;
  const pMin = min - range * 0.05;
  const pMax = max + range * 0.05;

  const yScale = (v: number) => padT + plotH - ((v - pMin) / (pMax - pMin)) * plotH;
  const cw = plotW / candles.length;
  // Nhãn ngày trên trục X: giãn cách để tối đa ~6 nhãn, tránh chữ chồng lên nhau với 30 nến.
  const xLabelStep = Math.max(1, Math.ceil(candles.length / 6));

  const handlePointer = (e: React.MouseEvent<SVGSVGElement> | React.TouchEvent<SVGSVGElement>) => {
    const svg = e.currentTarget;
    const rect = svg.getBoundingClientRect();
    const clientX = "touches" in e ? e.touches[0]?.clientX : e.clientX;
    if (clientX === undefined) return;
    const xInSvg = (clientX - rect.left) * (W / rect.width);
    const idx = Math.min(candles.length - 1, Math.max(0, Math.floor((xInSvg - padL) / cw)));
    setHoverIndex(idx);
  };

  const hovered = hoverIndex !== null ? candles[hoverIndex] : null;
  const hoveredX = hoverIndex !== null ? padL + hoverIndex * cw + cw / 2 : 0;
  // Lật tooltip sang trái khi nến được hover nằm ở nửa phải biểu đồ, tránh tràn ra ngoài.
  const tooltipLeftPct = hoverIndex !== null ? (hoverIndex / candles.length) * 100 : 0;
  const flipTooltip = tooltipLeftPct > 55;

  return (
    <div className="relative">
      <svg
        width="100%" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="w-full h-[200px] sm:h-[260px] cursor-crosshair"
        onMouseMove={handlePointer}
        onMouseLeave={() => setHoverIndex(null)}
        onTouchMove={handlePointer}
        onTouchEnd={() => setHoverIndex(null)}
      >
        {/* Gridlines + trục giá (Y) */}
        {[0, 1, 2, 3, 4].map(i => {
          const y = padT + (plotH / 4) * i;
          const val = pMax - ((pMax - pMin) / 4) * i;
          return (
            <g key={`grid-${i}`}>
              <line x1={padL} y1={y} x2={W - padR} y2={y} stroke="var(--color-border)" strokeWidth="1" strokeDasharray={i === 4 ? undefined : "2,3"} />
              <text x={padL - 6} y={y + 3} textAnchor="end" className="font-mono text-[9px] fill-muted-light">
                {val.toFixed(2)}
              </text>
            </g>
          );
        })}
        {/* Trục ngày (X) */}
        {candles.map((c: any, i: number) => {
          if (i % xLabelStep !== 0 && i !== candles.length - 1) return null;
          const x = padL + i * cw + cw / 2;
          return (
            <text key={`xl-${i}`} x={x} y={H - 6} textAnchor="middle" className="font-mono text-[8.5px] fill-muted-light">
              {formatShortDate(c.date)}
            </text>
          );
        })}
        {/* Nến */}
        {candles.map((c: any, i: number) => {
          const x = padL + i * cw + cw / 2;
          const isUp = c.close >= c.open;
          const color = isUp ? "var(--color-up)" : "var(--color-down)";
          const yHigh = yScale(c.high);
          const yLow = yScale(c.low);
          const yOpen = yScale(c.open);
          const yClose = yScale(c.close);
          const bodyTop = Math.min(yOpen, yClose);
          const bodyH = Math.max(Math.abs(yClose - yOpen), 1.2);
          const dimmed = hoverIndex !== null && hoverIndex !== i;

          return (
            <g key={`candle-${i}`} opacity={dimmed ? 0.4 : 1}>
              <line x1={x} y1={yHigh} x2={x} y2={yLow} stroke={color} strokeWidth="1.2" />
              <rect x={x - cw * 0.32} y={bodyTop} width={cw * 0.64} height={bodyH} rx={0.6} fill={color} />
            </g>
          );
        })}
        {/* Crosshair khi hover */}
        {hovered && (
          <line x1={hoveredX} y1={padT} x2={hoveredX} y2={H - padB} stroke="var(--color-muted-light)" strokeWidth="1" strokeDasharray="3,3" />
        )}
      </svg>

      {hovered && (
        <div
          className="absolute top-1 pointer-events-none bg-background border border-border rounded-md shadow-[var(--shadow-medium)] px-3 py-2 font-mono text-[11px] z-10 min-w-[128px]"
          style={
            flipTooltip
              ? { right: `${100 - tooltipLeftPct}%`, marginRight: 8 }
              : { left: `${tooltipLeftPct}%`, marginLeft: 8 }
          }
        >
          <div className="text-label font-semibold mb-1.5 whitespace-nowrap">{formatFullDate(hovered.date)}</div>
          <div className="grid grid-cols-2 gap-x-3 gap-y-0.5">
            <span className="text-muted-light">Mở</span><span className="text-foreground text-right">{hovered.open.toFixed(2)}</span>
            <span className="text-muted-light">Cao</span><span className="text-foreground text-right">{hovered.high.toFixed(2)}</span>
            <span className="text-muted-light">Thấp</span><span className="text-foreground text-right">{hovered.low.toFixed(2)}</span>
            <span className="text-muted-light">Đóng</span>
            <span className={clsx("text-right font-semibold", hovered.close >= hovered.open ? "text-up" : "text-down")}>
              {hovered.close.toFixed(2)}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

function formatVietnameseDate(dateStr: string) {
  if (!dateStr) return "";
  const parts = dateStr.split("-");
  if (parts.length !== 3) return dateStr;

  const d = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, parseInt(parts[2]));
  if (isNaN(d.getTime())) return dateStr;

  return `Ngày ${parts[2]} tháng ${parts[1]} năm ${parts[0]}`;
}

const VIETNAMESE_DAYS = ["Chủ Nhật", "Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy"];

// "YYYY-MM-DD" -> "Thứ Ba"... (dựng Date theo giờ địa phương từ từng phần — tránh
// new Date("YYYY-MM-DD") parse theo UTC làm lệch 1 ngày ở múi giờ âm).
function vietnameseWeekday(dateStr: string) {
  const parts = (dateStr || "").split("-");
  if (parts.length !== 3) return "";
  const d = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, parseInt(parts[2]));
  return isNaN(d.getTime()) ? "" : VIETNAMESE_DAYS[d.getDay()];
}

function formatVietnameseDateFull(dateStr: string) {
  if (!dateStr) return "";
  const parts = dateStr.split("-");
  if (parts.length !== 3) return dateStr;

  const d = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, parseInt(parts[2]));
  if (isNaN(d.getTime())) return dateStr;

  const dayOfWeek = VIETNAMESE_DAYS[d.getDay()];
  return `${dayOfWeek}, ngày ${parts[2]} tháng ${parts[1]} năm ${parts[0]}`;
}

/**
 * Toàn bộ nội dung "tờ báo cáo" (masthead → footer) — dùng chung cho cả màn
 * hình user (chỉ xem báo cáo đã published) và màn hình admin duyệt báo cáo,
 * để không lặp lại JSX giữa 2 nơi.
 */
export function ReportDocument({
  report,
  onDismissBizSuggestion,
}: {
  report: Report;
  // Chỉ màn hình admin truyền vào — hiện nút gỡ gợi ý kinh doanh (services/biz_memory.py).
  onDismissBizSuggestion?: (id: number) => void;
}) {
  const priceRows = report?.content["2"]?.prices || [];
  // Menu hamburger điều hướng section (thanh dưới banner).
  const [navOpen, setNavOpen] = useState(false);
  // Thanh trượt giá: tên hợp đồng + giá + Δ ngày, lấy thẳng từ Bảng giá nhanh.
  const tickerItems = priceRows
    .filter((r: any) => r.name && r.price)
    .map((r: any) => {
      // r.price dạng "<số> <đơn vị>" (vd "81.4400 EUR/tCO2") — tách để định dạng số, giữ đơn vị.
      const [rawNumber, ...unitParts] = String(r.price).split(" ");
      return {
        name: r.name as string,
        price: formatCompactPriceNumber(rawNumber),
        unit: subCO2(unitParts.join(" ")),
        dday: r.dday && r.dday !== "-" ? String(r.dday) : null,
      };
    });
  // Lặp danh sách cho đủ ≥ 12 mục mỗi bản — ít hợp đồng thì 1 bản có thể hẹp hơn
  // bề ngang thanh trên màn rộng, lộ khoảng trống khi vòng lặp chạy.
  const tickerLoop = tickerItems.length > 0
    ? Array.from({ length: Math.ceil(12 / tickerItems.length) }, () => tickerItems).flat()
    : [];
  // Chỉ số các dòng Bảng giá nhanh đang mở ghi chú (nút "i") — mặc định đóng hết.
  const [openNotes, setOpenNotes] = useState<Set<number>>(new Set());
  const toggleNote = (i: number) =>
    setOpenNotes((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  // Mục 3 mặc định hiện đủ phân tích (Diễn biến chính -> Cần theo dõi); ẩn được để
  // user chỉ cần xem nhanh bảng Kịch bản hành động, không cần đọc hết phần phân tích.
  const [showAnalysis, setShowAnalysis] = useState(true);

  // Rút dòng "**Tổng hợp:**" (kết luận chung giá EUA) ra khỏi các block phân
  // tích để đưa lên đầu báo cáo, làm phần "Nhận định" trong khối "🚨 ĐIỂM
  // NHẤN" gộp chung với "Tóm tắt điều hành" cũ — xem extractEuaSummary ở trên.
  const { cleanedBlocks: blocksAfterSummary, summary: euaSummary } = extractEuaSummary(report.content["3"]?.analysis_blocks);
  const euaVerdict = euaSummary ? parseEuaSentiment(euaSummary) : null;
  // "Cần theo dõi" giờ hiển thị thành mục riêng, giữa "Kịch bản hành động" và
  // "Gợi ý kinh doanh & giải pháp cho SIM" — xem extractWatchpoints ở trên.
  const { cleanedBlocks: analysisBlocks, watchpoints } = extractWatchpoints(blocksAfterSummary);
  const hasMarketDrivers =
    report.content["2"]?.market_drivers?.bullish?.length > 0 || report.content["2"]?.market_drivers?.bearish?.length > 0;
  // TÍN HIỆU HÔM NAY (đầu Phần 2) tái dùng đúng kịch bản "ngắn hạn" đã có
  // trong trading_scenarios (Mục 3) — không cần trường dữ liệu riêng cho
  // "hôm nay" từ backend.
  const todaySignal = report.content["3"]?.trading_scenarios?.find((sc: any) => sc.horizon === "ngắn hạn");
  // Bảng tín hiệu nhanh (ngay dưới TÍN HIỆU HÔM NAY) tái dùng kịch bản "trung
  // hạn" cho dòng xu hướng 1-3 tháng.
  const midTermSignal = report.content["3"]?.trading_scenarios?.find((sc: any) => sc.horizon === "trung hạn");
  // Hỗ trợ/Kháng cự/Mục tiêu — tính trực tiếp từ OHLC thật 30 phiên
  // (report.content["2"].chart_data), ĐÚNG công thức với backend
  // services/report_generator.py::_eua_technical_levels_summary() (đỉnh/đáy
  // 30 phiên + đo biên độ dao động cho mục tiêu breakout) — không qua LLM suy
  // diễn. "Cắt lỗ" không có công thức xác nhận từ dữ liệu thật nên để trống
  // (hiển thị "—") thay vì tự bịa mốc.
  const chartData = report.content["2"]?.chart_data || [];
  const technicalLevels = chartData.length > 0 ? (() => {
    const resistance = Math.max(...chartData.map((c: any) => c.high));
    const support = Math.min(...chartData.map((c: any) => c.low));
    return { support, resistance, target: resistance + (resistance - support) };
  })() : null;
  const fmtEua = (n: number) => `${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} EUR/tCO\u2082`;
  const NO_DATA = <span className="text-muted-light">—</span>;
  const quickSignalRows: { label: string; value: React.ReactNode }[] = [
    {
      label: "Xu hướng ngắn hạn (1–2 tuần)",
      value: todaySignal ? (
        <span className={TREND_META[todaySignal.direction]?.className}>
          {TREND_META[todaySignal.direction]?.arrow} {TREND_META[todaySignal.direction]?.label}
        </span>
      ) : NO_DATA,
    },
    {
      label: "Xu hướng trung hạn (1–3 tháng)",
      value: midTermSignal ? (
        <span className={TREND_META[midTermSignal.direction]?.className}>
          {TREND_META[midTermSignal.direction]?.arrow} {TREND_META[midTermSignal.direction]?.label}
        </span>
      ) : NO_DATA,
    },
    {
      label: "Khuyến nghị vị thế",
      value: todaySignal ? POSITION_META[todaySignal.direction] ?? NO_DATA : NO_DATA,
    },
    {
      label: "Vùng mua tham chiếu",
      value: todaySignal?.price_zone ? <RichText text={todaySignal.price_zone} /> : NO_DATA,
    },
    {
      label: "Hỗ trợ",
      value: technicalLevels ? `${fmtEua(technicalLevels.support)} (đáy 30 phiên gần nhất)` : NO_DATA,
    },
    {
      label: "Kháng cự",
      value: technicalLevels ? `${fmtEua(technicalLevels.resistance)} (đỉnh 30 phiên gần nhất)` : NO_DATA,
    },
    {
      label: "Mục tiêu",
      value: technicalLevels ? `${fmtEua(technicalLevels.target)} (đo biên độ nếu phá kháng cự)` : NO_DATA,
    },
  ];


  return (
    // max-w-[210mm]: khổ A4 — trên màn hình rộng báo cáo hiển thị như 1 trang PDF
    // thật (căn giữa, có viền/bóng đổ), thay vì kéo giãn hết chiều ngang trình
    // duyệt. Khi in (@media print trong globals.css), khổ A4 thật (@page) luôn
    // hẹp hơn 210mm (đã trừ margin) nên max-width này không co hẹp thêm nội dung in.
    // Mobile (< sm): báo cáo tràn sát 2 mép màn hình (-mx-4 bù đúng px-4 của <main>
    // trong app/layout.tsx), bỏ viền/bo góc/bóng của card — trước đây lề <main> +
    // viền card + lề nội dung + lề khung FramedHighlight cộng dồn, chữ trong khung
    // chỉ còn ~77% bề ngang màn hình 375px. Lề đọc giờ chỉ còn px-4 của nội dung.
    <div className="report-shell max-w-[210mm] mx-auto max-sm:-mx-4 bg-background text-foreground font-sans leading-[1.5] rounded-2xl max-sm:rounded-none border border-border max-sm:border-x-0 shadow-[var(--shadow-soft)] max-sm:shadow-none overflow-hidden mb-10">

      {/* Banner Stavian — 1 khối nền liền (không tách 2 nửa gradient ngược chiều,
          vốn tạo 1 đường nối lộ rõ ở giữa): hàng trên logo + ngày phát hành,
          vạch mảnh thụt theo lề nội dung, rồi eyebrow → tiêu đề → người báo cáo. */}
      <div className="relative overflow-hidden bg-gradient-to-br from-[#1B4D3E] via-[#1a5c4a] to-[#143d33] text-white">
        {/* Hoạ tiết chéo — "slice" (không "none") để hình không bị kéo méo theo
            tỉ lệ khung, 1 SVG chung cho cả banner nên các đường chéo liền mạch.
            .report-banner-art — bỏ giới hạn "svg { max-height: 200px }" (dành cho
            biểu đồ) khi in, xem globals.css, nếu không hoạ tiết bị cắt ngang trong PDF. */}
        <svg
          className="absolute inset-0 w-full h-full pointer-events-none report-banner-art"
          viewBox="0 0 800 300"
          preserveAspectRatio="xMaxYMid slice"
          aria-hidden="true"
        >
          <polygon points="520,0 800,0 800,300 680,300" fill="rgba(255,255,255,0.035)" />
          <polygon points="600,0 800,0 800,200 700,300 640,300" fill="rgba(255,255,255,0.025)" />
          <polygon points="0,190 260,300 0,300" fill="rgba(0,0,0,0.08)" />
          <line x1="470" y1="0" x2="630" y2="300" stroke="rgba(255,255,255,0.07)" strokeWidth="1" />
          <line x1="540" y1="0" x2="700" y2="300" stroke="rgba(255,255,255,0.045)" strokeWidth="1" />
        </svg>

        <div className="relative z-10 px-4 sm:px-10 pt-5 sm:pt-6 pb-6 sm:pb-7">
          {/* Hàng trên: logo trái, ngày phát hành phải — căn giữa theo chiều dọc */}
          <div className="flex items-center justify-between gap-4">
            <img
              src="/stavian_logo.png"
              alt="Stavian Industrial Metal"
              className="h-11 sm:h-14 w-auto object-contain shrink-0"
            />
            <div className="text-right">
              {/* Thứ trong tuần của ngày báo cáo (thay cho nhãn "Phát hành" cũ) */}
              <div className="text-[11px] sm:text-[12px] font-bold uppercase tracking-[0.12em] text-[#8fd9a8] mb-0.5">
                {vietnameseWeekday(report.report_date)}
              </div>
              <div className="text-[13px] sm:text-[15px] font-bold leading-snug">
                {formatVietnameseDate(report.report_date)}
              </div>
            </div>
          </div>

          {/* Vạch ngăn thụt theo lề nội dung (không tràn sát mép như đường nối cũ) */}
          <div className="h-px bg-white/15 mt-4 sm:mt-5 mb-4 sm:mb-5" />

          <div className="text-[11px] sm:text-[12px] font-semibold tracking-[0.2em] uppercase text-[#8fd9a8] mb-1.5">
            Carbon Market Daily News
          </div>
          <h1 className="text-[21px] sm:text-[28px] font-extrabold tracking-[0.2px] leading-[1.2] [text-wrap:balance]">
            TIN TỨC HÀNG NGÀY THỊ TRƯỜNG CARBON
          </h1>
          {/* div (không phải p) — tránh rule ".report-shell p { text-align: justify }"
              làm giãn chữ khi dòng này xuống hàng trên mobile. Tên công ty đã có
              trong logo nên ẩn trên mobile cho gọn 1 dòng. */}
          <div className="mt-2 text-[11.5px] sm:text-[12.5px] leading-snug text-white/80">
            Người báo cáo: Jenny AI · Phòng CLPT<span className="hidden sm:inline"> — Stavian Industrial Metal</span>
          </div>
        </div>

        {/* Viền nhấn dưới banner */}
        <div className="absolute bottom-0 left-0 right-0 h-[3px] bg-gradient-to-r from-[#2f8749] via-[#3da85e] to-[#2f8749]" />
      </div>

      {/* Thanh dưới banner: nút hamburger (menu điều hướng section) bên trái,
          phần còn lại là thanh trượt giá — giá + Δ ngày của từng hợp đồng trong
          Bảng giá nhanh chạy liên tục. Ẩn khi in/xuất PDF. */}
      <nav className="relative z-20 w-full flex items-stretch bg-[#f5f7f6] border-b border-border print:hidden">
        <div className="relative shrink-0 border-r border-border">
          <button
            type="button"
            onClick={() => setNavOpen((o) => !o)}
            aria-expanded={navOpen}
            aria-controls="report-section-menu"
            aria-label={navOpen ? "Đóng menu điều hướng" : "Mở menu điều hướng"}
            className={clsx(
              "h-full flex items-center gap-2 px-3 sm:px-4 py-2.5 text-[#1B4D3E] transition-colors",
              navOpen ? "bg-[#e8efeb]" : "hover:bg-[#e8efeb]"
            )}
          >
            {navOpen ? <X size={18} strokeWidth={2.5} /> : <Menu size={18} strokeWidth={2.5} />}
            <span className="hidden sm:inline text-[12.5px] font-bold uppercase tracking-wide">Mục lục</span>
          </button>

          {navOpen && (
            <>
              {/* Lớp phủ trong suốt — bấm ra ngoài menu thì đóng menu */}
              <div className="fixed inset-0 z-10" onClick={() => setNavOpen(false)} aria-hidden="true" />
              <ul
                id="report-section-menu"
                className="absolute left-0 top-full z-20 mt-px min-w-[220px] bg-background border border-border rounded-b-lg shadow-[var(--shadow-medium)] py-1"
              >
                {REPORT_SECTIONS.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      onClick={() => {
                        setNavOpen(false);
                        document.getElementById(item.id)?.scrollIntoView({ behavior: "smooth", block: "start" });
                      }}
                      className="w-full text-left px-4 py-2.5 text-[13.5px] font-semibold text-[#1B4D3E] hover:bg-[#e8efeb] hover:text-[#2f8749] transition-colors"
                    >
                      {item.label}
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>

        {tickerItems.length > 0 && (
          <div className="ticker relative flex-1 min-w-0 overflow-hidden flex items-center">
            {/* Nội dung nhân đôi (2 bản liền nhau) + dịch đúng 50% để vòng lặp liền
                mạch không bị giật khi quay lại đầu. Di chuột vào thì tạm dừng. */}
            <div
              className="ticker-track ticker-track-ltr motion-reduce:[animation-play-state:paused]"
              style={{ animationDuration: `${tickerLoop.length * 5}s` }}
            >
              {[0, 1].map((copy) => (
                <div key={copy} className="flex shrink-0 items-center" aria-hidden={copy === 1}>
                  {tickerLoop.map((t: { name: string; price: string; unit: string; dday: string | null }, i: number) => (
                    <span key={i} className="flex items-center gap-2 px-4 sm:px-5 whitespace-nowrap text-[12.5px] sm:text-[13px] border-r border-border/60">
                      <span className="font-bold text-[#1B4D3E]">{t.name}</span>
                      <span className="tabular-nums text-label">
                        {t.price}
                        {t.unit && <span className="ml-1 text-[11px] text-muted-light">{t.unit}</span>}
                      </span>
                      {t.dday && (
                        <span className={clsx("tabular-nums font-semibold", isPositiveDelta(t.dday) ? "text-up" : "text-down")}>
                          {isPositiveDelta(t.dday) ? "▲" : "▼"} {t.dday}
                        </span>
                      )}
                    </span>
                  ))}
                </div>
              ))}
            </div>
          </div>
        )}
      </nav>

      <div className="px-4 sm:px-10 pt-3 pb-10">

        {/* Đầu báo cáo gồm 2 khối xếp dọc:
            1) Nhận định — kết luận "Tổng hợp về giá EUA" (dòng "**Tổng hợp:**"
               cuối Mục 3, xem extractEuaSummary ở trên), khung màu theo nhãn xu
               hướng (đỏ/vàng/xanh — xem EUA_SENTIMENT_STYLE).
            2) Tóm tắt điều hành — khung xanh dương, tiêu đề nằm ngay trong khung
               (dải tiêu đề trên cùng, không dùng nhãn bo tròn đè lên viền), bên
               dưới liệt kê các yếu tố nổi bật trong ngày. */}
        <section id="section-highlights" className="pt-2 pb-5 space-y-4">
          {euaVerdict && (
            <div
              className={clsx(
                "report-heading rounded-lg px-3.5 py-3 shadow-sm [&_strong]:text-inherit [print-color-adjust:exact] [-webkit-print-color-adjust:exact]",
                EUA_SENTIMENT_STYLE[euaVerdict.sentiment].box
              )}
            >
              <h2 className={clsx(BLOCK_TITLE_CLASS, "mb-1.5")}>NHẬN ĐỊNH TỔNG QUAN</h2>
              {/* Nội dung nhận định: chữ thường, in đậm (KHÔNG in hoa — chỉ tiêu đề in hoa) */}
              <p className="text-[14.5px] sm:text-[15.5px] leading-[1.45] font-bold normal-case text-left">
                <RichText text={euaVerdict.text} />
              </p>
            </div>
          )}

          <FramedHighlight title="Tóm tắt điều hành" tone="blue">
            <ul className="list-none -my-0.5">
              {report.content["1"]?.bullets?.map((bullet: any, i: number) => {
                const b: string = typeof bullet === "string" ? bullet : bullet.text || "";
                const isMatch = b.includes(':');
                const tag = stripMarkdown(isMatch ? b.split(':')[0] : 'Note');
                const text = isMatch ? b.split(':').slice(1).join(':') : b;
                const sourceName = typeof bullet === "object" ? bullet.source_name : null;
                const sourceUrl = typeof bullet === "object" ? bullet.source_url : null;

                return (
                  <li key={i} className="py-1 text-[14.5px]">
                    <p className="text-foreground leading-[1.3]">
                      <span className={clsx("font-mono text-[10.5px] border rounded-[3px] px-1 py-px mr-2", tagColorClass(tag.trim()))}>
                        {tag.trim().substring(0, 15)}
                      </span>
                      <RichText text={text.trim()} />
                    </p>
                    {sourceName && (
                      <a
                        href={sourceUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="mt-1 inline-block font-mono text-[11px] text-primary hover:underline"
                      >
                        Nguồn: {sourceName} <ExternalLink size={10} className="inline -mt-0.5" aria-hidden="true" />
                      </a>
                    )}
                  </li>
                );
              })}
            </ul>
          </FramedHighlight>
        </section>

        {/* PHẦN 1 — TIN TỨC CHÍNH / NỔI BẬT TRONG NGÀY */}
        {/* Không ép bất kỳ khối nào (chart, bảng giá...) phải nằm trọn 1 trang —
            in tuần tự, tràn trang tự nhiên tới đâu hay tới đó, tránh để lại
            khoảng trắng dài phía trên khi 1 khối lớn bị đẩy nguyên sang trang
            sau vì không đủ chỗ còn lại trên trang hiện tại. */}
        <section id="section-market" className="py-5">
          <PartHeading eyebrow="Phần 1" title="Diễn biến thị trường" icon={LineChart} />

          <div className="flex flex-col lg:flex-row gap-4 items-stretch">
            <div className="lg:flex-[1.6] bg-background border border-border rounded-lg pt-2.5 pb-2 px-3 sm:p-4">
              <div className="flex justify-between font-mono text-[11px] text-muted-light mb-1.5 uppercase tracking-wider">
                <b className="text-label font-sans normal-case text-[13px]">EUA Dec-26 · Nến 30 ngày</b>
                <span className="normal-case">EUR/tCO₂e</span>
              </div>
              <CandlestickChart report={report} />
            </div>

            {report.content["2"]?.key_facts && (
              <div className="lg:flex-1 lg:min-w-[200px] flex flex-col justify-center border-l-2 border-primary bg-tint/40 rounded-r-lg px-3.5 py-2.5">
                <h4 className="font-mono text-[10.5px] font-bold uppercase tracking-widest text-primary-dark mb-1">Số liệu chính</h4>
                <div className="space-y-1 text-[13px] leading-[1.3] text-body">
                  {report.content["2"].key_facts
                    .split(/(?<=\.)\s+/)
                    .filter((s: string) => s.trim())
                    .map((s: string, i: number) => (
                      <p key={i} className="text-left"><RichText text={s} /></p>
                    ))}
                </div>
              </div>
            )}
          </div>

          <div className="mt-5">
            <SubHeading>Bảng giá nhanh</SubHeading>
            {report.content["2"]?.price_timestamp && (
              <p className="font-mono text-[11px] text-muted-light -mt-3 mb-4">{report.content["2"].price_timestamp}</p>
            )}

            {/* Bảng giá full-width */}
            <div className="overflow-x-auto border border-border rounded-lg">
              <table className="w-full sm:table-fixed border-collapse font-mono text-[12px] sm:text-[12.5px]">
                <thead>
                  <tr>
                    <th className="w-[28%] sm:w-[30%] text-left text-primary-dark font-bold text-[11px] uppercase tracking-wider px-1.5 sm:px-2 py-1.5 border-b-2 border-primary/30 border-r border-primary/15 bg-tint">Hợp đồng</th>
                    <th className="w-[18%] sm:w-[18%] text-center text-primary-dark font-bold text-[11px] uppercase tracking-wider px-1 sm:px-2 py-1.5 border-b-2 border-primary/30 border-r border-primary/15 bg-tint">Giá</th>
                    <th className="w-[54%] sm:w-[52%] text-left text-primary-dark font-bold text-[11px] uppercase tracking-wider px-1.5 sm:px-2 py-1.5 border-b-2 border-primary/30 bg-tint">Biến động</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {priceRows.map((r: any, i: number) => {
                    const [rawPriceNumber, ...priceUnitParts] = String(r.price || "").split(" ");
                    const priceNumber = formatCompactPriceNumber(rawPriceNumber);
                    const priceUnit = subCO2(priceUnitParts.join(" "));
                    return (
                      <tr key={i} className="even:bg-surface/60 hover:bg-tint/40 transition-colors">
                        <td className="px-1.5 sm:px-2 py-1.5 border-r border-border font-sans font-semibold text-label break-words">
                          {r.source_url ? (
                            // Link kiểu quen thuộc (xanh dương + luôn gạch chân + icon ↗)
                            // để tách bạch hẳn với tên cột (xanh ngọc, in hoa) — trước đây
                            // cùng tông xanh ngọc, chỉ gạch chân khi hover nên không nhận
                            // ra được là bấm được (nhất là trên mobile, không có hover).
                            <a
                              href={r.source_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              title={`Xem giá ${r.name} tại nguồn`}
                              className="text-blue-700 underline decoration-blue-700/40 underline-offset-2 hover:text-blue-900 hover:decoration-blue-900 transition-colors"
                            >
                              {r.name}
                              <ExternalLink size={11} className="inline-block ml-1 -mt-0.5 align-middle" aria-hidden="true" />
                            </a>
                          ) : (
                            r.name
                          )}
                        </td>
                        <td className="px-1 sm:px-2 py-1.5 border-r border-border text-center">
                          <div className="flex flex-col items-center leading-[1.3]">
                            <span className="break-words tabular-nums">{priceNumber}</span>
                            {priceUnit && <span className="text-[10px] text-muted-light break-words">{priceUnit}</span>}
                          </div>
                        </td>
                        <td className="px-1.5 sm:px-2 py-1.5 font-sans text-[11px] sm:text-[12px] leading-[1.5] text-body">
                          {/* Mặc định chỉ hiện Δ Ngày/Δ Tuần; ghi chú giá (instrument_notes)
                              ẩn sau nút "i", bấm mới mở ra. Khi in PDF luôn hiện ghi chú
                              (print:block) vì bản in không bấm được. */}
                          <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 font-mono text-[10px] sm:text-[11px]">
                            {r.dday !== "-" && (
                              <span className={clsx("tabular-nums", isPositiveDelta(r.dday) ? "text-up" : "text-down")}>
                                Δ Ngày: {r.dday}
                              </span>
                            )}
                            {r.dweek !== "-" && (
                              <span className={clsx("tabular-nums", isPositiveDelta(r.dweek) ? "text-up" : "text-down")}>
                                Δ Tuần: {r.dweek}
                              </span>
                            )}
                            {r.note && (
                              <button
                                type="button"
                                onClick={() => toggleNote(i)}
                                aria-expanded={openNotes.has(i)}
                                aria-label={openNotes.has(i) ? "Ẩn ghi chú giá" : "Xem ghi chú giá"}
                                title={openNotes.has(i) ? "Ẩn ghi chú" : "Xem ghi chú"}
                                className={clsx(
                                  "ml-auto inline-flex items-center justify-center w-5 h-5 rounded-full border transition-colors print:hidden",
                                  openNotes.has(i)
                                    ? "bg-primary text-white border-primary"
                                    : "text-primary border-primary/40 hover:bg-tint"
                                )}
                              >
                                <Info size={12} strokeWidth={2.5} aria-hidden="true" />
                              </button>
                            )}
                          </div>
                          {r.note && (
                            <div
                              className={clsx(
                                "mt-1 rounded-md bg-tint/60 border border-primary/15 px-2 py-1.5",
                                openNotes.has(i) ? "block" : "hidden print:block"
                              )}
                            >
                              {r.note}
                            </div>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* Diễn biến chính — tin tức nổi bật tác động cung/cầu EUA (trực tiếp
              hoặc gián tiếp), mỗi tin có nhãn chiều tác động + nguồn bài viết.
              Backend: content["2"].key_developments (report_generator.py::
              _prompt_key_developments). Báo cáo cũ chưa có trường này → ẩn. */}
          {report.content["2"]?.key_developments?.length > 0 && (
            <div className="mt-6">
              <SubHeading>Diễn biến chính</SubHeading>
              <DotBullets
                items={report.content["2"].key_developments}
                render={(d: any) => (
                  // Định dạng: **Tiêu đề bài báo**: tóm tắt + tác động EUA (Nguồn, ngày).
                  // Báo cáo cũ chưa có d.title → chỉ hiện d.text như trước.
                  <p className="text-[14px] sm:text-[14.5px] leading-[1.6] text-foreground">
                    {d.title && <strong className="font-bold text-label">{d.title}: </strong>}
                    <RichText text={d.text} />
                    {d.source_name && (
                      <>
                        {" ("}
                        <a
                          href={d.source_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-blue-700 underline decoration-blue-700/40 underline-offset-2 hover:text-blue-900 hover:decoration-blue-900"
                        >
                          {d.source_name}
                        </a>
                        {d.source_date ? `, ${d.source_date}` : ""})
                      </>
                    )}
                  </p>
                )}
              />
            </div>
          )}
        </section>

        {/* PHẦN 2 — PHÂN TÍCH VÀ KHUYẾN NGHỊ GIAO DỊCH */}
        <section id="section-analysis" className="py-5">
          <PartHeading eyebrow="Phần 2" title="Phân tích và khuyến nghị giao dịch" icon={BarChart3} />

          {/* TÍN HIỆU HÔM NAY — nội dung đầu tiên của Phần 2: chiến lược trading
              cho phiên hôm đó, rút gọn còn đúng 3 phần Hành động / Cơ sở / Độ
              tin cậy — tái dùng kịch bản "ngắn hạn" đã có ở Mục 3
              (trading_scenarios), không cần trường dữ liệu mới từ backend. */}
          {todaySignal && (
            <div className="mb-6">
              <FramedHighlight title="TÍN HIỆU HÔM NAY">
                {/* Nhấn mạnh trực diện: xu hướng + độ tin cậy lên đầu dạng nhãn lớn,
                    "Hành động" là chữ to nhất trong khối (ô nền nhạt, vạch nhấn trái),
                    "Cơ sở" cỡ chữ đọc thường nhưng lớn hơn trước. */}
                {/* 1 khung duy nhất, không lồng khung con: dòng xu hướng + độ tin cậy
                    (chữ, không nhãn viền), rồi Hành động / Cơ sở ngăn bằng vạch mảnh. */}
                <div className="divide-y divide-border [&>*]:py-3 first:[&>*]:pt-0 last:[&>*]:pb-0">
                  <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
                    {TREND_META[todaySignal.direction] && (
                      <div className="text-[13px] text-muted-light">
                        Xu hướng:{" "}
                        <span className={clsx("text-[16px] font-extrabold uppercase tracking-wide", TREND_META[todaySignal.direction].className)}>
                          {TREND_META[todaySignal.direction].arrow} {TREND_META[todaySignal.direction].label}
                        </span>
                      </div>
                    )}
                    {todaySignal.probability && (
                      <div className="text-[13px] text-muted-light">
                        Độ tin cậy:{" "}
                        <span className="text-[15px] font-bold text-label">{todaySignal.probability}</span>
                      </div>
                    )}
                  </div>

                  <div>
                    <h4 className="text-[12px] font-extrabold uppercase tracking-widest text-primary-dark mb-1">Hành động</h4>
                    {/* Chỉ các nhãn/số chính (**Entry:**, **Mục tiêu:**, **Cắt lỗ**...) in đậm + màu nhấn */}
                    <p className="text-[14.5px] sm:text-[15.5px] leading-[1.6] text-label text-left [&_strong]:font-bold [&_strong]:text-primary-dark">
                      {todaySignal.trading_strategy ? <RichText text={todaySignal.trading_strategy} /> : <span className="text-muted-light">—</span>}
                    </p>
                  </div>

                  <div>
                    <h4 className="text-[12px] font-extrabold uppercase tracking-widest text-primary-dark mb-1">Cơ sở</h4>
                    <p className="text-[14.5px] sm:text-[15.5px] leading-[1.6] text-label text-left">
                      {todaySignal.condition ? <RichText text={todaySignal.condition} /> : <span className="text-muted-light">—</span>}
                    </p>
                  </div>
                </div>
              </FramedHighlight>
            </div>
          )}

          {/* Bảng tín hiệu nhanh — nằm dưới TÍN HIỆU HÔM NAY, trên Phân tích:
              các chỉ số nào có sẵn từ dữ liệu (trading_scenarios, OHLC 30
              phiên) thì lấy đúng giá trị thật; chỉ số nào chưa có công thức
              xác nhận (Cắt lỗ) thì để trống, không suy diễn qua LLM. */}
          <div className="mb-6">
            <SubHeading>Bảng tín hiệu nhanh</SubHeading>
            <div className="overflow-x-auto border border-border rounded-lg">
              <table className="w-full border-collapse text-[12.5px] sm:text-[13px]">
                <thead>
                  <tr>
                    <th className="text-left font-mono text-[10px] uppercase tracking-wider text-primary-dark px-1.5 sm:px-3 py-2.5 border-b-2 border-primary/30 border-r border-border bg-tint w-[42%] sm:w-[34%]">Chỉ số</th>
                    <th className="text-left font-mono text-[10px] uppercase tracking-wider text-primary-dark px-1.5 sm:px-3 py-2.5 border-b-2 border-primary/30 bg-tint">Giá trị</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {quickSignalRows.map((row, i) => (
                    <tr key={i} className="align-top even:bg-surface/60">
                      <td className="px-1.5 sm:px-3 py-1.5 border-r border-border font-sans font-semibold text-label">{row.label}</td>
                      <td className="px-1.5 sm:px-3 py-1.5 leading-[1.5] text-body">{row.value}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Động lực thị trường — nằm dưới Bảng tín hiệu nhanh, trên Phân tích
              (chuyển từ Bảng giá nhanh (Phần 1) sang đây trước đó). */}
          {hasMarketDrivers && (
            <div className="mb-6">
              <SubHeading>Động lực thị trường</SubHeading>
              <div className="grid sm:grid-cols-2 gap-3">
                <div className="border border-up/25 bg-up/[0.04] rounded-lg overflow-hidden">
                  <div className="flex items-center gap-1.5 bg-up/10 text-up font-mono text-[11px] font-bold uppercase tracking-wider px-3 py-2 border-b border-up/20">
                    <TrendingUp size={14} strokeWidth={2.5} /> Động lực tăng
                  </div>
                  <div className="p-3 space-y-3">
                    {report.content["2"].market_drivers.bullish?.length > 0 ? (
                      report.content["2"].market_drivers.bullish.map((d: any, i: number) => (
                        <div key={i}>
                          <p className="text-[13px] leading-[1.5] text-body">
                            <span className={clsx(
                              "font-mono text-[9.5px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded mr-1.5 align-middle whitespace-nowrap",
                              d.tag === "FACT" ? "bg-up/10 text-up border border-up/30" : "bg-blue-50 text-blue-700 border border-blue-200"
                            )}>{d.tag}</span>
                            <RichText text={d.text} />
                          </p>
                          {d.source_url && (
                            <a
                              href={d.source_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="mt-1 inline-block font-mono text-[10.5px] text-primary hover:underline"
                            >
                              Nguồn: {d.source_name} <ExternalLink size={10} className="inline -mt-0.5" aria-hidden="true" />
                            </a>
                          )}
                        </div>
                      ))
                    ) : (
                      <p className="text-[12.5px] text-muted-light italic">Không có động lực tăng đáng chú ý.</p>
                    )}
                  </div>
                </div>
                <div className="border border-down/25 bg-down/[0.04] rounded-lg overflow-hidden">
                  <div className="flex items-center gap-1.5 bg-down/10 text-down font-mono text-[11px] font-bold uppercase tracking-wider px-3 py-2 border-b border-down/20">
                    <TrendingDown size={14} strokeWidth={2.5} /> Động lực giảm
                  </div>
                  <div className="p-3 space-y-3">
                    {report.content["2"].market_drivers.bearish?.length > 0 ? (
                      report.content["2"].market_drivers.bearish.map((d: any, i: number) => (
                        <div key={i}>
                          <p className="text-[13px] leading-[1.5] text-body">
                            <span className={clsx(
                              "font-mono text-[9.5px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded mr-1.5 align-middle whitespace-nowrap",
                              d.tag === "FACT" ? "bg-up/10 text-up border border-up/30" : "bg-blue-50 text-blue-700 border border-blue-200"
                            )}>{d.tag}</span>
                            <RichText text={d.text} />
                          </p>
                          {d.source_url && (
                            <a
                              href={d.source_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="mt-1 inline-block font-mono text-[10.5px] text-primary hover:underline"
                            >
                              Nguồn: {d.source_name} <ExternalLink size={10} className="inline -mt-0.5" aria-hidden="true" />
                            </a>
                          )}
                        </div>
                      ))
                    ) : (
                      <p className="text-[12.5px] text-muted-light italic">Không có động lực giảm đáng chú ý.</p>
                    )}
                  </div>
                </div>
              </div>
            </div>
          )}

          {report.content["3"] && (
            <div className="mb-6">
              <SubHeading>Phân tích</SubHeading>
              {report.content["3"].title && (
                <p className="-mt-2.5 mb-4 ml-[38px] text-[12px] text-muted-light italic">{report.content["3"].title}</p>
              )}

              {(analysisBlocks?.length > 0 || report.content["3"].correlation_analysis) && (
                <button
                  type="button"
                  onClick={() => setShowAnalysis(v => !v)}
                  className="mb-4 flex items-center gap-1.5 font-mono text-[11px] font-bold uppercase tracking-wider text-primary border border-primary/30 bg-tint/50 hover:bg-tint rounded-md px-2.5 py-1.5 transition-colors"
                >
                  {showAnalysis ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
                  {showAnalysis ? "Ẩn phân tích" : "Hiện phân tích"}
                </button>
              )}

              {showAnalysis && (
                <>
                  {analysisBlocks?.map((block: any, i: number) => (
                    <div key={i} className="mb-5">
                      <h4 className="font-mono text-[11.5px] font-bold uppercase tracking-widest text-primary mb-1.5">{block.heading}</h4>
                      <div className="space-y-1.5">
                        {(block.content || "").split("\n").filter((line: string) => line.trim()).map((line: string, j: number) => {
                          // Backend đôi khi tự chèn gạch đầu dòng thô ("- ...", có thể kèm
                          // dấu cách/tab thừa hoặc en-dash "–") cho từng mã/ý trong 1 nhóm,
                          // ở BẤT KỲ heading nào (Diễn biến chính, Phân tích...) — đổi hiển
                          // thị sang chấm tròn thay vì gạch ngang, không đổi nội dung chữ
                          // (chỉ bỏ đúng phần tiền tố gạch đầu dòng khớp được).
                          const trimmed = line.trim();
                          // Mục 3 / heading "Phân tích": dòng đầu mỗi nhóm gộp sẵn "Tên nhóm:
                          // <kết luận>" (xem ĐỊNH DẠNG trong _prompt_section3) — đóng khung nổi
                          // bật NGUYÊN dòng này thay vì hiện như chữ thường.
                          if (block.heading === "Phân tích" && GROUP_HEADLINE_PREFIXES.some((p) => trimmed.startsWith(p))) {
                            return (
                              <div key={j} className="rounded-md border border-primary/30 bg-tint/60 px-3 py-2">
                                <p className="text-[14px] leading-[1.5] font-bold text-primary-dark">
                                  <RichText text={trimmed} />
                                </p>
                              </div>
                            );
                          }
                          const dashMatch = trimmed.match(/^[-–—]\s+/);
                          if (!dashMatch) {
                            return <ConclusionAware key={j} text={line} className="text-[14px] leading-[1.5] text-body" />;
                          }
                          return (
                            <div key={j} className="flex gap-2.5">
                              <span className="mt-[9px] w-1.5 h-1.5 rounded-full bg-black shrink-0" aria-hidden="true" />
                              <div className="flex-1 min-w-0">
                                <ConclusionAware text={trimmed.slice(dashMatch[0].length)} className="text-[14px] leading-[1.5] text-body" />
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ))}

                  {report.content["3"].correlation_analysis && (
                    <div className="border-l-2 border-primary bg-tint/40 rounded-r-lg px-3 sm:px-4 py-3 my-5 space-y-2">
                      <h4 className="font-mono text-[11.5px] font-bold uppercase tracking-widest text-primary-dark mb-1.5">Chuỗi Logic: Gas + Coal + Power → EUA</h4>
                      {(report.content["3"].correlation_analysis.gas_comment || report.content["3"].correlation_analysis.gas_coal_power) && (
                        <div className="space-y-2.5 mb-1">
                          {report.content["3"].correlation_analysis.gas_comment && (
                            <p className="text-[13.5px] leading-[1.5] text-body"><b className="text-primary-dark font-bold">Gas:</b> <RichText text={report.content["3"].correlation_analysis.gas_comment} /></p>
                          )}
                          {report.content["3"].correlation_analysis.coal_comment && (
                            <p className="text-[13.5px] leading-[1.5] text-body"><b className="text-primary-dark font-bold">Than:</b> <RichText text={report.content["3"].correlation_analysis.coal_comment} /></p>
                          )}
                          {report.content["3"].correlation_analysis.power_comment && (
                            <p className="text-[13.5px] leading-[1.5] text-body"><b className="text-primary-dark font-bold">Điện Đức:</b> <RichText text={report.content["3"].correlation_analysis.power_comment} /></p>
                          )}
                        </div>
                      )}
                      <ConclusionAware
                        text={report.content["3"].correlation_analysis.fuel_switching_chain || report.content["3"].correlation_analysis.gas_coal_power}
                        className="text-[14px] leading-[1.5] text-body"
                      />
                      <div className="rounded-md border border-primary/30 bg-tint/60 px-3 py-2">
                        <p className="text-[14px] leading-[1.5] font-bold text-primary-dark">
                          <RichText text={report.content["3"].correlation_analysis.eua_conclusion} />
                        </p>
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
          )}

          {/* Kịch bản hành động */}
          {report.content["3"]?.trading_scenarios?.length > 0 && (() => {
            const HORIZON_ORDER = ["ngắn hạn", "trung hạn", "dài hạn"];
            const byHorizon: Record<string, any> = {};
            report.content["3"].trading_scenarios.forEach((sc: any) => { byHorizon[sc.horizon] = sc; });
            const columns = HORIZON_ORDER.filter(h => byHorizon[h]);
            // Cột "Chỉ tiêu" cố định 16%, phần còn lại chia đều cho số khung thời gian
            // thực có (1-3 cột) — table-fixed + % (thay vì min-width theo px) để bảng
            // luôn vừa khít chiều rộng khung chứa, không tràn ra ngoài phải kéo ngang,
            // và fit được cả khi in/xuất PDF (khổ A4 trừ margin ~190mm).
            const dataColWidthPct = (100 - 16) / columns.length;
            // "highlight" đánh dấu 2 hàng quan trọng nhất (vùng giá tham chiếu + chiến
            // lược trading) để tô nổi bật riêng, giúp mắt người đọc bắt được ngay phần
            // thông tin actionable nhất thay vì phải đọc lướt cả bảng.
            const ROWS: { label: string; icon?: typeof Target; highlight?: boolean; render: (sc: any) => React.ReactNode }[] = [
              {
                // Tên hàng ngắn gọn ("Xác suất / Chiều giá" cũ dài, xuống 2 dòng khó
                // nhìn trên mobile) — thông tin nằm ngay trong nội dung, mỗi dòng
                // có nhãn riêng "Chiều giá: ..." / "Xác suất: ...".
                label: "Nhận định",
                render: (sc) => {
                  const dirMeta = DIRECTION_META[sc.direction];
                  const DirIcon = dirMeta?.icon;
                  return (
                    <div className="flex flex-col gap-1.5 items-start">
                      {dirMeta && (
                        <span className={clsx("flex items-center gap-1 font-mono text-[10px] uppercase tracking-wider rounded px-1.5 py-0.5 border", dirMeta.className)}>
                          <DirIcon size={11} className="shrink-0" /> Chiều giá: {sc.direction}
                        </span>
                      )}
                      {sc.probability && (
                        <span className={clsx(
                          "font-mono text-[10px] uppercase tracking-wider rounded px-1.5 py-0.5 border",
                          sc.probability === "Cao" ? "text-up border-up/30 bg-up/10" :
                            sc.probability === "Thấp" ? "text-muted-light border-border" :
                              "text-warn border-warn/30 bg-warn-tint"
                        )}>Xác suất: {sc.probability}</span>
                      )}
                    </div>
                  );
                },
              },
              { label: "Điều kiện kích hoạt", render: (sc) => <RichText text={sc.condition} /> },
              {
                label: "Vùng giá tham chiếu", icon: Target, highlight: true,
                render: (sc) => sc.price_zone ? <span className="font-semibold text-primary-dark"><RichText text={sc.price_zone} /></span> : <span className="text-muted-light">—</span>,
              },
              { label: "Rủi ro chính", icon: AlertTriangle, render: (sc) => <span className="text-down"><RichText text={sc.key_risk} /></span> },
              {
                label: "Chiến lược Trading", icon: Crosshair, highlight: true,
                render: (sc) => sc.trading_strategy ? <RichText text={sc.trading_strategy} /> : <span className="text-muted-light">—</span>,
              },
            ];

            return (
              <div className="mb-6">
                <SubHeading>Kịch bản hành động</SubHeading>

                {/* Mobile: gộp TẤT CẢ khung thời gian vào 1 khối duy nhất (không còn
                      tách mỗi khung thời gian thành 1 card riêng) — nhóm theo từng chỉ
                      tiêu, trong mỗi chỉ tiêu liệt kê liền nhau Ngắn/Trung/Dài hạn để
                      đọc tập trung và so sánh ngay giữa các khung. Từ sm+ (kể cả khi
                      in/xuất PDF) dùng bảng % width bên dưới. */}
                <div className="sm:hidden border border-border rounded-lg overflow-hidden divide-y divide-border">
                  {ROWS.map((row, ri) => {
                    const RowIcon = row.icon;
                    return (
                      <div key={ri} className={clsx(row.highlight && "bg-primary/[0.06]")}>
                        <div className={clsx(
                          "flex items-center gap-1.5 px-3 py-2 text-[12px] font-bold uppercase tracking-wide text-primary-dark",
                          row.highlight ? "bg-primary/[0.08] border-l-2 border-primary" : "bg-tint"
                        )}>
                          {RowIcon && <RowIcon size={13} className="shrink-0" />}
                          {row.label}
                        </div>
                        <div className="divide-y divide-border/70">
                          {columns.map(h => {
                            const meta = HORIZON_META[h];
                            const Icon = meta.icon;
                            return (
                              <div key={h} className="flex items-start gap-2.5 px-3 py-2">
                                <span className={clsx(
                                  "shrink-0 w-[92px] inline-flex items-center gap-1 whitespace-nowrap rounded px-1.5 py-0.5 mt-0.5 text-[10px] font-bold uppercase tracking-wide",
                                  meta.iconBg
                                )}>
                                  <Icon size={11} className="shrink-0" /> {h}
                                </span>
                                <div className="flex-1 min-w-0 text-[13px] text-body leading-[1.5] break-words">
                                  {byHorizon[h] ? row.render(byHorizon[h]) : <span className="text-muted-light">—</span>}
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    );
                  })}
                </div>

                {/* sm+ trên màn hình VÀ khi in/xuất PDF: bảng so sánh nhiều cột.
                      table-fixed + width theo % (thay vì min-width theo px) để bảng luôn
                      co vừa khung chứa — kể cả khổ A4 lúc in — nên không còn cần kéo
                      ngang hay fallback card riêng cho print. */}
                <div className="hidden sm:block border border-border rounded-lg overflow-hidden">
                  <table className="w-full table-fixed border-collapse text-[13px]">
                    <thead>
                      <tr>
                        <th style={{ width: "16%" }} className="text-left font-mono text-[10px] uppercase tracking-wider text-primary-dark px-2 sm:px-3 py-2 sm:py-2.5 border-b-2 border-primary/30 border-r border-border bg-tint">Chỉ tiêu</th>
                        {columns.map(h => {
                          const meta = HORIZON_META[h];
                          const Icon = meta.icon;
                          return (
                            <th key={h} style={{ width: `${dataColWidthPct}%` }} className={clsx("text-left px-2 sm:px-3 py-2 sm:py-2.5 border-b-2 border-border", meta.iconBg)}>
                              <span className="flex items-center gap-1.5 font-mono text-[11px] font-bold uppercase tracking-wider">
                                <Icon size={13} /> {h}
                              </span>
                            </th>
                          );
                        })}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                      {ROWS.map((row, ri) => {
                        const RowIcon = row.icon;
                        return (
                          <tr key={ri} className={clsx(
                            "align-top transition-colors",
                            row.highlight ? "bg-primary/[0.06] hover:bg-primary/10" : "even:bg-surface/60 hover:bg-tint/40"
                          )}>
                            <td className={clsx(
                              "px-2 sm:px-3 py-2.5 sm:py-3 border-r text-[12.5px] break-words",
                              row.highlight ? "border-primary/20 bg-primary/[0.08] font-bold text-primary-dark" : "border-border bg-surface font-semibold text-label"
                            )}>
                              <span className="flex items-center gap-1.5">
                                {RowIcon && <RowIcon size={13} className="text-primary-dark shrink-0" />}
                                {row.label}
                              </span>
                            </td>
                            {columns.map(h => (
                              <td key={h} className={clsx(
                                "px-2 sm:px-3 py-2.5 sm:py-3 leading-[1.5] break-words",
                                row.highlight ? "text-label border-l border-primary/10" : "text-body"
                              )}>
                                {byHorizon[h] ? row.render(byHorizon[h]) : <span className="text-muted-light">—</span>}
                              </td>
                            ))}
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>

                {/* Lưu ý — chỉ mang tính tham khảo, không phải khuyến nghị đầu tư trực tiếp. */}
                <div className="mt-3 flex items-start gap-2 rounded-lg border border-warn/30 bg-warn-tint px-3 py-2.5">
                  <Info size={15} className="text-warn shrink-0 mt-0.5" />
                  <p className="text-[12.5px] leading-[1.5] text-body">
                    <b className="text-label">Lưu ý:</b> Đây là các chiến lược dựa trên phân tích của Jenny, chỉ mang tính chất tham khảo.
                    Người đọc cần xem xét kỹ, đối chiếu với bối cảnh thực tế và khẩu vị rủi ro của mình trước khi ra quyết định —
                    không phải khuyến nghị đầu tư/giao dịch trực tiếp.
                  </p>
                </div>
              </div>
            );
          })()}

          {/* Cần theo dõi — tách khỏi Mục 3/8, đứng giữa "Kịch bản hành động" và
              "Gợi ý kinh doanh & giải pháp cho SIM" (theo yêu cầu bố cục báo cáo). */}
          {watchpoints && (
            <div className="mb-6">
              <SubHeading>Cần theo dõi</SubHeading>
              <div className="space-y-1.5">
                {watchpoints.split("\n").filter((line: string) => line.trim()).map((line: string, j: number) => (
                  <ConclusionAware key={j} text={line} className="text-[14px] leading-[1.5] text-body" />
                ))}
              </div>
            </div>
          )}

          {/* Gợi ý kinh doanh & giải pháp cho SIM */}
          {report.content["biz"] && (
            <div className="mb-6">
              <SubHeading>{report.content["biz"].title}</SubHeading>
              <div className="flex flex-col gap-6">
                {/* Jenny nhắc lại — gợi ý ở báo cáo trước (trong 10 ngày) mà tình huống
                    kích hoạt vừa xảy ra hôm nay (services/biz_memory.py). */}
                {report.content["biz"].reminders?.length > 0 && (
                  <div className="rounded-lg border-2 border-warn/50 bg-warn-tint overflow-hidden [print-color-adjust:exact] [-webkit-print-color-adjust:exact]">
                    <div className="flex items-center gap-2.5 px-3.5 py-2.5 border-b border-warn/30">
                      <img src="/jenny.jpg" alt="" className="w-8 h-8 rounded-full object-cover shrink-0" />
                      <div className="text-[14px] sm:text-[15px] font-bold text-label">Jenny nhắc lại đề xuất trước đây</div>
                    </div>
                    <ul className="list-none divide-y divide-warn/20">
                      {report.content["biz"].reminders.map((r: any, i: number) => (
                        <li key={i} className="px-3.5 py-3 space-y-1.5">
                          <div className="flex items-start gap-2">
                            <p className="flex-1 min-w-0 text-[14px] leading-[1.5] text-foreground">
                              Ngày <b>{formatFullDate(r.suggested_date)}</b> Jenny đã đề xuất: <b><RichText text={r.action} /></b>
                            </p>
                            {onDismissBizSuggestion && typeof r.id === "number" && (
                              <DismissButton onClick={() => onDismissBizSuggestion(r.id)} className="shrink-0" />
                            )}
                          </div>
                          <p className="text-[13px] leading-[1.5] text-body">
                            <span className="font-semibold">Tình huống kích hoạt:</span> <RichText text={r.trigger} />
                          </p>
                          <p className="text-[13.5px] leading-[1.5] font-semibold text-up">
                            ✓ Tình huống đã xảy ra{r.evidence ? <>: <span className="font-normal text-foreground"><RichText text={r.evidence} /></span></> : "."}
                          </p>
                          {r.source_name && (
                            r.source_url ? (
                              <a
                                href={r.source_url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="inline-flex items-center gap-1 text-[11.5px] text-blue-700 underline decoration-blue-700/40 underline-offset-2 hover:text-blue-900"
                              >
                                Nguồn: {r.source_name} <ExternalLink size={10} aria-hidden="true" />
                              </a>
                            ) : (
                              <span className="text-[11.5px] text-muted-light">Nguồn: {r.source_name}</span>
                            )
                          )}
                          <p className="text-[13px] italic text-warn font-semibold">
                            Anh/chị đã thực hiện theo đề xuất này chưa?
                          </p>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                <div>
                  <BizRecommendationTable
                    heading="Ngắn hạn"
                    accent="text-label"
                    rows={report.content["biz"].short_term}
                    columns={[
                      { key: "trigger", label: "Tình huống kích hoạt" },
                      { key: "action", label: "Hành động đề xuất" },
                    ]}
                    infoKey="reason"
                    infoLabel="Lý do"
                    onDismiss={onDismissBizSuggestion}
                  />
                  {/* Gợi ý cũ còn trong trí nhớ nhưng tình huống chưa xảy ra — không viết
                      lại cả dòng, chỉ tham chiếu ngày đề xuất. */}
                  {report.content["biz"].tracking?.length > 0 && (
                    <div className="mt-3 rounded-lg border border-border bg-surface/60 px-3.5 py-2.5">
                      <h5 className="text-[11.5px] font-bold uppercase tracking-wider text-muted-light mb-1.5">
                        Đề xuất trước đây — đang theo dõi
                      </h5>
                      <ol className="list-none space-y-1.5">
                        {report.content["biz"].tracking.map((t: any, i: number) => (
                          <li key={i} className="flex items-start gap-2 text-[13px] leading-[1.5] text-body">
                            <p className="flex-1 min-w-0">
                              <span className="font-semibold text-label">{i + 1}.</span>{" "}
                              <RichText text={t.action} />{" "}
                              <span className="text-muted-light">
                                — đã gợi ý từ báo cáo ngày {formatFullDate(t.suggested_date)}, tình huống kích hoạt
                                (<RichText text={t.trigger} />) chưa xảy ra.
                              </span>
                            </p>
                            {onDismissBizSuggestion && typeof t.id === "number" && (
                              <DismissButton onClick={() => onDismissBizSuggestion(t.id)} className="shrink-0" />
                            )}
                          </li>
                        ))}
                      </ol>
                    </div>
                  )}
                </div>
                <BizRecommendationTable
                  heading="Dài hạn"
                  accent="text-primary"
                  rows={report.content["biz"].long_term}
                  columns={[
                    { key: "opportunity", label: "Cơ hội" },
                    { key: "solution", label: "Giải pháp đề xuất" },
                  ]}
                  // Kỳ vọng thu vào nút "i" (như Lý do ở bảng Ngắn hạn) — 3 cột hẹp
                  // trên mobile làm mỗi ô chỉ 2–3 chữ/dòng, rất khó đọc.
                  infoKey="expectation"
                  infoLabel="Kỳ vọng"
                  onDismiss={onDismissBizSuggestion}
                />
              </div>
            </div>
          )}

          {/* SECTION 4 (text/bullets) — Cập nhật tín chỉ carbon & CBAM */}
          {["4"].map(key => {
            const section = report.content[key];
            if (!section) return null;
            return (
              <div key={key} className="mb-6">
                <SubHeading>{section.title}</SubHeading>
                {section.bullets ? (
                  <DotBullets
                    items={section.bullets}
                    render={(bullet: any) => {
                      const b: string = typeof bullet === "string" ? bullet : bullet.text || "";
                      const sourceName = typeof bullet === "object" ? bullet.source_name : null;
                      const sourceUrl = typeof bullet === "object" ? bullet.source_url : null;
                      return (
                        <div>
                          <ConclusionAware text={b} className="text-[14px] leading-[1.5] text-body" />
                          {sourceName && (
                            <a
                              href={sourceUrl}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="mt-1 inline-block font-mono text-[11px] text-primary hover:underline"
                            >
                              Nguồn: {sourceName} <ExternalLink size={10} className="inline -mt-0.5" aria-hidden="true" />
                            </a>
                          )}
                        </div>
                      );
                    }}
                  />
                ) : (
                  <ConclusionAware text={section.text} className="text-[14px] leading-[1.5] text-body" />
                )}
              </div>
            );
          })}

          {/* Lịch sự kiện 7 ngày tới */}
          {report.content["8"] && (
            <div className="mb-2">
              <SubHeading>{report.content["8"].title}</SubHeading>
              {report.content["8"].events?.length > 0 ? (() => {
                // Tách rõ 2 nhóm thay vì 1 timeline gộp lẫn lộn: sự kiện ĐÃ có "outcome"
                // (đã diễn ra, đã cập nhật kết quả) đứng riêng khỏi sự kiện CHƯA diễn ra —
                // nhóm "sắp tới" sắp xếp theo "date" tăng dần để luôn đúng trình tự thời
                // gian dù thứ tự gốc trong content["8"].events là gì.
                //
                // Loại bỏ hẳn sự kiện chỉ có outcome placeholder "chưa xác nhận" — với
                // các mốc như EIA/API/Baker Hughes/đấu giá EUA, tin tức hầu như không
                // bao giờ xác nhận số liệu cụ thể (xem UNCONFIRMED_OUTCOME_TEXT), nên
                // hiện dòng này mỗi lần chỉ gây nhiễu, không có giá trị cho người đọc.
                const events: any[] = report.content["8"].events.filter(
                  (ev: any) => ev.outcome !== UNCONFIRMED_OUTCOME_TEXT
                );
                const results = events.filter((ev) => ev.outcome);
                const upcoming = events
                  .filter((ev) => !ev.outcome)
                  .slice()
                  .sort((a, b) => (a.date || "").localeCompare(b.date || ""));
                return (
                  <div className="space-y-5">
                    {results.length > 0 && (
                      <div>
                        <h4 className="font-mono text-[10.5px] font-bold uppercase tracking-widest text-label mb-2">Kết quả</h4>
                        <EventTimeline events={results} />
                      </div>
                    )}
                    {upcoming.length > 0 && (
                      <div>
                        <h4 className="font-mono text-[10.5px] font-bold uppercase tracking-widest text-label mb-2">Sự kiện sắp tới</h4>
                        <EventTimeline events={upcoming} />
                      </div>
                    )}
                  </div>
                );
              })() : (
                report.content["8"].bullets?.map((b: string, i: number) => (
                  <p key={i} className="text-[14px] leading-[1.5] text-body"><RichText text={b} /></p>
                ))
              )}
            </div>
          )}
        </section>

        {/* PHẦN 3 — CHI TIẾT CÁC TIN TỨC CHÍNH */}
        {report.content["6"] && (
          <section id="section-news" className="py-5">
            <PartHeading eyebrow="Phần 3" title={report.content["6"].title || "Chi tiết các tin tức chính"} icon={Newspaper} />
            {[
              { key: "international", label: "Quốc tế" },
              { key: "vietnam", label: "Việt Nam" },
            ].map(({ key, label }) => {
              const items = report.content["6"][key];
              return (
                <div key={key} className="mb-6 last:mb-0">
                  <h4 className="font-mono text-[11.5px] font-bold uppercase tracking-widest text-primary-dark mb-3">{label}</h4>
                  {items?.length > 0 ? (
                    <div className="space-y-4">
                      {items.map((art: any, i: number) => (
                        <div key={i} className="pb-4 border-b border-border-soft last:border-b-0 last:pb-0">
                          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                            <p className="text-[14px] font-semibold text-label leading-[1.3]">{i + 1}. {art.title}</p>
                            {art.topics?.map((topic: string, ti: number) => (
                              <span
                                key={ti}
                                className={clsx(
                                  "font-mono text-[9.5px] font-bold uppercase tracking-wide border rounded-[3px] px-1.5 py-0.5 whitespace-nowrap shrink-0",
                                  tagColorClass(topic)
                                )}
                              >
                                {topic}
                              </span>
                            ))}
                          </div>
                          <p className="mt-1 text-[13.5px] leading-[1.5] text-body"><RichText text={art.summary} /></p>
                          <a
                            href={art.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="mt-1 inline-block font-mono text-[11.5px] text-primary hover:underline"
                          >
                            Nguồn: {art.source} <ExternalLink size={10} className="inline -mt-0.5" aria-hidden="true" />
                          </a>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-[13.5px] text-muted-light italic">Không có tin tức {label.toLowerCase()} trong kỳ này.</p>
                  )}
                </div>
              );
            })}
          </section>
        )}

        {/* NGUỒN THAM KHẢO — đứng độc lập cuối báo cáo, không thuộc Phần nào */}
        {report.content["9"] && (
          <section id="section-sources" className="py-5">
            <PartHeading title={report.content["9"].title || "Nguồn tham khảo"} icon={Link2} />
            {report.content["9"].items?.length > 0 ? (
              <ul className="space-y-1.5">
                {report.content["9"].items.map((it: any, i: number) => (
                  <li key={i} className="font-mono text-[12px] leading-[1.5] text-muted-light">
                    [{it.source}]{" "}
                    <a
                      href={it.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-primary hover:underline"
                    >
                      {it.title}
                    </a>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="font-mono text-[12px] text-muted-light">Không có nguồn tin tức trong 48h qua.</p>
            )}
          </section>
        )}

        <footer className="pt-6 text-center border-t border-border">
          <p className="font-mono text-[11px] text-muted-light italic">Báo cáo nội bộ, tổng hợp tự động có kiểm duyệt. Không phải khuyến nghị đầu tư.</p>
        </footer>

      </div>
    </div>
  );
}
