"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import Image from "next/image";
import { useTranslations } from "next-intl";
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
  { icon: FileText, key: "userFeature1" },
  { icon: Bell, key: "userFeature2" },
  { icon: MessageCircleQuestion, key: "userFeature3" },
];

const ADMIN_FEATURES = [
  { icon: ShieldCheck, key: "adminFeature1" },
  { icon: Users, key: "adminFeature2" },
  { icon: BarChart3, key: "adminFeature3" },
  { icon: BookOpen, key: "adminFeature4" },
];

export default function LandingPage() {
  const router = useRouter();
  const t = useTranslations("userLanding");

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
          <h1 className="text-3xl md:text-5xl font-bold tracking-tight text-heading leading-[1.2]">
            {t("greeting")} <span className="whitespace-nowrap">Jenny AI,</span><br className="hidden lg:block" />
            <span className="inline-block mt-2">{t("employeeOf")} <span className="whitespace-nowrap">Stavian Industrial Metal.</span></span>
          </h1>
          <p className="text-body text-lg max-w-xl mx-auto md:mx-0 leading-relaxed">
            {t("intro")}
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
            <h2 className="text-xl font-bold text-label">{t("forUser")}</h2>
          </div>
          <ul className="space-y-4 flex-1">
            {USER_FEATURES.map(({ icon: Icon, key }, i) => (
              <li key={i} className="flex items-start gap-3 text-[15px] text-body">
                <Icon size={18} className="shrink-0 mt-0.5 text-primary-dark" />
                <span>{t(key)}</span>
              </li>
            ))}
          </ul>
          <a href="/login" className="btn-pill w-full justify-center py-3 mt-8 gap-2 text-[15px]">
            <LogIn size={18} />
            {t("loginUser")}
          </a>
        </div>

        <div className="bg-background border border-border hover:border-primary/40 transition-colors duration-300 rounded-3xl p-8 flex flex-col shadow-sm">
          <div className="flex items-center gap-3 mb-6">
            <div className="p-2.5 rounded-xl bg-tint text-primary-dark">
              <ShieldCheck size={24} />
            </div>
            <h2 className="text-xl font-bold text-label">{t("forAdmin")}</h2>
          </div>
          <ul className="space-y-4 flex-1">
            {ADMIN_FEATURES.map(({ icon: Icon, key }, i) => (
              <li key={i} className="flex items-start gap-3 text-[15px] text-body">
                <Icon size={18} className="shrink-0 mt-0.5 text-primary-dark" />
                <span>{t(key)}</span>
              </li>
            ))}
          </ul>
          <a href="/login?as=admin" className="btn-pill w-full justify-center py-3 mt-8 gap-2 text-[15px]">
            <LogIn size={18} />
            {t("loginAdmin")}
          </a>
        </div>
      </section>
    </div>
  );
}
