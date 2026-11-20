import { describe, expect, it } from "vitest";
import { talkUploadForm } from "../api";
import { decodeBlock } from "../decode";
import { connectAction, SseParser, signalAction, signalThreads } from "../sse";
import { decodeInbox, fileSize, jumpTarget, mergeOlder, splitInbox, talkFileUrl, talkItemFor } from "../talk";

// Trao đổi dùng hằng ngày (0115): giải mã dung thứ các trường mới, kênh báo tức thì, tách hộp tin, tin cũ tải thêm.

describe("kênh báo tức thì (SSE)", () => {
  it("ghép khúc bị cắt giữa dòng; bỏ dòng giữ kết nối; đọc mã luồng", () => {
    const p = new SseParser();
    expect(p.push("event: chan")).toEqual([]);
    const evs = p.push('ged\ndata: {"threads": ["a", "b"]}\n\n: ping\n\nevent: gone\ndata: {}\n\n');
    expect(evs.map((e) => e.event)).toEqual(["changed", "gone"]);
    expect(signalThreads(evs[0].data)).toEqual(["a", "b"]);
    expect(signalThreads(evs[1].data)).toEqual([]);
  });
  it("data hỏng ⇒ coi như rỗng, không vỡ", () => {
    const [e] = new SseParser().push("event: changed\ndata: {hỏng\n\n");
    expect(e).toEqual({ event: "changed", data: {} });
    expect(signalThreads({ threads: [1, "x", null] })).toEqual(["x"]);
  });
});

describe("gửi tệp: chú thích trong thân, không trên URL", () => {
  it("multipart có đủ trường, tệp cuối cùng", () => {
    const f = talkUploadForm(new File([new Uint8Array([137, 80])], "ảnh.png"), { subject_kind: "talk.room", subject_id: "r", body: "chú thích", reply_to: 7 });
    expect([...f.keys()]).toEqual(["subject_kind", "subject_id", "body", "reply_to", "file"]);
    expect(f.get("body")).toBe("chú thích");
    expect((f.get("file") as File).name).toBe("ảnh.png");
    expect([...talkUploadForm(new File([], "a.png"), { subject_kind: "k", subject_id: "s" }).keys()]).toEqual(["subject_kind", "subject_id", "file"]);
  });
});

describe("kênh tức thì: chỉ gone là mất quyền", () => {
  it("sự kiện ⇒ việc của app", () => {
    expect(["changed", "gone", "auth", "retry", "lạ"].map(signalAction)).toEqual(["changed", "gone", "auth", "retry", "ignore"]);
  });
  it("mã khi nối ⇒ việc của app (503 / mạng lỗi là thử lại, không phải mất quyền)", () => {
    expect([200, 404, 401, 503, 500, 0].map(connectAction)).toEqual(["ok", "gone", "auth", "retry", "retry", "retry"]);
  });
});

describe("khối trao đổi: trường mới", () => {
  it("server cũ (không có trường mới) ⇒ mặc định an toàn", () => {
    const b = decodeBlock({ type: "thread", subject_kind: "crm.lead", subject_id: "s", messages: [{ id: 1, author: "An", body: "chào" }] });
    if (b.type !== "thread") throw new Error("không phải khối trao đổi");
    expect(b.messages[0]).toMatchObject({ edited: false, recalled: false, attachments: [], reactions: [], reply: null, history: null, can_edit: false });
    expect(b).toMatchObject({ has_more: false, pinned: [], reaction_choices: [], assist: [], can_attach: false, focus_id: null });
  });
  it("tệp, trả lời, cảm xúc, lịch sử, ghim, nút tóm tắt", () => {
    const b = decodeBlock({
      type: "thread", subject_kind: "talk.direct", subject_id: "d", has_more: true, focus_id: 7, muted: true,
      reaction_choices: ["👍", "✅"], assist: [{ title: "Tóm tắt cho tôi", prompt: "Tóm tắt…" }, { title: "" }],
      pinned: [{ id: 3, author: "Nam", body: "Lịch trực", pinned_by: "Nam", at: "x" }],
      messages: [{
        id: 7, body: "", recalled: true, reply: { id: 3, author: "Nam", body: "Lịch trực", recalled: false },
        attachments: [{ id: "f1", filename: "a.png", content_type: "image/png", size_bytes: 10, is_image: true }, { filename: "không mã" }],
        reactions: [{ emoji: "👍", count: 2, mine: true, names: ["An", "Bích"] }, { emoji: "✅", count: 0 }],
        history: { query: "talk.message_history", input: { message_id: 7 } },
      }],
    });
    if (b.type !== "thread") throw new Error("không phải khối trao đổi");
    const m = b.messages[0];
    expect(m.attachments.map((f) => f.id)).toEqual(["f1"]);
    expect(m.reactions).toEqual([{ emoji: "👍", count: 2, mine: true, names: ["An", "Bích"] }]);
    expect(m.reply?.author).toBe("Nam");
    expect(m.history?.query).toBe("talk.message_history");
    expect(b.assist).toHaveLength(1);
    expect(b.pinned[0].id).toBe(3);
    expect([b.has_more, b.focus_id, b.muted]).toEqual([true, 7, true]);
  });
});

