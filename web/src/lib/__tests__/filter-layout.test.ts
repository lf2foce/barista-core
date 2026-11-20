import { describe, expect, it } from "vitest";
import { chipText, cleared, isActive, splitFilters } from "@/lib/filter-layout";
import type { FieldSpec } from "@/lib/types";

const f = (key: string, extra: Partial<FieldSpec> = {}) => ({ key, label: key, kind: "text", required: false, help: "", hidden: false, ...extra }) as FieldSpec;
const keys = (fs: FieldSpec[]) => fs.map((x) => x.key);

describe("bày bộ lọc: ô chính ngoài, còn lại vào nút Bộ lọc (rà bố cục 18/11)", () => {
  it("≤ 3 ô không gấp ⇒ bày hết, không nút", () => {
    const r = splitFilters([f("a"), f("b"), f("c")]);
    expect(keys(r.inline)).toEqual(["a", "b", "c"]);
    expect(r.more).toEqual([]);
  });

  it("nhóm gấp luôn vào nút, kể cả màn ít ô", () => {
    const r = splitFilters([f("stage"), f("q"), f("team", { group: "Theo đội" })]);
    expect(keys(r.inline)).toEqual(["stage", "q"]);
    expect(keys(r.more)).toEqual(["team"]);
  });

  it("server có cờ primary ⇒ theo cờ; ô thuộc nhóm gấp không lên ngoài", () => {
    const r = splitFilters([f("from"), f("to"), f("q", { primary: true }), f("state", { primary: true }), f("x", { group: "G", primary: true })]);
    expect(keys(r.inline)).toEqual(["q", "state"]);
    expect(keys(r.more)).toEqual(["from", "to", "x"]);
  });

  it("server cũ (không cờ) ⇒ đoán 2 ô đầu không gấp; thứ tự giữ nguyên", () => {
    const r = splitFilters([f("g", { group: "G" }), f("a"), f("b"), f("c"), f("d")]);
    expect(keys(r.inline)).toEqual(["a", "b"]);
    expect(keys(r.more)).toEqual(["g", "c", "d"]);
  });

  it("đang lọc = khác mặc định; chọn 'Tất cả' khi mặc định có giá trị cũng tính", () => {
    expect(isActive(f("s", { default: "active" }), "active")).toBe(false);
    expect(isActive(f("s", { default: "active" }), "")).toBe(true);
    expect(isActive(f("p"), "")).toBe(false);
    expect(isActive(f("p"), undefined)).toBe(false);
    expect(isActive(f("p"), "VOP2")).toBe(true);
    expect(isActive(f("b", { kind: "bool" }), false)).toBe(false);
    expect(cleared(f("s", { default: "active" }))).toBe("active");
    expect(cleared(f("p"))).toBeUndefined();
  });

  it("chữ chip: nhãn lựa chọn, tiền gọn, bool chỉ nhãn", () => {
    expect(chipText(f("st", { label: "Trạng thái", options: [{ value: "sold", label: "Đã bán" }] }), "sold")).toBe("Trạng thái: Đã bán");
    expect(chipText(f("p", { label: "Giá tối đa", kind: "money" }), 4500000000)).toBe("Giá tối đa: 4,5 tỷ");
    expect(chipText(f("c", { label: "Hiện lịch đã huỷ", kind: "bool" }), true)).toBe("Hiện lịch đã huỷ");
    expect(chipText(f("s", { label: "Trạng thái", options: [{ value: "active", label: "Hiệu lực" }] }), "")).toBe("Trạng thái: Tất cả");
  });
});
