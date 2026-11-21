import { describe, expect, it } from "vitest";
import { batchSummary, decodeItems, greetingOf, groupItems } from "../to-decide";

const raw = (o: Record<string, unknown>) => ({ kind: "proposal", id: "1", group: "proposal:flow.create_task", group_title: "Tạo việc", title: "Việc A", reason: "", batchable: true, brief: null, ...o });

describe("Chờ tôi duyệt (ADR 0253)", () => {
  it("giải mã từng việc, bỏ rác, giữ cờ gộp lô của server", () => {
    const items = decodeItems([raw({}), raw({ kind: "approval", id: "2", group: "approval:c:x", requester: "Mai", batchable: false }), { kind: "?", id: "3" }, null]);
    expect(items.map((x) => x.id)).toEqual(["1", "2"]);
    expect(items[1].requester).toBe("Mai");
    expect(items[1].batchable).toBe(false);
  });
  it("nhóm theo loại, giữ thứ tự; gộp lô khi cả nhóm gộp được", () => {
    const g = groupItems(decodeItems([raw({ id: "1" }), raw({ id: "2" }), raw({ id: "3", group: "proposal:acct.record_expense", batchable: false })]));
    expect(g.map((x) => [x.key, x.items.length, x.batchable])).toEqual([["proposal:flow.create_task", 2, true], ["proposal:acct.record_expense", 1, false]]);
  });
  it("dòng chào nói đúng việc: một việc ⇒ tên việc, nhiều ⇒ số theo loại", () => {
    expect(greetingOf(decodeItems([raw({ title: "Giữ chỗ GD-12" })]))).toContain("Giữ chỗ GD-12");
    expect(greetingOf(decodeItems([raw({ kind: "approval", requester: "Mai", title: "Đảo phiếu thu" })]))).toBe("Mai xin anh/chị duyệt: Đảo phiếu thu");
    expect(greetingOf(decodeItems([raw({ id: "1" }), raw({ id: "2" }), raw({ kind: "approval", id: "3" })]))).toBe("3 việc chờ anh/chị duyệt (2 đề xuất em soạn sẵn, 1 phiếu chờ duyệt) — xem danh sách");
  });
  it("tóm tắt lô: thẻ đã đổi căn cứ ở lại, nói vì sao", () => {
    expect(batchSummary([{ id: "1", status: "ok", message: "" }, { id: "2", status: "ok", message: "" }])).toEqual({ text: "Đã xong 2 việc", failed: false });
    const s = batchSummary([{ id: "1", status: "ok", message: "" }, { id: "2", status: "conflict", message: "Căn cứ đã đổi" }]);
    expect(s.failed).toBe(true);
    expect(s.text).toContain("còn 1 việc");
    expect(s.text).toContain("Căn cứ đã đổi");
  });
});
