"use client";

import { useEffect, type RefObject } from "react";

// Ngắt trang khi in/PDF: CSS chỉ có "break-inside: avoid" (giữ trọn khối) hoặc không — không có kiểu "giữ trọn nếu
// khối nhỏ". Khối lớn mà giữ trọn thì không vừa phần còn lại của trang sẽ bị đẩy nguyên sang trang sau, để trống
// cả mảng cuối trang. Hook này đo các khối/thẻ theo khổ in: khối cao hơn SPLIT_RATIO trang gắn `data-print-split`
// → CSS (globals.css) cho phép ngắt GIỮA các phần con của nó; khối nhỏ gắn `data-print-keep` → giữ trọn, chỉ sang
// trang mới khi phần còn lại của trang không đủ chứa nó.

// Vùng in A4 trong @page (globals.css): 210 − 2×10 mm ngang, 297 − 2×12 mm dọc, 1mm = 96/25,4 px.
const PRINT_WIDTH_PX = Math.round((190 * 96) / 25.4);
const PRINT_PAGE_HEIGHT_PX = Math.round((273 * 96) / 25.4);
// Khối cao hơn ngưỡng này (theo chiều cao 1 trang) được phép ngắt bên trong — khoảng trắng tối đa cuối trang do đẩy
// khối ≈ ngưỡng này. 0,18 ≈ 1 ô số liệu/thẻ tin/hàng bảng; lưới ô, bảng, thẻ kịch bản lớn hơn thì ngắt giữa các phần con.
const SPLIT_RATIO = 0.18;
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

export function usePrintBreakTuning(shellRef: RefObject<HTMLElement | null>) {
  useEffect(() => {
    const tune = () => {
      const shell = shellRef.current;
      if (!shell) return;
      // Đo theo đúng bề rộng vùng in (container query @container/report theo bề rộng khung báo cáo → bố cục cột như
      // bản in), rồi trả lại bề rộng cũ — chỉ giữ lại thuộc tính data-*.
      const prevWidth = shell.style.width;
      const prevMax = shell.style.maxWidth;
      shell.style.width = `${PRINT_WIDTH_PX}px`;
      shell.style.maxWidth = "none";
      const limit = PRINT_PAGE_HEIGHT_PX * SPLIT_RATIO;
      // Khối lớn được phép ngắt → phần con nhỏ bên trong (ô số liệu, khung con của thẻ) vẫn giữ trọn, phần con lớn
      // thì xét tiếp con của nó — để chỗ ngắt rơi vào khe giữa các phần con, không cắt ngang 1 ô/khung.
      const keepSmallParts = (el: HTMLElement) => {
        for (const child of Array.from(el.children) as HTMLElement[]) {
          if (child.getBoundingClientRect().height <= limit) child.dataset.printKeep = "";
          else keepSmallParts(child);
        }
      };
      reset();
      shell.querySelectorAll<HTMLElement>(BLOCK_SELECTOR).forEach((el) => {
        if (el.dataset.printKeep !== undefined || el.dataset.printSplit !== undefined) return;
        if (el.getBoundingClientRect().height <= limit) {
          el.dataset.printKeep = "";
        } else {
          el.dataset.printSplit = "";
          keepSmallParts(el);
        }
      });
      shell.style.width = prevWidth;
      shell.style.maxWidth = prevMax;
    };
    function reset() {
      shellRef.current?.querySelectorAll<HTMLElement>("[data-print-split], [data-print-keep]").forEach((el) => {
        delete el.dataset.printSplit;
        delete el.dataset.printKeep;
      });
    }
    // beforeprint: nút "Tải PDF" (window.print). Media query print: Playwright (services/report_pdf.py) chỉ
    // emulate_media("print") rồi page.pdf — đo lại khi đó, lúc style print đã áp dụng nên số đo sát bản in hơn.
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
  }, [shellRef]);
}
