// Ô chọn ngày (lịch thả xuống): đọc giá trị server / người gõ (ISO yyyy-mm-dd hoặc dd/mm/yyyy), dựng lưới tháng tuần bắt đầu thứ Hai, gửi lên ISO
// (lõi nhận cả hai — kernel/fields.parse_date). Chỉ là lịch dương thuần, không múi giờ: ngày là ngày, không phải thời điểm.

export type Day = { y: number; m: number; d: number };   // m: 1–12

const valid = (y: number, m: number, d: number): Day | null => {
  if (!(y >= 1900 && y <= 2200 && m >= 1 && m <= 12 && d >= 1)) return null;
  return d <= daysIn(y, m) ? { y, m, d } : null;
};

export function daysIn(y: number, m: number): number {
  return new Date(Date.UTC(y, m, 0)).getUTCDate();
}

/** "2026-11-05", "2026-11-05T…", "05/11/2026", "5/11/2026" ⇒ ngày; còn lại ⇒ null. */
export function parseDay(v: unknown): Day | null {
  if (typeof v !== "string") return null;
  const s = v.trim();
  let m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s);
  if (m) return valid(+m[1], +m[2], +m[3]);
  m = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(s);
  if (m) return valid(+m[3], +m[2], +m[1]);
  return null;
}

const pad = (n: number) => String(n).padStart(2, "0");
export const iso = (x: Day) => `${x.y}-${pad(x.m)}-${pad(x.d)}`;
export const vn = (x: Day) => `${pad(x.d)}/${pad(x.m)}/${x.y}`;
/** Ngày không nhập nhằng cho người đọc: "1 tháng 12, 2026" (ADR 0225) — d/m hay m/d đều không lẫn. */
export const vnLong = (x: Day) => `${x.d} tháng ${x.m}, ${x.y}`;
export const sameDay = (a: Day | null, b: Day | null) => !!a && !!b && a.y === b.y && a.m === b.m && a.d === b.d;

export function today(now = new Date()): Day {
  return { y: now.getFullYear(), m: now.getMonth() + 1, d: now.getDate() };
}

/** Lùi / tiến n tháng (giữ năm đúng khi qua tháng 1 / 12). */
export function addMonths(y: number, m: number, n: number): { y: number; m: number } {
  const k = y * 12 + (m - 1) + n;
  return { y: Math.floor(k / 12), m: (k % 12) + 1 };
}

/** Lưới 6 tuần × 7 ngày, tuần bắt đầu thứ Hai; ô ngoài tháng mang `out`. */
export function monthGrid(y: number, m: number): (Day & { out: boolean })[] {
  const first = new Date(Date.UTC(y, m - 1, 1)).getUTCDay();   // 0 = Chủ nhật
  const lead = (first + 6) % 7;                                  // số ô của tháng trước trước ngày 1 (thứ Hai = 0)
  const prev = addMonths(y, m, -1);
  const next = addMonths(y, m, 1);
  const cells: (Day & { out: boolean })[] = [];
  for (let i = lead; i > 0; i--) cells.push({ ...prev, d: daysIn(prev.y, prev.m) - i + 1, out: true });
  for (let d = 1; d <= daysIn(y, m); d++) cells.push({ y, m, d, out: false });
  for (let d = 1; cells.length < 42; d++) cells.push({ ...next, d, out: true });
  return cells;
}

export const WEEKDAYS = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"];

// ── Giờ trong ngày (HH:MM, 24 giờ) và tháng (yyyy-mm) cho ô chọn giờ / tháng ──

export type Clock = { h: number; m: number };

/**
 * Đọc giờ người gõ: "14:30", "14.30", "14h30", "1430", "9" (⇒ 09:00), "9h" ⇒ 24 giờ; ngoài khoảng 00:00–23:59 hoặc không đọc được ⇒ null.
 * Không đoán SA / CH: công ty dùng 24 giờ.
 */
export function parseClock(v: unknown): Clock | null {
  if (typeof v !== "string") return null;
  const s = v.trim().toLowerCase();
  let m = /^(\d{1,2})\s*[:.h]\s*(\d{1,2})?$/.exec(s);
  if (!m) m = /^(\d{1,2})$/.exec(s);
  if (!m) {
    const compact = /^(\d{2})(\d{2})$/.exec(s);   // 1430
    if (compact) m = compact;
  }
  if (!m) return null;
  const h = Number(m[1]);
  const min = m[2] === undefined || m[2] === "" ? 0 : Number(m[2]);
  return h <= 23 && min <= 59 ? { h, m: min } : null;
}

export const hhmm = (x: Clock) => `${pad(x.h)}:${pad(x.m)}`;

export type Month = { y: number; m: number };

/** "2026-11", "2026-11-05", "11/2026", "Tháng 11/2026" ⇒ tháng; còn lại ⇒ null. */
export function parseMonth(v: unknown): Month | null {
  if (typeof v !== "string") return null;
  const s = v.trim();
  let m = /^(\d{4})-(\d{2})(?:-\d{2})?/.exec(s);
  let y: number, mo: number;
  if (m) [y, mo] = [+m[1], +m[2]];
  else if ((m = /^(?:th[áa]ng\s*)?(\d{1,2})\/(\d{4})$/i.exec(s))) [y, mo] = [+m[2], +m[1]];
  else return null;
  return y >= 1900 && y <= 2200 && mo >= 1 && mo <= 12 ? { y, m: mo } : null;
}

export const isoMonth = (x: Month) => `${x.y}-${pad(x.m)}`;
export const vnMonth = (x: Month) => `Tháng ${x.m}/${x.y}`;
