import {
  FileText, Radar, Newspaper, Users, MessageSquareText, MessagesSquare, BrainCircuit, Sparkles,
  MessageSquareWarning, ClipboardList, BarChart3,
} from "lucide-react";

// Điều hướng admin — dùng chung giữa sidebar desktop (admin/layout.tsx) và menu
// mobile + breadcrumb "Admin/<trang>" trong Header.tsx, để 2 nơi luôn khớp nhãn/
// route. Phân nhóm như đang quản lý 1 nhân viên thực thụ (Jenny); giữa các nhóm là
// 1 vạch kẻ mờ. Nút "Đăng xuất" luôn nằm cuối nhóm 3 (render riêng ở 2 nơi trên).
export type AdminNavItem = { href: string; label: string; icon: typeof FileText };

export const ADMIN_NAV_GROUPS: { id: string; items: AdminNavItem[] }[] = [
  {
    // Nhóm 1: công việc giao tiếp hàng ngày
    id: "daily",
    items: [
      { href: "/admin/reports", label: "Báo cáo ngày", icon: FileText },
      { href: "/admin/chat-history", label: "Lịch sử giao tiếp", icon: MessagesSquare },
      { href: "/admin/chat-reviews", label: "Đánh giá chat", icon: MessageSquareText },
      { href: "/admin/feedback", label: "Phản ánh về Jenny", icon: MessageSquareWarning },
      { href: "/admin/users", label: "Đồng nghiệp", icon: Users },
    ],
  },
  {
    // Nhóm 2: cấu hình tri thức
    id: "knowledge",
    items: [
      { href: "/admin/news-sources", label: "Nguồn tin tức", icon: Newspaper },
      { href: "/admin/price-sources", label: "Nguồn giá", icon: Radar },
      { href: "/admin/eua-framework", label: "Khung phân tích EUA", icon: BrainCircuit },
      { href: "/admin/quote-chat-examples", label: "Đoạn chat tham khảo", icon: Sparkles },
    ],
  },
  {
    // Nhóm 3: hệ thống (+ Đăng xuất ở cuối)
    id: "system",
    items: [
      { href: "/admin/performance", label: "Thống kê hiệu suất", icon: BarChart3 },
      { href: "/admin/release-notes", label: "Release Note", icon: ClipboardList },
    ],
  },
];

export const ADMIN_NAV_ITEMS: AdminNavItem[] = ADMIN_NAV_GROUPS.flatMap((g) => g.items);
