"use client";

// Trang "QC Result": tổng hợp mọi biên bản QC báo cáo do Claude (vai Jack) gửi qua MCP — CHỈ admin.
// Gồm thống kê nhanh, danh sách biên bản (lọc theo kết luận) và quản lý token qc_agent cho lịch Claude QC 07:30.
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import clsx from "clsx";
import { useTranslations } from "next-intl";
import { ChevronLeft, ChevronRight, ClipboardCheck, Copy, KeyRound, Repeat, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { buildMobileConnectorUrl } from "@/lib/claudeConnect";
import type { QcAgentTokensResponse, QcReviewListResponse, QcReviewStats, QcVerdict } from "@/lib/types";
import { VerdictBadge, improved, scoreColor } from "@/components/QcReview";

const PAGE_SIZE = 20;
const FILTERS: (QcVerdict | "all")[] = ["all", "fail", "pass_with_fixes", "pass"];

function fmtTime(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh" }) : "—";
}

function AgentTokens() {
  const t = useTranslations("adminQcReviews");
  const [data, setData] = useState<QcAgentTokensResponse | null>(null);
  const [name, setName] = useState("Claude QC 07:30");
  const [created, setCreated] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showPrompt, setShowPrompt] = useState(false);

  const load = useCallback(async () => {
    const res = await api.get("/api/admin/qc-reviews/agent-tokens");
    setData(res.data);
  }, []);
  useEffect(() => {
    load().catch(() => {});
  }, [load]);

  const create = async () => {
    setBusy(true);
    try {
      const res = await api.post("/api/admin/qc-reviews/agent-tokens", { name });
      setCreated(res.data.token);
      await load();
    } catch (err: any) {
      alert(err.response?.data?.detail || t("tokenError"));
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (id: number) => {
    if (!confirm(t("confirmRevoke"))) return;
    try {
      await api.delete(`/api/admin/qc-reviews/agent-tokens/${id}`);
      await load();
    } catch (err: any) {
      alert(err.response?.data?.detail || t("tokenError"));
    }
  };

  const copy = (text: string) => navigator.clipboard?.writeText(text);

  if (!data) return null;
  const active = data.items.filter((x) => !x.revoked_at && new Date(x.expires_at) > new Date());
  return (
    <section className="bg-background border border-border rounded-2xl shadow-[var(--shadow-soft)] p-5 space-y-4">
      <div>
        <h2 className="flex items-center gap-2 font-bold text-heading"><KeyRound size={16} className="text-primary" />{t("agentTitle")}</h2>
        <p className="text-sm text-body mt-1">
          {t("agentFlow", {
            report: data.auto_report_time,
            fix: data.auto_qc_fix_time || t("autoFixOff"),
            publish: data.auto_publish_time || t("autoPublishOff"),
          })}
        </p>
      </div>

      {created && (
        <div className="rounded-xl border border-primary/30 bg-tint p-3 space-y-2 text-sm">
          <p className="font-semibold text-primary-dark">{t("tokenCreatedOnce")}</p>
          <div className="flex items-center gap-2">
            <code className="flex-1 min-w-0 break-all text-xs bg-background border border-border rounded px-2 py-1">{buildMobileConnectorUrl(created)}</code>
            <button type="button" onClick={() => copy(buildMobileConnectorUrl(created))} className="p-1.5 rounded border border-border hover:border-primary" title={t("copy")}>
              <Copy size={14} />
            </button>
          </div>
          <p className="text-xs text-body">{t("connectorHint")}</p>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={80}
          className="flex-1 min-w-[200px] rounded-lg border border-border bg-background px-3 py-1.5 text-sm"
        />
        <button
          type="button"
          onClick={create}
          disabled={busy || !name.trim()}
          className="bg-primary hover:bg-primary-dark text-white px-4 py-1.5 rounded-full font-semibold text-sm disabled:opacity-50"
        >
          {t("createToken")}
        </button>
      </div>

      {active.length > 0 && (
        <ul className="divide-y divide-border text-sm">
          {active.map((x) => (
            <li key={x.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
              <span>
                <b className="text-heading">{x.name}</b> <code className="text-xs text-muted-light">{x.token_prefix}…</code>
                <span className="block text-xs text-muted-light">
                  {t("tokenMeta", { by: x.created_by, expires: fmtTime(x.expires_at), used: fmtTime(x.last_used_at) })}
                </span>
              </span>
              <button type="button" onClick={() => revoke(x.id)} className="inline-flex items-center gap-1 text-xs font-semibold text-down hover:underline">
                <Trash2 size={12} />{t("revoke")}
              </button>
            </li>
          ))}
        </ul>
      )}

      <div>
        <button type="button" onClick={() => setShowPrompt((v) => !v)} className="text-sm font-semibold text-primary hover:text-primary-dark">
          {showPrompt ? t("hidePrompt") : t("showPrompt")}
        </button>
        {showPrompt && (
          <div className="mt-2 space-y-2">
            <p className="text-xs text-body">{t("promptHint")}</p>
            <div className="relative">
              <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-xl border border-border bg-surface p-3 text-xs text-body font-sans">{data.routine_prompt}</pre>
              <button type="button" onClick={() => copy(data.routine_prompt)} className="absolute top-2 right-2 p-1.5 rounded border border-border bg-background hover:border-primary" title={t("copy")}>
                <Copy size={14} />
              </button>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

export default function AdminQcResultsPage() {
  const t = useTranslations("adminQcReviews");
  const router = useRouter();
  const [filter, setFilter] = useState<QcVerdict | "all">("all");
  const [page, setPage] = useState(0);
  const [list, setList] = useState<QcReviewListResponse | null>(null);
  const [stats, setStats] = useState<QcReviewStats | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.get("/api/admin/qc-reviews/stats").then((r) => setStats(r.data)).catch(() => {});
  }, []);

  useEffect(() => {
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(page * PAGE_SIZE) });
    if (filter !== "all") params.set("verdict", filter);
    api
      .get(`/api/admin/qc-reviews?${params}`)
      .then((r) => { setList(r.data); setError(""); })
      .catch((err) => setError(err.response?.data?.detail || t("loadError")));
  }, [filter, page, t]);

  const pages = list ? Math.max(1, Math.ceil(list.total / PAGE_SIZE)) : 1;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold text-heading"><ClipboardCheck className="text-primary" />{t("pageTitle")}</h1>
        <p className="text-sm text-body mt-1">{t("pageDesc")}</p>
      </div>

      {stats && stats.reports > 0 && (
        <section className="grid gap-4 md:grid-cols-3">
          <div className="bg-background border border-border rounded-2xl p-4">
            <div className="text-xs text-muted-light">{t("statsAvg", { count: stats.reports })}</div>
            <div className={clsx("text-3xl font-bold", scoreColor(stats.avg_score ?? 0))}>{stats.avg_score?.toFixed(2) ?? "—"}<span className="text-base text-muted-light">/10</span></div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {(["pass", "pass_with_fixes", "fail"] as QcVerdict[]).map((v) => (
                <span key={v} className="inline-flex items-center gap-1 text-xs"><VerdictBadge verdict={v} /> {stats.by_verdict[v]}</span>
              ))}
            </div>
            {stats.trend.length > 1 && (
              <div className="mt-3 flex items-end gap-0.5 h-10" aria-hidden="true">
                {stats.trend.map((p) => (
                  <div key={p.report_date} title={`${p.report_date}: ${p.score}`}
                       className={clsx("flex-1 rounded-sm", p.verdict === "fail" ? "bg-down" : p.verdict === "pass" ? "bg-primary" : "bg-warn")}
                       style={{ height: `${Math.max(8, p.score * 10)}%` }} />
                ))}
              </div>
            )}
          </div>
          <div className="bg-background border border-border rounded-2xl p-4">
            <div className="text-xs text-muted-light mb-2">{t("statsCriteria")}</div>
            <ul className="space-y-1 text-sm">
              {stats.criteria_avg.map((c) => (
                <li key={c.key} className="flex justify-between gap-2">
                  <span className="text-body truncate">{c.label}</span>
                  <b className={c.avg != null ? scoreColor(c.avg) : "text-muted-light"}>{c.avg?.toFixed(1) ?? "—"}</b>
                </li>
              ))}
            </ul>
          </div>
          <div className="bg-background border border-border rounded-2xl p-4">
            <div className="text-xs text-muted-light mb-2">{t("statsRecurring")}</div>
            {stats.recurring.length === 0 ? (
              <p className="text-sm text-muted-light">{t("noRecurring")}</p>
            ) : (
              <ul className="space-y-1.5 text-sm">
                {stats.recurring.map((r) => (
                  <li key={r.fingerprint} className="flex gap-2">
                    <span className="inline-flex items-center gap-0.5 shrink-0 text-xs font-semibold text-down"><Repeat size={11} />{r.reports}</span>
                    <span className="text-body line-clamp-2">{r.title}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      )}

      <section className="bg-background border border-border rounded-2xl shadow-[var(--shadow-soft)] overflow-hidden">
        <div className="flex flex-wrap gap-1.5 p-3 border-b border-border">
          {FILTERS.map((f) => (
            <button
              key={f}
              type="button"
              onClick={() => { setFilter(f); setPage(0); }}
              className={clsx("px-3 py-1 rounded-full text-xs font-semibold border", filter === f ? "bg-primary text-white border-primary" : "border-border-soft text-body hover:border-primary")}
            >
              {f === "all" ? t("filterAll") : t(`verdict_${f}`)}
            </button>
          ))}
        </div>
        {error ? (
          <p className="p-4 text-sm text-down">{error}</p>
        ) : !list ? (
          <p className="p-4 text-sm text-muted-light">…</p>
        ) : list.items.length === 0 ? (
          <p className="p-6 text-sm text-muted-light text-center">{t("empty")}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-surface text-xs text-muted-light uppercase tracking-wider">
                <tr>
                  <th className="text-left px-3 py-2">{t("colReport")}</th>
                  <th className="text-left px-3 py-2">{t("colRun")}</th>
                  <th className="text-right px-3 py-2">{t("colScore")}</th>
                  <th className="text-left px-3 py-2">{t("colVerdict")}</th>
                  <th className="text-left px-3 py-2">{t("colFindings")}</th>
                  <th className="text-right px-3 py-2">{t("colJack")}</th>
                  <th className="text-left px-3 py-2 min-w-[260px]">{t("colSummary")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {list.items.map((r) => (
                  <tr key={r.id} onClick={() => router.push(`/admin/qc-results/${r.id}`)} className="cursor-pointer hover:bg-surface">
                    <td className="px-3 py-2 whitespace-nowrap">
                      <Link href={`/admin/qc-results/${r.id}`} className="font-semibold text-primary hover:underline" onClick={(e) => e.stopPropagation()}>
                        {r.report_date}
                      </Link>
                      <span className="block text-[11px] text-muted-light">
                        {r.current_report_status === "published" ? t("reportPublished") : r.current_report_status || "—"}
                        {r.is_stale ? ` · ${t("stale")}` : ""}
                      </span>
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap text-xs text-muted-light">#{r.run_no}<br />{fmtTime(r.created_at)}</td>
                    <td className={clsx("px-3 py-2 text-right font-bold tabular-nums", scoreColor(r.overall_score))}>
                      {r.overall_score.toFixed(2)}
                      {improved(r) && (
                        <span className="block text-[11px] font-semibold text-primary" title={t("scoreAfterFixesTitle")}>→ {r.score_after_fixes!.toFixed(2)}</span>
                      )}
                    </td>
                    <td className="px-3 py-2"><VerdictBadge verdict={r.verdict} /></td>
                    <td className="px-3 py-2 whitespace-nowrap text-xs">
                      <span className="text-down font-semibold">{r.critical_count}</span> / <span className="text-warn font-semibold">{r.major_count}</span> / <span className="text-muted-light">{r.minor_count}</span>
                    </td>
                    <td className="px-3 py-2 text-right text-xs">{r.jack_rule_score ?? "—"}</td>
                    <td className="px-3 py-2 text-xs text-body"><p className="line-clamp-2">{r.summary}</p></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {list && list.total > PAGE_SIZE && (
          <div className="flex items-center justify-end gap-2 p-3 border-t border-border text-sm">
            <button type="button" disabled={page === 0} onClick={() => setPage((p) => p - 1)} className="p-1 rounded border border-border disabled:opacity-40"><ChevronLeft size={14} /></button>
            <span>{page + 1}/{pages}</span>
            <button type="button" disabled={page + 1 >= pages} onClick={() => setPage((p) => p + 1)} className="p-1 rounded border border-border disabled:opacity-40"><ChevronRight size={14} /></button>
          </div>
        )}
      </section>

      <AgentTokens />
    </div>
  );
}
