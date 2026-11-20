// Trang riêng ở tab mới: mở MỘT trang của máy chủ (truy vấn + tham số) toàn màn hình, ngoài bàn làm việc — vd trình soạn văn bản Word (0227).
// Tên truy vấn và tham số đi trên địa chỉ; web không viết cứng tên thao tác nào (máy chủ nêu trong khối). Máy chủ vẫn kiểm quyền khi chạy truy vấn.

export type Standalone = { query: string; input: Record<string, string> };

const QUERY = /^[a-z][a-z_]*\.[a-z_]+$/;
const KEY = /^[a-z][a-z_]{0,39}$/;

/** Địa chỉ trang riêng cho một truy vấn. */
export function standaloneHref(query: string, input: Record<string, string>): string {
  const p = new URLSearchParams({ q: query });
  for (const [k, v] of Object.entries(input)) if (k !== "q") p.set(k, v);
  return `/open?${p.toString()}`;
}

/** Đọc lại từ tham số địa chỉ; tên truy vấn / khoá lạ ⇒ null / bỏ qua (không đưa chuỗi tuỳ ý vào lời gọi máy chủ). */
export function parseStandalone(params: Record<string, string | string[] | undefined>): Standalone | null {
  const q = params.q;
  if (typeof q !== "string" || !QUERY.test(q)) return null;
  const input: Record<string, string> = {};
  for (const [k, v] of Object.entries(params)) {
    if (k !== "q" && KEY.test(k) && typeof v === "string" && v.length <= 200) input[k] = v;
  }
  return { query: q, input };
}
