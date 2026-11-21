import { describe, expect, it } from "vitest";
import { stepDone } from "../chat";
import { decideStep, startDecide, type DecideState, type DecideStep } from "../decide";
import type { StreamEvent } from "../types";

/** Chạy cả luồng, gom các bước — như nơi gọi làm trong chat-context. */
function run(evs: StreamEvent[]): { steps: DecideStep[]; state: DecideState } {
  let state = startDecide();
  const steps: DecideStep[] = [];
  for (const ev of evs) {
    const r = decideStep(state, ev);
    state = r.state;
    steps.push(r);
  }
  return { steps, state };
}

describe("decide: dòng đầu quyết số phận của form", () => {
  it("rejected ⇒ form giữ, hiện lỗi, không chạy tiếp", () => {
    const { steps, state } = run([{ type: "rejected", card_id: "c", status: "invalid", message: "SĐT: sai định dạng" }]);
    expect(steps[0].outcome).toEqual({ status: "invalid", done: false, message: "SĐT: sai định dạng", replayed: false });
    expect(steps[0].startTurn).toBeUndefined();
    expect(state.phase).toBe("tail");
  });
  it("decided không chạy tiếp ⇒ thẻ xong; 'done' theo sau bị bỏ qua", () => {
    const { steps, state } = run([
      { type: "decided", card_id: "c", status: "ok", message: "Đã xong: Thêm khách Web thử A", replayed: false, resuming: false },
      { type: "done", thread_id: "T" },
    ]);
    expect(steps[0].outcome).toMatchObject({ status: "ok", done: true, message: "Đã xong: Thêm khách Web thử A" });
    expect(steps[0].startTurn).toBe(false);
    expect(steps[1]).toEqual({ state });
    expect(state.phase).toBe("tail");
  });
  it("decided không chạy tiếp mà máy chủ phát thẻ kết quả (link gửi khách) ⇒ vẫn trả thẻ để mở", () => {
    const linkCard = { id: "k", kind: "page", title: "Yêu cầu", query: "asks.request", input: {} };
    const { steps } = run([
      { type: "decided", card_id: "c", status: "ok", message: "Đã xong", replayed: false, resuming: false },
      { type: "card", card: linkCard as never },
    ]);
    expect(steps[1].card?.id).toBe("k");
  });

  it("bỏ đề xuất ⇒ dismissed, done", () => {
    const { steps } = run([{ type: "decided", card_id: "c", status: "dismissed", message: "Đã bỏ đề xuất", resuming: true }]);
    expect(steps[0].outcome).toMatchObject({ status: "dismissed", done: true });
    expect(steps[0].startTurn).toBe(true);
  });
  it("bấm lại (replayed) ⇒ vẫn done, đánh dấu replayed", () => {
    const { steps } = run([{ type: "decided", card_id: "c", status: "ok", message: "Đã xong", replayed: true }]);
    expect(steps[0].outcome).toMatchObject({ done: true, replayed: true });
  });
  it("error (không còn chờ / không phải của bạn) ⇒ form hiện lỗi", () => {
    const { steps } = run([{ type: "error", message: "Đề xuất này không còn chờ" }]);
    expect(steps[0].outcome).toEqual({ status: "error", done: false, message: "Đề xuất này không còn chờ", replayed: false });
  });
  it("sự kiện lạ trước kết quả ⇒ bỏ qua, vẫn chờ dòng đầu", () => {
    const { steps, state } = run([{ type: "ping" }]);
    expect(steps[0].outcome).toBeUndefined();
    expect(state.phase).toBe("first");
  });
});

describe("decide: phần chạy tiếp thành một lượt trả lời trong chat", () => {
  const card = { id: "p2", kind: "proposal", title: "Thêm việc", command: "flow.create_task", resumable: true, input: {}, fields: [] };
  const evs: StreamEvent[] = [
    { type: "decided", card_id: "p1", status: "ok", message: "Đã xong: Thêm khách", resuming: true },
    { type: "status", text: "Đang làm tiếp…" },
    { type: "step", n: 1, tool: "crm.create_lead", title: "Thêm khách", status: "ok", summary: "Đã xong: Thêm khách", card_id: "p1" },
    { type: "step", n: 2, tool: "flow.create_task", title: "Thêm việc", status: "running" },
    { type: "card", card: card as never },
    { type: "step", n: 2, tool: "flow.create_task", title: "Thêm việc", status: "proposed", card_id: "p2" },
    { type: "text", text: "Em đã tạo khách. " },
    { type: "text", text: "Em soạn sẵn việc gọi lại." },
    { type: "done", text: "Em đã tạo khách. Em soạn sẵn việc gọi lại.", thread_id: "T1", pending: true },
    { type: "rác sau cùng" },
  ];
  it("bước, thẻ (đề xuất mới — lại dừng), chữ, chốt bằng done", () => {
    const { steps, state } = run(evs);
    expect(steps[0].startTurn).toBe(true);
    const mid = steps[5].state.turn;
    expect(mid.status).toBe("Đang làm tiếp…");
    expect(mid.steps.map((s) => [s.n, s.status])).toEqual([[1, "ok"], [2, "proposed"]]);
    expect(steps[4].card?.id).toBe("p2");
    expect(steps[4].card?.resumable).toBe(true);
    expect(steps[8].reply).toBe("Em đã tạo khách. Em soạn sẵn việc gọi lại.");
    expect(steps[8].threadId).toBe("T1");
    expect(state.phase).toBe("tail");
    expect(state.turn.text).toBe("Em đã tạo khách. Em soạn sẵn việc gọi lại.");
    expect(steps[9].reply).toBeUndefined();
  });
  it("lỗi giữa lúc chạy tiếp ⇒ thành chữ trả lời (không phải kết quả của form)", () => {
    const { steps } = run([{ type: "decided", card_id: "p1", status: "ok", message: "x", resuming: true }, { type: "error", message: "Lỗi hệ thống" }]);
    expect(steps[1].outcome).toBeUndefined();
    expect(steps[1].reply).toBe("⚠︎ Lỗi hệ thống");
  });
  it("bước 'dismissed' tính là xong, không phải lỗi", () => {
    expect(stepDone({ n: 1, tool: "x", title: "x", status: "dismissed" })).toBe(true);
  });
});
