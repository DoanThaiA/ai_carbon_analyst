import {
  FileText, Radar, Newspaper, Users, MessageSquareText, MessagesSquare, BrainCircuit, Sparkles,
<<<<<<< HEAD
  MessageSquareWarning, ClipboardList, BarChart3, LineChart, CalendarCheck, TrendingUp, Database,
  Lightbulb, Handshake, MessageCircleHeart,
=======
  MessageSquareWarning, ClipboardList, BarChart3, LineChart, ClipboardCheck,
>>>>>>> d6b4d70b31b4791519175f3c66a938425760098d
} from "lucide-react";

// Điều hướng admin — dùng chung giữa sidebar desktop (components/admin/AdminSidebar.tsx),
// menu mobile + breadcrumb trong Header.tsx và trang Tổng quan /admin (app/admin/page.tsx),
// để mọi nơi luôn khớp nhãn/route/màu. Phân nhóm như đang quản lý 1 nhân viên thực thụ
// (Jenny). Nút "Đăng xuất" luôn nằm cuối sidebar/menu (render riêng, không phải 1 route).
export type AdminNavItem = {
  href: string;
  labelKey: string; // khoá dịch trong namespace shellNav; mô tả card = `desc_<labelKey>`
  icon: typeof FileText;
};

export type AdminNavTone = "teal" | "blue" | "violet" | "amber" | "pink" | "cyan";

export type AdminNavGroup = {
  id: string;
  labelKey: string; // shellNav; mô tả nhóm ở trang Tổng quan = `groupDesc_<id>`
  icon: typeof FileText;
  tone: AdminNavTone;
  items: AdminNavItem[];
};

export const ADMIN_HOME_HREF = "/admin";

// Thứ tự theo cấu trúc quản lý Jenny như 1 nhân viên: 1 Báo cáo ngày → 2 Thống kê &
// hiệu suất → 3 Nguồn lực → 4 Năng lực → 5 Đồng nghiệp → 6 Phản ánh về Jenny.
export const ADMIN_NAV_GROUPS: AdminNavGroup[] = [
  {
    id: "daily",
    labelKey: "group_daily",
<<<<<<< HEAD
    icon: CalendarCheck,
    tone: "teal",
    items: [{ href: "/admin/reports", labelKey: "reports", icon: FileText }],
=======
    items: [
      { href: "/admin/reports", labelKey: "reports", icon: FileText },
      // Biên bản QC (Claude/Jack qua MCP) — tổng hợp mọi báo cáo, xem services/report_qc_review.py.
      { href: "/admin/qc-results", labelKey: "qcResults", icon: ClipboardCheck },
    ],
>>>>>>> d6b4d70b31b4791519175f3c66a938425760098d
  },
  {
    id: "stats",
    labelKey: "group_stats",
    icon: TrendingUp,
    tone: "blue",
    items: [
      { href: "/admin/price-dashboard", labelKey: "priceDashboard", icon: LineChart },
      { href: "/admin/performance", labelKey: "performance", icon: BarChart3 },
    ],
  },
  {
    id: "resources",
    labelKey: "group_resources",
    icon: Database,
    tone: "violet",
    items: [
      { href: "/admin/price-sources", labelKey: "priceSources", icon: Radar },
      { href: "/admin/news-sources", labelKey: "newsSources", icon: Newspaper },
      { href: "/admin/eua-framework", labelKey: "euaFramework", icon: BrainCircuit },
    ],
  },
  {
    id: "capability",
    labelKey: "group_capability",
    icon: Lightbulb,
    tone: "amber",
    items: [
      { href: "/admin/quote-chat-examples", labelKey: "chatExamples", icon: Sparkles },
      { href: "/admin/chat-reviews", labelKey: "chatReviews", icon: MessageSquareText },
      { href: "/admin/chat-history", labelKey: "chatHistory", icon: MessagesSquare },
    ],
  },
  {
    id: "colleagues",
    labelKey: "group_colleagues",
    icon: Handshake,
    tone: "pink",
    items: [{ href: "/admin/users", labelKey: "users", icon: Users }],
  },
  {
    id: "feedback",
    labelKey: "group_feedback",
    icon: MessageCircleHeart,
    tone: "cyan",
    items: [
      { href: "/admin/feedback", labelKey: "feedback", icon: MessageSquareWarning },
      { href: "/admin/release-notes", labelKey: "releaseNotes", icon: ClipboardList },
    ],
  },
];

