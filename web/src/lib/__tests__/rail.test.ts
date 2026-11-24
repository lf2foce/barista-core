import { describe, expect, it } from "vitest";
import { railCurrent, railLayout, railWorkspaceKeys } from "../rail";
import type { Manifest, WorkspaceSpec } from "../types";

// Thanh bên trái theo vai (ADR 0255). Khoá phân hệ ở đây là khoá GIẢ — lõi và web không biết phân hệ nào của gói ngành nào.

const ws = (key: string, extra: Partial<WorkspaceSpec> = {}): WorkspaceSpec => ({ key, title: `Phân hệ ${key}`, icon: "square.grid.2x2", screens: [], ...extra });
const manifest = (workspaces: WorkspaceSpec[], rail?: { role: string[]; pinned: string[] }): Manifest => ({
  user: { id: "u", name: "A", email: "a@x.demo", role: "r", role_label: "Vai", team: null },
  tenant: { id: "t", name: "T" }, workspaces, inbox: null, pins: [], ...(rail ? { rail } : {}),
});
const all = [ws("a"), ws("b"), ws("c"), ws("d"), ws("e")];

describe("bố cục thanh bên theo manifest", () => {
  it("không có rail (vai chưa có mặc định) ⇒ vẽ hết như trước, không nhóm", () => {
    const l = railLayout(manifest(all));
    expect(l.grouped).toBe(false);
    expect(l.all.map((i) => i.ws.key)).toEqual(["a", "b", "c", "d", "e"]);
    expect(railWorkspaceKeys(manifest(all))).toEqual(["a", "b", "c", "d", "e"]);
  });

  it("có rail ⇒ Theo vai rồi Đã ghim, phần còn lại chỉ ở Tất cả mục — mỗi phân hệ một chỗ", () => {
    const l = railLayout(manifest(all, { role: ["b", "a"], pinned: ["d"] }));
    expect(l.grouped).toBe(true);
    expect(l.role.map((w) => w.key)).toEqual(["b", "a"]);
    expect(l.pinned.map((w) => w.key)).toEqual(["d"]);
    expect(l.all.map((i) => [i.ws.key, i.place])).toEqual([["a", "role"], ["b", "role"], ["c", null], ["d", "pinned"], ["e", null]]);
    expect(railWorkspaceKeys(manifest(all, { role: ["b", "a"], pinned: ["d"] }))).toEqual(["b", "a", "d"]);
  });

  it("không lặp: phân hệ đã ở Theo vai không hiện lại ở Đã ghim, khoá trùng chỉ giữ lần đầu", () => {
    const l = railLayout(manifest(all, { role: ["a", "b", "a"], pinned: ["b", "c", "c"] }));
    expect(l.role.map((w) => w.key)).toEqual(["a", "b"]);
    expect(l.pinned.map((w) => w.key)).toEqual(["c"]);
    const shown = [...l.role, ...l.pinned].map((w) => w.key);
    expect(new Set(shown).size).toBe(shown.length);
  });

  it("lọc theo quyền: khoá không còn trong workspaces (mất quyền / gói tắt) bị bỏ", () => {
    const l = railLayout(manifest([ws("a"), ws("c")], { role: ["a", "b"], pinned: ["d", "c"] }));
    expect(l.role.map((w) => w.key)).toEqual(["a"]);
    expect(l.pinned.map((w) => w.key)).toEqual(["c"]);
  });

  it("mặc định của vai lọc hết ⇒ không để thanh bên trống: vẽ hết như chưa khai", () => {
    const l = railLayout(manifest(all, { role: ["x", "y"], pinned: ["c"] }));
    expect(l.grouped).toBe(false);
    expect(l.all).toHaveLength(5);
  });

  it("ghim ghi nhận kể cả khi phân hệ đã ở Theo vai (hiện một lần, panel vẫn bấm bỏ ghim được)", () => {
    const l = railLayout(manifest([ws("a", { pinned: true }), ws("b")], { role: ["a"], pinned: [] }));
    expect(l.pinned).toEqual([]);
    expect(l.all.find((i) => i.ws.key === "a")).toMatchObject({ place: "role", pinned: true });
    expect(l.all.find((i) => i.ws.key === "b")).toMatchObject({ place: null, pinned: false });
  });

  it("phân hệ cố định (fixed) luôn có chỗ riêng cho mọi vai — không lặp trong Theo vai / Đã ghim, phím tắt ở cuối", () => {
    const spaces = [...all, ws("hub", { fixed: true })];
    const grouped = railLayout(manifest(spaces, { role: ["hub", "a"], pinned: ["hub", "b"] }));
    expect(grouped.fixed.map((w) => w.key)).toEqual(["hub"]);
    expect(grouped.role.map((w) => w.key)).toEqual(["a"]);
    expect(grouped.pinned.map((w) => w.key)).toEqual(["b"]);
    expect(railWorkspaceKeys(manifest(spaces, { role: ["a"], pinned: [] }))).toEqual(["a", "hub"]);
    expect(railLayout(manifest(spaces)).fixed.map((w) => w.key)).toEqual(["hub"]);              // vai không có mặc định: vẫn có chỗ riêng
    expect(railWorkspaceKeys(manifest(spaces))).toEqual(["a", "b", "c", "d", "e", "hub"]);
  });

  it("chưa có manifest ⇒ rỗng, không lỗi", () => {
    expect(railLayout(null)).toEqual({ grouped: false, role: [], pinned: [], fixed: [], all: [] });
  });
});

describe("phân hệ đang mở", () => {
  const l = railLayout(manifest(all, { role: ["a"], pinned: ["d"] }));
  it("mở từ Tất cả mục ⇒ vẫn có một mục sáng, báo chưa nằm ở nhóm nào", () => {
    expect(railCurrent(l, "c")).toMatchObject({ onRail: false });
    expect(railCurrent(l, "c")?.ws.key).toBe("c");
  });
  it("đang ở phân hệ có trên thanh bên ⇒ onRail (màn hẹp vẫn cần mục này)", () => {
    expect(railCurrent(l, "a")).toMatchObject({ onRail: true });
    expect(railCurrent(l, "d")).toMatchObject({ onRail: true });
  });
  it("không ở phân hệ / vẽ hết ⇒ null", () => {
    expect(railCurrent(l, null)).toBeNull();
    expect(railCurrent(l, "zzz")).toBeNull();
    expect(railCurrent(railLayout(manifest(all)), "a")).toBeNull();
  });
});
