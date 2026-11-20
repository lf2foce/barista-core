import { describe, expect, it } from "vitest";
import { applies, buildInput, coerce, droppedScreenKeys, fixedInput, hiddenInitial, initialValues, menuChanged, prefillInput, missingRequired, openInput, rowClick, rowsOf, setCell, visibleFields } from "../form";
import { newKey, nextKey } from "../idempotency";
import type { ActionSpec, FieldSpec, FormSpec, Json, Manifest } from "../types";

const f = (key: string, over: Partial<FieldSpec> = {}): FieldSpec => ({ key, label: key, kind: "text", required: false, help: "", hidden: false, ...over });
const action = (over: Partial<ActionSpec> = {}): ActionSpec => ({ command: "c", title: "t", style: "default", bind: {}, preset: {}, when: {}, importable: false, ...over });
const row = { id: "r1", values: { id: "r1", status: "reserved", code: "D-1" } };

describe("form", () => {
  it("when: nút dòng chỉ hiện khi cột thuộc giá trị cho phép", () => {
    expect(applies(action({ when: { status: ["reserved", "deposited"] } }), row)).toBe(true);
    expect(applies(action({ when: { status: ["signed"] } }), row)).toBe(false);
    expect(applies(action(), row)).toBe(true);
  });
  it("bind + preset thành giá trị cố định; bind cột thiếu ⇒ null", () => {
    expect(fixedInput(action({ bind: { code: "code", x: "missing" }, preset: { approve: true } }), row)).toEqual({ code: "D-1", x: null, approve: true });
    expect(fixedInput(action({ preset: { a: 1 } }), null)).toEqual({ a: 1 });
  });
  it("trường hiện = không ẩn và không bị cố định (kể cả cố định null)", () => {
    const spec: FormSpec = { command: "c", title: "t", description: "", importable: false, fields: [f("code"), f("note"), f("secret", { hidden: true }), f("x")] };
    expect(visibleFields(spec, { code: "D-1", x: null }).map((k) => k.key)).toEqual(["note"]);
  });
  it("giá trị đầu: mặc định của form rồi phần đã soạn; null không đè", () => {
    const fields = [f("kind", { default: "call" }), f("note", { default: "" })];
    expect(initialValues(fields, { note: "gọi lại", kind: null })).toEqual({ kind: "call", note: "gọi lại" });
  });
  it("coerce: trống bỏ; số ⇒ số; tiền / ngày giữ nguyên chữ người gõ", () => {
    expect(coerce(f("a"), "  ")).toBeUndefined();
    expect(coerce(f("n", { kind: "number" }), "2,5")).toBe(2.5);
    expect(coerce(f("m", { kind: "money" }), "4,5 tỷ")).toBe("4,5 tỷ");
    expect(coerce(f("d", { kind: "date" }), "05/11/2026")).toBe("05/11/2026");
  });
  it("buildInput: chỉ trường đang hiện + giá trị cố định (cố định thắng)", () => {
    const vis = [f("note")];
    expect(buildInput({ note: "x", hidden_leak: "no", lead_id: "evil" }, vis, { lead_id: "L1" })).toEqual({ note: "x", lead_id: "L1" });
  });
  it("thiếu trường bắt buộc", () => {
    const vis = [f("a", { required: true }), f("b", { required: true, kind: "bool" }), f("c", { required: true, kind: "multiselect" })];
    expect(missingRequired(vis, { c: [] }).map((x) => x.key)).toEqual(["a", "c"]);
  });
  it("trường ẩn đã điền sẵn (id của thẻ đề xuất) vẫn được gửi; không điền thì không bịa", () => {
    const spec: FormSpec = { command: "c", title: "t", description: "", importable: false, fields: [f("lead_id", { hidden: true, kind: "id" }), f("other", { hidden: true }), f("note")] };
    const fixed = hiddenInitial(spec, { lead_id: "L1", note: "x" });
    expect(fixed).toEqual({ lead_id: "L1" });
    expect(buildInput({ note: "x" }, visibleFields(spec, fixed), fixed)).toEqual({ note: "x", lead_id: "L1" });
  });
  it("prefill: điền sẵn từ dòng nhưng vẫn hiện + sửa được; bind vẫn cố định; thứ tự mặc định → prefill → trợ lý", () => {
    const shelfRow = { id: "finance", values: { key: "finance", name: "Kế toán", everyone: false, teams: ["KD-A"], roles: ["accountant", "director"], people: [] } };
    const a = action({ bind: { key: "key" }, prefill: { name: "name", everyone: "everyone", teams: "teams", roles: "roles", people: "people", missing: "nope" } });
    const spec: FormSpec = { command: "kb.save_shelf", title: "t", description: "", importable: false,
      fields: [f("key", { hidden: true }), f("name"), f("everyone", { kind: "bool", default: false }), f("teams", { kind: "multiselect", default: [] }), f("roles", { kind: "multiselect", default: [] }), f("people", { kind: "multiselect", default: [] })] };
    const fixed = fixedInput(a, shelfRow);
    expect(fixed).toEqual({ key: "finance" });
    expect(visibleFields(spec, fixed).map((x) => x.key)).toEqual(["name", "everyone", "teams", "roles", "people"]);
    const pre = prefillInput(a, shelfRow);
    expect(pre).toEqual({ name: "Kế toán", everyone: false, teams: ["KD-A"], roles: ["accountant", "director"], people: [] });
    expect(initialValues(spec.fields, {}, pre)).toMatchObject({ teams: ["KD-A"], roles: ["accountant", "director"], everyone: false, name: "Kế toán" });
    // giá trị trợ lý soạn thắng prefill
    expect(initialValues(spec.fields, { name: "Trợ lý đặt" }, pre).name).toBe("Trợ lý đặt");
    expect(prefillInput(a, null)).toEqual({});
    expect(prefillInput(action(), shelfRow)).toEqual({});
  });
  it("openInput lấy tham số từ cột của dòng", () => {
    expect(openInput({ lead_id: "id" }, row)).toEqual({ lead_id: "r1" });
  });
});

