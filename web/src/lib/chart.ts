import { compactMoney, number } from "./format";

// Khối "chart" (kernel/ui.py): server tính số, web chỉ vẽ (Charts.swift). null ⇒ KHÔNG vẽ và KHÔNG thành 0 — "không đo được" khác "bằng 0".

/** `as` / `axis` chỉ có ở biểu đồ "combo" (0166): cột hay đường, trục trái hay phải. */
export type ChartSeries = { name: string; values: (number | null)[]; tone?: string; as?: "bar" | "line"; axis?: "left" | "right"; x?: (number | null)[] };
/** `pick` (0173): bấm nhãn i ⇒ bộ lọc `param` của màn = `values[i]` (bấm lại: bỏ lọc). */
export type ChartPick = { param: string; values: string[] };
export type ChartData = { title: string; kind: string; unit: string; unit_right?: string; labels: string[]; series: ChartSeries[]; note: string; pick?: ChartPick };

export const CHART_KINDS = ["bar", "hbar", "line", "area", "donut", "funnel", "combo", "scatter", "treemap"] as const;

/** Số trên ô / nhãn: tiền gọn ("45,77 tỷ", "461 tr", "0 đ"), phần trăm một số lẻ, còn lại số đếm. */
export function chartValue(v: number, unit: string): string {
  if (unit === "vnd") return compactMoney(v);
  if (unit === "pct") return `${number(Math.round(v * 10) / 10)}%`;
  return number(v);
}

/** Nhãn trục: gọn hơn ô số — "0" thay vì "0 đ". */
export function chartAxis(v: number, unit: string): string {
  return v === 0 ? "0" : chartValue(v, unit);
}

/** null hết ⇒ chưa đo được; phễu / bánh toàn 0 cũng không có gì để vẽ (cột / đường toàn 0 vẫn là thông tin thật: "chưa phát sinh"). */
export function hasChartData(c: Pick<ChartData, "kind" | "series">): boolean {
  const all = c.series.flatMap((s) => s.values).filter((v): v is number => typeof v === "number");
  if (all.length === 0) return false;
  return !((c.kind === "funnel" || c.kind === "donut") && all.every((v) => v === 0));
}

/** Màu hệ thống (biến CSS sáng / tối trong globals.css); không ghi tông ⇒ lần lượt theo bảng. */
const TONES = ["blue", "teal", "green", "orange", "purple", "red", "pink", "indigo", "mint"];
const PALETTE = ["blue", "teal", "orange", "purple", "pink", "indigo", "mint"];
export function chartColor(tone: string | undefined, index = 0): string {
  const name = tone && TONES.includes(tone) ? tone : PALETTE[index % PALETTE.length];
  return `var(--chart-${name})`;
}

/** Dòng dữ liệu cho Recharts: một dòng / nhãn, khoá s0, s1… theo chuỗi; null giữ nguyên (Recharts bỏ qua, không vẽ thành 0). */
export function chartRows(c: ChartData): Record<string, string | number | null>[] {
  return c.labels.map((label, li) => {
    const row: Record<string, string | number | null> = { label };
    c.series.forEach((s, si) => {
      const v = s.values[li];
      row[`s${si}`] = typeof v === "number" ? v : null;
    });
    return row;
  });
}

export type FunnelStep = { label: string; value: number | null; pctPrev: number | null };

/** Phễu: một số đo, nhạt dần theo bước; "% bước trước" chỉ khi cả hai bước đo được và bước trước > 0. */
export function funnelSteps(c: ChartData): FunnelStep[] {
  const vals = c.series[0]?.values ?? [];
  return c.labels.map((label, i) => {
    const v = typeof vals[i] === "number" ? (vals[i] as number) : null;
    const prev = i > 0 && typeof vals[i - 1] === "number" ? (vals[i - 1] as number) : null;
    return { label, value: v, pctPrev: v !== null && prev !== null && prev > 0 ? Math.round((v / prev) * 100) : null };
  });
}

export type Slice = { index: number; label: string; value: number; pct: number | null };

/** Bánh: bỏ miếng null; phần trăm làm tròn theo tổng các miếng đo được. */
export function donutSlices(c: ChartData): { slices: Slice[]; total: number } {
  const vals = c.series[0]?.values ?? [];
  const items = c.labels.flatMap((label, index) => (typeof vals[index] === "number" ? [{ index, label, value: vals[index] as number }] : []));
  const total = items.reduce((a, b) => a + b.value, 0);
  return { slices: items.map((x) => ({ ...x, pct: total > 0 ? Math.round((x.value / total) * 100) : null })), total };
}

/** Đỉnh của trục ngang (thanh ngang / phễu): chừa chỗ in số cuối thanh. */
export function headroom(c: ChartData, factor: number): number {
  const all = c.series.flatMap((s) => s.values).filter((v): v is number => typeof v === "number");
  return Math.max(Math.max(0, ...all) * factor, 1);
}

/** Đơn vị của MỘT chuỗi: chuỗi trục phải của combo đo bằng `unit_right` (trống ⇒ như `unit`). */
export function seriesUnit(c: Pick<ChartData, "unit" | "unit_right">, s: ChartSeries): string {
  return s.axis === "right" && c.unit_right ? c.unit_right : c.unit;
}

/** Combo: chuỗi nào vẽ cột, chuỗi nào vẽ đường, có trục phải không. Không khai `as` ⇒ cột; không khai `axis` ⇒ trục trái. */
export function comboSeries(c: ChartData): { index: number; series: ChartSeries; as: "bar" | "line"; axis: "left" | "right" }[] {
  return c.series.map((series, index) => ({ index, series, as: series.as === "line" ? "line" : "bar", axis: series.axis === "right" ? "right" : "left" }));
}

/** Chiều cao (px) phần vẽ của biểu đồ — cùng số với ChartContainer ở chart-body.tsx (+ một hàng chú giải khi ≥ 2 chuỗi). Khung thẻ giữ sẵn chỗ này
 *  trong lúc phần vẽ (Recharts) còn đang tải ⇒ thẻ không nhảy khi vẽ xong. Đổi chiều cao ở chart-body.tsx ⇒ đổi cả ở đây. */
export function chartBodyHeight(c: Pick<ChartData, "kind" | "labels" | "series">): number {
  const legend = c.series.length > 1 ? 24 : 0;
  switch (c.kind) {
    case "hbar":
      return c.labels.length * 34 * Math.max(1, c.series.length) + 8;
    case "funnel":
      return c.labels.length * 40 + 8;
    case "donut":
      return 168;
    case "scatter":
      return 240;
    case "treemap":
      return 260;
    case "combo":
      return 220 + legend;
    default:
      return 200 + legend;
  }
}
