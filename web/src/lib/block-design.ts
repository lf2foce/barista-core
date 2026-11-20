import type { Json } from "./types";

// Bảng sửa khối kiểu BI (0220 §bảng sửa): server gửi định nghĩa khối + những gì chọn được (`apps.block_design`); web giữ BẢN NHÁP đúng hình `Block`
// của server, mỗi lần chọn chỉ đổi vài trường rồi đồng bộ `source.measures` / `source.by` với ô đang chọn. Server vẫn là người kiểm (xem trước =
// `apps.preview_block`, lưu = `apps.update_block` / `apps.add_block`) — ở đây chỉ ghép định nghĩa, không tính số, không suy quyền.

export type SourceDef = {
  query?: string;
  input?: Record<string, Json>;
  dataset?: string;
  sql?: string;
  measures?: string[];
  by?: string[];
  where?: Record<string, Json>;
  date?: string;
  period?: string;
};
export type BlockDef = {
  kind: string;
  title?: string;
  source: SourceDef;
  measure?: string;
  measure2?: string;
  by?: string;
  split?: string;
  chart?: string;
  where?: Record<string, Json[]>;
  w?: number | null;
  rows?: number;
  sort?: string;
  filter_param?: string;
  columns?: string[];
  [extra: string]: unknown;
};

export type Choice = { key: string; label: string };
export type DsDim = Choice & { values: Record<string, string>; month: boolean };
export type DsMeasure = Choice & { format: string; ratio: string };
export type Dataset = { key: string; title: string; dims: DsDim[]; measures: DsMeasure[]; dates: Choice[] };
export type Column = Choice & { format: string };
export type Param = Choice & { kind: string };
export type Design = {
  appId: string;
  page: string;
  index: number | null;
  blocks: number;
  maxBlocks: number;
  block: BlockDef | null;
  datasets: Dataset[];
  columns: Column[] | null;
  note: string;
  params: Param[];
  kinds: Choice[];
  charts: Choice[];
  periods: Choice[];
  maxRows: number;
};

const isObj = (x: unknown): x is Record<string, unknown> => typeof x === "object" && x !== null && !Array.isArray(x);
const str = (x: unknown, d = ""): string => (typeof x === "string" ? x : d);
const pairs = (x: unknown): Choice[] => (isObj(x) ? Object.entries(x).map(([key, v]) => ({ key, label: str(isObj(v) ? v.label : v, key) })) : []);

export function decodeDesign(raw: unknown): Design {
  const o = isObj(raw) ? raw : {};
  const datasets: Dataset[] = (Array.isArray(o.datasets) ? o.datasets : []).filter(isObj).map((d) => ({
    key: str(d.dataset),
    title: str(d.title, str(d.dataset)),
    dims: isObj(d.dims)
      ? Object.entries(d.dims).filter(([, v]) => isObj(v)).map(([key, v]) => {
          const x = v as Record<string, unknown>;
          const values = isObj(x.values) ? Object.fromEntries(Object.entries(x.values).map(([k, l]) => [k, str(l, k)])) : {};
          return { key, label: str(x.label, key), values, month: x.month === true };
        })
      : [],
    measures: isObj(d.measures)
      ? Object.entries(d.measures).filter(([, v]) => isObj(v)).map(([key, v]) => {
          const x = v as Record<string, unknown>;
          return { key, label: str(x.label, key), format: str(x.format, "number"), ratio: str(x.ratio) };
        })
      : [],
    dates: pairs(d.dates),
  }));
  const columns = Array.isArray(o.columns)
    ? o.columns.filter(isObj).map((c) => ({ key: str(c.key), label: str(c.label, str(c.key)), format: str(c.format, "text") }))
    : null;
  return {
    appId: str(o.app_id),
    page: str(o.page),
    index: typeof o.index === "number" ? o.index : null,
    blocks: typeof o.blocks === "number" ? o.blocks : 0,
    maxBlocks: typeof o.max_blocks === "number" ? o.max_blocks : 12,
    block: isObj(o.block) && isObj(o.block.source) ? (o.block as BlockDef) : null,
    datasets,
    columns,
    note: str(o.note),
    params: (Array.isArray(o.params) ? o.params : []).filter(isObj).map((p) => ({ key: str(p.key), label: str(p.label, str(p.key)), kind: str(p.kind) })),
    kinds: pairs(o.kinds),
    charts: pairs(o.charts),
    periods: pairs(o.periods),
    maxRows: typeof o.max_rows === "number" ? o.max_rows : 100,
  };
}

/** Hình đang chọn: ô số / bảng / loại biểu đồ (một ô chọn như khung Visualizations). Khối "ô số sẵn có của nguồn" không đổi hình được. */
export function visualOf(b: BlockDef): string {
  return b.kind === "chart" ? str(b.chart, "bar") : b.kind;
}

