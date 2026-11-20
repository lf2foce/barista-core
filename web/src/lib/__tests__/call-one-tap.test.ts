import { describe, expect, it } from "vitest";
import { callEntry, callPage, splitGroups, visibleFields } from "../form";
import type { FieldSpec } from "../types";

const ID = "0b9d6f0e-7a51-4b6c-9d0e-2b8f3c1d4e5f";
const f = (key: string, extra: Partial<FieldSpec> = {}): FieldSpec => ({ key, label: key, kind: "text", required: false, help: "", hidden: false, ...extra });

// Nút Gọi một chạm (0114 ghi chú 07/11): server đặt ?auto=1 / &video=1 trên trang gọi, ô hiếm cần gấp vào "Tuỳ chọn khác".
describe("gọi một chạm", () => {
  it("trang gọi nhận thêm ?auto=1 và &video=1 do server đặt, không nhận gì khác", () => {
    for (const ok of [`/goi/${ID}`, `/goi/${ID}?auto=1`, `/goi/${ID}?auto=1&video=1`]) expect(callPage({ call_page: ok })).toBe(ok);
    for (const bad of [`/goi/${ID}?video=1`, `/goi/${ID}?auto=1&x=1`, `/goi/${ID}?auto=1&video=1&y=2`, `/goi/${ID}#x`, `https://ac.vn/goi/${ID}?auto=1`]) {
      expect(callPage({ call_page: bad })).toBeNull();
    }
  });

  it("vào ngay chỉ khi auto=1; camera chỉ bật kèm auto=1 (mặc định chỉ tiếng)", () => {
    expect(callEntry({})).toEqual({ auto: false, video: false });
    expect(callEntry({ auto: "1" })).toEqual({ auto: true, video: false });
    expect(callEntry({ auto: "1", video: "1" })).toEqual({ auto: true, video: true });
    expect(callEntry({ video: "1" })).toEqual({ auto: false, video: false });
    expect(callEntry({ auto: ["1", "1"], video: "0" })).toEqual({ auto: false, video: false });
  });

  it("nút điền sẵn hết ⇒ không còn ô nào; ô cùng nhóm tách khỏi ô thường theo thứ tự", () => {
    const fields = [f("subject_kind", { hidden: true }), f("callee"), f("video", { kind: "bool" }), f("join_url", { group: "Tuỳ chọn khác" })];
    const spec = { command: "meet.ring", title: "Gọi", description: "", importable: false, fields };
    expect(visibleFields(spec, { subject_kind: "talk.direct", callee: "", video: false, join_url: "" })).toEqual([]);
    const picker = visibleFields(spec, { subject_kind: "talk.room" });
    expect(splitGroups(picker)).toEqual({ main: [fields[1], fields[2]], groups: [{ name: "Tuỳ chọn khác", fields: [fields[3]] }] });
  });
});
