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

export interface HotNewsItem {
  id: number;
  title: string | null;
  url: string;
  source: string;
  hot_news_reason: string | null;
  published_at: string | null;
  crawled_at: string;
}
