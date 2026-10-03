"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertCircle,
  Check,
  Copy,
  KeyRound,
  Loader2,
  Plug,
  Trash2,
} from "lucide-react";
import { format } from "date-fns";
import clsx from "clsx";
import { api } from "@/lib/api";
import {
  OS_INFO,
  TOKEN_PLACEHOLDER,
  apiErrorMessage,
  apiErrorStatus,
  buildClaudeConfig,
  copyText,
  detectOs,
  isTokenActive,
  type DesktopOs,
} from "@/lib/claudeConnect";
import type { ApiTokenCreated, ApiTokenSummary } from "@/lib/types";

function CopyButton({ text, label = "Sao chép" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={async () => {
        const ok = await copyText(text);
        setCopied(ok);
        if (ok) setTimeout(() => setCopied(false), 2000);
      }}
      className="shrink-0 flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-full border border-border bg-background hover:border-primary hover:text-primary-dark transition-colors"
    >
      {copied ? <Check size={13} /> : <Copy size={13} />}
      {copied ? "Đã chép" : label}
    </button>
  );
}

function CodeBlock({ text }: { text: string }) {
  return (
    <div className="relative bg-surface border border-border rounded-lg">
      <div className="absolute top-2 right-2">
        <CopyButton text={text} />
      </div>
      <pre className="p-4 pr-24 text-xs sm:text-[13px] text-body overflow-x-auto whitespace-pre-wrap break-all">{text}</pre>
    </div>
  );
}

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <section className="bg-background border border-border rounded-2xl p-5 md:p-6">
      <div className="flex items-center gap-3 mb-3">
        <span className="h-7 w-7 rounded-full bg-primary text-white text-sm font-bold flex items-center justify-center shrink-0">
          {n}
        </span>
        <h3 className="text-lg font-bold text-heading">{title}</h3>
      </div>
      <div className="space-y-3 text-sm text-body">{children}</div>
    </section>
  );
}

function fmtDate(iso: string | null): string {
  return iso ? format(new Date(iso), "dd/MM/yyyy HH:mm") : "—";
}

