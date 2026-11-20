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
