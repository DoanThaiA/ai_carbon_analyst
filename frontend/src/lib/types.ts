export type ReportStatus = "draft" | "published" | "generating" | "failed";

export interface Report {
  id: number;
  report_date: string;
  status: ReportStatus;
  content: any;
  // Bản dịch tiếng Anh của content (services/report_translation.py) — null khi chưa dịch
  // hoặc bản dịch đã cũ so với content hiện tại.
  content_en?: any | null;
  error_message?: string | null;
  created_at: string;
  published_at: string | null;
}

// Khối "Ước tính giá CBAM" (content["2"].cbam_estimate) — services/cbam_estimate.py::build_cbam_estimate.
export interface CbamEstimate {
  quarter: string;
  as_of: string;
  auctions_done: number;
  auctions_total: number;
  volume_done_mt: number;
  volume_total_mt: number;
  fixed_share_pct: number;
  qtd_vwap: number | null;
  spot: number;
  spot_vs_qtd_pct: number | null;
  estimate: number;
  ci_level: number;
  ci_low: number | null;
  ci_high: number | null;
  daily_vol_pct: number | null;
  signal: "increase" | "hold" | "reduce";
  signal_reason: "quarter_fixed" | "quarter_not_started" | "quarter_mostly_fixed" | "spot_below_qtd" | "spot_above_qtd" | "spot_near_qtd";
  hedge_gap_pct: number;
  hedge_fixed_share_pct: number;
  tactical_max_pct?: number;
  // % theo KHỐI LƯỢNG QUÝ (báo cáo từ 18/10/2026; bản cũ không có)
  open_share_pct?: number;
  core_share_of_quarter_pct?: number;
  tactical_share_of_quarter_pct?: number;
  // Chi phí CBAM / tấn sản phẩm theo GIẢ ĐỊNH phát thải & benchmark (services/cbam_estimate.py::product_exposure)
  product_exposure?: {
    key: string;
    cn: string;
    embedded: number;
    benchmark: number;
    free_allocation_factor_pct: number;
    net_tco2_per_t: number;
    cost_eur_per_t: number;
    cost_low: number | null;
    cost_high: number | null;
    per_0_1_tco2_eur?: number;
    // Từ 18/10/2026: khấu trừ = hệ số CBAM × CSCF × benchmark CBAM theo mã CN; kịch bản giá trị mặc định.
    route?: string | null;
    cscf_pct?: number;
    deduction_tco2_per_t?: number;
    default_markup_pct?: number;
    defaults?: { origin: string; default_value: number; with_markup: number; net_tco2_per_t: number; cost_eur_per_t: number }[];
    benchmark_source?: string | null;
    benchmark_verified?: boolean;
    assumed: boolean;
  }[];
  // Phiên đấu giá cuối quý + ngày đáo hạn futures Dec (cbam_products.yaml) — báo cáo từ 18/10/2026.
  last_auction_date?: string | null;
  futures_expiry?: string | null;
  last_auction: { date: string; platform: string; price: number; cover_ratio: number | null } | null;
  upcoming: { count: number; volume_mt: number; first_date: string | null; last_date: string | null } | null;
  prev_quarter: { quarter: string; vwap: number | null; official: number | null } | null;
}

// Khối "Dự báo trước: đúng hay sai" (content["2"].forecast_review) — services/forecast_review.py.
export interface ForecastReviewItem {
  report_date: string;
  direction: string;
  probability: string | null;
  ref_close: number;
  last_date: string;
  last_close: number;
  change_pct: number;
  sessions: number;
  // on_track/diverging: chiều %Δ đúng/ngược nhưng chưa hết khung ngắn hạn — chưa kết luận.
  verdict: "correct" | "wrong" | "unclear" | "on_track" | "diverging" | "direction_only";
  high_confidence_miss: boolean;
  trade: {
    side: "buy" | "sell";
    order_type?: "limit" | "stop";
    entry_low: number;
    entry_high: number;
    stop: number;
    target: number | null;
    status: "not_filled" | "open" | "stopped" | "target_hit" | "expired" | "cancelled_target";
    filled_date: string | null;
    fill_price?: number | null;
    exit_date: string | null;
    exit_price: number | null;
  } | null;
  // Báo cáo từ 18/10/2026: cách chấm, lệch xa nhất trong khung, lỗi thiết kế lệnh.
  graded_by?: "trade" | "direction" | "path";
  max_excursion_pct?: number;
  design_flaws?: (
    | { kind: "low_rr"; rr: number; entry: number; risk: number; reward: number }
    | { kind: "tight_stop"; distance: number; atr: number; atr_multiple: number; entry: number }
  )[];
}

// Mục 3: 1 hành động duy nhất cho sổ phòng hộ SIM + đối chiếu lệnh đang mở (services/report_generator.py
// _finalize_hedge_fields). Báo cáo cũ không có → thẻ vị thế dùng hedge_note của kịch bản ngắn hạn.
export interface HedgeAction {
  // increase/hold/reduce = nhãn chung với thẻ CBAM (phần lõi luôn bám lịch đấu giá); buy_now/... = báo cáo cũ.
  action: "increase" | "hold" | "reduce" | "buy_now" | "buy_scheduled" | "defer";
  core?: string;
  text: string;
  tactical_max_pct?: number;
}

