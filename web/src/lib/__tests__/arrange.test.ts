import { describe, expect, it } from "vitest";
import { indexAfter, layoutInput, layoutOf, move } from "@/lib/arrange";
import { decodeBlock } from "@/lib/decode";

const edit = (index: number) => ({ command: "apps.revise_block", title: "Sửa khối này", preset: { app_id: "A", page: "main", index } });
const chart = (index: number, w?: number) => decodeBlock({ type: "chart", kind: "bar", title: `B${index}`, labels: ["x"], series: [{ name: "s", values: [1] }], edit: edit(index), ...(w ? { w } : {}) });
const kpis = (...idx: number[]) => decodeBlock({ type: "kpis", items: idx.map((i) => ({ label: `K${i}`, value: i, format: "number", edit: edit(i) })) });

describe("sắp xếp dashboard (0175)", () => {
  it("dựng Ô từ khối có nút sửa của app; hàng ô số gộp ⇒ một Ô nhiều chỉ số gốc", () => {
    const l = layoutOf([kpis(1, 2), chart(3, 8), chart(4, 4)]);
    expect(l).not.toBeNull();
    expect(l!.app_id).toBe("A");
    expect(l!.tiles.map((t) => [t.indices, t.w])).toEqual([[[1, 2], 12], [[3], 8], [[4], 4]]);
  });

  it("thẻ không phải app (khối không có nút sửa của app) ⇒ không sắp xếp", () => {
    expect(layoutOf([decodeBlock({ type: "chart", kind: "bar", labels: ["x"], series: [{ name: "s", values: [1] }] })])).toBeNull();
  });

  it("đổi chỗ + đổi cỡ ⇒ đúng đầu vào apps.layout (cùng Ô ⇒ cùng bề rộng)", () => {
    const l = layoutOf([kpis(1, 2), chart(3, 8), chart(4, 4)])!;
    const tiles = move(l.tiles, 2, 0).map((t, i) => (i === 0 ? { ...t, w: 12 } : t));
    expect(layoutInput({ ...l, tiles })).toEqual({ app_id: "A", page: "main", order: [4, 1, 2, 3], widths: [12, 12, 12, 8] });
  });

  it("move giữ nguyên khi vị trí sai", () => {
    expect(move([1, 2, 3], 5, 0)).toEqual([1, 2, 3]);
    expect(move([1, 2, 3], 0, 2)).toEqual([2, 3, 1]);
  });
});


describe("giữ khối đang chọn sau khi kéo thả", () => {
  it("khối gốc thứ 3 chuyển lên đầu ⇒ vị trí mới 1; Ô ô số gộp đếm từng khối", () => {
    const t = (id: string, indices: number[]) => ({ id, title: id, kind: "chart:bar", indices, w: 6 });
    const tiles = [t("c", [3]), t("ab", [1, 2]), t("d", [4])];
    expect([indexAfter(tiles, 3), indexAfter(tiles, 1), indexAfter(tiles, 2), indexAfter(tiles, 4)]).toEqual([1, 2, 3, 4]);
    expect(indexAfter(tiles, 9)).toBe(9);
  });
});

describe("khối ô số nhiều mục", () => {
  it("một khối ô số 4 mục là MỘT khối gốc — thứ tự gửi lõi là hoán vị 1..n", () => {
    const four = decodeBlock({ type: "kpis", items: [1, 2, 3, 4].map((i) => ({ label: `Ô ${i}`, value: i, edit: edit(1) })) });
    const two = decodeBlock({ type: "kpis", items: [1, 2].map((i) => ({ label: `Mục ${i}`, value: i, edit: edit(2) })) });
    const table = decodeBlock({ type: "table", page: { title: "Bảng", columns: [], rows: [] }, edit: edit(3) });
    const l = layoutOf([four, two, table])!;
    expect(l.tiles.map((t) => t.indices)).toEqual([[1], [2], [3]]);
    expect(layoutInput(l).order).toEqual([1, 2, 3]);
  });
});
