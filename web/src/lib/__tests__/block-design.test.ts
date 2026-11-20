import { describe, expect, it } from "vitest";
import { autoTitle, dataChanged, decodeDesign, emptyBlock, filtersOf, listMeasures, normalize, removeFilter, setDataset, setFilter, setVisual, type BlockDef } from "../block-design";

const raw = {
  app_id: "a1", page: "main", index: 2, blocks: 3, max_blocks: 12,
  block: { kind: "chart", chart: "bar", title: "Lead", measure: "count", measure2: "", by: "stage", split: "", source: { dataset: "crm.leads", measures: ["count"], by: ["stage"] }, w: 6 },
  datasets: [
    { dataset: "crm.leads", title: "Lead", dims: { stage: { label: "Chặng", values: { new: "Mới", deal: "Giao dịch" } }, team: { label: "Đội" }, month: { label: "Tháng về", month: true } },
      measures: { count: { label: "Số lead", format: "number" }, won: { label: "Thành", format: "number" }, win_rate: { label: "Tỷ lệ", format: "percent", ratio: "won / count" } },
      dates: { came_on: "Ngày về" } },
    { dataset: "mkt.spend", title: "Chi phí", dims: { channel: { label: "Kênh" } }, measures: { spend: { label: "Chi phí", format: "money" } }, dates: {} },
  ],
  columns: null, note: "", params: [{ key: "ky", label: "Kỳ", kind: "period" }, { key: "doi", label: "Đội", kind: "select" }],
  kinds: { kpi: "Ô số", table: "Bảng" }, charts: { bar: "Cột", line: "Đường", combo: "Cột + đường" }, periods: { this_month: "Tháng này" }, max_rows: 100,
};

describe("bảng sửa khối (0220)", () => {
  const d = decodeDesign(raw);
  const leads = d.datasets[0];
  const block = d.block!;

  it("giải mã danh mục: chiều có nhãn giá trị, tỷ lệ, cột ngày, tham số", () => {
    expect(d.index).toBe(2);
    expect(leads.dims.map((x) => x.key)).toEqual(["stage", "team", "month"]);
    expect(leads.dims[0].values.deal).toBe("Giao dịch");
    expect(leads.dims[2].month).toBe(true);
    expect(leads.measures[2].ratio).toBe("won / count");
    expect(leads.dates).toEqual([{ key: "came_on", label: "Ngày về" }]);
    expect(d.kinds.map((k) => k.key)).toEqual(["kpi", "table"]);
    expect(decodeDesign(null).datasets).toEqual([]);
  });

  it("biểu đồ: nguồn đồng bộ với giá trị + trục + tách", () => {
    const b = normalize({ ...block, split: "team" }, leads);
    expect(b.source.measures).toEqual(["count"]);
    expect(b.source.by).toEqual(["stage", "team"]);
    expect(normalize({ ...block, split: "stage" }, leads).split).toBe("");          // tách trùng trục ⇒ bỏ
    expect(normalize({ ...block, measure: "khong_co" }, leads).measure).toBe("count");
  });

  it("đổi hình: đường ⇒ trục tháng; cột + đường ⇒ có giá trị thứ hai; ô số ⇒ bỏ trục", () => {
    const line = setVisual(block, "line", leads);
    expect([line.kind, line.chart, line.by]).toEqual(["chart", "line", "month"]);
    const combo = setVisual(block, "combo", leads);
    expect(combo.measure2).toBe("won");
    expect(combo.source.measures).toEqual(["count", "won"]);
    const kpi = setVisual(combo, "kpi", leads);
    expect([kpi.kind, kpi.by, kpi.measure2]).toEqual(["kpi", "", ""]);
    expect(kpi.source).toMatchObject({ measures: ["count"], by: [] });
  });

  it("bảng: nhiều giá trị ở nguồn, chia ≤ 2 chiều, xếp chỉ theo cột có", () => {
    const t = setVisual({ ...block, split: "team" }, "table", leads);
    expect(t.kind).toBe("table");
    expect(t.source).toMatchObject({ measures: ["count"], by: ["stage", "team"] });
    const many = normalize({ ...t, sort: "-won", source: { ...t.source, measures: ["count", "won"], by: ["stage", "team", "month"] } }, leads);
    expect(many.source.by).toEqual(["stage", "team"]);
    expect(many.sort).toBe("-won");
    expect(normalize({ ...many, sort: "month" }, leads).sort).toBe("");
    const back = setVisual(many, "bar", leads);
    expect([back.measure, back.by]).toEqual(["count", "stage"]);
  });

  it("đổi nguồn: bỏ lọc / kỳ của nguồn cũ, giữ hình", () => {
    const b = setDataset({ ...block, where: { stage: ["deal"] }, source: { ...block.source, period: "$ky", date: "came_on" } }, d.datasets[1]);
    expect(b.source).toEqual({ dataset: "mkt.spend", measures: ["spend"], by: ["channel"] });
    expect([b.chart, b.where]).toEqual(["bar", {}]);
  });

  it("lọc: giá trị cố định ở khối, gắn tham số ở nguồn — một chiều một chỗ", () => {
    let b: BlockDef = setFilter(block, { dim: "stage", values: ["deal"], param: "" });
    expect(b.where).toEqual({ stage: ["deal"] });
    b = setFilter(b, { dim: "stage", values: [], param: "doi" });
    expect([b.where, b.source.where]).toEqual([{}, { stage: "$doi" }]);
    expect(filtersOf(b)).toEqual([{ dim: "stage", values: [], param: "doi" }]);
    expect(filtersOf(removeFilter(b, "stage"))).toEqual([]);
  });

  it("khối mới và nguồn danh sách", () => {
    const e = emptyBlock(d)!;
    expect([e.kind, e.chart, e.measure, e.by]).toEqual(["chart", "bar", "count", "stage"]);
    expect(listMeasures([{ key: "amount", label: "Giá trị", format: "money" }, { key: "name", label: "Tên", format: "text" }]))
      .toEqual([{ key: "count", label: "Số dòng" }, { key: "sum:amount", label: "Tổng Giá trị" }]);
  });

  it("tiêu đề gợi ý theo dữ liệu; đổi kiểu biểu đồ thôi thì không tính là đổi dữ liệu", () => {
    expect(autoTitle(block, d)).toBe("Số lead theo chặng");
    expect(autoTitle(setVisual(block, "kpi", leads), d)).toBe("Số lead");
    const t = setVisual({ ...block, split: "team" }, "table", leads);
    expect(autoTitle(t, d)).toBe("Số lead theo chặng, đội");
    expect(autoTitle(setDataset(block, d.datasets[1]), d)).toBe("Chi phí theo kênh");
    expect(dataChanged(block, setVisual(block, "hbar", leads))).toBe(false);
    expect(dataChanged(block, { ...block, measure: "won" })).toBe(true);
    expect(dataChanged(block, setVisual(block, "kpi", leads))).toBe(true);
  });
});
