import { describe, expect, it } from "vitest";
import { decodeDirect } from "../call";
import type { Snapshot } from "../call-engine";
import { CallHub, overlayConflict, parseCallPage, standalonePage, startsCall, undoOf } from "../call-hub";
import { CallSession, type EngineLike, type Entry, type Member } from "../call-session";

// Bảng gọi nổi (0114, ghi chú 07/11): máy trạng thái của phiên gọi giữ ở CallHub (ngoài cây trang). Bộ máy WebRTC và đường truyền được thay bằng giả —
// nơi chạy test là node, không có RTCPeerConnection / micro; phần WebRTC thật vẫn do call-engine.ts và kịch bản trình duyệt `scripts/call-check.mjs`.

const A = "0b9d6f0e-7a51-4b6c-9d0e-2b8f3c1d4e5f";
const B = "1c0e7a1f-8b62-4c7d-8e1f-3c9a4d2e5f60";
const flush = () => new Promise<void>((r) => setTimeout(r, 0));

const idle: Snapshot = {
  phase: "idle", peerHere: false, peerLeft: false, noAnswer: false, muted: false, listenOnly: false, camera: false, remoteVideo: false, signalUp: false,
  otherWindow: false, error: null, mediaCode: null, connectedAt: null, waitingAt: null, mics: [], micId: "", cams: [], camId: "", deviceError: null,
  turn: false, relay: false,
};

class FakeEngine implements EngineLike {
  snap: Snapshot = { ...idle };
  remote = {} as MediaStream;
  cameraTrack = null;
  started = 0;
  hung = 0;
  closed: string[] = [];
  private fns = new Set<(s: Snapshot) => void>();
  subscribe(fn: (s: Snapshot) => void) {
    this.fns.add(fn);
    fn(this.snap);
    return () => this.fns.delete(fn);
  }
  set(p: Partial<Snapshot>) {
    this.snap = { ...this.snap, ...p };
    for (const fn of this.fns) fn(this.snap);
  }
  /** chờ người cho phép micro: start treo tới khi gọi `allow()` */
  gate: Promise<void> | null = null;
  allow = () => undefined as void;
  hold() {
    this.gate = new Promise<void>((r) => (this.allow = r));
  }
  async start() {
    this.started += 1;
    this.set({ phase: "media" });
    if (this.gate) await this.gate;
    if (this.snap.phase === "closed" || this.snap.phase === "ended") return false;   // như CallEngine: đóng rồi thì không vào
    this.set({ phase: "waiting" });
    return true;
  }
  async hangup() {
    this.hung += 1;
    this.close("closed");
  }
  close(phase: "ended" | "closed") {
    this.closed.push(phase);
    this.set({ phase });
  }
  toggleMute() {}
  async toggleCamera() {}
  async replaceMic() {}
  async replaceCamera() {}
  reconnectNow() {}
}

function rig(opts: { active?: boolean; hold?: string[] } = {}) {
  const engines = new Map<string, FakeEngine>();
  const joined: string[] = [];
  const ended: string[] = [];
  const left: string[] = [];
  const undone: string[] = [];
  const opened: string[] = [];
  let allowWindow = true;
  const hub = new CallHub({
    makeSession: (id: string, entry: Entry) => {
      const engine = new FakeEngine();
      if (opts.hold?.includes(id)) engine.hold();
      engines.set(id, engine);
      const t = {
        state: async () => decodeDirect({ call_id: id, title: "A gọi B", active: opts.active ?? !ended.includes(id), seat: "a", polite: false, peer: { name: "Bình", outsider: false }, cursor: 0 }),
        send: async () => undefined,
        presence: async () => undefined,
        beacon: () => undefined,
        listen: () => () => undefined,
        join: async () => void joined.push(id),
        end: async () => void ended.push(id),
        leave: async () => void left.push(id),
        invite: async () => ({ url: "", summary: "" }),
        revokeInvite: async () => undefined,
      };
      return new CallSession({ key: id, transport: t, member: t as unknown as Member, entry, deps: { makeEngine: () => engine } });
    },
    openWindow: (url) => {
      opened.push(url);
      return allowWindow;
    },
    undo: async (id, how) => void undone.push(`${how}:${id}`),
  });
  return { hub, engines, joined, ended, left, undone, opened, block: () => (allowWindow = false) };
}

