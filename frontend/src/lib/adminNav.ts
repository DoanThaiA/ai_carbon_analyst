import {
  FileText, Radar, Newspaper, Users, MessageSquareText, MessagesSquare, BrainCircuit, Sparkles,
  MessageSquareWarning, ClipboardList, BarChart3,
} from "lucide-react";

// Điều hướng admin — dùng chung giữa sidebar desktop (admin/layout.tsx) và menu
// mobile + breadcrumb "Admin/<trang>" trong Header.tsx, để 2 nơi luôn khớp nhãn/
// route. Phân nhóm như đang quản lý 1 nhân viên thực thụ (Jenny); giữa các nhóm là
// 1 vạch kẻ mờ. Nút "Đăng xuất" luôn nằm cuối nhóm 3 (render riêng ở 2 nơi trên).
export type AdminNavItem = { href: string; label: string; icon: typeof FileText };

// Thứ tự theo cấu trúc quản lý Jenny như 1 nhân viên: 1 Báo cáo ngày → 2 Thống kê &
// hiệu suất → 3 Nguồn lực → 4 Năng lực → 5 Đồng nghiệp → 6 Phản ánh về Jenny.
// `label` của nhóm hiện làm tiêu đề nhỏ khi sidebar mở rộng (hover).
export const ADMIN_NAV_GROUPS: { id: string; label: string; items: AdminNavItem[] }[] = [
  {
    id: "daily",
    label: "1. Báo cáo ngày",
    items: [{ href: "/admin/reports", label: "Báo cáo ngày", icon: FileText }],
  },
  {
    id: "stats",
    label: "2. Thống kê & Hiệu suất",
    items: [{ href: "/admin/performance", label: "Thống kê hiệu suất", icon: BarChart3 }],
  },
  {
    id: "resources",
    label: "3. Nguồn lực của Jenny",
    items: [
      { href: "/admin/price-sources", label: "Nguồn giá", icon: Radar },
      { href: "/admin/news-sources", label: "Nguồn tin tức", icon: Newspaper },
      { href: "/admin/eua-framework", label: "Khung phân tích EUA", icon: BrainCircuit },
    ],
  },
  {
    id: "capability",
    label: "4. Năng lực của Jenny",
    items: [
      { href: "/admin/quote-chat-examples", label: "Đoạn chat tham khảo", icon: Sparkles },
      { href: "/admin/chat-reviews", label: "Đánh giá chat", icon: MessageSquareText },
      { href: "/admin/chat-history", label: "Lịch sử giao tiếp", icon: MessagesSquare },
    ],
  },
  {
    id: "colleagues",
    label: "5. Đồng nghiệp",
    items: [{ href: "/admin/users", label: "Đồng nghiệp", icon: Users }],
  },
  {
    // Nhóm cuối (+ Đăng xuất ở cuối)
    id: "feedback",
    label: "6. Phản ánh về Jenny",
    items: [
      { href: "/admin/feedback", label: "Phản ánh về Jenny", icon: MessageSquareWarning },
      { href: "/admin/release-notes", label: "Release Note", icon: ClipboardList },
    ],
  },
];

export const ADMIN_NAV_ITEMS: AdminNavItem[] = ADMIN_NAV_GROUPS.flatMap((g) => g.items);
