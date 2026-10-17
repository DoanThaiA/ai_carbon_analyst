"use client";

import { useState } from "react";
import { Plus, Trash2, ChevronDown, ChevronUp, Code2 } from "lucide-react";
import clsx from "clsx";

type Json = any;

// ---------------------------------------------------------------------------
// Editor có cấu trúc cho report.content — thay thế cho ô textarea raw JSON.
// Mỗi mục (1..9, biz) map đúng theo shape mà ReportDocument.tsx render, để
// người duyệt sửa trực tiếp trên form thay vì phải hiểu cấu trúc JSON.
// Vẫn giữ 1 lối thoát "Raw JSON" cho các trường hiếm khi cần sửa tay (vd.
// chart_data) hoặc khi cấu trúc thực tế lệch khỏi các mục đã hỗ trợ ở đây.
// ---------------------------------------------------------------------------

function FieldLabel({ children }: { children: React.ReactNode }) {
  return (
    <label className="block font-mono text-[10px] font-bold uppercase tracking-wider text-muted-light mb-1.5">
      {children}
    </label>
  );
}

function TextInput({
  value,
  onChange,
  mono,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  mono?: boolean;
  placeholder?: string;
}) {
  return (
    <input
      type="text"
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      className={clsx(
        "w-full text-[13.5px] p-2.5 bg-surface border border-border rounded-lg outline-none focus:border-primary focus:ring-1 focus:ring-primary/20",
        mono && "font-mono"
      )}
    />
  );
}

function TextArea({
  value,
  onChange,
  rows = 3,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  rows?: number;
  placeholder?: string;
}) {
  return (
    <textarea
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value)}
      rows={rows}
      placeholder={placeholder}
      className="w-full text-[13.5px] p-2.5 bg-surface border border-border rounded-lg outline-none focus:border-primary focus:ring-1 focus:ring-primary/20 resize-y leading-relaxed"
    />
  );
}

function Select({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (v: string) => void;
  options: string[];
}) {
  return (
    <select
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value)}
      className="w-full text-[13px] p-2.5 bg-surface border border-border rounded-lg outline-none focus:border-primary focus:ring-1 focus:ring-primary/20"
    >
      <option value="" disabled>
        — chọn —
      </option>
      {options.map((o) => (
        <option key={o} value={o}>
          {o}
        </option>
      ))}
    </select>
  );
}

function AddButton({ onClick, label }: { onClick: () => void; label: string }) {
  return (
    <button
      onClick={onClick}
      type="button"
      className="flex items-center gap-1.5 text-primary hover:text-primary-dark text-xs font-semibold border border-dashed border-primary/40 hover:border-primary rounded-md px-3 py-2 w-full justify-center transition-colors"
    >
      <Plus size={14} />
      {label}
    </button>
  );
}

