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

function useStringListOps(list: string[] | undefined, onChange: (next: string[]) => void) {
  const items = list || [];
  const update = (i: number, value: string) => {
    const next = items.slice();
    next[i] = value;
    onChange(next);
  };
  const remove = (i: number) => onChange(items.filter((_, idx) => idx !== i));
  const add = () => onChange([...items, ""]);
  return { items, update, remove, add };
}

function BulletsEditor({
  bullets,
  onChange,
  placeholder,
}: {
  bullets: string[] | undefined;
  onChange: (b: string[]) => void;
  placeholder?: string;
}) {
  const { items, update, remove, add } = useStringListOps(bullets, onChange);
  return (
    <div className="space-y-2">
      {items.map((b, i) => (
        <div key={i} className="flex gap-2 items-start group">
          <span className="font-mono text-[10px] text-muted-light pt-3 w-4 shrink-0">{i + 1}</span>
          <textarea
            value={b}
            onChange={(e) => update(i, e.target.value)}
            rows={2}
            placeholder={placeholder}
            className="flex-1 text-[13.5px] p-2.5 bg-surface border border-border rounded-lg outline-none focus:border-primary focus:ring-1 focus:ring-primary/20 resize-y"
          />
          <button
            type="button"
            onClick={() => remove(i)}
            title="Xóa dòng"
            className="text-muted-light hover:text-down p-1.5 mt-1 opacity-60 group-hover:opacity-100 transition-opacity shrink-0"
          >
            <Trash2 size={15} />
          </button>
        </div>
      ))}
      <AddButton onClick={add} label="Thêm dòng" />
    </div>
  );
}

type ColumnDef = {
  key: string;
  label: string;
  type?: "text" | "textarea" | "select";
  options?: string[];
  mono?: boolean;
};

