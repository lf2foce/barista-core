import { text } from "./format";
import type { ActionSpec, FieldSpec, FormSpec, Input, Json, Manifest, Row, ScreenSpec } from "./types";

/** Nút dòng chỉ hiện khi dòng thoả `when` (trình bày thôi — server vẫn kiểm lại khi chạy). */
export function applies(action: ActionSpec, row: Row): boolean {
  return Object.entries(action.when).every(([key, allowed]) => allowed.some((v) => text(v) === text(row.values[key])));
}

/** Giá trị cố định của lệnh: `preset` + cột của dòng theo `bind`. */
export function fixedInput(action: ActionSpec, row: Row | null): Input {
  const out: Input = { ...action.preset };
  if (row) for (const [field, col] of Object.entries(action.bind)) out[field] = row.values[col] ?? null;
  return out;
}

/**
 * `prefill` (form "Sửa" một dòng): ô lấy giá trị từ cột của dòng nhưng VẪN HIỆN và SỬA ĐƯỢC — khác `bind` (cố định, ẩn). Mảng vào ô chọn nhiều.
 */
export function prefillInput(action: ActionSpec, row: Row | null): Input {
  const out: Input = {};
  if (!row) return out;
  for (const [field, col] of Object.entries(action.prefill ?? {})) {
    const v = row.values[col];
    if (v !== undefined && v !== null) out[field] = v;
  }
  return out;
}

/** Đầu vào kèm khi tải danh sách chọn của một ô (`options_with`): giá trị các trường đó trong form (thường là trường ẩn điền sẵn). Thiếu ⇒ bỏ. */
export function optionsInput(field: FieldSpec, values: Input): Record<string, string> {
  const out: Record<string, string> = {};
  for (const k of field.options_with ?? []) {
    const v = values[k];
    if (v !== undefined && v !== null && v !== "") out[k] = String(v);
  }
  return out;
}

/** Trường hiện cho người điền: không ẩn và không bị cố định. */
export function visibleFields(spec: FormSpec | null, fixed: Input): FieldSpec[] {
  return (spec?.fields ?? []).filter((f) => !f.hidden && !(f.key in fixed));
}

/**
 * Trường ẩn (kind "id"…) mà phần soạn sẵn đã điền — vd thẻ đề xuất của trợ lý mang `lead_id` trong `input`. Người dùng không thấy / không
 * sửa, nhưng lệnh cần ⇒ gửi kèm như giá trị cố định (nếu không, server báo "Lead: bắt buộc").
 */
export function hiddenInitial(spec: FormSpec | null, initial: Input): Input {
  const out: Input = {};
  for (const f of spec?.fields ?? []) if (f.hidden && initial[f.key] !== undefined && initial[f.key] !== null) out[f.key] = initial[f.key];
  return out;
}

/** Giá trị ban đầu: mặc định của form → điền sẵn từ dòng (`prefill`) → phần đã soạn sẵn (trợ lý / thẻ). */
export function initialValues(fields: FieldSpec[], initial: Input, prefill: Input = {}): Input {
  const values: Input = {};
  for (const f of fields) if (f.default !== undefined && f.default !== null) values[f.key] = f.default;
  for (const [k, v] of Object.entries(prefill)) if (v !== null) values[k] = v;
  for (const [k, v] of Object.entries(initial)) if (v !== null) values[k] = v;
  return values;
}

/** Ô nhập chữ → giá trị gửi lên: trống ⇒ bỏ; trường số ⇒ số; còn lại giữ nguyên chữ người gõ (server hiểu "4,5 tỷ", "05/11/2026"). */
export function coerce(field: FieldSpec, raw: string): Json | undefined {
  const t = raw.trim();
  if (t === "") return undefined;
  if (field.kind === "number") {
    const n = Number(t.replace(",", "."));
    return Number.isFinite(n) ? n : raw;
  }
  return raw;
}

/** Đầu vào gửi lệnh: chỉ các trường đang hiện + giá trị cố định. */
export function buildInput(values: Input, visible: FieldSpec[], fixed: Input): Input {
  const input: Input = {};
  const keys = new Set(visible.map((f) => f.key));
  for (const [k, v] of Object.entries(values)) if (keys.has(k) && v !== undefined) input[k] = v;
  for (const [k, v] of Object.entries(fixed)) input[k] = v;
  return input;
}

