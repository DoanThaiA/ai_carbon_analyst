"use client";

import { useEffect, useState } from "react";
import { Plus, Pencil, Trash2, Save, X, AlertCircle, CheckCircle2, XCircle, ImagePlus, Loader2 } from "lucide-react";
import clsx from "clsx";
import { api } from "@/lib/api";
import type { Attachment } from "@/lib/types";
import { uploadFileToMinIO, validateFile } from "@/lib/minioUpload";
import { AttachmentBadge } from "@/components/AttachmentBadge";

const MAX_EVIDENCE_IMAGES = 3;

interface ReleaseNote {
  id: number;
  order_index: number;
  note_date: string;
  customer_request: string;
  change_description: string;
  test_result: string | null;
  evidence_images: Attachment[] | null;
  status: "dat" | "chua_dat";
}

const todayStr = () => new Date().toISOString().slice(0, 10);

const EMPTY_FORM = {
  note_date: todayStr(),
  customer_request: "",
  change_description: "",
  test_result: "",
  evidence_images: [] as Attachment[],
  status: "chua_dat" as "dat" | "chua_dat",
};

// "YYYY-MM-DD" -> "dd/MM/yyyy" cho hiển thị.
function formatDate(dateStr: string) {
  const parts = dateStr.split("-");
  return parts.length === 3 ? `${parts[2]}/${parts[1]}/${parts[0]}` : dateStr;
}

/** Upload từng ảnh đã chọn lên MinIO (tối đa MAX_EVIDENCE_IMAGES ảnh/mục),
 * nối vào mảng ảnh hiện có — dùng chung cho form Thêm mục và form Sửa. */
async function uploadEvidenceImages(
  files: FileList,
  existing: Attachment[],
  onChange: (next: Attachment[]) => void,
  onError: (msg: string) => void,
  onUploadingChange: (uploading: boolean) => void,
) {
  const selected = Array.from(files);
  if (existing.length + selected.length > MAX_EVIDENCE_IMAGES) {
    onError(`Chỉ được đính kèm tối đa ${MAX_EVIDENCE_IMAGES} ảnh minh chứng cho 1 mục.`);
    return;
  }
  onError("");
  onUploadingChange(true);
  const next = [...existing];
  for (const file of selected) {
    if (!file.type.startsWith("image/")) {
      onError(`"${file.name}": chỉ nhận file ảnh (JPEG/PNG/WEBP).`);
      continue;
    }
    const invalid = validateFile(file);
    if (invalid) {
      onError(invalid);
      continue;
    }
    try {
      next.push(await uploadFileToMinIO(file));
    } catch (err: any) {
      onError(err?.message || `Upload "${file.name}" thất bại.`);
    }
  }
  onChange(next);
  onUploadingChange(false);
}

/** Lưới thumbnail ảnh minh chứng — dùng chung cho form Thêm mục & Sửa
 * (có nút xoá từng ảnh) và cho khối "Kết quả kiểm tra thực tế" chỉ-xem. */
function EvidenceImagesField({
  images,
  editable,
  uploading,
  error,
  onChange,
  onError,
  onUploadingChange,
}: {
  images: Attachment[];
  editable: boolean;
  uploading?: boolean;
  error?: string;
  onChange?: (next: Attachment[]) => void;
  onError?: (msg: string) => void;
  onUploadingChange?: (uploading: boolean) => void;
}) {
  if (!editable && images.length === 0) return null;

  return (
    <div className="mt-1.5">
      <div className="flex items-center gap-2 flex-wrap">
        {images.map((img, i) => (
          <div key={img.file_key} className="relative">
            <AttachmentBadge attachment={img} />
            {editable && (
              <button
                type="button"
                onClick={() => onChange?.(images.filter((_, j) => j !== i))}
                className="absolute -top-1.5 -right-1.5 bg-background border border-border rounded-full p-0.5 text-muted-light hover:text-down"
                aria-label="Xoá ảnh"
              >
                <X size={10} />
              </button>
            )}
          </div>
        ))}
        {editable && images.length < MAX_EVIDENCE_IMAGES && (
          <label className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-dashed border-border-soft text-xs text-muted-light cursor-pointer hover:border-primary hover:text-primary-dark transition-colors">
            {uploading ? <Loader2 size={14} className="animate-spin" /> : <ImagePlus size={14} />}
            Đính kèm ảnh minh chứng
            <input
              type="file"
              accept="image/jpeg,image/png,image/webp"
              multiple
              className="hidden"
              disabled={uploading}
              onChange={async (e) => {
                if (!e.target.files?.length || !onChange || !onError || !onUploadingChange) return;
                await uploadEvidenceImages(e.target.files, images, onChange, onError, onUploadingChange);
                e.target.value = "";
              }}
            />
          </label>
        )}
      </div>
      {error && <p className="text-[11px] text-down mt-1">{error}</p>}
    </div>
  );
}

