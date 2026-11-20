import type { FieldSpec, Input } from "@/lib/types";

/** Bộ lọc NGƯỜI NÀY chọn lần trước trên một màn (rà vai 18/11/2026: kỳ, trạng thái…) — tiện ích riêng từng người ở trình duyệt, không phải cài đặt
 * công ty: mặc định vẫn do server khai (ô lọc `default`), web chỉ nhớ lựa chọn của người đó theo công ty × người × truy vấn. Chỉ nhớ ô LỌC của màn
 * (không đầu vào cố định `input`, không giá trị mở từ chat / ô số). Kho trình duyệt có thể trống / bị chặn ⇒ coi như chưa nhớ gì. */
const PREFIX = "barista-filters:";
const MAX_CHARS = 4000;

export interface Store {
  getItem(k: string): string | null;
  setItem(k: string, v: string): void;
  removeItem(k: string): void;
}

function store(): Store | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

export const filterKey = (tenant: string, user: string, query: string) => `${PREFIX}${tenant}:${user}:${query}`;

/** Lựa chọn đã nhớ — chỉ ô lọc màn còn khai (ô đã bỏ / đổi tên thì quên). */
export function recall(key: string, fields: FieldSpec[], s: Store | null = store()): Input {
  try {
    const raw = s?.getItem(key);
    const saved = raw ? (JSON.parse(raw) as unknown) : null;
    if (!saved || typeof saved !== "object" || Array.isArray(saved)) return {};
    const known = new Set(fields.map((f) => f.key));
    return Object.fromEntries(Object.entries(saved as Input).filter(([k]) => known.has(k)));
  } catch {
    return {};
  }
}

/** Ghi lựa chọn: chỉ ô khác mặc định của server; không còn ô nào khác mặc định ⇒ xoá (lần sau mở theo mặc định). */
export function remember(key: string, fields: FieldSpec[], filters: Input, s: Store | null = store()): void {
  const picked: Input = {};
  for (const f of fields) {
    const v = filters[f.key];
    if (v !== undefined && JSON.stringify(v) !== JSON.stringify(f.default ?? null)) picked[f.key] = v;
  }
  try {
    const text = JSON.stringify(picked);
    if (!Object.keys(picked).length || text.length > MAX_CHARS) s?.removeItem(key);
    else s?.setItem(key, text);
  } catch {
    /* kho trình duyệt bị chặn — bỏ qua, màn vẫn chạy */
  }
}