/** Thiếu trường bắt buộc nào (kiểm nhẹ phía người dùng; server vẫn là nơi quyết). */
export function missingRequired(visible: FieldSpec[], values: Input): FieldSpec[] {
  return visible.filter((f) => {
    if (!f.required || f.kind === "bool") return false;
    const v = values[f.key];
    return v === undefined || v === null || v === "" || (Array.isArray(v) && v.length === 0);
  });
}

/** Mở chi tiết từ một dòng: tham số lấy từ cột của dòng theo `bind`. */
/** Bấm MỘT lần vào dòng làm gì (`row_click` của màn): mở chi tiết, mở form của nút mặc định (khi dòng thoả `when` của nút), hoặc null = chỉ chọn dòng. */
export type RowClick = { kind: "open" } | { kind: "action"; action: ActionSpec } | null;
export function rowClick(spec: Pick<ScreenSpec, "row_click" | "open" | "row_actions">, row: Row): RowClick {
  if (!spec.row_click) return null;
  if (spec.row_click === "open") return spec.open ? { kind: "open" } : null;
  const action = spec.row_actions.find((a) => a.command === spec.row_click);
  return action && applies(action, row) ? { kind: "action", action } : null;
}

export function openInput(bind: Record<string, string>, row: Row): Input {
  const input: Input = {};
  for (const [field, col] of Object.entries(bind)) input[field] = row.values[col] ?? null;
  return input;
}

/**
 * Lệnh xong ⇒ thẻ kết quả nên mở (vd "Soạn bản tư vấn gửi khách" ⇒ papers.view). Ưu tiên `shows` của form nếu server công bố; chưa có ⇒ quy
 * ước theo khoá trong output (`paper_id` ⇒ giấy tờ vừa soạn).
 */
const RESULT_VIEW_BY_KEY: Record<string, string> = { paper_id: "papers.view" };
export function resultView(spec: Pick<FormSpec, "shows"> | null, output: unknown): { query: string; input: Input } | null {
  if (!output || typeof output !== "object" || Array.isArray(output)) return null;
  const out = output as Record<string, unknown>;
  const s = spec?.shows;
  const pair = Array.isArray(s) ? { query: s[0], key: s[1] } : s ?? null;
  if (pair && out[pair.key] !== undefined && out[pair.key] !== null) return { query: pair.query, input: { [pair.key]: String(out[pair.key]) } };
  for (const [key, query] of Object.entries(RESULT_VIEW_BY_KEY)) {
    if (typeof out[key] === "string" && out[key]) return { query, input: { [key]: out[key] as string } };
  }
  return null;
}

/**
 * Lệnh trả link cần MỞ ngay (vd tham gia cuộc gọi: `once_open_url`, khoá `once_*` không lưu ở đâu — 0064). Trình duyệt chặn cửa sổ mở sau một lần
 * chờ mạng (nhất là Safari trên điện thoại) ⇒ web hiện một liên kết thật để người bấm. Chỉ nhận https.
 */
export function openLink(output: unknown): { url: string; host: string } | null {
  if (!output || typeof output !== "object" || Array.isArray(output)) return null;
  const raw = (output as Record<string, unknown>).once_open_url;
  if (typeof raw !== "string") return null;
  try {
    const u = new URL(raw);
    return u.protocol === "https:" && u.hostname ? { url: u.href, host: u.hostname } : null;
  } catch {
    return null;
  }
}

/** Trang gọi trực tiếp (core 0114 bổ sung 07/11) — đường TƯƠNG ĐỐI trong chính web (`/goi/<mã>`); dạng khác ⇒ null. Ưu tiên hơn `once_open_url`
 *  (địa chỉ đầy đủ cho app Mac, có thể là http ở máy dev). */
export function callPage(output: unknown): string | null {
  if (!output || typeof output !== "object" || Array.isArray(output)) return null;
  const raw = (output as Record<string, unknown>).call_page;
  // `?auto=1` (vào ngay) và `&video=1` (hiện nút "Bật camera") do server đặt khi người vừa bấm Gọi / Tham gia một chạm (0114 ghi chú 07/11)
  return typeof raw === "string" && /^\/goi\/[0-9a-f-]{36}(\?auto=1(&video=1)?)?$/.test(raw) ? raw : null;
}

