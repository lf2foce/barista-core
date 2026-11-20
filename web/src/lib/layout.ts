import type { Block } from "./types";

// Chia hàng cho thẻ (0166): khối có `w` (1–12 trên lưới 12 cột) mà LIỀN NHAU và tổng ≤ 12 nằm cùng một hàng; hàng đầy (đủ 12) thì sang hàng mới.
// Khối không có `w` chiếm cả hàng (ngắt hàng đang dồn dở). Hàm thuần: một hàng chỉ có MỘT khối thì không thành lưới — vẽ như khối thường.

export type Placed = { index: number; block: Block };
export type Row = { cells: Placed[] };

export const GRID_COLUMNS = 12;

export function layoutRows(blocks: Block[]): Row[] {
  const rows: Row[] = [];
  let used = 0;
  blocks.forEach((block, index) => {
    const w = block.w;
    const last = rows[rows.length - 1];
    if (w === undefined) {
      rows.push({ cells: [{ index, block }] });
      used = GRID_COLUMNS;   // khối cả hàng: khối có `w` kế tiếp mở hàng mới
      return;
    }
    if (last && used + w <= GRID_COLUMNS && last.cells.every((c) => c.block.w !== undefined)) {
      last.cells.push({ index, block });
      used += w;
    } else {
      rows.push({ cells: [{ index, block }] });
      used = w;
    }
  });
  return rows;
}

/** Hàng có từ hai khối trở lên mới cần lưới. */
export const isGridRow = (r: Row): boolean => r.cells.length > 1;

/** Bề rộng tối thiểu của MỘT ô để đọc được (nhãn trục không dính vào nhau) và bề rộng tối thiểu của vùng để chia hai ô một hàng. */
export const MIN_CELL = 360;
const TWO_UP = 2 * MIN_CELL + 24;   // hai ô + khoảng cách

/**
 * Bề rộng mỗi ô theo bề rộng CÓ SẴN của vùng chứa (không theo cửa sổ): mọi ô đủ ``MIN_CELL`` thì giữ bố cục của máy chủ (``w``); không thì hai ô một hàng (ô lẻ cuối
 * chiếm cả hàng), vùng còn hẹp hơn thì mỗi ô một hàng. ``width`` ≤ 0 (chưa đo) ⇒ giữ nguyên. Hàm thuần.
 */
export function effectiveSpans(ws: number[], width: number): number[] {
  if (width <= 0 || ws.length < 2) return ws;
  if (ws.every((w) => (width * w) / GRID_COLUMNS >= MIN_CELL)) return ws;
  if (width >= TWO_UP) return ws.map((_, i) => (ws.length % 2 === 1 && i === ws.length - 1 ? GRID_COLUMNS : GRID_COLUMNS / 2));
  return ws.map(() => GRID_COLUMNS);
}
