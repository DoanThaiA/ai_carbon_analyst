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

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
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

export interface AdminChatSessionDetail {
  id: number;
  user_email: string;
  report_date: string;
  quote: string;
  rating: ChatRating | null;
  rating_reason: string | null;
  created_at: string;
  updated_at: string;
  messages: ChatTurn[];
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

export interface HotNewsItem {
  id: number;
  title: string | null;
  url: string;
  source: string;
  hot_news_reason: string | null;
  published_at: string | null;
  crawled_at: string;
}