describe("khoá chống lặp", () => {
  it("khoá mới là uuid, khác nhau mỗi lần", () => {
    expect(newKey()).toMatch(/^[0-9a-f-]{36}$/);
    expect(newKey()).not.toBe(newKey());
  });
  it("đổi khoá khi conflict / invalid (sửa rồi gửi lại = yêu cầu mới), giữ khi lỗi khác", () => {
    expect(nextKey("k", "invalid")).not.toBe("k");
    expect(nextKey("k", "conflict")).not.toBe("k");
    expect(nextKey("k", "forbidden")).toBe("k");
    expect(nextKey("k", "ok")).toBe("k");
  });
});

import { headerFilename } from "../api";

describe("tên file cho header X-Filename", () => {
  it("mã hoá phần trăm (chỉ ASCII), giải mã lại đúng tên tiếng Việt", () => {
    const name = "Quy chế thưởng Đội A (bản 2).pdf";
    const h = headerFilename(name);
    expect(h).toMatch(/^[\x21-\x7e]+$/);
    expect(decodeURIComponent(h)).toBe(name);
    expect(headerFilename("báo cáo 📄.docx")).toBe("b%C3%A1o%20c%C3%A1o%20%F0%9F%93%84.docx");
    expect(headerFilename("")).toBe("file");
  });
});

describe("bảng nhiều dòng (rows)", () => {
  it("đọc an toàn: không phải danh sách ⇒ rỗng, dòng lạ ⇒ object rỗng", () => {
    expect(rowsOf(undefined)).toEqual([]);
    expect(rowsOf("x")).toEqual([]);
    expect(rowsOf([{ title: "A" }, "lạ", null])).toEqual([{ title: "A" }, {}, {}]);
  });
  it("sửa một ô không đụng dòng khác; ô trống ⇒ bỏ khoá", () => {
    const rows: Record<string, Json>[] = [{ title: "A", due_on: "01/12/2026" }, { title: "B" }];
    expect(setCell(rows, 1, "assignee_email", "mkt2@x.vn")).toEqual([{ title: "A", due_on: "01/12/2026" }, { title: "B", assignee_email: "mkt2@x.vn" }]);
    expect(setCell(rows, 0, "due_on", undefined)).toEqual([{ title: "A" }, { title: "B" }]);
    expect(rows[0]).toEqual({ title: "A", due_on: "01/12/2026" });
  });
});

describe("menu đổi sau lệnh (0176)", () => {
  const ws = (...keys: string[]) => ({ key: "w", title: "W", icon: "", screens: keys.map((key) => ({ key })) });
  const man = (...keys: string[]) => ({ workspaces: [ws(...keys)] }) as unknown as Manifest;
  it("chỉ `menu: true` mới tải lại manifest", () => {
    expect(menuChanged({ summary: "x", menu: true })).toBe(true);
    for (const o of [null, undefined, "menu", [true], { menu: "true" }, { summary: "x" }]) expect(menuChanged(o)).toBe(false);
  });
  it("màn rời menu ⇒ đóng tab; lần tải đầu (chưa có menu cũ) không đóng gì", () => {
    expect([...droppedScreenKeys(man("a", "app-1", "b"), man("a", "b"))]).toEqual(["app-1"]);
    expect(droppedScreenKeys(null, man("a")).size).toBe(0);
    expect(droppedScreenKeys(man("a"), man("a", "c")).size).toBe(0);
  });
});

describe("bấm một lần vào dòng (row_click)", () => {
  const row = { id: "1", values: { id: "1", open: "yes" } };
  const end = action({ command: "hr.end_employment", when: { open: ["yes"] } });
  const opener = { query: "hr.history", bind: { party_id: "id" }, title: "Lịch sử" };
  it("'open' mở chi tiết; tên lệnh mở đúng nút khi dòng thoả when; không khai ⇒ chỉ chọn", () => {
    expect(rowClick({ row_click: "open", open: opener, row_actions: [end] }, row)).toEqual({ kind: "open" });
    expect(rowClick({ row_click: "hr.end_employment", open: null, row_actions: [end] }, row)).toEqual({ kind: "action", action: end });
    expect(rowClick({ row_click: "hr.end_employment", open: null, row_actions: [end] }, { id: "2", values: { open: "no" } })).toBeNull();
    expect(rowClick({ open: opener, row_actions: [end] }, row)).toBeNull();
    expect(rowClick({ row_click: "open", open: null, row_actions: [] }, row)).toBeNull();   // server không gửi khi thiếu đích — phòng hờ
  });
});
