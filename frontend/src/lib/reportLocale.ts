import type { Report } from "./types";

// Chọn bản nội dung báo cáo theo ngôn ngữ giao diện: "en" + có content_en → hiển thị
// bản dịch; "en" mà chưa có bản dịch → giữ tiếng Việt, `fallback=true` để trang hiện
// thông báo. Chỉ đổi phần HIỂN THỊ — sửa/QC luôn làm trên `content` tiếng Việt.
export function localizeReport(report: Report, locale: string): { report: Report; fallback: boolean } {
  if (locale !== "en") return { report, fallback: false };
  if (report.content_en) return { report: { ...report, content: report.content_en }, fallback: false };
  return { report, fallback: true };
}

// Gỡ 1 gợi ý biz (theo id) khỏi bản dịch đang giữ ở client — backend dịch lại sau,
// nhưng bản EN đang xem không được còn hiện gợi ý vừa gỡ.
export function removeBizFromContentEn(contentEn: any, id: number): any {
  const biz = contentEn?.biz;
  if (!biz) return contentEn;
  const drop = (rows: any) => (Array.isArray(rows) ? rows.filter((r: any) => r?.id !== id) : rows);
  return {
    ...contentEn,
    biz: { ...biz, short_term: drop(biz.short_term), long_term: drop(biz.long_term), reminders: drop(biz.reminders) },
  };
}