export interface OpenPosition {
  report_date: string;
  side: "buy" | "sell";
  entry_low: number;
  entry_high: number;
  filled_date: string | null;
  fill_price: number | null;
  stop: number;
  target: number | null;
  decision: "keep" | "move" | "close" | null;
  text: string;
}

export interface ForecastReview {
  items: ForecastReviewItem[];
  wrong_in_row: number;
  // Báo cáo cũ không có 2 trường dưới.
  calibration?: { probability: string; correct: number; total: number; direction_only?: number }[];
  rules?: {
    horizon_sessions: number; direction_threshold_pct: number; sideways_max_pct: number; order_valid_sessions: number;
    calibration_reports?: number; sideways_path_max_pct?: number; min_rr?: number; min_stop_atr?: number; method_since?: string;
  };
  // Báo cáo tạo nên tỷ lệ cửa sổ (có thể gồm báo cáo đã ra khỏi bảng) + tỷ lệ lũy kế — báo cáo từ 18/10/2026.
  calibration_window?: { report_date: string; probability: string | null; verdict: string }[];
  calibration_all?: { probability: string; correct: number; total: number; direction_only?: number }[];
  calibration_all_reports?: number;
  calibration_all_excluded?: number;
  // Dự báo ngây thơ trên cùng các báo cáo đã có kết luận (services/forecast_review.py::baseline_from).
  baseline?: {
    model: { correct: number; total: number };
    always_sideways: { correct: number; total: number };
    momentum: { correct: number; total: number; sessions: number };
  };
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

// --- Biên bản QC báo cáo (Claude/Jack gửi qua MCP) — backend api/routers/admin_qc_reviews.py, CHỈ admin ---

export type QcVerdict = "pass" | "pass_with_fixes" | "fail";
export type QcSeverity = "critical" | "major" | "minor";
export type QcFindingStatus = "open" | "fixed" | "wont_fix";

export interface QcCriterionDef {
  key: string;
  label: string;
  weight: number;
  guide: string;
}

export interface QcCriterionScore {
  key: string;
  label: string;
  weight: number;
  score: number;
  comment: string;
}

export interface QcFinding {
  id: number;
  position: number;
  severity: QcSeverity;
  criterion: string;
  criterion_label: string;
  section: string | null;
  location: string | null;
  field_path: string | null;
  title: string;
  current_text: string | null;
  expected_text: string | null;
  evidence: string | null;
  verification: "verified" | "partially_verified" | "unverified";
  source_refs: { label: string; url: string }[];
  repeat_count: number;
  status: QcFindingStatus;
  status_note: string | null;
  updated_at: string | null;
}

export interface QcReviewSummary {
  id: number;
  report_date: string;
  run_no: number;
  source: string;
  reviewer: string | null;
  report_status: string;
  verdict: QcVerdict;
  claude_verdict: QcVerdict | null;
  overall_score: number;
  criteria: QcCriterionScore[];
  critical_count: number;
  major_count: number;
  minor_count: number;
  jack_rule_score: number | null;
  jack_rule_issue_count: number | null;
  summary: string;
  strengths: string[];
  priorities: { rank: number; text: string; expected_gain: number | null }[];
  created_at: string | null;
  is_stale: boolean;
  current_report_status?: string | null;
}

export interface QcReviewDetail extends QcReviewSummary {
  body_markdown: string | null;
  findings: QcFinding[];
}

// "Sửa báo cáo theo QC" (services/report_qc_fix.py) — bước xem trước.
export interface QcFixChange {
  id: number;
  path: string;
  before: string;
  after: string;
  finding_ids: number[];
  edits: { old: string; new: string; finding_ids: number[]; note: string | null }[];
  rejected: { old: string | null; new: string | null; reason: string }[];
  jack_regressions: string[];
  selected: boolean;
}

export interface QcFixPreview {
  report_date: string;
  review_id: number;
  run_no: number;
  base_hash: string;
  review_is_stale: boolean;
  changes: QcFixChange[];
  findings: {
    id: number;
    title: string;
    severity: QcSeverity;
    section: string | null;
    status: "proposed" | "manual" | "skipped" | "rejected";
    reason: string | null;
  }[];
  jack_score_before: number | null;
  jack_score_after: number | null;
}

export interface QcFixApplyResult {
  content: Report["content"];
  content_hash: string;
  finding_ids: number[];
  changed_paths: string[];
}

export interface QcReportReviews {
  report_date: string;
  report_status: string;
  latest: QcReviewDetail | null;
  history: QcReviewSummary[];
  auto_publish: { time: string | null; allowed: boolean; reason: string };
  criteria: QcCriterionDef[];
}

export interface QcReviewListResponse {
  total: number;
  items: QcReviewSummary[];
  criteria: QcCriterionDef[];
}

export interface QcReviewStats {
  reports: number;
  by_verdict: Record<QcVerdict, number>;
  avg_score: number | null;
  trend: { report_date: string; score: number; verdict: QcVerdict }[];
  criteria_avg: { key: string; label: string; weight: number; avg: number | null }[];
  recurring: { fingerprint: string; reports: number; title: string; criterion: string; section: string | null }[];
}

export interface QcAgentToken {
  id: number;
  name: string;
  token_prefix: string;
  created_by: string;
  created_at: string;
  expires_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

export interface QcAgentTokensResponse {
  items: QcAgentToken[];
  routine_prompt: string;
  auto_report_time: string;
  auto_publish_time: string | null;
}
