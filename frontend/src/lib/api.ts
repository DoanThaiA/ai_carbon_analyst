import axios from "axios";

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// withCredentials: true — bắt buộc để trình duyệt gửi/nhận cookie session
// (access_token, httpOnly) giữa frontend (:3000) và backend (:8000).
export const api = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: true,
});

// access_token/refresh_token là cookie httpOnly (JS không đọc được), nên
// không thể tự biết đang là phiên admin hay user để gọi đúng endpoint
// refresh. Lưu tạm 1 cờ role (không nhạy cảm) ở localStorage ngay sau khi
// login/OTP verify thành công — chỉ dùng để chọn endpoint refresh, không
// dùng để xác thực (việc đó vẫn do backend quyết định qua cookie thật).
const AUTH_ROLE_KEY = "auth_role";
type AuthRole = "admin" | "user";

export function setAuthRole(role: AuthRole | null) {
  try {
    if (role) window.localStorage.setItem(AUTH_ROLE_KEY, role);
    else window.localStorage.removeItem(AUTH_ROLE_KEY);
  } catch {
    // localStorage có thể bị chặn (private mode/Safari ITP) — bỏ qua, không
    // ảnh hưởng tới cookie thật, chỉ mất khả năng tự-refresh cho tab này.
  }
}

function getAuthRole(): AuthRole | null {
  try {
    const v = window.localStorage.getItem(AUTH_ROLE_KEY);
    return v === "admin" || v === "user" ? v : null;
  } catch {
    return null;
  }
}

const REFRESH_PATH_BY_ROLE: Record<AuthRole, string> = {
  admin: "/api/admin/auth/refresh",
  user: "/api/auth/refresh",
};

// Các endpoint tự thân của luồng auth: không được retry qua interceptor bên
// dưới, tránh vòng lặp vô hạn khi chính request refresh trả về 401.
const AUTH_ENDPOINTS = [
  "/api/admin/auth/login",
  "/api/admin/auth/refresh",
  "/api/auth/otp/request",
  "/api/auth/otp/verify",
  "/api/auth/refresh",
];

let refreshInFlight: Promise<boolean> | null = null;

// Gọi endpoint refresh tương ứng role đang lưu. Dùng chung 1 promise cho các
// request 401 đến đồng thời để không bắn nhiều request refresh song song.
export function refreshSession(): Promise<boolean> {
  const role = getAuthRole();
  if (!role) return Promise.resolve(false);

  if (!refreshInFlight) {
    refreshInFlight = axios
      .post(`${API_BASE_URL}${REFRESH_PATH_BY_ROLE[role]}`, null, { withCredentials: true })
      .then(() => true)
      .catch(() => {
        setAuthRole(null);
        return false;
      })
      .finally(() => {
        refreshInFlight = null;
      });
  }
  return refreshInFlight;
}

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const config = error?.config;
    const status = error?.response?.status;

    const isAuthEndpoint = AUTH_ENDPOINTS.some((p) => config?.url?.includes(p));
    if (status !== 401 || !config || config.__isRetryAfterRefresh || isAuthEndpoint) {
      return Promise.reject(error);
    }

    const refreshed = await refreshSession();
    if (!refreshed) {
      return Promise.reject(error);
    }

    // Access token mới đã được set qua cookie (Set-Cookie của /refresh) —
    // retry đúng 1 lần request gốc, không retry lặp lại nếu vẫn 401.
    config.__isRetryAfterRefresh = true;
    return api(config);
  }
);
