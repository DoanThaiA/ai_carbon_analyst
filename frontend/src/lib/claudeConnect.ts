import { API_BASE_URL } from "@/lib/api";
import type { ApiTokenSummary, ClaudeTaskType } from "@/lib/types";

// Phiên bản gói MCP client (mcp_client/pyproject.toml). uv suy ra phiên bản từ TÊN FILE
// wheel nên tên file phải đúng chuẩn `<tên>-<phiên bản>-py3-none-any.whl` (không dùng
// "latest"); khi phát hành bản mới thì tăng số này + build lại bằng scripts/build_mcp_client.sh
// — đổi URL cũng là cách buộc uv bỏ qua cache bản cũ.
export const MCP_CLIENT_VERSION = "0.1.1";

// Khớp HandoffCreateRequest.quote (max_length=4000) ở backend schemas/claude_models.py.
export const MAX_QUOTE_CHARS = 4000;

export const TASK_TYPES: { value: ClaudeTaskType; label: string; placeholder: string }[] = [
  { value: "other", label: "Phân tích / hỏi đáp", placeholder: "VD: Phân tích sâu hơn nhận định này và các rủi ro chính." },
  { value: "pdf", label: "Sinh file PDF", placeholder: "VD: Soạn memo PDF 2 trang tóm tắt luận điểm này cho lãnh đạo." },
  { value: "excel", label: "Sinh file Excel", placeholder: "VD: Lập bảng Excel kịch bản giá EUA 3 tháng tới theo các giả định." },
  { value: "strategy", label: "Lập chiến lược", placeholder: "VD: Đề xuất chiến lược giao dịch kèm điều kiện vào/ra lệnh và quản trị rủi ro." },
];

export function wheelUrl(): string {
  const configured = process.env.NEXT_PUBLIC_MCP_WHEEL_URL;
  if (configured) return configured;
  const origin = typeof window !== "undefined" ? window.location.origin : "";
  return `${origin}/downloads/carbon_analyst_mcp-${MCP_CLIENT_VERSION}-py3-none-any.whl`;
}

export type DesktopOs = "mac" | "windows";

export const OS_INFO: Record<
  DesktopOs,
  { label: string; configPath: string; defaultUvxPath: string; installCmd: string; whereCmd: string }
> = {
  mac: {
    label: "macOS",
    configPath: "~/Library/Application Support/Claude/claude_desktop_config.json",
    // App desktop trên macOS không thừa hưởng PATH của shell nên phải dùng đường dẫn tuyệt đối.
    defaultUvxPath: "/Users/<tên-máy-của-bạn>/.local/bin/uvx",
    installCmd: "curl -LsSf https://astral.sh/uv/install.sh | sh",
    whereCmd: "which uvx",
  },
  windows: {
    label: "Windows",
    configPath: "%APPDATA%\\Claude\\claude_desktop_config.json",
    defaultUvxPath: "uvx",
    installCmd: 'powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"',
    whereCmd: "where uvx",
  },
};

export function detectOs(): DesktopOs {
  if (typeof navigator !== "undefined" && /win/i.test(navigator.userAgent)) return "windows";
  return "mac";
}

export const TOKEN_PLACEHOLDER = "cat_DÁN_TOKEN_CỦA_BẠN_VÀO_ĐÂY";

export function buildClaudeConfig(opts: { uvxPath: string; token?: string }): string {
  return JSON.stringify(
    {
      mcpServers: {
        "carbon-analyst": {
          command: opts.uvxPath.trim() || "uvx",
          args: ["--from", wheelUrl(), "carbon-analyst-mcp"],
          env: {
            CARBON_API_URL: API_BASE_URL,
            CARBON_API_TOKEN: opts.token || TOKEN_PLACEHOLDER,
          },
        },
      },
    },
    null,
    2
  );
}

export function isTokenActive(t: ApiTokenSummary): boolean {
  return t.revoked_at === null && new Date(t.expires_at).getTime() > Date.now();
}

// Clipboard API cần secure context + đôi khi mất "user activation" sau await — luôn
// trả về boolean để UI có đường lui (nút "Sao chép" thủ công), không ném lỗi.
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

export function apiErrorStatus(err: unknown): number | undefined {
  return (err as { response?: { status?: number } })?.response?.status;
}

export function apiErrorMessage(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && typeof detail[0]?.msg === "string") return detail[0].msg;
  return fallback;
}
