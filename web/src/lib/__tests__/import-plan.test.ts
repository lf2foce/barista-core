import { describe, expect, it } from "vitest";
import { BLANK, confidence, mergePicks, suggestionsToApply, dateOrders, importButton, initialMapping, missingRequired, planOf, setColumn, summaryLine } from "../import-plan";
import type { ImportAnalysis, ImportMatch, ImportPlanReport } from "../types";

const analysis = (over: Partial<ImportAnalysis> = {}): ImportAnalysis => ({
  auto: true,
  command: "crm.create_lead",
  title: "Thêm khách",
  file_name: "f.xlsx",
  signature: "s",
  sheets: [],
  sheet: "Khách",
  header_line: 5,
  rows: 4,
  columns: [],
  fields: [
    { key: "name", label: "Họ tên", kind: "text", required: true, options: false },
    { key: "phone", label: "SĐT", kind: "phone", required: false, options: false },
    { key: "note", label: "Ghi chú", kind: "text", required: false, options: false },
  ],
  mapping: [
    { field: "name", column: 1, confidence: 0.9, source: "name", why: "" },
    { field: "phone", column: 2, confidence: 0.6, source: "value", why: "" },
  ],
  unmapped_required: [],
  skipped: [],
  skipped_count: 0,
  layout: null,
  resolutions: {},
  dates: [],
  locale: { country: "VN", timezone: "Asia/Ho_Chi_Minh", date_order: "dmy" },
  key: null,
  ai: { state: "none" },
  ...over,
});

describe("import-plan", () => {
  it("khởi tạo ánh xạ từ điều máy đoán, trường chưa có cột là null", () => {
    expect(initialMapping(analysis())).toEqual({ name: 1, phone: 2, note: null });
  });

  it("chọn một cột đang thuộc trường khác thì trường kia bị bỏ trống (mỗi cột một trường)", () => {
    const m = setColumn(initialMapping(analysis()), "note", 2);
    expect(m).toEqual({ name: 1, phone: null, note: 2 });
    expect(setColumn(m, "note", null)).toEqual({ name: 1, phone: null, note: null });
  });

  it("trường bắt buộc chưa có cột bị liệt kê", () => {
    const a = analysis();
    expect(missingRequired(a, { name: null, phone: 2, note: null })).toEqual(["name"]);
    expect(missingRequired(a, initialMapping(a))).toEqual([]);
  });

  it("nhãn độ tin cậy: đã nhớ · chắc · gợi ý · máy đoán · người chọn", () => {
    const m = (source: ImportMatch["source"], confidenceValue: number): ImportMatch => ({ field: "x", column: 0, confidence: confidenceValue, source, why: "" });
    expect(confidence(m("saved", 0.97)).text).toBe("Đã nhớ");
    expect(confidence(m("name", 1))).toEqual({ text: "Chắc 100%", tone: "green" });
    expect(confidence(m("value", 0.7))).toEqual({ text: "Gợi ý 70%", tone: "orange" });
    expect(confidence(m("ai", 0.7)).tone).toBe("purple");
    expect(confidence(null).text).toBe("Bạn chọn");
  });

  it("kế hoạch gửi lên chỉ có trường đã khớp; 'bỏ trống ô này' thành chuỗi rỗng", () => {
    const a = analysis();
    const p = planOf(a, { name: 1, phone: null, note: 3 }, { resolutions: { source: { facebook: "ads", zalo: BLANK } }, dateOrders: { birthday: "mdy" }, overwrite: true, remember: false });
    expect(p).toEqual({
      sheet: "Khách",
      header_line: 5,
      mapping: { name: 1, note: 3 },
      resolutions: { source: { facebook: "ads", zalo: "" } },
      date_orders: { birthday: "mdy" },
      overwrite: true,
      remember: false,
    });
  });

  it("lệnh nhập cả bảng: giá trị cấp lệnh (kế hoạch đích / tên kế hoạch mới) đi kèm kế hoạch gửi lên; không có thì không thêm trường", () => {
    const a = analysis();
    const base = { resolutions: {}, dateOrders: {}, overwrite: false, remember: true };
    expect(planOf(a, { name: 1 }, { ...base, preset: { plan_id: "p1" } }).preset).toEqual({ plan_id: "p1" });
    expect(planOf(a, { name: 1 }, { ...base, preset: { plan_title: "Ra mắt" } }).preset).toEqual({ plan_title: "Ra mắt" });
    expect("preset" in planOf(a, { name: 1 }, { ...base, preset: {} })).toBe(false);
  });

  it("cách đọc ngày: người bấm đổi thắng điều server quyết", () => {
    const info = { field: "birthday", label: "Sinh nhật", order: "dmy" as const, decided: "country" as const, text_dates: 2, sample: "03/04/1990", examples: {} };
    expect(dateOrders([info], {})).toEqual({ birthday: "dmy" });
    expect(dateOrders([info], { birthday: "mdy" })).toEqual({ birthday: "mdy" });
  });

  it("dòng tóm tắt + chữ nút nhập", () => {
    const r = { summary: { create: 3, update: 2, skip: 1, failed: 0, ignored: 2, waiting: 0 } } as ImportPlanReport;
    expect(summaryLine(r.summary)).toBe("3 thêm mới · 2 cập nhật · 1 trùng — bỏ qua");
    expect(importButton(r)).toBe("Nhập 5 dòng");
    expect(importButton({ summary: { create: 0, update: 0, skip: 4, failed: 0, ignored: 0, waiting: 0 } } as ImportPlanReport)).toBe("Không có dòng mới để nhập");
  });

  it("giá trị đã chọn vẫn còn trong danh sách chọn qua các lần xem trước; gợi ý của máy chỉ điền một lần", () => {
    const g = [{ field: "source", label: "Nguồn", options: [{ value: "ads", label: "Quảng cáo" }], values: [{ raw: "Quảng cáo ", key: "quangcao", count: 3, suggest: "ads", suggest_label: "Quảng cáo" }] }];
    const seen = mergePicks({}, g);
    expect(Object.keys(seen)).toEqual(["source|quangcao"]);
    expect(mergePicks(seen, [])).toEqual(seen); // lần sau server không báo nữa (đã chọn) — vẫn giữ
    const done = new Set<string>();
    expect(suggestionsToApply(g, {}, done)).toEqual({ source: { quangcao: "ads" } });
    expect(suggestionsToApply(g, {}, done)).toBeNull(); // không áp lại khi người đã đổi
    expect(suggestionsToApply(g, { source: { quangcao: "__blank__" } }, new Set())).toBeNull(); // người đã chọn thì không đè
  });
});
