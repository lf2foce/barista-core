import { cell, compactMoney, text, toNumber } from "@/lib/format";
import type { FieldSpec, Json, Option } from "@/lib/types";

/**
 * Bày bộ lọc của màn (rà bố cục 18/11/2026, kiểu Linear / Notion / Airtable): ô CHÍNH nằm ngoài hàng đầu, ô còn lại (kể cả nhóm gấp `group`) gom vào
 * nút "Bộ lọc" mở NGUYÊN bộ form; ô phụ đang bật hiện thành chip. Chỉ quyết CÁCH BÀY — giá trị lọc vẫn gửi nguyên cho server như trước.
 */

/** Màn có tới ngần này ô (không ô nào thuộc nhóm gấp) thì bày hết ra ngoài — không cần nút "Bộ lọc" cho ba ô. */
export const INLINE_MAX = 3;
/** Server cũ chưa gửi cờ `primary` ⇒ đoán: ngần này ô đầu không thuộc nhóm gấp là chính (cùng luật mặc định của server). */
export const GUESS_PRIMARY = 2;

export function splitFilters(fields: FieldSpec[]): { inline: FieldSpec[]; more: FieldSpec[] } {
  const plain = fields.filter((f) => !f.group);
  if (fields.length <= INLINE_MAX) return { inline: plain, more: fields.filter((f) => f.group) };
  const flagged = fields.some((f) => f.primary !== undefined) ? plain.filter((f) => f.primary) : [];
  const keys = new Set((flagged.length ? flagged : plain.slice(0, GUESS_PRIMARY)).map((f) => f.key));
  return { inline: fields.filter((f) => keys.has(f.key)), more: fields.filter((f) => !keys.has(f.key)) };
}

const same = (a: Json | undefined, b: Json | undefined) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

const empty = (v: Json | undefined) => v === undefined || v === null || v === "" || v === false;

/** Ô đang thật sự lọc: khác mặc định server khai. Trống mà mặc định có giá trị (vd chọn "Tất cả" khi mặc định "Hiệu lực") cũng là đang lọc. */
export function isActive(f: FieldSpec, v: Json | undefined): boolean {
  if (empty(v)) return !empty(f.default);
  return !same(v, f.default);
}

/** Giá trị "bỏ lọc" của một ô: về mặc định server khai, không có thì bỏ trống (undefined = không gửi). */
export const cleared = (f: FieldSpec): Json | undefined => (f.default === undefined || f.default === null ? undefined : f.default);

/** Chữ trên chip: "Nhãn: giá trị" — lựa chọn ⇒ nhãn lựa chọn; tiền ⇒ gọn (4,5 tỷ); có / không ⇒ chỉ nhãn. */
export function chipText(f: FieldSpec, v: Json | undefined, options?: Option[]): string {
  if (f.kind === "bool") return f.label;
  const opt = (options ?? f.options)?.find((o) => o.value === text(v));
  if (opt) return `${f.label}: ${opt.label}`;
  if (v === "" || v === undefined || v === null) return `${f.label}: Tất cả`;
  const n = f.kind === "money" ? toNumber(v) : null;
  return `${f.label}: ${n !== null ? compactMoney(n) : cell(v, f.kind === "date" ? "date" : "text")}`;
}
