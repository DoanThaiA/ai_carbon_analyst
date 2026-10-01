"use client";

import { useEffect, useState } from "react";
import { Plus, Trash2, AlertCircle, Mail, Pencil, Check, X } from "lucide-react";
import { format } from "date-fns";
import clsx from "clsx";
import { api } from "@/lib/api";

type RelationType = "superior" | "peer";

interface AllowedUser {
  id: number;
  email: string;
  full_name: string | null;
  job_title: string | null;
  relation_type: RelationType;
  is_active: boolean;
  created_at: string;
}

// Phân quyền + phạm vi tương tác suy ra từ mối quan hệ với Jenny.
const RELATION_META: Record<RelationType, { label: string; access: string; scope: string }> = {
  superior: {
    label: "Cấp trên",
    access: "Admin",
    scope: "Quyền admin: xem, chat, hỏi tư vấn, chỉnh sửa nội dung và đánh giá",
  },
  peer: {
    label: "Đồng cấp",
    access: "Người dùng",
    scope: "Xem, chat, hỏi tư vấn, đánh giá — không được chỉnh sửa",
  },
};

const inputCls =
  "w-full px-3 py-2 border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary";

export default function AdminUsersPage() {
  const [users, setUsers] = useState<AllowedUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [form, setForm] = useState({ email: "", full_name: "", job_title: "", relation_type: "peer" as RelationType });
  // Hàng đang sửa (họ tên / chức danh / mối quan hệ)
  const [editId, setEditId] = useState<number | null>(null);
  const [draft, setDraft] = useState({ full_name: "", job_title: "", relation_type: "peer" as RelationType });

  const fetchUsers = async () => {
    try {
      const res = await api.get("/api/admin/users");
      setUsers(res.data);
    } catch (err) {
      setError("Không thể tải danh sách người dùng.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchUsers();
  }, []);

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    try {
      await api.post("/api/admin/users", form);
      setForm({ email: "", full_name: "", job_title: "", relation_type: "peer" });
      await fetchUsers();
    } catch (err: any) {
      setError(err.response?.data?.detail || "Lỗi khi thêm người dùng.");
    }
  };

  const startEdit = (u: AllowedUser) => {
    setEditId(u.id);
    setDraft({ full_name: u.full_name ?? "", job_title: u.job_title ?? "", relation_type: u.relation_type });
  };

  const saveEdit = async (id: number) => {
    setError("");
    try {
      await api.put(`/api/admin/users/${id}`, draft);
      setEditId(null);
      await fetchUsers();
    } catch (err: any) {
      setError(err.response?.data?.detail || "Lỗi khi cập nhật.");
    }
  };

  const handleToggleActive = async (u: AllowedUser) => {
    try {
      await api.put(`/api/admin/users/${u.id}`, { is_active: !u.is_active });
      await fetchUsers();
    } catch (err: any) {
      setError(err.response?.data?.detail || "Lỗi khi cập nhật.");
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm("Xoá email này khỏi danh sách được phép truy cập?")) return;
    try {
      await api.delete(`/api/admin/users/${id}`);
      await fetchUsers();
    } catch (err: any) {
      setError(err.response?.data?.detail || "Lỗi khi xoá.");
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-3xl font-bold uppercase tracking-tight text-heading mb-2">Đồng Nghiệp</h2>
        <p className="text-body">Đồng nghiệp của Jenny: cấp trên và đồng cấp được phép đăng nhập hệ thống</p>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg flex items-start gap-3">
          <AlertCircle size={20} className="shrink-0 mt-0.5" />
          <p>{error}</p>
        </div>
      )}

      <form onSubmit={handleAdd} className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5 items-end">
        <input
          type="text"
          placeholder="Họ tên"
          value={form.full_name}
          onChange={(e) => setForm({ ...form, full_name: e.target.value })}
          className={inputCls}
        />
        <input
          type="text"
          placeholder="Chức danh"
          value={form.job_title}
          onChange={(e) => setForm({ ...form, job_title: e.target.value })}
          className={inputCls}
        />
        <div className="relative">
          <Mail size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-light" />
          <input
            type="email"
            required
            placeholder="email@congty.com"
            value={form.email}
            onChange={(e) => setForm({ ...form, email: e.target.value })}
            className={clsx(inputCls, "pl-9")}
          />
        </div>
        <select
          value={form.relation_type}
          onChange={(e) => setForm({ ...form, relation_type: e.target.value as RelationType })}
          className={inputCls}
          aria-label="Mối quan hệ với Jenny"
        >
          <option value="peer">Đồng cấp</option>
          <option value="superior">Cấp trên</option>
        </select>
        <button type="submit" className="btn-pill py-2.5 justify-center">
          <Plus size={18} />
          Thêm
        </button>
      </form>
      <p className="text-xs text-muted-light -mt-3">
        Cấp trên: quyền admin, được chỉnh sửa và đánh giá. Đồng cấp: xem, chat, hỏi tư vấn, đánh giá — không được chỉnh sửa.
      </p>

      {loading ? (
        <div className="bg-background border border-border rounded-2xl h-40 animate-pulse" />
      ) : users.length === 0 ? (
        <div className="text-center py-16 bg-surface border border-border-soft border-dashed rounded-2xl">
          <Mail size={40} className="mx-auto text-muted mb-3" />
          <p className="text-body">Chưa có user nào được cấp quyền.</p>
        </div>
      ) : (
        <>
          {/* Bảng đầy đủ — chỉ hiện từ md trở lên. */}
          <div className="hidden md:block bg-background border border-border rounded-2xl overflow-hidden overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-muted-light text-xs uppercase tracking-wider">
                  <th className="px-4 py-3 font-semibold">Họ tên</th>
                  <th className="px-4 py-3 font-semibold">Chức danh</th>
                  <th className="px-4 py-3 font-semibold">Email</th>
                  <th className="px-4 py-3 font-semibold">Phân quyền</th>
                  <th className="px-4 py-3 font-semibold">Quan hệ với Jenny</th>
                  <th className="px-4 py-3 font-semibold">Phạm vi tương tác</th>
                  <th className="px-4 py-3 font-semibold">Trạng thái</th>
                  <th className="px-4 py-3 font-semibold text-right">Hành động</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {users.map(u => {
                  const editing = editId === u.id;
                  const meta = RELATION_META[editing ? draft.relation_type : u.relation_type];
                  return (
                    <tr key={u.id} className="align-top">
                      <td className="px-4 py-2.5 font-semibold text-label">
                        {editing ? (
                          <input className={inputCls} value={draft.full_name} onChange={(e) => setDraft({ ...draft, full_name: e.target.value })} />
                        ) : (u.full_name || <span className="text-muted-light font-normal">—</span>)}
                      </td>
                      <td className="px-4 py-2.5 text-body">
                        {editing ? (
                          <input className={inputCls} value={draft.job_title} onChange={(e) => setDraft({ ...draft, job_title: e.target.value })} />
                        ) : (u.job_title || <span className="text-muted-light">—</span>)}
                      </td>
                      <td className="px-4 py-2.5 text-body">{u.email}</td>
                      <td className="px-4 py-2.5">
                        <span className={clsx(
                          "px-2 py-0.5 rounded-full text-xs font-semibold",
                          meta.access === "Admin" ? "bg-primary-dark text-white" : "bg-surface-alt text-label"
                        )}>{meta.access}</span>
                      </td>
                      <td className="px-4 py-2.5 text-body">
                        {editing ? (
                          <select
                            className={inputCls}
                            value={draft.relation_type}
                            onChange={(e) => setDraft({ ...draft, relation_type: e.target.value as RelationType })}
                          >
                            <option value="peer">Đồng cấp</option>
                            <option value="superior">Cấp trên</option>
                          </select>
                        ) : meta.label}
                      </td>
                      <td className="px-4 py-2.5 text-body text-xs leading-[1.5] max-w-[260px]">{meta.scope}</td>
                      <td className="px-4 py-2.5">
                        <button
                          onClick={() => handleToggleActive(u)}
                          className={clsx(
                            "px-2 py-0.5 rounded-full text-xs font-semibold transition-colors",
                            u.is_active ? "bg-tint text-primary-dark" : "bg-surface-alt text-muted-light"
                          )}
                        >
                          {u.is_active ? "Active" : "Tắt"}
                        </button>
                      </td>
                      <td className="px-4 py-2.5 text-right whitespace-nowrap">
                        {editing ? (
                          <>
                            <button onClick={() => saveEdit(u.id)} className="p-1.5 rounded hover:bg-tint text-primary-dark" aria-label="Lưu"><Check size={16} /></button>
                            <button onClick={() => setEditId(null)} className="p-1.5 rounded hover:bg-surface text-body" aria-label="Huỷ"><X size={16} /></button>
                          </>
                        ) : (
                          <button onClick={() => startEdit(u)} className="p-1.5 rounded hover:bg-surface text-body" aria-label="Sửa"><Pencil size={16} /></button>
                        )}
                        <button onClick={() => handleDelete(u.id)} className="p-1.5 rounded hover:bg-red-50 text-down" aria-label="Xoá">
                          <Trash2 size={16} />
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Dạng card — chỉ hiện dưới md. */}
          <div className="md:hidden space-y-3">
            {users.map(u => {
              const meta = RELATION_META[u.relation_type];
              return (
                <div key={u.id} className="bg-background border border-border rounded-2xl p-4 space-y-2">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="font-semibold text-label truncate">{u.full_name || u.email}</p>
                      {u.job_title && <p className="text-xs text-body">{u.job_title}</p>}
                      <p className="text-xs text-muted-light truncate">{u.email}</p>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      <button
                        onClick={() => handleToggleActive(u)}
                        className={clsx(
                          "px-2 py-0.5 rounded-full text-xs font-semibold transition-colors",
                          u.is_active ? "bg-tint text-primary-dark" : "bg-surface-alt text-muted-light"
                        )}
                      >
                        {u.is_active ? "Active" : "Tắt"}
                      </button>
                      <button onClick={() => handleDelete(u.id)} className="p-1.5 rounded hover:bg-red-50 text-down" aria-label="Xoá">
                        <Trash2 size={16} />
                      </button>
                    </div>
                  </div>
                  <p className="text-xs text-body"><b>{meta.label}</b> · {meta.access} — {meta.scope}</p>
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