export const ADMIN_NAV_ITEMS: AdminNavItem[] = ADMIN_NAV_GROUPS.flatMap((g) => g.items);

// Class Tailwind theo màu nhóm — viết đủ chuỗi (không ghép động) để Tailwind quét được.
// Nền/viền pastel nhạt, màu đậm chỉ cho icon/chữ nhấn.
export const ADMIN_TONE_CLASSES: Record<AdminNavTone, {
  dot: string; iconWrap: string; text: string; activeItem: string; cardHover: string; focusRing: string; arrowHover: string;
}> = {
  teal: {
    dot: "bg-teal-500", iconWrap: "bg-teal-50 text-teal-700 ring-teal-100", text: "text-teal-700",
    activeItem: "bg-teal-50 text-teal-800 ring-teal-100", cardHover: "hover:border-teal-200 hover:bg-teal-50/40",
    focusRing: "focus-visible:ring-teal-500", arrowHover: "group-hover:text-teal-600",
  },
  blue: {
    dot: "bg-blue-500", iconWrap: "bg-blue-50 text-blue-700 ring-blue-100", text: "text-blue-700",
    activeItem: "bg-blue-50 text-blue-800 ring-blue-100", cardHover: "hover:border-blue-200 hover:bg-blue-50/40",
    focusRing: "focus-visible:ring-blue-500", arrowHover: "group-hover:text-blue-600",
  },
  violet: {
    dot: "bg-violet-500", iconWrap: "bg-violet-50 text-violet-700 ring-violet-100", text: "text-violet-700",
    activeItem: "bg-violet-50 text-violet-800 ring-violet-100", cardHover: "hover:border-violet-200 hover:bg-violet-50/40",
    focusRing: "focus-visible:ring-violet-500", arrowHover: "group-hover:text-violet-600",
  },
  amber: {
    dot: "bg-amber-500", iconWrap: "bg-amber-50 text-amber-700 ring-amber-100", text: "text-amber-700",
    activeItem: "bg-amber-50 text-amber-800 ring-amber-100", cardHover: "hover:border-amber-200 hover:bg-amber-50/40",
    focusRing: "focus-visible:ring-amber-500", arrowHover: "group-hover:text-amber-600",
  },
  pink: {
    dot: "bg-pink-500", iconWrap: "bg-pink-50 text-pink-700 ring-pink-100", text: "text-pink-700",
    activeItem: "bg-pink-50 text-pink-800 ring-pink-100", cardHover: "hover:border-pink-200 hover:bg-pink-50/40",
    focusRing: "focus-visible:ring-pink-500", arrowHover: "group-hover:text-pink-600",
  },
  cyan: {
    dot: "bg-cyan-500", iconWrap: "bg-cyan-50 text-cyan-700 ring-cyan-100", text: "text-cyan-700",
    activeItem: "bg-cyan-50 text-cyan-800 ring-cyan-100", cardHover: "hover:border-cyan-200 hover:bg-cyan-50/40",
    focusRing: "focus-visible:ring-cyan-500", arrowHover: "group-hover:text-cyan-600",
  },
};

/** Route admin đang mở thuộc mục nào (khớp tiền tố, vd /admin/reports/2026-10-19 → reports). */
export function activeAdminItem(pathname: string | null | undefined) {
  if (!pathname) return undefined;
  for (const group of ADMIN_NAV_GROUPS) {
    const item = group.items.find(({ href }) => pathname === href || pathname.startsWith(`${href}/`));
    if (item) return { group, item };
  }
  return undefined;
}
