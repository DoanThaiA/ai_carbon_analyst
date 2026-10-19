"use client";

// Sơ đồ luồng công việc AI Agent + kiểm duyệt báo cáo: Sonic → Jenny → Jack → Phòng CLPT → Báo cáo,
// nhánh phản hồi (Bản QC, Người đọc) quay về Trưởng phòng CLPT (Admin) để nâng cấp Jenny.
// Vẽ toàn bộ bằng 1 SVG trong hệ toạ độ cố định (viewBox) → khối và đường nối luôn khớp nhau khi co giãn;
// màn hình hẹp thì cuộn ngang (min-width), không ép các khối chồng lên nhau. Sửa nội dung/vị trí ở
// WORKFLOW_NODES / WORKFLOW_EDGES bên dưới — đường nối tự bám theo cạnh khối qua `anchor()`. Chữ nằm ở namespace
// i18n `reportWorkflow` (vi/en): `<key>.title` + `<key>.desc` (xuống dòng bằng "\n" — giữ đúng số dòng của khối).

import { useTranslations } from "next-intl";

const VIEW_W = 1240;
const VIEW_H = 460;
// Bề rộng hiển thị: tối đa MAX_W (≈ 0,8 lần toạ độ gốc → tiêu đề ~16px, mô tả ~13px), căn giữa vùng nội dung;
// hẹp hơn MIN_W thì cuộn ngang thay vì thu chữ quá nhỏ.
const MAX_W = 1000;
const MIN_W = 820;

const COLORS = {
  background: "#F3F7F7",
  teal: "#064E40",
  nodeBorder: "#D5DEDD",
  title: "#111827",
  text: "#4B5563",
  white: "#FFFFFF",
};

type NodeVariant = "default" | "emphasis" | "dashed" | "admin";

export type WorkflowNode = {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
  key: string; // khoá i18n trong namespace reportWorkflow
  variant: NodeVariant;
};

type Side = "top" | "right" | "bottom" | "left";
type Anchor = { node: string; side: Side; at?: number }; // at: vị trí dọc cạnh, 0–1 (mặc định giữa)
type Point = [number, number];

export type WorkflowEdge = {
  id: string;
  from: Anchor;
  to: Anchor;
  dashed?: boolean;
  // Điểm gấp khúc trung gian (đường vuông góc). Dùng hàm để bám theo vị trí khối.
  via?: (from: Point, to: Point) => Point[];
  label?: { key: string; dx: number; dy: number }; // key: khoá i18n trong reportWorkflow
};

// Hàng trên: 5 khối rộng 200, cách nhau 48 → 24 … 1216.
const ROW_Y = 30;
const NODE_W = 200;
const NODE_H = 110;
const col = (i: number) => 24 + i * (NODE_W + 48);
const LOWER_Y = 250;

export const WORKFLOW_NODES: WorkflowNode[] = [
  { id: "sonic", x: col(0), y: ROW_Y, w: NODE_W, h: NODE_H, key: "sonic", variant: "default" },
  { id: "jenny", x: col(1), y: ROW_Y, w: NODE_W, h: NODE_H, key: "jenny", variant: "default" },
  { id: "jack", x: col(2), y: ROW_Y, w: NODE_W, h: NODE_H, key: "jack", variant: "default" },
  { id: "clpt", x: col(3), y: ROW_Y, w: NODE_W, h: NODE_H, key: "clpt", variant: "default" },
  { id: "report", x: col(4), y: ROW_Y, w: NODE_W, h: NODE_H, key: "report", variant: "emphasis" },
  // ~58% bề rộng sơ đồ, bắt đầu sát mép trái.
  { id: "admin", x: 24, y: LOWER_Y, w: 690, h: NODE_H, key: "admin", variant: "admin" },
  { id: "qc", x: col(3), y: LOWER_Y, w: NODE_W, h: NODE_H, key: "qc", variant: "dashed" },
  { id: "reader", x: col(4), y: LOWER_Y, w: NODE_W, h: NODE_H, key: "reader", variant: "dashed" },
];

const FEEDBACK_FLOOR_Y = 440; // đường phản hồi từ Người đọc chạy sát đáy sơ đồ
const FEEDBACK_ENTRY_X = 560; // điểm đi lên cạnh dưới khối Admin

export const WORKFLOW_EDGES: WorkflowEdge[] = [
  { id: "sonic-jenny", from: { node: "sonic", side: "right" }, to: { node: "jenny", side: "left" } },
  { id: "jenny-jack", from: { node: "jenny", side: "right" }, to: { node: "jack", side: "left" } },
  { id: "jack-clpt", from: { node: "jack", side: "right" }, to: { node: "clpt", side: "left" } },
  { id: "clpt-report", from: { node: "clpt", side: "right" }, to: { node: "report", side: "left" } },
  {
    id: "admin-jenny", from: { node: "admin", side: "top", at: (col(1) + NODE_W / 2 - 24) / 690 }, to: { node: "jenny", side: "bottom" },
    label: { key: "upgradeJenny", dx: 12, dy: 6 },
  },
  { id: "clpt-qc", from: { node: "clpt", side: "bottom" }, to: { node: "qc", side: "top" }, dashed: true },
  { id: "qc-admin", from: { node: "qc", side: "left" }, to: { node: "admin", side: "right" }, dashed: true },
  { id: "report-reader", from: { node: "report", side: "bottom" }, to: { node: "reader", side: "top" }, dashed: true },
  {
    id: "reader-admin", from: { node: "reader", side: "bottom" },
    to: { node: "admin", side: "bottom", at: (FEEDBACK_ENTRY_X - 24) / 690 }, dashed: true,
    via: (from, to) => [[from[0], FEEDBACK_FLOOR_Y], [to[0], FEEDBACK_FLOOR_Y]],
  },
];

