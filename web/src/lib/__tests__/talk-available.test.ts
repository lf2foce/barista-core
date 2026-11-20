import { describe, expect, it } from "vitest";
import { talkAvailable } from "../talk";
import type { Manifest } from "../types";

// Hộp tin chỉ đọc khi core nói người này chạy được `talk.inbox` (gói Trao đổi bật + có quyền) — trước đây gọi mỗi lần tải / có tín hiệu và nhận 404.

const base: Manifest = { user: { id: "u", name: "A", email: "a@x.demo", role: "edu_teacher", role_label: "Giáo viên", team: null },
  tenant: { id: "t", name: "T" }, workspaces: [], inbox: null, pins: [] };

describe("hộp tin theo manifest", () => {
  it("chỉ đọc khi có talk.inbox trong danh sách truy vấn", () => {
    expect(talkAvailable({ ...base, queries: ["edu.today", "talk.inbox"] })).toBe(true);
    expect(talkAvailable({ ...base, queries: ["edu.today"] })).toBe(false);
    expect(talkAvailable({ ...base, queries: [] })).toBe(false);
  });
  it("chưa có manifest thì không đọc; core cũ không gửi danh sách thì đọc như trước", () => {
    expect(talkAvailable(null)).toBe(false);
    expect(talkAvailable(base)).toBe(true);
  });
});
