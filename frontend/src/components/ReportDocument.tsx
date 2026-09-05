"use client";

import { useState } from "react";
import clsx from "clsx";
import Image from "next/image";
import {
  Clock, CalendarRange, Compass, TrendingUp, TrendingDown, Minus, Target, AlertTriangle, ListChecks,
  Sparkles, LineChart, BarChart3, FileText, AlignLeft, Newspaper, Scale, CalendarDays, Lightbulb, Link2,
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
        <p className="text-[13.5px] leading-relaxed font-bold text-primary-dark">
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

// Icon riêng cho từng section giúp người đọc nhận diện ngay loại nội dung
// (giá/tin tức/lịch/gợi ý...) thay vì chỉ dựa vào số thứ tự.
const SECTION_ICON: Record<string, typeof Sparkles> = {
  "01": Sparkles,
  "02": LineChart,
  "03": BarChart3,
  "04": FileText,
  "05": AlignLeft,
  "06": Newspaper,
  "07": Scale,
  "08": CalendarDays,
  "SIM": Lightbulb,
  "09": Link2,
};

// Đầu mục section: icon nổi bật trong khối bo tròn để phân biệt rõ ràng từng
// phần trong báo cáo dài, tiêu đề lớn/đậm hơn để tạo phân cấp thị giác rõ.
function SectionHeading({ number, title }: { number: string; title: string }) {
  const Icon = SECTION_ICON[number] ?? Sparkles;
  return (
    <div className="flex items-center gap-3 mb-4">
      <span className="flex items-center justify-center w-9 h-9 rounded-xl bg-tint text-primary-dark shrink-0">
        <Icon size={18} strokeWidth={2.25} />
      </span>
      <div className="flex items-baseline gap-2 flex-wrap">
        <span className="text-[20px] font-extrabold tracking-tight text-foreground">{title}</span>
        <span className="font-mono text-[10px] font-semibold text-muted-light tracking-wider">{number}</span>
      </div>
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
                      className={clsx("px-2 sm:px-3 py-2.5 sm:py-3 text-body leading-relaxed", i < columns.length - 1 && "border-r border-border")}
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

/**
 * Toàn bộ nội dung "tờ báo cáo" (masthead → footer) — dùng chung cho cả màn
 * hình user (chỉ xem báo cáo đã published) và màn hình admin duyệt báo cáo,
 * để không lặp lại JSX giữa 2 nơi.
 */
export function ReportDocument({ report }: { report: Report }) {
  const priceRows = report?.content["2"]?.prices || [];

  const tickerData = priceRows.map((r: any) => ({
    name: r.name,
    price: r.price,
    unit: '',
    delta: r.dday,
  }));

  return (
    <div className="bg-background text-foreground font-sans leading-relaxed rounded-2xl border border-border shadow-[var(--shadow-soft)] overflow-hidden mb-10">

      {/* Masthead — nền riêng (brand dark) để tách rõ khỏi phần nội dung trắng bên dưới.
          Ngày báo cáo được cân bằng thị giác với title bên trái: cùng cỡ chữ/độ đậm,
          chỉ khác màu (accent) để nổi bật và dễ nhận diện ngay lập tức. */}
      <div className="bg-primary-dark px-6 sm:px-10 pt-6 pb-6">
        <div className="flex justify-between items-center flex-wrap gap-4">
          <div className="flex items-center gap-3">
            <Image src="/stavian_logo.png" alt="Stavian" width={337} height={191} className="h-9 w-auto block" />
            <div className="w-[1px] h-7 bg-white/25" />
            <div className="text-2xl sm:text-3xl font-extrabold tracking-tight text-white leading-none">
              Daily Carbon <span className="text-accent">Intelligence</span>
            </div>
          </div>
          <div className="text-right">
            <div className="font-mono text-[10px] font-semibold uppercase tracking-widest text-white/50 mb-1">Báo cáo ngày</div>
            <div className="text-2xl sm:text-3xl font-extrabold tracking-tight text-accent leading-none">{report.report_date}</div>
            <div className="font-mono text-[11px] text-white/60 mt-1.5">Giá chốt 18:00 CET · Cập nhật 06:30 ICT</div>
          </div>
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
              <span className={isPositiveDelta(t.delta) ? "text-up" : "text-down"}>{t.delta}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="px-6 sm:px-10 pt-5 pb-10">

        {/* SECTION 1 */}
        <section className="py-5">
          <SectionHeading number="01" title="Tóm tắt điều hành" />
          <ul className="list-none">
            {report.content["1"]?.bullets?.map((b: string, i: number) => {
              const isMatch = b.includes(':');
              const tag = stripMarkdown(isMatch ? b.split(':')[0] : 'Note');
              const text = isMatch ? b.split(':').slice(1).join(':') : b;

              return (
                <li key={i} className="py-2.5 border-t border-border text-[14.5px] first:border-t-0">
                  <p className="text-foreground">
                    <span className="font-mono text-[10.5px] text-primary-dark bg-tint border border-primary/20 rounded-[3px] px-1 py-px mr-2">
                      {tag.trim().substring(0, 15)}
                    </span>
                    <RichText text={text.trim()} />
                  </p>
                </li>
              );
            })}
          </ul>
        </section>

        {/* SECTION 2 */}
        <section className="py-5">
          <SectionHeading number="02" title="Bảng giá nhanh" />
          {report.content["2"]?.price_timestamp && (
            <p className="font-mono text-[11px] text-muted-light -mt-3 mb-4">{report.content["2"].price_timestamp}</p>
          )}

          {/* Bảng giá full-width; nến + số liệu chính đứng cùng hàng bên phải trên desktop
              (lg:flex-row), xếp chồng trên mobile (chart trước, số liệu chính sau, giữ nguyên
              thứ tự cũ). table-fixed + width cố định theo %: cột Hợp đồng rộng nhất (tên hợp
              đồng dài như "German Power" không bị ép xuống dòng nhiều), 4 cột còn lại
              (Giá/Δ Ngày/Δ Tuần/Ghi chú) rộng bằng nhau — tránh 1 cột co hẹp bất thường làm
              hàng cao lên, giữ giao diện gọn trên mobile. */}
          <div className="flex flex-col gap-5">
            <div className="overflow-x-auto">
              <table className="w-full table-fixed border-collapse font-mono text-[13.5px] sm:text-[12.5px]">
                <thead>
                  <tr>
                    <th className="w-[20%] text-left text-primary-dark font-bold text-[11px] uppercase tracking-wider px-1.5 sm:px-2.5 py-2 border-b-2 border-primary/30 border-r border-primary/15 bg-tint">Hợp đồng</th>
                    <th className="w-[21%] text-left text-primary-dark font-bold text-[11px] uppercase tracking-wider px-1.5 sm:px-2.5 py-2 border-b-2 border-primary/30 border-r border-primary/15 bg-tint">Giá</th>
                    <th className="w-[21%] text-left text-primary-dark font-bold text-[11px] uppercase tracking-wider px-1.5 sm:px-2.5 py-2 border-b-2 border-primary/30 border-r border-primary/15 bg-tint">Δ Ngày</th>
                    <th className="w-[21%] text-left text-primary-dark font-bold text-[11px] uppercase tracking-wider px-1.5 sm:px-2.5 py-2 border-b-2 border-primary/30 border-r border-primary/15 bg-tint">Δ Tuần</th>
                    <th className="w-[17%] text-left text-primary-dark font-bold text-[11px] uppercase tracking-wider px-1.5 sm:px-2.5 py-2 border-b-2 border-primary/30 bg-tint">Ghi chú</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {priceRows.map((r: any, i: number) => {
                    // "price" từ backend là 1 chuỗi "<số> <đơn vị>" (vd "72.4000 EUR/tCO2e") —
                    // đơn vị thường không có khoảng trắng nên trình duyệt không tự ngắt dòng
                    // được, dễ tràn sang cột Δ Ngày bên cạnh trên màn hình hẹp. Tách riêng số
                    // (dòng trên, rút gọn 2 số thập phân cho ngắn) và đơn vị (dòng dưới, nhỏ
                    // hơn) thay vì hiện chung 1 dòng.
                    const [rawPriceNumber, ...priceUnitParts] = String(r.price || "").split(" ");
                    const priceNumber = formatCompactPriceNumber(rawPriceNumber);
                    const priceUnit = priceUnitParts.join(" ");
                    return (
                      <tr key={i} className="even:bg-surface/60 hover:bg-tint/40 transition-colors">
                        <td className="px-1.5 sm:px-2.5 py-2.5 border-r border-border font-sans font-semibold text-label">{r.name}</td>
                        <td className="px-1.5 sm:px-2.5 py-2.5 border-r border-border align-top">
                          <div className="flex flex-col leading-tight">
                            <span className="break-words">{priceNumber}</span>
                            {priceUnit && <span className="text-[10px] text-muted-light break-words">{priceUnit}</span>}
                          </div>
                        </td>
                        <td className={clsx("px-1.5 sm:px-2.5 py-2.5 border-r border-border break-words", isPositiveDelta(r.dday) ? "text-up" : "text-down")}>{r.dday}</td>
                        <td className={clsx("px-1.5 sm:px-2.5 py-2.5 border-r border-border break-words", isPositiveDelta(r.dweek) ? "text-up" : "text-down")}>{r.dweek}</td>
                        <td className="px-1.5 sm:px-2.5 py-2.5 font-sans text-[12px] text-body">{r.note}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <div className="flex flex-col lg:flex-row gap-4 items-stretch">
              <div className="lg:flex-[1.6] bg-background border border-border rounded-lg pt-2.5 pb-2 px-3 sm:p-4">
                <div className="flex justify-between font-mono text-[11px] text-muted-light mb-1.5 uppercase tracking-wider">
                  <b className="text-label font-sans normal-case text-[13px]">EUA Dec-26 · Nến 30 ngày</b>
                  <span>EUR/tCO₂e</span>
                </div>
                <CandlestickChart report={report} />
              </div>

              {report.content["2"]?.key_facts && (
                <div className="lg:flex-1 lg:min-w-[200px] flex flex-col justify-center border-l-2 border-primary bg-tint/40 rounded-r-lg px-3.5 py-2.5">
                  <h4 className="font-mono text-[10.5px] font-bold uppercase tracking-widest text-primary-dark mb-1">Số liệu chính</h4>
                  <p className="text-[13px] leading-relaxed text-body"><RichText text={report.content["2"].key_facts} /></p>
                </div>
              )}
            </div>
          </div>

          {(report.content["2"]?.market_drivers?.bullish?.length > 0 || report.content["2"]?.market_drivers?.bearish?.length > 0) && (
            <div className="mt-4">
              <h4 className="font-mono text-[10.5px] font-bold uppercase tracking-widest text-label mb-2">Động lực thị trường</h4>
              <div className="grid sm:grid-cols-2 gap-3">
                <div className="border border-up/25 bg-up/[0.04] rounded-lg overflow-hidden">
                  <div className="flex items-center gap-1.5 bg-up/10 text-up font-mono text-[11px] font-bold uppercase tracking-wider px-3 py-2 border-b border-up/20">
                    <TrendingUp size={14} strokeWidth={2.5} /> Động lực tăng
                  </div>
                  <div className="p-3 space-y-3">
                    {report.content["2"].market_drivers.bullish?.length > 0 ? (
                      report.content["2"].market_drivers.bullish.map((d: any, i: number) => (
                        <div key={i}>
                          <p className="text-[13px] leading-relaxed text-body">
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
                          <p className="text-[13px] leading-relaxed text-body">
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
        </section>

        {/* SECTION 3 */}
        {report.content["3"] && (
          <section className="py-5">
            <SectionHeading number="03" title={report.content["3"].title} />

            {report.content["3"].analysis_blocks?.map((block: any, i: number) => (
              <div key={i} className="mb-5">
                <h4 className="font-mono text-[11.5px] font-bold uppercase tracking-widest text-primary mb-1.5">{block.heading}</h4>
                <div className="space-y-1.5">
                  {(block.content || "").split("\n").filter((line: string) => line.trim()).map((line: string, j: number) => (
                    <ConclusionAware key={j} text={line} className="text-[14px] leading-relaxed text-body" />
                  ))}
                </div>
              </div>
            ))}

            {report.content["3"].correlation_analysis && (
              <div className="border-l-2 border-primary bg-tint/40 rounded-r-lg pl-4 pr-4 py-3 my-5 space-y-2">
                <h4 className="font-mono text-[11.5px] font-bold uppercase tracking-widest text-primary-dark mb-1.5">Chuỗi Logic: Gas + Coal + Power → EUA</h4>
                {(report.content["3"].correlation_analysis.gas_comment || report.content["3"].correlation_analysis.gas_coal_power) && (
                  <div className="space-y-2.5 mb-1">
                    {report.content["3"].correlation_analysis.gas_comment && (
                      <p className="text-[13.5px] leading-relaxed text-body"><b className="text-primary-dark font-bold">Gas:</b> <RichText text={report.content["3"].correlation_analysis.gas_comment} /></p>
                    )}
                    {report.content["3"].correlation_analysis.coal_comment && (
                      <p className="text-[13.5px] leading-relaxed text-body"><b className="text-primary-dark font-bold">Than:</b> <RichText text={report.content["3"].correlation_analysis.coal_comment} /></p>
                    )}
                    {report.content["3"].correlation_analysis.power_comment && (
                      <p className="text-[13.5px] leading-relaxed text-body"><b className="text-primary-dark font-bold">Điện Đức:</b> <RichText text={report.content["3"].correlation_analysis.power_comment} /></p>
                    )}
                  </div>
                )}
                <ConclusionAware
                  text={report.content["3"].correlation_analysis.fuel_switching_chain || report.content["3"].correlation_analysis.gas_coal_power}
                  className="text-[14px] leading-relaxed text-body"
                />
                <div className="rounded-md border border-primary/30 bg-tint/60 px-3 py-2">
                  <p className="text-[14px] leading-relaxed font-bold text-primary-dark">
                    <RichText text={report.content["3"].correlation_analysis.eua_conclusion} />
                  </p>
                </div>
              </div>
            )}

            {report.content["3"].trading_scenarios?.length > 0 && (() => {
              const HORIZON_ORDER = ["ngắn hạn", "trung hạn", "dài hạn"];
              const byHorizon: Record<string, any> = {};
              report.content["3"].trading_scenarios.forEach((sc: any) => { byHorizon[sc.horizon] = sc; });
              const columns = HORIZON_ORDER.filter(h => byHorizon[h]);
              const ROWS: { label: string; icon?: typeof Target; render: (sc: any) => React.ReactNode }[] = [
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
                  label: "Vùng giá tham chiếu", icon: Target,
                  render: (sc) => sc.price_zone ? <RichText text={sc.price_zone} /> : <span className="text-muted-light">—</span>,
                },
                { label: "Định giá thị trường", render: (sc) => <RichText text={sc.market_pricing} /> },
                { label: "Rủi ro chính", icon: AlertTriangle, render: (sc) => <span className="text-down"><RichText text={sc.key_risk} /></span> },
                { label: "Kế hoạch hành động", icon: ListChecks, render: (sc) => <RichText text={sc.action_plan} /> },
              ];

              return (
                <div className="mt-5">
                  <h4 className="font-mono text-[11.5px] font-bold uppercase tracking-widest text-label mb-3">Kịch bản chiến lược</h4>

                  {/* Mobile: mỗi khung thời gian là 1 card xếp dọc (label/giá trị theo hàng)
                      thay vì bảng 4 cột — bảng 4 cột luôn cần kéo ngang trên màn hình hẹp,
                      xếp card tránh hẳn thanh cuộn ngang. */}
                  <div className="sm:hidden space-y-4">
                    {columns.map(h => {
                      const meta = HORIZON_META[h];
                      const Icon = meta.icon;
                      const sc = byHorizon[h];
                      return (
                        <div key={h} className="border border-border rounded-lg overflow-hidden">
                          <div className={clsx("flex items-center gap-1.5 font-mono text-[11px] font-bold uppercase tracking-wider px-3 py-2", meta.iconBg)}>
                            <Icon size={13} /> {h}
                          </div>
                          <div className="divide-y divide-border">
                            {ROWS.map((row, ri) => {
                              const RowIcon = row.icon;
                              return (
                                <div key={ri} className="px-3 py-2.5">
                                  <div className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-primary-dark mb-1">
                                    {RowIcon && <RowIcon size={12} className="shrink-0" />}
                                    {row.label}
                                  </div>
                                  <div className="text-[13px] text-body leading-relaxed">
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

                  {/* sm+: giữ bảng so sánh 4 cột như cũ (đủ rộng để không cần kéo ngang) */}
                  <div className="hidden sm:block overflow-x-auto border border-border rounded-lg">
                    <table className="w-full border-collapse text-[13px]">
                      <thead>
                        <tr>
                          <th className="text-left font-mono text-[10px] uppercase tracking-wider text-primary-dark px-2 sm:px-3 py-2 sm:py-2.5 border-b-2 border-primary/30 border-r border-border bg-tint min-w-[120px]">Chỉ tiêu</th>
                          {columns.map(h => {
                            const meta = HORIZON_META[h];
                            const Icon = meta.icon;
                            return (
                              <th key={h} className={clsx("text-left px-2 sm:px-3 py-2 sm:py-2.5 border-b-2 border-border min-w-[200px]", meta.iconBg)}>
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
                            <tr key={ri} className="align-top even:bg-surface/60 hover:bg-tint/40 transition-colors">
                              <td className="px-2 sm:px-3 py-2.5 sm:py-3 border-r border-border bg-surface font-semibold text-label text-[12.5px]">
                                <span className="flex items-center gap-1.5">
                                  {RowIcon && <RowIcon size={13} className="text-primary-dark shrink-0" />}
                                  {row.label}
                                </span>
                              </td>
                              {columns.map(h => (
                                <td key={h} className="px-2 sm:px-3 py-2.5 sm:py-3 text-body leading-relaxed">
                                  {byHorizon[h] ? row.render(byHorizon[h]) : <span className="text-muted-light">—</span>}
                                </td>
                              ))}
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              );
            })()}
          </section>
        )}

        {/* SECTIONS 4, 5 (text/bullets) */}
        {["4", "5"].map(key => {
          const section = report.content[key];
          if (!section) return null;
          return (
            <section key={key} className="py-5">
              <SectionHeading number={`0${key}`} title={section.title} />
              <div className="space-y-2.5">
                {section.bullets ? (
                  section.bullets.map((b: string, i: number) => (
                    <ConclusionAware key={i} text={b} className="text-[14px] leading-relaxed text-body" />
                  ))
                ) : (
                  <ConclusionAware text={section.text} className="text-[14px] leading-relaxed text-body" />
                )}
              </div>
            </section>
          );
        })}

        {/* SECTION 7 — Quan điểm trái chiều */}
        {report.content["7"] && (
          <section className="py-5">
            <SectionHeading number="07" title={report.content["7"].title} />
            {report.content["7"].points?.length > 0 ? (
              <div className="space-y-5">
                {report.content["7"].points.map((pt: any, i: number) => (
                  <div key={i} className="pb-5 border-b border-border-soft last:border-b-0 last:pb-0">
                    <p className="text-[14px] leading-relaxed text-body"><RichText text={pt.viewpoint} /></p>
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
          </section>
        )}

        {/* SECTION 8 — Lịch sự kiện */}
        {report.content["8"] && (
          <section className="py-5">
            <SectionHeading number="08" title={report.content["8"].title} />
            {report.content["8"].events?.length > 0 ? (
              // Lịch dạng timeline tuần tự: mỗi sự kiện là 1 "ô lịch" ngày/giờ nối bằng
              // 1 trục dọc + chấm tròn, thay vì bảng hàng/cột — dễ quét theo trình tự thời gian.
              <div>
                {report.content["8"].events.map((ev: any, i: number) => {
                  const isLast = i === report.content["8"].events.length - 1;
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
            ) : (
              report.content["8"].bullets?.map((b: string, i: number) => (
                <p key={i} className="text-[14px] leading-relaxed text-body"><RichText text={b} /></p>
              ))
            )}
          </section>
        )}

        {/* SECTION BIZ — Gợi ý kinh doanh */}
        {report.content["biz"] && (
          <section className="py-5">
            <SectionHeading number="SIM" title={report.content["biz"].title} />
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
          </section>
        )}

        {/* SECTION 9 — Nguồn */}
        {report.content["9"] && (
          <section className="py-5">
            <SectionHeading number="09" title={report.content["9"].title} />
            {report.content["9"].items?.length > 0 ? (
              <ul className="space-y-1.5">
                {report.content["9"].items.map((it: any, i: number) => (
                  <li key={i} className="font-mono text-[12px] text-muted-light">
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
