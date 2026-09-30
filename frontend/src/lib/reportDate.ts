const WEEKDAYS_VI = ["Chủ Nhật", "Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy"];

// "2026-09-30" -> "Thứ Tư" (parse tay theo y/m/d để không lệch ngày do múi giờ).
export function weekdayVi(reportDate: string): string {
  const [y, m, d] = reportDate.split("-").map(Number);
  if (!y || !m || !d) return "";
  return WEEKDAYS_VI[new Date(y, m - 1, d).getDay()];
}

// Tiêu đề thẻ báo cáo: "Báo cáo Thứ Tư ngày 2026-09-30".
export function reportTitle(reportDate: string): string {
  const wd = weekdayVi(reportDate);
  return wd ? `Báo cáo ${wd} ngày ${reportDate}` : `Báo cáo ngày ${reportDate}`;
}
