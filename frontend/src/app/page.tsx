"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import Image from "next/image";
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
    <div className="max-w-5xl mx-auto space-y-16 pb-12">
      {/* Hero Section */}
      <section className="flex flex-col-reverse md:flex-row items-center gap-10 pt-8 md:pt-12">
        <div className="flex-1 space-y-6 text-center md:text-left">
          <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-tint text-primary-dark text-sm font-semibold">
            <Sparkles size={16} />
            AI Carbon Analyst
          </div>
          <h1 className="text-3xl md:text-5xl font-bold tracking-tight text-heading leading-[1.15]">
            Xin chào, tôi là Jenny AI,<br className="hidden md:block" /> nhân viên của Stavian Industrial Metal.
          </h1>
          <p className="text-body text-lg max-w-xl mx-auto md:mx-0 leading-relaxed">
            Tôi theo dõi tin tức và giá thị trường carbon/năng lượng mỗi ngày, tổng hợp thành báo cáo 
            Daily Carbon Intelligence, và trả lời câu hỏi của bạn về bất kỳ đoạn nào trong báo cáo.
            Chọn vai trò của bạn bên dưới để bắt đầu.
          </p>
        </div>
        <div className="flex-1 w-full flex justify-center md:justify-end">
          <div className="relative w-64 h-64 md:w-[400px] md:h-[400px] rounded-full overflow-hidden shadow-[var(--shadow-soft)] border-[6px] border-background">
            <Image
              src="/jenny.jpg"
              alt="Jenny AI Avatar"
              fill
              className="object-cover"
              priority
            />
          </div>
        </div>
      </section>

      {/* Role Selection */}
      <section className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="bg-background border border-border hover:border-primary/40 transition-colors duration-300 rounded-3xl p-8 flex flex-col shadow-sm">
          <div className="flex items-center gap-3 mb-6">
            <div className="p-2.5 rounded-xl bg-tint text-primary-dark">
              <Users size={24} />
            </div>
            <h2 className="text-xl font-bold text-label">Dành cho User</h2>
          </div>
          <ul className="space-y-4 flex-1">
            {USER_FEATURES.map(({ icon: Icon, text }, i) => (
              <li key={i} className="flex items-start gap-3 text-[15px] text-body">
                <Icon size={18} className="shrink-0 mt-0.5 text-primary-dark" />
                <span>{text}</span>
              </li>
            ))}
          </ul>
          <a href="/login" className="btn-pill w-full justify-center py-3 mt-8 gap-2 text-[15px]">
            <LogIn size={18} />
            Đăng nhập (User)
          </a>
        </div>

        <div className="bg-background border border-border hover:border-primary/40 transition-colors duration-300 rounded-3xl p-8 flex flex-col shadow-sm">
          <div className="flex items-center gap-3 mb-6">
            <div className="p-2.5 rounded-xl bg-tint text-primary-dark">
              <ShieldCheck size={24} />
            </div>
            <h2 className="text-xl font-bold text-label">Dành cho Admin</h2>
          </div>
          <ul className="space-y-4 flex-1">
            {ADMIN_FEATURES.map(({ icon: Icon, text }, i) => (
              <li key={i} className="flex items-start gap-3 text-[15px] text-body">
                <Icon size={18} className="shrink-0 mt-0.5 text-primary-dark" />
                <span>{text}</span>
              </li>
            ))}
          </ul>
          <a href="/login?as=admin" className="btn-pill w-full justify-center py-3 mt-8 gap-2 text-[15px]">
            <LogIn size={18} />
            Đăng nhập (Admin)
          </a>
        </div>
      </section>
    </div>
  );
}
