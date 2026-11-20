import { describe, expect, it } from "vitest";
import { decodeBlock, decodePage } from "../decode";

describe("decode dung thứ", () => {
  it("khối lạ ⇒ unknown, không vỡ thẻ", () => {
    const p = decodePage({ kind: "card", title: "T", blocks: [{ type: "kpis", items: [] }, { type: "hologram", x: 1 }, null, 5, { type: "text", markdown: "a" }] });
    expect(p.kind).toBe("card");
    if (p.kind !== "card") return;
    expect(p.blocks.map((b) => b.type)).toEqual(["kpis", "unknown", "unknown", "unknown", "text"]);
  });
  it("trường thiếu có mặc định; trường thêm mới bị bỏ qua", () => {
    const b = decodeBlock({ type: "callout", title: "x", extra: "mới" });
    expect(b).toEqual({ type: "callout", tone: "muted", title: "x", text: "", facts: [], draft: "", actions: [] });
  });
  it("bảng: khoá dòng theo row_key, thiếu ⇒ số thứ tự", () => {
    const t = decodePage({ title: "b", columns: [], rows: [{ id: "a1" }, { name: "x" }], row_key: "id" });
    expect(t.kind).toBe("table");
    if (t.kind === "table") expect(t.rows.map((r) => r.id)).toEqual(["a1", "1"]);
  });
  it("audio: chỉ nhận đường /v1/ của core, link ngoài ⇒ unknown", () => {
    expect(decodeBlock({ type: "audio", title: "Ghi âm", src: "/v1/telephony/calls/abc-1/recording", duration_s: 90 }))
      .toEqual({ type: "audio", title: "Ghi âm", src: "/v1/telephony/calls/abc-1/recording", duration_s: 90 });
    expect(decodeBlock({ type: "audio", src: "https://evil.example/a.mp3" }).type).toBe("unknown");
    expect(decodeBlock({ type: "audio", src: "/v1/../x" }).type).toBe("unknown");
    expect(decodeBlock({ type: "audio", src: "/v1/a?y=1" }).type).toBe("unknown");
    expect(decodeBlock({ type: "audio", src: "/v1/a" })).toMatchObject({ title: "", duration_s: null });
  });
  it("khối tải sau: giữ truy vấn + đầu vào để thẻ tự tải; thiếu truy vấn ⇒ unknown", () => {
    expect(decodeBlock({ type: "later", query: "exec.overview_watch", input: { period: "month" }, title: "Việc cần nhìn" }))
      .toEqual({ type: "later", query: "exec.overview_watch", input: { period: "month" }, title: "Việc cần nhìn" });
    expect(decodeBlock({ type: "later", query: "x" })).toEqual({ type: "later", query: "x", input: {}, title: "" });
    expect(decodeBlock({ type: "later", input: {} }).type).toBe("unknown");
  });
  it("phần tải sau (ui.part) giữ khoá phần; trang phía server đọc paging, sai ⇒ bỏ", () => {
    expect(decodeBlock({ type: "later", query: "exec.monthly", input: {}, part: "trend", title: "Xu hướng" }))
      .toEqual({ type: "later", query: "exec.monthly", input: {}, part: "trend", title: "Xu hướng" });
    const t = decodePage({ title: "b", columns: [], rows: [{ id: "a" }], paging: { offset: 0, limit: 300, total: 2241, complete: true } });
    expect(t.kind === "table" && t.paging).toEqual({ offset: 0, limit: 300, total: 2241, complete: true });
    const bad = decodePage({ title: "b", columns: [], rows: [], paging: { offset: "x", limit: 300, total: 1 } });
    expect(bad.kind === "table" && bad.paging).toBe(undefined);
  });
  it("kind thiếu ⇒ bảng (như app Mac)", () => {
    expect(decodePage({ title: "b" }).kind).toBe("table");
  });
  it("board: move hợp lệ + form_columns; move thiếu ⇒ null", () => {
    const b = decodeBlock({ type: "board", columns: [{ key: "a", title: "A", items: [{ id: "1", title: "t", open: { query: "q", input: { x: 1 } } }] }], move: { command: "c", id_key: "task_id", column_key: "status", form_columns: ["blocked"] } });
    expect(b.type === "board" && b.move).toEqual({ command: "c", id_key: "task_id", column_key: "status", form_columns: ["blocked"] });
    expect(b.type === "board" && b.columns[0].items[0].open).toEqual({ query: "q", input: { x: 1 } });
    const b2 = decodeBlock({ type: "board", columns: [] });
    expect(b2.type === "board" && b2.move).toBeNull();
  });
  it("thread: tin nhắn và thẻ tham chiếu", () => {
    const b = decodeBlock({ type: "thread", subject_kind: "crm.lead", subject_id: "9", can_post: true, messages: [{ id: 1, author: "A", mine: false, body: "hi", at: "2026-10-31T15:22:03+07:00", mentions_me: true, card: { query: "q", input: {}, title: "T" } }] });
    expect(b.type).toBe("thread");
    if (b.type === "thread") {
      expect(b.messages[0].card?.title).toBe("T");
      expect(b.thread_id).toBeNull();
    }
  });
  it("thread: thẻ sống mang trạng thái + nút theo quyền người xem (0089); thiếu live ⇒ null", () => {
    const live = { gone: false, subtitle: "Chờ bạn duyệt", actions: [{ command: "core.decide", title: "Duyệt", style: "primary", preset: { approval_id: "a1", approve: true } }] };
    const b = decodeBlock({ type: "thread", subject_kind: "talk.room", subject_id: "r", messages: [
      { id: 1, author: "A", mine: false, body: "x", at: "", mentions_me: false, card: { query: "core.approval", input: { approval_id: "a1" }, title: "Phiếu", live } },
      { id: 2, author: "A", mine: false, body: "y", at: "", mentions_me: false, card: { query: "q", input: {}, title: "T" } },
    ] });
    if (b.type !== "thread") throw new Error("không phải thread");
    expect(b.messages[0].card?.live?.subtitle).toBe("Chờ bạn duyệt");
    expect(b.messages[0].card?.live?.actions[0]).toMatchObject({ command: "core.decide", title: "Duyệt", preset: { approve: true } });
    expect(b.messages[1].card?.live).toBeNull();
    expect(b.messages[0].card?.live?.headline).toBe("");                  // thẻ không khai tên theo người xem ⇒ trống, app giữ tên đã lưu
  });
  it("thread: thẻ cuộc gọi mang tên theo người xem (headline)", () => {
    const live = { gone: false, subtitle: "Đang chờ bạn vào", headline: "A gọi cho bạn", actions: [] };
    const b = decodeBlock({ type: "thread", subject_kind: "talk.direct", subject_id: "d", messages: [
      { id: 1, author: "A", mine: false, body: "A gọi B", at: "", mentions_me: true, card: { query: "meet.call", input: { call_id: "c" }, title: "Cuộc gọi: A gọi B", live } },
    ] });
    if (b.type !== "thread") throw new Error("không phải thread");
    expect(b.messages[0].card?.live).toMatchObject({ headline: "A gọi cho bạn", subtitle: "Đang chờ bạn vào" });
  });
  it("detail: ask chỉ khi có prompt", () => {
    const d = decodePage({ kind: "detail", title: "d", ask: { title: "Hỏi", prompt: "Về: " } });
    expect(d.kind === "detail" && d.ask).toEqual({ title: "Hỏi", prompt: "Về: " });
  });
  it("lưới thẻ (chợ gói): ô thiếu trường không vỡ, mở trang theo open", () => {
    const b = decodeBlock({ type: "tiles", title: "Ngành", items: [{ title: "Cho thuê", module: "hosting", badge: "Có sẵn", tone: "accent",
      open: { query: "admin.pack_info", input: { pack: "hosting" } } }, { title: 5 }] });
    expect(b.type).toBe("tiles");
    if (b.type !== "tiles") return;
    expect(b.items[0]).toMatchObject({ title: "Cho thuê", module: "hosting", open: { query: "admin.pack_info", input: { pack: "hosting" } } });
    expect(b.items[1]?.title).toBe("5");                                  // cùng luật str() của mọi khối
    expect(b.items[1]?.open).toBeUndefined();
  });
});

