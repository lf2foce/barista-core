import { describe, expect, it } from "vitest";
import * as f from "../format";

describe("format (kiểu Việt Nam)", () => {
  it("số có dấu chấm nghìn, phẩy thập phân", () => {
    expect(f.number(4500000000)).toBe("4.500.000.000");
    expect(f.number(2.345)).toBe("2,35");
  });
  it("tiền gọn: tỷ / triệu / đồng", () => {
    expect(f.compactMoney(4_500_000_000)).toBe("4,5 tỷ");
    expect(f.compactMoney(-1_250_000_000)).toBe("-1,25 tỷ");
    expect(f.compactMoney(12_400_000)).toBe("12,4 tr");
    expect(f.compactMoney(4_500_000)).toBe("4,5 tr");      // từng hiện "5 tr" (lệch 11%)
    expect(f.compactMoney(4_550_000)).toBe("4,55 tr");
    expect(f.compactMoney(455_400_000)).toBe("455 tr");
    expect(f.compactMoney(500_000)).toBe("500.000 đ");
  });
  it("ngày / giờ theo múi giờ Việt Nam", () => {
    expect(f.date("2026-10-30")).toBe("30/10/2026");
    expect(f.date("2026-10-30T10:00:00+07:00")).toBe("30/10/2026");
    expect(f.datetime("2026-10-31T10:05:14.904892+00:00")).toBe("31/10/2026 17:05");
    expect(f.datetime("2026-10-31T17:05:14+07:00")).toBe("31/10/2026 17:05");
    expect(f.datetime("không phải ngày")).toBe("không phải ngày");
  });
  it("SĐT: số cùng nước hiện nội địa liền, số nước khác đủ mã (ADR 0199)", () => {
    expect(f.phone("+84898654936")).toBe("0898654936");
    expect(f.phone("+842412345678")).toBe("02412345678");
    expect(f.phone("+14155552671")).toBe("+14155552671");
    expect(f.phone("+84898654936", null)).toBe("+84898654936");   // công ty nước chưa có luật nội địa
    expect(f.phone("+14155552671", { code: "1", trunk: "" })).toBe("4155552671");
    expect(f.phone("091•••")).toBe("091•••");   // số đã che / chữ tự do: nguyên văn
    expect(f.phone("0100000019")).toBe("0100000019");
    expect(f.phone("123")).toBe("123");
    expect(f.cell("+84898654936", "phone")).toBe("0898654936");
  });
  it("cell theo format", () => {
    expect(f.cell(1234567, "money")).toBe("1.234.567");
    expect(f.cell("12.5", "percent")).toBe("12,5%");
    expect(f.cell("lead", "badge", { lead: "Lead" })).toBe("Lead");
    expect(f.cell("x", "badge")).toBe("x");
    expect(f.cell(true, "text")).toBe("Có");
    expect(f.cell(null, "money")).toBe("");
  });
  it("KPI tiền dùng dạng gọn, rỗng là gạch ngang", () => {
    const k = { label: "a", value: 2_000_000_000, format: "money", tone: null, hint: "" };
    expect(f.kpi(k)).toBe("2 tỷ");
    expect(f.kpi({ ...k, value: null })).toBe("—");
    expect(f.kpi({ ...k, value: 17, format: "number" })).toBe("17");
  });
});

describe("chưa có gì thì ẩn (rà UI 23/11/2026)", () => {
  it("ô số 0 / trống bị bỏ, ô có số giữ nguyên thứ tự", () => {
    const k = (label: string, value: number | string | null) => ({ label, value });
    // 0 / "0" (đã đo, bằng 0) ẩn; null / "—" (CHƯA đo) vẫn hiện — không được biến mất lặng lẽ
    expect(f.shownKpis([k("a", 0), k("b", 3), k("c", null), k("d", "—"), k("e", "0"), k("g", 1.5)]).map((x) => x.label)).toEqual(["b", "c", "d", "g"]);
    expect(f.shownKpis([k("a", 0), k("b", 0)])).toEqual([]);
  });
  it("cột trống ở MỌI dòng bị ẩn; cột có số 0 vẫn giữ", () => {
    const cols = [{ key: "a" }, { key: "b" }, { key: "c" }];
    const rows = [{ values: { a: 1, b: null, c: 0 } }, { values: { a: 2, b: "—", c: 5 } }];
    expect(f.shownColumns(cols, rows).map((c) => c.key)).toEqual(["a", "c"]);
    expect(f.shownColumns(cols, []).length).toBe(3);
  });
  it("phụ đề từ tiêu đề server", () => {
    expect(f.titleTail("Sổ giao dịch · năm 2026", "Sổ giao dịch")).toBe("năm 2026");
    expect(f.titleTail("Sổ giao dịch", "Sổ giao dịch")).toBeNull();
    expect(f.titleTail("Xếp hạng · Giao dịch ký · 11/2026", "Xếp hạng")).toBe("Giao dịch ký · 11/2026");
  });
  it("nhãn server áp cho mọi kiểu cột; tông callout đỏ / cam không còn xám", () => {
    expect(f.cell("local", "text", { local: "Trong công ty" })).toBe("Trong công ty");
    expect(f.cardToneVar("red")).toBe("var(--bad)");
    expect(f.cardToneVar("orange")).toBe("var(--warn)");
  });
});
