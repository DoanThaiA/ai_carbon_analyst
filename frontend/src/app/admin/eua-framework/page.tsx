"use client";

import { useEffect, useMemo, useState } from "react";
import { BrainCircuit, ChevronDown, ChevronUp, RotateCcw, Save, AlertCircle, Search, Pencil } from "lucide-react";
import { format } from "date-fns";
import clsx from "clsx";
import { api } from "@/lib/api";
import type { EuaFrameworkBlock } from "@/lib/types";

export default function AdminEuaFrameworkPage() {
  const [blocks, setBlocks] = useState<EuaFrameworkBlock[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");

  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState<Set<string>>(new Set());
  const [resetting, setResetting] = useState<Set<string>>(new Set());

  const fetchBlocks = async () => {
    try {
      const res = await api.get("/api/admin/eua-framework/blocks");
      setBlocks(res.data);
    } catch {
      setError("Không thể tải khung phân tích EUA.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchBlocks();
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return blocks;
    return blocks.filter(
      (b) => b.title.toLowerCase().includes(q) || b.block_id.toLowerCase().includes(q)
    );
  }, [blocks, query]);

  function effectiveContent(b: EuaFrameworkBlock): string {
    return b.custom_content ?? b.default_content;
  }

  function toggleExpand(b: EuaFrameworkBlock) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(b.block_id)) {
        next.delete(b.block_id);
      } else {
        next.add(b.block_id);
        setDrafts((d) => (d[b.block_id] !== undefined ? d : { ...d, [b.block_id]: effectiveContent(b) }));
      }
      return next;
    });
  }

  async function handleSave(b: EuaFrameworkBlock) {
    const content = drafts[b.block_id];
    if (content === undefined || !content.trim() || saving.has(b.block_id)) return;
    setSaving((s) => new Set(s).add(b.block_id));
    setError("");
    try {
      const res = await api.put(`/api/admin/eua-framework/blocks/${b.block_id}`, { content });
      const updated: EuaFrameworkBlock = res.data;
      setBlocks((prev) => prev.map((x) => (x.block_id === updated.block_id ? updated : x)));
      setDrafts((d) => ({ ...d, [b.block_id]: updated.custom_content ?? updated.default_content }));
    } catch (err: any) {
      setError(err.response?.data?.detail || "Lỗi khi lưu nội dung.");
    } finally {
      setSaving((s) => {
        const next = new Set(s);
        next.delete(b.block_id);
        return next;
      });
    }
  }

  async function handleReset(b: EuaFrameworkBlock) {
    if (!b.is_customized) return;
    if (!confirm(`Khôi phục "${b.title}" về nội dung mặc định trong code? Nội dung custom hiện tại sẽ mất.`)) return;
    setResetting((s) => new Set(s).add(b.block_id));
    setError("");
    try {
      const res = await api.delete(`/api/admin/eua-framework/blocks/${b.block_id}`);
      const updated: EuaFrameworkBlock = res.data;
      setBlocks((prev) => prev.map((x) => (x.block_id === updated.block_id ? updated : x)));
      setDrafts((d) => ({ ...d, [b.block_id]: updated.default_content }));
    } catch (err: any) {
      setError(err.response?.data?.detail || "Lỗi khi khôi phục mặc định.");
    } finally {
      setResetting((s) => {
        const next = new Set(s);
        next.delete(b.block_id);
        return next;
      });
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-3xl font-bold tracking-tight text-heading mb-2">Khung Phân Tích EUA</h2>
        <p className="text-body">
          Custom nội dung diễn giải từng khối tri thức nhân quả dùng để sinh báo cáo & chat AI — không đổi
          được cơ chế nào áp dụng cho topic tin tức nào, chỉ đổi được NỘI DUNG diễn giải của từng khối.
        </p>
      </div>

      <div className="relative max-w-sm">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-light" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Tìm theo tên khối..."
          className="w-full pl-9 pr-3 py-2.5 border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary"
        />
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg flex items-start gap-3">
          <AlertCircle size={20} className="shrink-0 mt-0.5" />
          <p>{error}</p>
        </div>
      )}

      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="bg-surface border border-border rounded-2xl h-16 animate-pulse" />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-16 bg-surface border border-border-soft border-dashed rounded-2xl">
          <BrainCircuit size={40} className="mx-auto text-muted mb-3" />
          <p className="text-body">Không tìm thấy khối nào khớp.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {filtered.map((b) => {
            const isOpen = expanded.has(b.block_id);
            const draft = drafts[b.block_id] ?? effectiveContent(b);
            const isDirty = isOpen && draft !== effectiveContent(b);
            const isSaving = saving.has(b.block_id);
            const isResetting = resetting.has(b.block_id);

            return (
              <div key={b.block_id} className="bg-background border border-border rounded-2xl overflow-hidden">
                <button
                  onClick={() => toggleExpand(b)}
                  className="w-full flex items-center justify-between gap-3 px-5 py-4 text-left hover:bg-surface transition-colors"
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <div className={clsx("p-1.5 rounded-lg shrink-0", b.is_customized ? "bg-tint text-primary-dark" : "bg-surface-alt text-muted-light")}>
                      <Pencil size={14} />
                    </div>
                    <div className="min-w-0">
                      <p className="font-semibold text-label truncate">{b.title}</p>
                      <p className="text-[11px] text-muted-light font-mono truncate">{b.block_id}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-3 shrink-0">
                    {b.is_customized && (
                      <span className="px-2 py-0.5 rounded-full text-xs font-semibold bg-tint text-primary-dark border border-primary/20">
                        Đã custom
                      </span>
                    )}
                    {isOpen ? <ChevronUp size={18} className="text-muted-light" /> : <ChevronDown size={18} className="text-muted-light" />}
                  </div>
                </button>

                {isOpen && (
                  <div className="px-5 pb-5 space-y-3 border-t border-border-soft pt-4">
                    {b.is_customized && b.updated_at && (
                      <p className="text-[12px] text-muted-light">
                        Cập nhật lần cuối {format(new Date(b.updated_at), "HH:mm dd/MM/yyyy")}
                        {b.updated_by ? ` bởi ${b.updated_by}` : ""}
                      </p>
                    )}
                    <textarea
                      value={draft}
                      onChange={(e) => setDrafts((d) => ({ ...d, [b.block_id]: e.target.value }))}
                      rows={16}
                      className="w-full text-[13px] leading-relaxed font-mono px-3 py-2.5 rounded-lg border border-border-soft bg-surface focus:outline-none focus:border-primary resize-y"
                    />
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => handleSave(b)}
                        disabled={!isDirty || isSaving || !draft.trim()}
                        className="btn-pill py-1.5 px-3.5 text-sm disabled:opacity-40"
                      >
                        {isSaving ? (
                          <div className="w-4 h-4 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                        ) : (
                          <Save size={15} />
                        )}
                        Lưu thay đổi
                      </button>
                      {isDirty && (
                        <button
                          onClick={() => setDrafts((d) => ({ ...d, [b.block_id]: effectiveContent(b) }))}
                          className="text-sm font-semibold text-muted-light hover:text-body px-2 py-1.5"
                        >
                          Huỷ chỉnh sửa
                        </button>
                      )}
                      {b.is_customized && (
                        <button
                          onClick={() => handleReset(b)}
                          disabled={isResetting}
                          className="ml-auto flex items-center gap-1.5 text-sm font-semibold text-down hover:bg-red-50 px-2.5 py-1.5 rounded-lg transition-colors disabled:opacity-40"
                        >
                          {isResetting ? (
                            <div className="w-4 h-4 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                          ) : (
                            <RotateCcw size={14} />
                          )}
                          Khôi phục mặc định
                        </button>
                      )}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
