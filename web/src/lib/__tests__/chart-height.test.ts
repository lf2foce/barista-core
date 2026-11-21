import { describe, expect, it } from "vitest";
import { chartBodyHeight, type ChartData } from "../chart";

const c = (kind: string, labels: number, series = 1): Pick<ChartData, "kind" | "labels" | "series"> => ({
  kind,
  labels: Array.from({ length: labels }, (_, i) => `n${i}`),
  series: Array.from({ length: series }, (_, i) => ({ name: `s${i}`, values: [] })),
});

describe("chartBodyHeight (chỗ giữ sẵn khi phần vẽ còn tải)", () => {
  it("cột / đường: 200px, thêm hàng chú giải khi nhiều chuỗi", () => {
    expect(chartBodyHeight(c("bar", 5))).toBe(200);
    expect(chartBodyHeight(c("line", 5, 2))).toBe(224);
    expect(chartBodyHeight(c("lạ", 3))).toBe(200);
  });
  it("thanh ngang / phễu: theo số nhãn như ChartContainer", () => {
    expect(chartBodyHeight(c("hbar", 4, 2))).toBe(4 * 34 * 2 + 8);
    expect(chartBodyHeight(c("funnel", 5))).toBe(5 * 40 + 8);
  });
  it("bánh / phân tán / ô vuông / kết hợp: cỡ cố định", () => {
    expect(chartBodyHeight(c("donut", 9))).toBe(168);
    expect(chartBodyHeight(c("scatter", 9))).toBe(240);
    expect(chartBodyHeight(c("treemap", 9))).toBe(260);
    expect(chartBodyHeight(c("combo", 6, 2))).toBe(244);
  });
});
