"use client";

import { useState } from "react";
import clsx from "clsx";
import {
  Clock, CalendarRange, Compass, TrendingUp, TrendingDown, Minus, Target, AlertTriangle,
  Sparkles, LineChart, BarChart3, Newspaper, Link2,
  Crosshair, ChevronDown, ChevronUp, Info, ShieldCheck,
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
  "đi ngang": { icon: Minus, className: "text-muted-light border-border bg-surface-alt" },
};

// Nhãn xu hướng dạng mũi tên + chữ cho "Bảng tín hiệu nhanh" (Phần 2) — chỉ là
// cách gọi tên khác của cùng "direction" (tăng/giảm/đi ngang) đã có sẵn trong
// trading_scenarios, KHÔNG phải trường dữ liệu mới/suy diễn thêm.
const TREND_META: Record<string, { arrow: string; label: string; className: string }> = {
  "tăng": { arrow: "↗", label: "NGHIÊNG TĂNG", className: "text-up" },
  "giảm": { arrow: "↘", label: "NGHIÊNG GIẢM", className: "text-down" },
  "đi ngang": { arrow: "↔", label: "GIẰNG CO", className: "text-muted-light" },
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
function RichText({ text }: { text: string }) {
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
const CONCLUSION_TAG_RE = /\*\*(?:Tổng hợp|Kết luận)\s*:?\*\*/;

// Riêng dòng "**Tổng hợp:**" cuối mục 3 (kết luận chung về giá EUA — xem
// _prompt_section3/KẾT LUẬN CHUNG CHO CẢ MỤC "PHÂN TÍCH" trong report_generator.py)
// cần TÁCH RA khỏi các block phân tích để đưa lên đầu báo cáo, làm phần "Nhận
// định" trong khối "🚨 ĐIỂM NHẤN" gộp chung với "Tóm tắt điều hành" — không
// dùng chung CONCLUSION_TAG_RE vì regex đó match luôn cả "**Kết luận:**" của
// từng nhóm nhỏ (những dòng đó vẫn ở lại trong block, vẫn tô nổi bật tại chỗ
// như cũ qua ConclusionAware).
const EUA_SUMMARY_TAG_RE = /\*\*Tổng hợp\s*:?\*\*/;

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
        <p className="text-[13.5px] leading-[1.55] font-bold text-primary-dark">
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
//  - FramedHighlight: khung viền nổi bật có tiêu đề dạng "nhãn" nằm đè lên viền
//    trên (kiểu legend/fieldset) — dùng cho các khối cần nhấn mạnh nhất (Tóm
//    tắt điều hành, Tổng hợp giá EUA).
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
        <div className="text-[17px] sm:text-[20px] font-extrabold uppercase tracking-tight text-primary-dark leading-tight">
          {label}
        </div>
      </div>
      <div className="mt-3 h-[3px] w-full bg-gradient-to-r from-primary via-primary/30 to-transparent rounded-full" />
    </div>
  );
}

