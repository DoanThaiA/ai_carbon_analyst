import { API_BASE_URL } from "@/lib/api";

interface QuoteChatStreamArgs {
  reportDate: string;
  question: string;
  // Câu hỏi đầu tiên của 1 đoạn trích: gửi `quote`, chưa có `sessionId`.
  // Các câu hỏi tiếp theo: chỉ cần `sessionId` — quote + lịch sử được server
  // tự nạp lại từ Postgres (bộ nhớ ngắn hạn), không cần gửi lại.
  sessionId?: number | null;
  quote?: string;
  onMeta: (meta: { sessionId: number; sources: unknown[] }) => void;
  onDelta: (text: string) => void;
  onDone: () => void;
  onError: (message: string) => void;
  signal?: AbortSignal;
}

// EventSource gốc không hỗ trợ POST body, mà quote có thể khá dài —
// nên đọc SSE thủ công qua fetch + ReadableStream thay vì dùng EventSource.
export async function streamQuoteChat({
  reportDate,
  question,
  sessionId,
  quote,
  onMeta,
  onDelta,
  onDone,
  onError,
  signal,
}: QuoteChatStreamArgs): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}/api/reports/${reportDate}/quote-chat`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, session_id: sessionId ?? null, quote }),
      signal,
    });
  } catch {
    onError("Không thể kết nối đến server.");
    return;
  }

  if (!res.ok || !res.body) {
    onError(res.status === 401 ? "Phiên đăng nhập đã hết hạn." : "Đã xảy ra lỗi, vui lòng thử lại.");
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Mỗi sự kiện SSE cách nhau bởi 1 dòng trống ("\n\n").
    let sepIndex: number;
    while ((sepIndex = buffer.indexOf("\n\n")) !== -1) {
      const rawEvent = buffer.slice(0, sepIndex);
      buffer = buffer.slice(sepIndex + 2);

      let eventName = "message";
      let dataLine = "";
      for (const line of rawEvent.split("\n")) {
        if (line.startsWith("event:")) eventName = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLine += line.slice(5).trim();
      }
      if (!dataLine) continue;

      let payload: unknown;
      try {
        payload = JSON.parse(dataLine);
      } catch {
        continue;
      }

      if (eventName === "meta") {
        const m = payload as { session_id: number; sources: unknown[] };
        onMeta({ sessionId: m.session_id, sources: m.sources ?? [] });
      } else if (eventName === "delta" && typeof payload === "string") {
        onDelta(payload);
      } else if (eventName === "error") {
        const message = (payload as { message?: string })?.message || "Đã xảy ra lỗi, vui lòng thử lại.";
        onError(message);
        return;
      } else if (eventName === "done") {
        onDone();
        return;
      }
    }
  }
  onDone();
}
