export type ReportStatus = "draft" | "published" | "generating" | "failed";

export interface Report {
  id: number;
  report_date: string;
  status: ReportStatus;
  content: any;
  error_message?: string | null;
  created_at: string;
  published_at: string | null;
}

export interface ReportSummary {
  id: number;
  report_date: string;
  status: ReportStatus;
  error_message?: string | null;
  created_at: string;
  published_at: string | null;
}

// Định dạng file đính kèm cho phép trong Quote Chat — khớp với
// ALLOWED_ATTACHMENT_MEDIA_TYPES bên schemas/chat_models.py.
export type AttachmentMediaType =
  | "image/jpeg"
  | "image/png"
  | "image/webp"
  | "application/pdf"
  | "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

export interface Attachment {
  file_name: string;
  file_key: string;
  media_type: AttachmentMediaType;
}

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
  attachments?: Attachment[] | null;
}

export interface ChatSource {
  url: string;
  title: string | null;
  source_name: string | null;
  published_at: string | null;
}

export type ChatRating = "good" | "bad";

export interface ChatSessionSummary {
  id: number;
  quote: string;
  created_at: string;
  updated_at: string;
  rating: ChatRating | null;
  rating_reason: string | null;
}

export interface ChatSessionDetail {
  id: number;
  quote: string;
  created_at: string;
  messages: ChatTurn[];
  rating: ChatRating | null;
  rating_reason: string | null;
}

export interface AdminChatSessionSummary {
  id: number;
  user_email: string;
  report_date: string;
  quote: string;
  rating: ChatRating | null;
  rating_reason: string | null;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface AdminChatSessionListResponse {
  items: AdminChatSessionSummary[];
  total: number;
}

export interface AdminChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  example_id: number | null;
}

export interface AdminChatSessionDetail {
  id: number;
  user_email: string;
  report_date: string;
  quote: string;
  rating: ChatRating | null;
  rating_reason: string | null;
  created_at: string;
  updated_at: string;
  messages: AdminChatMessage[];
}

export interface QuoteChatExample {
  id: number;
  question: string;
  answer: string;
  source_session_id: number | null;
  source_report_date: string | null;
  source_quote: string | null;
  created_by: string | null;
  created_at: string;
}

export interface EuaFrameworkBlock {
  block_id: string;
  title: string;
  default_content: string;
  custom_content: string | null;
  is_customized: boolean;
  updated_at: string | null;
  updated_by: string | null;
}

export interface FeedbackItem {
  id: number;
  user_email: string;
  reporter_name: string | null;
  reporter_role: "user" | "admin";
  content: string;
  created_at: string;
}

export interface FeedbackListResponse {
  items: FeedbackItem[];
  total: number;
}

export interface HotNewsItem {
  id: number;
  title: string | null;
  url: string;
  source: string;
  hot_news_reason: string | null;
  published_at: string | null;
  crawled_at: string;
}

// --- Kết nối Claude Desktop (MCP) — xem backend api/routers/claude_connect.py ---

export type ClaudeTaskType = "other" | "pdf" | "excel" | "strategy";

export interface ApiTokenSummary {
  id: number;
  name: string;
  token_prefix: string;
  created_at: string;
  expires_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

// Token gốc chỉ có ở phản hồi lúc tạo — backend chỉ lưu hash, không lấy lại được.
export interface ApiTokenCreated extends ApiTokenSummary {
  token: string;
}

export interface HandoffCreated {
  handoff_id: string;
  prompt: string;
  expires_at: string;
}

// --- Lucy QC báo cáo trước khi duyệt — xem backend services/report_qc.py ---

export type QCSeverity = "error" | "warning" | "info";
export type QCCheck = "price" | "scenario" | "source" | "calendar" | "biz" | "consistency" | "causal";

export interface QCIssue {
  check: QCCheck;
  // Khớp key của report.content: "1" | "2" | "3" | "4" | "8" | "9" | "biz"
  section: string;
  severity: QCSeverity;
  message: string;
  field_path: string;
}

export interface QCResult {
  id: number;
  report_date: string;
  status: "running" | "done" | "failed";
  overall_score: number | null;
  price_accuracy_score: number | null;
  scenario_score: number | null;
  source_score: number | null;
  calendar_score: number | null;
  biz_score: number | null;
  // 2 check AI (services/report_qc_llm.py) — null nếu không chạy được (LLM lỗi) hoặc QC nhanh.
  consistency_score: number | null;
  causal_score: number | null;
  issues: QCIssue[];
  error_message: string | null;
  checked_at: string;
  checked_by: string | null;
}
