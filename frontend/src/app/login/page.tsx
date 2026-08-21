"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Mail, KeyRound, AlertCircle, User, Lock } from "lucide-react";
import clsx from "clsx";
import Image from "next/image";
import { api } from "@/lib/api";

type Mode = "user" | "admin";

function ModeSwitch({ mode, onChange }: { mode: Mode; onChange: (m: Mode) => void }) {
  return (
    <div className="flex p-1 bg-surface rounded-full mb-6">
      {([
        { key: "user" as const, label: "Người dùng" },
        { key: "admin" as const, label: "Admin" },
      ]).map(({ key, label }) => (
        <button
          key={key}
          type="button"
          onClick={() => onChange(key)}
          className={clsx(
            "flex-1 py-2 rounded-full text-sm font-semibold transition-colors duration-300 ease-in-out",
            mode === key ? "bg-primary text-white" : "text-body hover:text-label"
          )}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

function UserLoginForm() {
  const router = useRouter();
  const [step, setStep] = useState<"email" | "otp">("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");

  const handleRequestOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      const res = await api.post("/api/auth/otp/request", { email });
      setInfo(res.data.message || "Mã OTP đã được gửi.");
      setStep("otp");
    } catch (err: any) {
      setError(err.response?.data?.detail || "Không gửi được mã OTP.");
    } finally {
      setLoading(false);
    }
  };

  const handleVerifyOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      await api.post("/api/auth/otp/verify", { email, code });
      router.push("/");
    } catch (err: any) {
      setError(err.response?.data?.detail || "Mã OTP không đúng.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      {error && (
        <div className="mb-4 bg-red-50 border border-red-200 text-red-700 px-3 py-2 rounded-lg flex items-start gap-2 text-sm">
          <AlertCircle size={16} className="shrink-0 mt-0.5" />
          <p>{error}</p>
        </div>
      )}

      {step === "email" ? (
        <form onSubmit={handleRequestOtp} className="space-y-4">
          <div>
            <label className="block text-sm font-semibold text-label mb-1.5">Email công việc</label>
            <div className="relative">
              <Mail size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-light" />
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="ban@congty.com"
                className="w-full pl-9 pr-3 py-2.5 border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary"
              />
            </div>
          </div>
          <button type="submit" disabled={loading} className="btn-pill w-full justify-center py-2.5">
            {loading ? "Đang gửi..." : "Gửi mã đăng nhập"}
          </button>
        </form>
      ) : (
        <form onSubmit={handleVerifyOtp} className="space-y-4">
          {info && <p className="text-sm text-body">{info} Kiểm tra hộp thư <b className="text-label">{email}</b>.</p>}
          <div>
            <label className="block text-sm font-semibold text-label mb-1.5">Mã xác nhận (6 số)</label>
            <div className="relative">
              <KeyRound size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-light" />
              <input
                type="text"
                inputMode="numeric"
                maxLength={6}
                required
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                placeholder="123456"
                className="w-full pl-9 pr-3 py-2.5 border border-border rounded-lg text-sm tracking-[0.3em] font-mono focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary"
              />
            </div>
          </div>
          <button type="submit" disabled={loading} className="btn-pill w-full justify-center py-2.5">
            {loading ? "Đang xác nhận..." : "Xác nhận đăng nhập"}
          </button>
          <button
            type="button"
            onClick={() => { setStep("email"); setCode(""); setError(""); }}
            className="w-full text-sm text-body hover:text-primary transition-colors"
          >
            &larr; Dùng email khác
          </button>
        </form>
      )}
    </>
  );
}

function AdminLoginForm() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      await api.post("/api/admin/auth/login", { username, password });
      router.push("/admin/reports");
    } catch (err: any) {
      setError(err.response?.data?.detail || "Đăng nhập thất bại.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      {error && (
        <div className="mb-4 bg-red-50 border border-red-200 text-red-700 px-3 py-2 rounded-lg flex items-start gap-2 text-sm">
          <AlertCircle size={16} className="shrink-0 mt-0.5" />
          <p>{error}</p>
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className="block text-sm font-semibold text-label mb-1.5">Tài khoản</label>
          <div className="relative">
            <User size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-light" />
            <input
              type="text"
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full pl-9 pr-3 py-2.5 border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary"
            />
          </div>
        </div>
        <div>
          <label className="block text-sm font-semibold text-label mb-1.5">Mật khẩu</label>
          <div className="relative">
            <Lock size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-light" />
            <input
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full pl-9 pr-3 py-2.5 border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary"
            />
          </div>
        </div>
        <button type="submit" disabled={loading} className="btn-pill w-full justify-center py-2.5">
          {loading ? "Đang đăng nhập..." : "Đăng nhập"}
        </button>
      </form>
    </>
  );
}

function LoginPageContent() {
  const searchParams = useSearchParams();
  const initialMode: Mode = searchParams.get("as") === "admin" ? "admin" : "user";
  const [mode, setMode] = useState<Mode>(initialMode);

  return (
    <div className="min-h-[70vh] flex items-center justify-center">
      <div className="w-full max-w-sm bg-background border border-border rounded-2xl shadow-[var(--shadow-soft)] p-8">
        <div className="flex items-center gap-3 mb-6 bg-primary-dark p-4 rounded-xl -mx-4 -mt-4">
          <Image src="/stavian_logo.png" alt="Stavian" width={100} height={28} className="h-7 w-auto block" />
          <div className="w-[1px] h-5 bg-white/25 mx-1"></div>
          <h1 className="text-lg font-extrabold tracking-tight text-white">
            AI Carbon Analyst
          </h1>
        </div>

        <ModeSwitch mode={mode} onChange={setMode} />

        {mode === "user" ? <UserLoginForm /> : <AdminLoginForm />}
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginPageContent />
    </Suspense>
  );
}
