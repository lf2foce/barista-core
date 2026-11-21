import { describe, expect, it } from "vitest";
import { decodeBrief, settledOf } from "../brief";
import { undoOfOutput } from "../autonomy";
import { decodeCard } from "../chat";
import type { ChatCard } from "../types";

const card = (o: Partial<ChatCard> = {}): ChatCard => ({ id: "c", kind: "proposal", title: "t", ...o });

describe("bản chạy thử chuẩn (ADR 0253)", () => {
  it("giải mã hồ sơ · cũ → mới · vì sao · đảo được; trường lạ / thiếu không làm vỡ", () => {
    const b = decodeBrief({
      title: "Dời hạn việc A", subject: { label: "Việc A", open: { query: "flow.task", input: { task_id: "1" } } },
      changes: [{ label: "Hạn", before: "1 tháng 12, 2026", after: "5 tháng 12, 2026" }, { nhãn: "rác" }],
      reason: "Khách hẹn lại", reversible: { ok: true, how: "Bấm Hoàn tác" },
    });
    expect(b?.subject?.open?.query).toBe("flow.task");
    expect(b?.changes).toEqual([{ label: "Hạn", before: "1 tháng 12, 2026", after: "5 tháng 12, 2026" }]);
    expect(b?.reversible).toEqual({ ok: true, how: "Bấm Hoàn tác" });
    expect(decodeBrief(null)).toBeNull();
    expect(decodeBrief({ title: "x" })?.reversible.ok).toBeNull();
  });
  it("thẻ mang bản chạy thử sau decodeCard", () => {
    expect(decodeCard(card({ brief: { title: "x", subject: null, changes: [], reason: "r", reversible: { ok: false, how: "" } } })).brief?.reason).toBe("r");
  });
  it("trạng thái đã quyết do SERVER nói; chữ vừa bấm chỉ lấp chỗ trống", () => {
    expect(settledOf(card())).toBeNull();
    expect(settledOf(card({ state: "proposed" }))).toBeNull();
    expect(settledOf(card({ state: "executed", state_label: "Đã xong: A" }))?.label).toBe("Đã xong: A");
    expect(settledOf(card({ state: "sent", approval_id: "p1" }))).toEqual({ label: "Đã gửi duyệt", tone: "ok", approvalId: "p1" });
    expect(settledOf(card({ state: "dismissed" }))?.tone).toBe("muted");
    expect(settledOf(card(), { message: "Đã gửi duyệt", approvalId: "p2" })?.approvalId).toBe("p2");
    expect(settledOf(card({ state: "executed" }), { message: "khác" })?.label).toBe("Đã xong");   // server thắng
  });
  it("Hoàn tác lấy từ kết quả lệnh nguyên văn", () => {
    expect(undoOfOutput({ undo: { command: "flow.adjust_task", input: { a: 1 }, title: "Hoàn tác: A" } })).toEqual({ command: "flow.adjust_task", input: { a: 1 }, title: "Hoàn tác: A" });
    expect(undoOfOutput({ undo: { command: "", input: {} } })).toBeNull();
    expect(undoOfOutput(null)).toBeNull();
  });
  it.each(["running", "failed", "needs_reconciliation"])("khôi phục thẻ %s từ server, giữ nguyên nhãn trạng thái", (state) => {
    expect(settledOf(decodeCard(card({ state, state_label: "Trạng thái từ server" })))).toEqual({ label: "Trạng thái từ server", tone: "muted" });
    expect(settledOf(card({ state: "proposed" }), { status: state, message: "Trạng thái từ lần bấm" })).toEqual({ label: "Trạng thái từ lần bấm", tone: "muted" });
  });
});
