import { describe, expect, it } from "vitest";
import { morningOutcome } from "../morning";

const r = (status: string, error = "") => ({ status, output: null, error, approval_id: null, replayed: false });

describe("đề xuất buổi sáng (0074 A)", () => {
  it("chạy được / gửi duyệt / bỏ ⇒ thẻ xong", () => {
    expect(morningOutcome(r("ok"), "confirm")).toMatchObject({ done: true, status: "ok" });
    expect(morningOutcome(r("pending_approval"), "confirm")).toMatchObject({ done: true, message: "Đã gửi duyệt" });
    expect(morningOutcome(r("dismissed"), "dismiss")).toMatchObject({ done: true, message: "Đã bỏ đề xuất" });
  });
  it("căn cứ đổi / sai dữ liệu ⇒ giữ form, nói lý do của server", () => {
    expect(morningOutcome(r("conflict", "Căn cứ đã đổi"), "confirm")).toEqual({ status: "conflict", done: false, message: "Căn cứ đã đổi" });
  });
  it("thẻ đã khoá ở server (lỗi khi gọi ngoài / cần đối soát / vừa đối soát) ⇒ thẻ xong, không để form bấm lại", () => {
    expect(morningOutcome({ ...r("failed", "Kênh từ chối"), state: "failed" }, "confirm")).toEqual({ status: "failed", done: true, message: "Kênh từ chối" });
    expect(morningOutcome({ ...r("needs_reconciliation", "mạng"), state: "needs_reconciliation" }, "confirm")).toMatchObject({ done: true, status: "needs_reconciliation" });
    expect(morningOutcome({ ...r("ok"), state: "executed", message: "Đã đối soát" }, "reconcile_done")).toEqual({ status: "executed", done: true, message: "Đã đối soát" });
  });
});
