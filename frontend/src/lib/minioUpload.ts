import { api } from "@/lib/api";
import type { Attachment, AttachmentMediaType } from "@/lib/types";

// Khớp NGUYÊN VĂN với services/minio_service.py — sửa 1 nơi mà không sửa nơi
// kia sẽ khiến FE cho chọn file mà backend từ chối (hoặc ngược lại).
export const MAX_ATTACHMENTS_PER_TURN = 3;
export const IMAGE_MAX_BYTES = 5 * 1024 * 1024; // 5 MB
export const DOCUMENT_MAX_BYTES = 10 * 1024 * 1024; // 10 MB

const IMAGE_MEDIA_TYPES = new Set<string>(["image/jpeg", "image/png", "image/webp"]);
const DOCUMENT_MEDIA_TYPES = new Set<string>([
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
]);

export const ALLOWED_MEDIA_TYPES = new Set<string>([...IMAGE_MEDIA_TYPES, ...DOCUMENT_MEDIA_TYPES]);

// Dùng cho thuộc tính `accept` của <input type="file"> — trình duyệt lọc theo
// đuôi file lẫn MIME type khai báo.
export const ACCEPT_ATTR = ".jpg,.jpeg,.png,.webp,.pdf,.docx,image/jpeg,image/png,image/webp,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document";

function maxBytesFor(mediaType: string): number {
  return IMAGE_MEDIA_TYPES.has(mediaType) ? IMAGE_MAX_BYTES : DOCUMENT_MAX_BYTES;
}

/** Kiểm tra 1 file trước khi upload — trả về thông báo lỗi (string) nếu không
 * hợp lệ, null nếu OK. Chỉ là lớp UX (báo lỗi sớm) — backend luôn kiểm tra lại
 * (xem services/minio_service.py), vì FE có thể bị bypass. */
export function validateFile(file: File): string | null {
  if (file.type === "application/msword") {
    return `"${file.name}": chưa hỗ trợ file .doc — vui lòng lưu lại dưới dạng .docx.`;
  }
  if (!ALLOWED_MEDIA_TYPES.has(file.type)) {
    return `"${file.name}": định dạng không được hỗ trợ (chỉ nhận Ảnh JPEG/PNG/WEBP, PDF, hoặc Word .docx).`;
  }
  const limit = maxBytesFor(file.type);
  if (file.size > limit) {
    return `"${file.name}": vượt quá dung lượng cho phép (${limit / (1024 * 1024)}MB).`;
  }
  return null;
}

/** Xin presigned URL rồi PUT thẳng file lên MinIO — trả về Attachment để gửi
 * kèm payload /quote-chat. Ném lỗi (string message) nếu 1 trong 2 bước fail. */
export async function uploadFileToMinIO(file: File): Promise<Attachment> {
  let uploadUrl: string;
  let fileKey: string;
  try {
    const presignRes = await api.post("/api/upload/presigned-url", {
      file_name: file.name,
      content_type: file.type,
      size: file.size,
    });
    uploadUrl = presignRes.data.upload_url;
    fileKey = presignRes.data.file_key;
  } catch (err: any) {
    const detail = err?.response?.data?.detail;
    throw new Error(typeof detail === "string" ? detail : `Không xin được quyền upload cho "${file.name}".`);
  }

  const putRes = await fetch(uploadUrl, {
    method: "PUT",
    body: file,
    headers: { "Content-Type": file.type },
  });
  if (!putRes.ok) {
    throw new Error(`Upload "${file.name}" thất bại (HTTP ${putRes.status}).`);
  }

  return { file_name: file.name, file_key: fileKey, media_type: file.type as AttachmentMediaType };
}

/** URL tạm (10 phút) để xem lại 1 file đã đính kèm (thumbnail ảnh / mở PDF-Word
 * trong tab mới) — gọi lại mỗi lần cần hiện, không cache lâu vì URL hết hạn. */
export async function fetchAttachmentViewUrl(fileKey: string): Promise<string> {
  const res = await api.get("/api/upload/view-url", { params: { file_key: fileKey } });
  return res.data.url as string;
}
