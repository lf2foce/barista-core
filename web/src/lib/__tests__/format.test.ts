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
    expect(f.compactMoney(12_400_000)).toBe("12 tr");
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
