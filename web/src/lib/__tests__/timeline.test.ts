import { describe, expect, it } from "vitest";
import { decodeBlock, decodeTable, decodeTimeline } from "@/lib/decode";
import { arrows, barBox, flatten, itemLabel, rangeOf, ROW_H, weekTicks, xOf } from "@/lib/timeline";

const raw = {
  type: "timeline",
  title: "Dòng thời gian",
  today: "2026-11-16",
  start: "2026-11-09",
  end: "2026-12-06",
  milestones: [{ on: "2026-12-04", title: "Mốc kế hoạch", tone: "accent" }, { on: "bad-date", title: "x" }],
  off: [{ from: "2026-11-14", to: "2026-11-15", kind: "weekend" }],
  note: "",
  groups: [
    {
      key: "g0",
      title: "Chuẩn bị",
      subtitle: "giai đoạn",
      items: [
        { id: "a", title: "Chốt key visual", start: "2026-11-10", end: "2026-11-13", kind: "bar", tone: "accent", status: "Đang làm", who: "Hạnh", dates: "10/11 – 13/11", after: [] },
        { id: "b", title: "Landing page", start: "2026-11-16", end: "2026-11-20", kind: "bar", tone: "muted", status: "Cần làm", who: "Bảo", dates: "16/11 – 20/11", flag: "Bắt đầu sớm", after: ["a"] },
        { id: "c", title: "Chưa hạn", start: "2026-11-17", end: null, kind: "bar", tone: "muted", status: "Cần làm", who: "", dates: "từ 17/11", after: [] },
        { id: "bad", title: "Hỏng ngày", start: "xx", end: null },
      ],
    },
    { key: "g1", title: "Chạy", collapsed: true, start: "2026-11-23", end: "2026-12-04", items: [{ id: "d", title: "Quảng cáo", start: "2026-11-23", end: "2026-12-04", after: ["b"] }] },
  ],
};

describe("dòng thời gian (0222)", () => {
  it("giải mã: bỏ dòng / mốc sai ngày, trường thiếu có mặc định", () => {
    const t = decodeTimeline(raw)!;
    expect(t.groups.map((g) => g.items.length)).toEqual([3, 1]);
    expect(t.milestones).toHaveLength(1);
    expect(t.groups[0].items[2].end).toBeNull();
    expect(t.groups[1].collapsed).toBe(true);
    expect(t.groups[1].items[0].tone).toBe("muted");
    expect(decodeTimeline({ ...raw, start: "2026-12-10" })).toBeNull();
    expect(decodeBlock(raw)).toMatchObject({ type: "timeline" });
    expect(decodeBlock({ type: "timeline" })).toEqual({ type: "unknown" });
  });

  it("là góc nhìn của bảng khi server khai", () => {
    const t = decodeTable({ title: "", kpis: [], columns: [], rows: [], empty: "", note: "", views: { timeline: raw } });
    expect(t.views!.timeline!.groups).toHaveLength(2);
    expect(decodeTable({ title: "", kpis: [], columns: [], rows: [], empty: "", note: "", views: { calendar: { date: "d", title: "t" } } }).views!.timeline).toBeNull();
  });

  it("vị trí: thanh gồm cả ngày hạn, chưa hạn = một ngày, kẹp trong khoảng", () => {
    const r = rangeOf(decodeTimeline(raw)!);
    expect(r.days).toBe(28);
    expect(xOf(r, "2026-11-09")).toBe(0);
    expect(barBox(r, "2026-11-10", "2026-11-13").width).toBe(4 * r.px);
    expect(barBox(r, "2026-11-17", null).width).toBe(r.px);
    expect(xOf(r, "2027-01-01")).toBe(r.width);
  });

  it("hàng: nhóm gấp chỉ còn tiêu đề; mũi tên chỉ giữa các dòng đang hiện", () => {
    const t = decodeTimeline(raw)!;
    const r = rangeOf(t);
    const closed = flatten(t.groups, {});
    expect(closed.map((x) => x.kind)).toEqual(["group", "item", "item", "item", "group"]);
    expect(arrows(closed, r)).toHaveLength(1);   // a → b; d chờ b nhưng nhóm Chạy đang gấp
    const open = flatten(t.groups, { g1: true });
    expect(arrows(open, r)).toHaveLength(2);
    expect(open.at(-1)!.y).toBe((open.length - 1) * ROW_H);
  });

  it("trục có vạch thứ Hai; câu đọc cho trình đọc màn hình đủ người, ngày, trạng thái, cảnh báo", () => {
    expect(weekTicks(rangeOf(decodeTimeline(raw)!))[0].label).toBe("9/11");
    const b = decodeTimeline(raw)!.groups[0].items[1];
    expect(itemLabel(b)).toBe("Landing page, Bảo, 16/11 – 20/11, Cần làm, Bắt đầu sớm");
  });
});
