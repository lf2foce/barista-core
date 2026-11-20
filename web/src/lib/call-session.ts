import type { DirectState } from "./call";
import { CallEngine, type Snapshot } from "./call-engine";
import { CallError, linkTransport, memberTransport, type Transport } from "./call-transport";

// Một phiên gọi trực tiếp 1–1 phía trình duyệt (core 0114, ghi chú 07/11 "bảng gọi nổi"): gom phần việc trước đây nằm trong component trang gọi — tải
// trạng thái, giữ ghế, dựng bộ máy WebRTC (call-engine.ts), vào / rời, camera một lần bấm — thành một đối tượng KHÔNG phụ thuộc React, để HAI nơi dùng
// chung: trang đứng riêng (/goi/<mã>, /c/goi/<mã link>) và bảng nổi gắn ở khung ngoài cùng của web (call-hub.ts). Phiên sống ở ngoài cây trang ⇒ đổi
// trang không làm rớt cuộc gọi; ai đóng phiên (dispose) là chủ của nó.

export type Member = ReturnType<typeof memberTransport>;
export type Entry = { auto: boolean; video: boolean };
export type EngineLike = Pick<CallEngine, "snap" | "remote" | "cameraTrack" | "subscribe" | "start" | "hangup" | "close" | "toggleMute" | "toggleCamera" | "replaceMic" | "replaceCamera" | "reconnectNow">;

/** ringing = chưa vào / đang mở micro / chờ hoặc đang nối; joined = đã nối (kể cả mất nối đang nối lại); ended = cuộc gọi đã kết thúc hoặc không còn vào được. */
export type SessionPhase = "ringing" | "joined" | "ended";

export type SessionView = {
  phase: SessionPhase;
  st: DirectState | null;
  loadError: string | null;
  busy: boolean;
  engine: EngineLike | null;
  snap: Snapshot | null;
  cameraOffered: boolean;
  entry: Entry | undefined;
  canEnd: boolean;
  /** bộ máy đang chạy (đã xin micro xong hoặc đang xin) — khác "chưa vào / đã rời". */
  inCall: boolean;
  /** đang giữ một cuộc gọi (đang vào hoặc đã vào, chưa kết thúc): mở cuộc gọi khác phải hỏi rời cuộc này. Chỉ mới mở bảng, chưa vào ⇒ không. */
  holds: boolean;
};

export type SessionDeps = { makeEngine?: (t: Transport, st: DirectState) => EngineLike };

export function phaseOf(st: DirectState | null, loadError: string | null, snap: Snapshot | null): SessionPhase {
  const ep = snap?.phase;
  if (!st) return loadError ? "ended" : "ringing";
  if (!st.active || ep === "ended" || (ep === "closed" && snap?.error)) return "ended";
  return ep === "connected" || ep === "reconnecting" ? "joined" : "ringing";
}

export function linkSession(token: string, entry?: Entry, deps?: SessionDeps): CallSession {
  return new CallSession({ key: token, transport: linkTransport(token), entry, deps });
}

export function memberSession(callId: string, entry?: Entry, deps?: SessionDeps): CallSession {
  const t = memberTransport(callId);
  return new CallSession({ key: callId, transport: t, member: t, entry, deps });
}

export class CallSession {
  readonly key: string;
  readonly transport: Transport;
  readonly member: Member | undefined;
  readonly entry: Entry | undefined;
  private readonly makeEngine: (t: Transport, st: DirectState) => EngineLike;
  private st: DirectState | null = null;
  private loadError: string | null = null;
  private busy = false;
  private engine: EngineLike | null = null;
  private snap: Snapshot | null = null;
  private cameraOffered = false;
  private epoch = 0;
  private autoTried = false;
  private peerWas = false;
  private unsub: (() => void) | null = null;
  private listeners = new Set<() => void>();
  private view: SessionView;

  constructor(o: { key: string; transport: Transport; member?: Member; entry?: Entry; deps?: SessionDeps }) {
    this.key = o.key;
    this.transport = o.transport;
    this.member = o.member;
    this.entry = o.entry;
    this.makeEngine = o.deps?.makeEngine ?? ((t, st) => new CallEngine(t, st));
    this.view = this.build();
  }

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  getSnapshot = (): SessionView => this.view;

  get phase(): SessionPhase {
    return this.view.phase;
  }

  private build(): SessionView {
    const ep = this.snap?.phase;
    const phase = phaseOf(this.st, this.loadError, this.snap);
    const inCall = !!ep && ep !== "idle" && ep !== "ended" && ep !== "closed";
    return {
      phase,
      st: this.st,
      loadError: this.loadError,
      busy: this.busy,
      engine: this.engine,
      snap: this.snap,
      cameraOffered: this.cameraOffered,
      entry: this.entry,
      canEnd: !!this.member,
      inCall,
      holds: phase !== "ended" && (inCall || this.busy),
    };
  }

