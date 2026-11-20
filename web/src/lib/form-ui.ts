import { text } from "./format";
import type { FieldSpec, Input, Json, Option } from "./types";

/**
 * Logic THUẦN của lớp vẽ form (agent form D, 18/11/2026): đánh dấu bắt buộc, bố cục hai cột, ô chọn có tìm (lọc / nhớ lựa chọn gần nhất), ô phụ thuộc,
 * tiền có dấu chấm nghìn. Không tự tính nghiệp vụ — chỉ trình bày giá trị người đang gõ; server vẫn là nơi kiểm.
 */

export const isBlank = (v: Json | undefined): boolean => v === undefined || v === null || (typeof v === "string" && !v.trim()) || (Array.isArray(v) && !v.length);

// ── bắt buộc ──

/**
 * Cách đánh dấu: hầu hết ô bắt buộc ⇒ không đánh dấu ô nào, ô TUỲ CHỌN ghi "không bắt buộc"; ít ô bắt buộc ⇒ dấu * ở ô bắt buộc; đủ cả hoặc không ô nào
 * bắt buộc ⇒ không ghi gì (khỏi lặp "Bắt buộc" ở từng ô).
 */
export function markMode(visible: FieldSpec[]): "none" | "star" | "optional" {
  const all = visible.filter((f) => f.kind !== "bool" && f.kind !== "rows");
  const req = all.filter((f) => f.required).length;
  if (!req || req === all.length) return "none";
  return req * 2 > all.length ? "optional" : "star";
}

// ── bố cục ──

const SHORT_KINDS = new Set(["date", "time", "month", "money", "number", "percent", "phone"]);

/** Ô ngắn (ngày, giờ, tiền, số, SĐT, lựa chọn cố định ít dòng): hai ô ngắn liền nhau xếp một hàng hai cột. */
export function isShort(f: FieldSpec): boolean {
  if (f.help.length > 56) return false;
  if (SHORT_KINDS.has(f.kind)) return true;
  return f.kind === "select" && !f.options_query && !!f.options && f.options.length > 0 && f.options.length <= 6;
}

/** Xếp ô thành hàng: [a, b] khi hai ô ngắn liền nhau, còn lại [a]. Giữ nguyên thứ tự server khai. */
export function pairFields(fields: FieldSpec[]): FieldSpec[][] {
  const out: FieldSpec[][] = [];
  for (let i = 0; i < fields.length; i++) {
    const a = fields[i];
    const b = fields[i + 1];
    if (b && isShort(a) && isShort(b)) {
      out.push([a, b]);
      i++;
    } else out.push([a]);
  }
  return out;
}

// ── ô chọn có tìm ──

/** Bỏ dấu, thường — khớp "pham" với "Phạm". */
export const fold = (s: string): string => s.normalize("NFD").replace(/đ/g, "d").replace(/Đ/g, "D").replace(/[̀-ͯ]/g, "").toLowerCase();

/**
 * Lọc danh sách đã có theo chữ gõ: mọi từ có trong nhãn / dòng phụ / giá trị. Xếp hạng: nhãn bắt đầu bằng cả cụm gõ → mọi từ khớp ĐẦU TỪ ("ng" → "Ngoại",
 * không phải "Hải Phòng") → khớp giữa từ; cùng hạng giữ thứ tự gốc. Trống ⇒ nguyên danh sách.
 */
export function filterOptions(opts: Option[], q: string): Option[] {
  const words = fold(q).split(/\s+/).filter(Boolean);
  if (!words.length) return opts;
  const first = fold(q.trim());
  const ranked: [number, Option][] = [];
  for (const o of opts) {
    const hay = fold(`${o.label} ${o.hint ?? ""} ${o.value}`);
    if (!words.every((w) => hay.includes(w))) continue;
    const label = fold(o.label);
    const rank = label.startsWith(first) ? 0 : words.every((w) => hay.startsWith(w) || hay.includes(` ${w}`) || hay.includes(`-${w}`)) ? 1 : 2;
    ranked.push([rank, o]);
  }
  return ranked.map((r, i) => [r[0], i, r[1]] as const).sort((x, y) => x[0] - y[0] || x[1] - y[1]).map((r) => r[2]);
}

const RECENT_MAX = 5;
const recentKey = (query: string) => `barista:recent-options:${query}`;

