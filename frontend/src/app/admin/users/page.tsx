"use client";

import { useEffect, useState } from "react";
import { Plus, Trash2, AlertCircle, Mail } from "lucide-react";
import { format } from "date-fns";
import clsx from "clsx";
import { api } from "@/lib/api";

interface AllowedUser {
  id: number;
  email: string;
  is_active: boolean;
  created_at: string;
}

export default function AdminUsersPage() {
  const [users, setUsers] = useState<AllowedUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [newEmail, setNewEmail] = useState("");

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
      await api.post("/api/admin/users", { email: newEmail });
      setNewEmail("");
      await fetchUsers();
    } catch (err: any) {
      setError(err.response?.data?.detail || "Lỗi khi thêm người dùng.");
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
        <h2 className="text-3xl font-bold tracking-tight text-heading mb-2">Người Dùng</h2>
        <p className="text-body">Danh sách Gmail được phép đăng nhập xem daily report</p>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg flex items-start gap-3">
          <AlertCircle size={20} className="shrink-0 mt-0.5" />
          <p>{error}</p>
        </div>
      )}

      <form onSubmit={handleAdd} className="flex gap-3">
        <div className="relative flex-1 max-w-sm">
          <Mail size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-light" />
          <input
            type="email"
            required
            placeholder="email@congty.com"
            value={newEmail}
            onChange={(e) => setNewEmail(e.target.value)}
            className="w-full pl-9 pr-3 py-2.5 border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary"
          />
        </div>
        <button type="submit" className="btn-pill py-2.5">
          <Plus size={18} />
          Thêm
        </button>
      </form>

      {loading ? (
        <div className="bg-background border border-border rounded-2xl h-40 animate-pulse" />
      ) : users.length === 0 ? (
        <div className="text-center py-16 bg-surface border border-border-soft border-dashed rounded-2xl">
          <Mail size={40} className="mx-auto text-muted mb-3" />
          <p className="text-body">Chưa có user nào được cấp quyền.</p>
        </div>
      ) : (
        <div className="bg-background border border-border rounded-2xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-muted-light text-xs uppercase tracking-wider">
                <th className="px-4 py-3 font-semibold">Email</th>
                <th className="px-4 py-3 font-semibold">Ngày thêm</th>
                <th className="px-4 py-3 font-semibold">Trạng thái</th>
                <th className="px-4 py-3 font-semibold text-right">Hành động</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {users.map(u => (
                <tr key={u.id}>
                  <td className="px-4 py-2.5 font-semibold text-label">{u.email}</td>
                  <td className="px-4 py-2.5 text-body">{format(new Date(u.created_at), "dd/MM/yyyy")}</td>
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
                  <td className="px-4 py-2.5 text-right">
                    <button onClick={() => handleDelete(u.id)} className="p-1.5 rounded hover:bg-red-50 text-down">
                      <Trash2 size={16} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
