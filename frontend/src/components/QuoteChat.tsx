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
  ThumbsUp,
  ThumbsDown,
  MessageSquareWarning,
  Paperclip,
  FileText,
} from "lucide-react";
import clsx from "clsx";
import { formatDistanceToNow, format } from "date-fns";
import { vi } from "date-fns/locale";
import { api } from "@/lib/api";
import type { Attachment, ChatRating, ChatSessionSummary, ChatSource, ChatTurn } from "@/lib/types";
import { streamQuoteChat } from "@/lib/quoteChatStream";
import {
  ACCEPT_ATTR,
  MAX_ATTACHMENTS_PER_TURN,
  uploadFileToMinIO,
  validateFile,
} from "@/lib/minioUpload";
import { AttachmentBadge, type DisplayAttachment } from "@/components/AttachmentBadge";
import { JennyFeedbackModal } from "@/components/JennyFeedbackModal";

interface FloatingTrigger {
  x: number;
  y: number;
  quote: string;
}

interface DisplayMessage extends Omit<ChatTurn, "attachments"> {
  streaming?: boolean;
  sources?: ChatSource[];
  attachments?: DisplayAttachment[] | null;
}

// 1 file người dùng vừa chọn, đang/đã upload nền lên MinIO — chưa gửi kèm câu hỏi.
interface PendingAttachment {
  id: string;
  file: File;
  status: "uploading" | "done" | "error";
  attachment?: Attachment;
  previewUrl?: string;
  error?: string;
}

