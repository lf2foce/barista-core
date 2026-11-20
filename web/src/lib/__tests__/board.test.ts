import { describe, expect, it } from "vitest";
import { columnOf, formActionFor, movePayload, needsForm, visibleColumns } from "../board";
import type { BoardColumn, BoardMove } from "../types";

const move: BoardMove = { command: "flow.set_task", id_key: "task_id", column_key: "status", form_columns: ["blocked"] };
const cols: BoardColumn[] = [
  { key: "todo", title: "Cần làm", items: [{ id: "a", title: "A" }, { id: "b", title: "B" }] },
  { key: "doing", title: "Đang làm", items: [] },
  { key: "blocked", title: "Kẹt", items: [] },
];

describe("board", () => {
  it("payload kéo thẻ = {id_key: id, column_key: cột}", () => {
    expect(movePayload(move, "a", "doing")).toEqual({ task_id: "a", status: "doing" });
  });
  it("cột trong form_columns phải mở form, cột khác chạy luôn", () => {
    expect(needsForm(move, "blocked")).toBe(true);
    expect(needsForm(move, "doing")).toBe(false);
    expect(needsForm(null, "doing")).toBe(false);
  });
  it("form cho cột cần hỏi thêm: lệnh + preset điền sẵn", () => {
    const a = formActionFor(move, cols[0].items[0], cols[2]);
    expect(a.command).toBe("flow.set_task");
    expect(a.preset).toEqual({ task_id: "a", status: "blocked" });
    expect(a.title).toBe("A → Kẹt");
  });
  it("chuyển lạc quan: thẻ sang cột mới, các cột khác giữ nguyên", () => {
    const v = visibleColumns(cols, { a: "doing" });
    expect(v[0].items.map((i) => i.id)).toEqual(["b"]);
    expect(v[1].items.map((i) => i.id)).toEqual(["a"]);
    expect(columnOf(cols, { a: "doing" }, "a")).toBe("doing");
    expect(columnOf(cols, {}, "b")).toBe("todo");
  });
});