function SectionShell({
  number,
  title,
  children,
}: {
  number: string;
  title: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(true);
  return (
    <div className="border border-border rounded-xl bg-background overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between px-4 py-3 bg-surface hover:bg-surface-alt transition-colors"
      >
        <div className="flex items-center gap-3">
          <span className="font-mono text-[11px] font-bold text-white bg-primary rounded-[4px] px-2 py-1 leading-none">
            {number}
          </span>
          <span className="font-bold text-heading text-[15px]">{title}</span>
        </div>
        {open ? (
          <ChevronUp size={16} className="text-muted-light shrink-0" />
        ) : (
          <ChevronDown size={16} className="text-muted-light shrink-0" />
        )}
      </button>
      {open && <div className="p-4 space-y-4">{children}</div>}
    </div>
  );
}

// ---- array helpers ---------------------------------------------------------

function useArrayOps<T extends Record<string, any>>(list: T[] | undefined, onChange: (next: T[]) => void) {
  const items = list || [];
  const update = (i: number, patch: Partial<T>) => {
    const next = items.slice();
    next[i] = { ...next[i], ...patch };
    onChange(next);
  };
  const remove = (i: number) => onChange(items.filter((_, idx) => idx !== i));
  const add = (item: T) => onChange([...items, item]);
  return { items, update, remove, add };
}

// Bullet của Mục 1/4 là object { text, source_name, source_url } (nguồn tin do
// backend tự map từ [N], xem services/report_generator.py::_resolve_bullet_sources)
// thay vì string thô như các mục khác — BulletsEditor xử lý được cả 2 dạng,
// chỉ cho sửa "text", giữ nguyên source_name/source_url khi lưu lại.
type BulletItem = string | { text: string; source_name?: string | null; source_url?: string | null; [k: string]: unknown };

function useBulletListOps(list: BulletItem[] | undefined, onChange: (next: BulletItem[]) => void) {
  const items = list || [];
  const update = (i: number, value: string) => {
    const next = items.slice();
    const cur = next[i];
    next[i] = typeof cur === "object" && cur !== null ? { ...cur, text: value } : value;
    onChange(next);
  };
  // Sửa nguồn: bullet dạng chuỗi thô được chuyển sang object { text, source_name, source_url }.
  const updateSource = (i: number, patch: { source_name?: string | null; source_url?: string | null }) => {
    const next = items.slice();
    const cur = next[i];
    next[i] = typeof cur === "object" && cur !== null ? { ...cur, ...patch } : { text: cur, ...patch };
    onChange(next);
  };
  const remove = (i: number) => onChange(items.filter((_, idx) => idx !== i));
  const add = () => onChange([...items, ""]);
  return { items, update, updateSource, remove, add };
}

function BulletsEditor({
  bullets,
  onChange,
  placeholder,
}: {
  bullets: BulletItem[] | undefined;
  onChange: (b: BulletItem[]) => void;
  placeholder?: string;
}) {
  const { items, update, updateSource, remove, add } = useBulletListOps(bullets, onChange);
  return (
    <div className="space-y-2">
      {items.map((b, i) => {
        const text = typeof b === "string" ? b : b.text || "";
        const sourceName = typeof b === "object" && b !== null ? b.source_name : null;
        const sourceUrl = typeof b === "object" && b !== null ? b.source_url : null;
        return (
          <div key={i} className="flex gap-2 items-start group">
            <span className="font-mono text-[10px] text-muted-light pt-3 w-4 shrink-0">{i + 1}</span>
            <div className="flex-1">
              <textarea
                value={text}
                onChange={(e) => update(i, e.target.value)}
                rows={2}
                placeholder={placeholder}
                className="w-full text-[13.5px] p-2.5 bg-surface border border-border rounded-lg outline-none focus:border-primary focus:ring-1 focus:ring-primary/20 resize-y"
              />
              <div className="mt-1 grid grid-cols-1 sm:grid-cols-2 gap-1.5">
                <input
                  value={sourceName ?? ""}
                  onChange={(e) => updateSource(i, { source_name: e.target.value || null })}
                  placeholder="Tên nguồn"
                  className="w-full text-[12px] p-1.5 bg-surface border border-border rounded outline-none focus:border-primary"
                />
                <input
                  value={sourceUrl ?? ""}
                  onChange={(e) => updateSource(i, { source_url: e.target.value || null })}
                  placeholder="URL nguồn"
                  className="w-full font-mono text-[12px] p-1.5 bg-surface border border-border rounded outline-none focus:border-primary"
                />
              </div>
            </div>
            <button
              type="button"
              onClick={() => remove(i)}
              title="Xóa dòng"
              className="text-muted-light hover:text-down p-1.5 mt-1 opacity-60 group-hover:opacity-100 transition-opacity shrink-0"
            >
              <Trash2 size={15} />
            </button>
          </div>
        );
      })}
      <AddButton onClick={add} label="Thêm dòng" />
    </div>
  );
}

type ColumnDef = {
  key: string;
  label: string;
  // "number": lưu số (rỗng → null) — giá đóng cửa, OHLC, %... phải giữ kiểu số vì frontend/Jack QC tính toán trên đó.
  type?: "text" | "textarea" | "select" | "number" | "checkbox";
  options?: string[];
  mono?: boolean;
};

function emptyRow(columns: ColumnDef[]) {
  return Object.fromEntries(columns.map((c) => [c.key, c.type === "number" ? null : c.type === "checkbox" ? false : ""]));
}

const CELL_CLASS = "w-full text-[12.5px] p-1.5 bg-surface border border-border rounded outline-none focus:border-primary";

// 1 ô nhập theo kiểu cột — dùng chung cho bảng (TableRowsEditor), thẻ (CardRowsEditor) và object (ObjectFieldsEditor).
function CellInput({ column, value, onChange }: { column: ColumnDef; value: Json; onChange: (v: Json) => void }) {
  if (column.type === "select") {
    return (
      <select value={value ?? ""} onChange={(e) => onChange(e.target.value)} className={CELL_CLASS}>
        <option value="">—</option>
        {column.options!.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
        {value && !column.options!.includes(value) && <option value={value}>{value}</option>}
      </select>
    );
  }
  if (column.type === "textarea") {
    return <textarea value={value ?? ""} onChange={(e) => onChange(e.target.value)} rows={2} className={clsx(CELL_CLASS, "resize-y")} />;
  }
  if (column.type === "number") {
    return (
      <input
        type="number"
        step="any"
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
        className={clsx(CELL_CLASS, "font-mono tabular-nums")}
      />
    );
  }
  if (column.type === "checkbox") {
    return <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} className="w-4 h-4 accent-primary" />;
  }
  return <input value={value ?? ""} onChange={(e) => onChange(e.target.value)} className={clsx(CELL_CLASS, column.mono && "font-mono")} />;
}

// Ô JSON cho 1 giá trị bất kỳ (mảng/object lồng nhau, hoặc cả 1 mục) — áp dụng khi rời ô, báo lỗi nếu JSON sai.
function JsonField({ value, onChange, rows = 8 }: { value: Json; onChange: (v: Json) => void; rows?: number }) {
  const serialized = JSON.stringify(value ?? null, null, 2);
  const [text, setText] = useState(serialized);
  const [error, setError] = useState("");
  const [synced, setSynced] = useState(serialized);
  if (serialized !== synced) {
    // Giá trị đổi từ form khác → đồng bộ lại ô (mẫu "điều chỉnh state khi prop đổi" của React).
    setSynced(serialized);
    setText(serialized);
    setError("");
  }
  return (
    <div className="space-y-1">
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={() => {
          try {
            const parsed = JSON.parse(text);
            setError("");
            if (JSON.stringify(parsed, null, 2) !== serialized) onChange(parsed);
          } catch {
            setError("JSON không hợp lệ — chưa áp dụng. Kiểm tra dấu ngoặc, dấu phẩy...");
          }
        }}
        rows={rows}
        className="w-full font-mono text-[12px] p-2.5 bg-surface border border-border rounded-lg outline-none focus:border-primary resize-y"
      />
      {error && <p className="text-xs text-down font-medium">{error}</p>}
    </div>
  );
}

// Sửa 1 object phẳng (thẻ CBAM, quy tắc chấm, lệnh mô phỏng...): mỗi khoá 1 ô theo kiểu giá trị hiện có
// (số → ô số, true/false → ô tick, chuỗi → ô chữ; `selects` cho enum), mảng/object lồng → ô JSON.
function ObjectFieldsEditor({
  value,
  onChange,
  labels = {},
  selects = {},
}: {
  value: Json;
  onChange: (v: Json) => void;
  labels?: Record<string, string>;
  selects?: Record<string, string[]>;
}) {
  const obj = value || {};
  const keys = Array.from(new Set([...Object.keys(labels), ...Object.keys(obj)]));
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-3 gap-y-2">
      {keys.map((k) => {
        const v = obj[k];
        const label = labels[k] ? `${labels[k]} (${k})` : k;
        if (v !== null && typeof v === "object") {
          return (
            <div key={k} className="sm:col-span-2">
              <FieldLabel>{label}</FieldLabel>
              <JsonField value={v} onChange={(nv) => onChange({ ...obj, [k]: nv })} rows={4} />
            </div>
          );
        }
        const type: ColumnDef["type"] = selects[k] ? "select" : typeof v === "number" ? "number" : typeof v === "boolean" ? "checkbox" : "text";
        return (
          <div key={k}>
            <FieldLabel>{label}</FieldLabel>
            <CellInput column={{ key: k, label, type, options: selects[k] }} value={v} onChange={(nv) => onChange({ ...obj, [k]: nv })} />
          </div>
        );
      })}
    </div>
  );
}

