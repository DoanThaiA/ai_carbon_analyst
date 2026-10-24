"use client";

import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Maximize2, Minimize2 } from "lucide-react";
import { useTranslations } from "next-intl";

const FOCUS_CLASS = "report-focus";

// Nút "Phóng to" ở thanh công cụ trang báo cáo (user + admin): bật class `report-focus` trên <html> — globals.css ẩn
// header site và mọi khối `.report-focus-hide` (thanh công cụ), chỉ còn báo cáo — đồng thời xin toàn màn hình của
// trình duyệt (không hỗ trợ, vd Safari iOS, thì chỉ ẩn phần xung quanh). Thoát: nút nổi góc trái dưới (góc phải dưới là nút chat, mép trên là thanh mục lục), Esc, hoặc rời toàn
// màn hình; rời trang cũng tự gỡ class.
export function ReportFocusToggle({ className }: { className?: string }) {
  const t = useTranslations("reportFocus");
  const [on, setOn] = useState(false);

  const exit = useCallback(() => {
    setOn(false);
    if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
  }, []);

  const enter = () => {
    setOn(true);
    document.documentElement.requestFullscreen?.().catch(() => {});
  };

  useEffect(() => {
    if (!on) return;
    const root = document.documentElement;
    root.classList.add(FOCUS_CLASS);
    window.scrollTo({ top: 0 });
    // Esc khi đang toàn màn hình: trình duyệt tự thoát toàn màn hình (không phát keydown) → bắt qua fullscreenchange.
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") exit(); };
    const onFs = () => { if (!document.fullscreenElement) setOn(false); };
    window.addEventListener("keydown", onKey);
    document.addEventListener("fullscreenchange", onFs);
    return () => {
      root.classList.remove(FOCUS_CLASS);
      window.removeEventListener("keydown", onKey);
      document.removeEventListener("fullscreenchange", onFs);
    };
  }, [on, exit]);

  // Rời trang khi đang toàn màn hình → thoát toàn màn hình.
  useEffect(() => () => { if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {}); }, []);

  return (
    <>
      <button
        onClick={enter}
        className={className ?? "flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-full border border-border-soft text-body hover:border-primary hover:text-primary-dark transition-colors"}
      >
        <Maximize2 size={14} />
        {t("enter")}
      </button>
      {/* Portal: thanh công cụ chứa nút này bị ẩn khi phóng to, nút thoát phải nằm ngoài nó. */}
      {on && createPortal(
        <button
          onClick={exit}
          title={t("exitHint")}
          aria-label={t("exitHint")}
          className="fixed bottom-4 left-4 z-[55] flex items-center gap-1.5 rounded-full border border-border bg-background/90 px-3 py-1.5 text-xs font-semibold text-body shadow-[var(--shadow-soft)] backdrop-blur hover:border-primary hover:text-primary-dark transition-colors print:hidden"
        >
          <Minimize2 size={14} />
          {t("exit")}
        </button>,
        document.body,
      )}
    </>
  );
}
