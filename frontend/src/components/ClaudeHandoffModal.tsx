"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { X, Send, Loader2, CheckCircle2, AlertCircle, Copy, Check, Quote as QuoteIcon } from "lucide-react";
import clsx from "clsx";
import { useTranslations } from "next-intl";
import { api } from "@/lib/api";
import { MAX_QUOTE_CHARS, TASK_TYPES, apiErrorMessage, copyText, isTokenActive } from "@/lib/claudeConnect";
import type { ApiTokenSummary, ClaudeTaskType, HandoffCreated } from "@/lib/types";

// "Hỏi Claude": gói đoạn bôi đen + câu hỏi thành handoff trên backend, rồi đưa user câu
// prompt ngắn để dán vào Claude Desktop — Claude Desktop tự lấy ngữ cảnh qua MCP
// (get_handoff). Đi MỘT CHIỀU: câu trả lời/file do Claude sinh ra không lưu ngược về hệ thống.
export function ClaudeHandoffModal({
  open,
  onClose,
  reportDate,
  quote,
}: {
  open: boolean;
  onClose: () => void;
  reportDate: string;
  quote: string;
}) {
  const t = useTranslations("userHandoff");
  const [taskType, setTaskType] = useState<ClaudeTaskType>("other");
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<HandoffCreated | null>(null);
  const [copied, setCopied] = useState(false);
  // null = đang kiểm tra; false = chưa có token nào còn dùng được -> dẫn sang trang kết nối.
  const [hasToken, setHasToken] = useState<boolean | null>(null);
  // Tăng mỗi lần đóng modal — request đang bay khi user đóng sẽ thấy số khác và bỏ kết quả, tránh
  // "rò" prompt của yêu cầu cũ sang lần mở sau (state được reset lúc đóng nhưng request vẫn trả về).
  const sessionRef = useRef(0);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    api
      .get<ApiTokenSummary[]>("/api/claude/tokens")
      .then((res) => {
        if (!cancelled) setHasToken(res.data.some(isTokenActive));
      })
      .catch(() => {
        // Không kiểm tra được thì không chặn user — vẫn cho tạo handoff.
        if (!cancelled) setHasToken(true);
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  if (!open) return null;

  const placeholder = t(`task_${taskType}_ph`);
  // Backend giới hạn quote 4000 ký tự — cắt ở FE kèm thông báo thay vì để user nhận lỗi 422.
  const quoteTruncated = quote.length > MAX_QUOTE_CHARS;
  const quoteToSend = quoteTruncated ? quote.slice(0, MAX_QUOTE_CHARS) : quote;

  const handleClose = () => {
    sessionRef.current += 1;
    // Reset để lần mở sau là form trống, không giữ prompt của handoff cũ.
    setTaskType("other");
    setQuestion("");
    setError("");
    setResult(null);
    setCopied(false);
    setHasToken(null);
    onClose();
  };

  const handleCopy = async (text: string) => {
    const ok = await copyText(text);
    setCopied(ok);
    if (ok) setTimeout(() => setCopied(false), 2000);
    return ok;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!question.trim()) return;
    const session = sessionRef.current;
    setLoading(true);
    setError("");
    try {
      const res = await api.post<HandoffCreated>(`/api/reports/${reportDate}/quote-chat/handoff`, {
        quote: quoteToSend,
        question: question.trim(),
        task_type: taskType,
      });
      if (session !== sessionRef.current) return; // modal đã đóng trong lúc chờ
      setResult(res.data);
      await handleCopy(res.data.prompt);
    } catch (err) {
      if (session !== sessionRef.current) return;
      setError(apiErrorMessage(err, t("createFailed")));
    } finally {
      if (session === sessionRef.current) setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 print:hidden">
      <div className="absolute inset-0 bg-black/40" onClick={handleClose} />
      <div className="relative w-full max-w-lg max-h-[90vh] overflow-y-auto bg-background border border-border rounded-2xl shadow-[var(--shadow-medium)] p-6">
        <button
          onClick={handleClose}
          className="absolute top-4 right-4 text-muted-light hover:text-heading transition-colors"
          aria-label={t("close")}
        >
          <X size={18} />
        </button>

        <h3 className="text-lg font-bold text-heading pr-6">{t("title")}</h3>
        <p className="text-sm text-body mt-1">
          {t("intro")}
        </p>

        {hasToken === false && !result && (
          <div className="mt-4 bg-warn-tint border border-warn/30 text-warn px-4 py-3 rounded-lg flex items-start gap-3 text-sm">
            <AlertCircle size={18} className="shrink-0 mt-0.5" />
            <p>
              {t.rich("notConnected", {
                link: (c) => (
                  <Link href="/connect-claude" className="font-semibold underline">
                    {c}
                  </Link>
                ),
              })}
            </p>
          </div>
        )}

        {result ? (
          <div className="mt-5 space-y-4">
            <div className="bg-tint border border-primary/20 text-primary-dark px-4 py-3 rounded-lg flex items-start gap-3 text-sm">
              <CheckCircle2 size={18} className="shrink-0 mt-0.5" />
              <p>
                {copied ? t("copiedNotice") : t("createdNotice")}
              </p>
            </div>
            <div className="bg-surface border border-border rounded-lg p-3 text-sm text-body whitespace-pre-wrap break-words">
              {result.prompt}
            </div>
            <div className="flex items-center justify-between gap-3">
              <p className="text-xs text-muted-light">{t("validity")}</p>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => handleCopy(result.prompt)}
                  className="flex items-center gap-1.5 text-sm font-semibold px-3.5 py-2 rounded-full border border-border hover:border-primary hover:text-primary-dark transition-colors"
                >
                  {copied ? <Check size={14} /> : <Copy size={14} />}
                  {copied ? t("copied") : t("copy")}
                </button>
                <button
                  type="button"
                  onClick={handleClose}
                  className="text-sm font-semibold px-3.5 py-2 rounded-full bg-primary text-white hover:bg-primary-dark transition-colors"
                >
                  {t("done")}
                </button>
              </div>
            </div>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="mt-5 space-y-4">
            {quote.trim() && (
              <div className="bg-surface border border-border rounded-lg px-3 py-2.5 flex gap-2.5">
                <QuoteIcon size={14} className="text-primary shrink-0 mt-1" />
                <p className="text-sm text-body italic line-clamp-4 break-words">{quote}</p>
              </div>
            )}
            {quoteTruncated && (
              <p className="text-xs text-warn -mt-2">
                {t("truncated", { max: MAX_QUOTE_CHARS })}
              </p>
            )}

            <div>
              <label className="block text-sm font-semibold text-label mb-2">{t("taskType")}</label>
              <div className="flex flex-wrap gap-2">
                {TASK_TYPES.map((tt) => (
                  <button
                    key={tt.value}
                    type="button"
                    onClick={() => setTaskType(tt.value)}
                    className={clsx(
                      "text-sm font-semibold px-3.5 py-1.5 rounded-full border transition-colors",
                      taskType === tt.value
                        ? "bg-primary text-white border-primary"
                        : "bg-background text-body border-border hover:border-primary hover:text-primary-dark"
                    )}
                  >
                    {t(`task_${tt.value}`)}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <label htmlFor="claude-question" className="block text-sm font-semibold text-label mb-2">
                {t("yourRequest")}
              </label>
              <textarea
                id="claude-question"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder={placeholder}
                rows={4}
                maxLength={2000}
                required
                className="w-full resize-y rounded-lg border border-border bg-background px-3 py-2.5 text-sm text-body placeholder:text-muted-light focus:outline-none focus:border-primary"
              />
            </div>

            {error && (
              <div className="bg-red-50 border border-red-200 text-red-700 px-3 py-2.5 rounded-lg flex items-start gap-2.5 text-sm">
                <AlertCircle size={16} className="shrink-0 mt-0.5" />
                <p>{error}</p>
              </div>
            )}

            <div className="flex justify-end">
              <button
                type="submit"
                disabled={loading || !question.trim()}
                className="flex items-center gap-2 text-sm font-semibold px-4 py-2.5 rounded-full bg-primary text-white hover:bg-primary-dark disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {loading ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
                {t("submit")}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
