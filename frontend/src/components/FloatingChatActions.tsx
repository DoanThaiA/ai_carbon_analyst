"use client";

import { useEffect, useRef, useState } from "react";
import Image from "next/image";
import { History, MessageSquareWarning, MessageCircleMore, Sparkles, X } from "lucide-react";
import clsx from "clsx";

// Nút nổi của QuoteChat: CHỈ 1 avatar Jenny ở mọi kích thước màn hình. Bấm vào mở
// menu 3 lựa chọn: Chat (hỏi đáp tự do, không cần bôi đen), Lịch sử hỏi đáp, Phản ánh.
// Vị trí chọn theo khoảng trống thực tế bên phải .report-shell:
// - "gutter": đủ chỗ (desktop rộng) → đặt giữa khoảng trống bên phải báo cáo,
//   không đè lên nội dung.
// - "fab": không đủ chỗ (mobile/tablet, desktop hẹp) → góc phải dưới; tự ẩn khi
//   cuộn xuống, hiện lại khi cuộn lên hoặc dừng cuộn; thêm khoảng trống cuối báo
//   cáo để nút không che nội dung cuối trang.

const BTN_SIZE = 56;
const GUTTER_MIN_MARGIN = 8; // khoảng cách tối thiểu giữa nút và mép báo cáo/mép màn hình
const SCROLL_STOP_MS = 600;

// Popup gợi ý cạnh avatar Jenny: tự bật khi mở báo cáo, sau HINT_AUTO_HIDE_MS không
// tương tác thì thu nhỏ vào avatar (chỉ còn avatar). Đang rê chuột trên popup thì
// không tự thu nhỏ.
const HINT_AUTO_HIDE_MS = 8000;
// Đường dẫn báo cáo đã tự bật gợi ý — component này bị unmount khi mở chat và
// mount lại khi đóng chat (xem QuoteChat), nên nhớ ở cấp module để gợi ý chỉ tự
// bật 1 lần mỗi khi MỞ 1 báo cáo, không bật lại sau mỗi lần đóng chat.
let hintAutoShownPath: string | null = null;

const menuItemCls =
  "flex items-center gap-2 whitespace-nowrap bg-background border border-border text-body text-sm font-semibold px-4 py-2.5 rounded-full shadow-[var(--shadow-medium)] hover:border-primary hover:text-primary-dark transition-colors";

// Bong bóng thoại nằm bên TRÁI avatar (nút luôn ở phía phải), mũi nhọn chỉ vào avatar.
// Luôn render, chỉ đổi trạng thái để có hiệu ứng: mở = phóng to + hiện dần từ phía
// avatar; đóng = thu nhỏ + mờ dần "chui" về avatar (origin-right).
function JennyHintBubble({
  open,
  onClose,
  onHoverChange,
}: {
  open: boolean;
  onClose: () => void;
  onHoverChange: (hovering: boolean) => void;
}) {
  return (
    <div
      role="status"
      aria-hidden={!open}
      onMouseEnter={() => onHoverChange(true)}
      onMouseLeave={() => onHoverChange(false)}
      className={clsx(
        "absolute right-full top-1/2 mr-3 w-[230px] rounded-xl bg-background border border-primary/30 shadow-[var(--shadow-medium)] px-3.5 py-3",
        "origin-right transition-all duration-300 ease-out motion-reduce:transition-none",
        open
          ? "-translate-y-1/2 translate-x-0 scale-100 opacity-100"
          : "-translate-y-1/2 translate-x-3 scale-50 opacity-0 pointer-events-none"
      )}
    >
      <button
        onClick={onClose}
        aria-label="Đóng gợi ý"
        className="absolute top-1.5 right-1.5 p-1 rounded-full text-muted-light hover:text-body hover:bg-surface"
      >
        <X size={13} />
      </button>
      <p className="pr-4 text-[13.5px] leading-[1.4] font-semibold text-label">
        Nếu có gì thắc mắc hãy hỏi tôi nhé!
      </p>
      <p className="mt-1 text-[12px] leading-[1.4] text-muted-light">
        Bấm vào tôi để chat, hoặc bôi đen đoạn bất kỳ trong báo cáo rồi bấm <b className="text-primary-dark">Hỏi AI</b>.
      </p>
      {/* Mũi nhọn bong bóng */}
      <span
        aria-hidden="true"
        className="absolute left-full top-1/2 -translate-y-1/2 -ml-[6px] w-3 h-3 rotate-45 bg-background border-r border-t border-primary/30"
      />
    </div>
  );
}

