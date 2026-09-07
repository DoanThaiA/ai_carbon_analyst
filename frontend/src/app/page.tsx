"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import {
  Sparkles,
  FileText,
  Bell,
  MessageCircleQuestion,
  ShieldCheck,
  Users,
  BarChart3,
  BookOpen,
  LogIn,
} from "lucide-react";
import { api } from "@/lib/api";

const USER_FEATURES = [
  { icon: FileText, text: "Xem báo cáo Daily Carbon Intelligence đã được duyệt" },
  { icon: Bell, text: "Nhận thông báo Hot News ngay khi có tin ảnh hưởng thị trường" },
  { icon: MessageCircleQuestion, text: "Bôi đen 1 đoạn trong báo cáo và hỏi đáp trực tiếp với AI" },
];

const ADMIN_FEATURES = [
  { icon: ShieldCheck, text: "Duyệt/publish báo cáo trước khi hiển thị cho người dùng" },
  { icon: Users, text: "Quản lý danh sách người dùng được phép đăng nhập" },
  { icon: BarChart3, text: "Quản lý nguồn giá & khung phân tích nhân quả EUA" },
  { icon: BookOpen, text: "Duyệt và chọn ví dụ mẫu (few-shot) cho AI hỏi đáp" },
];

export default function LandingPage() {
  const router = useRouter();

  useEffect(() => {
    // Người đã đăng nhập vào thẳng "/" thì đưa luôn về màn hình làm việc của
    // họ, thay vì phải xem lại trang giới thiệu mỗi lần.
    api.get("/api/auth/me")
      .then((res) => {
        router.replace(res.data.role === "admin" ? "/admin/reports" : "/dashboard");
      })
      .catch(() => {});
  }, [router]);

  return (
    <div className="max-w-4xl mx-auto space-y-10">
      <section className="text-center space-y-4 pt-6">
        <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-tint text-primary-dark text-sm font-semibold">
          <Sparkles size={16} />
          AI Carbon Analyst
        </div>
        <h1 className="text-3xl md:text-4xl font-bold tracking-tight text-heading">
          Xin chào, tôi là Jenny AI, nhân viên của Stavian Industrial Metal.
        </h1>
        <p className="text-body max-w-2xl mx-auto leading-relaxed">
          Tôi theo dõi tin tức và giá thị trường carbon/năng lượng mỗi ngày, tổng hợp thành báo cáo
          Daily Carbon Intelligence, và trả lời câu hỏi của bạn về bất kỳ đoạn nào trong báo cáo.
          Chọn vai trò của bạn bên dưới để bắt đầu.
        </p>
      </section>

      <section className="grid grid-cols-1 md:grid-cols-2 gap-5">
        <div className="bg-background border border-border rounded-2xl p-6 flex flex-col">
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2 rounded-lg bg-tint text-primary-dark">
              <Users size={20} />
            </div>
            <h2 className="text-lg font-bold text-label">Dành cho User</h2>
          </div>
          <ul className="space-y-3 flex-1">
            {USER_FEATURES.map(({ icon: Icon, text }, i) => (
              <li key={i} className="flex items-start gap-2.5 text-sm text-body">
                <Icon size={16} className="shrink-0 mt-0.5 text-primary-dark" />
                <span>{text}</span>
              </li>
            ))}
          </ul>
          <a href="/login" className="btn-pill w-full justify-center py-2.5 mt-6 gap-2">
            <LogIn size={15} />
            Đăng nhập (User)
          </a>
        </div>

        <div className="bg-background border border-border rounded-2xl p-6 flex flex-col">
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2 rounded-lg bg-tint text-primary-dark">
              <ShieldCheck size={20} />
            </div>
            <h2 className="text-lg font-bold text-label">Dành cho Admin</h2>
          </div>
          <ul className="space-y-3 flex-1">
            {ADMIN_FEATURES.map(({ icon: Icon, text }, i) => (
              <li key={i} className="flex items-start gap-2.5 text-sm text-body">
                <Icon size={16} className="shrink-0 mt-0.5 text-primary-dark" />
                <span>{text}</span>
              </li>
            ))}
          </ul>
          <a href="/login?as=admin" className="btn-pill w-full justify-center py-2.5 mt-6 gap-2">
            <LogIn size={15} />
            Đăng nhập (Admin)
          </a>
        </div>
      </section>
    </div>
  );
}
