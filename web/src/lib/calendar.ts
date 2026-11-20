import type { CalendarCreator, CalendarItem, CalendarMove, Input, Json } from "./types";

// Phần thuần của màn Lịch (0240): đọc giờ từ chuỗi ISO server gửi (giờ CÔNG TY — không đổi theo múi giờ máy), xếp mục chồng giờ thành cột cạnh nhau,
// ráp đầu vào lệnh theo ánh xạ trường server khai. Không quyết quyền, không tính số nghiệp vụ.

/** "2026-11-18T08:30+07:00" ⇒ 510 (phút từ 0 giờ của NGÀY trong chuỗi); chuỗi chỉ có ngày ⇒ 0. */
export function minutesOf(iso: string): number {
  const m = /T(\d{2}):(\d{2})/.exec(iso);
  return m ? Number(m[1]) * 60 + Number(m[2]) : 0;
}

export const dayOf = (iso: string): string => iso.slice(0, 10);

export function hhmm(mins: number): string {
  const m = Math.max(0, Math.min(24 * 60, Math.round(mins)));
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}

export const snap = (mins: number, step = 15): number => Math.round(mins / step) * step;

export function addDays(day: string, n: number): string {
  const [y, m, d] = day.split("-").map(Number);
  const t = new Date(Date.UTC(y, m - 1, d + n));
  return t.toISOString().slice(0, 10);
}

/** "18/11" — nhãn ngắn; nhãn dài server gửi trong `days`. */
export function shortDay(day: string): string {
  return `${day.slice(8, 10)}/${day.slice(5, 7)}`;
}

/** Khoảng phút [bắt đầu, kết thúc) của một mục TRONG một ngày (mục qua đêm bị cắt theo ngày đó). */
export function spanIn(item: CalendarItem, day: string): [number, number] | null {
  const sd = dayOf(item.start);
  const ed = dayOf(item.end);
  if (day < sd || day > ed) return null;
  const s = sd === day ? minutesOf(item.start) : 0;
  let e = ed === day ? minutesOf(item.end) : 24 * 60;
  if (e <= s) e = Math.min(24 * 60, s + 15);
  if (ed === day && minutesOf(item.end) === 0 && sd !== day) return null;   // kết thúc đúng 0 giờ hôm nay: không chiếm ngày này
  return [s, e];
}

export type Placed = { item: CalendarItem; start: number; end: number; col: number; cols: number };

/** Xếp các mục có giờ của một cột (một người × một ngày): mục chồng giờ chia nhau bề ngang như app lịch — mỗi cụm chồng nhau dùng chung số cột. */
export function layout(items: CalendarItem[], day: string): Placed[] {
  const spans = items
    .filter((i) => !i.all_day)
    .map((item) => ({ item, span: spanIn(item, day) }))
    .filter((x): x is { item: CalendarItem; span: [number, number] } => x.span !== null)
    .sort((a, b) => a.span[0] - b.span[0] || b.span[1] - a.span[1]);
  const out: Placed[] = [];
  let cluster: Placed[] = [];
  let clusterEnd = -1;
  const flush = () => {
    const cols = Math.max(1, ...cluster.map((p) => p.col + 1));
    for (const p of cluster) p.cols = cols;
    out.push(...cluster);
    cluster = [];
  };
  for (const { item, span } of spans) {
    // mục ngắn vẫn chiếm tối thiểu 20 phút khi xét chồng (để chữ đọc được)
    const [s, e] = [span[0], Math.max(span[1], span[0] + 20)];
    if (cluster.length && s >= clusterEnd) flush();
    const used = new Set(cluster.filter((p) => Math.max(p.end, p.start + 20) > s).map((p) => p.col));
    let col = 0;
    while (used.has(col)) col++;
    cluster.push({ item, start: span[0], end: span[1], col, cols: 1 });
    clusterEnd = Math.max(clusterEnd, e);
  }
  if (cluster.length) flush();
  return out;
}

export function allDayIn(items: CalendarItem[], day: string): CalendarItem[] {
  return items.filter((i) => i.all_day && dayOf(i.start) <= day && day <= dayOf(i.end));
}

/** Đầu vào lệnh tạo theo ánh xạ trường của cách tạo (server khai); trường lệnh không nhận thì không gửi. */
export function createPayload(
  c: CalendarCreator,
  v: { day: string; start: number; end: number; title: string; subject?: string; place?: string; attendees?: string[] },
): Input {
  const f = c.fields;
  const out: Input = { ...c.preset };
  if (f.day) out[f.day] = v.day;
  if (f.start) out[f.start] = hhmm(v.start);
  if (f.end) out[f.end] = hhmm(v.end);
  if (f.minutes) out[f.minutes] = Math.max(0, v.end - v.start);
  if (f.title && v.title.trim()) out[f.title] = v.title.trim();
  if (f.attendee && v.attendees?.length) out[f.attendee] = [...new Set(v.attendees)];
  if (c.subject && v.subject) out[c.subject.field] = v.subject;
  if (c.place && v.place?.trim()) out[c.place] = v.place.trim();
  return out;
}

/** Đầu vào lệnh dời (thả mục sang giờ / ngày mới). */
export function movePayload(m: CalendarMove, day: string, start: number, end: number): Input {
  const out: Record<string, Json> = { ...m.preset };
  if (m.fields.day) out[m.fields.day] = day;
  if (m.fields.start) out[m.fields.start] = hhmm(start);
  if (m.fields.end) out[m.fields.end] = hhmm(end);
  return out;
}

/** Cách tạo khớp với loại người chọn; không có ⇒ cách đầu tiên. */
export function creatorFor(creators: CalendarCreator[], key: string): CalendarCreator | undefined {
  return creators.find((c) => c.key === key) ?? creators[0];
}

/** Ngày trong khoảng có mục — lịch biểu bỏ ngày trống (trừ hôm nay, để người thấy "hôm nay trống"). */
export function agendaDays(items: CalendarItem[], days: string[], today: string): { day: string; items: CalendarItem[] }[] {
  return days
    .map((day) => ({
      day,
      items: items
        .filter((i) => (i.all_day ? dayOf(i.start) <= day && day <= dayOf(i.end) : dayOf(i.start) === day))
        .sort((a, b) => Number(b.all_day) - Number(a.all_day) || a.start.localeCompare(b.start) || a.title.localeCompare(b.title)),
    }))
    .filter((g) => g.items.length > 0 || g.day === today);
}

/** Mục trùng mã trên nhiều làn (họp nhiều người) — tuần / lịch biểu chỉ hiện một lần. */
export function unique(items: CalendarItem[]): CalendarItem[] {
  const seen = new Set<string>();
  return items.filter((i) => (seen.has(i.id) ? false : (seen.add(i.id), true)));
}

/** Màu của tông (biến CSS sáng / tối ở globals.css). */
export function toneVar(tone: string): string {
  const known = ["blue", "teal", "green", "orange", "purple", "red", "pink", "indigo"];
  return known.includes(tone) ? `var(--chart-${tone})` : "var(--muted-foreground)";
}
