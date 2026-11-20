import { describe, expect, it } from "vitest";
import { agendaDays, agendaHeader, boardFromRows } from "@/lib/board";
import { decodeTable } from "@/lib/decode";

const raw = {
  title: "Khách",
  kpis: [],
  columns: [{ key: "stage", label: "Chặng", format: "badge" }, { key: "customer", label: "Khách", format: "text" }],
  rows: [
    { id: "a", stage: "lead", customer: "An", next_follow_up: "2026-11-07" },
    { id: "b", stage: "prospect", customer: "Bình", next_follow_up: "2026-11-05" },
    { id: "c", stage: "lead", customer: "Chi", next_follow_up: null },
  ],
  empty: "",
  note: "",
  views: {
    board: { by: "stage", title: "customer", subtitle: "", columns: [{ key: "lead", title: "Lead" }, { key: "prospect", title: "Tiềm năng" }, { key: "lost", title: "Mất" }],
             move: { command: "crm.set_stage", id_key: "lead_id", column_key: "stage", form_columns: ["lost"] } },
    calendar: { date: "next_follow_up", title: "customer", subtitle: "" },
  },
};

describe("góc nhìn bảng (0087)", () => {
  it("Kanban dựng từ cùng các dòng, cột theo thứ tự server, cột rỗng vẫn hiện", () => {
    const t = decodeTable(raw);
    const cols = boardFromRows(t.rows, t.views!.board!, (r) => ({ query: "crm.journey", input: { lead_id: r.id } }));
    expect(cols.map((c) => [c.key, c.items.map((i) => i.title)])).toEqual([["lead", ["An", "Chi"]], ["prospect", ["Bình"]], ["lost", []]]);
    expect(cols[0].items[0].open).toEqual({ query: "crm.journey", input: { lead_id: "a" } });
    expect(t.views!.board!.move!.form_columns).toEqual(["lost"]);
  });

  it("Lịch xếp theo ngày tăng dần, dòng chưa có ngày để cuối", () => {
    const t = decodeTable(raw);
    expect(agendaDays(t.rows, t.views!.calendar!).map((d) => [d.day, d.rows.map((r) => r.id)])).toEqual([["2026-11-05", ["b"]], ["2026-11-07", ["a"]], ["", ["c"]]]);
  });

  it("lịch sự kiện (overdue=false): ngày đã qua không thành quá hạn; mặc định vẫn là hạn", () => {
    expect(decodeTable(raw).views!.calendar!.overdue).toBe(true);
    const events = decodeTable({ ...raw, views: { calendar: { date: "day", title: "title", subtitle: "", overdue: false } } });
    expect(events.views!.calendar!.overdue).toBe(false);
    expect(agendaHeader("2026-11-02", "2026-11-07", true)).toMatchObject({ tone: "bad" });
    expect(agendaHeader("2026-11-02", "2026-11-07", false)).toMatchObject({ tone: "muted" });
    expect(agendaHeader("2026-11-02", "2026-11-07", false).text).not.toContain("Quá hạn");
    expect(agendaHeader("2026-11-07", "2026-11-07", false).text.startsWith("Hôm nay")).toBe(true);
  });

  it("bảng không khai góc nhìn ⇒ views null", () => {
    expect(decodeTable({ ...raw, views: undefined }).views).toBeNull();
  });
});
