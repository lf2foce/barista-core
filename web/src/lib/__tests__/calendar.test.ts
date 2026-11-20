import { describe, expect, it } from "vitest";
import { agendaDays, createPayload, layout, minutesOf, movePayload, spanIn, unique } from "../calendar";
import { decodeBlock } from "../decode";
import type { CalendarCreator, CalendarItem } from "../types";

const it_ = (id: string, start: string, end: string, extra: Partial<CalendarItem> = {}): CalendarItem => ({
  id, lane: "", title: id, subtitle: "", start, end, all_day: false, kind: "", tone: "blue", status: "booked", busy: false, repeats: false, links: [], ...extra,
});

describe("lịch (0240)", () => {
  it("đọc giờ từ chuỗi ISO giờ công ty, không theo múi giờ máy", () => {
    expect(minutesOf("2026-11-18T08:30+07:00")).toBe(510);
    expect(minutesOf("2026-11-18")).toBe(0);
  });

  it("mục chồng giờ chia nhau bề ngang theo cụm; mục rời cụm dùng cả bề ngang", () => {
    const d = "2026-11-18";
    const out = layout([it_("a", `${d}T09:00+07:00`, `${d}T10:00+07:00`), it_("b", `${d}T09:30+07:00`, `${d}T11:00+07:00`),
                        it_("c", `${d}T10:15+07:00`, `${d}T10:45+07:00`), it_("d", `${d}T14:00+07:00`, `${d}T15:00+07:00`)], d);
    const by = Object.fromEntries(out.map((p) => [p.item.id, p]));
    expect([by.a.col, by.b.col, by.c.col]).toEqual([0, 1, 0]);
    expect([by.a.cols, by.b.cols, by.c.cols]).toEqual([2, 2, 2]);
    expect([by.d.col, by.d.cols]).toEqual([0, 1]);
  });

  it("mục qua đêm bị cắt theo ngày; kết thúc đúng 0 giờ không chiếm ngày sau", () => {
    const x = it_("x", "2026-11-18T22:00+07:00", "2026-11-19T01:00+07:00");
    expect(spanIn(x, "2026-11-18")).toEqual([1320, 1440]);
    expect(spanIn(x, "2026-11-19")).toEqual([0, 60]);
    expect(spanIn(it_("y", "2026-11-18T22:00+07:00", "2026-11-19T00:00+07:00"), "2026-11-19")).toBeNull();
  });

  it("ráp đầu vào lệnh theo ánh xạ server khai — trường lệnh không nhận thì không gửi", () => {
    const crm: CalendarCreator = { key: "site_visit", label: "Tham quan", kind: "site_visit", tone: "green", command: "crm.schedule", preset: { kind: "visit" },
      fields: { day: "day", start: "time", minutes: "minutes", title: "note" }, subject: { field: "lead_id", label: "Khách", search: "crm.lead_pick", required: true }, place: "place" };
    expect(createPayload(crm, { day: "2026-11-19", start: 840, end: 930, title: " dẫn xem căn mẫu ", subject: "L1", place: "Sun Feliza", attendees: ["a@x"] }))
      .toEqual({ kind: "visit", day: "2026-11-19", time: "14:00", minutes: 90, note: "dẫn xem căn mẫu", lead_id: "L1", place: "Sun Feliza" });
    const core: CalendarCreator = { key: "meeting", label: "Họp", kind: "meeting", tone: "purple", command: "schedule.create", preset: { kind: "meeting" },
      fields: { day: "day", start: "start", end: "end", title: "title", attendee: "attendee_emails" } };
    expect(createPayload(core, { day: "2026-11-19", start: 480, end: 510, title: "Họp đội", attendees: ["a@x", "b@x", "a@x"] }))
      .toEqual({ kind: "meeting", day: "2026-11-19", start: "08:00", end: "08:30", title: "Họp đội", attendee_emails: ["a@x", "b@x"] });
    expect(movePayload({ command: "crm.move_appointment", preset: { appointment_id: "A" }, fields: { day: "day", start: "time" } }, "2026-11-20", 600, 690))
      .toEqual({ appointment_id: "A", day: "2026-11-20", time: "10:00" });
  });

  it("lịch biểu bỏ ngày trống trừ hôm nay; tuần gộp mục nhiều làn", () => {
    const items = [it_("a", "2026-11-19T09:00+07:00", "2026-11-19T10:00+07:00", { lane: "u1" }), it_("a", "2026-11-19T09:00+07:00", "2026-11-19T10:00+07:00", { lane: "u2" })];
    expect(unique(items)).toHaveLength(1);
    expect(agendaDays(unique(items), ["2026-11-18", "2026-11-19", "2026-11-20"], "2026-11-18").map((g) => [g.day, g.items.length]))
      .toEqual([["2026-11-18", 0], ["2026-11-19", 1]]);
  });

  it("giải mã khối lịch dung thứ: chế độ lạ ⇒ Ngày, mục thiếu giờ bị bỏ, khoảng sai ⇒ khối lạ", () => {
    const [b, bad] = [
      { type: "calendar", view: "month", start: "2026-11-18", end: "2026-11-18", hours: [7, 20], items: [{ id: "a", start: "2026-11-18T09:00+07:00", end: "2026-11-18T10:00+07:00" }, { id: "b" }],
        create: { creators: [{ key: "m", command: "schedule.create", fields: { day: "day" } }, { key: "x" }], understand: "schedule.understand", me: "a@x" } },
      { type: "calendar", start: "2026-11-19", end: "2026-11-18" },
    ].map(decodeBlock);
    expect(b.type).toBe("calendar");
    if (b.type !== "calendar") return;
    expect(b.view).toBe("day");
    expect(b.items.map((i) => i.id)).toEqual(["a"]);
    expect(b.create?.creators.map((c) => c.key)).toEqual(["m"]);
    expect(bad.type).toBe("unknown");
  });
});