describe("hộp tin: tin riêng / phòng / hồ sơ", () => {
  it("tách theo `section` của server", () => {
    const d = decodeInbox({
      items: [
        { thread_id: "1", subject_kind: "talk.direct", section: "direct", title: "Bích", muted: true },
        { thread_id: "2", subject_kind: "talk.room", section: "room", title: "Phòng A" },
        { thread_id: "3", subject_kind: "crm.lead", section: "record", title: "Khách" },
      ],
      can_start_direct: true,
    });
    const { directs, rooms, records } = splitInbox(d.items);
    expect([directs.map((x) => x.title), rooms.map((x) => x.title), records.map((x) => x.title)]).toEqual([["Bích"], ["Phòng A"], ["Khách"]]);
    expect(directs[0].muted).toBe(true);
    expect(d.can_start_direct).toBe(true);
  });
  it("dòng tạm cho luồng mở từ thông báo", () => {
    expect(talkItemFor("talk.direct", "d1", "Bích")).toMatchObject({ subject_kind: "talk.direct", subject_id: "d1", title: "Bích", unread: 0 });
  });
  it("cỡ tệp, đường tải tệp, ghép tin cũ không trùng", () => {
    expect(fileSize(900)).toBe("1 KB");
    expect(fileSize(3_565_158)).toBe("3,4 MB");
    expect(talkFileUrl("a b")).toBe("/api/core/v1/talk/files/a%20b");
    expect(mergeOlder([{ id: 1 }, { id: 2 }], [{ id: 2 }, { id: 3 }]).map((m) => m.id)).toEqual([1, 2, 3]);
  });
});

describe("tab Tệp của trao đổi (0128)", () => {
  it("khối trao đổi: `files` là tham chiếu trang; server cũ không gửi ⇒ null", () => {
    const old = decodeBlock({ type: "thread", subject_kind: "talk.room", subject_id: "r", messages: [] });
    const now = decodeBlock({ type: "thread", subject_kind: "talk.room", subject_id: "r", messages: [], files: { title: "Tệp", query: "talk.files", input: { thread_id: "t" } } });
    if (old.type !== "thread" || now.type !== "thread") throw new Error("không phải khối trao đổi");
    expect(old.files).toBeNull();
    expect(now.files).toEqual({ title: "Tệp", query: "talk.files", input: { thread_id: "t" } });
  });
  it("dòng danh sách mang tệp (ảnh thu nhỏ qua đường tệp sẵn có); thiếu mã ⇒ bỏ", () => {
    const b = decodeBlock({ type: "list", items: [
      { title: "a.png", file: { id: "f1", filename: "a.png", content_type: "image/png", size_bytes: 9, is_image: true } },
      { title: "b.pdf", file: { filename: "không mã" } },
      { title: "c" },
    ] });
    if (b.type !== "list") throw new Error("không phải danh sách");
    expect(b.items[0].file).toEqual({ id: "f1", filename: "a.png", content_type: "image/png", size_bytes: 9, is_image: true });
    expect(b.items[1].file).toBeUndefined();
    expect(talkFileUrl(b.items[0].file!.id)).toBe("/api/core/v1/talk/files/f1");
  });
  it("bấm dòng: về đúng tin của luồng, còn lại mở trang", () => {
    expect(jumpTarget({ query: "talk.thread", input: { subject_kind: "talk.room", subject_id: "r", around: 42 } })).toBe(42);
    expect(jumpTarget({ query: "talk.thread", input: { around: "42" } })).toBeNull();
    expect(jumpTarget({ query: "kb.document", input: { document_id: "d" } })).toBeNull();
    expect(jumpTarget(undefined)).toBeNull();
  });
});