/** Tuỳ chọn của trang gọi theo địa chỉ server gửi: có `auto=1` ⇒ vào cuộc gọi ngay; có `video=1` ⇒ hiện nút "Bật camera" (camera không bao giờ tự bật; mặc định chỉ tiếng). */
export function callEntry(search: Record<string, string | string[] | undefined>): { auto: boolean; video: boolean } {
  const on = (v: string | string[] | undefined) => v === "1";
  return { auto: on(search.auto), video: on(search.auto) && on(search.video) };
}

/** Ô không khai `group` luôn hiện; ô cùng `group` (vd "Tuỳ chọn khác") gấp lại sau một nút — cùng khoá `group` server đã dùng cho bộ lọc của màn. Thứ tự giữ nguyên. */
export function splitGroups(fields: FieldSpec[]): { main: FieldSpec[]; groups: { name: string; fields: FieldSpec[] }[] } {
  const names = [...new Set(fields.flatMap((f) => (f.group ? [f.group] : [])))];
  return { main: fields.filter((f) => !f.group), groups: names.map((name) => ({ name, fields: fields.filter((f) => f.group === name) })) };
}

/** Cảnh báo sau khi lưu (vd kb.add_document: tài liệu tên gần giống đang hiệu lực) — người chọn có "Thay bằng bản mới" không. */
export type SavedWarning = { warning: string; similar: { id: string; title: string; ratio?: number }[] };
export function savedWarning(output: unknown): SavedWarning | null {
  if (!output || typeof output !== "object" || Array.isArray(output)) return null;
  const o = output as Record<string, unknown>;
  if (typeof o.warning !== "string" || !o.warning) return null;
  const similar = (Array.isArray(o.similar) ? o.similar : []).flatMap((x) => {
    const r = (x ?? {}) as Record<string, unknown>;
    return typeof r.id === "string" ? [{ id: r.id, title: typeof r.title === "string" ? r.title : r.id, ratio: typeof r.ratio === "number" ? r.ratio : undefined }] : [];
  });
  return { warning: o.warning, similar };
}

/** Bảng nhiều dòng (kind "rows"): giá trị là danh sách object — đọc an toàn (giá trị lạ ⇒ object rỗng). */
export function rowsOf(value: Json | undefined): Record<string, Json>[] {
  if (!Array.isArray(value)) return [];
  return value.map((r) => (r && typeof r === "object" && !Array.isArray(r) ? (r as Record<string, Json>) : {}));
}

/** Sửa một ô của một dòng — trống ⇒ bỏ khoá đó (server dùng mặc định), không đụng các dòng khác. */
export function setCell(rows: Record<string, Json>[], i: number, key: string, v: Json | undefined): Record<string, Json>[] {
  return rows.map((r, j) => {
    if (j !== i) return r;
    const next = { ...r };
    if (v === undefined) delete next[key];
    else next[key] = v;
    return next;
  });
}


/** Lệnh vừa đổi MENU (xoá app, đưa / gỡ dashboard…) ⇒ server trả `menu: true` (hợp đồng chỉ thêm, 0176): web tải lại manifest ngay, không để mục cũ trỏ
 *  vào thứ đã xoá đến khi người dùng tải lại trang. */
export function menuChanged(output: unknown): boolean {
  return !!output && typeof output === "object" && !Array.isArray(output) && (output as Record<string, unknown>).menu === true;
}

/** Màn có trong menu cũ mà menu mới không còn (app vừa xoá, dashboard vừa gỡ…) ⇒ tab đang mở của nó đóng theo. Chỉ màn TỪNG có trong menu cũ —
 *  màn mở từ chỗ khác (không thuộc menu) không bị đụng. */
export function droppedScreenKeys(prev: Manifest | null, next: Manifest): Set<string> {
  const keys = (m: Manifest) => new Set(m.workspaces.flatMap((w) => w.screens.map((s) => s.key)));
  if (!prev) return new Set();
  const now = keys(next);
  return new Set([...keys(prev)].filter((k) => !now.has(k)));
}
