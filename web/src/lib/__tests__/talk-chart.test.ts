import { describe, expect, it } from "vitest";
import { chartAxis, chartColor, chartRows, chartValue, donutSlices, funnelSteps, hasChartData, headroom, type ChartData } from "../chart";
import { decodeBlock } from "../decode";
import {
  badgeText, decodeInbox, messagesBadge, noticeIdsForSubject, preview, railSections, roomColor, roomInitials, sectionId, splitInbox, splitMentions, stamp, unreadSummary,
  type TalkItem,
} from "../talk";
import type { Notice } from "../types";

const item = (over: Partial<TalkItem> = {}): TalkItem => ({
  thread_id: "t", subject_kind: "crm.lead", subject_id: "s", kind_label: "Khách / lead", title: "Lan", last_author: "An", last_body: "chào",
  last_mine: false, at: "", unread: 0, mentioned: 0, muted: false, inactive: false,
  section: over.subject_kind === "talk.room" ? "room" : over.subject_kind === "talk.direct" ? "direct" : "record", ...over,
});

describe("tin nhắn: ảnh phòng", () => {
  it("chữ đầu của 2 từ đầu, bỏ dấu, in hoa", () => {
    expect(roomInitials("Dự án Hồ Sen Residence")).toBe("DA");
    expect(roomInitials("Kinh doanh A")).toBe("KD");
    expect(roomInitials("Sàn mẫu · Toàn công ty")).toBe("SM");
    expect(roomInitials("Đội ứng cứu")).toBe("DU");
    expect(roomInitials("ấm")).toBe("A");
    expect(roomInitials("· 123 ·")).toBe("#");
  });
  it("màu cố định theo tên, thuộc bảng màu", () => {
    expect(roomColor("Kinh doanh A")).toBe(roomColor("Kinh doanh A"));
    expect(roomColor("Kinh doanh A")).toMatch(/^#[0-9a-f]{6}$/);
  });
});

describe("tin nhắn: mốc giờ (giờ Việt Nam)", () => {
  const now = new Date("2026-10-31T10:00:00Z"); // 17:00 ở VN
  it("hôm nay ⇒ HH:mm", () => {
    expect(stamp("2026-10-31T18:10:02.07+07:00", now)).toBe("18:10");
    expect(stamp("2026-10-30T17:30:00Z", now)).toBe("00:30"); // 00:30 ngày 31 ở VN
  });
  it("hôm qua ⇒ 'Hôm qua'; cũ hơn ⇒ dd/MM", () => {
    expect(stamp("2026-10-30T08:00:00+07:00", now)).toBe("Hôm qua");
    expect(stamp("2026-10-28T23:59:00+07:00", now)).toBe("28/10");
  });
  it("hỏng / trống ⇒ chuỗi rỗng", () => {
    expect(stamp("", now)).toBe("");
    expect(stamp("không phải ngày", now)).toBe("");
  });
});

describe("tin nhắn: hộp và huy hiệu", () => {
  it("giải mã dung thứ", () => {
    const d = decodeInbox({ unread: 2, items: [{ title: "A", subject_kind: "talk.room", unread: "x", extra: 1 }], mới: true });
    expect(d.unread).toBe(2);
    expect(d.mentioned).toBe(0);
    expect(d.items[0]).toMatchObject({ title: "A", unread: 0, last_mine: false });
    expect(decodeInbox(null)).toEqual({ unread: 0, mentioned: 0, items: [], can_start_direct: false });
    expect(d.items[0].section).toBe("record"); // nhóm chỉ theo `section` server gửi — không suy từ loại
  });
  it("tách phòng / hồ sơ giữ thứ tự mới nhất trước", () => {
    const { rooms, records } = splitInbox([item({ title: "r1", subject_kind: "talk.room" }), item({ title: "h1" }), item({ title: "r2", subject_kind: "talk.room" })]);
    expect(rooms.map((x) => x.title)).toEqual(["r1", "r2"]);
    expect(records.map((x) => x.title)).toEqual(["h1"]);
  });
  it("huy hiệu = thông báo chưa đọc + luồng chưa đọc", () => {
    expect(messagesBadge(3, { unread: 2, mentioned: 1, items: [], can_start_direct: true })).toBe(5);
    expect(messagesBadge(1, null)).toBe(1);
    expect(unreadSummary(5)).toBe("5 mục chưa đọc");
    expect(unreadSummary(0)).toBe("Đã đọc hết");
    expect(badgeText(120)).toBe("99+");
  });
  it("dòng tin cuối", () => {
    expect(preview(item({ last_mine: true, last_body: "ok\nnhé" }))).toBe("Bạn: ok nhé");
    expect(preview(item())).toBe("An: chào");
    expect(preview(item({ subject_kind: "talk.room", last_body: "" }))).toBe("Chưa có tin — gửi lời chào đầu tiên");
    expect(preview(item({ last_body: "" }))).toBe("");
  });
  it("thông báo trỏ vào luồng đang mở (chưa đọc) ⇒ đánh dấu đọc", () => {
    const mk = (id: number, read: boolean, query: string, sid: string): Notice => ({ id, kind: "mention", title: "", body: "", read, created_at: "", open: { query, input: { subject_id: sid, subject_kind: "crm.lead" } } });
    expect(noticeIdsForSubject([mk(1, false, "talk.thread", "S"), mk(2, true, "talk.thread", "S"), mk(3, false, "talk.thread", "X"), mk(4, false, "flow.case", "S")], "S")).toEqual([1]);
  });
  it("tô @tên", () => {
    expect(splitMentions("@An @Bích ưu tiên, @Nam.")).toEqual([
      { text: "@An", mention: true }, { text: " ", mention: false }, { text: "@Bích", mention: true },
      { text: " ưu tiên, ", mention: false }, { text: "@Nam", mention: true }, { text: ".", mention: false },
    ]);
    expect(splitMentions("không nhắc ai")).toEqual([{ text: "không nhắc ai", mention: false }]);
  });
  it("mục thanh bên trái: trợ lý, việc (bàn việc), tin nhắn, rồi phân hệ", () => {
    expect(railSections(["sales", "work"]).map(sectionId)).toEqual(["assistant", "desk", "messages", "ws:sales", "ws:work"]);
  });
});

const chart = (over: Partial<ChartData> = {}): ChartData => ({ title: "t", kind: "bar", unit: "vnd", labels: ["a", "b"], series: [{ name: "x", values: [1, null] }], note: "", ...over });

describe("biểu đồ", () => {
  it("định dạng số: tiền gọn, trục '0'", () => {
    expect(chartValue(45_770_000_000, "vnd")).toBe("45,77 tỷ");
    expect(chartValue(461_250_000, "vnd")).toBe("461 tr");
    expect(chartValue(0, "vnd")).toBe("0 đ");
    expect(chartAxis(0, "vnd")).toBe("0");
    expect(chartAxis(6_620_000_000, "vnd")).toBe("6,62 tỷ");
    expect(chartValue(12.345, "pct")).toBe("12,3%");
    expect(chartValue(1234, "count")).toBe("1.234");
  });
  it("chưa đo được: null hết, hoặc phễu / bánh toàn 0 (cột toàn 0 vẫn vẽ)", () => {
    expect(hasChartData(chart({ series: [{ name: "x", values: [null, null] }] }))).toBe(false);
    expect(hasChartData(chart({ kind: "funnel", series: [{ name: "x", values: [0, 0] }] }))).toBe(false);
    expect(hasChartData(chart({ kind: "donut", series: [{ name: "x", values: [0, null] }] }))).toBe(false);
    expect(hasChartData(chart({ kind: "bar", series: [{ name: "x", values: [0, 0] }] }))).toBe(true);
    expect(hasChartData(chart({ series: [] }))).toBe(false);
  });
  it("dòng Recharts giữ null (không thành 0)", () => {
    expect(chartRows(chart({ series: [{ name: "x", values: [1, null] }, { name: "y", values: [2] }] }))).toEqual([
      { label: "a", s0: 1, s1: 2 },
      { label: "b", s0: null, s1: null },
    ]);
  });
  it("phễu: % so bước trước, bỏ qua bước không đo được", () => {
    const f = funnelSteps(chart({ kind: "funnel", labels: ["a", "b", "c", "d"], series: [{ name: "x", values: [40, 18, null, 11] }] }));
    expect(f.map((s) => s.pctPrev)).toEqual([null, 45, null, null]);
    expect(f[2].value).toBeNull();
  });
  it("bánh: bỏ miếng null, % theo tổng", () => {
    const d = donutSlices(chart({ kind: "donut", labels: ["DTP", "HSR", "X"], series: [{ name: "x", values: [10_470_000_000, 6_650_000_000, null] }] }));
    expect(d.total).toBe(17_120_000_000);
    expect(d.slices.map((s) => s.pct)).toEqual([61, 39]);
  });
  it("màu: tông server, không có ⇒ bảng theo thứ tự", () => {
    expect(chartColor("purple")).toBe("var(--chart-purple)");
    expect(chartColor(undefined, 1)).toBe("var(--chart-teal)");
    expect(chartColor("lạ", 0)).toBe("var(--chart-blue)");
  });
  it("chừa chỗ cuối thanh", () => {
    expect(headroom(chart({ series: [{ name: "x", values: [100, null] }] }), 1.18)).toBeCloseTo(118);
    expect(headroom(chart({ series: [{ name: "x", values: [null] }] }), 1.18)).toBe(1);
  });
  it("giải mã khối chart: kind lạ ⇒ bar, giá trị không phải số ⇒ null", () => {
    const b = decodeBlock({ type: "chart", kind: "radar", labels: ["a"], series: [{ name: "x", values: [1, "2", null, NaN] }] });
    expect(b.type === "chart" && b.kind).toBe("bar");
    expect(b.type === "chart" && b.series[0].values).toEqual([1, null, null, null]);
  });
});

import { shortcutFor, type KeyLike } from "../shortcuts";

describe("phím tắt", () => {
  const k = (over: Partial<KeyLike>): KeyLike => ({ key: "", code: "", altKey: false, ctrlKey: false, metaKey: false, shiftKey: false, ...over });
  it("⌘K / Ctrl+K mở tìm", () => {
    expect(shortcutFor(k({ key: "k", code: "KeyK", metaKey: true }))).toEqual({ kind: "palette" });
    expect(shortcutFor(k({ key: "K", code: "KeyK", ctrlKey: true }))).toEqual({ kind: "palette" });
  });
  it("Ctrl+Shift+S / ⌃⌘S ẩn hiện thanh; Ctrl+S thường (lưu trang) không đụng", () => {
    expect(shortcutFor(k({ key: "S", code: "KeyS", ctrlKey: true, shiftKey: true }))).toEqual({ kind: "rail" });
    expect(shortcutFor(k({ key: "s", code: "KeyS", ctrlKey: true, metaKey: true }))).toEqual({ kind: "rail" });
    expect(shortcutFor(k({ key: "s", code: "KeyS", ctrlKey: true }))).toBeNull();
  });
  it("Alt+số chuyển mục (⌥ trên Mac đổi ký tự nên đọc theo code); ⌘/Ctrl+số để trình duyệt chuyển tab", () => {
    expect(shortcutFor(k({ key: "¡", code: "Digit1", altKey: true }))).toEqual({ kind: "section", index: 0 });
    expect(shortcutFor(k({ key: "9", code: "Digit9", altKey: true }))).toEqual({ kind: "section", index: 8 });
    expect(shortcutFor(k({ key: "1", code: "Digit1", metaKey: true }))).toBeNull();
    expect(shortcutFor(k({ key: "0", code: "Digit0", altKey: true }))).toBeNull();
  });
});
