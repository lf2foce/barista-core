import type { Manifest, WorkspaceSpec } from "./types";

// Thanh bên trái theo vai (ADR 0255): "Theo vai" + "Đã ghim" + nút "Tất cả mục". Server quyết phân hệ nào ở nhóm nào (đã lọc quyền, không lặp); phần thuần ở đây
// chỉ ráp manifest thành bố cục để vẽ — và giữ lời hứa "một mục hiện một lần" ngay cả khi manifest lệch (khoá lạ bị bỏ, khoá trùng chỉ giữ lần đầu).

export type RailPlace = "role" | "pinned" | null;
export type RailItem = { ws: WorkspaceSpec; /** đang hiện ở nhóm nào trên thanh bên (null: chỉ ở "Tất cả mục") */ place: RailPlace; /** người xem tự ghim phân hệ này */ pinned: boolean };
export type RailLayout = {
  /** false: server không gửi `rail` (vai không có mặc định) ⇒ thanh bên vẽ hết phân hệ như trước, không có nhóm và không có "Tất cả mục". */
  grouped: boolean;
  role: WorkspaceSpec[];
  pinned: WorkspaceSpec[];
  /** Mọi phân hệ người xem thấy, theo thứ tự menu — nội dung panel "Tất cả mục". */
  all: RailItem[];
};

export function railLayout(m: Manifest | null): RailLayout {
  const spaces = m?.workspaces ?? [];
  const byKey = new Map(spaces.map((w) => [w.key, w]));
  const taken = new Set<string>();
  const pick = (keys: string[] | undefined): WorkspaceSpec[] =>
    (keys ?? []).flatMap((k) => {
      const w = byKey.get(k);
      if (!w || taken.has(k)) return [];
      taken.add(k);
      return [w];
    });
  if (!m?.rail) return { grouped: false, role: [], pinned: [], all: spaces.map((ws) => ({ ws, place: null, pinned: ws.pinned === true })) };
  const role = pick(m.rail.role);
  const pinned = pick(m.rail.pinned);
  // mặc định của vai lọc hết (không còn phân hệ nào thấy được) ⇒ như chưa khai: vẽ hết, đừng để thanh bên trống
  if (role.length === 0) return { grouped: false, role: [], pinned: [], all: spaces.map((ws) => ({ ws, place: null, pinned: ws.pinned === true })) };
  const placeOf = (k: string): RailPlace => (role.some((w) => w.key === k) ? "role" : pinned.some((w) => w.key === k) ? "pinned" : null);
  return { grouped: true, role, pinned, all: spaces.map((ws) => ({ ws, place: placeOf(ws.key), pinned: ws.pinned === true })) };
}

/** Khoá phân hệ theo thứ tự hiện trên thanh bên (phím tắt Alt+số): vẽ hết ⇒ mọi phân hệ; có nhóm ⇒ "Theo vai" rồi "Đã ghim" (phần còn lại không có phím tắt). */
export function railWorkspaceKeys(m: Manifest | null): string[] {
  const l = railLayout(m);
  return l.grouped ? [...l.role, ...l.pinned].map((w) => w.key) : l.all.map((i) => i.ws.key);
}

/** Phân hệ đang mở mà thanh bên (rộng) không hiện — mở từ "Tất cả mục": vẫn có một mục sáng để biết mình đang ở đâu. `onRail`: nó đã có ở nhóm nào đó (màn hẹp
 *  chỉ hiện phần cốt lõi nên vẫn cần mục này). Không có nhóm (vẽ hết) hoặc đang ở mục cốt lõi ⇒ null. */
export function railCurrent(layout: RailLayout, workspaceKey: string | null): { ws: WorkspaceSpec; onRail: boolean } | null {
  if (!layout.grouped || !workspaceKey) return null;
  const hit = layout.all.find((i) => i.ws.key === workspaceKey);
  return hit ? { ws: hit.ws, onRail: hit.place !== null } : null;
}