const NODE_BY_ID = Object.fromEntries(WORKFLOW_NODES.map((n) => [n.id, n]));

function anchor({ node, side, at = 0.5 }: Anchor): Point {
  const n = NODE_BY_ID[node];
  switch (side) {
    case "top": return [n.x + n.w * at, n.y];
    case "bottom": return [n.x + n.w * at, n.y + n.h];
    case "left": return [n.x, n.y + n.h * at];
    case "right": return [n.x + n.w, n.y + n.h * at];
  }
}

function edgePoints(e: WorkflowEdge): Point[] {
  const a = anchor(e.from);
  const b = anchor(e.to);
  return [a, ...(e.via ? e.via(a, b) : []), b];
}

function NodeBox({ node }: { node: WorkflowNode }) {
  const t = useTranslations("reportWorkflow");
  const lines = t(`${node.key}.desc`).split("\n");
  const isAdmin = node.variant === "admin";
  const stroke = isAdmin ? COLORS.teal
    : node.variant === "emphasis" ? COLORS.teal
      : node.variant === "dashed" ? COLORS.teal
        : COLORS.nodeBorder;
  // Khối Admin: chữ trắng căn trái, padding 18. Các khối khác: căn giữa.
  const textX = isAdmin ? node.x + 18 : node.x + node.w / 2;
  const anchorAttr = isAdmin ? "start" : "middle";
  const titleY = isAdmin ? node.y + 18 + 20 : node.y + 42;
  const lineStart = isAdmin ? titleY + 30 : titleY + 26;
  const lineGap = isAdmin ? 26 : 22;

  return (
    <g>
      <rect
        x={node.x}
        y={node.y}
        width={node.w}
        height={node.h}
        rx={14}
        fill={isAdmin ? COLORS.teal : COLORS.white}
        stroke={stroke}
        strokeWidth={node.variant === "emphasis" ? 2.5 : node.variant === "default" ? 1.5 : 2}
        strokeDasharray={node.variant === "dashed" ? "7 5" : undefined}
      />
      <text x={textX} y={titleY} textAnchor={anchorAttr} fontSize={20} fontWeight={700} fill={isAdmin ? COLORS.white : COLORS.title}>
        {t(`${node.key}.title`)}
      </text>
      {lines.map((line, i) => (
        <text
          key={i}
          x={textX}
          y={lineStart + i * lineGap}
          textAnchor={anchorAttr}
          fontSize={isAdmin ? 17 : 16}
          fill={isAdmin ? COLORS.white : COLORS.text}
          fillOpacity={isAdmin ? 0.92 : 1}
        >
          {line}
        </text>
      ))}
    </g>
  );
}

export function ReportWorkflowDiagram({ className }: { className?: string }) {
  const t = useTranslations("reportWorkflow");
  const title = t("ariaLabel");
  return (
    <div className={className} style={{ overflowX: "auto", maxWidth: MAX_W, marginInline: "auto", borderRadius: 16, background: COLORS.background }}>
      <svg
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        role="img"
        aria-label={title}
        style={{ display: "block", width: "100%", minWidth: MIN_W, height: "auto" }}
      >
        <title>{title}</title>
        <defs>
          <marker id="rwd-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill={COLORS.teal} />
          </marker>
        </defs>
        <rect width={VIEW_W} height={VIEW_H} fill={COLORS.background} />

        {/* Đường nối vẽ TRƯỚC khối → luôn nằm phía sau, không che chữ. */}
        <g fill="none" stroke={COLORS.teal} strokeWidth={2} strokeLinejoin="round">
          {WORKFLOW_EDGES.map((e) => (
            <polyline
              key={e.id}
              points={edgePoints(e).map((p) => p.join(",")).join(" ")}
              strokeDasharray={e.dashed ? "7 5" : undefined}
              markerEnd="url(#rwd-arrow)"
            />
          ))}
        </g>
        {WORKFLOW_EDGES.filter((e) => e.label).map((e) => {
          const [a, b] = edgePoints(e);
          return (
            <text
              key={`${e.id}-label`}
              x={(a[0] + b[0]) / 2 + e.label!.dx}
              y={(a[1] + b[1]) / 2 + e.label!.dy}
              fontSize={17}
              fontWeight={700}
              fill="#000000"
            >
              {t(e.label!.key)}
            </text>
          );
        })}

        {WORKFLOW_NODES.map((n) => <NodeBox key={n.id} node={n} />)}
      </svg>
    </div>
  );
}
