import type { Json, Kpi } from "./types";

// Định dạng số / ngày kiểu Việt Nam — phản chiếu `Fmt` trong macos/Sources/BaristaDesk/Theme.swift:
// 4.500.000.000 · 4,5 tỷ · 30/10/2026 · 2,3% · 0100 000 019. App chỉ trình bày, không tính số.

const NUM = new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 2 });
export const number = (v: number): string => NUM.format(v);

/** Tiền gọn: giữ đủ số lẻ để không lệch quá ~1% (rà UI 23/11: 4.500.000 đ từng hiện "5 tr", lệch 11%) — 4,55 tr · 45,5 tr · 455 tr · 4,55 tỷ.
 *  Cùng luật `Fmt.compactMoney` bên Swift. */
export function compactMoney(v: number): string {
  const a = Math.abs(v);
  const at = (unit: number, digits: number) => number(Math.round((v / unit) * 10 ** digits) / 10 ** digits);
  if (a >= 1e9) return `${at(1e9, 2)} tỷ`;
  if (a >= 1e6) return `${at(1e6, a >= 1e8 ? 0 : a >= 1e7 ? 1 : 2)} tr`;
  return `${number(v)} đ`;
}

/** Ô số ĐÃ ĐO và bằng 0 (kể cả "0" dạng chữ) hoặc chữ rỗng. KHÔNG gồm null / "—": đó là CHƯA ĐO / không tính được — phải hiện ("—"), không được
 *  biến mất lặng lẽ (rà của Codex 23/11/2026: "chưa đo" khác "đo ra 0"). */
export function isBlank(v: Json | undefined): boolean {
  if (v === "") return true;
  const n = toNumber(v);
  return n === 0;
}

/** Ô số còn hiện: "chưa có gì thì ẩn hết" (anh Anh 23/11/2026) — ô đo ra 0 bị bỏ, hàng toàn 0 thì không còn ô nào (KpiRow không vẽ); ô chưa đo
 *  (null / "—") vẫn hiện. */
export function shownKpis<K extends { value: Json | null }>(kpis: K[]): K[] {
  return kpis.filter((k) => !isBlank(k.value));
}

/** Cột còn hiện: bỏ cột mà MỌI dòng đều trống (null / "" / "—"). Số 0 vẫn là dữ liệu của cột (một dòng có thể 0, dòng khác không). */
export function shownColumns<C extends { key: string }>(columns: C[], rows: { values: Record<string, Json> }[]): C[] {
  if (!rows.length) return columns;
  const empty = (v: Json | undefined) => v === null || v === undefined || v === "" || v === "—";
  return columns.filter((c) => rows.some((r) => !empty(r.values[c.key])));
}

/** Phần riêng của tiêu đề server gửi so với tên màn trên menu ("Sổ giao dịch · năm 2026" ⇒ "năm 2026") — làm phụ đề đầu thẻ. Trùng ⇒ null. */
export function titleTail(server: string | null | undefined, menu: string): string | null {
  const t = (server ?? "").trim();
  if (!t || t === menu) return null;
  if (t.startsWith(menu)) return t.slice(menu.length).replace(/^[\s·:—-]+/, "").trim() || null;
  return t;
}

/** Số từ JSON (số hoặc chữ số); không phải số ⇒ null. */
export function toNumber(v: Json | undefined): number | null {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

/** Chữ thô (khoá, so sánh `when`, giá trị lựa chọn) — như `JSON.text` bên Swift. */
export function text(v: Json | undefined): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "number") return String(v);
  if (typeof v === "string") return v;
  if (Array.isArray(v)) return v.map(text).join(", ");
  return "";
}

export function date(s: string): string {
  const p = s.slice(0, 10).split("-");
  return p.length === 3 && p[0].length === 4 ? `${p[2]}/${p[1]}/${p[0]}` : s;
}

const VN_PARTS = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Ho_Chi_Minh",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

function vnParts(d: Date): Record<string, string> {
  return Object.fromEntries(VN_PARTS.formatToParts(d).map((p) => [p.type, p.value]));
}

