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

export interface ChatSessionSummary {
  id: number;
  quote: string;
  created_at: string;
  updated_at: string;
}

export interface ChatSessionDetail {
  id: number;
  quote: string;
  created_at: string;
  messages: ChatTurn[];
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