describe("bảng gọi nổi: địa chỉ trang gọi", () => {
  it("đọc /goi/<mã>?auto=1&video=1 do server gửi; dạng khác ⇒ null; camera chỉ đi kèm auto", () => {
    expect(parseCallPage(`/goi/${A}`)).toEqual({ callId: A, entry: { auto: false, video: false } });
    expect(parseCallPage(`/goi/${A}?auto=1`)).toEqual({ callId: A, entry: { auto: true, video: false } });
    expect(parseCallPage(`/goi/${A}?auto=1&video=1`)).toEqual({ callId: A, entry: { auto: true, video: true } });
    expect(parseCallPage(`/goi/${A}?video=1`)).toEqual({ callId: A, entry: { auto: false, video: false } });
    for (const bad of ["/goi/x", `https://ac.vn/goi/${A}`, `/c/goi/${A}`, ""]) expect(parseCallPage(bad)).toBeNull();
    expect(standalonePage(A, { auto: true, video: true })).toBe(`/goi/${A}?auto=1&video=1`);
  });
});

describe("bảng gọi nổi: máy trạng thái", () => {
  it("idle → ringing → joined → ended; rời trang (gỡ mọi người nghe) KHÔNG làm rớt cuộc gọi", async () => {
    const { hub, engines } = rig();
    expect(hub.getSnapshot().phase).toBe("idle");
    expect(hub.openPage(`/goi/${A}?auto=1`)).toBe("opened");
    expect(hub.getSnapshot().phase).toBe("ringing");
    await flush();
    const e = engines.get(A)!;
    expect(e.started).toBe(1);   // ?auto=1 ⇒ vào ngay, không bắt bấm lần nữa
    expect(hub.getSnapshot().session?.snap?.phase).toBe("waiting");

    // "điều hướng": các component của trang cũ gỡ đăng ký, trang mới đăng ký — phiên, bộ máy, micro không đụng tới
    let renders = 0;
    const gone = hub.subscribe(() => renders++);
    gone();
    const again = hub.subscribe(() => renders++);
    e.set({ phase: "connected", peerHere: true, connectedAt: 1000 });
    expect(hub.getSnapshot().phase).toBe("joined");
    expect(renders).toBeGreaterThan(0);
    expect(e.closed).toEqual([]);
    expect(hub.getSnapshot().session?.engine).toBe(e);

    e.set({ phase: "reconnecting" });   // mất nối vẫn là đang ở trong cuộc gọi
    expect(hub.getSnapshot().phase).toBe("joined");

    e.close("ended");   // máy chủ báo cuộc gọi đã kết thúc
    expect(hub.getSnapshot().phase).toBe("ended");
    hub.dismiss();
    expect(hub.getSnapshot().phase).toBe("idle");
    again();
  });

  it("không có auto: chờ người bấm Vào cuộc gọi; có ghế rồi thì không giữ ghế lại", async () => {
    const { hub, engines } = rig();
    hub.open(A);
    await flush();
    expect(engines.get(A)!.started).toBe(0);
    expect(hub.getSnapshot().session?.engine).toBeNull();
    hub.enter(false);
    await flush();
    expect(engines.get(A)!.started).toBe(1);
  });

  it("cuộc gọi đã kết thúc từ trước ⇒ ended, không vào", async () => {
    const { hub, engines } = rig({ active: false });
    hub.open(A, { auto: true, video: false });
    await flush();
    expect(hub.getSnapshot().phase).toBe("ended");
    expect(engines.get(A)!.started).toBe(0);
  });

  it("Rời ⇒ báo rời qua bộ máy rồi đóng bảng; Kết thúc cho cả hai ⇒ meet.end", async () => {
    const { hub, engines, ended } = rig();
    hub.open(A, { auto: true, video: false });
    await flush();
    await hub.leave(false);
    expect(engines.get(A)!.hung).toBe(1);
    expect(hub.getSnapshot().phase).toBe("idle");
    expect(ended).toEqual([]);

    hub.open(B, { auto: true, video: false });
    await flush();
    await hub.leave(true);
    expect(ended).toEqual([B]);
    expect(hub.getSnapshot().phase).toBe("idle");
  });
});

