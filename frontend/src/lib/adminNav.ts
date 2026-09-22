import { FileText, Radar, Users, MessageSquareText, BrainCircuit, Sparkles, MessageSquareWarning, ClipboardList } from "lucide-react";

// Danh sách điều hướng admin — dùng chung giữa sidebar desktop (admin/layout.tsx)
// và menu mobile + breadcrumb "Admin/<trang>" trong Header.tsx, để 2 nơi luôn
// khớp nhãn/route với nhau thay vì khai báo trùng lặp.
export const ADMIN_NAV_ITEMS = [
  { href: "/admin/reports", label: "Báo cáo", icon: FileText },
  { href: "/admin/price-sources", label: "Nguồn giá", icon: Radar },
  { href: "/admin/users", label: "Người dùng", icon: Users },
  { href: "/admin/chat-reviews", label: "Đánh giá chat", icon: MessageSquareText },
  { href: "/admin/quote-chat-examples", label: "Đoạn chat tham khảo", icon: Sparkles },
  { href: "/admin/eua-framework", label: "Khung phân tích EUA", icon: BrainCircuit },
  { href: "/admin/feedback", label: "Phản ánh về Jenny", icon: MessageSquareWarning },
  { href: "/admin/release-notes", label: "Release Note", icon: ClipboardList },
];
