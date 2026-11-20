import { describe, expect, it } from "vitest";
import { applyEvent, continueOf, asideCards, newTurn, resultCards, stepDone } from "../chat";
import { NdjsonParser } from "../ndjson";
import { isNearBottom } from "../scroll";
import type { ChatCard, StreamEvent } from "../types";

describe("ndjson", () => {
  it("ghép dòng bị cắt giữa các mảnh, bỏ dòng hỏng", () => {
    const p = new NdjsonParser();
    expect(p.push('{"type":"text","te')).toEqual([]);
    expect(p.push('xt":"xin"}\nrác\n{"type":"done"}\n{"type":"te')).toEqual([{ type: "text", text: "xin" }, { type: "done" }]);
    expect(p.flush()).toEqual([]);
  });
  it("UTF-8 bị cắt giữa ký tự vẫn đúng", () => {
    const bytes = new TextEncoder().encode('{"type":"text","text":"ạ"}\n');
    const p = new NdjsonParser();
    const out = [...p.push(bytes.slice(0, 22)), ...p.push(bytes.slice(22))];
    expect(out).toEqual([{ type: "text", text: "ạ" }]);
  });
});

describe("applyEvent", () => {
  const run = (evs: StreamEvent[]) => evs.reduce((a, ev) => ({ ...applyEvent(a.turn, ev), turn: applyEvent(a.turn, ev).turn }), { turn: newTurn() } as ReturnType<typeof applyEvent>);
  it("chữ nối dần, text_reset xoá", () => {
    const r = run([{ type: "text", text: "a" }, { type: "text", text: "b" }]);
    expect(r.turn.text).toBe("ab");
    expect(run([{ type: "text", text: "a" }, { type: "text_reset" }]).turn.text).toBe("");
  });
  it("bước cập nhật theo n, không nhân đôi", () => {
    const r = run([{ type: "step", n: 1, title: "A", status: "running" }, { type: "step", n: 1, title: "A", status: "ok", summary: "xong" }]);
    expect(r.turn.steps).toHaveLength(1);
    expect(r.turn.steps[0]).toMatchObject({ status: "ok", summary: "xong" });
    expect(stepDone(r.turn.steps[0])).toBe(true);
    expect(stepDone({ n: 2, tool: "", title: "", status: "running" })).toBeNull();
    expect(stepDone({ n: 3, tool: "", title: "", status: "denied" })).toBe(false);
    expect(stepDone({ n: 4, tool: "", title: "", status: "proposed" })).toBe(true);
  });
  it("thẻ được giải mã (khối lạ không vỡ) và báo cho nơi gọi", () => {
    const card = { id: "c1", kind: "page", title: "T", page: { kind: "card", title: "T", blocks: [{ type: "zzz" }] } } as unknown as ChatCard;
    const r = applyEvent(newTurn(), { type: "card", card });
    expect(r.card?.page?.kind === "card" && r.card.page.blocks[0].type).toBe("unknown");
  });
  it("done trả chữ chốt + thread_id; error có tiền tố", () => {
    expect(applyEvent({ ...newTurn(), text: "tạm" }, { type: "done", text: "cuối", thread_id: "T" })).toMatchObject({ reply: "cuối", threadId: "T" });
    expect(applyEvent(newTurn(), { type: "error", message: "hỏng" }).reply).toBe("⚠︎ hỏng");
    expect(applyEvent(newTurn(), { type: "sự kiện lạ" }).reply).toBeUndefined();
  });
  it("thẻ kết quả dưới câu trả lời: mọi đề xuất + thẻ cuối không rỗng", () => {
    const mk = (id: string, kind = "page", rows = 1) => ({ id, kind, title: id, page: { kind: "table", title: "", kpis: [], columns: [], rows: Array(rows).fill({ id: "x", values: {} }), empty: "", note: "" } }) as unknown as ChatCard;
    const r = resultCards([mk("a"), mk("p", "proposal"), mk("b"), mk("c", "page", 0)]);
    expect(r.map((c) => c.id)).toEqual(["p", "b"]);
  });
  it("gợi ý bên lề không lẫn vào thẻ kết quả, không thành 'thẻ cuối'", () => {
    const mk = (id: string, kind = "page", aside = false) => ({ id, kind, title: id, aside, page: { kind: "table", title: "", kpis: [], columns: [], rows: [{ id: "x", values: {} }], empty: "", note: "" } }) as unknown as ChatCard;
    const cards = [mk("a"), mk("m", "proposal", true)];
    expect(resultCards(cards).map((c) => c.id)).toEqual(["a"]);
    expect(asideCards(cards).map((c) => c.id)).toEqual(["m"]);
  });
});

describe("bám đáy", () => {
  it("sát đáy ⇒ bám; cuộn lên đọc ⇒ thôi", () => {
    expect(isNearBottom(920, 80, 1000)).toBe(true);
    expect(isNearBottom(500, 400, 1000)).toBe(false);
    expect(isNearBottom(0, 400, 400)).toBe(true);
  });
});

describe("chạm trần bước", () => {
  it("done mang limit ⇒ câu Tiếp tục do lõi soạn; thiếu / lạ ⇒ không có nút", () => {
    expect(applyEvent(newTurn(), { type: "done", text: "Em đã làm 50 bước…", limit: { steps: 50, continue: "Tiếp tục làm nốt phần còn dở." } }).cont).toBe("Tiếp tục làm nốt phần còn dở.");
    expect(applyEvent(newTurn(), { type: "done", text: "Xong." }).cont).toBeUndefined();
    expect(continueOf({ continue: 5 })).toBeUndefined();
    expect(continueOf(null)).toBeUndefined();
    expect(continueOf({ continue: "  Tiếp  " })).toBe("Tiếp");
  });
});
