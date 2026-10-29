"use client";

import { useEffect, type RefObject } from "react";

// Ngắt trang khi in/PDF: CSS chỉ có "break-inside: avoid" (giữ trọn khối) hoặc không — không có kiểu "giữ trọn nếu
// khối nhỏ". Khối lớn mà giữ trọn thì không vừa phần còn lại của trang sẽ bị đẩy nguyên sang trang sau, để trống
// cả mảng cuối trang. Hook này đo các khối/thẻ theo khổ in: khối cao hơn SPLIT_RATIO trang gắn `data-print-split`
// → CSS (globals.css) cho phép ngắt GIỮA các phần con của nó; khối nhỏ gắn `data-print-keep` → giữ trọn, chỉ sang
// trang mới khi phần còn lại của trang không đủ chứa nó. Khối gắn sẵn `data-print-whole` (vd biểu đồ nến) luôn giữ trọn dù
// cao bao nhiêu — tách ra thì tiêu đề khung ở trang trước, biểu đồ ở trang sau.

// Vùng in A4 trong @page (globals.css): 210 − 2×10 mm ngang, 297 − 2×12 mm dọc, 1mm = 96/25,4 px.
const PRINT_WIDTH_PX = Math.round((190 * 96) / 25.4);
const PRINT_PAGE_HEIGHT_PX = Math.round((273 * 96) / 25.4);
// Khối cao hơn ngưỡng này (theo chiều cao 1 trang) được phép ngắt bên trong — khoảng trắng tối đa cuối trang do đẩy
// khối ≈ ngưỡng này. 0,18 ≈ 1 ô số liệu/thẻ tin/hàng bảng; lưới ô, bảng, thẻ kịch bản lớn hơn thì ngắt giữa các phần con.
// (Thử 0,1 khi làm PDF gọn 29/10/2026: không bớt trang mà thẻ nhỏ — ô "Dự báo trước" — bị cắt ngang khung.)
const SPLIT_RATIO = 0.18;
// Khối gắn data-print-whole chỉ được giữ trọn khi cao không quá tỉ lệ này của trang.
const WHOLE_MAX_RATIO = 0.9;
const TABLE_GROUPS = new Set(["THEAD", "TBODY", "TFOOT"]);
// Khối cần xét: đang giữ trọn khi in (Tailwind print:break-inside-avoid + quy tắc chung trong globals.css) và các
// khung thẻ có viền — thẻ nhỏ thì giữ trọn, không để bị cắt ngang khung.
const BLOCK_SELECTOR = [
  '[class*="print:break-inside-avoid"]',
  ".break-inside-avoid-print",
  ".rounded-md.border",
  ".rounded-lg.border",
  ".rounded-xl.border",
  "article",
  "li",
  "tr",
].join(",");

export function resetPrintBreaks(shell: HTMLElement | null) {
  shell?.querySelectorAll<HTMLElement>("[data-print-split], [data-print-keep]").forEach((el) => {
    delete el.dataset.printSplit;
    delete el.dataset.printKeep;
  });
}

// Đo + gắn data-print-split/keep cho các khối trong `shell` theo chiều cao vùng in 1 trang `pageHeightPx` (px hiển
// thị — getBoundingClientRect đã tính cả zoom của trang export). Trang export (/reports/[date]/export) gọi trực tiếp
// với khổ người dùng chọn, sau khi bố cục/biểu đồ đã ổn định.
export function tunePrintBreaks(shell: HTMLElement | null, pageHeightPx: number = PRINT_PAGE_HEIGHT_PX) {
  if (!shell) return;
  const limit = pageHeightPx * SPLIT_RATIO;
  // Khối lớn được phép ngắt → phần con nhỏ bên trong (ô số liệu, khung con của thẻ) vẫn giữ trọn, phần con lớn
  // thì xét tiếp con của nó — để chỗ ngắt rơi vào khe giữa các phần con, không cắt ngang 1 ô/khung.
  // Nhóm hàng của bảng (thead/tbody/tfoot) không gắn giữ trọn — Chrome đẩy nguyên cả bảng sang trang sau thay vì
  // ngắt giữa các hàng; xét tiếp từng hàng (tr đã giữ trọn bằng CSS).
  // Phần con lớn cũng được ngắt bên trong (data-print-split) — ô bảng (td) cao hơn ngưỡng (hàng chi tiết gợi ý kinh doanh)
  // vốn bị CSS giữ trọn (tr, td: break-inside avoid) nên cả nhóm hàng bị đẩy sang trang sau.
  const keepSmallParts = (el: HTMLElement) => {
    for (const child of Array.from(el.children) as HTMLElement[]) {
      if (child.dataset.printWhole !== undefined) continue; // giữ trọn / đã xét ở bước data-print-whole
      if (child.getBoundingClientRect().height <= limit && !TABLE_GROUPS.has(child.tagName)) {
        child.dataset.printKeep = "";
      } else {
        if (!TABLE_GROUPS.has(child.tagName)) child.dataset.printSplit = "";
        keepSmallParts(child);
      }
    }
  };
  resetPrintBreaks(shell);
  // Khối "luôn giữ trọn" mà gần bằng/cao hơn 1 trang (trang ngang 16:9 thấp) thì giữ trọn chỉ đẩy nó sang trang sau
  // rồi vẫn bị cắt — cho ngắt như khối lớn thường.
  shell.querySelectorAll<HTMLElement>("[data-print-whole]").forEach((el) => {
    if (el.getBoundingClientRect().height > pageHeightPx * WHOLE_MAX_RATIO) {
      el.dataset.printSplit = "";
      keepSmallParts(el);
    }
  });
  shell.querySelectorAll<HTMLElement>(BLOCK_SELECTOR).forEach((el) => {
    if (el.dataset.printKeep !== undefined || el.dataset.printSplit !== undefined) return;
    if (el.closest("[data-print-whole]:not([data-print-split])")) return;
    if (el.getBoundingClientRect().height <= limit) {
      el.dataset.printKeep = "";
    } else {
      el.dataset.printSplit = "";
      keepSmallParts(el);
    }
  });
}