// Khung viền + tiêu đề "đè" lên viền trên, canh giữa — tạo điểm nhấn thị giác
// mạnh nhất trong báo cáo, dùng cho 2 khối quan trọng nhất: 🚨 ĐIỂM NHẤN (đầu
// tiên của báo cáo — gộp "Tóm tắt điều hành" + kết luận "Tổng hợp về giá EUA"
// cũ, variant "danger", khung/nhãn màu đỏ #7A1E1E để tách bạch mức độ cảnh
// báo) và TÍN HIỆU HÔM NAY (đầu tiên của Phần 2, variant mặc định "primary").
function FramedHighlight({
  title,
  children,
  className,
  variant = "primary",
}: {
  title: string;
  children: React.ReactNode;
  className?: string;
  variant?: "primary" | "danger";
}) {
  const isDanger = variant === "danger";
  return (
    <div
      className={clsx(
        "report-heading report-frame relative rounded-2xl border-2 px-5 sm:px-7 pt-8 pb-6 shadow-[var(--shadow-soft)]",
        isDanger
          ? "border-[#7A1E1E]/30 bg-gradient-to-b from-[#7A1E1E]/[0.07] to-background"
          : "border-primary/25 bg-gradient-to-b from-tint/70 to-background",
        className
      )}
    >
      <div className="absolute -top-[15px] left-1/2 -translate-x-1/2">
        <span
          className={clsx(
            "inline-flex items-center gap-2 rounded-full text-white px-4 py-[7px] shadow-[0_3px_10px_rgba(15,95,90,0.32)] whitespace-nowrap",
            isDanger ? "bg-[#7A1E1E]" : "bg-primary-dark"
          )}
        >
          <span className="font-extrabold text-[12.5px] sm:text-[13.5px] tracking-wide uppercase">{title}</span>
        </span>
      </div>
      {children}
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
              <span className="font-mono text-[11px] font-bold text-primary-dark leading-none">{ev.datetime_vn || "—"}</span>
            </div>
            <div className="flex flex-col items-center shrink-0">
              <span className={clsx("w-2.5 h-2.5 rounded-full border-2 bg-background mt-3.5 shrink-0", impactDot)} />
              {!isLast && <span className="w-px flex-1 bg-border mt-1" />}
            </div>
            <div className={clsx("flex-1 min-w-0", !isLast && "pb-4")}>
              <div className="flex items-start justify-between gap-3 pt-1">
                <span className="text-[13.5px] text-foreground leading-snug">{ev.event}</span>
                <span className={clsx(
                  "shrink-0 font-mono text-[10px] uppercase px-1.5 py-0.5 rounded border",
                  ev.impact === "Cao" ? "text-down border-down/30 bg-red-50" :
                    ev.impact === "Trung" ? "text-warn border-warn/30 bg-warn-tint" :
                      "text-muted-light border-border"
                )}>{ev.impact}</span>
              </div>
              {ev.outcome && (
                <p className="mt-1.5 text-[12.5px] text-body italic">
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
function BizRecommendationTable({
  heading,
  accent,
  rows,
  columns,
}: {
  heading: string;
  accent: string;
  rows: Record<string, string>[] | undefined;
  columns: { key: string; label: string }[];
}) {
  return (
    <div>
      <h4 className={clsx("font-mono text-[11.5px] font-bold uppercase tracking-widest mb-3", accent)}>{heading}</h4>
      {rows && rows.length > 0 ? (
        <div className="overflow-x-auto border border-border rounded-lg">
          <table className="w-full border-collapse text-[13px]">
            <thead>
              <tr>
                <th className="text-left font-mono text-[10px] uppercase tracking-wider text-primary-dark px-2 sm:px-3 py-2 sm:py-2.5 border-b-2 border-primary/30 border-r border-border bg-tint w-[32px] sm:w-[36px]">#</th>
                {columns.map((col, i) => (
                  <th
                    key={col.key}
                    className={clsx(
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
              {rows.map((row, ri) => (
                <tr key={ri} className="align-top even:bg-surface/60 hover:bg-tint/40 transition-colors">
                  <td className="px-2 sm:px-3 py-2.5 sm:py-3 border-r border-border bg-surface font-mono text-[12px] text-muted-light">{ri + 1}</td>
                  {columns.map((col, i) => (
                    <td
                      key={col.key}
                      className={clsx("px-2 sm:px-3 py-2.5 sm:py-3 text-body leading-[1.55]", i < columns.length - 1 && "border-r border-border")}
                    >
                      <RichText text={row[col.key] || ""} />
                    </td>
                  ))}
                </tr>
              ))}
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

/**
 * Toàn bộ nội dung "tờ báo cáo" (masthead → footer) — dùng chung cho cả màn
 * hình user (chỉ xem báo cáo đã published) và màn hình admin duyệt báo cáo,
 * để không lặp lại JSX giữa 2 nơi.
 */
export function ReportDocument({ report }: { report: Report }) {
  const priceRows = report?.content["2"]?.prices || [];
  // Mục 3 mặc định hiện đủ phân tích (Diễn biến chính -> Cần theo dõi); ẩn được để
  // user chỉ cần xem nhanh bảng Kịch bản chiến lược, không cần đọc hết phần phân tích.
  const [showAnalysis, setShowAnalysis] = useState(true);

  // Rút dòng "**Tổng hợp:**" (kết luận chung giá EUA) ra khỏi các block phân
  // tích để đưa lên đầu báo cáo, làm phần "Nhận định" trong khối "🚨 ĐIỂM
  // NHẤN" gộp chung với "Tóm tắt điều hành" cũ — xem extractEuaSummary ở trên.
  const { cleanedBlocks: analysisBlocks, summary: euaSummary } = extractEuaSummary(report.content["3"]?.analysis_blocks);
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
  const fmtEua = (n: number) => `${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} EUR/tCO₂`;
  const NO_DATA = <span className="text-muted-light">—</span>;
  const quickSignalRows: { label: string; value: React.ReactNode }[] = [
    {
      label: "Xu hướng ngắn hạn (1–2 tuần)",
      value: todaySignal ? (
        <span className={TREND_META[todaySignal.direction]?.className}>
          {TREND_META[todaySignal.direction]?.arrow} {TREND_META[todaySignal.direction]?.label}
          {todaySignal.condition && <> — <RichText text={todaySignal.condition} /></>}
        </span>
      ) : NO_DATA,
    },
    {
      label: "Xu hướng trung hạn (1–3 tháng)",
      value: midTermSignal ? (
        <span className={TREND_META[midTermSignal.direction]?.className}>
          {TREND_META[midTermSignal.direction]?.arrow} {TREND_META[midTermSignal.direction]?.label}
          {midTermSignal.condition && <> — <RichText text={midTermSignal.condition} /></>}
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

  const tickerData = priceRows.map((r: any) => ({
    name: r.name,
    price: r.price,
    unit: '',
    delta: r.dday,
  }));

  return (
    // max-w-[210mm]: khổ A4 — trên màn hình rộng báo cáo hiển thị như 1 trang PDF
    // thật (căn giữa, có viền/bóng đổ), thay vì kéo giãn hết chiều ngang trình
    // duyệt. Khi in (@media print trong globals.css), khổ A4 thật (@page) luôn
    // hẹp hơn 210mm (đã trừ margin) nên max-width này không co hẹp thêm nội dung in.
    <div className="report-shell max-w-[210mm] mx-auto bg-background text-foreground font-sans leading-[1.55] rounded-2xl border border-border shadow-[var(--shadow-soft)] overflow-hidden mb-10">

      {/* Thanh ngày — đứng đầu báo cáo, sát header (đã bỏ banner Stavian/tiêu đề
          phía trên nó). */}
      <div className="w-full bg-[#2f8749] py-2 text-center">
        <div className="text-[13px] sm:text-[15px] font-bold text-white">
          {formatVietnameseDate(report.report_date)}
        </div>
      </div>

      {/* Ticker — full-bleed edge to edge inside the card */}
      <div className="border-b border-border overflow-hidden whitespace-nowrap bg-surface ticker group">
        <div className="ticker-track">
          {/* Render twice for loop */}
          {[...tickerData, ...tickerData].map((t, i) => (
            <div key={i} className="font-mono text-[12.5px] px-6 flex items-center gap-2 border-r border-border text-body">
              <span>{t.name}</span>
              <b className="text-foreground font-semibold">{t.price} {t.unit}</b>
              <span className={t.delta === "-" ? "text-muted-light" : isPositiveDelta(t.delta) ? "text-up" : "text-down"}>{t.delta}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="px-6 sm:px-10 pt-5 pb-10">

        {/* 🚨 ĐIỂM NHẤN — đóng khung đỏ nổi bật, đứng đầu tiên của báo cáo
            (thay cho vị trí "Tóm tắt điều hành" cũ): gộp danh sách yếu tố nổi
            bật trong ngày (nội dung y hệt "Tóm tắt điều hành" cũ) với Nhận
            định — kết luận của "Tổng hợp về giá EUA" cũ (dòng "**Tổng
            hợp:**" cuối Mục 3, xem extractEuaSummary ở trên). */}
        <section className="py-5">
          <FramedHighlight title="🚨 ĐIỂM NHẤN" variant="danger">
            <ul className="list-none">
              {report.content["1"]?.bullets?.map((bullet: any, i: number) => {
                const b: string = typeof bullet === "string" ? bullet : bullet.text || "";
                const isMatch = b.includes(':');
                const tag = stripMarkdown(isMatch ? b.split(':')[0] : 'Note');
                const text = isMatch ? b.split(':').slice(1).join(':') : b;
                const sourceName = typeof bullet === "object" ? bullet.source_name : null;
                const sourceUrl = typeof bullet === "object" ? bullet.source_url : null;

                return (
                  <li key={i} className="py-2.5 border-t border-border-soft text-[14.5px] first:border-t-0 first:pt-0">
                    <p className="text-foreground leading-[1.55]">
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
                        Nguồn: {sourceName} ↗
                      </a>
                    )}
                  </li>
                );
              })}
            </ul>

            {euaSummary && (
              <div className="mt-4 pt-3 border-t border-[#7A1E1E]/20">
                <h4 className="font-mono text-[11px] font-bold uppercase tracking-widest text-[#7A1E1E] mb-1.5">Nhận định</h4>
                <p className="text-[14.5px] sm:text-[15.5px] leading-[1.55] font-bold text-[#7A1E1E] text-center sm:text-left">
                  <RichText text={euaSummary} />
                </p>
              </div>
            )}
          </FramedHighlight>
        </section>

        {/* PHẦN 1 — TIN TỨC CHÍNH / NỔI BẬT TRONG NGÀY */}
        <section className="py-5">
          <PartHeading eyebrow="Phần 1" title="Tổng quan giá thị trường" icon={LineChart} />

          <div className="flex flex-col lg:flex-row gap-4 items-stretch">
            <div className="print-keep-together lg:flex-[1.6] bg-background border border-border rounded-lg pt-2.5 pb-2 px-3 sm:p-4">
              <div className="flex justify-between font-mono text-[11px] text-muted-light mb-1.5 uppercase tracking-wider">
                <b className="text-label font-sans normal-case text-[13px]">EUA Dec-26 · Nến 30 ngày</b>
                <span>EUR/tCO₂e</span>
              </div>
              <CandlestickChart report={report} />
            </div>

            {report.content["2"]?.key_facts && (
              <div className="lg:flex-1 lg:min-w-[200px] flex flex-col justify-center border-l-2 border-primary bg-tint/40 rounded-r-lg px-3.5 py-2.5">
                <h4 className="font-mono text-[10.5px] font-bold uppercase tracking-widest text-primary-dark mb-1">Số liệu chính</h4>
                <div className="space-y-1 text-[13px] leading-snug text-body">
                  {report.content["2"].key_facts
                    .split(/(?<=\.)\s+/)
                    .filter((s: string) => s.trim())
                    .map((s: string, i: number) => (
                      <p key={i}><RichText text={s} /></p>
                    ))}
                </div>
              </div>
            )}
          </div>

          <div className="print:break-inside-avoid mt-5">
            <SubHeading>Bảng giá nhanh</SubHeading>
            {report.content["2"]?.price_timestamp && (
              <p className="font-mono text-[11px] text-muted-light -mt-3 mb-4">{report.content["2"].price_timestamp}</p>
            )}

            {/* Bảng giá full-width */}
            <div className="overflow-x-auto">
              <table className="w-full table-fixed border-collapse font-mono text-[13.5px] sm:text-[12.5px]">
                <thead>
                  <tr>
                    <th className="w-[20%] text-left text-primary-dark font-bold text-[11px] uppercase tracking-wider px-1.5 sm:px-2 py-1.5 border-b-2 border-primary/30 border-r border-primary/15 bg-tint">Hợp đồng</th>
                    <th className="w-[15%] text-center text-primary-dark font-bold text-[11px] uppercase tracking-wider px-1.5 sm:px-2 py-1.5 border-b-2 border-primary/30 border-r border-primary/15 bg-tint">Giá</th>
                    <th className="w-[65%] text-left text-primary-dark font-bold text-[11px] uppercase tracking-wider px-1.5 sm:px-2 py-1.5 border-b-2 border-primary/30 bg-tint">Ghi chú</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {priceRows.map((r: any, i: number) => {
                    const [rawPriceNumber, ...priceUnitParts] = String(r.price || "").split(" ");
                    const priceNumber = formatCompactPriceNumber(rawPriceNumber);
                    const priceUnit = priceUnitParts.join(" ");
                    return (
                      <tr key={i} className="even:bg-surface/60 hover:bg-tint/40 transition-colors">
                        <td className="px-1.5 sm:px-2 py-1.5 border-r border-border font-sans font-semibold text-label">
                          {r.source_url ? (
                            <a
                              href={r.source_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-primary hover:underline"
                            >
                              {r.name}
                            </a>
                          ) : (
                            r.name
                          )}
                        </td>
                        <td className="px-1.5 sm:px-2 py-1.5 border-r border-border text-center">
                          <div className="flex flex-col items-center leading-tight">
                            <span className="break-words tabular-nums">{priceNumber}</span>
                            {priceUnit && <span className="text-[10px] text-muted-light break-words">{priceUnit}</span>}
                          </div>
                        </td>
                        <td className="px-1.5 sm:px-2 py-1.5 font-sans text-[12px] text-body">
                          {(r.dday !== "-" || r.dweek !== "-") && (
                            <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 font-mono text-[11px] mb-1">
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
                            </div>
                          )}
                          {r.note}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </section>

        {/* PHẦN 2 — PHÂN TÍCH VÀ KHUYẾN NGHỊ GIAO DỊCH */}
        <section className="py-5">
          <PartHeading eyebrow="Phần 2" title="Phân tích và khuyến nghị giao dịch" icon={BarChart3} />

          {/* TÍN HIỆU HÔM NAY — nội dung đầu tiên của Phần 2: chiến lược trading
              cho phiên hôm đó, rút gọn còn đúng 3 phần Hành động / Cơ sở / Độ
              tin cậy — tái dùng kịch bản "ngắn hạn" đã có ở Mục 3
              (trading_scenarios), không cần trường dữ liệu mới từ backend. */}
          {todaySignal && (
            <div className="mb-6">
              <FramedHighlight title="TÍN HIỆU HÔM NAY">
                <div className="space-y-3.5">
                  <div>
                    <h4 className="font-mono text-[11px] font-bold uppercase tracking-widest text-primary-dark mb-1">Hành động</h4>
                    <p className="text-[14.5px] leading-[1.6] font-bold text-label">
                      {todaySignal.trading_strategy ? <RichText text={todaySignal.trading_strategy} /> : <span className="text-muted-light font-normal">—</span>}
                    </p>
                  </div>
                  <div>
                    <h4 className="font-mono text-[11px] font-bold uppercase tracking-widest text-primary-dark mb-1">Cơ sở</h4>
                    <p className="text-[13.5px] leading-[1.55] text-body">
                      {todaySignal.condition ? <RichText text={todaySignal.condition} /> : <span className="text-muted-light">—</span>}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <h4 className="font-mono text-[11px] font-bold uppercase tracking-widest text-primary-dark shrink-0">Độ tin cậy</h4>
                    {todaySignal.probability ? (
                      <span className={clsx(
                        "inline-block font-mono text-[11px] uppercase tracking-wider rounded px-2 py-0.5 border whitespace-nowrap",
                        todaySignal.probability === "Cao" ? "text-up border-up/30 bg-up/10" :
                          todaySignal.probability === "Thấp" ? "text-muted-light border-border" :
                            "text-warn border-warn/30 bg-warn-tint"
                      )}>{todaySignal.probability}</span>
                    ) : <span className="text-muted-light text-[13.5px]">—</span>}
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
              <table className="w-full border-collapse text-[13px]">
                <thead>
                  <tr>
                    <th className="text-left font-mono text-[10px] uppercase tracking-wider text-primary-dark px-3 py-2.5 border-b-2 border-primary/30 border-r border-border bg-tint w-[42%] sm:w-[34%]">Chỉ số</th>
                    <th className="text-left font-mono text-[10px] uppercase tracking-wider text-primary-dark px-3 py-2.5 border-b-2 border-primary/30 bg-tint">Giá trị</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {quickSignalRows.map((row, i) => (
                    <tr key={i} className="align-top even:bg-surface/60">
                      <td className="px-3 py-2.5 border-r border-border font-sans font-semibold text-label">{row.label}</td>
                      <td className="px-3 py-2.5 leading-[1.55] text-body">{row.value}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

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
                          const dashMatch = trimmed.match(/^[-–—]\s+/);
                          if (!dashMatch) {
                            return <ConclusionAware key={j} text={line} className="text-[14px] leading-[1.55] text-body" />;
                          }
                          return (
                            <div key={j} className="flex gap-2.5">
                              <span className="mt-[9px] w-1.5 h-1.5 rounded-full bg-black shrink-0" aria-hidden="true" />
                              <div className="flex-1 min-w-0">
                                <ConclusionAware text={trimmed.slice(dashMatch[0].length)} className="text-[14px] leading-[1.55] text-body" />
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ))}

                  {report.content["3"].correlation_analysis && (
                    <div className="border-l-2 border-primary bg-tint/40 rounded-r-lg pl-4 pr-4 py-3 my-5 space-y-2">
                      <h4 className="font-mono text-[11.5px] font-bold uppercase tracking-widest text-primary-dark mb-1.5">Chuỗi Logic: Gas + Coal + Power → EUA</h4>
                      {(report.content["3"].correlation_analysis.gas_comment || report.content["3"].correlation_analysis.gas_coal_power) && (
                        <div className="space-y-2.5 mb-1">
                          {report.content["3"].correlation_analysis.gas_comment && (
                            <p className="text-[13.5px] leading-[1.55] text-body"><b className="text-primary-dark font-bold">Gas:</b> <RichText text={report.content["3"].correlation_analysis.gas_comment} /></p>
                          )}
                          {report.content["3"].correlation_analysis.coal_comment && (
                            <p className="text-[13.5px] leading-[1.55] text-body"><b className="text-primary-dark font-bold">Than:</b> <RichText text={report.content["3"].correlation_analysis.coal_comment} /></p>
                          )}
                          {report.content["3"].correlation_analysis.power_comment && (
                            <p className="text-[13.5px] leading-[1.55] text-body"><b className="text-primary-dark font-bold">Điện Đức:</b> <RichText text={report.content["3"].correlation_analysis.power_comment} /></p>
                          )}
                        </div>
                      )}
                      <ConclusionAware
                        text={report.content["3"].correlation_analysis.fuel_switching_chain || report.content["3"].correlation_analysis.gas_coal_power}
                        className="text-[14px] leading-[1.55] text-body"
                      />
                      <div className="rounded-md border border-primary/30 bg-tint/60 px-3 py-2">
                        <p className="text-[14px] leading-[1.55] font-bold text-primary-dark">
                          <RichText text={report.content["3"].correlation_analysis.eua_conclusion} />
                        </p>
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
          )}

          {/* Động lực thị trường — chuyển từ Bảng giá nhanh (Phần 1) sang đây */}
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
                          <p className="text-[13px] leading-[1.55] text-body">
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
                              Nguồn: {d.source_name} ↗
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
                          <p className="text-[13px] leading-[1.55] text-body">
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
                              Nguồn: {d.source_name} ↗
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

          {/* Kịch bản chiến lược */}
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
                label: "Xác suất / Chiều giá",
                render: (sc) => {
                  const dirMeta = DIRECTION_META[sc.direction];
                  const DirIcon = dirMeta?.icon;
                  return (
                    <div className="flex flex-col gap-1.5 items-start">
                      {sc.probability && (
                        <span className={clsx(
                          "font-mono text-[10px] uppercase tracking-wider rounded px-1.5 py-0.5 border",
                          sc.probability === "Cao" ? "text-up border-up/30 bg-up/10" :
                            sc.probability === "Thấp" ? "text-muted-light border-border" :
                              "text-warn border-warn/30 bg-warn-tint"
                        )}>Xác suất: {sc.probability}</span>
                      )}
                      {dirMeta && (
                        <span className={clsx("flex items-center gap-1 font-mono text-[10px] uppercase tracking-wider rounded px-1.5 py-0.5 border", dirMeta.className)}>
                          <DirIcon size={11} /> {sc.direction}
                        </span>
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
                <SubHeading>Kịch bản chiến lược</SubHeading>

                {/* Mobile: mỗi khung thời gian là 1 card xếp dọc (label/giá trị theo
                      hàng) thay vì bảng nhiều cột — không đủ chỗ ngang trên màn hình hẹp.
                      Từ sm+ (kể cả khi in/xuất PDF) dùng bảng % width bên dưới — không cần
                      fallback card cho print nữa vì bảng đã tự co vừa khổ giấy, không còn
                      bị tràn/cắt chữ như trước. */}
                <div className="sm:hidden space-y-4">
                  {columns.map(h => {
                    const meta = HORIZON_META[h];
                    const Icon = meta.icon;
                    const sc = byHorizon[h];
                    return (
                      <div key={h} className="print-keep-together border border-border rounded-lg overflow-hidden">
                        <div className={clsx("flex items-center gap-1.5 font-mono text-[11px] font-bold uppercase tracking-wider px-3 py-2", meta.iconBg)}>
                          <Icon size={13} /> {h}
                        </div>
                        <div className="divide-y divide-border">
                          {ROWS.map((row, ri) => {
                            const RowIcon = row.icon;
                            return (
                              <div key={ri} className={clsx("px-3 py-2.5", row.highlight && "bg-primary/[0.06] border-l-2 border-primary")}>
                                <div className={clsx(
                                  "flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider mb-1",
                                  row.highlight ? "text-primary-dark font-bold" : "text-primary-dark"
                                )}>
                                  {RowIcon && <RowIcon size={12} className="shrink-0" />}
                                  {row.label}
                                </div>
                                <div className="text-[13px] text-body leading-[1.55]">
                                  {sc ? row.render(sc) : <span className="text-muted-light">—</span>}
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
                                "px-2 sm:px-3 py-2.5 sm:py-3 leading-[1.55] break-words",
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
                  <p className="text-[12.5px] leading-[1.55] text-body">
                    <b className="text-label">Lưu ý:</b> Đây là các chiến lược dựa trên phân tích của Jenny, chỉ mang tính chất tham khảo.
                    Người đọc cần xem xét kỹ, đối chiếu với bối cảnh thực tế và khẩu vị rủi ro của mình trước khi ra quyết định —
                    không phải khuyến nghị đầu tư/giao dịch trực tiếp.
                  </p>
                </div>
              </div>
            );
          })()}

          {/* SECTIONS 4, 5 (text/bullets) — Cập nhật tín chỉ carbon & CBAM / Tín hiệu liên thị trường */}
          {["4", "5"].map(key => {
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
                          <ConclusionAware text={b} className="text-[14px] leading-[1.55] text-body" />
                          {sourceName && (
                            <a
                              href={sourceUrl}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="mt-1 inline-block font-mono text-[11px] text-primary hover:underline"
                            >
                              Nguồn: {sourceName} ↗
                            </a>
                          )}
                        </div>
                      );
                    }}
                  />
                ) : (
                  <ConclusionAware text={section.text} className="text-[14px] leading-[1.55] text-body" />
                )}
              </div>
            );
          })}

          {/* Quan điểm trái chiều đáng chú ý */}
          {report.content["7"] && (
            <div className="mb-6">
              <SubHeading>{report.content["7"].title}</SubHeading>
              {report.content["7"].points?.length > 0 ? (
                <div className="space-y-5">
                  {report.content["7"].points.map((pt: any, i: number) => (
                    <div key={i} className="pb-5 border-b border-border-soft last:border-b-0 last:pb-0">
                      <p className="text-[14px] leading-[1.55] text-body"><RichText text={pt.viewpoint} /></p>
                      {pt.source_url && (
                        <a
                          href={pt.source_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="mt-2 inline-block font-mono text-[11.5px] text-primary hover:underline"
                        >
                          Nguồn: {pt.source_name} ↗
                        </a>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-[14px] text-muted-light italic">{report.content["7"].text}</p>
              )}
            </div>
          )}

          {/* Lịch sự kiện 7 ngày tới */}
          {report.content["8"] && (
            <div className="mb-6">
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
                  <p key={i} className="text-[14px] leading-[1.55] text-body"><RichText text={b} /></p>
                ))
              )}
            </div>
          )}

          {/* Gợi ý kinh doanh & giải pháp cho SIM */}
          {report.content["biz"] && (
            <div className="mb-2">
              <SubHeading>{report.content["biz"].title}</SubHeading>
              <div className="flex flex-col gap-6">
                <BizRecommendationTable
                  heading="Ngắn hạn"
                  accent="text-label"
                  rows={report.content["biz"].short_term}
                  columns={[
                    { key: "trigger", label: "Tình huống kích hoạt" },
                    { key: "action", label: "Hành động đề xuất" },
                    { key: "reason", label: "Lý do" },
                  ]}
                />
                <BizRecommendationTable
                  heading="Dài hạn"
                  accent="text-primary"
                  rows={report.content["biz"].long_term}
                  columns={[
                    { key: "opportunity", label: "Cơ hội" },
                    { key: "solution", label: "Giải pháp đề xuất" },
                    { key: "expectation", label: "Kỳ vọng" },
                  ]}
                />
              </div>
            </div>
          )}
        </section>

        {/* PHẦN 3 — CHI TIẾT CÁC TIN TỨC CHÍNH */}
        {report.content["6"] && (
          <section className="py-5">
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
                          <p className="text-[14px] font-semibold text-label leading-snug">{i + 1}. {art.title}</p>
                          <p className="mt-1 text-[13.5px] leading-[1.55] text-body"><RichText text={art.summary} /></p>
                          <a
                            href={art.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="mt-1 inline-block font-mono text-[11.5px] text-primary hover:underline"
                          >
                            Nguồn: {art.source} ↗
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
          <section className="py-5">
            <PartHeading title={report.content["9"].title || "Nguồn tham khảo"} icon={Link2} />
            {report.content["9"].items?.length > 0 ? (
              <ul className="space-y-1.5">
                {report.content["9"].items.map((it: any, i: number) => (
                  <li key={i} className="font-mono text-[12px] leading-[1.6] text-muted-light">
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
