"use client";

import { useEffect, useRef, useState } from "react";
import Image from "next/image";
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

// Popup gợi ý cạnh avatar Jenny: tự bật khi mở báo cáo, sau HINT_AUTO_HIDE_MS không
// tương tác thì thu nhỏ vào avatar (chỉ còn avatar); bấm avatar để bật/tắt lại.
// Đang rê chuột trên popup thì không tự thu nhỏ.
const HINT_AUTO_HIDE_MS = 2000;
// Đường dẫn báo cáo đã tự bật gợi ý — component này bị unmount khi mở chat và
// mount lại khi đóng chat (xem QuoteChat), nên nhớ ở cấp module để gợi ý chỉ tự
// bật 1 lần mỗi khi MỞ 1 báo cáo, không bật lại sau mỗi lần đóng chat.
let hintAutoShownPath: string | null = null;

function JennyAvatarButton({ hintOpen, onToggle }: { hintOpen: boolean; onToggle: () => void }) {
  return (
    <button
      onClick={onToggle}
      title="Hỏi Jenny"
      aria-label="Hỏi Jenny"
      aria-expanded={hintOpen}
      className="relative w-11 h-11 rounded-full shadow-[var(--shadow-medium)] ring-2 ring-primary ring-offset-2 ring-offset-background hover:ring-primary-dark transition-shadow"
    >
      <Image src="/jenny.jpg" alt="Jenny AI" width={44} height={44} className="w-11 h-11 rounded-full object-cover" />
      {/* Chấm xanh "đang trực tuyến" */}
      <span className="absolute bottom-0 right-0 w-3 h-3 rounded-full bg-up border-2 border-background" aria-hidden="true" />
    </button>
  );
}

// Bong bóng thoại nằm bên TRÁI avatar (cụm nút luôn ở mép phải), mũi nhọn chỉ vào avatar.
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
        Bôi đen đoạn bất kỳ trong báo cáo rồi bấm <b className="text-primary-dark">Hỏi AI</b>.
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

  const jennyHint = (
    <div className="relative mb-1">
      <JennyAvatarButton hintOpen={hintOpen} onToggle={() => setHintOpen((v) => !v)} />
      <JennyHintBubble
        open={hintOpen}
        onClose={() => {
          setHintOpen(false);
          setHintHover(false); // popup đóng ngay dưới con trỏ → mouseleave có thể không bắn
        }}
        onHoverChange={setHintHover}
      />
    </div>
  );

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
        {jennyHint}
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
        {/* Avatar Jenny luôn nằm trên cùng cụm nút (trên các lựa chọn Lịch sử/Phản ánh) */}
        {jennyHint}
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