describe("thẻ gọn (0094)", () => {
  it("giữ phần compact; tổng không phải số ⇒ null (không đoán)", async () => {
    const { decodePage } = await import("../decode");
    const t = decodePage({ kind: "table", rows: [], columns: [], compact: { shown: 3, total: 28 } });
    expect(t.kind === "table" && t.compact).toEqual({ shown: 3, total: 28 });
    const c = decodePage({ kind: "card", blocks: [], compact: { shown: 5, total: "?" } });
    expect(c.kind === "card" && c.compact).toEqual({ shown: 5, total: null });
    const plain = decodePage({ kind: "table", rows: [], columns: [] });
    expect(plain.kind === "table" && plain.compact).toBeUndefined();
  });
});

describe("nguồn gốc con số (0097)", () => {
  it("ô số có drill hợp lệ ⇒ bấm được; thiếu / sai ⇒ không có", async () => {
    const { decodeKpi } = await import("../decode");
    expect(decodeKpi({ label: "Đã ký", value: 3, drill: { query: "deals.list", input: { status: "signed" }, sum: null } }).drill).toEqual({
      query: "deals.list",
      input: { status: "signed" },
      sum: null,
    });
    expect(decodeKpi({ label: "x", value: 1, drill: { input: {} } }).drill).toBeUndefined();
    expect(decodeKpi({ label: "x", value: 1 }).drill).toBeUndefined();
  });
});

