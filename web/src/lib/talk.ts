import { foldVietnamese } from "./search";
import type { Json, Manifest, Notice, OpenRef } from "./types";

// Hộp "Tin nhắn" kiểu Lark (Rail.swift / Talk.swift): phòng nhóm + trao đổi trên hồ sơ từ `talk.inbox`, cùng thông báo hệ thống.
// Phần thuần ở đây (kiểm thử được): giải mã dung thứ, tách phòng / hồ sơ, số trên huy hiệu, mốc giờ, chữ cái ảnh phòng.

export type TalkItem = {
  thread_id: string;
  subject_kind: string;
  subject_id: string;
  kind_label: string;
  title: string;
  last_author: string;
  last_body: string;
  last_mine: boolean;
  at: string;
  unread: number;
  mentioned: number;
  /** 0115: tắt tiếng (không tính lên menu), nhóm hiển thị, tin riêng với người đã rời công ty. */
  muted: boolean;
  section: "direct" | "room" | "record";
  inactive: boolean;
};
export type TalkInbox = { unread: number; mentioned: number; items: TalkItem[]; can_start_direct: boolean };

type Obj = Record<string, unknown>;
const o = (v: unknown): Obj => (typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Obj) : {});
const s = (v: unknown): string => (typeof v === "string" ? v : v == null ? "" : String(v));
const n = (v: unknown): number => (typeof v === "number" && Number.isFinite(v) ? v : 0);

export function decodeInbox(raw: unknown): TalkInbox {
  const r = o(raw);
  const items = (Array.isArray(r.items) ? r.items : []).map((x): TalkItem => {
    const i = o(x);
    return {
      thread_id: s(i.thread_id),
      subject_kind: s(i.subject_kind),
      subject_id: s(i.subject_id),
      kind_label: s(i.kind_label),
      title: s(i.title),
      last_author: s(i.last_author),
      last_body: s(i.last_body),
      last_mine: i.last_mine === true,
      at: s(i.at),
      unread: n(i.unread),
      mentioned: n(i.mentioned),
      muted: i.muted === true,
      section: i.section === "direct" || i.section === "room" ? i.section : "record",
      inactive: i.inactive === true,
    };
  });
  return { unread: n(r.unread), mentioned: n(r.mentioned), items, can_start_direct: r.can_start_direct === true };
}

/** Dòng tạm cho một luồng chưa có trong hộp (mở từ thông báo / vừa tạo tin riêng) — chỉ loại + mã; luồng tự tải phần còn lại. */
export function talkItemFor(subjectKind: string, subjectId: string, title = ""): TalkItem {
  return { ...decodeInbox({ items: [{ subject_kind: subjectKind, subject_id: subjectId, title }] }).items[0] };
}

export const ROOM = "talk.room";
export const DIRECT = "talk.direct";
export const isRoom = (i: { subject_kind: string }): boolean => i.subject_kind === ROOM;
export const isDirect = (i: { subject_kind: string }): boolean => i.subject_kind === DIRECT;

/** Dòng tin cuối: "Bạn: …" / "{người gửi}: …"; phòng / tin riêng chưa có tin ⇒ lời mời chào. */
export function preview(i: TalkItem): string {
  if (!i.last_body) return isRoom(i) || isDirect(i) ? "Chưa có tin — gửi lời chào đầu tiên" : "";
  return `${i.last_mine ? "Bạn" : i.last_author}: ${i.last_body.replace(/\s+/g, " ")}`;
}

export function splitInbox(items: TalkItem[]): { directs: TalkItem[]; rooms: TalkItem[]; records: TalkItem[] } {
  return {
    directs: items.filter((i) => i.section === "direct"),
    rooms: items.filter((i) => i.section === "room"),
    records: items.filter((i) => i.section === "record"),
  };
}

/** Cỡ tệp đọc được: "820 KB", "3,4 MB". */
export function fileSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1).replace(".", ",")} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/** Đường tải tệp trong trao đổi (qua BFF; server kiểm quyền đọc luồng MỖI lần tải). */
export const talkFileUrl = (id: string): string => `/api/core/v1/talk/files/${encodeURIComponent(id)}`;

/** Tab "Tệp" (0128): dòng mở về đúng tin của luồng (server gửi `talk.thread` + `around`) ⇒ mã tin để nhảy trong luồng; còn lại (vd tài liệu kệ đội) ⇒ null, mở trang. */
export function jumpTarget(open: OpenRef | undefined): number | null {
  if (!open || open.query !== "talk.thread") return null;
  const a = open.input.around;
  return typeof a === "number" && Number.isInteger(a) && a > 0 ? a : null;
}

