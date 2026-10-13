import { useTranslations } from "next-intl";

// "2026-09-30" -> 0..6 (Date.getDay()), parse tay theo y/m/d để không lệch ngày do múi giờ; -1 nếu sai định dạng.
function weekdayIndex(reportDate: string): number {
  const [y, m, d] = reportDate.split("-").map(Number);
  if (!y || !m || !d) return -1;
  return new Date(y, m - 1, d).getDay();
}

// Hook trả về hàm dựng tiêu đề thẻ báo cáo: "Báo cáo Thứ Tư ngày 2026-09-30" (vi) / "Report Wednesday, 2026-09-30" (en).
export function useReportTitle(): (reportDate: string) => string {
  const t = useTranslations("shellReportDate");
  return (reportDate: string) => {
    const idx = weekdayIndex(reportDate);
    return idx >= 0
      ? t("titleWithWeekday", { weekday: t(`weekday${idx}`), date: reportDate })
      : t("title", { date: reportDate });
  };
}
