"use client";

import { useEffect, useRef, useState } from "react";
import { History, MessageSquareWarning, MessageCircleMore, X } from "lucide-react";
import clsx from "clsx";

// Cụm nút nổi "Phản ánh" + "Lịch sử hỏi đáp" của QuoteChat. Trước đây là pill
// có chữ cố định ở góc phải dưới — đè lên nội dung báo cáo ở mọi vị trí cuộn
// (desktop che ~210px bên phải báo cáo, mobile che cuối dòng chữ/cột bảng).
// 2 chế độ, chọn theo khoảng trống thực tế bên phải .report-shell:
// - "gutter": đủ chỗ (desktop rộng) → 2 nút tròn chỉ có icon, đặt giữa khoảng
//   trống bên phải báo cáo, không đè lên nội dung.
// - "fab": không đủ chỗ (mobile/tablet, desktop hẹp) → gộp thành 1 nút, bấm mới
//   hiện 2 lựa chọn; tự ẩn khi cuộn xuống, hiện lại khi cuộn lên hoặc dừng cuộn;
//   thêm khoảng trống cuối báo cáo để nút không che nội dung cuối trang.

const BTN_SIZE = 44;
const GUTTER_MIN_MARGIN = 8; // khoảng cách tối thiểu giữa nút và mép báo cáo/mép màn hình
const SCROLL_STOP_MS = 600;

const iconBtnCls =
  "flex items-center justify-center w-11 h-11 rounded-full bg-background border border-border text-primary shadow-[var(--shadow-medium)] hover:border-primary hover:text-primary-dark transition-colors";

export function FloatingChatActions({
  containerRef,
  onFeedback,
  onHistory,
}: {
  containerRef: React.RefObject<HTMLDivElement | null>;
  onFeedback: () => void;
  onHistory: () => void;
}) {
  // undefined = chưa đo xong (chưa render nút, tránh desktop nháy chế độ fab);
  // null = không đủ chỗ → fab; number = toạ độ left của cột nút trong khoảng trống.
  const [gutterLeft, setGutterLeft] = useState<number | null | undefined>(undefined);
  const [menuOpen, setMenuOpen] = useState(false);
  const [hidden, setHidden] = useState(false);
  const lastScrollY = useRef(0);

  // Đo khoảng trống bên phải báo cáo — chạy lại khi đổi kích thước cửa sổ hoặc
  // khi chính báo cáo đổi kích thước/vị trí (sidebar admin, tải xong nội dung...).
  // useEffect (không phải useLayoutEffect): layout effect của component con chạy
  // TRƯỚC khi React gắn ref của div cha (QuoteChat) → containerRef.current còn null.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const measure = () => {
      const shell = container.querySelector(".report-shell") ?? container;
      const rect = shell.getBoundingClientRect();
      const viewportWidth = document.documentElement.clientWidth; // không tính thanh cuộn
      const gap = viewportWidth - rect.right;
      setGutterLeft(gap >= BTN_SIZE + GUTTER_MIN_MARGIN * 2 ? Math.round(rect.right + (gap - BTN_SIZE) / 2) : null);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(container);
    const shell = container.querySelector(".report-shell");
    if (shell) ro.observe(shell);
    window.addEventListener("resize", measure);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [containerRef]);

  const fabMode = gutterLeft === null;
  const measured = gutterLeft !== undefined;

  // Chế độ fab: cuộn xuống → ẩn, cuộn lên hoặc dừng cuộn → hiện lại.
  useEffect(() => {
    if (!fabMode) return;
    lastScrollY.current = window.scrollY;
    let stopTimer: ReturnType<typeof setTimeout> | undefined;
    const onScroll = () => {
      const y = window.scrollY;
      const delta = y - lastScrollY.current;
      lastScrollY.current = y;
      if (delta > 4) {
        setHidden(true);
        setMenuOpen(false);
      } else if (delta < -4) {
        setHidden(false);
      }
      clearTimeout(stopTimer);
      stopTimer = setTimeout(() => setHidden(false), SCROLL_STOP_MS);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      clearTimeout(stopTimer);
    };
  }, [fabMode]);

  if (!measured) return null;

  if (!fabMode) {
    return (
      <div
        style={{ left: gutterLeft ?? undefined }}
        className="fixed bottom-6 z-40 flex flex-col gap-2 print:hidden"
      >
        <button onClick={onHistory} title="Lịch sử hỏi đáp" aria-label="Lịch sử hỏi đáp" className={iconBtnCls}>
          <History size={18} />
        </button>
        <button onClick={onFeedback} title="Phản ánh thái độ của Jenny" aria-label="Phản ánh thái độ của Jenny" className={iconBtnCls}>
          <MessageSquareWarning size={18} />
        </button>
      </div>
    );
  }

  const pick = (action: () => void) => {
    setMenuOpen(false);
    action();
  };

  return (
    <>
      {/* Khoảng trống cuối báo cáo — nút nổi không che nội dung cuối trang. */}
      <div aria-hidden="true" className="h-16 print:hidden" />

      {menuOpen && (
        <div className="fixed inset-0 z-40 print:hidden" onClick={() => setMenuOpen(false)} aria-hidden="true" />
      )}

      <div
        className={clsx(
          "fixed bottom-4 right-4 z-40 flex flex-col items-end gap-2 print:hidden transition-all duration-200",
          hidden && !menuOpen ? "translate-y-24 opacity-0 pointer-events-none" : "translate-y-0 opacity-100"
        )}
      >
        {menuOpen && (
          <div className="flex flex-col items-end gap-2">
            <button
              onClick={() => pick(onHistory)}
              className="flex items-center gap-2 bg-background border border-border text-body text-sm font-semibold px-4 py-2.5 rounded-full shadow-[var(--shadow-medium)] hover:border-primary hover:text-primary-dark transition-colors"
            >
              <History size={17} className="text-primary" />
              Lịch sử hỏi đáp
            </button>
            <button
              onClick={() => pick(onFeedback)}
              className="flex items-center gap-2 bg-background border border-border text-body text-sm font-semibold px-4 py-2.5 rounded-full shadow-[var(--shadow-medium)] hover:border-primary hover:text-primary-dark transition-colors"
            >
              <MessageSquareWarning size={17} className="text-primary" />
              Phản ánh
            </button>
          </div>
        )}
        <button
          onClick={() => setMenuOpen((v) => !v)}
          aria-label={menuOpen ? "Đóng menu" : "Mở menu hỏi đáp"}
          aria-expanded={menuOpen}
          title="Hỏi đáp"
          className={clsx(
            "flex items-center justify-center w-12 h-12 rounded-full shadow-[var(--shadow-medium)] transition-colors",
            menuOpen ? "bg-background border border-border text-body" : "bg-primary text-white hover:bg-primary-dark"
          )}
        >
          {menuOpen ? <X size={20} /> : <MessageCircleMore size={20} />}
        </button>
      </div>
    </>
  );
}
