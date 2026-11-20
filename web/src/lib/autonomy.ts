// Tự chủ của trợ lý (0144) — phần thuần của nút chọn mức và nút Hoàn tác (kiểm thử được); nơi gọi lo trạng thái React.

/** Lời báo sau khi bấm một mức: chạy thẳng / thành phiếu chờ chủ tài khoản / lỗi. */
export function pickerOutcome(status: string, summary: string, error: string): { message: string; error: boolean } {
  if (status === "ok") return { message: summary || "Đã đổi", error: false };
  if (status === "pending_approval") return { message: "Đã gửi, chờ chủ tài khoản duyệt", error: false };
  return { message: error || `Không đổi được (${status})`, error: true };
}

export type UndoState = { phase: "idle" | "running" | "undone" | "failed"; message: string };

/** Kết quả bấm Hoàn tác: ok ⇒ "Đã hoàn tác"; chờ duyệt ⇒ báo; còn lại (conflict: việc đã được sửa sau đó, không có quyền…) ⇒ lỗi trên thẻ, kết quả
 *  gốc giữ nguyên (server không đè thay đổi mới). */
export function undoOutcome(status: string, error: string): UndoState {
  if (status === "ok") return { phase: "undone", message: "Đã hoàn tác" };
  if (status === "pending_approval") return { phase: "undone", message: "Đã gửi phiếu duyệt hoàn tác" };
  return { phase: "failed", message: error || `Không hoàn tác được (${status})` };
}