/** ISO (UTC "Z" hay "+07:00", có / không phần lẻ giây) → Date; hỏng ⇒ null. */
export function parseISO(s: string): Date | null {
  if (s.length < 19) return null;
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function datetime(s: string): string {
  const d = parseISO(s);
  if (!d) return s;
  const p = vnParts(d);
  return `${p.day}/${p.month}/${p.year} ${p.hour}:${p.minute}`;
}

export function clock(d: Date): string {
  const p = vnParts(d);
  return `${p.hour}:${p.minute}`;
}

/** SĐT lưu E.164 (ADR 0199, bổ sung 15/11/2026). Số cùng nước công ty (`rule` = `tenant.phone` của manifest; mặc định Việt Nam) ⇒ số nội địa liền,
 *  không dấu cách (`+84898654936` → `0898654936`); số nước khác giữ `+<mã nước><số>` đủ mã. Không phải E.164 (số đã che, chữ tự do) ⇒ nguyên văn.
 *  Giữ cùng luật `phone_display` ở backend (kernel/fields.py). */
export function phone(s: string, rule: { code: string; trunk: string } | null = { code: "84", trunk: "0" }): string {
  return rule && /^\+\d+$/.test(s) && s.startsWith(`+${rule.code}`) ? rule.trunk + s.slice(1 + rule.code.length) : s;
}

/** Một ô theo `format` server gửi. */
export function cell(v: Json | undefined, format: string, labels?: Record<string, string>): string {
  if (v === null || v === undefined) return "";
  // nhãn server khai (mã ⇒ chữ) áp cho MỌI kiểu cột, không riêng huy hiệu — mã nội bộ không lọt ra màn hình (rà 23/11: "Đọc bởi = local")
  if (labels && typeof v !== "object" && labels[text(v)] !== undefined) return labels[text(v)];
  switch (format) {
    case "money":
    case "number": {
      const n = toNumber(v);
      return n === null ? text(v) : number(n);
    }
    case "percent": {
      const n = toNumber(v);
      return n === null ? text(v) : `${number(n)}%`;
    }
    case "date":
      return date(text(v));
    case "datetime":
      return datetime(text(v));
    case "phone":
      return phone(text(v));
    case "badge":
      return labels?.[text(v)] ?? text(v);
    default:
      if (typeof v === "boolean") return v ? "Có" : "Không";
      return text(v);
  }
}

export function kpi(k: Kpi): string {
  if (k.value === null) return "—";
  if (k.format === "money") {
    const n = toNumber(k.value);
    return n === null ? text(k.value) : compactMoney(n);
  }
  return cell(k.value, k.format);
}

/** Tông bảng (badge, KPI) → tên lớp màu. */
export function toneClass(tone: string | null | undefined): string {
  switch (tone) {
    case "green":
      return "text-ok";
    case "orange":
      return "text-warn";
    case "red":
      return "text-bad";
    case "blue":
      return "text-cold";
    case "purple":
      return "text-violet";
    case "gray":
      return "text-muted-foreground";
    default:
      return "text-foreground";
  }
}

/** Tông khối thẻ: ok · warn · bad · accent · muted. */
export function cardToneVar(tone: string | null | undefined): string {
  // nhận cả bộ tông của bảng (green / orange / red / blue / purple) — trước đây khối gửi "red" / "orange" rơi về màu xám (rà 23/11)
  switch (tone) {
    case "ok":
    case "green":
      return "var(--ok)";
    case "warn":
    case "orange":
      return "var(--warn)";
    case "bad":
    case "red":
      return "var(--bad)";
    case "accent":
    case "blue":
      return "var(--cold)";
    case "purple":
      return "var(--violet)";
    default:
      return "var(--muted-foreground)";
  }
}

export function pillStyle(tone: string | null | undefined): { color: string; background: string } {
  const map: Record<string, string> = {
    green: "var(--ok)",
    orange: "var(--warn)",
    red: "var(--bad)",
    blue: "var(--cold)",
    purple: "var(--violet)",
  };
  const c = (tone && map[tone]) || "var(--muted-foreground)";
  return { color: c, background: `color-mix(in oklab, ${c} 14%, transparent)` };
}
