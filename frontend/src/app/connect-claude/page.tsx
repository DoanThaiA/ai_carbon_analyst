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
import { useTranslations } from "next-intl";
import { api } from "@/lib/api";
import {
  OS_INFO,
  apiErrorMessage,
  apiErrorStatus,
  buildClaudeConfig,
  copyText,
  detectOs,
  isTokenActive,
  uvxPathFor,
  type DesktopOs,
} from "@/lib/claudeConnect";
import type { ApiTokenCreated, ApiTokenSummary } from "@/lib/types";

function CopyButton({ text, label }: { text: string; label?: string }) {
  const t = useTranslations("userConnect");
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
      {copied ? t("copied") : (label ?? t("copy"))}
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
  const t = useTranslations("userConnect");
  const router = useRouter();
  const [role, setRole] = useState<string | null>(null);
  const [tokens, setTokens] = useState<ApiTokenSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [os, setOs] = useState<DesktopOs>("mac");
  const [uvxPath, setUvxPath] = useState(uvxPathFor("mac", t("uvxUserPlaceholder")));
  const [tokenName, setTokenName] = useState(t("defaultTokenName"));
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
        setUvxPath(uvxPathFor(detected, t("uvxUserPlaceholder")));
        setRole(me.data.role);
        if (me.data.role === "user") await loadTokens();
      } catch (err) {
        if (apiErrorStatus(err) === 401) {
          router.replace("/login");
          return;
        }
        setError(apiErrorMessage(err, t("loadError")));
      } finally {
        setLoading(false);
      }
    })();
  }, [router, loadTokens, t]);

  function chooseOs(next: DesktopOs) {
    // Chỉ đổi đường dẫn uvx nếu user chưa tự sửa (còn đang là giá trị mặc định của OS cũ).
    if (uvxPath === uvxPathFor(os, t("uvxUserPlaceholder"))) setUvxPath(uvxPathFor(next, t("uvxUserPlaceholder")));
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
      setError(apiErrorMessage(err, t("createFailed")));
    } finally {
      setCreating(false);
    }
  }

  async function handleRevoke(tk: ApiTokenSummary) {
    if (!window.confirm(t("revokeConfirm", { name: tk.name }))) return;
    setRevokingId(tk.id);
    setError("");
    try {
      await api.delete(`/api/claude/tokens/${tk.id}`);
      if (newToken?.id === tk.id) setNewToken(null);
      await loadTokens();
    } catch (err) {
      setError(apiErrorMessage(err, t("revokeFailed")));
    } finally {
      setRevokingId(null);
    }
  }

  const info = OS_INFO[os];
  const config = buildClaudeConfig({ uvxPath, token: newToken?.token, tokenPlaceholder: t("tokenPlaceholder") });

  if (loading) {
    return <div className="max-w-3xl mx-auto bg-background border border-border rounded-2xl h-48 animate-pulse" />;
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div>
        <h2 className="text-3xl font-bold tracking-tight text-heading mb-2 flex items-center gap-3">
          <Plug size={28} className="text-primary" />
          {t("title")}
        </h2>
        <p className="text-body">
          {t.rich("intro", { b: (c) => <strong>{c}</strong> })}
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
          <p>{t("userOnly")}</p>
        </div>
      ) : (
        <>
          <div className="flex items-center gap-2" role="tablist" aria-label={t("osLabel")}>
            <span className="text-sm font-semibold text-label mr-1">{t("yourOs")}</span>
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

          <Step n={1} title={t("step1Title")}>
            <p>
              {t("step1Open", { shell: os === "mac" ? "Terminal" : "PowerShell" })}
            </p>
            <CodeBlock text={info.installCmd} />
            <p>
              {t.rich("step1After", {
                shell: os === "mac" ? "Terminal" : "PowerShell",
                cmd: info.whereCmd,
                code: (c) => <code className="bg-surface px-1.5 py-0.5 rounded">{c}</code>,
              })}
            </p>
          </Step>

          <Step n={2} title={t("step2Title")}>
            <p>
              {t("step2Desc")}
            </p>
            <form onSubmit={handleCreate} className="flex flex-col sm:flex-row gap-2">
              <input
                value={tokenName}
                onChange={(e) => setTokenName(e.target.value)}
                maxLength={100}
                placeholder={t("tokenNamePlaceholder")}
                aria-label={t("tokenNameLabel")}
                className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:border-primary"
              />
              <button
                type="submit"
                disabled={creating || !tokenName.trim()}
                className="flex items-center justify-center gap-2 text-sm font-semibold px-4 py-2 rounded-full bg-primary text-white hover:bg-primary-dark disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {creating ? <Loader2 size={14} className="animate-spin" /> : <KeyRound size={14} />}
                {t("createToken")}
              </button>
            </form>

            {newToken && (
              <div className="bg-tint border border-primary/30 rounded-lg p-4 space-y-2">
                <p className="font-semibold text-primary-dark">
                  {t("newTokenNotice")}
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
                {tokens.map((tk) => {
                  const active = isTokenActive(tk);
                  return (
                    <li key={tk.id} className="flex items-center justify-between gap-3 px-4 py-3">
                      <div className="min-w-0">
                        <p className="font-semibold text-heading truncate">
                          {tk.name}{" "}
                          <span className="font-mono text-xs text-muted-light font-normal">{tk.token_prefix}…</span>
                        </p>
                        <p className="text-xs text-muted-light">
                          {t("tokenCreated", { date: fmtDate(tk.created_at) })} · {t("tokenLastUsed", { date: fmtDate(tk.last_used_at) })} ·{" "}
                          {tk.revoked_at ? t("tokenRevokedAt", { date: fmtDate(tk.revoked_at) }) : t("tokenExpiresAt", { date: fmtDate(tk.expires_at) })}
                        </p>
                      </div>
                      <div className="flex items-center gap-3 shrink-0">
                        <span
                          className={clsx(
                            "text-xs font-semibold px-2.5 py-1 rounded-full border",
                            active ? "bg-tint text-primary-dark border-primary/20" : "bg-surface text-muted-light border-border"
                          )}
                        >
                          {active ? t("statusActive") : tk.revoked_at ? t("statusRevoked") : t("statusExpired")}
                        </span>
                        {active && (
                          <button
                            onClick={() => handleRevoke(tk)}
                            disabled={revokingId === tk.id}
                            className="text-muted-light hover:text-down transition-colors disabled:opacity-50"
                            aria-label={t("revokeToken", { name: tk.name })}
                            title={t("revoke")}
                          >
                            {revokingId === tk.id ? <Loader2 size={16} className="animate-spin" /> : <Trash2 size={16} />}
                          </button>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </Step>

          <Step n={3} title={t("step3Title")}>
            <p>
              {t.rich("step3Desc", {
                path: info.configPath,
                b: (c) => <strong>{c}</strong>,
                code: (c) => <code className="bg-surface px-1.5 py-0.5 rounded break-all">{c}</code>,
              })}
            </p>
            <div>
              <label htmlFor="uvx-path" className="block text-sm font-semibold text-label mb-1.5">
                {t("uvxPathLabel")}
              </label>
              <input
                id="uvx-path"
                value={uvxPath}
                onChange={(e) => setUvxPath(e.target.value)}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm font-mono focus:outline-none focus:border-primary"
              />
              {os === "mac" && (
                <p className="text-xs text-muted-light mt-1">
                  {t.rich("macPathHint", { code: (c) => <code>{c}</code> })}
                </p>
              )}
            </div>
            <CodeBlock text={config} />
            {!newToken && (
              <p className="text-xs text-warn">
                {t.rich("replacePlaceholder", { placeholder: t("tokenPlaceholder"), code: (c) => <code>{c}</code> })}
              </p>
            )}
          </Step>

          <Step n={4} title={t("step4Title")}>
            <ol className="list-decimal pl-5 space-y-1.5">
              <li>{t("step4a")}</li>
              <li>
                {t.rich("step4b", { code: (c) => <code>{c}</code> })}
              </li>
              <li>
                {t.rich("step4c", { b: (c) => <strong>{c}</strong> })}
              </li>
            </ol>
          </Step>
        </>
      )}
    </div>
  );
}
