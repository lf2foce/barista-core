import { describe, expect, it } from "vitest";
import { pickerOutcome, undoOutcome } from "../autonomy";
import { decodeCard, opensOnCanvas, resultCards } from "../chat";
import { decodePage } from "../decode";
import type { ChatCard } from "../types";

const card = (c: Partial<ChatCard>): ChatCard => ({ id: c.id ?? "x", kind: "page", title: "T", ...c });

describe("chọn mức tự chủ (0144)", () => {
  it("bảng mang khối chọn mức ở đầu; khối lạ trong đầu bảng bị bỏ", () => {
    const page = decodePage({
      kind: "table", title: "Trợ lý được làm gì", columns: [], rows: [],
      header: [
        { type: "picker", title: "Mức tự chủ của trợ lý", current: "", custom: "Tuỳ chỉnh", pending: "Cân bằng: chờ chủ tài khoản duyệt",
          items: [{ key: "balanced", title: "Cân bằng", text: "…", facts: ["Có Hoàn tác"], action: { command: "admin.set_agent_preset", preset: { preset: "balanced" } } },
                  { key: "", title: "hỏng" }] },
        { type: "lạ" },
      ],
    });
    expect(page.kind).toBe("table");
    if (page.kind !== "table") return;
    expect(page.header).toHaveLength(1);
    const b = page.header![0];
    expect(b.type).toBe("picker");
    if (b.type !== "picker") return;
    expect(b.items.map((i) => i.key)).toEqual(["balanced"]);
    expect(b.items[0].action?.preset).toEqual({ preset: "balanced" });
    expect(b.custom).toBe("Tuỳ chỉnh");
  });
  it("bảng cũ không có đầu bảng ⇒ không có trường", () => {
    const page = decodePage({ kind: "table", title: "x", columns: [], rows: [] });
    expect(page.kind === "table" && page.header).toBeFalsy();
  });
  it("lời báo: chạy thẳng, chờ chủ tài khoản, lỗi", () => {
    expect(pickerOutcome("ok", "Trợ lý: Thận trọng", "")).toEqual({ message: "Trợ lý: Thận trọng", error: false });
    expect(pickerOutcome("pending_approval", "", "").message).toContain("chờ chủ tài khoản duyệt");
    expect(pickerOutcome("denied", "", "Không có quyền").error).toBe(true);
  });
});

describe("thẻ trợ lý đã tự làm + đề xuất một chạm (0144)", () => {
  it("giải mã why / undo; undo thiếu lệnh ⇒ không có nút", () => {
    const c = decodeCard(card({ kind: "proposal", why: ["mặc định: việc ghi này cần người xác nhận", 3 as unknown as string, ""] }));
    expect(c.why).toEqual(["mặc định: việc ghi này cần người xác nhận"]);
    const r = decodeCard(card({ kind: "result", undo: { command: "flow.set_task", input: { task_id: "t", status: "cancelled" }, title: "Hoàn tác: huỷ việc" } }));
    expect(r.undo?.command).toBe("flow.set_task");
    expect(decodeCard(card({ kind: "result", undo: { command: "", input: {}, title: "" } })).undo).toBeNull();
  });
  it("thẻ đã tự làm và đề xuất luôn nằm dưới câu trả lời, không tự bung ra khung", () => {
    const cards = [card({ id: "a", kind: "result" }), card({ id: "b", page: null }), card({ id: "c", kind: "page" })];
    expect(resultCards(cards).map((c) => c.id)).toEqual(["a", "c"]);
    expect(opensOnCanvas(card({ kind: "result" }))).toBe(false);
    expect(opensOnCanvas(card({ kind: "proposal", resumable: true }))).toBe(false);
    expect(opensOnCanvas(card({ kind: "proposal" }))).toBe(true);   // thẻ cũ không quyết được trong chat ⇒ vẫn mở khung
  });
  it("Hoàn tác: ok ⇒ Đã hoàn tác; việc đã được sửa sau đó ⇒ lỗi giữ nguyên lời server", () => {
    expect(undoOutcome("ok", "")).toEqual({ phase: "undone", message: "Đã hoàn tác" });
    const conflict = undoOutcome("conflict", "Việc đã được sửa sau đó — không hoàn tác để khỏi đè thay đổi mới. Mở việc để xem.");
    expect(conflict.phase).toBe("failed");
    expect(conflict.message).toContain("đã được sửa sau đó");
    expect(undoOutcome("denied", "").message).toContain("denied");
  });
});
