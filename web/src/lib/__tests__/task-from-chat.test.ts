import { describe, expect, it } from "vitest";
import { decodeAction, decodeBlock } from "../decode";
import { initialValues, optionsInput } from "../form";
import type { FieldSpec, ThreadBlockData } from "../types";

// Tạo việc từ một tin (0115 / 0117, ghi chú 07/11): nút + dấu theo tin do server gửi, ô điền sẵn `values`, danh sách chọn kèm `options_with`.

describe("tin nhắn: nút và dấu do gói khác góp", () => {
  it("giải mã nút + dấu; thiếu trường ⇒ rỗng (server cũ)", () => {
    const b = decodeBlock({
      type: "thread",
      messages: [
        {
          id: 7,
          body: "Gọi lại khách",
          actions: [{ command: "flow.task_from_message", title: "Tạo việc", preset: { message_id: 7 }, values: { title: "Gọi lại khách" } }, { title: "hỏng" }],
          marks: [{ title: "Đã tạo việc", detail: "Gọi lại khách · Cần làm", tone: "accent", open: { query: "flow.task", input: { task_id: "t" } } }, { detail: "không tên" }],
        },
        { id: 8, body: "cũ" },
      ],
    }) as ThreadBlockData & { type: string };
    const [m, old] = b.messages;
    expect(m.actions.map((a) => [a.command, a.preset, a.values])).toEqual([["flow.task_from_message", { message_id: 7 }, { title: "Gọi lại khách" }]]);
    expect(m.marks).toEqual([{ title: "Đã tạo việc", detail: "Gọi lại khách · Cần làm", tone: "accent", open: { query: "flow.task", input: { task_id: "t" } } }]);
    expect([old.actions, old.marks]).toEqual([[], []]);
  });
});

describe("form mở từ nút có ô điền sẵn", () => {
  it("`values` vào giá trị ban đầu (sửa được); nút không có `values` giữ như cũ", () => {
    const a = decodeAction({ command: "flow.task_from_message", preset: { message_id: 7 }, values: { title: "Gọi lại" } });
    const fields: FieldSpec[] = [{ key: "title", label: "Việc", kind: "text", required: true, help: "", hidden: false }];
    expect(initialValues(fields, a.preset, { ...(a.values ?? {}) })).toEqual({ message_id: 7, title: "Gọi lại" });
    expect(decodeAction({ command: "x" }).values).toBeUndefined();
    // đường riêng của gói (chấm công): chỉ nhận dạng /v1/<gói>/<…>; dạng lạ bỏ qua ⇒ đường lệnh chung
    expect(decodeAction({ command: "timekeeping.punch", route: "/v1/timekeeping/punch" }).route).toBe("/v1/timekeeping/punch");
    expect(decodeAction({ command: "x", route: "https://evil.example/x" }).route).toBeUndefined();
    expect(decodeAction({ command: "x", route: "/v1/../auth/logout" }).route).toBeUndefined();
  });
  it("danh sách chọn kèm đúng trường khai `options_with`, bỏ trường trống", () => {
    const f: FieldSpec = { key: "assignee", label: "Giao cho", kind: "text", required: false, help: "", hidden: false,
      options_query: "flow.message_assignee_options", options_with: ["message_id", "khong_co"] };
    expect(optionsInput(f, { message_id: 7, title: "x" })).toEqual({ message_id: "7" });
    expect(optionsInput({ ...f, options_with: undefined }, { message_id: 7 })).toEqual({});
  });
});
