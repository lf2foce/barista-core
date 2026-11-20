import { describe, expect, it } from "vitest";
import { decodeBlock } from "../decode";
import { openLink } from "../form";

describe("gọi video (0114)", () => {
  it("khối trao đổi mang nút gói khác góp (Gọi); thiếu ⇒ mảng rỗng", () => {
    const b = decodeBlock({ type: "thread", subject_kind: "talk.room", subject_id: "r", messages: [],
      actions: [{ command: "meet.start", title: "Gọi", style: "primary", preset: { subject_kind: "talk.room", subject_id: "r" } }] });
    if (b.type !== "thread") throw new Error("không phải thread");
    expect(b.actions.map((a) => [a.command, a.title, a.preset.subject_id])).toEqual([["meet.start", "Gọi", "r"]]);
    const old = decodeBlock({ type: "thread", subject_kind: "crm.lead", subject_id: "9", messages: [] });
    expect(old.type === "thread" && old.actions).toEqual([]);
  });
  it("link cần mở: chỉ https có tên máy", () => {
    expect(openLink({ once_open_url: "https://meet.google.com/abc-defg-hij", summary: "x" })).toEqual({ url: "https://meet.google.com/abc-defg-hij", host: "meet.google.com" });
    for (const bad of ["javascript:alert(1)", "http://zoom.us/j/1", "không phải link", 5]) expect(openLink({ once_open_url: bad })).toBeNull();
    expect(openLink({ call_id: "c" })).toBeNull();
    expect(openLink(null)).toBeNull();
  });
});