// Biểu đồ nến khi xuất PDF (CandlestickChart, ReportDocument.tsx) nhận chiều cao ép (px bố cục) qua sự kiện này; null =
// trả về chiều cao mặc định.
export const CHART_FIT_EVENT = "report-chart-fit";
// Hạ biểu đồ tối đa còn tỉ lệ này của chiều cao gốc — thấp hơn nữa thì thà sang trang sau.
const CHART_FIT_MIN_RATIO = 0.65;

// Khối biểu đồ nến (data-chart-fit, giữ trọn khi in) — hạ chiều cao biểu đồ khi:
//  (1) khối cao hơn WHOLE_MAX_RATIO trang (trang ngang thấp) — không thì khối bị cho ngắt bên trong, biểu đồ (không cắt được)
//      bị đẩy sang trang sau và vẽ đè lên khối phía sau;
//  (2) khối vắt qua cuối TRANG 1 — cho vừa phần còn lại của trang 1 thay vì đẩy cả khối sang trang 2 và bỏ trắng cuối trang 1
//      (khối mở đầu dài ngắn khác nhau theo báo cáo, có/không có ô "Điều đáng quan tâm nhất"). Đo theo dòng chảy liên tục:
//      khối bắt đầu trong trang 1 thì mọi thứ phía trước nằm trọn trang 1 (không ngắt trang nào) nên vị trí đo = vị trí thật.
// (2) hạ dần (ô tóm tắt cạnh biểu đồ co theo chậm hơn) tối đa còn CHART_FIT_MIN_RATIO chiều cao gốc; vẫn tràn → trả lại
// chiều cao sau (1).
export async function fitChartToPage(shell: HTMLElement | null, pageHeightPx: number, settle: () => Promise<unknown>) {
  const block = shell?.querySelector<HTMLElement>("[data-chart-fit]");
  const svg = block?.querySelector<SVGSVGElement>("svg.candle-chart");
  if (!shell || !block || !svg) return;
  const setHeight = async (h: number) => {
    window.dispatchEvent(new CustomEvent(CHART_FIT_EVENT, { detail: h }));
    await settle();
  };
  // Hạ biểu đồ để khối thấp đi `excessPx` (px hiển thị), không dưới `minH` (đơn vị viewBox = px bố cục).
  const shrink = async (excessPx: number, minH: number) => {
    const viewH = svg.viewBox.baseVal.height;
    const pxPerUnit = svg.getBoundingClientRect().height / viewH;
    if (!(pxPerUnit > 0) || viewH <= minH) return false;
    await setHeight(Math.max(minH, Math.floor(viewH - (excessPx + 6) / pxPerUnit)));
    return true;
  };
  const startH = svg.viewBox.baseVal.height;
  const tooTall = block.getBoundingClientRect().height - pageHeightPx * (WHOLE_MAX_RATIO - 0.05);
  if (tooTall > 0) await shrink(tooTall, Math.ceil(startH * 0.4));
  const top = block.getBoundingClientRect().top - shell.getBoundingClientRect().top;
  const overflow = () => top + block.getBoundingClientRect().height - pageHeightPx;
  if (top >= pageHeightPx || overflow() <= 0) return;
  const fittedH = svg.viewBox.baseVal.height;
  const minH = Math.ceil(startH * CHART_FIT_MIN_RATIO);
  for (let i = 0; i < 3 && overflow() > 0; i++) {
    if (!(await shrink(overflow(), minH))) break;
  }
  if (overflow() > 0) await setHeight(fittedH);
}

// In bằng trình duyệt (Ctrl+P) — khổ A4 dọc mặc định. `enabled=false` khi trang export tự gọi tunePrintBreaks.
export function usePrintBreakTuning(shellRef: RefObject<HTMLElement | null>, enabled = true) {
  useEffect(() => {
    if (!enabled) return;
    const tune = () => {
      const shell = shellRef.current;
      if (!shell) return;
      // Đo theo đúng bề rộng vùng in (container query @container/report theo bề rộng khung báo cáo → bố cục cột như
      // bản in), rồi trả lại bề rộng cũ — chỉ giữ lại thuộc tính data-*.
      const prevWidth = shell.style.width;
      const prevMax = shell.style.maxWidth;
      shell.style.width = `${PRINT_WIDTH_PX}px`;
      shell.style.maxWidth = "none";
      tunePrintBreaks(shell);
      shell.style.width = prevWidth;
      shell.style.maxWidth = prevMax;
    };
    const reset = () => resetPrintBreaks(shellRef.current);
    const mql = window.matchMedia("print");
    const onMedia = (e: MediaQueryListEvent) => (e.matches ? tune() : reset());
    window.addEventListener("beforeprint", tune);
    window.addEventListener("afterprint", reset);
    mql.addEventListener("change", onMedia);
    if (mql.matches) tune();
    return () => {
      window.removeEventListener("beforeprint", tune);
      window.removeEventListener("afterprint", reset);
      mql.removeEventListener("change", onMedia);
    };
  }, [shellRef, enabled]);
}