export const TWO_MEASURES = new Set(["combo", "scatter"]);
/** Biểu đồ tách chuỗi theo chiều thứ hai được (cột / thanh / đường / miền). */
export const SPLITTABLE = new Set(["bar", "hbar", "line", "area"]);
const TIME = new Set(["line", "area"]);

export function datasetOf(design: Design, b: BlockDef): Dataset | null {
  return design.datasets.find((d) => d.key === b.source.dataset) ?? null;
}

/** Khối mới: biểu đồ cột trên tập dữ liệu đầu tiên, chỉ số đầu, chiều đầu (không phải tháng). */
export function emptyBlock(design: Design): BlockDef | null {
  const ds = design.datasets[0];
  if (!ds || !ds.measures.length) return null;
  const by = (ds.dims.find((d) => !d.month) ?? ds.dims[0])?.key ?? "";
  return normalize({ kind: "chart", chart: "bar", title: "", measure: ds.measures[0].key, by, source: { dataset: ds.key } }, ds);
}

/** Đổi hình. Sang đường / miền ⇒ trục tháng nếu tập có; sang cột + đường / điểm rải ⇒ cần chỉ số thứ hai. */
export function setVisual(b: BlockDef, visual: string, ds: Dataset | null): BlockDef {
  let next: BlockDef = visual === "kpi" || visual === "table" ? { ...b, kind: visual } : { ...b, kind: "chart", chart: visual };
  if (b.kind === "table" && next.kind !== "table" && ds) {   // bảng nhiều chỉ số / chiều ⇒ hình mới lấy chỉ số + chiều đầu
    next = { ...next, measure: b.source.measures?.[0] ?? next.measure, by: b.source.by?.[0] ?? next.by, split: "" };
  }
  if (next.kind === "table" && b.kind !== "table" && ds) {
    next = { ...next, source: { ...next.source, measures: [b.measure, TWO_MEASURES.has(visualOf(b)) ? b.measure2 : ""].filter((x): x is string => !!x),
                                by: b.kind === "chart" ? [b.by, b.split].filter((x): x is string => !!x) : [] } };
  }
  if (ds && TIME.has(visual)) {
    const month = ds.dims.find((d) => d.month);
    if (month && next.by !== month.key) next = { ...next, by: month.key };
  }
  if (ds && TWO_MEASURES.has(visual) && !next.measure2) next = { ...next, measure2: ds.measures.find((m) => m.key !== next.measure)?.key ?? "" };
  return normalize(next, ds);
}

/** Đồng bộ phần nguồn với ô đang chọn — mỗi khối một chỗ khai: biểu đồ / ô số khai ở khối, bảng khai ở nguồn. Chỉ áp cho nguồn tập dữ liệu
 *  (nguồn danh sách: giá trị "count" / "sum:<cột>", trục là cột — server tự hiểu). */
export function normalize(b: BlockDef, ds: Dataset | null): BlockDef {
  if (!ds) return b;
  const first = ds.measures[0]?.key ?? "";
  const known = (k: string | undefined) => !!k && ds.measures.some((m) => m.key === k);
  const dim = (k: string | undefined) => !!k && ds.dims.some((d) => d.key === k);
  if (b.kind === "kpi") {
    const measure = known(b.measure) ? b.measure! : first;
    return { ...b, measure, measure2: "", by: "", split: "", filter_param: "", source: { ...b.source, measures: [measure], by: [] } };
  }
  if (b.kind === "chart") {
    const v = visualOf(b);
    const measure = known(b.measure) ? b.measure! : first;
    const measure2 = TWO_MEASURES.has(v) && known(b.measure2) && b.measure2 !== measure ? b.measure2! : "";
    const by = dim(b.by) ? b.by! : (ds.dims.find((d) => !d.month) ?? ds.dims[0])?.key ?? "";
    const split = SPLITTABLE.has(v) && dim(b.split) && b.split !== by ? b.split! : "";
    return { ...b, measure, measure2, by, split, sort: "", source: { ...b.source, measures: [measure, measure2].filter(Boolean), by: [by, split].filter(Boolean) } };
  }
  if (b.kind === "table") {
    const measures = (b.source.measures ?? []).filter(known);
    const by = (b.source.by ?? []).filter(dim).filter((k, i, a) => a.indexOf(k) === i).slice(0, 2);
    const cols = new Set([...by, ...(measures.length ? measures : [first])]);
    const sort = b.sort && cols.has(b.sort.replace(/^-/, "")) ? b.sort : "";
    return { ...b, measure: "count", measure2: "", by: "", split: "", columns: [], filter_param: "", sort,
             source: { ...b.source, measures: measures.length ? measures : [first], by } };
  }
  return b;
}

