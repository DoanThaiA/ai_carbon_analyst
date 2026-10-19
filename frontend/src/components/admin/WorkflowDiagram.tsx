"use client";

import { useTranslations } from "next-intl";

/* ── Tiny arrow primitives ── */

/** Horizontal right-arrow between pipeline nodes. */
function HArrow() {
  return (
    <div className="flex items-center shrink-0 self-center px-0.5">
      <div className="w-5 h-px bg-primary-dark/60" />
      <div className="w-0 h-0 border-y-[4px] border-y-transparent border-l-[6px] border-l-primary-dark/60" />
    </div>
  );
}

/** Dashed horizontal left-arrow (QC → Admin). */
function HDashedArrowLeft() {
  return (
    <div className="flex items-center shrink-0 self-center px-0.5">
      <div className="w-0 h-0 border-y-[4px] border-y-transparent border-r-[6px] border-r-primary-dark/50" />
      <div className="w-5 border-t border-dashed border-primary-dark/50" />
    </div>
  );
}

/** Pipeline node – solid thin border. */
function PNode({ title, desc }: { title: string; desc: string }) {
  return (
    <div className="flex-1 min-w-0 border border-primary-dark/25 rounded-lg px-3 py-2.5 bg-background">
      <p className="font-semibold text-[13px] text-label leading-tight">{title}</p>
      <p className="text-[11px] text-body mt-0.5 leading-snug">{desc}</p>
    </div>
  );
}

/** Dashed-border node (Bản QC, Người đọc). */
function DNode({ title, desc }: { title: string; desc: string }) {
  return (
    <div className="flex-1 min-w-0 border border-dashed border-primary-dark/30 rounded-lg px-3 py-2.5 bg-background">
      <p className="font-semibold text-[13px] text-label leading-tight">{title}</p>
      <p className="text-[11px] text-body mt-0.5 leading-snug">{desc}</p>
    </div>
  );
}

/**
 * Sơ đồ quy trình làm việc giữa các vai trò trong hệ thống.
 * Hiển thị trên trang Đồng nghiệp (/admin/users), giúp người quản trị
 * hình dung luồng xử lý báo cáo hàng ngày.
 */
export default function WorkflowDiagram() {
  const t = useTranslations("adminUsrUsers");

  return (
    <div className="bg-background border border-border rounded-2xl p-5 sm:p-6">
      <p className="text-xs font-semibold text-muted-light uppercase tracking-wider mb-4">
        {t("wf.heading")}
      </p>

      <div className="overflow-x-auto pb-1 -mx-5 px-5 sm:-mx-6 sm:px-6">
        <div style={{ minWidth: 760 }} className="space-y-2">

          {/* ── Top pipeline: Sonic → Jenny → Jack → Phòng CLPT → Báo cáo ── */}
          <div className="flex items-stretch">
            <PNode title="Sonic" desc={t("wf.sonic")} />
            <HArrow />
            <PNode title="Jenny" desc={t("wf.jenny")} />
            <HArrow />
            <PNode title="Jack (Claude.ai)" desc={t("wf.jack")} />
            <HArrow />
            <PNode title={t("wf.clptRoom")} desc={t("wf.clptRoomDesc")} />
            <HArrow />
            <PNode title={t("wf.report")} desc={t("wf.reportDesc")} />
          </div>

          {/* ── Vertical connector: ↑ "nâng cấp Jenny" ──
              Aligned under the 2nd box (Jenny). Uses the same flex structure
              as the top row so the column widths match automatically. */}
          <div className="flex items-start" aria-hidden="true">
            {/* spacer: 1st box */}
            <div className="flex-1 min-w-0" />
            {/* spacer: 1st arrow */}
            <div className="shrink-0 px-0.5 w-[26px]" />
            {/* Jenny column — arrow lives here */}
            <div className="flex-1 min-w-0 flex justify-center">
              <div className="flex flex-col items-center -mt-0.5">
                <span className="text-[11px] italic text-primary-dark mb-0.5">{t("wf.upgradeJenny")}</span>
                <div className="w-0 h-0 border-x-[4px] border-x-transparent border-b-[6px] border-b-primary-dark/60" />
                <div className="h-4 w-px bg-primary-dark/60" />
              </div>
            </div>
            {/* spacer: remaining 3 boxes + 3 arrows */}
            <div className="shrink-0 px-0.5 w-[26px]" />
            <div className="flex-1 min-w-0" />
            <div className="shrink-0 px-0.5 w-[26px]" />
            <div className="flex-1 min-w-0" />
            <div className="shrink-0 px-0.5 w-[26px]" />
            <div className="flex-1 min-w-0" />
          </div>

          {/* ── Bottom row: Admin | ←--- Bản QC | Người đọc ── */}
          <div className="flex items-stretch gap-3">
            {/* Trưởng phòng CLPT (Admin) — dark teal card */}
            <div className="bg-primary-dark text-white rounded-lg px-4 py-3 min-w-0" style={{ flex: "2.2 1 0%" }}>
              <p className="font-bold text-[13px] leading-tight">{t("wf.adminTitle")}</p>
              <p className="text-[11px] mt-1 leading-snug opacity-90">{t("wf.adminLine1")}</p>
              <p className="text-[11px] italic leading-snug opacity-75">{t("wf.adminLine2")}</p>
            </div>

            <HDashedArrowLeft />

            <DNode title={t("wf.qcTitle")} desc={t("wf.qcDesc")} />
            <DNode title={t("wf.readerTitle")} desc={t("wf.readerDesc")} />
          </div>
        </div>
      </div>
    </div>
  );
}