function emptyRow(columns: ColumnDef[]) {
  return Object.fromEntries(columns.map((c) => [c.key, ""]));
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
                      {c.type === "select" ? (
                        <select
                          value={row[c.key] || ""}
                          onChange={(e) => update(i, { [c.key]: e.target.value })}
                          className="w-full text-[12.5px] p-1.5 bg-surface border border-border rounded outline-none focus:border-primary"
                        >
                          <option value="" disabled>
                            —
                          </option>
                          {c.options!.map((o) => (
                            <option key={o} value={o}>
                              {o}
                            </option>
                          ))}
                        </select>
                      ) : c.type === "textarea" ? (
                        <textarea
                          value={row[c.key] || ""}
                          onChange={(e) => update(i, { [c.key]: e.target.value })}
                          rows={2}
                          className="w-full text-[12.5px] p-1.5 bg-surface border border-border rounded outline-none focus:border-primary resize-y"
                        />
                      ) : (
                        <input
                          value={row[c.key] || ""}
                          onChange={(e) => update(i, { [c.key]: e.target.value })}
                          className={clsx(
                            "w-full text-[12.5px] p-1.5 bg-surface border border-border rounded outline-none focus:border-primary",
                            c.mono && "font-mono"
                          )}
                        />
                      )}
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
                {c.type === "select" ? (
                  <Select value={row[c.key] || ""} onChange={(v) => update(i, { [c.key]: v })} options={c.options!} />
                ) : c.type === "textarea" ? (
                  <TextArea value={row[c.key] || ""} onChange={(v) => update(i, { [c.key]: v })} rows={2} />
                ) : (
                  <TextInput value={row[c.key] || ""} onChange={(v) => update(i, { [c.key]: v })} mono={c.mono} />
                )}
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

export function ReportEditor({ content, onChange }: { content: Json; onChange: (next: Json) => void }) {
  const [rawMode, setRawMode] = useState(false);
  const [rawText, setRawText] = useState(() => JSON.stringify(content, null, 2));
  const [rawError, setRawError] = useState("");

  const patchSection = (key: string, patch: Json) =>
    onChange({ ...content, [key]: { ...(content[key] || {}), ...patch } });

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
            <FieldLabel>Các gạch đầu dòng (dạng &quot;Tag: nội dung&quot;)</FieldLabel>
            <BulletsEditor
              bullets={content["1"]?.bullets}
              onChange={(b) => patchSection("1", { bullets: b })}
              placeholder="Vd: EUA: giá đóng cửa tăng 1.2% do..."
            />
          </SectionShell>

          <SectionShell number="02" title="Bảng giá nhanh">
            <div>
              <FieldLabel>Thời điểm chốt giá</FieldLabel>
              <TextInput mono value={sec2.price_timestamp} onChange={(v) => patchSection("2", { price_timestamp: v })} />
            </div>
            <div>
              <FieldLabel>Bảng giá</FieldLabel>
              <TableRowsEditor
                columns={[
                  { key: "name", label: "Hợp đồng" },
                  { key: "price", label: "Giá", mono: true },
                  { key: "dday", label: "Δ Ngày", mono: true },
                  { key: "dweek", label: "Δ Tuần", mono: true },
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
                    { key: "tag", label: "Tag", type: "select", options: ["FACT", "ANALYSIS"] },
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
                    { key: "tag", label: "Tag", type: "select", options: ["FACT", "ANALYSIS"] },
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
          </SectionShell>

          {sec3 && (
            <SectionShell number="03" title={sec3.title || "Phân tích chuyên sâu"}>
              <div>
                <FieldLabel>Tiêu đề mục</FieldLabel>
                <TextInput value={sec3.title} onChange={(v) => patchSection("3", { title: v })} />
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

              <div>
                <FieldLabel>Kịch bản giao dịch</FieldLabel>
                <CardRowsEditor
                  columns={[
                    { key: "horizon", label: "Khung thời gian", type: "select", options: ["ngắn hạn", "trung hạn", "dài hạn"] },
                    { key: "probability", label: "Xác suất", type: "select", options: ["Cao", "Trung bình", "Thấp"] },
                    { key: "direction", label: "Chiều giá", type: "select", options: ["tăng", "giảm", "đi ngang"] },
                    { key: "condition", label: "Điều kiện kích hoạt", type: "textarea" },
                    { key: "price_zone", label: "Vùng giá tham chiếu" },
                    { key: "market_pricing", label: "Định giá thị trường", type: "textarea" },
                    { key: "key_risk", label: "Rủi ro chính", type: "textarea" },
                    { key: "action_plan", label: "Kế hoạch hành động", type: "textarea" },
                  ]}
                  rows={sec3.trading_scenarios}
                  onChange={(rows) => patchSection("3", { trading_scenarios: rows })}
                  addLabel="Thêm kịch bản"
                />
              </div>
            </SectionShell>
          )}

          {["4", "5"].map((key) => {
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
                      { key: "url", label: "URL" },
                    ]}
                    rows={content["6"][key]}
                    onChange={(rows) => onChange({ ...content, "6": { ...content["6"], [key]: rows } })}
                    addLabel={`Thêm tin ${label.toLowerCase()}`}
                  />
                </div>
              ))}
            </SectionShell>
          )}

          {content["7"] && (
            <SectionShell number="07" title={content["7"].title || "Quan điểm trái chiều"}>
              <div>
                <FieldLabel>Tiêu đề mục</FieldLabel>
                <TextInput value={content["7"].title} onChange={(v) => patchSection("7", { title: v })} />
              </div>
              <div>
                <FieldLabel>Các quan điểm</FieldLabel>
                <CardRowsEditor
                  columns={[
                    { key: "viewpoint", label: "Nội dung quan điểm", type: "textarea" },
                    { key: "source_name", label: "Tên nguồn" },
                    { key: "source_url", label: "URL nguồn" },
                  ]}
                  rows={content["7"].points}
                  onChange={(rows) => patchSection("7", { points: rows })}
                  addLabel="Thêm quan điểm"
                />
              </div>
              <div>
                <FieldLabel>Text dự phòng (hiện khi không có quan điểm nào)</FieldLabel>
                <TextArea value={content["7"].text} onChange={(v) => patchSection("7", { text: v })} rows={2} />
              </div>
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
                    { key: "datetime_vn", label: "Thời gian (VN)" },
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
                    { key: "reason", label: "Lý do" },
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
                  ]}
                  rows={content["biz"].long_term}
                  onChange={(rows) => patchSection("biz", { long_term: rows })}
                  addLabel="Thêm gợi ý dài hạn"
                />
              </div>
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
            </SectionShell>
          )}
        </>
      )}
    </div>
  );
}