/** Đổi tập dữ liệu: giữ hình + tiêu đề, bỏ lọc / kỳ của tập cũ (chiều khác nhau), chọn lại chỉ số / chiều mặc định. */
export function setDataset(b: BlockDef, ds: Dataset): BlockDef {
  const fresh: BlockDef = { ...b, measure: "", measure2: "", by: "", split: "", sort: "", where: {}, source: { dataset: ds.key } };
  if (b.kind === "table") fresh.source = { dataset: ds.key, measures: [ds.measures[0]?.key ?? ""], by: [ds.dims[0]?.key ?? ""] };
  const out = normalize(fresh, ds);
  return b.kind === "chart" ? setVisual(out, visualOf(b), ds) : out;
}

/** Lọc của khối trên một chiều: danh sách giá trị cố định (ở khối) HOẶC gắn tham số của app (ở nguồn, "$khoá") — một chiều chỉ lọc một chỗ. */
export type Filter = { dim: string; values: Json[]; param: string };

export function filtersOf(b: BlockDef): Filter[] {
  const out: Filter[] = Object.entries(b.where ?? {}).map(([dim, values]) => ({ dim, values: Array.isArray(values) ? values : [values], param: "" }));
  for (const [dim, v] of Object.entries(b.source.where ?? {})) {
    if (typeof v === "string" && v.startsWith("$")) out.push({ dim, values: [], param: v.slice(1) });
    else out.push({ dim, values: Array.isArray(v) ? v : [v], param: "" });
  }
  return out;
}

export function setFilter(b: BlockDef, f: Filter): BlockDef {
  const where = { ...(b.where ?? {}) };
  const swhere = { ...(b.source.where ?? {}) };
  delete where[f.dim];
  delete swhere[f.dim];
  if (f.param) swhere[f.dim] = `$${f.param}`;
  else if (f.values.length) where[f.dim] = f.values;
  return { ...b, where, source: { ...b.source, where: swhere } };
}

export function removeFilter(b: BlockDef, dim: string): BlockDef {
  return setFilter(b, { dim, values: [], param: "" });
}

/** Lựa chọn giá trị cho khối dùng NGUỒN DANH SÁCH: số dòng + tổng từng cột số. */
export function listMeasures(columns: Column[]): Choice[] {
  const numeric = columns.filter((c) => ["money", "number", "int", "integer", "percent"].includes(c.format));
  return [{ key: "count", label: "Số dòng" }, ...numeric.map((c) => ({ key: `sum:${c.key}`, label: `Tổng ${c.label}` }))];
}

const lowerFirst = (s: string) => (s ? s[0].toLocaleLowerCase("vi") + s.slice(1) : s);

/** Tiêu đề gợi ý theo dữ liệu đang chọn: "<giá trị> theo <chiều>" — dùng cho tới khi người sửa tự gõ tiêu đề. */
export function autoTitle(b: BlockDef, design: Design): string {
  const ds = datasetOf(design, b);
  const measures: Choice[] = ds ? ds.measures : listMeasures(design.columns ?? []);
  const dims: Choice[] = ds ? ds.dims : (design.columns ?? []);
  const label = (list: Choice[], k: string | undefined) => list.find((x) => x.key === k)?.label ?? k ?? "";
  if (b.kind === "table") {
    const m = (b.source.measures ?? []).map((k) => label(measures, k)).join(", ");
    const by = (b.source.by ?? []).map((k) => lowerFirst(label(dims, k))).join(", ");
    return [m, by && `theo ${by}`].filter(Boolean).join(" ");
  }
  const m = label(measures, b.measure);
  if (b.kind !== "chart" || !b.by) return m;
  return `${m} theo ${lowerFirst(label(dims, b.by))}`;
}

/** Đổi phần DỮ LIỆU (nguồn, giá trị, trục, chia theo, ô số ↔ biểu đồ ↔ bảng) — khi đó tiêu đề tự gợi ý lại (đổi kiểu biểu đồ thôi thì giữ). */
export function dataChanged(a: BlockDef, b: BlockDef): boolean {
  const kindOf = (x: BlockDef) => (x.kind === "chart" ? "chart" : x.kind);
  return a.source.dataset !== b.source.dataset || a.source.query !== b.source.query || a.measure !== b.measure || a.by !== b.by
    || kindOf(a) !== kindOf(b) || JSON.stringify(a.source.measures ?? []) !== JSON.stringify(b.source.measures ?? [])
    || JSON.stringify(a.source.by ?? []) !== JSON.stringify(b.source.by ?? []);
}