export default function ReleaseNotesPage() {
  const [notes, setNotes] = useState<ReleaseNote[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showAddForm, setShowAddForm] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [formImageError, setFormImageError] = useState("");
  const [formImageUploading, setFormImageUploading] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState<Partial<ReleaseNote>>({});
  const [editImageError, setEditImageError] = useState("");
  const [editImageUploading, setEditImageUploading] = useState(false);

  const fetchNotes = async () => {
    try {
      const res = await api.get("/api/admin/release-notes");
      setNotes(res.data);
    } catch (err) {
      setError("Không thể tải danh sách release note.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchNotes();
  }, []);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    try {
      await api.post("/api/admin/release-notes", form);
      setForm(EMPTY_FORM);
      setFormImageError("");
      setShowAddForm(false);
      await fetchNotes();
    } catch (err: any) {
      setError(err.response?.data?.detail || "Lỗi khi thêm release note.");
    }
  };

  const startEdit = (n: ReleaseNote) => {
    setEditingId(n.id);
    setEditForm({ ...n, evidence_images: n.evidence_images ?? [] });
    setEditImageError("");
  };

  const handleUpdate = async (id: number) => {
    setError("");
    try {
      const { note_date, customer_request, change_description, test_result, evidence_images, status } = editForm;
      await api.put(`/api/admin/release-notes/${id}`, {
        note_date,
        customer_request,
        change_description,
        test_result,
        evidence_images,
        status,
      });
      setEditingId(null);
      await fetchNotes();
    } catch (err: any) {
      setError(err.response?.data?.detail || "Lỗi khi cập nhật release note.");
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm("Xoá mục release note này?")) return;
    try {
      await api.delete(`/api/admin/release-notes/${id}`);
      await fetchNotes();
    } catch (err: any) {
      setError(err.response?.data?.detail || "Lỗi khi xoá release note.");
    }
  };

  const inputCls = "w-full px-2 py-1.5 border border-border rounded text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary";
  const textareaCls = clsx(inputCls, "min-h-[60px] resize-y");

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4">
        <div>
          <h2 className="text-3xl font-bold uppercase tracking-tight text-heading mb-2">Release Note</h2>
          <p className="text-body">Theo dõi yêu cầu khách hàng và kết quả kiểm tra thực tế trên báo cáo</p>
        </div>
        <button onClick={() => setShowAddForm(v => !v)} className="btn-pill py-2.5 shrink-0">
          <Plus size={18} />
          Thêm mục
        </button>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg flex items-start gap-3">
          <AlertCircle size={20} className="shrink-0 mt-0.5" />
          <p>{error}</p>
        </div>
      )}

      {showAddForm && (
        <form onSubmit={handleCreate} className="bg-background border border-border rounded-2xl p-5 space-y-3">
          <input required type="date" value={form.note_date} onChange={e => setForm({ ...form, note_date: e.target.value })} className={inputCls + " w-48"} />
          <textarea required placeholder="Yêu cầu khách hàng" value={form.customer_request} onChange={e => setForm({ ...form, customer_request: e.target.value })} className={textareaCls} />
          <textarea required placeholder="Nội dung đã chỉnh sửa" value={form.change_description} onChange={e => setForm({ ...form, change_description: e.target.value })} className={textareaCls} />
          <textarea placeholder="Kết quả kiểm tra thực tế trên báo cáo" value={form.test_result} onChange={e => setForm({ ...form, test_result: e.target.value })} className={textareaCls} />
          <EvidenceImagesField
            images={form.evidence_images}
            editable
            uploading={formImageUploading}
            error={formImageError}
            onChange={(imgs) => setForm(f => ({ ...f, evidence_images: imgs }))}
            onError={setFormImageError}
            onUploadingChange={setFormImageUploading}
          />
          <select value={form.status} onChange={e => setForm({ ...form, status: e.target.value as "dat" | "chua_dat" })} className={inputCls + " w-48"}>
            <option value="dat">✔ ĐẠT</option>
            <option value="chua_dat">✘ CHƯA ĐẠT</option>
          </select>
          <div className="flex items-center gap-3">
            <button type="submit" className="btn-pill py-2 px-5">Lưu</button>
            <button type="button" onClick={() => setShowAddForm(false)} className="text-body hover:text-primary text-sm font-semibold">Huỷ</button>
          </div>
        </form>
      )}

      {loading ? (
        <div className="bg-background border border-border rounded-2xl h-40 animate-pulse" />
      ) : (
        <>
          {/* Bảng đầy đủ — chỉ hiện từ md trở lên. Nhiều cột chữ dài (yêu cầu
              khách hàng, nội dung chỉnh sửa, kết quả kiểm tra) không co vừa màn
              hình hẹp dù cho overflow-x-auto — dùng dạng card ở dưới cho mobile. */}
          <div className="hidden md:block bg-background border border-border rounded-2xl overflow-hidden overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-muted-light text-xs uppercase tracking-wider">
                  <th className="px-4 py-3 font-semibold w-28">Ngày</th>
                  <th className="px-4 py-3 font-semibold">Yêu cầu khách hàng</th>
                  <th className="px-4 py-3 font-semibold">Nội dung đã chỉnh sửa</th>
                  <th className="px-4 py-3 font-semibold">Kết quả kiểm tra thực tế</th>
                  <th className="px-4 py-3 font-semibold">Kết luận</th>
                  <th className="px-4 py-3 font-semibold text-right">Hành động</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {notes.map((n) => {
                  const isEditing = editingId === n.id;
                  return (
                    <tr key={n.id} className="align-top">
                      <td className="px-4 py-2.5 text-muted-light whitespace-nowrap">
                        {isEditing ? (
                          <input type="date" value={editForm.note_date} onChange={e => setEditForm({ ...editForm, note_date: e.target.value })} className={inputCls} />
                        ) : (
                          formatDate(n.note_date)
                        )}
                      </td>
                      <td className="px-4 py-2.5 whitespace-pre-wrap">
                        {isEditing ? <textarea value={editForm.customer_request} onChange={e => setEditForm({ ...editForm, customer_request: e.target.value })} className={textareaCls} /> : n.customer_request}
                      </td>
                      <td className="px-4 py-2.5 whitespace-pre-wrap">
                        {isEditing ? <textarea value={editForm.change_description} onChange={e => setEditForm({ ...editForm, change_description: e.target.value })} className={textareaCls} /> : n.change_description}
                      </td>
                      <td className="px-4 py-2.5 whitespace-pre-wrap text-muted-light">
                        {isEditing ? <textarea value={editForm.test_result ?? ""} onChange={e => setEditForm({ ...editForm, test_result: e.target.value })} className={textareaCls} /> : (n.test_result || "—")}
                        <EvidenceImagesField
                          images={isEditing ? (editForm.evidence_images ?? []) : (n.evidence_images ?? [])}
                          editable={isEditing}
                          uploading={editImageUploading}
                          error={editImageError}
                          onChange={(imgs) => setEditForm({ ...editForm, evidence_images: imgs })}
                          onError={setEditImageError}
                          onUploadingChange={setEditImageUploading}
                        />
                      </td>
                      <td className="px-4 py-2.5">
                        {isEditing ? (
                          <select value={editForm.status} onChange={e => setEditForm({ ...editForm, status: e.target.value as "dat" | "chua_dat" })} className={inputCls}>
                            <option value="dat">✔ ĐẠT</option>
                            <option value="chua_dat">✘ CHƯA ĐẠT</option>
                          </select>
                        ) : n.status === "dat" ? (
                          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-semibold bg-tint text-primary-dark">
                            <CheckCircle2 size={14} /> ĐẠT
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-semibold bg-red-50 text-down">
                            <XCircle size={14} /> CHƯA ĐẠT
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-2.5">
                        <div className="flex items-center justify-end gap-2">
                          {isEditing ? (
                            <>
                              <button onClick={() => handleUpdate(n.id)} className="p-1.5 rounded hover:bg-tint text-primary-dark"><Save size={16} /></button>
                              <button onClick={() => setEditingId(null)} className="p-1.5 rounded hover:bg-surface text-body"><X size={16} /></button>
                            </>
                          ) : (
                            <>
                              <button onClick={() => startEdit(n)} className="p-1.5 rounded hover:bg-surface text-body"><Pencil size={16} /></button>
                              <button onClick={() => handleDelete(n.id)} className="p-1.5 rounded hover:bg-red-50 text-down"><Trash2 size={16} /></button>
                            </>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Dạng card — chỉ hiện dưới md, mỗi mục xếp dọc theo từng trường thay
              vì bảng nhiều cột, tránh phải kéo ngang trên màn hình hẹp. */}
          <div className="md:hidden space-y-3">
            {notes.map((n) => {
              const isEditing = editingId === n.id;
              return (
                <div key={n.id} className="bg-background border border-border rounded-2xl p-4 space-y-3">
                  <div className="flex items-center justify-between">
                    {isEditing ? (
                      <input type="date" value={editForm.note_date} onChange={e => setEditForm({ ...editForm, note_date: e.target.value })} className={inputCls + " w-40"} />
                    ) : (
                      <span className="text-xs font-semibold text-muted-light">{formatDate(n.note_date)}</span>
                    )}
                    <div className="flex items-center gap-2">
                      {isEditing ? (
                        <>
                          <button onClick={() => handleUpdate(n.id)} className="p-1.5 rounded hover:bg-tint text-primary-dark"><Save size={16} /></button>
                          <button onClick={() => setEditingId(null)} className="p-1.5 rounded hover:bg-surface text-body"><X size={16} /></button>
                        </>
                      ) : (
                        <>
                          <button onClick={() => startEdit(n)} className="p-1.5 rounded hover:bg-surface text-body"><Pencil size={16} /></button>
                          <button onClick={() => handleDelete(n.id)} className="p-1.5 rounded hover:bg-red-50 text-down"><Trash2 size={16} /></button>
                        </>
                      )}
                    </div>
                  </div>

                  <div>
                    <p className="text-xs font-semibold uppercase tracking-wider text-muted-light mb-1">Yêu cầu khách hàng</p>
                    {isEditing ? (
                      <textarea value={editForm.customer_request} onChange={e => setEditForm({ ...editForm, customer_request: e.target.value })} className={textareaCls} />
                    ) : (
                      <p className="text-sm whitespace-pre-wrap">{n.customer_request}</p>
                    )}
                  </div>

                  <div>
                    <p className="text-xs font-semibold uppercase tracking-wider text-muted-light mb-1">Nội dung đã chỉnh sửa</p>
                    {isEditing ? (
                      <textarea value={editForm.change_description} onChange={e => setEditForm({ ...editForm, change_description: e.target.value })} className={textareaCls} />
                    ) : (
                      <p className="text-sm whitespace-pre-wrap">{n.change_description}</p>
                    )}
                  </div>

                  <div>
                    <p className="text-xs font-semibold uppercase tracking-wider text-muted-light mb-1">Kết quả kiểm tra thực tế</p>
                    {isEditing ? (
                      <textarea value={editForm.test_result ?? ""} onChange={e => setEditForm({ ...editForm, test_result: e.target.value })} className={textareaCls} />
                    ) : (
                      <p className="text-sm whitespace-pre-wrap text-muted-light">{n.test_result || "—"}</p>
                    )}
                    <EvidenceImagesField
                      images={isEditing ? (editForm.evidence_images ?? []) : (n.evidence_images ?? [])}
                      editable={isEditing}
                      uploading={editImageUploading}
                      error={editImageError}
                      onChange={(imgs) => setEditForm({ ...editForm, evidence_images: imgs })}
                      onError={setEditImageError}
                      onUploadingChange={setEditImageUploading}
                    />
                  </div>

                  <div>
                    <p className="text-xs font-semibold uppercase tracking-wider text-muted-light mb-1">Kết luận</p>
                    {isEditing ? (
                      <select value={editForm.status} onChange={e => setEditForm({ ...editForm, status: e.target.value as "dat" | "chua_dat" })} className={inputCls}>
                        <option value="dat">✔ ĐẠT</option>
                        <option value="chua_dat">✘ CHƯA ĐẠT</option>
                      </select>
                    ) : n.status === "dat" ? (
                      <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-semibold bg-tint text-primary-dark">
                        <CheckCircle2 size={14} /> ĐẠT
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-semibold bg-red-50 text-down">
                        <XCircle size={14} /> CHƯA ĐẠT
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