describe("bảng gọi nổi: một người một cuộc gọi", () => {
  it("mở lại đúng cuộc gọi đang mở ⇒ mở rộng bảng, không dựng lại", async () => {
    const { hub, engines } = rig();
    hub.open(A, { auto: true, video: false });
    await flush();
    hub.setCollapsed(true);
    expect(hub.open(A)).toBe("same");
    expect(hub.getSnapshot().collapsed).toBe(false);
    expect(engines.size).toBe(1);
  });

  it("cuộc thứ hai khi cuộc thứ nhất chưa kết thúc ⇒ hỏi; ở lại giữ nguyên, đồng ý ⇒ rời cuộc thứ nhất rồi vào cuộc thứ hai", async () => {
    const { hub, engines } = rig();
    hub.open(A, { auto: true, video: false });
    await flush();
    engines.get(A)!.set({ phase: "connected", peerHere: true });

    expect(hub.open(B, { auto: true, video: false })).toBe("conflict");
    expect(hub.getSnapshot().conflict).toEqual({ kind: "opened", callId: B, entry: { auto: true, video: false }, undo: null });
    expect(hub.getSnapshot().callId).toBe(A);
    expect(engines.has(B)).toBe(false);

    hub.cancelSwitch();
    expect(hub.getSnapshot().conflict).toBeNull();
    expect(hub.getSnapshot().phase).toBe("joined");
    expect(engines.get(A)!.hung).toBe(0);

    hub.open(B, { auto: true, video: false });
    await hub.confirmSwitch();
    await flush();
    expect(engines.get(A)!.hung).toBe(1);
    expect(hub.getSnapshot().callId).toBe(B);
    expect(hub.getSnapshot().conflict).toBeNull();
    expect(engines.get(B)!.started).toBe(1);
  });

  it("cuộc thứ nhất đã kết thúc ⇒ cuộc mới thay thẳng, không hỏi", async () => {
    const { hub, engines } = rig();
    hub.open(A, { auto: true, video: false });
    await flush();
    engines.get(A)!.close("ended");
    expect(hub.open(B, { auto: true, video: false })).toBe("opened");
    expect(hub.getSnapshot().callId).toBe(B);
  });
});

describe("bảng gọi nổi: đường lùi sang thẻ riêng", () => {
  it("mở được thẻ ⇒ bảng nổi rời đi; bị chặn ⇒ giữ bảng và báo", async () => {
    const r = rig();
    r.hub.open(A, { auto: true, video: true });
    await flush();
    r.block();
    expect(await r.hub.openInTab()).toBe(false);
    expect(r.hub.getSnapshot().notice).toContain("chặn");
    expect(r.hub.getSnapshot().phase).toBe("ringing");
    expect(r.engines.get(A)!.hung).toBe(0);

    const ok = rig();
    ok.hub.open(A, { auto: true, video: true });
    await flush();
    expect(await ok.hub.openInTab()).toBe(true);
    expect(ok.opened).toEqual([`/goi/${A}?auto=1&video=1`]);
    expect(ok.engines.get(A)!.hung).toBe(1);
    expect(ok.hub.getSnapshot().phase).toBe("idle");
  });
});

