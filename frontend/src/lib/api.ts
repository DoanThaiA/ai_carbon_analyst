import axios from "axios";

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// withCredentials: true — bắt buộc để trình duyệt gửi/nhận cookie session
// (access_token, httpOnly) giữa frontend (:3000) và backend (:8000).
export const api = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: true,
});