/** Ghép tin cũ vừa tải thêm vào trước cửa sổ đang có (bỏ trùng theo mã, giữ thứ tự cũ → mới). */
export function mergeOlder<T extends { id: number }>(older: T[], current: T[]): T[] {
  const seen = new Set(current.map((m) => m.id));
  return [...older.filter((m) => !seen.has(m.id)), ...current];
}

/** Số trên mục "Tin nhắn" của thanh bên trái = thông báo chưa đọc + số luồng trao đổi có tin chưa đọc (server đếm). */
export function messagesBadge(noticesUnread: number, talk: TalkInbox | null): number {
  return Math.max(0, noticesUnread) + Math.max(0, talk?.unread ?? 0);
}

export function unreadSummary(count: number): string {
  return count > 0 ? `${count} mục chưa đọc` : "Đã đọc hết";
}

export function badgeText(count: number): string {
  return count > 99 ? "99+" : String(count);
}

/** Chữ cái ảnh phòng: chữ đầu của 2 từ đầu, bỏ dấu, in hoa ("Dự án Hồ Sen" → "DA"; không in hoa chữ có dấu). */
export function roomInitials(name: string): string {
  const words = name.match(/\p{L}+/gu) ?? [];
  const text = foldVietnamese(words.slice(0, 2).map((w) => w[0]).join("")).toUpperCase();
  return text || "#";
}

const ROOM_PALETTE = ["#007aff", "#30b0c7", "#5856d6", "#ff9500", "#ff2d55", "#af52de", "#34c759"];

/** Màu cố định theo tên (như nhóm chat Lark) — cùng phép băm với RoomAvatar bên Mac. */
export function roomColor(name: string): string {
  let h = 0;
  for (const ch of name) h = (h + (ch.codePointAt(0) ?? 0)) % 997;
  return ROOM_PALETTE[h % ROOM_PALETTE.length];
}

const VN_DAY = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Ho_Chi_Minh", year: "numeric", month: "2-digit", day: "2-digit" });
const VN_TIME = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Ho_Chi_Minh", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });

/** Mốc giờ ở danh sách tin nhắn (giờ Việt Nam): hôm nay "14:05", hôm qua "Hôm qua", cũ hơn "28/10". */
export function stamp(iso: string, now: Date = new Date()): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const day = VN_DAY.format(d);
  if (day === VN_DAY.format(now)) return VN_TIME.format(d);
  if (day === VN_DAY.format(new Date(now.getTime() - 86_400_000))) return "Hôm qua";
  const [, m, dd] = day.split("-");
  return `${dd}/${m}`;
}

/** Thông báo chưa đọc trỏ vào chính luồng này (bị nhắc tên / có tin mới) — mở luồng là coi như đã đọc, một việc không đếm hai nơi. */
export function noticeIdsForSubject(notices: Notice[], subjectId: string): number[] {
  return notices
    .filter((x) => !x.read && x.open?.query === "talk.thread" && String((x.open.input as Record<string, Json>).subject_id ?? "") === subjectId)
    .map((x) => x.id);
}

/** Mục của thanh bên trái theo thứ tự hiện (phím tắt 1…9). */
export type Section = { kind: "assistant" } | { kind: "desk" } | { kind: "messages" } | { kind: "workspace"; key: string };
export const sectionId = (s: Section): string => (s.kind === "workspace" ? `ws:${s.key}` : s.kind);
export function railSections(workspaceKeys: string[]): Section[] {
  return [{ kind: "assistant" }, { kind: "desk" }, { kind: "messages" }, ...workspaceKeys.map((key) => ({ kind: "workspace" as const, key }))];
}

/** Tách "@tên" trong tin để tô màu — thấy ngay ai được nhắc. */
export function splitMentions(body: string): { text: string; mention: boolean }[] {
  const out: { text: string; mention: boolean }[] = [];
  const re = /@[^\s,.:;!?]+/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(body))) {
    if (m.index > last) out.push({ text: body.slice(last, m.index), mention: false });
    out.push({ text: m[0], mention: true });
    last = m.index + m[0].length;
  }
  if (last < body.length) out.push({ text: body.slice(last), mention: false });
  return out;
}

/** Có nên đọc hộp tin không: core khai `talk.inbox` trong danh sách truy vấn người này chạy được (gói Trao đổi bật + có quyền). Chưa có manifest ⇒
 *  không; core cũ không gửi danh sách ⇒ vẫn đọc như trước (lỗi thì giữ null). */
export function talkAvailable(m: Manifest | null): boolean {
  if (!m) return false;
  return m.queries === undefined || m.queries.includes("talk.inbox");
}
