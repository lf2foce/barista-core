import { describe, expect, it } from "vitest";
import { decodePage } from "../decode";

describe("bảng rỗng có việc nên làm (lịch)", () => {
  it("empty_actions đi theo bảng; thiếu thì không có", () => {
    const act = { command: "schedule.add_resource", title: "Thêm người / phòng", style: "primary", bind: {}, preset: {}, when: {}, importable: false };
    const p = decodePage({ kind: "table", title: "T", columns: [], rows: [], empty: "Chưa có người hay phòng nào.", empty_actions: [act] });
    expect(p.kind === "table" && p.empty_actions?.map((a) => a.command)).toEqual(["schedule.add_resource"]);
    const q = decodePage({ kind: "table", title: "T", columns: [], rows: [], empty: "x" });
    expect(q.kind === "table" && q.empty_actions).toBeUndefined();
  });
});