// LLM hay tự chèn "**đậm**" để nhấn mạnh dù không được yêu cầu — bong bóng chat
// render text thô (whitespace-pre-wrap) nên trước đây hiện nguyên dấu ** rất xấu.
// Parse nhẹ, không phụ thuộc thư viện markdown ngoài: chỉ nhận cặp ** đã đóng đủ,
// cặp ** chưa đóng (đang stream dở) giữ nguyên dạng chữ cho tới khi delta tiếp
// theo mang theo dấu đóng — tự khớp lại ở lần render sau, không cần xử lý riêng.
function renderInlineMarkdown(text: string): React.ReactNode {
  const parts = text.split(/(\*\*[^*\n]+\*\*)/g);
  return parts.map((part, i) =>
    part.startsWith("**") && part.endsWith("**") && part.length > 4 ? (
      <strong key={i} className="font-semibold">
        {part.slice(2, -2)}
      </strong>
    ) : (
      <span key={i}>{part}</span>
    )
  );
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
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [trigger, setTrigger] = useState<FloatingTrigger | null>(null);
  const [chatOpen, setChatOpen] = useState(false);
  const [activeQuote, setActiveQuote] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [suggestionsLoading, setSuggestionsLoading] = useState(false);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);

  const [pendingAttachments, setPendingAttachments] = useState<PendingAttachment[]>([]);
  const [attachmentError, setAttachmentError] = useState("");

  const [feedbackOpen, setFeedbackOpen] = useState(false);

  const [historyOpen, setHistoryOpen] = useState(false);
  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [sessionsLoaded, setSessionsLoaded] = useState(false);

  const [rating, setRating] = useState<ChatRating | null>(null);
  const [showReasonBox, setShowReasonBox] = useState(false);
  const [reasonDraft, setReasonDraft] = useState("");
  const [ratingSubmitting, setRatingSubmitting] = useState(false);
  const [ratingError, setRatingError] = useState("");

  // Phát hiện bôi đen văn bản bên trong nội dung báo cáo.
  useEffect(() => {
    if (chatOpen) return; // panel đang mở (kể cả chưa có quote) — không tranh chấp với việc chọn text trong panel

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
  }, [chatOpen]);

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

  function clearPendingAttachments() {
    setPendingAttachments((prev) => {
      prev.forEach((p) => p.previewUrl && URL.revokeObjectURL(p.previewUrl));
      return [];
    });
    setAttachmentError("");
  }

  function handleAttachClick() {
    fileInputRef.current?.click();
  }

  function handleFilesSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files || []);
    e.target.value = ""; // cho phép chọn lại đúng file đó lần sau
    if (files.length === 0) return;

    setAttachmentError("");
    if (pendingAttachments.length + files.length > MAX_ATTACHMENTS_PER_TURN) {
      setAttachmentError(`Chỉ được đính kèm tối đa ${MAX_ATTACHMENTS_PER_TURN} file cho mỗi lượt hỏi.`);
      return;
    }

    const accepted: File[] = [];
    for (const file of files) {
      const error = validateFile(file);
      if (error) {
        setAttachmentError(error);
        continue;
      }
      accepted.push(file);
    }
    if (accepted.length === 0) return;

    const newPending: PendingAttachment[] = accepted.map((file) => ({
      id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
      file,
      status: "uploading",
      previewUrl: file.type.startsWith("image/") ? URL.createObjectURL(file) : undefined,
    }));
    setPendingAttachments((prev) => [...prev, ...newPending]);

    newPending.forEach((pending) => {
      uploadFileToMinIO(pending.file)
        .then((attachment) => {
          setPendingAttachments((prev) =>
            prev.map((p) => (p.id === pending.id ? { ...p, status: "done", attachment } : p))
          );
        })
        .catch((err: Error) => {
          setPendingAttachments((prev) =>
            prev.map((p) => (p.id === pending.id ? { ...p, status: "error", error: err.message } : p))
          );
        });
    });
  }

  function removePendingAttachment(id: string) {
    setPendingAttachments((prev) => {
      const target = prev.find((p) => p.id === id);
      if (target?.previewUrl) URL.revokeObjectURL(target.previewUrl);
      return prev.filter((p) => p.id !== id);
    });
  }

  function resetRatingState() {
    setRating(null);
    setShowReasonBox(false);
    setReasonDraft("");
    setRatingSubmitting(false);
    setRatingError("");
  }

  function openChat() {
    if (!trigger) return;
    const quote = trigger.quote;
    setChatOpen(true);
    setActiveQuote(quote);
    setSessionId(null); // đoạn trích mới -> phiên chat mới, backend sẽ tạo session khi gửi câu hỏi đầu tiên
    setMessages([]);
    setInput("");
    setTrigger(null);
    resetRatingState();
    clearPendingAttachments();
    window.getSelection()?.removeAllRanges();
    fetchSuggestions(quote);
  }

  function closeChat() {
    abortRef.current?.abort();
    setChatOpen(false);
    setActiveQuote(null);
    setSessionId(null);
    setMessages([]);
    setSuggestions([]);
    setInput("");
    setSending(false);
    setHistoryOpen(false);
    resetRatingState();
    clearPendingAttachments();
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

  // Nút mở lịch sử hỏi đáp trực tiếp (không cần bôi đen văn bản trước) — mở
  // thẳng khung chat với lịch sử hiện sẵn, chưa gắn với đoạn trích nào cho tới
  // khi người dùng chọn 1 phiên cũ hoặc bôi đen đoạn văn bản mới.
  function openHistoryPanel() {
    setChatOpen(true);
    setHistoryOpen(true);
    if (!sessionsLoaded) fetchSessions();
  }

  async function selectSession(s: ChatSessionSummary) {
    if (s.id === sessionId) return;
    abortRef.current?.abort();
    setSending(false);
    try {
      const res = await api.get(`/api/reports/${reportDate}/quote-chat/sessions/${s.id}`);
      const detail = res.data as {
        id: number;
        quote: string;
        messages: ChatTurn[];
        rating: ChatRating | null;
        rating_reason: string | null;
      };
      setActiveQuote(detail.quote);
      setSessionId(detail.id);
      setMessages(detail.messages);
      setSuggestions([]);
      setInput("");
      clearPendingAttachments();
      // Lịch sử KHÔNG tự đóng khi chọn phiên — chỉ đóng khi người dùng bấm lại
      // biểu tượng đồng hồ (toggleHistory), để có thể chọn xem nhiều phiên liên tiếp.
      resetRatingState();
      setRating(detail.rating);
      setReasonDraft(detail.rating_reason || "");
    } catch {
      // Bỏ qua — giữ nguyên phiên hiện tại nếu tải lỗi.
    }
  }

  async function sendQuestion(question: string) {
    const q = question.trim();
    const isUploadingAttachment = pendingAttachments.some((p) => p.status === "uploading");
    if (!q || !activeQuote || sending || isUploadingAttachment) return;

    // Chỉ những file upload thành công (status "done") mới có file_key để gửi
    // kèm — file lỗi bị bỏ qua lặng lẽ (đã có cảnh báo lúc chọn/upload).
    const readyAttachments = pendingAttachments.filter(
      (p): p is PendingAttachment & { attachment: Attachment } => p.status === "done" && !!p.attachment
    );
    const bubbleAttachments: DisplayAttachment[] = readyAttachments.map((p) => ({
      ...p.attachment,
      previewUrl: p.previewUrl,
    }));

    setMessages((prev) => [
      ...prev,
      { role: "user", content: q, attachments: bubbleAttachments.length > 0 ? bubbleAttachments : undefined },
      { role: "assistant", content: "", streaming: true },
    ]);
    setInput("");
    setPendingAttachments([]);
    setAttachmentError("");
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
      attachments: readyAttachments.map((p) => p.attachment),
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

  async function submitRating(value: ChatRating, reason?: string) {
    if (!sessionId || ratingSubmitting) return;
    setRatingSubmitting(true);
    setRatingError("");
    try {
      const res = await api.post(`/api/reports/${reportDate}/quote-chat/sessions/${sessionId}/rating`, {
        rating: value,
        reason: reason?.trim() || undefined,
      });
      setRating(res.data.rating);
      setShowReasonBox(false);
      if (sessionsLoaded) fetchSessions();
    } catch (err: any) {
      const detail = err.response?.data?.detail;
      const message = Array.isArray(detail) ? detail[0]?.msg : detail;
      setRatingError(message || "Lỗi khi gửi đánh giá, vui lòng thử lại.");
    } finally {
      setRatingSubmitting(false);
    }
  }

  return (
    <div ref={containerRef} className="relative">
      {children}

      {trigger && (
        <button
          style={{ position: "fixed", left: trigger.x, top: trigger.y - 44, transform: "translateX(-50%)" }}
          onClick={openChat}
          className="z-50 flex items-center gap-1.5 bg-primary text-white text-xs font-semibold px-3 py-2 rounded-full shadow-[var(--shadow-medium)] hover:bg-primary-dark transition-colors print:hidden"
        >
          <MessageCircleQuestion size={14} />
          Hỏi AI
        </button>
      )}

      {!chatOpen && (
        <div className="fixed bottom-6 right-6 z-40 flex items-center gap-2 print:hidden">
          <button
            onClick={() => setFeedbackOpen(true)}
            title="Phản ánh thái độ của Jenny"
            className="flex items-center gap-2 bg-background border border-border text-body text-sm font-semibold px-4 py-3 rounded-full shadow-[var(--shadow-medium)] hover:border-primary hover:text-primary-dark transition-colors"
          >
            <MessageSquareWarning size={18} className="text-primary" />
            <span className="hidden sm:inline">Phản ánh</span>
          </button>
          <button
            onClick={openHistoryPanel}
            className="flex items-center gap-2 bg-background border border-border text-body text-sm font-semibold px-4 py-3 rounded-full shadow-[var(--shadow-medium)] hover:border-primary hover:text-primary-dark transition-colors"
          >
            <History size={18} className="text-primary" />
            Lịch sử hỏi đáp
          </button>
        </div>
      )}

      <JennyFeedbackModal open={feedbackOpen} onClose={() => setFeedbackOpen(false)} />

      {chatOpen && (
        <div className="fixed inset-0 z-[60] flex justify-end print:hidden">
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

            {activeQuote && (
              <div className="px-4 py-3 border-b border-border-soft bg-tint/40 shrink-0">
                <div className="flex gap-2 items-start">
                  <QuoteIcon size={14} className="text-primary shrink-0 mt-0.5" />
                  <p className="text-[13px] leading-relaxed text-body italic line-clamp-4">{activeQuote}</p>
                </div>
              </div>
            )}

            <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
              {!activeQuote ? (
                <div className="h-full flex flex-col items-center justify-center text-center gap-2 px-6">
                  <MessagesSquare size={28} className="text-muted-light" />
                  <p className="text-[13px] text-muted-light leading-relaxed">
                    Chọn 1 phiên trong lịch sử bên trái, hoặc bôi đen một đoạn trong báo cáo để bắt đầu hỏi đáp mới.
                  </p>
                </div>
              ) : (
                messages.length === 0 && (
                  <p className="text-[13px] text-muted-light">Đặt câu hỏi về đoạn trích trên, hoặc chọn gợi ý bên dưới.</p>
                )
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
                        {renderInlineMarkdown(m.content)}
                        {m.streaming && <span className="inline-block w-1.5 h-3.5 bg-primary/60 ml-0.5 align-middle animate-pulse" />}
                      </>
                    ) : m.streaming ? (
                      <Loader2 size={14} className="animate-spin text-muted-light" />
                    ) : null}
                  </div>

                  {!!m.attachments?.length && (
                    <div className={clsx("mt-1.5 flex flex-wrap gap-1.5 max-w-[85%]", m.role === "user" ? "justify-end" : "justify-start")}>
                      {m.attachments.map((att, ai) => (
                        <AttachmentBadge key={ai} attachment={att} />
                      ))}
                    </div>
                  )}

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

            {sessionId && (
              <div className="px-4 py-2.5 border-t border-border-soft shrink-0">
                {rating ? (
                  <div
                    className={clsx(
                      "flex items-center gap-1.5 text-[12px] font-semibold",
                      rating === "good" ? "text-primary-dark" : "text-down"
                    )}
                  >
                    {rating === "good" ? <ThumbsUp size={13} /> : <ThumbsDown size={13} />}
                    Bạn đã đánh giá phiên này là {rating === "good" ? "Tốt" : "Không tốt"}
                  </div>
                ) : showReasonBox ? (
                  <div className="space-y-2">
                    <textarea
                      value={reasonDraft}
                      onChange={(e) => setReasonDraft(e.target.value)}
                      placeholder="Vì sao câu trả lời chưa tốt? (bắt buộc)"
                      rows={2}
                      className="w-full text-[12.5px] px-3 py-2 rounded-lg border border-border-soft bg-surface focus:outline-none focus:border-primary resize-none"
                    />
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => submitRating("bad", reasonDraft)}
                        disabled={ratingSubmitting || !reasonDraft.trim()}
                        className="text-[12px] font-semibold px-3 py-1.5 rounded-full bg-down text-white disabled:opacity-40 hover:opacity-90 transition-opacity"
                      >
                        {ratingSubmitting ? "Đang gửi..." : "Gửi đánh giá"}
                      </button>
                      <button
                        onClick={() => {
                          setShowReasonBox(false);
                          setRatingError("");
                        }}
                        disabled={ratingSubmitting}
                        className="text-[12px] font-semibold px-3 py-1.5 rounded-full text-muted-light hover:bg-surface transition-colors"
                      >
                        Huỷ
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="flex items-center gap-2.5 flex-wrap">
                    <span className="text-[12px] text-muted-light">Phiên hỏi đáp này thế nào?</span>
                    <button
                      onClick={() => submitRating("good")}
                      disabled={ratingSubmitting}
                      className="flex items-center gap-1.5 text-[12px] font-semibold px-2.5 py-1.5 rounded-full border border-border-soft text-body hover:border-primary hover:text-primary-dark disabled:opacity-40 transition-colors"
                    >
                      <ThumbsUp size={13} /> Tốt
                    </button>
                    <button
                      onClick={() => setShowReasonBox(true)}
                      disabled={ratingSubmitting}
                      className="flex items-center gap-1.5 text-[12px] font-semibold px-2.5 py-1.5 rounded-full border border-border-soft text-body hover:border-down hover:text-down disabled:opacity-40 transition-colors"
                    >
                      <ThumbsDown size={13} /> Không tốt
                    </button>
                  </div>
                )}
                {ratingError && <p className="text-[11px] text-down mt-1.5">{ratingError}</p>}
              </div>
            )}

            {activeQuote && messages.length === 0 && (
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

            {pendingAttachments.length > 0 && (
              <div className="px-4 pt-2 flex flex-wrap gap-2 shrink-0">
                {pendingAttachments.map((p) => (
                  <div
                    key={p.id}
                    className={clsx(
                      "flex items-center gap-1.5 pl-1.5 pr-2 py-1.5 rounded-lg border text-[11px] max-w-[170px]",
                      p.status === "error" ? "border-down/40 bg-down/5" : "border-border-soft bg-surface"
                    )}
                    title={p.status === "error" ? p.error : p.file.name}
                  >
                    {p.previewUrl ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={p.previewUrl} alt={p.file.name} className="w-6 h-6 rounded object-cover shrink-0" />
                    ) : (
                      <FileText
                        size={13}
                        className={clsx("shrink-0", p.file.type === "application/pdf" ? "text-red-500" : "text-blue-500")}
                      />
                    )}
                    <span className="truncate">{p.file.name}</span>
                    {p.status === "uploading" && <Loader2 size={11} className="animate-spin text-muted-light shrink-0" />}
                    <button
                      type="button"
                      onClick={() => removePendingAttachment(p.id)}
                      className="text-muted-light hover:text-foreground shrink-0"
                      aria-label="Xoá file đính kèm"
                    >
                      <X size={12} />
                    </button>
                  </div>
                ))}
              </div>
            )}
            {attachmentError && <p className="px-4 pt-1.5 text-[11px] text-down shrink-0">{attachmentError}</p>}

            <form
              onSubmit={(e) => {
                e.preventDefault();
                sendQuestion(input);
              }}
              className="flex items-center gap-2 px-4 py-3 border-t border-border shrink-0"
            >
              <input
                ref={fileInputRef}
                type="file"
                accept={ACCEPT_ATTR}
                multiple
                hidden
                onChange={handleFilesSelected}
              />
              <button
                type="button"
                onClick={handleAttachClick}
                disabled={sending || !activeQuote || pendingAttachments.length >= MAX_ATTACHMENTS_PER_TURN}
                title="Đính kèm ảnh/PDF/Word"
                className="w-9 h-9 flex items-center justify-center rounded-full border border-border-soft text-muted-light hover:text-primary hover:border-primary disabled:opacity-40 transition-colors shrink-0"
              >
                <Paperclip size={16} />
              </button>
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder={activeQuote ? "Hỏi thêm về đoạn trích..." : "Chọn 1 phiên hoặc bôi đen đoạn trích để hỏi..."}
                disabled={sending || !activeQuote}
                className="flex-1 text-[13.5px] px-3 py-2 rounded-full border border-border-soft bg-surface focus:outline-none focus:border-primary disabled:opacity-60"
              />
              <button
                type="submit"
                disabled={
                  sending ||
                  !activeQuote ||
                  !input.trim() ||
                  pendingAttachments.some((p) => p.status === "uploading")
                }
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
