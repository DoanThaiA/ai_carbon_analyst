"use client";

import { Info } from "lucide-react";
import { useTranslations } from "next-intl";

// Giao diện tiếng Anh nhưng báo cáo chưa có bản dịch (content_en null) — báo cho người
// đọc biết đang xem bản gốc tiếng Việt. Vẫn in ra PDF — PDF tiếng Anh lỡ render từ bản
// tiếng Việt thì người nhận cũng biết lý do.
export function EnglishFallbackNotice() {
  const t = useTranslations("reportDoc");
  return (
    <div className="max-w-[210mm] mx-auto mb-4 flex items-start gap-2 rounded-lg border border-warn/30 bg-warn-tint px-3 py-2.5 text-[13.5px] text-body">
      <Info size={15} className="text-warn shrink-0 mt-0.5" aria-hidden="true" />
      <span>{t("englishUnavailable")}</span>
    </div>
  );
}