describe("app tự ghép (0098)", () => {
  it("khối câu hỏi: bỏ câu không có lựa chọn, mặc định nút gửi", () => {
    const b = decodeBlock({ type: "choices", questions: [{ key: "ky", question: "Kỳ nào?", options: ["Tháng", "Năm"] }, { key: "x", question: "Trống?", options: [] }] });
    expect(b.type).toBe("choices");
    if (b.type !== "choices") return;
    expect(b.questions.map((q) => q.key)).toEqual(["ky"]);
    expect(b.questions[0].multi).toBe(false);
    expect(b.submit).toBe("Gửi");
  });

  it("tab: dưới hai tab thì bỏ; bảng trong thẻ mang nút dòng + mở chi tiết", () => {
    const one = decodePage({ kind: "card", blocks: [], tabs: { param: "page", items: [{ key: "a", title: "A" }] } });
    expect(one.kind === "card" && one.tabs).toBeFalsy();
    const two = decodePage({ kind: "card", tabs: { param: "page", items: [{ key: "a", title: "A" }, { key: "b", title: "B" }], current: "b" },
      blocks: [{ type: "table", page: { rows: [{ id: 1 }], columns: [], row_actions: [{ command: "x.y", title: "Làm" }], open: { query: "x.z", bind: { id: "id" } } } }] });
    expect(two.kind === "card" && two.tabs?.current).toBe("b");
    const t = two.kind === "card" ? two.blocks[0] : null;
    expect(t?.type === "table" && t.page.row_actions?.[0].command).toBe("x.y");
    expect(t?.type === "table" && t.page.open?.query).toBe("x.z");
  });
});

describe("sửa một khối (0098)", () => {
  it("khối và ô số mang nút sửa; thiếu / hỏng thì không có", () => {
    const edit = { command: "apps.revise_block", title: "Sửa khối này", preset: { app_id: "a", page: "main", index: 2 } };
    const chart = decodeBlock({ type: "chart", kind: "bar", labels: [], series: [], edit });
    expect(chart.edit?.preset).toEqual({ app_id: "a", page: "main", index: 2 });
    const kpis = decodeBlock({ type: "kpis", items: [{ label: "A", value: 1, edit }, { label: "B", value: 2, edit: "x" }] });
    expect(kpis.type === "kpis" && kpis.items.map((k) => !!k.edit)).toEqual([true, false]);
  });
});

