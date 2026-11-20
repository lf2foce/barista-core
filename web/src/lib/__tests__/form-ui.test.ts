import { describe, expect, it } from "vitest";
import { caretAfter, dependentsOf, filterOptions, fold, groupMoney, isShort, markMode, optionsArgs, optionsParams, pairFields, withRecents } from "../form-ui";
import { vnLong } from "../dates";
import type { FieldSpec, Option } from "../types";

const f = (key: string, over: Partial<FieldSpec> = {}): FieldSpec => ({ key, label: key, kind: "text", required: false, help: "", hidden: false, ...over });

describe("đánh dấu bắt buộc", () => {
  it("hầu hết bắt buộc ⇒ ô tuỳ chọn mới ghi chú; ít bắt buộc ⇒ dấu *; đủ cả / không ô nào ⇒ không ghi gì", () => {
    expect(markMode([f("a", { required: true }), f("b", { required: true }), f("c")])).toBe("optional");
    expect(markMode([f("a", { required: true }), f("b"), f("c"), f("d")])).toBe("star");
    expect(markMode([f("a", { required: true }), f("b", { required: true })])).toBe("none");
    expect(markMode([f("a"), f("b")])).toBe("none");
    expect(markMode([f("a", { required: true }), f("t", { kind: "bool" })])).toBe("none");   // công tắc không tính
  });
});

describe("bố cục hai cột", () => {
  it("hai ô ngắn liền nhau chung hàng; ô dài một mình; ô nhiều chữ trợ giúp không ghép", () => {
    const rows = pairFields([f("ten"), f("ngay", { kind: "date" }), f("gio", { kind: "time" }), f("ghi", { kind: "textarea" }), f("tien", { kind: "money" })]);
    expect(rows.map((r) => r.map((x) => x.key))).toEqual([["ten"], ["ngay", "gio"], ["ghi"], ["tien"]]);
    expect(isShort(f("tien", { kind: "money", help: "x".repeat(80) }))).toBe(false);
    expect(isShort(f("kieu", { kind: "select", options: [{ value: "a", label: "A" }] }))).toBe(true);
    expect(isShort(f("khach", { kind: "select", options_query: "crm.leads" }))).toBe(false);   // danh sách từ server ⇒ ô rộng
  });
});

describe("ô chọn có tìm", () => {
  const opts: Option[] = [
    { value: "a@x.vn", label: "Nguyễn Thị Lan", hint: "Trưởng nhóm" },
    { value: "b@x.vn", label: "Hải Phòng Land", hint: "Dự án" },
    { value: "c@x.vn", label: "Phạm Lan Anh", hint: "Kế toán" },
  ];
  it("bỏ dấu, nhiều từ, khớp cả dòng phụ", () => {
    expect(fold("Đặng Phạm")).toBe("dang pham");
    expect(filterOptions(opts, "pham lan").map((o) => o.value)).toEqual(["c@x.vn"]);
    expect(filterOptions(opts, "ke toan").map((o) => o.value)).toEqual(["c@x.vn"]);
    expect(filterOptions(opts, "")).toBe(opts);
    expect(filterOptions(opts, "zzz")).toEqual([]);
  });
  it("hạng: đầu nhãn → đầu từ → giữa từ", () => {
    expect(filterOptions(opts, "ng").map((o) => o.value)).toEqual(["a@x.vn", "b@x.vn"]);   // Nguyễn (đầu nhãn) trước "Phòng" (giữa từ); "Phạm Lan Anh" không có "ng"
    expect(filterOptions(opts, "an").map((o) => o.value)).toEqual(["b@x.vn", "c@x.vn", "a@x.vn"]);   // "án" / "Anh" đầu từ trước; "Lan" (an giữa từ) cuối
  });
  it("gần đây lên đầu, chỉ những dòng còn trong danh sách", () => {
    const { recent, rest } = withRecents(opts, [{ value: "c@x.vn", label: "Phạm Lan Anh" }, { value: "đã-xoá", label: "?" }]);
    expect(recent.map((o) => o.value)).toEqual(["c@x.vn"]);
    expect(rest.map((o) => o.value)).toEqual(["a@x.vn", "b@x.vn"]);
  });
});

describe("ô phụ thuộc", () => {
  const unit = f("unit", { options_query: "x.units", options_args: { project_code: "project" }, options_with: ["tenant_hint"] });
  const project = f("project", { options_query: "x.projects" });
  it("options_args đổi tên tham số; options_with giữ tên", () => {
    expect(optionsArgs(unit)).toEqual({ tenant_hint: "tenant_hint", project_code: "project" });
    expect(dependentsOf([project, unit], "project").map((x) => x.key)).toEqual(["unit"]);
    expect(dependentsOf([project, unit], "unit")).toEqual([]);
  });
  it("ô nguồn đang hiện mà chưa chọn ⇒ chờ; đã chọn ⇒ tham số đúng tên; ô nguồn ẩn trống ⇒ vẫn tải", () => {
    const vis = new Set(["project", "unit"]);
    expect(optionsParams(unit, {}, vis).waiting).toBe(true);
    expect(optionsParams(unit, { project: "VOP2" }, vis)).toEqual({ params: { project_code: "VOP2" }, waiting: false });
    expect(optionsParams(unit, {}, new Set(["unit"]))).toEqual({ params: {}, waiting: false });
  });
});

describe("tiền và ngày", () => {
  it("dấu chấm nghìn khi gõ số; có chữ / dấu phẩy ⇒ giữ nguyên", () => {
    expect(groupMoney("4500000")).toBe("4.500.000");
    expect(groupMoney("4.500.0000")).toBe("45.000.000");
    expect(groupMoney("4,5 tỷ")).toBe("4,5 tỷ");
    expect(groupMoney("300tr")).toBe("300tr");
    expect(groupMoney("")).toBe("");
    expect(groupMoney("007")).toBe("7");
  });
  it("con trỏ giữ sau cùng số chữ số", () => {
    expect(caretAfter("4.500.000", 4)).toBe(5);   // sau "4.500"
    expect(caretAfter("4.500.000", 0)).toBe(0);
    expect(caretAfter("12", 9)).toBe(2);
  });
  it("ngày không nhập nhằng", () => {
    expect(vnLong({ y: 2026, m: 12, d: 1 })).toBe("1 tháng 12, 2026");
  });
});
