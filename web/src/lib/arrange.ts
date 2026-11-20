import type { Block } from "./types";

// Sắp xếp dashboard (0175): từ thẻ app đang mở dựng danh sách Ô để kéo thả / đổi cỡ, rồi ra đúng đầu vào của lệnh `apps.layout`.
// Mỗi khối thẻ mang nút "Sửa khối này" (`apps.revise_block`, preset {app_id, page, index}) ⇒ biết khối gốc thứ mấy; hàng ô số gộp nhiều khối gốc
// (ô số liền nhau không có `w`) ⇒ một Ô mang nhiều chỉ số gốc, di chuyển / đổi cỡ cùng nhau.

export type Tile = { id: string; title: string; kind: string; indices: number[]; w: number };
export type Layout = { app_id: string; page: string; tiles: Tile[] };

const editOf = (x: { edit?: { command: string; preset: Record<string, unknown> } }) =>
  x.edit && x.edit.command === "apps.revise_block" ? x.edit.preset : null;

export function layoutOf(blocks: Block[]): Layout | null {
  let app = "", page = "";
  const tiles: Tile[] = [];
  for (const b of blocks) {
    const presets = b.type === "kpis" ? b.items.map((k) => editOf(k as never)).filter((p) => p !== null) : [editOf(b as never)].filter((p) => p !== null);
    if (!presets.length) return null;   // khối không thuộc app (hoặc không sửa được) ⇒ không sắp xếp được trang này
    app = String(presets[0]!.app_id ?? app);
    page = String(presets[0]!.page ?? page);
    // khối ô số nhiều mục: MỖI mục mang chỉ số của cùng một khối gốc ⇒ bỏ trùng (giữ thứ tự) — không thì apps.layout nhận thứ tự [1,1,1,…] và từ chối
    const indices = [...new Set(presets.map((p) => Number(p!.index)).filter((n) => Number.isInteger(n) && n > 0))];
    const title = b.type === "kpis" ? b.items.map((k) => k.label).join(" · ") : b.type === "chart" ? b.title : b.type === "table" ? b.page.title : b.type === "callout" ? b.title : b.type;
    tiles.push({ id: indices.join("-"), title: title || "Khối", kind: b.type === "chart" ? `chart:${b.kind}` : b.type, indices, w: b.w ?? 12 });
  }
  return tiles.length && app ? { app_id: app, page, tiles } : null;
}

/** Đầu vào `apps.layout`: thứ tự chỉ số gốc theo Ô mới + bề rộng từng khối gốc (cùng Ô ⇒ cùng bề rộng). */
export function layoutInput(l: Layout): { app_id: string; page: string; order: number[]; widths: number[] } {
  const order = l.tiles.flatMap((t) => t.indices);
  const widths = l.tiles.flatMap((t) => t.indices.map(() => t.w));
  return { app_id: l.app_id, page: l.page, order, widths };
}

/** Chuyển Ô ở vị trí `from` sang vị trí `to` (thả lên Ô đó). */
export function move<T>(list: T[], from: number, to: number): T[] {
  if (from === to || from < 0 || to < 0 || from >= list.length || to >= list.length) return list;
  const next = [...list];
  const [x] = next.splice(from, 1);
  next.splice(to, 0, x);
  return next;
}

/** Khối gốc thứ `index` (1…) nằm ở vị trí nào sau khi lưu thứ tự Ô mới — để giữ đúng khối đang chọn sau khi kéo thả. */
export function indexAfter(tiles: Tile[], index: number): number {
  const at = tiles.flatMap((t) => t.indices).indexOf(index);
  return at < 0 ? index : at + 1;
}
