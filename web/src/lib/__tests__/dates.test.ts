import { describe, expect, it } from "vitest";
import { addMonths, daysIn, hhmm, iso, isoMonth, monthGrid, parseClock, parseDay, parseMonth, vn, vnMonth } from "../dates";

describe("ô chọn ngày", () => {
  it("đọc ISO và dd/mm/yyyy, bỏ ngày không có thật", () => {
    expect(parseDay("2026-11-05")).toEqual({ y: 2026, m: 11, d: 5 });
    expect(parseDay("2026-11-05T10:00:00+07:00")).toEqual({ y: 2026, m: 11, d: 5 });
    expect(parseDay("5/11/2026")).toEqual({ y: 2026, m: 11, d: 5 });
    expect(parseDay("31/02/2026")).toBeNull();
    expect(parseDay("")).toBeNull();
    expect(parseDay(20261105)).toBeNull();
  });
  it("in ra ISO (gửi lõi) và dd/mm/yyyy (hiện cho người)", () => {
    const d = { y: 2026, m: 3, d: 7 };
    expect([iso(d), vn(d)]).toEqual(["2026-03-07", "07/03/2026"]);
  });
  it("tháng nhuận, qua năm", () => {
    expect([daysIn(2028, 2), daysIn(2026, 2), daysIn(2026, 12)]).toEqual([29, 28, 31]);
    expect(addMonths(2026, 1, -1)).toEqual({ y: 2025, m: 12 });
    expect(addMonths(2026, 12, 1)).toEqual({ y: 2027, m: 1 });
  });
  it("lưới 6 tuần bắt đầu thứ Hai", () => {
    const g = monthGrid(2026, 11);   // 01/11/2026 là Chủ nhật ⇒ 6 ô tháng 10 trước
    expect(g).toHaveLength(42);
    expect(g.slice(0, 7).filter((c) => c.out)).toHaveLength(6);
    expect(g[6]).toMatchObject({ y: 2026, m: 11, d: 1, out: false });
    expect(g[0]).toMatchObject({ y: 2026, m: 10, d: 26, out: true });
    expect(g.filter((c) => !c.out)).toHaveLength(30);
  });
});

describe("ô chọn giờ", () => {
  it("đọc các cách người ta gõ giờ, 24 giờ", () => {
    expect(parseClock("14:30")).toEqual({ h: 14, m: 30 });
    expect(parseClock("14h30")).toEqual({ h: 14, m: 30 });
    expect(parseClock("14.5")).toEqual({ h: 14, m: 5 });
    expect(parseClock("1430")).toEqual({ h: 14, m: 30 });
    expect(parseClock("9")).toEqual({ h: 9, m: 0 });
    expect(parseClock("9h")).toEqual({ h: 9, m: 0 });
    expect(parseClock(" 08:05 ")).toEqual({ h: 8, m: 5 });
  });
  it("giờ không có thật ⇒ null; in ra HH:MM", () => {
    for (const bad of ["24:00", "12:60", "25", "abc", "", "12:3x", 1430]) expect(parseClock(bad)).toBeNull();
    expect(hhmm({ h: 7, m: 5 })).toBe("07:05");
  });
});

describe("ô chọn tháng", () => {
  it("đọc yyyy-mm, ngày ISO, 11/2026, Tháng 11/2026", () => {
    for (const ok of ["2026-11", "2026-11-05", "11/2026", "Tháng 11/2026", "tháng 11/2026"]) expect(parseMonth(ok)).toEqual({ y: 2026, m: 11 });
    for (const bad of ["2026-13", "2026-00", "13/2026", "2026", "", null]) expect(parseMonth(bad)).toBeNull();
  });
  it("gửi lõi yyyy-mm, hiện Tháng m/yyyy", () => {
    expect([isoMonth({ y: 2026, m: 3 }), vnMonth({ y: 2026, m: 3 })]).toEqual(["2026-03", "Tháng 3/2026"]);
  });
});
