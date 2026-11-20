import { describe, expect, it } from "vitest";
import { decodeBlock } from "@/lib/decode";
import { comboSeries, hasChartData, seriesUnit } from "@/lib/chart";
import { effectiveSpans, isGridRow, layoutRows } from "@/lib/layout";
import type { Block } from "@/lib/types";

const kpis = (w?: number) => decodeBlock({ type: "kpis", items: [], ...(w === undefined ? {} : { w }) });
const chart = (w?: number) => decodeBlock({ type: "chart", kind: "bar", labels: ["a"], series: [{ name: "s", values: [1] }], ...(w === undefined ? {} : { w }) });

describe("bề rộng khối `w` (0166)", () => {
  it("decode: số nguyên 1–12 giữ lại, còn lại coi như không khai (khối chiếm cả hàng như cũ)", () => {
    expect(kpis(4).w).toBe(4);
    for (const bad of [0, 13, 2.5, "6", null, -1]) expect(decodeBlock({ type: "kpis", items: [], w: bad }).w).toBeUndefined();
    expect(kpis().w).toBeUndefined();
  });

  it("khối liền nhau có `w` tổng ≤ 12 nằm cùng hàng; đủ 12 thì sang hàng mới", () => {
    const rows = layoutRows([chart(4), chart(4), chart(4), chart(6), chart(6)]);
    expect(rows.map((r) => r.cells.map((c) => c.index))).toEqual([[0, 1, 2], [3, 4]]);
    expect(rows.every(isGridRow)).toBe(true);
  });

  it("khối không có `w` chiếm cả hàng và ngắt hàng đang dồn dở", () => {
    const rows = layoutRows([chart(6), kpis(), chart(6), chart(6)]);
    expect(rows.map((r) => r.cells.map((c) => c.index))).toEqual([[0], [1], [2, 3]]);
    expect(isGridRow(rows[0])).toBe(false);
  });

  it("tổng vượt 12 ⇒ khối kế tiếp mở hàng mới, không ai bị cắt", () => {
    const rows = layoutRows([chart(8), chart(8), chart(4)]);
    expect(rows.map((r) => r.cells.map((c) => c.index))).toEqual([[0], [1, 2]]);
  });
});

describe("biểu đồ combo (0166)", () => {
  const combo = (kind = "combo"): Block => decodeBlock({
    type: "chart", kind, unit: "vnd", unit_right: "pct", labels: ["T1", "T2"],
    series: [{ name: "GMV", values: [10, 20], as: "bar" }, { name: "Tỉ lệ", values: [5, null], as: "line", axis: "right" }],
  });

  it("decode giữ as / axis / unit_right; chuỗi không khai ⇒ cột, trục trái", () => {
    const c = combo();
    if (c.type !== "chart") throw new Error("không phải chart");
    expect(c.kind).toBe("combo");
    expect(comboSeries(c).map((p) => [p.as, p.axis])).toEqual([["bar", "left"], ["line", "right"]]);
    expect(seriesUnit(c, c.series[1])).toBe("pct");
    expect(seriesUnit(c, c.series[0])).toBe("vnd");
    expect(hasChartData(c)).toBe(true);
    const plain = decodeBlock({ type: "chart", kind: "combo", labels: ["a"], series: [{ name: "x", values: [1] }] });
    if (plain.type !== "chart") throw new Error("không phải chart");
    expect(comboSeries(plain).map((p) => [p.as, p.axis])).toEqual([["bar", "left"]]);
  });

  it("giá trị lạ của as / axis bị bỏ, không vỡ", () => {
    const c = decodeBlock({ type: "chart", kind: "combo", labels: ["a"], series: [{ name: "x", values: [1], as: "pie", axis: "top" }] });
    if (c.type !== "chart") throw new Error("không phải chart");
    expect(c.series[0].as).toBeUndefined();
    expect(c.series[0].axis).toBeUndefined();
  });
});

describe("biểu đồ đứng một mình có `w`", () => {
  it("một hàng chỉ có một khối ⇒ không thành lưới (card.tsx vẽ như khối thường — không được bỏ rơi)", () => {
    const rows = layoutRows([kpis(), chart(12), kpis()]);
    expect(rows.map((r) => r.cells.length)).toEqual([1, 1, 1]);
    expect(rows.some(isGridRow)).toBe(false);
  });
});

describe("lọc chéo `pick` của biểu đồ (0173)", () => {
  const withPick = (pick: unknown) => decodeBlock({ type: "chart", kind: "bar", labels: ["A", "B"], series: [{ name: "s", values: [1, 2] }], pick });

  it("giữ param + values khi đủ độ dài nhãn", () => {
    const c = withPick({ param: "branch", values: ["a", "b"] });
    if (c.type !== "chart") throw new Error("không phải chart");
    expect(c.pick).toEqual({ param: "branch", values: ["a", "b"] });
  });

  it("thiếu param hoặc lệch độ dài ⇒ bỏ pick (biểu đồ vẫn vẽ, không bấm được)", () => {
    for (const bad of [{ values: ["a", "b"] }, { param: "branch", values: ["a"] }, { param: "", values: ["a", "b"] }, "x", null]) {
      const c = withPick(bad);
      if (c.type !== "chart") throw new Error("không phải chart");
      expect(c.pick).toBeUndefined();
    }
  });
});

describe("scatter / treemap (0173)", () => {
  it("scatter giữ hoành độ x của chuỗi; giá trị lạ bị bỏ thành null", () => {
    const c = decodeBlock({ type: "chart", kind: "scatter", labels: ["A", "B"], series: [{ name: "y", values: [1, 2], x: [10, "z"] }] });
    if (c.type !== "chart") throw new Error("không phải chart");
    expect(c.kind).toBe("scatter");
    expect(c.series[0].x).toEqual([10, null]);
  });
  it("treemap là một loại biểu đồ hợp lệ", () => {
    const c = decodeBlock({ type: "chart", kind: "treemap", labels: ["A"], series: [{ name: "v", values: [5] }] });
    if (c.type !== "chart") throw new Error("không phải chart");
    expect(c.kind).toBe("treemap");
  });
});

describe("effectiveSpans — số ô một hàng theo bề rộng CÓ SẴN của vùng", () => {
  it("đủ rộng thì giữ bố cục của máy chủ; chưa đo (0) cũng giữ", () => {
    expect(effectiveSpans([4, 4, 4], 1200)).toEqual([4, 4, 4]);   // mỗi ô 400px
    expect(effectiveSpans([4, 4, 4], 0)).toEqual([4, 4, 4]);
    expect(effectiveSpans([12], 200)).toEqual([12]);              // một ô: không đổi
  });
  it("ô nào dưới 360px ⇒ hai ô một hàng (ô lẻ cuối chiếm cả hàng); hẹp hơn nữa ⇒ mỗi ô một hàng", () => {
    expect(effectiveSpans([4, 4, 4], 900)).toEqual([6, 6, 12]);   // 300px mỗi ô → 2 ô (450px) + ô lẻ
    expect(effectiveSpans([8, 4], 1000)).toEqual([6, 6]);         // ô 4 cột chỉ 333px
    expect(effectiveSpans([3, 3, 3, 3], 1300)).toEqual([6, 6, 6, 6]);
    expect(effectiveSpans([6, 6], 700)).toEqual([12, 12]);        // 350px mỗi ô < 360
    expect(effectiveSpans([4, 4, 4], 420)).toEqual([12, 12, 12]);
  });
});
