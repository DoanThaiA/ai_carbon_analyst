import {
  FileText, Radar, Newspaper, Users, MessageSquareText, MessagesSquare, BrainCircuit, Sparkles,
  MessageSquareWarning, ClipboardList, BarChart3, LineChart,
} from "lucide-react";

// Điều hướng admin — dùng chung giữa sidebar desktop (admin/layout.tsx) và menu
// mobile + breadcrumb "Admin/<trang>" trong Header.tsx, để 2 nơi luôn khớp nhãn/
// route. Phân nhóm như đang quản lý 1 nhân viên thực thụ (Jenny); giữa các nhóm là
// 1 vạch kẻ mờ. Nút "Đăng xuất" luôn nằm cuối nhóm 3 (render riêng ở 2 nơi trên).
export type AdminNavItem = { href: string; labelKey: string; icon: typeof FileText };

// Thứ tự theo cấu trúc quản lý Jenny như 1 nhân viên: 1 Báo cáo ngày → 2 Thống kê &
// hiệu suất → 3 Nguồn lực → 4 Năng lực → 5 Đồng nghiệp → 6 Phản ánh về Jenny.
// `labelKey` (khoá dịch trong namespace shellNav) của nhóm hiện làm tiêu đề nhỏ khi sidebar mở rộng (hover).
export const ADMIN_NAV_GROUPS: { id: string; labelKey: string; items: AdminNavItem[] }[] = [
  {
    id: "daily",
    labelKey: "group_daily",
    items: [{ href: "/admin/reports", labelKey: "reports", icon: FileText }],
  },
  {
    id: "stats",
    labelKey: "group_stats",
    items: [
      { href: "/admin/price-dashboard", labelKey: "priceDashboard", icon: LineChart },
      { href: "/admin/performance", labelKey: "performance", icon: BarChart3 },
    ],
  },
  {
    id: "resources",
    labelKey: "group_resources",
    items: [
      { href: "/admin/price-sources", labelKey: "priceSources", icon: Radar },
      { href: "/admin/news-sources", labelKey: "newsSources", icon: Newspaper },
      { href: "/admin/eua-framework", labelKey: "euaFramework", icon: BrainCircuit },
    ],
  },
  {
    id: "capability",
    labelKey: "group_capability",
    items: [
      { href: "/admin/quote-chat-examples", labelKey: "chatExamples", icon: Sparkles },
      { href: "/admin/chat-reviews", labelKey: "chatReviews", icon: MessageSquareText },
      { href: "/admin/chat-history", labelKey: "chatHistory", icon: MessagesSquare },
    ],
  },
  {
    id: "colleagues",
    labelKey: "group_colleagues",
    items: [{ href: "/admin/users", labelKey: "users", icon: Users }],
  },
  {
    // Nhóm cuối (+ Đăng xuất ở cuối)
    id: "feedback",
    labelKey: "group_feedback",
    items: [
      { href: "/admin/feedback", labelKey: "feedback", icon: MessageSquareWarning },
      { href: "/admin/release-notes", labelKey: "releaseNotes", icon: ClipboardList },
    ],
  },
];

export const ADMIN_NAV_ITEMS: AdminNavItem[] = ADMIN_NAV_GROUPS.flatMap((g) => g.items);