// Lối thoát cuối mỗi mục: sửa JSON của RIÊNG mục đó (trường form chưa hỗ trợ, cấu trúc lạ).
function SectionJson({ value, onChange }: { value: Json; onChange: (v: Json) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border-t border-dashed border-border pt-3">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 text-xs font-medium text-muted-light hover:text-foreground"
      >
        <Code2 size={13} />
        {open ? "Ẩn JSON của mục này" : "Sửa JSON của mục này (mọi trường, kể cả trường không có trong form)"}
      </button>
      {open && (
        <div className="mt-2">
          <JsonField value={value} onChange={onChange} rows={16} />
        </div>
      )}
    </div>
  );
}

// Khối có thể chưa tồn tại (báo cáo cũ) → nút tạo với giá trị khởi tạo.
function OptionalBlock({ present, onCreate, label, children }: { present: boolean; onCreate: () => void; label: string; children: React.ReactNode }) {
  return present ? <>{children}</> : <AddButton onClick={onCreate} label={label} />;
}

// Dùng cho danh sách gọn (bảng giá, gợi ý kinh doanh) — mỗi hàng là 1 dòng bảng.
function TableRowsEditor({
  columns,
  rows,
  onChange,
  addLabel,
}: {
  columns: ColumnDef[];
  rows: Json[] | undefined;
  onChange: (rows: Json[]) => void;
  addLabel: string;
}) {
  const { items, update, remove, add } = useArrayOps(rows, onChange);
  return (
    <div className="space-y-2.5">
      {items.length > 0 && (
        <div className="overflow-x-auto border border-border rounded-lg">
          <table className="w-full border-collapse text-[13px]">
            <thead>
              <tr>
                {columns.map((c) => (
                  <th
                    key={c.key}
                    className="text-left font-mono text-[10px] uppercase tracking-wider text-primary-dark px-2.5 py-2 border-b-2 border-primary/30 border-r border-border bg-tint"
                  >
                    {c.label}
                  </th>
                ))}
                <th className="bg-tint border-b-2 border-primary/30 w-9" />
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {items.map((row, i) => (
                <tr key={i} className="even:bg-surface/60 align-top">
                  {columns.map((c, ci) => (
                    <td
                      key={c.key}
                      className={clsx("p-1.5", ci < columns.length - 1 && "border-r border-border")}
                    >
                      <CellInput column={c} value={row[c.key]} onChange={(v) => update(i, { [c.key]: v })} />
                    </td>
                  ))}
                  <td className="p-1.5 text-center">
                    <button type="button" onClick={() => remove(i)} title="Xóa hàng" className="text-muted-light hover:text-down p-1">
                      <Trash2 size={14} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <AddButton onClick={() => add(emptyRow(columns))} label={addLabel} />
    </div>
  );
}

// Dùng cho danh sách có trường dài (tin tức, quan điểm, sự kiện...) — mỗi item là 1 card.
function CardRowsEditor({
  columns,
  rows,
  onChange,
  addLabel,
}: {
  columns: ColumnDef[];
  rows: Json[] | undefined;
  onChange: (rows: Json[]) => void;
  addLabel: string;
}) {
  const { items, update, remove, add } = useArrayOps(rows, onChange);
  return (
    <div className="space-y-3">
      {items.map((row, i) => (
        <div key={i} className="relative border border-border rounded-lg p-3.5 bg-surface/60">
          <div className="flex justify-between items-center mb-2.5">
            <span className="font-mono text-[10px] text-muted-light">#{i + 1}</span>
            <button
              type="button"
              onClick={() => remove(i)}
              className="flex items-center gap-1 text-muted-light hover:text-down text-xs font-medium"
            >
              <Trash2 size={13} />
              Xóa
            </button>
          </div>
          <div className="space-y-2.5">
            {columns.map((c) => (
              <div key={c.key}>
                <FieldLabel>{c.label}</FieldLabel>
                <CellInput column={c} value={row[c.key]} onChange={(v) => update(i, { [c.key]: v })} />
              </div>
            ))}
          </div>
        </div>
      ))}
      <AddButton onClick={() => add(emptyRow(columns))} label={addLabel} />
    </div>
  );
}

const CORRELATION_FIELDS: { key: string; label: string }[] = [
  { key: "gas_comment", label: "Nhận định Gas" },
  { key: "coal_comment", label: "Nhận định Than" },
  { key: "power_comment", label: "Nhận định Điện Đức" },
  { key: "fuel_switching_chain", label: "Chuỗi fuel-switching" },
  { key: "eua_conclusion", label: "Kết luận EUA" },
];

const SOURCE_COLUMNS: ColumnDef[] = [
  { key: "source_name", label: "Tên nguồn" },
  { key: "source_url", label: "URL nguồn", mono: true },
];
const KEY_DEV_COLUMNS: ColumnDef[] = [
  { key: "title", label: "Tiêu đề" },
  { key: "text", label: "Nội dung", type: "textarea" },
  { key: "impact", label: "Tác động lên EUA", type: "select", options: ["tăng", "giảm", "trung lập"] },
  ...SOURCE_COLUMNS,
  { key: "source_date", label: "Ngày đăng (DD/MM/YYYY)" },
];
const VERDICTS = ["correct", "wrong", "unclear", "on_track", "diverging", "direction_only"];
const TRADE_SELECTS = {
  side: ["buy", "sell"],
  order_type: ["limit", "stop"],
  status: ["not_filled", "open", "stopped", "target_hit", "expired", "cancelled_target"],
};
const EMPTY_TRADE = {
  side: "buy", order_type: "limit", entry_low: null, entry_high: null, stop: null, target: null,
  status: "not_filled", filled_date: null, fill_price: null, exit_date: null, exit_price: null,
};

// Bảng "Dự báo trước: đúng hay sai" (content["2"].forecast_review — Python tính lúc sinh báo cáo, xem
// services/forecast_review.py). Cho sửa tay mọi trường; báo cáo sau vẫn chấm lại từ dữ liệu gốc.
function ForecastReviewEditor({ review, onChange }: { review: Json; onChange: (v: Json) => void }) {
  const items: Json[] = review.items || [];
  const setItem = (i: number, patch: Json) => onChange({ ...review, items: items.map((it, idx) => (idx === i ? { ...it, ...patch } : it)) });
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3 max-w-md">
        <div>
          <FieldLabel>Số báo cáo sai liên tiếp</FieldLabel>
          <CellInput column={{ key: "wrong_in_row", label: "", type: "number" }} value={review.wrong_in_row} onChange={(v) => onChange({ ...review, wrong_in_row: v })} />
        </div>
      </div>
      {items.map((it, i) => (
        <div key={i} className="border border-border rounded-lg p-3.5 bg-surface/60 space-y-2.5">
          <div className="flex justify-between items-center">
            <span className="font-mono text-[10px] text-muted-light">Dự báo #{i + 1}</span>
            <button
              type="button"
              onClick={() => onChange({ ...review, items: items.filter((_, idx) => idx !== i) })}
              className="flex items-center gap-1 text-muted-light hover:text-down text-xs font-medium"
            >
              <Trash2 size={13} />
              Xóa
            </button>
          </div>
          <ObjectFieldsEditor
            value={Object.fromEntries(Object.entries(it).filter(([k]) => k !== "trade"))}
            onChange={(v) => setItem(i, v)}
            labels={{
              report_date: "Ngày báo cáo", direction: "Chiều dự báo", probability: "Xác suất", verdict: "Kết luận",
              ref_close: "Giá tham chiếu", last_date: "Phiên mới nhất", last_close: "Giá mới nhất", change_pct: "%Δ lũy kế",
              sessions: "Số phiên", high_confidence_miss: "Sai dù xác suất Cao",
            }}
            selects={{ direction: ["tăng", "giảm", "đi ngang"], probability: ["Cao", "Trung bình", "Thấp"], verdict: VERDICTS }}
          />
          <div className="border-l-2 border-primary/30 pl-3">
            <FieldLabel>Lệnh mô phỏng</FieldLabel>
            {it.trade ? (
              <div className="space-y-2">
                <ObjectFieldsEditor value={it.trade} onChange={(v) => setItem(i, { trade: v })} selects={TRADE_SELECTS} />
                <button type="button" onClick={() => setItem(i, { trade: null })} className="text-xs text-muted-light hover:text-down">
                  Bỏ lệnh
                </button>
              </div>
            ) : (
              <AddButton onClick={() => setItem(i, { trade: { ...EMPTY_TRADE } })} label="Thêm lệnh" />
            )}
          </div>
        </div>
      ))}
      <AddButton
        onClick={() => onChange({ ...review, items: [...items, { report_date: "", direction: "tăng", probability: "Trung bình",
          ref_close: null, last_date: "", last_close: null, change_pct: null, sessions: null, verdict: "unclear",
          high_confidence_miss: false, trade: null }] })}
        label="Thêm dự báo"
      />
      <div>
        <FieldLabel>Tỷ lệ đúng theo mức xác suất</FieldLabel>
        <TableRowsEditor
          columns={[
            { key: "probability", label: "Xác suất", type: "select", options: ["Cao", "Trung bình", "Thấp"] },
            { key: "correct", label: "Đúng", type: "number" },
            { key: "total", label: "Tổng", type: "number" },
            { key: "direction_only", label: "Đúng hướng, không khớp", type: "number" },
          ]}
          rows={review.calibration}
          onChange={(rows) => onChange({ ...review, calibration: rows })}
          addLabel="Thêm mức xác suất"
        />
      </div>
      {review.rules && (
        <div>
          <FieldLabel>Quy tắc chấm (hiển thị dưới bảng)</FieldLabel>
          <ObjectFieldsEditor value={review.rules} onChange={(v) => onChange({ ...review, rules: v })} />
        </div>
      )}
    </div>
  );
}

export function ReportEditor({ content, onChange }: { content: Json; onChange: (next: Json) => void }) {
  const [rawMode, setRawMode] = useState(false);
  const [rawText, setRawText] = useState(() => JSON.stringify(content, null, 2));
  const [rawError, setRawError] = useState("");

  const patchSection = (key: string, patch: Json) =>
    onChange({ ...content, [key]: { ...(content[key] || {}), ...patch } });
  // Thay nguyên 1 mục (ô "Sửa JSON của mục này").
  const setSection = (key: string, value: Json) => onChange({ ...content, [key]: value });

  const toggleRaw = () => {
    if (!rawMode) setRawText(JSON.stringify(content, null, 2));
    setRawError("");
    setRawMode((r) => !r);
  };

  const applyRaw = () => {
    try {
      const parsed = JSON.parse(rawText);
      onChange(parsed);
      setRawError("");
    } catch {
      setRawError("JSON không hợp lệ — thay đổi chưa được áp dụng. Vui lòng kiểm tra lại dấu ngoặc, dấu phẩy...");
    }
  };

  const sec2 = content["2"] || {};
  const sec3 = content["3"];
  // Báo cáo Chủ Nhật/Thứ Hai (không có phiên giao dịch — services/report_generator.py::
  // is_no_price_report): không có bảng giá/động lực/kịch bản giao dịch để sửa.
  const noPriceData = !!sec2.no_price_data;

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <button
          type="button"
          onClick={toggleRaw}
          className="flex items-center gap-1.5 text-xs font-medium text-muted-light hover:text-foreground transition-colors"
        >
          <Code2 size={14} />
          {rawMode ? "Quay lại chỉnh sửa theo mục" : "Chỉnh raw JSON (nâng cao)"}
        </button>
      </div>

      {rawMode ? (
        <div className="space-y-2">
          <div className="bg-yellow-50 text-yellow-800 text-xs px-3 py-2 rounded border border-yellow-200">
            <strong>Lưu ý:</strong> chỉ dùng chế độ này cho các trường không có trong form (vd. dữ liệu nến), hoặc khi
            cần sửa cấu trúc mà form chưa hỗ trợ. Thay đổi được áp dụng khi rời khỏi ô này.
          </div>
          {rawError && <p className="text-xs text-down font-medium">{rawError}</p>}
          <textarea
            value={rawText}
            onChange={(e) => setRawText(e.target.value)}
            onBlur={applyRaw}
            className="w-full h-[600px] font-mono text-sm p-4 bg-surface border border-border rounded-lg outline-none focus:border-primary focus:ring-1 focus:ring-primary/20 resize-y"
          />
        </div>
      ) : (
        <>
          <SectionShell number="01" title="Tóm tắt điều hành">
            <div>
              <FieldLabel>Tiêu đề mục</FieldLabel>
              <TextInput value={content["1"]?.title} onChange={(v) => patchSection("1", { title: v })} />
            </div>
            <FieldLabel>Các gạch đầu dòng (dạng &quot;Tag: nội dung&quot;)</FieldLabel>
            <BulletsEditor
              bullets={content["1"]?.bullets}
              onChange={(b) => patchSection("1", { bullets: b })}
              placeholder="Vd: EUA: giá đóng cửa tăng 1.2% do..."
            />
            <SectionJson value={content["1"]} onChange={(v) => setSection("1", v)} />
          </SectionShell>

          {noPriceData ? (
          <SectionShell number="02" title="Bảng giá nhanh">
            <div>
              <FieldLabel>Thông báo (ngày không có phiên giao dịch — báo cáo không có phần giá)</FieldLabel>
              <TextArea value={sec2.no_price_notice} onChange={(v) => patchSection("2", { no_price_notice: v })} rows={2} />
            </div>
            <div>
              <FieldLabel>Diễn biến chính (tin tức)</FieldLabel>
              <CardRowsEditor
                columns={KEY_DEV_COLUMNS}
                rows={sec2.key_developments}
                onChange={(rows) => patchSection("2", { key_developments: rows })}
                addLabel="Thêm diễn biến"
              />
            </div>
            <SectionJson value={content["2"]} onChange={(v) => setSection("2", v)} />
          </SectionShell>
          ) : (
          <SectionShell number="02" title="Bảng giá nhanh">
            <div>
              <FieldLabel>Tiêu đề mục</FieldLabel>
              <TextInput value={sec2.title} onChange={(v) => patchSection("2", { title: v })} />
            </div>
            <div>
              <FieldLabel>Thời điểm chốt giá</FieldLabel>
              <TextInput mono value={sec2.price_timestamp} onChange={(v) => patchSection("2", { price_timestamp: v })} />
            </div>
            <div>
              <FieldLabel>Bảng giá</FieldLabel>
              <TableRowsEditor
                columns={[
                  { key: "code", label: "Mã", mono: true },
                  { key: "name", label: "Hợp đồng" },
                  { key: "price", label: "Giá (hiển thị)", mono: true },
                  { key: "close", label: "Giá đóng cửa (số)", type: "number" },
                  { key: "dday", label: "Δ Ngày", mono: true },
                  { key: "dweek", label: "Δ Tuần", mono: true },
                  { key: "day_change_pct", label: "Δ ngày % (số)", type: "number" },
                  { key: "week_change_pct", label: "Δ tuần % (số)", type: "number" },
                  { key: "note", label: "Ghi chú" },
                ]}
                rows={sec2.prices}
                onChange={(rows) => patchSection("2", { prices: rows })}
                addLabel="Thêm hợp đồng"
              />
            </div>
            <div>
              <FieldLabel>Số liệu chính</FieldLabel>
              <TextArea value={sec2.key_facts} onChange={(v) => patchSection("2", { key_facts: v })} rows={2} />
            </div>
            <div className="grid sm:grid-cols-2 gap-4">
              <div>
                <FieldLabel>Động lực tăng (bullish)</FieldLabel>
                <CardRowsEditor
                  columns={[
                    { key: "tag", label: "Tag", type: "select", options: ["FACT", "OPINION", "CLAIM", "ANALYSIS"] },
                    { key: "text", label: "Nội dung", type: "textarea" },
                    { key: "source_name", label: "Tên nguồn" },
                    { key: "source_url", label: "URL nguồn" },
                  ]}
                  rows={sec2.market_drivers?.bullish}
                  onChange={(rows) =>
                    patchSection("2", { market_drivers: { ...(sec2.market_drivers || {}), bullish: rows } })
                  }
                  addLabel="Thêm động lực tăng"
                />
              </div>
              <div>
                <FieldLabel>Động lực giảm (bearish)</FieldLabel>
                <CardRowsEditor
                  columns={[
                    { key: "tag", label: "Tag", type: "select", options: ["FACT", "OPINION", "CLAIM", "ANALYSIS"] },
                    { key: "text", label: "Nội dung", type: "textarea" },
                    { key: "source_name", label: "Tên nguồn" },
                    { key: "source_url", label: "URL nguồn" },
                  ]}
                  rows={sec2.market_drivers?.bearish}
                  onChange={(rows) =>
                    patchSection("2", { market_drivers: { ...(sec2.market_drivers || {}), bearish: rows } })
                  }
                  addLabel="Thêm động lực giảm"
                />
              </div>
            </div>
            <div>
              <FieldLabel>Diễn biến chính (tin tức dưới Bảng giá nhanh)</FieldLabel>
              <CardRowsEditor
                columns={KEY_DEV_COLUMNS}
                rows={sec2.key_developments}
                onChange={(rows) => patchSection("2", { key_developments: rows })}
                addLabel="Thêm diễn biến"
              />
            </div>
            <div>
              <FieldLabel>Dự báo trước: đúng hay sai</FieldLabel>
              <OptionalBlock
                present={!!sec2.forecast_review}
                onCreate={() => patchSection("2", { forecast_review: { items: [], wrong_in_row: 0, calibration: [] } })}
                label="Tạo bảng dự báo trước"
              >
                <ForecastReviewEditor review={sec2.forecast_review} onChange={(v) => patchSection("2", { forecast_review: v })} />
              </OptionalBlock>
            </div>
            {sec2.cbam_estimate && (
              <div>
                <FieldLabel>Ước tính giá CBAM quý (thẻ CBAM)</FieldLabel>
                <ObjectFieldsEditor
                  value={sec2.cbam_estimate}
                  onChange={(v) => patchSection("2", { cbam_estimate: v })}
                  selects={{ signal: ["increase", "hold", "reduce"] }}
                />
              </div>
            )}
            <details>
              <summary className="cursor-pointer text-[12px] font-bold uppercase tracking-wider text-muted-light">
                Dữ liệu nến EUA ({(sec2.chart_data || []).length} phiên — biểu đồ, mốc kỹ thuật)
              </summary>
              <div className="mt-2">
                <TableRowsEditor
                  columns={[
                    { key: "date", label: "Ngày (YYYY-MM-DD)", mono: true },
                    { key: "open", label: "Mở", type: "number" },
                    { key: "high", label: "Cao", type: "number" },
                    { key: "low", label: "Thấp", type: "number" },
                    { key: "close", label: "Đóng", type: "number" },
                    { key: "volume", label: "Khối lượng", type: "number" },
                  ]}
                  rows={sec2.chart_data}
                  onChange={(rows) => patchSection("2", { chart_data: rows })}
                  addLabel="Thêm phiên"
                />
              </div>
            </details>
            <SectionJson value={content["2"]} onChange={(v) => setSection("2", v)} />
          </SectionShell>
          )}

          {sec3 && (
            <SectionShell number="03" title={sec3.title || "Phân tích chuyên sâu"}>
              <div>
                <FieldLabel>Tiêu đề mục</FieldLabel>
                <TextInput value={sec3.title} onChange={(v) => patchSection("3", { title: v })} />
              </div>
              <div>
                <FieldLabel>Nguyên nhân &amp; điều chỉnh (so với báo cáo trước — hiện dưới bảng dự báo)</FieldLabel>
                <TextArea value={sec3.vs_previous} onChange={(v) => patchSection("3", { vs_previous: v })} rows={3} />
              </div>
              <div>
                <FieldLabel>Các khối phân tích</FieldLabel>
                <CardRowsEditor
                  columns={[
                    { key: "heading", label: "Tiêu đề khối" },
                    { key: "content", label: "Nội dung", type: "textarea" },
                  ]}
                  rows={sec3.analysis_blocks}
                  onChange={(rows) => patchSection("3", { analysis_blocks: rows })}
                  addLabel="Thêm khối phân tích"
                />
              </div>

              {sec3.correlation_analysis && (
                <div className="border-l-2 border-primary/40 pl-4 space-y-3">
                  <FieldLabel>Chuỗi logic: Gas + Than + Điện → EUA</FieldLabel>
                  {CORRELATION_FIELDS.map(({ key, label }) => (
                    <div key={key}>
                      <FieldLabel>{label}</FieldLabel>
                      <TextArea
                        rows={2}
                        value={sec3.correlation_analysis[key]}
                        onChange={(v) =>
                          onChange({
                            ...content,
                            "3": {
                              ...sec3,
                              correlation_analysis: { ...sec3.correlation_analysis, [key]: v },
                            },
                          })
                        }
                      />
                    </div>
                  ))}
                </div>
              )}

              {!noPriceData && (
              <div>
                <FieldLabel>Kịch bản giao dịch</FieldLabel>
                <CardRowsEditor
                  columns={[
                    { key: "horizon", label: "Khung thời gian", type: "select", options: ["ngắn hạn", "trung hạn", "dài hạn"] },
                    { key: "probability", label: "Xác suất", type: "select", options: ["Cao", "Trung bình", "Thấp"] },
                    { key: "direction", label: "Chiều giá", type: "select", options: ["tăng", "giảm", "đi ngang"] },
                    { key: "condition", label: "Điều kiện kích hoạt", type: "textarea" },
                    { key: "price_zone", label: "Vùng giá tham chiếu" },
                    { key: "key_risk", label: "Rủi ro chính", type: "textarea" },
                    { key: "trading_strategy", label: "Chiến lược bàn giao dịch", type: "textarea" },
                    // Báo cáo mới gom phòng hộ về 1 hedge_action (bên dưới) — chỉ báo cáo cũ còn hedge_note từng kịch bản.
                    ...(sec3.hedge_action ? [] : [{ key: "hedge_note", label: "Sổ phòng hộ SIM (CBAM)", type: "textarea" as const }]),
                  ]}
                  rows={sec3.trading_scenarios}
                  onChange={(rows) => patchSection("3", { trading_scenarios: rows })}
                  addLabel="Thêm kịch bản"
                />
              </div>
              )}

              {!noPriceData && (
                <div className="space-y-2">
                  <FieldLabel>Sổ phòng hộ SIM — nhãn chung với thẻ CBAM (increase / hold / reduce), phần lõi + phần chiến thuật ≤20%</FieldLabel>
                  <OptionalBlock
                    present={!!sec3.hedge_action}
                    onCreate={() => patchSection("3", { hedge_action: { action: "hold", core: "", text: "" } })}
                    label="Thêm hành động phòng hộ"
                  >
                    <Select
                      value={sec3.hedge_action?.action}
                      options={["increase", "hold", "reduce"]}
                      onChange={(v) => patchSection("3", { hedge_action: { ...sec3.hedge_action, action: v } })}
                    />
                    <FieldLabel>Phần lõi (bám lịch đấu giá)</FieldLabel>
                    <TextArea
                      rows={2}
                      value={sec3.hedge_action?.core}
                      onChange={(v) => patchSection("3", { hedge_action: { ...sec3.hedge_action, core: v } })}
                    />
                    <FieldLabel>Phần chiến thuật (≤20%, quan điểm giao dịch)</FieldLabel>
                    <TextArea
                      rows={2}
                      value={sec3.hedge_action?.text}
                      onChange={(v) => patchSection("3", { hedge_action: { ...sec3.hedge_action, text: v } })}
                    />
                    <button type="button" onClick={() => patchSection("3", { hedge_action: null })} className="text-xs text-muted-light hover:text-down">
                      Bỏ hành động phòng hộ
                    </button>
                  </OptionalBlock>
                </div>
              )}

              {!noPriceData && (
                <div>
                  <FieldLabel>Lệnh đang mở (đối chiếu với mức giá mới)</FieldLabel>
                  <CardRowsEditor
                    columns={[
                      { key: "report_date", label: "Báo cáo ra lệnh (YYYY-MM-DD)", mono: true },
                      { key: "side", label: "Chiều", type: "select", options: ["buy", "sell"] },
                      { key: "entry_low", label: "Entry thấp", type: "number" },
                      { key: "entry_high", label: "Entry cao", type: "number" },
                      { key: "filled_date", label: "Ngày khớp (YYYY-MM-DD)", mono: true },
                      { key: "fill_price", label: "Giá khớp", type: "number" },
                      { key: "stop", label: "Cắt lỗ", type: "number" },
                      { key: "target", label: "Mục tiêu", type: "number" },
                      { key: "decision", label: "Quyết định", type: "select", options: ["keep", "move", "close"] },
                      { key: "text", label: "Diễn giải", type: "textarea" },
                    ]}
                    rows={sec3.open_positions}
                    onChange={(rows) => patchSection("3", { open_positions: rows })}
                    addLabel="Thêm lệnh đang mở"
                  />
                </div>
              )}

              {!noPriceData && (
                <div>
                  <FieldLabel>Chú thích giả định mô hình chuyển đổi nhiên liệu</FieldLabel>
                  <TextArea value={sec3.fuel_model_note} onChange={(v) => patchSection("3", { fuel_model_note: v })} rows={3} />
                </div>
              )}

              <div>
                <FieldLabel>Nguồn đã trích trong Mục 3 (đưa vào Nguồn tham khảo)</FieldLabel>
                <TableRowsEditor
                  columns={SOURCE_COLUMNS}
                  rows={sec3.sources}
                  onChange={(rows) => patchSection("3", { sources: rows })}
                  addLabel="Thêm nguồn"
                />
              </div>
              <SectionJson value={content["3"]} onChange={(v) => setSection("3", v)} />
            </SectionShell>
          )}

          {["4"].map((key) => {
            const section = content[key];
            if (!section) return null;
            return (
              <SectionShell key={key} number={`0${key}`} title={section.title || `Mục ${key}`}>
                <div>
                  <FieldLabel>Tiêu đề mục</FieldLabel>
                  <TextInput value={section.title} onChange={(v) => patchSection(key, { title: v })} />
                </div>
                {section.bullets ? (
                  <div>
                    <FieldLabel>Nội dung (từng dòng)</FieldLabel>
                    <BulletsEditor bullets={section.bullets} onChange={(b) => patchSection(key, { bullets: b })} />
                  </div>
                ) : (
                  <div>
                    <FieldLabel>Nội dung</FieldLabel>
                    <TextArea value={section.text} onChange={(v) => patchSection(key, { text: v })} rows={4} />
                  </div>
                )}
                <SectionJson value={section} onChange={(v) => setSection(key, v)} />
              </SectionShell>
            );
          })}

          {content["6"] && (
            <SectionShell number="06" title={content["6"].title || "Chi tiết tin tức"}>
              <div>
                <FieldLabel>Tiêu đề mục</FieldLabel>
                <TextInput value={content["6"].title} onChange={(v) => patchSection("6", { title: v })} />
              </div>
              {[
                { key: "international", label: "Quốc tế" },
                { key: "vietnam", label: "Việt Nam" },
              ].map(({ key, label }) => (
                <div key={key}>
                  <FieldLabel>{label}</FieldLabel>
                  <CardRowsEditor
                    columns={[
                      { key: "title", label: "Tiêu đề bài" },
                      { key: "summary", label: "Tóm tắt", type: "textarea" },
                      { key: "source", label: "Nguồn" },
                      { key: "origin", label: "Hãng gốc (khi bài dẫn lại)" },
                      { key: "url", label: "URL", mono: true },
                      { key: "published_date", label: "Ngày đăng" },
                    ]}
                    rows={content["6"][key]}
                    onChange={(rows) => onChange({ ...content, "6": { ...content["6"], [key]: rows } })}
                    addLabel={`Thêm tin ${label.toLowerCase()}`}
                  />
                </div>
              ))}
              <SectionJson value={content["6"]} onChange={(v) => setSection("6", v)} />
            </SectionShell>
          )}

          {content["8"] && (
            <SectionShell number="08" title={content["8"].title || "Lịch sự kiện"}>
              <div>
                <FieldLabel>Tiêu đề mục</FieldLabel>
                <TextInput value={content["8"].title} onChange={(v) => patchSection("8", { title: v })} />
              </div>
              <div>
                <FieldLabel>Sự kiện</FieldLabel>
                <CardRowsEditor
                  columns={[
                    { key: "date", label: "Ngày (YYYY-MM-DD — dùng để xếp/lọc)", mono: true },
                    { key: "datetime_vn", label: "Thời gian hiển thị (VN)" },
                    { key: "event", label: "Sự kiện" },
                    { key: "impact", label: "Tác động", type: "select", options: ["Cao", "Trung", "Thấp"] },
                    { key: "outcome", label: "Kết quả (nếu đã diễn ra)", type: "textarea" },
                  ]}
                  rows={content["8"].events}
                  onChange={(rows) => patchSection("8", { events: rows })}
                  addLabel="Thêm sự kiện"
                />
              </div>
              <div>
                <FieldLabel>Bullets dự phòng (khi không dùng dạng lịch sự kiện)</FieldLabel>
                <BulletsEditor bullets={content["8"].bullets} onChange={(b) => patchSection("8", { bullets: b })} />
              </div>
              <SectionJson value={content["8"]} onChange={(v) => setSection("8", v)} />
            </SectionShell>
          )}

          {content["biz"] && (
            <SectionShell number="SIM" title={content["biz"].title || "Gợi ý kinh doanh"}>
              <div>
                <FieldLabel>Tiêu đề mục</FieldLabel>
                <TextInput value={content["biz"].title} onChange={(v) => patchSection("biz", { title: v })} />
              </div>
              <div>
                <FieldLabel>Ngắn hạn</FieldLabel>
                <TableRowsEditor
                  columns={[
                    { key: "trigger", label: "Tình huống kích hoạt" },
                    { key: "action", label: "Hành động đề xuất" },
                    { key: "reason", label: "Cơ sở của đề xuất" },
                    ...SOURCE_COLUMNS,
                  ]}
                  rows={content["biz"].short_term}
                  onChange={(rows) => patchSection("biz", { short_term: rows })}
                  addLabel="Thêm gợi ý ngắn hạn"
                />
              </div>
              <div>
                <FieldLabel>Dài hạn</FieldLabel>
                <TableRowsEditor
                  columns={[
                    { key: "opportunity", label: "Cơ hội" },
                    { key: "solution", label: "Giải pháp đề xuất" },
                    { key: "expectation", label: "Kỳ vọng" },
                    ...SOURCE_COLUMNS,
                  ]}
                  rows={content["biz"].long_term}
                  onChange={(rows) => patchSection("biz", { long_term: rows })}
                  addLabel="Thêm gợi ý dài hạn"
                />
              </div>
              <div>
                <FieldLabel>Jenny nhắc lại (gợi ý cũ vừa kích hoạt / bị tin tức vô hiệu)</FieldLabel>
                <CardRowsEditor
                  columns={[
                    { key: "suggested_date", label: "Ngày gợi ý", mono: true },
                    { key: "trigger", label: "Tình huống kích hoạt", type: "textarea" },
                    { key: "action", label: "Hành động", type: "textarea" },
                    { key: "reason", label: "Cơ sở", type: "textarea" },
                    { key: "outcome", label: "Kết quả", type: "select", options: ["triggered", "contradicted"] },
                    { key: "evidence", label: "Bằng chứng", type: "textarea" },
                    ...SOURCE_COLUMNS,
                  ]}
                  rows={content["biz"].reminders}
                  onChange={(rows) => patchSection("biz", { reminders: rows })}
                  addLabel="Thêm nhắc lại"
                />
              </div>
              <div>
                <FieldLabel>Gợi ý cũ đang theo dõi (chưa kích hoạt)</FieldLabel>
                <CardRowsEditor
                  columns={[
                    { key: "suggested_date", label: "Ngày gợi ý", mono: true },
                    { key: "trigger", label: "Tình huống kích hoạt", type: "textarea" },
                    { key: "action", label: "Hành động", type: "textarea" },
                    { key: "reason", label: "Cơ sở", type: "textarea" },
                    { key: "expires_on", label: "Theo dõi tới (YYYY-MM-DD)", mono: true },
                  ]}
                  rows={content["biz"].tracking}
                  onChange={(rows) => patchSection("biz", { tracking: rows })}
                  addLabel="Thêm gợi ý theo dõi"
                />
              </div>
              <SectionJson value={content["biz"]} onChange={(v) => setSection("biz", v)} />
            </SectionShell>
          )}

          {content["9"] && (
            <SectionShell number="09" title={content["9"].title || "Nguồn"}>
              <div>
                <FieldLabel>Tiêu đề mục</FieldLabel>
                <TextInput value={content["9"].title} onChange={(v) => patchSection("9", { title: v })} />
              </div>
              <CardRowsEditor
                columns={[
                  { key: "source", label: "Nguồn" },
                  { key: "title", label: "Tiêu đề" },
                  { key: "url", label: "URL" },
                ]}
                rows={content["9"].items}
                onChange={(rows) => patchSection("9", { items: rows })}
                addLabel="Thêm nguồn"
              />
              <SectionJson value={content["9"]} onChange={(v) => setSection("9", v)} />
            </SectionShell>
          )}
        </>
      )}
    </div>
  );
}
