"use client";

import { useEffect, useState } from "react";
import { Plus, Pencil, Trash2, Save, X, AlertCircle } from "lucide-react";
import clsx from "clsx";
import { api } from "@/lib/api";

interface PriceSource {
  id: number;
  symbol: string;
  instrument_code: string;
  instrument_name: string;
  category: string;
  unit: string;
  exchange: string;
  is_active: boolean;
}

const EMPTY_FORM = {
  symbol: "",
  instrument_code: "",
  instrument_name: "",
  category: "",
  unit: "",
  exchange: "",
  is_active: true,
};

export default function PriceSourcesPage() {
  const [sources, setSources] = useState<PriceSource[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showAddForm, setShowAddForm] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState<Partial<PriceSource>>({});

  const fetchSources = async () => {
    try {
      const res = await api.get("/api/admin/price-sources");
      setSources(res.data);
    } catch (err) {
      setError("Không thể tải danh sách nguồn giá.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchSources();
  }, []);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    try {
      await api.post("/api/admin/price-sources", form);
      setForm(EMPTY_FORM);
      setShowAddForm(false);
      await fetchSources();
    } catch (err: any) {
      setError(err.response?.data?.detail || "Lỗi khi thêm nguồn giá.");
    }
  };

  const startEdit = (s: PriceSource) => {
    setEditingId(s.id);
    setEditForm(s);
  };

  const handleUpdate = async (id: number) => {
    setError("");
    try {
      const { symbol, instrument_name, category, unit, exchange, is_active } = editForm;
      await api.put(`/api/admin/price-sources/${id}`, { symbol, instrument_name, category, unit, exchange, is_active });
      setEditingId(null);
      await fetchSources();
    } catch (err: any) {
      setError(err.response?.data?.detail || "Lỗi khi cập nhật nguồn giá.");
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm("Xoá nguồn giá này? Crawler sẽ không lấy giá cho hợp đồng này nữa.")) return;
    try {
      await api.delete(`/api/admin/price-sources/${id}`);
      await fetchSources();
    } catch (err: any) {
      setError(err.response?.data?.detail || "Lỗi khi xoá nguồn giá.");
    }
  };

  const inputCls = "w-full px-2 py-1.5 border border-border rounded text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary";

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h2 className="text-3xl font-bold tracking-tight text-heading mb-2">Nguồn Giá</h2>
          <p className="text-body">Cấu hình các hợp đồng để crawl giá từ Barchart</p>
        </div>
        <button onClick={() => setShowAddForm(v => !v)} className="btn-pill py-2.5">
          <Plus size={18} />
          Thêm nguồn giá
        </button>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg flex items-start gap-3">
          <AlertCircle size={20} className="shrink-0 mt-0.5" />
          <p>{error}</p>
        </div>
      )}

      {showAddForm && (
        <form onSubmit={handleCreate} className="bg-background border border-border rounded-2xl p-5 grid grid-cols-2 md:grid-cols-3 gap-3">
          <input required placeholder="Symbol (vd: NG*0)" value={form.symbol} onChange={e => setForm({ ...form, symbol: e.target.value })} className={inputCls} />
          <input required placeholder="Mã nội bộ (vd: NG)" value={form.instrument_code} onChange={e => setForm({ ...form, instrument_code: e.target.value })} className={inputCls} />
          <input required placeholder="Tên hiển thị" value={form.instrument_name} onChange={e => setForm({ ...form, instrument_name: e.target.value })} className={inputCls} />
          <input required placeholder="Category (vd: gas)" value={form.category} onChange={e => setForm({ ...form, category: e.target.value })} className={inputCls} />
          <input required placeholder="Đơn vị (vd: USD/MMBtu)" value={form.unit} onChange={e => setForm({ ...form, unit: e.target.value })} className={inputCls} />
          <input required placeholder="Sàn (vd: NYMEX)" value={form.exchange} onChange={e => setForm({ ...form, exchange: e.target.value })} className={inputCls} />
          <div className="col-span-2 md:col-span-3 flex items-center gap-3">
            <button type="submit" className="btn-pill py-2 px-5">Lưu</button>
            <button type="button" onClick={() => setShowAddForm(false)} className="text-body hover:text-primary text-sm font-semibold">Huỷ</button>
          </div>
        </form>
      )}

      {loading ? (
        <div className="bg-background border border-border rounded-2xl h-40 animate-pulse" />
      ) : (
        <div className="bg-background border border-border rounded-2xl overflow-hidden overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-muted-light text-xs uppercase tracking-wider">
                <th className="px-4 py-3 font-semibold">Symbol</th>
                <th className="px-4 py-3 font-semibold">Mã</th>
                <th className="px-4 py-3 font-semibold">Tên</th>
                <th className="px-4 py-3 font-semibold">Category</th>
                <th className="px-4 py-3 font-semibold">Đơn vị</th>
                <th className="px-4 py-3 font-semibold">Sàn</th>
                <th className="px-4 py-3 font-semibold">Trạng thái</th>
                <th className="px-4 py-3 font-semibold text-right">Hành động</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {sources.map(s => {
                const isEditing = editingId === s.id;
                return (
                  <tr key={s.id}>
                    <td className="px-4 py-2.5 font-mono">
                      {isEditing ? <input value={editForm.symbol} onChange={e => setEditForm({ ...editForm, symbol: e.target.value })} className={inputCls} /> : s.symbol}
                    </td>
                    <td className="px-4 py-2.5 font-mono text-muted-light">{s.instrument_code}</td>
                    <td className="px-4 py-2.5 font-semibold text-label">
                      {isEditing ? <input value={editForm.instrument_name} onChange={e => setEditForm({ ...editForm, instrument_name: e.target.value })} className={inputCls} /> : s.instrument_name}
                    </td>
                    <td className="px-4 py-2.5">
                      {isEditing ? <input value={editForm.category} onChange={e => setEditForm({ ...editForm, category: e.target.value })} className={inputCls} /> : s.category}
                    </td>
                    <td className="px-4 py-2.5">
                      {isEditing ? <input value={editForm.unit} onChange={e => setEditForm({ ...editForm, unit: e.target.value })} className={inputCls} /> : s.unit}
                    </td>
                    <td className="px-4 py-2.5">
                      {isEditing ? <input value={editForm.exchange} onChange={e => setEditForm({ ...editForm, exchange: e.target.value })} className={inputCls} /> : s.exchange}
                    </td>
                    <td className="px-4 py-2.5">
                      {isEditing ? (
                        <label className="flex items-center gap-1.5 text-xs">
                          <input type="checkbox" checked={!!editForm.is_active} onChange={e => setEditForm({ ...editForm, is_active: e.target.checked })} />
                          Active
                        </label>
                      ) : (
                        <span className={clsx(
                          "px-2 py-0.5 rounded-full text-xs font-semibold",
                          s.is_active ? "bg-tint text-primary-dark" : "bg-surface-alt text-muted-light"
                        )}>
                          {s.is_active ? "Active" : "Tắt"}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-2.5">
                      <div className="flex items-center justify-end gap-2">
                        {isEditing ? (
                          <>
                            <button onClick={() => handleUpdate(s.id)} className="p-1.5 rounded hover:bg-tint text-primary-dark"><Save size={16} /></button>
                            <button onClick={() => setEditingId(null)} className="p-1.5 rounded hover:bg-surface text-body"><X size={16} /></button>
                          </>
                        ) : (
                          <>
                            <button onClick={() => startEdit(s)} className="p-1.5 rounded hover:bg-surface text-body"><Pencil size={16} /></button>
                            <button onClick={() => handleDelete(s.id)} className="p-1.5 rounded hover:bg-red-50 text-down"><Trash2 size={16} /></button>
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
      )}
    </div>
  );
}