describe("bảng gọi nổi: sửa sau review (07/11)", () => {
  it("chuyển cuộc khi cuộc cũ còn chờ cho phép micro: cuộc cũ đóng, ghi rời, cho phép micro sau đó KHÔNG vào lại; bấm hai lần chỉ chuyển một lần", async () => {
    const { hub, engines, left } = rig({ hold: [A] });
    hub.open(A, { auto: true, video: false });
    await flush();
    const a = engines.get(A)!;
    expect(a.snap.phase).toBe("media");
    expect(hub.holds()).toBe(true);

    expect(hub.open(B, { auto: true, video: false })).toBe("conflict");
    const one = hub.confirmSwitch();
    const two = hub.confirmSwitch();   // bấm đúp
    await Promise.all([one, two]);
    await flush();
    expect(hub.getSnapshot().callId).toBe(B);
    expect([...engines.keys()]).toEqual([A, B]);
    expect(engines.get(B)!.started).toBe(1);

    a.allow();   // người dùng giờ mới bấm "Cho phép" micro của cuộc A
    await flush();
    await flush();
    expect(a.snap.phase).toBe("closed");
    expect(a.started).toBe(1);
    expect(hub.getSnapshot().callId).toBe(B);
    expect(hub.getSnapshot().session?.engine).toBe(engines.get(B));
    expect(left).toEqual([A]);   // ghế không treo
  });

  it("hỏi TRƯỚC khi gửi lệnh: không giữ cuộc nào ⇒ đi luôn; ở lại ⇒ không gửi; đồng ý ⇒ rời cuộc cũ rồi mới gửi", async () => {
    const { hub, engines } = rig();
    expect(await hub.ask()).toBe(true);
    hub.open(A, { auto: true, video: false });
    await flush();
    engines.get(A)!.set({ phase: "connected", peerHere: true });

    const stay = hub.ask();
    expect(hub.getSnapshot().conflict).toEqual({ kind: "ask", inline: false });
    expect(overlayConflict(hub.getSnapshot().conflict)).toBe(true);
    hub.cancelSwitch();
    expect(await stay).toBe(false);
    expect(hub.getSnapshot().phase).toBe("joined");

    const go = hub.ask();
    await hub.confirmSwitch();
    expect(await go).toBe(true);
    expect(engines.get(A)!.hung).toBe(1);
    expect(hub.getSnapshot().phase).toBe("idle");
    expect(hub.getSnapshot().switching).toBe(false);
  });

  it("lệnh đã tạo cuộc mới (hai nơi bấm gần nhau) mà người dùng ở lại ⇒ gỡ cuộc mới: người gọi huỷ, người tham gia rời", async () => {
    const { hub, engines, undone } = rig();
    hub.open(A, { auto: true, video: false });
    await flush();
    engines.get(A)!.set({ phase: "connected", peerHere: true });
    expect(hub.openPage(`/goi/${B}?auto=1`, undoOf({ call_id: B, already: false, seat: "a" }))).toBe("conflict");
    hub.cancelSwitch();
    await flush();
    expect(undone).toEqual([`end:${B}`]);
    expect(undoOf({ call_id: B, seat: "b" })).toBe("leave");
    expect(undoOf({ call_id: B, already: true, seat: "a" })).toBe("leave");
    expect(undoOf({ once_open_url: "https://meet.google.com/x" })).toBeNull();
  });

  it("lệnh nào là gọi / tham gia cuộc khác", () => {
    expect(startsCall("meet.ring", { subject_kind: "talk.direct" }, null)).toBe(true);
    expect(startsCall("meet.ring", { join_url: "https://meet.google.com/x" }, null)).toBe(false);
    expect(startsCall("meet.ring", { via_link: true }, null)).toBe(false);
    expect(startsCall("meet.start", { provider: "external" }, null)).toBe(false);
    expect(startsCall("meet.join", { call_id: B, enter: true }, A)).toBe(true);
    expect(startsCall("meet.join", { call_id: A, enter: true }, A)).toBe(false);
    expect(startsCall("meet.join", { call_id: B }, A)).toBe(false);
    expect(startsCall("talk.post", {}, A)).toBe(false);
  });

  it("chưa vào (micro bị chặn): Đóng được, và cuộc sau không bị hỏi", async () => {
    const { hub, engines } = rig();
    hub.open(A, { auto: true, video: false });
    await flush();
    engines.get(A)!.set({ phase: "idle", error: "Micro bị chặn", mediaCode: "denied" });
    expect(hub.holds()).toBe(false);
    expect(hub.open(B, { auto: true, video: false })).toBe("opened");
    hub.dismiss();
    expect(hub.getSnapshot().phase).toBe("idle");
  });

  it("đổi phiên đăng nhập: rời trước, không chờ quá hạn", async () => {
    const { hub, engines } = rig();
    hub.open(A, { auto: true, video: false });
    await flush();
    engines.get(A)!.set({ phase: "connected", peerHere: true });
    await hub.leaveWithin(50);
    expect(engines.get(A)!.hung).toBe(1);
    expect(hub.getSnapshot().phase).toBe("idle");
    const slow = new CallHub({ makeSession: () => { throw new Error("không dùng"); } });
    const t0 = Date.now();
    await slow.leaveWithin(50);   // không có cuộc gọi ⇒ về ngay
    expect(Date.now() - t0).toBeLessThan(50);
  });
});

describe("bảng gọi nổi: câu hỏi nằm trong form đang mở (review lại 08/11)", () => {
  it("form hỏi (inline) ⇒ bảng nổi KHÔNG vẽ lớp phủ (hộp thoại form giữ tiêu điểm); ở lại ⇒ không gửi lệnh", async () => {
    const { hub, engines } = rig();
    hub.open(A, { auto: true, video: false });
    await flush();
    engines.get(A)!.set({ phase: "connected", peerHere: true });
    const answer = hub.ask(true);
    expect(hub.getSnapshot().conflict).toEqual({ kind: "ask", inline: true });
    expect(overlayConflict(hub.getSnapshot().conflict)).toBe(false);
    hub.cancelSwitch();   // Esc hoặc "Ở lại" trong form, hoặc form bị đóng
    expect(await answer).toBe(false);
    expect(hub.getSnapshot().conflict).toBeNull();
    expect(hub.getSnapshot().phase).toBe("joined");
    expect(overlayConflict({ kind: "opened", callId: B, entry: { auto: true, video: false }, undo: null })).toBe(true);
    expect(overlayConflict(null)).toBe(false);
  });
});
