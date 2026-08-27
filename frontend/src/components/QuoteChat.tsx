"use client";

import { useEffect, useRef, useState } from "react";
import {
  MessageCircleQuestion,
  X,
  Send,
  Loader2,
  Quote as QuoteIcon,
  History,
  ArrowLeft,
  MessagesSquare,
  Newspaper,
  ExternalLink,
} from "lucide-react";
import clsx from "clsx";
import { formatDistanceToNow, format } from "date-fns";
import { vi } from "date-fns/locale";
import { api } from "@/lib/api";
import type { ChatSessionSummary, ChatSource, ChatTurn } from "@/lib/types";
import { streamQuoteChat } from "@/lib/quoteChatStream";

interface FloatingTrigger {
  x: number;
  y: number;
  quote: string;
}

interface DisplayMessage extends ChatTurn {
  streaming?: boolean;
  sources?: ChatSource[];
}

/**
 * Bọc quanh nội dung báo cáo: người dùng bôi đen 1 đoạn -> hiện nút "Hỏi AI" nổi
 * cạnh vùng chọn -> mở panel chat neo vào đúng đoạn đó, có gợi ý câu hỏi phổ biến
 * và trả lời streaming (SSE) từ backend.
 */
export function QuoteChat({ reportDate, children }: { reportDate: string; children: React.ReactNode }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const [trigger, setTrigger] = useState<FloatingTrigger | null>(null);
  const [activeQuote, setActiveQuote] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [suggestionsLoading, setSuggestionsLoading] = useState(false);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);

  const [historyOpen, setHistoryOpen] = useState(false);
  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [sessionsLoaded, setSessionsLoaded] = useState(false);

  // Phát hiện bôi đen văn bản bên trong nội dung báo cáo.
  useEffect(() => {
    if (activeQuote) return; // panel đang mở — không tranh chấp với việc chọn text trong panel

    let timeoutId: ReturnType<typeof setTimeout>;

    function handleSelectionChange() {
      clearTimeout(timeoutId);
      timeoutId = setTimeout(() => {
        const selection = window.getSelection();
        if (!selection || selection.isCollapsed) {
          setTrigger(null);
          return;
        }
        const text = selection.toString().trim();
        if (!text || text.length < 3 || !selection.anchorNode || !containerRef.current?.contains(selection.anchorNode)) {
          setTrigger(null);
          return;
        }
        const rect = selection.getRangeAt(0).getBoundingClientRect();
        setTrigger({ x: rect.left + rect.width / 2, y: rect.top, quote: text });
      }, 150); // Debounce 150ms để tối ưu hiệu năng khi bôi đen
    }

    document.addEventListener("selectionchange", handleSelectionChange);
    return () => {
      clearTimeout(timeoutId);
      document.removeEventListener("selectionchange", handleSelectionChange);
    };
  }, [activeQuote]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => () => abortRef.current?.abort(), []);

  async function fetchSuggestions(quote: string) {
    setSuggestionsLoading(true);
    try {
      const res = await api.post(`/api/reports/${reportDate}/quote-chat/suggestions`, { quote });
      setSuggestions(res.data.questions || []);
    } catch {
      setSuggestions([]);
    } finally {
      setSuggestionsLoading(false);
    }
  }

  function openChat() {
    if (!trigger) return;
    const quote = trigger.quote;
    setActiveQuote(quote);
    setSessionId(null); // đoạn trích mới -> phiên chat mới, backend sẽ tạo session khi gửi câu hỏi đầu tiên
    setMessages([]);
    setInput("");
    setTrigger(null);
    window.getSelection()?.removeAllRanges();
    fetchSuggestions(quote);
  }

  function closeChat() {
    abortRef.current?.abort();
    setActiveQuote(null);
    setSessionId(null);
    setMessages([]);
    setSuggestions([]);
    setInput("");
    setSending(false);
    setHistoryOpen(false);
  }

  async function fetchSessions() {
    setSessionsLoading(true);
    try {
      const res = await api.get(`/api/reports/${reportDate}/quote-chat/sessions`);
      setSessions(res.data || []);
      setSessionsLoaded(true);
    } catch {
      setSessions([]);
    } finally {
      setSessionsLoading(false);
    }
  }

  function toggleHistory() {
    setHistoryOpen((open) => {
      const next = !open;
      if (next && !sessionsLoaded) fetchSessions();
      return next;
    });
  }

  async function selectSession(s: ChatSessionSummary) {
    if (s.id === sessionId) {
      setHistoryOpen(false);
      return;
    }
    abortRef.current?.abort();
    setSending(false);
    try {
      const res = await api.get(`/api/reports/${reportDate}/quote-chat/sessions/${s.id}`);
      const detail = res.data as { id: number; quote: string; messages: ChatTurn[] };
      setActiveQuote(detail.quote);
      setSessionId(detail.id);
      setMessages(detail.messages);
      setSuggestions([]);
      setInput("");
      setHistoryOpen(false);
    } catch {
      // Bỏ qua — giữ nguyên phiên hiện tại nếu tải lỗi.
    }
  }

  async function sendQuestion(question: string) {
    const q = question.trim();
    if (!q || !activeQuote || sending) return;

    setMessages((prev) => [...prev, { role: "user", content: q }, { role: "assistant", content: "", streaming: true }]);
    setInput("");
    setSending(true);

    const controller = new AbortController();
    abortRef.current = controller;

    const appendDelta = (delta: string) => {
      setMessages((prev) => {
        const next = [...prev];
        const last = next[next.length - 1];
        if (last?.role === "assistant") next[next.length - 1] = { ...last, content: last.content + delta };
        return next;
      });
    };
    const finish = (extra?: string) => {
      setMessages((prev) => {
        const next = [...prev];
        const last = next[next.length - 1];
        if (last?.role === "assistant") {
          next[next.length - 1] = { ...last, content: last.content || extra || "", streaming: false };
        }
        return next;
      });
      setSending(false);
    };

    await streamQuoteChat({
      reportDate,
      question: q,
      sessionId,
      quote: sessionId ? undefined : activeQuote,
      signal: controller.signal,
      onMeta: (meta) => {
        setSessionId(meta.sessionId);
        if (meta.sources.length > 0) {
          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last?.role === "assistant") next[next.length - 1] = { ...last, sources: meta.sources };
            return next;
          });
        }
      },
      onDelta: appendDelta,
      onDone: () => {
        finish();
        if (sessionsLoaded) fetchSessions(); // cập nhật lịch sử: phiên mới hoặc thời gian sửa gần nhất
      },
      onError: (message) => finish(`⚠️ ${message}`),
    });
  }

  return (
    <div ref={containerRef} className="relative">
      {children}

      {trigger && (
        <button
          style={{ position: "fixed", left: trigger.x, top: trigger.y - 44, transform: "translateX(-50%)" }}
          onClick={openChat}
          className="z-50 flex items-center gap-1.5 bg-primary text-white text-xs font-semibold px-3 py-2 rounded-full shadow-[var(--shadow-medium)] hover:bg-primary-dark transition-colors"
        >
          <MessageCircleQuestion size={14} />
          Hỏi AI
        </button>
      )}

      {activeQuote && (
        <div className="fixed inset-0 z-[60] flex justify-end">
          <div className="absolute inset-0 bg-black/20" onClick={() => closeChat()} />
          <div className="relative h-full flex shadow-[var(--shadow-medium)]">
            {/* Sidebar lịch sử chat — trên desktop hiện song song bên cạnh khung chat;
                trên mobile thay thế khung chat (đỡ chật), quay lại bằng nút mũi tên. */}
            <div
              className={clsx(
                "h-full bg-background border-border-soft flex-col shrink-0 overflow-hidden",
                historyOpen ? "flex w-full sm:w-[280px] sm:border-r" : "hidden w-0"
              )}
            >
              <div className="flex items-center gap-2 px-4 py-3.5 border-b border-border shrink-0">
                <button
                  onClick={() => setHistoryOpen(false)}
                  className="text-muted-light hover:text-foreground transition-colors sm:hidden"
                  aria-label="Quay lại khung chat"
                >
                  <ArrowLeft size={18} />
                </button>
                <div className="flex items-center gap-2 text-label font-semibold text-sm">
                  <History size={16} className="text-primary" />
                  Lịch sử hỏi đáp
                </div>
              </div>

              <div className="flex-1 overflow-y-auto">
                {sessionsLoading ? (
                  <div className="flex items-center justify-center py-10 text-muted-light">
                    <Loader2 size={18} className="animate-spin" />
                  </div>
                ) : sessions.length === 0 ? (
                  <div className="px-4 py-8 text-center">
                    <MessagesSquare size={28} className="mx-auto text-muted-light mb-2" />
                    <p className="text-[12.5px] text-muted-light leading-relaxed">
                      Chưa có lịch sử. Bôi đen một đoạn trong báo cáo để bắt đầu hỏi đáp.
                    </p>
                  </div>
                ) : (
                  <ul className="py-1.5">
                    {sessions.map((s) => (
                      <li key={s.id}>
                        <button
                          onClick={() => selectSession(s)}
                          className={clsx(
                            "w-full text-left px-4 py-2.5 border-l-2 transition-colors",
                            s.id === sessionId
                              ? "border-primary bg-tint/50"
                              : "border-transparent hover:bg-tint/30"
                          )}
                        >
                          <p className="text-[12.5px] leading-snug text-body italic line-clamp-2">
                            {s.quote}
                          </p>
                          <p className="text-[11px] text-muted-light mt-1">
                            {formatDistanceToNow(new Date(s.updated_at), { addSuffix: true, locale: vi })}
                          </p>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>

            <div
              className={clsx(
                "relative w-full sm:w-[420px] h-full bg-background border-l border-border flex-col",
                historyOpen ? "hidden sm:flex" : "flex"
              )}
            >
              <div className="flex items-center justify-between px-4 py-3.5 border-b border-border shrink-0">
                <div className="flex items-center gap-2 text-label font-semibold text-sm">
                  <MessageCircleQuestion size={16} className="text-primary" />
                  Hỏi đáp về đoạn trích
                </div>
                <div className="flex items-center gap-3">
                  <button
                    onClick={toggleHistory}
                    className={clsx(
                      "transition-colors",
                      historyOpen ? "text-primary" : "text-muted-light hover:text-foreground"
                    )}
                    aria-label="Lịch sử hỏi đáp"
                    title="Lịch sử hỏi đáp"
                  >
                    <History size={17} />
                  </button>
                  <button
                    onClick={() => closeChat()}
                    className="text-muted-light hover:text-foreground transition-colors"
                  >
                    <X size={18} />
                  </button>
                </div>
              </div>

            <div className="px-4 py-3 border-b border-border-soft bg-tint/40 shrink-0">
              <div className="flex gap-2 items-start">
                <QuoteIcon size={14} className="text-primary shrink-0 mt-0.5" />
                <p className="text-[13px] leading-relaxed text-body italic line-clamp-4">{activeQuote}</p>
              </div>
            </div>

            <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
              {messages.length === 0 && (
                <p className="text-[13px] text-muted-light">Đặt câu hỏi về đoạn trích trên, hoặc chọn gợi ý bên dưới.</p>
              )}
              {messages.map((m, i) => (
                <div key={i} className={clsx("flex flex-col", m.role === "user" ? "items-end" : "items-start")}>
                  <div
                    className={clsx(
                      "max-w-[85%] rounded-2xl px-3.5 py-2.5 text-[13.5px] leading-relaxed whitespace-pre-wrap",
                      m.role === "user"
                        ? "bg-primary text-white rounded-br-sm"
                        : "bg-surface text-body rounded-bl-sm border border-border"
                    )}
                  >
                    {m.content ? (
                      <>
                        {m.content}
                        {m.streaming && <span className="inline-block w-1.5 h-3.5 bg-primary/60 ml-0.5 align-middle animate-pulse" />}
                      </>
                    ) : m.streaming ? (
                      <Loader2 size={14} className="animate-spin text-muted-light" />
                    ) : null}
                  </div>

                  {m.role === "assistant" && !!m.sources?.length && (
                    <div className="mt-1.5 max-w-[85%] w-full rounded-xl border border-border-soft bg-tint/30 px-3 py-2.5">
                      <p className="flex items-center gap-1.5 text-[11px] font-semibold text-label mb-1.5">
                        <Newspaper size={12} className="text-primary" />
                        Danh sách tin tức tham khảo
                      </p>
                      <ul className="space-y-1.5">
                        {m.sources.map((s, si) => (
                          <li key={si} className="flex items-start gap-1.5">
                            <ExternalLink size={11} className="text-muted-light shrink-0 mt-[3px]" />
                            <a
                              href={s.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-[12px] leading-snug text-primary-dark hover:text-primary hover:underline break-words"
                            >
                              {s.title || s.source_name || s.url}
                              {s.published_at && (
                                <span className="text-muted-light font-normal">
                                  {" "}
                                  ({format(new Date(s.published_at), "dd/MM/yyyy")})
                                </span>
                              )}
                            </a>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              ))}
              <div ref={messagesEndRef} />
            </div>

            {messages.length === 0 && (
              <div className="px-4 pb-3 flex flex-wrap gap-2 shrink-0">
                {suggestionsLoading ? (
                  <span className="text-[12px] text-muted-light flex items-center gap-1.5">
                    <Loader2 size={12} className="animate-spin" /> Đang gợi ý câu hỏi...
                  </span>
                ) : (
                  suggestions.map((q, i) => (
                    <button
                      key={i}
                      onClick={() => sendQuestion(q)}
                      className="text-[12px] px-2.5 py-1.5 rounded-full border border-border-soft bg-surface text-body hover:border-primary hover:text-primary-dark transition-colors"
                    >
                      {q}
                    </button>
                  ))
                )}
              </div>
            )}

            <form
              onSubmit={(e) => {
                e.preventDefault();
                sendQuestion(input);
              }}
              className="flex items-center gap-2 px-4 py-3 border-t border-border shrink-0"
            >
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Hỏi thêm về đoạn trích..."
                disabled={sending}
                className="flex-1 text-[13.5px] px-3 py-2 rounded-full border border-border-soft bg-surface focus:outline-none focus:border-primary disabled:opacity-60"
              />
              <button
                type="submit"
                disabled={sending || !input.trim()}
                className="w-9 h-9 flex items-center justify-center rounded-full bg-primary text-white disabled:opacity-40 hover:bg-primary-dark transition-colors shrink-0"
              >
                {sending ? <Loader2 size={16} className="animate-spin" /> : <Send size={15} />}
              </button>
            </form>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
