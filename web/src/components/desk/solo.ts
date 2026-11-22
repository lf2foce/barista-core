import { screenItemId, useDesk } from "./desk-context";
import type { ScreenSpec, WorkspaceSpec } from "@/lib/types";

/** Phân hệ chỉ có MỘT màn ⇒ không cần cột danh sách màn: vào thẳng màn đó và dùng trọn khung làm việc. Suy từ manifest, không biết gói nào. */
export function useSolo(): { ws: WorkspaceSpec; screen: ScreenSpec; id: string } | null {
  const desk = useDesk();
  if (desk.section.kind !== "workspace") return null;
  const key = desk.section.key;
  const ws = desk.manifest?.workspaces.find((w) => w.key === key);
  return ws && ws.screens.length === 1 ? { ws, screen: ws.screens[0], id: screenItemId(ws.screens[0]) } : null;
}

/** Màn TỰ VẼ TRỌN khung (`full_frame`, vd "Soạn") của phân hệ đang chọn ĐANG MỞ ⇒ như phân hệ một màn: không cột danh sách màn, không đầu khung — màn tự vẽ
 *  ngăn của mình và mang menu sang các màn khác của phân hệ. Đọc cờ từ manifest HIỆN TẠI (không từ thẻ đã lưu trên khung — thẻ cũ không có cờ). */
export function useFullScreen(): { ws: WorkspaceSpec; screen: ScreenSpec; id: string } | null {
  const desk = useDesk();
  if (desk.section.kind !== "workspace") return null;
  const key = desk.section.key;
  const ws = desk.manifest?.workspaces.find((w) => w.key === key);
  const hero = desk.canvas.find((c) => c.id === desk.activeId);
  if (!ws || hero?.kind !== "screen") return null;
  const screen = ws.screens.find((s) => screenItemId(s) === hero.id);
  return screen?.full_frame ? { ws, screen, id: hero.id } : null;
}