  private emit(): void {
    this.view = this.build();
    for (const fn of [...this.listeners]) fn();
  }

  /** Mở phiên: tải trạng thái; địa chỉ có `auto` ⇒ vào cuộc gọi ngay (người vừa bấm Gọi / Tham gia đã nói rõ ý). Lỗi (micro bị chặn…) ⇒ nút thủ công còn đó. */
  init(): void {
    const e = ++this.epoch;
    void (async () => {
      await this.reload(e);
      if (e !== this.epoch) return;
      if (this.entry?.auto && !this.autoTried && this.st?.active && !this.engine) {
        this.autoTried = true;
        await this.enter(false);
      }
    })();
  }

  async reload(epoch = this.epoch): Promise<void> {
    try {
      const s = await this.transport.state();
      if (epoch !== this.epoch) return;
      this.st = s;
      this.loadError = null;
    } catch (e) {
      if (epoch !== this.epoch) return;
      this.loadError = e instanceof CallError || e instanceof Error ? e.message : "Không tải được cuộc gọi";
    }
    this.emit();
  }

  private attach(e: EngineLike): void {
    this.unsub?.();
    this.engine = e;
    this.peerWas = false;
    this.unsub = e.subscribe((s) => {
      this.snap = s;
      // người kia vừa vào / vào lại: đọc lại tên người kia + lời mời (mình mở trang trước khi họ ngồi ghế thì chưa biết họ là ai)
      if (s.peerHere && !this.peerWas) void this.reload();
      this.peerWas = s.peerHere;
      this.emit();
    });
  }

  /** "Vào cuộc gọi" / vào tự động. Thành viên chưa có ghế ⇒ giữ ghế trước. `listenOnly`: vào mà không dùng micro. Camera KHÔNG bao giờ tự bật. */
  async enter(listenOnly = false): Promise<void> {
    if (!this.st || this.busy) return;
    // phiên bị đóng (rời / chuyển sang cuộc gọi khác) trong lúc đang vào ⇒ dừng ở bước kế tiếp, nhả micro, trả ghế; không đụng trạng thái phiên nữa
    const ep = this.epoch;
    const gone = () => ep !== this.epoch;
    this.busy = true;
    this.emit();
    let e: EngineLike | null = null;
    try {
      let s = this.st;
      if (!s.seat && this.member) {
        await this.member.join();
        if (gone()) return this.abandon(null);
        s = await this.transport.state();
        if (gone()) return this.abandon(null);
        this.st = s;
      }
      const cur = this.engine;
      e = cur && cur.snap.phase === "idle" ? cur : this.makeEngine(this.transport, s);
      this.attach(e);
      await e.start(listenOnly);
      if (gone()) return this.abandon(e);
    } catch (err) {
      if (gone()) return this.abandon(e);
      this.loadError = err instanceof Error ? err.message : "Không vào được cuộc gọi";
    } finally {
      if (!gone()) {
        this.busy = false;
        this.emit();
      }
    }
  }

  /** Vào dở thì phiên bị đóng: đóng bộ máy (nó tự nhả micro vừa lấy, không chào người kia) và ghi mình rời để ghế không treo. */
  private abandon(e: EngineLike | null): void {
    e?.close("closed");
    void this.member?.leave().catch(() => undefined);
  }

  /** Rời cuộc gọi; `endForAll` (chỉ thành viên) ⇒ đóng cuộc gọi cho cả hai. */
  async hangup(endForAll = false): Promise<void> {
    if (!this.engine) return;
    this.busy = true;
    this.emit();
    await this.engine.hangup();
    if (endForAll && this.member) await this.member.end().catch(() => undefined);
    await this.reload();
    this.busy = false;
    this.emit();
  }

  /** Một lần bấm "Bật camera" (gợi ý của gọi video): từ đó chỉ còn nút tròn trong thanh điều khiển. */
  offerCamera(): void {
    this.cameraOffered = true;
    void this.engine?.toggleCamera();
    this.emit();
  }

  /** Đóng hẳn phiên: nhả micro / camera, ngắt nghe tín hiệu. Chỉ chủ của phiên gọi (trang đứng riêng khi rời trang, hub khi người dùng rời). */
  dispose(): void {
    this.epoch += 1;
    this.autoTried = false;
    this.unsub?.();
    this.unsub = null;
    this.engine?.close("closed");
    this.engine = null;
    this.snap = null;
    this.busy = false;
    this.cameraOffered = false;
    this.emit();
  }
}
