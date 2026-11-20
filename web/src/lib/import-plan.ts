import type { ImportAnalysis, ImportDateInfo, ImportMatch, ImportPlan, ImportPlanReport } from "./types";

// Logic THUẦN của màn nhập Excel tự khớp (0226): server đoán, máy khách chỉ giữ lựa chọn của người rồi gửi lại. Không tự tính nghiệp vụ.

export const BLANK = "__blank__";

export type Mapping = Record<string, number | null>;

/** Ánh xạ ban đầu = đúng điều server đoán. */
export function initialMapping(a: ImportAnalysis): Mapping {
  const m: Mapping = {};
  for (const f of a.fields) m[f.key] = null;
  for (const x of a.mapping) m[x.field] = x.column;
  return m;
}

/** Chọn cột cho một trường. Cột đã thuộc trường khác thì trường kia bị bỏ trống (mỗi cột một trường). */
export function setColumn(m: Mapping, field: string, column: number | null): Mapping {
  const next: Mapping = { ...m };
  if (column !== null) for (const k of Object.keys(next)) if (k !== field && next[k] === column) next[k] = null;
  next[field] = column;
  return next;
}

export function missingRequired(a: ImportAnalysis, m: Mapping): string[] {
  return a.fields.filter((f) => f.required && m[f.key] == null).map((f) => f.key);
}

/** Người đã đổi ô này so với đoán của máy ⇒ không còn nhãn độ tin cậy của máy. */
export function matchOf(a: ImportAnalysis, m: Mapping, field: string): ImportMatch | null {
  const x = a.mapping.find((y) => y.field === field);
  return x && x.column === m[field] ? x : null;
}

export type Tone = "green" | "orange" | "purple" | "blue";

export function confidence(x: ImportMatch | null): { text: string; tone: Tone } {
  if (!x) return { text: "Bạn chọn", tone: "blue" };
  if (x.source === "saved") return { text: "Đã nhớ", tone: "green" };
  if (x.source === "ai") return { text: "Máy đoán", tone: "purple" };
  if (x.confidence >= 0.75) return { text: `Chắc ${Math.round(x.confidence * 100)}%`, tone: "green" };
  return { text: `Gợi ý ${Math.round(x.confidence * 100)}%`, tone: "orange" };
}

export function planOf(
  a: ImportAnalysis,
  m: Mapping,
  extra: { resolutions: ImportPlan["resolutions"]; dateOrders: Record<string, string>; overwrite: boolean; remember: boolean; preset?: Record<string, string> },
): ImportPlan {
  const mapping: Record<string, number> = {};
  // "__blank__" = người chọn "bỏ trống ô này" ⇒ server nhận chuỗi rỗng
  const resolutions: ImportPlan["resolutions"] = {};
  for (const [f, m] of Object.entries(extra.resolutions)) resolutions[f] = Object.fromEntries(Object.entries(m).map(([k, v]) => [k, v === BLANK ? "" : v]));
  for (const [k, v] of Object.entries(m)) if (v !== null && v !== undefined) mapping[k] = v;
  return { sheet: a.sheet, header_line: a.header_line, mapping, resolutions, date_orders: extra.dateOrders, overwrite: extra.overwrite, remember: extra.remember, ...(extra.preset && Object.keys(extra.preset).length ? { preset: extra.preset } : {}) };
}

/** Cách đọc ngày đang chọn cho mỗi cột ngày chữ (đã tính bởi server: dữ liệu > nhớ > vùng công ty); người bấm đổi thì ghi đè. */
export function dateOrders(infos: ImportDateInfo[], chosen: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const i of infos) out[i.field] = chosen[i.field] ?? i.order;
  return out;
}

export const DATE_WHY: Record<ImportDateInfo["decided"], string> = {
  data: "file có ngày không thể đọc kiểu khác",
  country: "cả cột mơ hồ — theo thói quen của vùng công ty",
  mixed: "cột lẫn hai kiểu — chọn kiểu nhiều hơn, kiểm lại",
  saved: "theo lần nhập trước",
  user: "bạn chọn",
};

export function summaryLine(s: ImportPlanReport["summary"]): string {
  const parts = [`${s.create} thêm mới`];
  if (s.update) parts.push(`${s.update} cập nhật`);
  if (s.skip) parts.push(`${s.skip} trùng — bỏ qua`);
  if (s.failed) parts.push(`${s.failed} lỗi`);
  return parts.join(" · ");
}

export function columnLabel(c: { letter: string; header: string; group: string }): string {
  return `${c.letter} · ${c.group ? `${c.group} / ` : ""}${c.header}`;
}

/** Chữ hiện trên nút nhập: số dòng sẽ vào (thêm mới + cập nhật). */
export function importButton(r: ImportPlanReport | null): string {
  if (!r) return "Nhập";
  const n = r.summary.create + r.summary.update;
  return n ? `Nhập ${n} dòng` : "Không có dòng mới để nhập";
}

/** Các giá trị cột lựa chọn từng hiện ra để chọn — gộp qua các lần xem trước (server chỉ báo cái CHƯA chọn; cái đã chọn vẫn phải đổi lại được). */
export type PickItem = { field: string; label: string; options: { value: string; label: string }[]; key: string; raw: string; count: number; suggest: string | null; suggestLabel: string | null };

export function mergePicks(seen: Record<string, PickItem>, groups: ImportPlanReport["unresolved"]): Record<string, PickItem> {
  const next = { ...seen };
  for (const g of groups)
    for (const v of g.values)
      next[`${g.field}|${v.key}`] = { field: g.field, label: g.label, options: g.options, key: v.key, raw: v.raw, count: v.count, suggest: v.suggest, suggestLabel: v.suggest_label };
  return next;
}

/** Gợi ý của máy chưa được áp: điền sẵn cho người xem lại / đổi. */
export function suggestionsToApply(
  groups: ImportPlanReport["unresolved"],
  resolutions: ImportPlan["resolutions"],
  done: Set<string>,
): ImportPlan["resolutions"] | null {
  const next: ImportPlan["resolutions"] = {};
  let any = false;
  for (const g of groups)
    for (const v of g.values) {
      const id = `${g.field}|${v.key}`;
      if (!v.suggest || done.has(id) || resolutions[g.field]?.[v.key]) continue;
      done.add(id);
      next[g.field] = { ...(next[g.field] ?? resolutions[g.field] ?? {}), [v.key]: v.suggest };
      any = true;
    }
  return any ? { ...resolutions, ...next } : null;
}
