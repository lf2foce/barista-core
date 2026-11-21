"use client";

import dynamic from "next/dynamic";
import { Spinner } from "@/components/primitives";

// Sổ DUY NHẤT của các khối nặng tải theo nhu cầu (next/dynamic): Recharts + d3, lịch, soạn văn bản, Word, xem PDF, kéo thả dashboard (dnd-kit),
// nhập Excel, nạp tài liệu, ⌘K (cmdk). Chúng KHÔNG nằm trong JS tải lần đầu của "/" và "/login" — chỉ tải khi màn thật sự vẽ tới.
// Khối nhẹ (bảng, thẻ số, danh sách, kanban, dòng thời gian…) vẫn nhập tĩnh như cũ. Thêm khối nặng mới ⇒ thêm ở ĐÂY rồi nhập từ đây.

/** Chỗ giữ khi khối chiếm cả thẻ còn đang tải: cùng nền, không đổ bóng, vòng quay như mọi chỗ "đang tải" khác. */
function Pending() {
  return (
    <div className="grid min-h-[240px] flex-1 place-items-center">
      <Spinner />
    </div>
  );
}
const none = () => null;

/** Phần vẽ của biểu đồ — khung thẻ (chart.tsx) đã giữ sẵn chiều cao nên lúc tải không vẽ gì thêm. */
export const ChartBody = dynamic(() => import("./chart-body").then((m) => m.ChartBody), { loading: none });
export const CalendarBlock = dynamic(() => import("./calendar").then((m) => m.CalendarBlock), { loading: Pending });
export const ComposerBlock = dynamic(() => import("./composer").then((m) => m.ComposerBlock), { loading: Pending });
export const WordDocBlock = dynamic(() => import("./word-doc").then((m) => m.WordDocBlock), { loading: Pending });
export const DashboardEditor = dynamic(() => import("@/components/pages/dashboard-editor").then((m) => m.DashboardEditor), { loading: Pending });
export const ImportWizard = dynamic(() => import("@/components/forms/import-wizard").then((m) => m.ImportWizard), { loading: Pending });
// hộp thoại: chỉ dựng khi mở ⇒ lúc tải không cần chỗ giữ
export const PdfDialog = dynamic(() => import("./pdf-viewer").then((m) => m.PdfDialog), { loading: none });
export const KbIntakeDialog = dynamic(() => import("@/components/forms/kb-intake").then((m) => m.KbIntakeDialog), { loading: none });
export const CommandPalette = dynamic(() => import("@/components/desk/palette").then((m) => m.CommandPalette), { loading: none });

/** Tải sẵn phần vẽ biểu đồ khi rảnh (màn chủ của nhiều vai là dashboard): chạy song song với lúc chờ dữ liệu màn, không chặn lần vẽ đầu. */
export function preloadCharts(): void {
  void import("./chart-body");
}