/** Lựa chọn gần nhất của một truy vấn chọn (máy này, trình duyệt này) — mới nhất trước. Không có / bị chặn bộ nhớ ⇒ rỗng. */
export function recentOptions(query: string): Option[] {
  try {
    const raw = JSON.parse(localStorage.getItem(recentKey(query)) ?? "[]") as unknown;
    return Array.isArray(raw) ? raw.filter((o): o is Option => !!o && typeof (o as Option).value === "string" && typeof (o as Option).label === "string").slice(0, RECENT_MAX) : [];
  } catch {
    return [];
  }
}

export function rememberOption(query: string, o: Option): void {
  try {
    const next = [o, ...recentOptions(query).filter((x) => x.value !== o.value)].slice(0, RECENT_MAX);
    localStorage.setItem(recentKey(query), JSON.stringify(next));
  } catch {
    /* bộ nhớ bị chặn: bỏ qua — chỉ mất phần "gần đây" */
  }
}

/** Danh sách hiện khi chưa gõ gì: nhóm "Gần đây" (chỉ những dòng còn trong danh sách) lên đầu, phần còn lại giữ thứ tự gốc. */
export function withRecents(opts: Option[], recents: Option[]): { recent: Option[]; rest: Option[] } {
  const have = new Map(opts.map((o) => [o.value, o]));
  const recent = recents.map((r) => have.get(r.value)).filter((o): o is Option => !!o);
  const ids = new Set(recent.map((o) => o.value));
  return { recent, rest: opts.filter((o) => !ids.has(o.value)) };
}

// ── ô phụ thuộc ──

/** Tham số truy vấn chọn → khoá trường của form: `options_with` (cùng tên) hợp với `options_args` (đổi tên). */
export function optionsArgs(f: FieldSpec): Record<string, string> {
  return { ...Object.fromEntries((f.options_with ?? []).map((k) => [k, k])), ...(f.options_args ?? {}) };
}

/**
 * Tham số kèm khi tải danh sách của ô `f`. `waiting`: danh sách phụ thuộc một ô ĐANG HIỆN mà người chưa chọn ⇒ chưa tải (chờ). Trường ẩn / cố định
 * còn trống thì bỏ qua như trước (danh sách vẫn tải).
 */
export function optionsParams(f: FieldSpec, values: Input, visibleKeys: ReadonlySet<string>): { params: Record<string, string>; waiting: boolean } {
  const params: Record<string, string> = {};
  let waiting = false;
  for (const [param, key] of Object.entries(optionsArgs(f))) {
    const v = values[key];
    if (v !== undefined && v !== null && v !== "") params[param] = String(v);
    else if (visibleKeys.has(key)) waiting = true;
  }
  return { params, waiting };
}

/** Ô nào phụ thuộc ô `key` (danh sách tải lại + giá trị bị xoá khi `key` đổi). */
export function dependentsOf(fields: FieldSpec[], key: string): FieldSpec[] {
  return fields.filter((f) => f.options_query && Object.values(optionsArgs(f)).includes(key));
}

// ── tiền ──

/**
 * Tiền đang gõ: chỉ có chữ số / dấu chấm ⇒ gom nghìn bằng dấu chấm ("4500000" → "4.500.000"); có chữ ("4,5 tỷ", "300tr") hoặc dấu phẩy ⇒ giữ nguyên chữ
 * người gõ (server hiểu đơn vị). Trả thêm số chữ số đứng trước vị trí con trỏ để giữ con trỏ sau khi định dạng lại.
 */
export function groupMoney(raw: string): string {
  const t = raw.replace(/\s/g, "");
  if (!t || !/^[\d.]+$/.test(t)) return raw;
  const digits = t.replace(/\D/g, "").replace(/^0+(?=\d)/, "");
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
}

/** Vị trí con trỏ trong `formatted` sao cho đứng sau cùng số chữ số như trước (`digitsBefore` = số chữ số bên trái con trỏ ở chữ cũ). */
export function caretAfter(formatted: string, digitsBefore: number): number {
  if (digitsBefore <= 0) return 0;
  let seen = 0;
  for (let i = 0; i < formatted.length; i++) {
    if (/\d/.test(formatted[i])) seen++;
    if (seen === digitsBefore) return i + 1;
  }
  return formatted.length;
}

/** Chữ hiện cho giá trị đã chọn trong chip / ô: nhãn của lựa chọn nếu có, không thì chính giá trị. */
export function shownValue(f: FieldSpec, v: Json | undefined, opts: Option[] | undefined): string {
  const s = text(v);
  const o = opts?.find((x) => x.value === s);
  return o ? o.label : s;
}
