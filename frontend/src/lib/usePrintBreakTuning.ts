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
  const keepSmallParts = (el: HTMLElement) => {
    for (const child of Array.from(el.children) as HTMLElement[]) {
      if (child.getBoundingClientRect().height <= limit && !TABLE_GROUPS.has(child.tagName)) child.dataset.printKeep = "";
      else keepSmallParts(child);
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
