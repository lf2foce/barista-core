import type { TimelineData, TimelineGroup, TimelineItem } from "./types";

// Dòng thời gian (0222): phần TOÁN VẼ (ngày → vị trí) — mọi dữ kiện (ngày, nhóm, mốc, ngày nghỉ, phụ thuộc) do server gửi, ở đây không tính nghiệp vụ.

export const ROW_H = 32;
export const HEAD_H = 40;
const DAY_MS = 86_400_000;

export function dayNumber(iso: string): number {
  const [y, m, d] = iso.split("-").map(Number);
  return Math.round(Date.UTC(y, m - 1, d) / DAY_MS);
}

export function daysBetween(a: string, b: string): number {
  return dayNumber(b) - dayNumber(a);
}

/** Số điểm ảnh một ngày: vừa khung ~960px nhưng không hẹp hơn 10 / rộng hơn 32 (kế hoạch dài ⇒ cuộn ngang). */
export function pxPerDay(spanDays: number): number {
  return Math.max(10, Math.min(32, Math.round(960 / Math.max(1, spanDays))));
}

export type Range = { start: string; days: number; px: number; width: number };

export function rangeOf(t: Pick<TimelineData, "start" | "end">): Range {
  const days = daysBetween(t.start, t.end) + 1;
  const px = pxPerDay(days);
  return { start: t.start, days, px, width: days * px };
}

/** Vị trí ngang (px) của ĐẦU ngày `iso`; ngoài khoảng thì kẹp vào hai mép. */
export function xOf(r: Range, iso: string): number {
  return Math.max(0, Math.min(r.days, daysBetween(r.start, iso))) * r.px;
}

/** Thanh từ đầu ngày `start` tới HẾT ngày `end` (hạn là ngày phải xong ⇒ gồm cả ngày đó); rộng tối thiểu 1 ngày. */
export function barBox(r: Range, start: string, end: string | null): { left: number; width: number } {
  const left = xOf(r, start);
  const right = end ? xOf(r, end) + r.px : left + r.px;
  return { left, width: Math.max(r.px, right - left) };
}

export type FlatRow =
  | { kind: "group"; group: TimelineGroup; open: boolean; y: number }
  | { kind: "item"; item: TimelineItem; group: TimelineGroup; y: number };

/** Các hàng đang hiện (nhóm gấp ⇒ chỉ hàng tiêu đề), theo thứ tự vẽ; `y` = đỉnh hàng. */
export function flatten(groups: TimelineGroup[], opened: Record<string, boolean>): FlatRow[] {
  const out: FlatRow[] = [];
  let y = 0;
  for (const g of groups) {
    const open = opened[g.key] ?? !g.collapsed;
    out.push({ kind: "group", group: g, open, y });
    y += ROW_H;
    if (open) {
      for (const item of g.items) {
        out.push({ kind: "item", item, group: g, y });
        y += ROW_H;
      }
    }
  }
  return out;
}

export type Arrow = { from: { x: number; y: number }; to: { x: number; y: number } };

/** Mũi tên phụ thuộc giữa các dòng ĐANG HIỆN: từ cuối thanh việc trước tới đầu thanh việc sau. Dòng không hiện (nhóm gấp) ⇒ không vẽ. */
export function arrows(rows: FlatRow[], r: Range): Arrow[] {
  const at = new Map<string, { item: TimelineItem; y: number }>();
  for (const row of rows) if (row.kind === "item") at.set(row.item.id, { item: row.item, y: row.y });
  const out: Arrow[] = [];
  for (const { item, y } of at.values()) {
    for (const id of item.after) {
      const pre = at.get(id);
      if (!pre) continue;
      const pb = barBox(r, pre.item.start, pre.item.end);
      const sb = barBox(r, item.start, item.end);
      out.push({ from: { x: pb.left + pb.width, y: pre.y + ROW_H / 2 }, to: { x: sb.left, y: y + ROW_H / 2 } });
    }
  }
  return out;
}

/** Vạch đầu tuần (thứ Hai) trên trục: nhãn "d/M"; khung hẹp ⇒ cách tuần. */
export function weekTicks(r: Range): { x: number; label: string; month: boolean }[] {
  const out: { x: number; label: string; month: boolean }[] = [];
  const base = dayNumber(r.start);
  const every = r.px < 16 ? 2 : 1;
  let n = 0;
  for (let i = 0; i < r.days; i++) {
    const dt = new Date((base + i) * DAY_MS);
    if (dt.getUTCDay() !== 1) continue;
    if (n++ % every) continue;
    out.push({ x: i * r.px, label: `${dt.getUTCDate()}/${dt.getUTCMonth() + 1}`, month: dt.getUTCDate() <= 7 });
  }
  return out;
}

/** Câu đọc cho trình đọc màn hình: một dòng việc nói đủ tên, người làm, ngày, trạng thái, cảnh báo. */
export function itemLabel(i: TimelineItem): string {
  return [i.title, i.who, i.kind === "milestone" ? `mốc ${i.dates}` : i.dates, i.status, i.flag].filter(Boolean).join(", ");
}
