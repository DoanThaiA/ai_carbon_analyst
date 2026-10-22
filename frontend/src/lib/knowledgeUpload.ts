import { api } from "@/lib/api";

// Khớp NGUYÊN VĂN với services/knowledge_extraction.py::KNOWLEDGE_MAX_BYTES và
// services/minio_service.py::validate_knowledge_upload — chỉ là lớp UX (báo lỗi sớm),
// backend luôn kiểm lại file THẬT (dung lượng, magic bytes, trùng nội dung).
export const KNOWLEDGE_MAX_BYTES = 20 * 1024 * 1024; // 20 MB
export const KNOWLEDGE_ACCEPT =
  ".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document";
const MEDIA_BY_EXT: Record<string, string> = {
  pdf: "application/pdf",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
};

export type TranslateFn = (key: string, values?: Record<string, string | number>) => string;

/** `detail` (chuỗi) trong lỗi axios của FastAPI, không có thì `fallback`. */
export function apiErrorDetail(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } } | null)?.response?.data?.detail;
  return typeof detail === "string" ? detail : fallback;
}

/** Loại MIME theo đuôi file — một số trình duyệt để trống `file.type` với .docx. */
export function knowledgeMediaType(file: File): string | null {
  const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
  return MEDIA_BY_EXT[ext] ?? null;
}

export function validateKnowledgeFile(file: File, t: TranslateFn): string | null {
  if (file.name.toLowerCase().endsWith(".doc")) return t("errDoc");
  if (!knowledgeMediaType(file)) return t("errFormat");
  if (file.size > KNOWLEDGE_MAX_BYTES) return t("errTooLarge", { limit: KNOWLEDGE_MAX_BYTES / (1024 * 1024) });
  return null;
}

/** Tiêu đề gợi ý từ tên file: bỏ đuôi, đổi _/- thành khoảng trắng. */
export function titleFromFileName(name: string): string {
  return name.replace(/\.(pdf|docx)$/i, "").replace(/[_]+/g, " ").replace(/\s+/g, " ").trim();
}

/** Xin presigned URL (admin) rồi PUT thẳng file lên MinIO — trả file_key để gửi kèm metadata. */
export async function uploadKnowledgeFile(file: File, t: TranslateFn): Promise<string> {
  const contentType = knowledgeMediaType(file) as string;
  let uploadUrl: string;
  let fileKey: string;
  try {
    const res = await api.post("/api/admin/knowledge/presigned-url", {
      file_name: file.name,
      content_type: contentType,
      size: file.size,
    });
    uploadUrl = res.data.upload_url;
    fileKey = res.data.file_key;
  } catch (err) {
    throw new Error(apiErrorDetail(err, t("errPresign")));
  }
  const putRes = await fetch(uploadUrl, { method: "PUT", body: file, headers: { "Content-Type": contentType } });
  if (!putRes.ok) throw new Error(t("errUpload", { status: putRes.status }));
  return fileKey;
}
