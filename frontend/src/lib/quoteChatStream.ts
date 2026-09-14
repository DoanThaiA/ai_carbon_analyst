import { API_BASE_URL, refreshSession } from "@/lib/api";
import type { Attachment, ChatSource } from "@/lib/types";

interface QuoteChatStreamArgs {
  reportDate: string;
  question: string;
  // Câu hỏi đầu tiên của 1 đoạn trích: gửi `quote`, chưa có `sessionId`.
  // Các câu hỏi tiếp theo: chỉ cần `sessionId` — quote + lịch sử được server
  // tự nạp lại từ Postgres (bộ nhớ ngắn hạn), không cần gửi lại.
  sessionId?: number | null;
  quote?: string;
  // File đính kèm CỦA CÂU HỎI NÀY (đã upload lên MinIO trước đó, chỉ gửi
  // file_key/file_name/media_type — không gửi lại nội dung file).
  attachments?: Attachment[];
  onMeta: (meta: { sessionId: number; sources: ChatSource[] }) => void;
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
  attachments,
  onMeta,
  onDelta,
  onDone,
  onError,
  signal,
}: QuoteChatStreamArgs): Promise<void> {
  const doFetch = () =>
    fetch(`${API_BASE_URL}/api/reports/${reportDate}/quote-chat`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        session_id: sessionId ?? null,
        quote,
        attachments: attachments && attachments.length > 0 ? attachments : undefined,
      }),
      signal,
    });

  let res: Response;
  try {
    res = await doFetch();
    if (res.status === 401) {
      // access_token vừa hết hạn — thử refresh bằng refresh_token (còn hạn
      // dài hơn nhiều) rồi gọi lại đúng 1 lần, thay vì báo lỗi ngay.
      const refreshed = await refreshSession();
      if (refreshed) res = await doFetch();
    }
  } catch {
    onError("Không thể kết nối đến server.");
    return;
  }

  if (!res.ok || !res.body) {
    if (res.status === 401) {
      onError("Phiên đăng nhập đã hết hạn.");
      return;
    }
    // 429 (hết quota/ngày) và các lỗi validate khác trả JSON { detail } — ưu
    // tiên hiện đúng message đó thay vì câu chung chung.
    let detail: string | undefined;
    try {
      const data = await res.json();
      if (typeof data?.detail === "string") detail = data.detail;
    } catch {
      // không phải JSON hoặc body rỗng — bỏ qua, dùng fallback bên dưới
    }
    onError(detail || "Đã xảy ra lỗi, vui lòng thử lại.");
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
        const m = payload as { session_id: number; sources: ChatSource[] };
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