export function FloatingChatActions({
  containerRef,
  onChat,
  onFeedback,
  onHistory,
  onClaude,
}: {
  containerRef: React.RefObject<HTMLDivElement | null>;
  onChat: () => void;
  onFeedback: () => void;
  onHistory: () => void;
  // Tuỳ chọn: chỉ truyền khi user được phép "Hỏi Claude" (xem QuoteChat) — không có thì ẩn mục menu.
  onClaude?: () => void;
}) {
  // undefined = chưa đo xong (chưa render nút, tránh desktop nháy chế độ fab);
  // null = không đủ chỗ → fab; number = toạ độ left của nút trong khoảng trống.
  const [gutterLeft, setGutterLeft] = useState<number | null | undefined>(undefined);
  const [menuOpen, setMenuOpen] = useState(false);
  const [hidden, setHidden] = useState(false);
  // Gợi ý của Jenny bật sẵn khi vừa mở báo cáo, tự tắt sau vài giây.
  const [hintOpen, setHintOpen] = useState(false);
  const [hintHover, setHintHover] = useState(false);
  const lastScrollY = useRef(0);

  useEffect(() => {
    const path = window.location.pathname;
    if (hintAutoShownPath === path) return;
    hintAutoShownPath = path;
    setHintOpen(true);
  }, []);

  useEffect(() => {
    if (!hintOpen || hintHover) return;
    const t = setTimeout(() => setHintOpen(false), HINT_AUTO_HIDE_MS);
    return () => clearTimeout(t);
  }, [hintOpen, hintHover]);

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

  const pick = (action: () => void) => {
    setMenuOpen(false);
    action();
  };

  return (
    <>
      {/* Khoảng trống cuối báo cáo — nút nổi (chế độ fab) không che nội dung cuối trang. */}
      {fabMode && <div aria-hidden="true" className="h-16 print:hidden" />}

      {menuOpen && (
        <div className="fixed inset-0 z-40 print:hidden" onClick={() => setMenuOpen(false)} aria-hidden="true" />
      )}

      <div
        style={fabMode ? undefined : { left: gutterLeft ?? undefined }}
        className={clsx(
          "fixed z-40 print:hidden transition-all duration-200",
          fabMode ? "bottom-4 right-4" : "bottom-28",
          fabMode && hidden && !menuOpen ? "translate-y-24 opacity-0 pointer-events-none" : "translate-y-0 opacity-100"
        )}
      >
        <div className="relative">
          {/* Menu mở lên phía trên, canh mép phải của avatar */}
          {menuOpen && (
            <div className="absolute bottom-full right-0 mb-3 flex flex-col items-end gap-2">
              <button onClick={() => pick(onChat)} className={menuItemCls}>
                <MessageCircleMore size={17} className="text-primary" />
                Chat với Jenny
              </button>
              {onClaude && (
                <button onClick={() => pick(onClaude)} className={menuItemCls}>
                  <Sparkles size={17} className="text-primary" />
                  Hỏi Claude Desktop
                </button>
              )}
              <button onClick={() => pick(onHistory)} className={menuItemCls}>
                <History size={17} className="text-primary" />
                Lịch sử hỏi đáp
              </button>
              <button onClick={() => pick(onFeedback)} className={menuItemCls}>
                <MessageSquareWarning size={17} className="text-primary" />
                Phản ánh
              </button>
            </div>
          )}

          {/* Chỉ avatar Jenny — không kèm icon/badge nào khác */}
          <button
            onClick={() => {
              setMenuOpen((v) => !v);
              setHintOpen(false);
            }}
            aria-label={menuOpen ? "Đóng menu" : "Mở menu Jenny: Chat, Lịch sử hỏi đáp, Phản ánh"}
            aria-expanded={menuOpen}
            title="Jenny"
            className="relative block w-14 h-14 rounded-full shadow-[var(--shadow-medium)] ring-2 ring-primary ring-offset-2 ring-offset-background hover:ring-primary-dark transition-shadow"
          >
            <Image src="/jenny_chat.jpg" alt="Jenny AI" width={112} height={112} sizes="56px" className="w-14 h-14 rounded-full object-cover" />
            {menuOpen && (
              <span className="absolute inset-0 flex items-center justify-center rounded-full bg-black/45 text-white">
                <X size={20} />
              </span>
            )}
          </button>

          <JennyHintBubble
            open={hintOpen && !menuOpen}
            onClose={() => {
              setHintOpen(false);
              setHintHover(false); // popup đóng ngay dưới con trỏ → mouseleave có thể không bắn
            }}
            onHoverChange={setHintHover}
          />
        </div>
      </div>
    </>
  );
}
