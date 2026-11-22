// Đề xuất trợ lý soạn sẵn buổi sáng (quyết định 0074 A): thẻ cùng hình thẻ đề xuất trong chat (`source: "morning"`), Xác nhận / Bỏ qua
// POST /v1/assistant/morning/{id}/decide — server chạy bằng quyền NGƯỜI bấm và kiểm căn cứ. Hàm thuần: kết quả lệnh ⇒ thẻ xong hay giữ form.
import type { CommandResult } from "@/lib/types";

export type MorningOutcome = { status: string; done: boolean; message: string; approvalId?: string };

export function morningOutcome(r: CommandResult, decision: string): MorningOutcome {
  if (r.status === "dismissed" || (decision === "dismiss" && r.status === "ok")) return { status: "dismissed", done: true, message: "Đã bỏ đề xuất" };
  // thẻ đã KHOÁ ở trạng thái server nói (lỗi khi gọi ngoài / cần đối soát / vừa đối soát) ⇒ thẻ xong, không để form sửa-bấm-lại (rà 22/11)
  if (r.state && r.state !== "proposed") return { status: r.state, done: true, message: r.message || r.error || (r.state === "executed" ? "Đã làm" : "Thực hiện lỗi") };
  if (r.status === "ok") return { status: "ok", done: true, message: "Đã làm" };
  if (r.status === "pending_approval") return { status: "pending_approval", done: true, message: "Đã gửi duyệt", ...(r.approval_id ? { approvalId: r.approval_id } : {}) };
  return { status: r.status, done: false, message: r.error || "Không chạy được — thử lại hoặc sửa đề xuất" };
}