describe("xem bản gốc + bảng thật (0102)", () => {
  it("original / text_role / section.table; thiếu ⇒ null", () => {
    const p = decodePage({
      kind: "detail", title: "Hợp đồng", style: "document", text_role: "extracted",
      original: { title: "Bản gốc", path: "/v1/kb/documents/x/preview", filename: "a.pdf", page: 3, kind: "pdf" },
      sections: [{ title: "Phí", fields: [], table: { columns: ["Căn", "Phí"], rows: [["A1", "10"]], total: ["Cộng", "10"] } }, { title: "Ghi chú", text: "x" }],
    });
    expect(p.kind).toBe("detail");
    if (p.kind !== "detail") return;
    expect(p.original).toEqual({ title: "Bản gốc", path: "/v1/kb/documents/x/preview", filename: "a.pdf", page: 3 });
    expect(p.textRole).toBe("extracted");
    expect(p.sections[0].table).toEqual({ columns: ["Căn", "Phí"], rows: [["A1", "10"]], total: ["Cộng", "10"] });
    expect(p.sections[1].table).toBeNull();
    const q = decodePage({ kind: "detail", title: "T", sections: [], original: { title: "x" } });
    if (q.kind !== "detail") return;
    expect(q.original).toBeNull();                                                 // thiếu path ⇒ không có bản gốc
    expect(q.textRole).toBeNull();
  });
});

describe("nguồn gốc của số trên khối (0220)", () => {
  it("giải mã source và bỏ khi thiếu nhãn", async () => {
    const { decodeBlock, decodeSource } = await import("../decode");
    const b = decodeBlock({ type: "kpis", items: [], source: { kind: "dataset", label: "Chi phí quảng cáo", scope: "cả công ty", through: "2026-11-13", warn: "Số đủ tới 13/11/2026" } });
    expect(b.source).toEqual({ kind: "dataset", label: "Chi phí quảng cáo", scope: "cả công ty", through: "2026-11-13", reliableFrom: "", warn: "Số đủ tới 13/11/2026" });
    expect(decodeSource({ kind: "dataset" })).toBeUndefined();
    expect(decodeBlock({ type: "kpis", items: [] }).source).toBeUndefined();
  });
  it("một dòng nguồn: bỏ phần không khai, ghi rõ SQL tự do", async () => {
    const { sourceText } = await import("../source");
    const d = (iso: string) => iso.split("-").reverse().join("/");
    expect(sourceText({ kind: "dataset", label: "Lead", scope: "cả công ty", through: "2026-11-13", reliableFrom: "", warn: "" }, d))
      .toBe("Nguồn: Lead · số cả công ty · đủ tới 13/11/2026");
    expect(sourceText({ kind: "sql", label: "Câu hỏi riêng", scope: "", through: "", reliableFrom: "", warn: "" }, d)).toBe("Nguồn: Câu hỏi riêng (SQL tự do)");
  });
  it("vì sao (0220 lát 7): câu hỏi điền sẵn của ô số và biểu đồ — chỉ nhận chuỗi", () => {
    const k = decodeBlock({ type: "kpis", items: [{ label: "Số lead", value: 3, why: "Vì sao số lead thay đổi?" }, { label: "x", value: 1, why: 5 }] });
    expect(k.type === "kpis" && k.items.map((i) => i.why)).toEqual(["Vì sao số lead thay đổi?", undefined]);
    const c = decodeBlock({ type: "chart", kind: "bar", labels: ["a"], series: [{ name: "s", values: [1] }], why: "Vì sao?" });
    expect(c.why).toBe("Vì sao?");
  });
});
