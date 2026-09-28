/**
 * Ngày HIỂN THỊ của báo cáo = report_date + 1 ngày.
 *
 * report_date (DB/API/URL) là ngày dữ liệu — báo cáo sinh lúc 07:00 ngày T
 * dùng phiên đóng cửa + tin tức của ngày T-1, nên report_date = T-1 (xem
 * scheduler.py::run_auto_report_job, admin/reports/page.tsx::handleGenerateToday).
 * Giao diện hiển thị theo ngày TẠO báo cáo (T) — chỉ đổi phần hiển thị, KHÔNG
 * đổi report_date dùng để gọi API/đường dẫn/sinh báo cáo.
 *
 * Nhận/trả về "YYYY-MM-DD"; chuỗi không đúng định dạng thì trả nguyên.
 */
export function displayReportDate(reportDate: string): string {
  const parts = reportDate?.split("-");
  if (!parts || parts.length !== 3) return reportDate;
  // Date.UTC để cộng ngày không bị lệch theo múi giờ/giờ mùa hè của máy client.
  const d = new Date(Date.UTC(+parts[0], +parts[1] - 1, +parts[2] + 1));
  if (isNaN(d.getTime())) return reportDate;
  return d.toISOString().slice(0, 10);
}