export default function ConnectClaudePage() {
  const router = useRouter();
  const [role, setRole] = useState<string | null>(null);
  const [tokens, setTokens] = useState<ApiTokenSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [os, setOs] = useState<DesktopOs>("mac");
  const [uvxPath, setUvxPath] = useState(OS_INFO.mac.defaultUvxPath);
  const [tokenName, setTokenName] = useState("Máy của tôi");
  const [creating, setCreating] = useState(false);
  // Token gốc vừa tạo — chỉ giữ trong state của trang này (backend không lưu lại được).
  const [newToken, setNewToken] = useState<ApiTokenCreated | null>(null);
  const [revokingId, setRevokingId] = useState<number | null>(null);

  const loadTokens = useCallback(async () => {
    const res = await api.get<ApiTokenSummary[]>("/api/claude/tokens");
    setTokens(res.data);
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const me = await api.get("/api/auth/me");
        // Detect OS sau await (trang còn hiện skeleton tới lúc này) để tránh lệch hydration
        // giữa SSR (không có navigator) và client.
        const detected = detectOs();
        setOs(detected);
        setUvxPath(OS_INFO[detected].defaultUvxPath);
        setRole(me.data.role);
        if (me.data.role === "user") await loadTokens();
      } catch (err) {
        if (apiErrorStatus(err) === 401) {
          router.replace("/login");
          return;
        }
        setError(apiErrorMessage(err, "Không thể tải thông tin kết nối."));
      } finally {
        setLoading(false);
      }
    })();
  }, [router, loadTokens]);

  function chooseOs(next: DesktopOs) {
    // Chỉ đổi đường dẫn uvx nếu user chưa tự sửa (còn đang là giá trị mặc định của OS cũ).
    if (uvxPath === OS_INFO[os].defaultUvxPath) setUvxPath(OS_INFO[next].defaultUvxPath);
    setOs(next);
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!tokenName.trim()) return;
    setCreating(true);
    setError("");
    try {
      const res = await api.post<ApiTokenCreated>("/api/claude/tokens", { name: tokenName.trim() });
      setNewToken(res.data);
      await loadTokens();
    } catch (err) {
      setError(apiErrorMessage(err, "Không tạo được token, vui lòng thử lại."));
    } finally {
      setCreating(false);
    }
  }

  async function handleRevoke(t: ApiTokenSummary) {
    if (!window.confirm(`Thu hồi token "${t.name}"? Máy đang dùng token này sẽ mất kết nối ngay.`)) return;
    setRevokingId(t.id);
    setError("");
    try {
      await api.delete(`/api/claude/tokens/${t.id}`);
      if (newToken?.id === t.id) setNewToken(null);
      await loadTokens();
    } catch (err) {
      setError(apiErrorMessage(err, "Không thu hồi được token, vui lòng thử lại."));
    } finally {
      setRevokingId(null);
    }
  }

  const info = OS_INFO[os];
  const config = buildClaudeConfig({ uvxPath, token: newToken?.token });

  if (loading) {
    return <div className="max-w-3xl mx-auto bg-background border border-border rounded-2xl h-48 animate-pulse" />;
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div>
        <h2 className="text-3xl font-bold tracking-tight text-heading mb-2 flex items-center gap-3">
          <Plug size={28} className="text-primary" />
          Kết nối Claude Desktop
        </h2>
        <p className="text-body">
          Cài một lần để bôi đen đoạn trong báo cáo rồi bấm <strong>Hỏi Claude</strong>: Claude Desktop tự lấy đoạn
          trích và số liệu của hệ thống để làm các tác vụ khó (sinh PDF/Excel, lập chiến lược...). Kết quả nằm
          trong Claude Desktop, không lưu ngược lại hệ thống.
        </p>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg flex items-start gap-3">
          <AlertCircle size={20} className="shrink-0 mt-0.5" />
          <p>{error}</p>
        </div>
      )}

      {role !== "user" ? (
        <div className="bg-warn-tint border border-warn/30 text-warn px-4 py-3 rounded-lg flex items-start gap-3">
          <AlertCircle size={20} className="shrink-0 mt-0.5" />
          <p>Tính năng này chỉ dành cho tài khoản người dùng (đăng nhập bằng email), không áp dụng cho tài khoản quản trị.</p>
        </div>
      ) : (
        <>
          <div className="flex items-center gap-2" role="tablist" aria-label="Hệ điều hành">
            <span className="text-sm font-semibold text-label mr-1">Máy của bạn dùng:</span>
            {(Object.keys(OS_INFO) as DesktopOs[]).map((k) => (
              <button
                key={k}
                role="tab"
                aria-selected={os === k}
                onClick={() => chooseOs(k)}
                className={clsx(
                  "text-sm font-semibold px-3.5 py-1.5 rounded-full border transition-colors",
                  os === k
                    ? "bg-primary text-white border-primary"
                    : "bg-background text-body border-border hover:border-primary hover:text-primary-dark"
                )}
              >
                {OS_INFO[k].label}
              </button>
            ))}
          </div>

          <Step n={1} title="Cài công cụ uv (một lần mỗi máy)">
            <p>
              Mở {os === "mac" ? "Terminal" : "PowerShell"} và chạy lệnh dưới đây. Cần có sẵn Claude Desktop trên máy.
            </p>
            <CodeBlock text={info.installCmd} />
            <p>
              Cài xong, mở cửa sổ {os === "mac" ? "Terminal" : "PowerShell"} mới và chạy{" "}
              <code className="bg-surface px-1.5 py-0.5 rounded">{info.whereCmd}</code> để lấy đường dẫn của{" "}
              <code className="bg-surface px-1.5 py-0.5 rounded">uvx</code> — dùng cho bước 3.
            </p>
          </Step>

          <Step n={2} title="Tạo token cá nhân">
            <p>
              Token cho phép Claude Desktop đọc dữ liệu báo cáo thay bạn (chỉ đọc, hết hạn sau 90 ngày, thu hồi được
              bất cứ lúc nào). Mỗi máy nên dùng một token riêng.
            </p>
            <form onSubmit={handleCreate} className="flex flex-col sm:flex-row gap-2">
              <input
                value={tokenName}
                onChange={(e) => setTokenName(e.target.value)}
                maxLength={100}
                placeholder="Tên máy, vd MacBook công ty"
                aria-label="Tên token"
                className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:border-primary"
              />
              <button
                type="submit"
                disabled={creating || !tokenName.trim()}
                className="flex items-center justify-center gap-2 text-sm font-semibold px-4 py-2 rounded-full bg-primary text-white hover:bg-primary-dark disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {creating ? <Loader2 size={14} className="animate-spin" /> : <KeyRound size={14} />}
                Tạo token
              </button>
            </form>

            {newToken && (
              <div className="bg-tint border border-primary/30 rounded-lg p-4 space-y-2">
                <p className="font-semibold text-primary-dark">
                  Token mới — sao chép ngay, sẽ không hiện lại sau khi rời trang này:
                </p>
                <div className="flex items-center gap-2">
                  <code className="flex-1 bg-background border border-border rounded px-2.5 py-2 text-xs break-all">
                    {newToken.token}
                  </code>
                  <CopyButton text={newToken.token} />
                </div>
              </div>
            )}

            {tokens.length > 0 && (
              <ul className="divide-y divide-border border border-border rounded-lg">
                {tokens.map((t) => {
                  const active = isTokenActive(t);
                  return (
                    <li key={t.id} className="flex items-center justify-between gap-3 px-4 py-3">
                      <div className="min-w-0">
                        <p className="font-semibold text-heading truncate">
                          {t.name}{" "}
                          <span className="font-mono text-xs text-muted-light font-normal">{t.token_prefix}…</span>
                        </p>
                        <p className="text-xs text-muted-light">
                          Tạo {fmtDate(t.created_at)} · Dùng gần nhất {fmtDate(t.last_used_at)} ·{" "}
                          {t.revoked_at ? `Đã thu hồi ${fmtDate(t.revoked_at)}` : `Hết hạn ${fmtDate(t.expires_at)}`}
                        </p>
                      </div>
                      <div className="flex items-center gap-3 shrink-0">
                        <span
                          className={clsx(
                            "text-xs font-semibold px-2.5 py-1 rounded-full border",
                            active ? "bg-tint text-primary-dark border-primary/20" : "bg-surface text-muted-light border-border"
                          )}
                        >
                          {active ? "Đang dùng" : t.revoked_at ? "Đã thu hồi" : "Hết hạn"}
                        </span>
                        {active && (
                          <button
                            onClick={() => handleRevoke(t)}
                            disabled={revokingId === t.id}
                            className="text-muted-light hover:text-down transition-colors disabled:opacity-50"
                            aria-label={`Thu hồi token ${t.name}`}
                            title="Thu hồi"
                          >
                            {revokingId === t.id ? <Loader2 size={16} className="animate-spin" /> : <Trash2 size={16} />}
                          </button>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </Step>

          <Step n={3} title="Thêm kết nối vào Claude Desktop">
            <p>
              Trong Claude Desktop: <strong>Settings → Developer → Edit Config</strong> (hoặc mở thẳng file{" "}
              <code className="bg-surface px-1.5 py-0.5 rounded break-all">{info.configPath}</code>), rồi dán đoạn
              dưới đây. Nếu file đã có <code className="bg-surface px-1.5 py-0.5 rounded">mcpServers</code>, chỉ thêm
              mục <code className="bg-surface px-1.5 py-0.5 rounded">carbon-analyst</code> vào trong đó.
            </p>
            <div>
              <label htmlFor="uvx-path" className="block text-sm font-semibold text-label mb-1.5">
                Đường dẫn uvx trên máy bạn
              </label>
              <input
                id="uvx-path"
                value={uvxPath}
                onChange={(e) => setUvxPath(e.target.value)}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm font-mono focus:outline-none focus:border-primary"
              />
              {os === "mac" && (
                <p className="text-xs text-muted-light mt-1">
                  macOS cần đường dẫn đầy đủ (kết quả của <code>which uvx</code>) vì ứng dụng không dùng PATH của
                  Terminal.
                </p>
              )}
            </div>
            <CodeBlock text={config} />
            {!newToken && (
              <p className="text-xs text-warn">
                Thay <code>{TOKEN_PLACEHOLDER}</code> bằng token ở bước 2 (tạo token mới ở trên để đoạn này tự điền sẵn).
              </p>
            )}
          </Step>

          <Step n={4} title="Khởi động lại và dùng thử">
            <ol className="list-decimal pl-5 space-y-1.5">
              <li>Thoát hẳn Claude Desktop (không chỉ đóng cửa sổ) rồi mở lại.</li>
              <li>
                Trong ô chat sẽ có biểu tượng công cụ, mở ra thấy <code>carbon-analyst</code> là đã kết nối.
              </li>
              <li>
                Vào một báo cáo, bôi đen đoạn cần hỏi, bấm <strong>Hỏi Claude</strong>, chọn loại tác vụ, gửi yêu
                cầu rồi dán prompt vào Claude Desktop. Lần đầu Claude sẽ hỏi quyền dùng công cụ — chọn cho phép.
              </li>
            </ol>
          </Step>
        </>
      )}
    </div>
  );
}
