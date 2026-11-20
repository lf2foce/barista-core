import { flowOf, forCurrent, helloKind, mediaError, recovery, type DirectState, type Flow, type PeerRef, type Signal } from "./call";
import type { StreamEnd, Transport } from "./call-transport";

// Bộ máy gọi trực tiếp 1–1 trong trình duyệt (core 0114 bổ sung 07/11). WebRTC ngang hàng: tiếng / hình đi thẳng giữa hai máy, máy chủ chỉ chuyển tín
// hiệu tới đúng người kia. Thương lượng theo kiểu "perfect negotiation" (ghế b nhường khi hai bên cùng gửi offer). Mỗi lần dựng lại kết nối là một
// THẾ HỆ mới, báo bằng lời chào (hello) — thư của thế hệ cũ bị bỏ, không làm hỏng kết nối mới.
//   Rớt mạng: "disconnected" quá 3 giây / "failed" ⇒ khởi động lại ICE; 20 giây chưa hồi ⇒ dựng lại kết nối. Kênh tín hiệu rớt thì tự nối lại từ thư
//   cuối đã nhận. Đóng thẻ ⇒ báo "tạm biệt" + "kết thúc" bằng sendBeacon. Mở lại sau khoá màn hình ⇒ kiểm lại kết nối, phát lại tiếng.
//   Có mặt: máy CHỈ báo của chính nó (nối được / mất nối / kết thúc); server tính phút, không đoán.

export type Phase = "idle" | "media" | "waiting" | "connecting" | "connected" | "reconnecting" | "ended" | "closed";
export type Snapshot = {
  phase: Phase;
  peerHere: boolean;
  peerLeft: boolean;
  noAnswer: boolean;
  muted: boolean;
  listenOnly: boolean;
  camera: boolean;
  remoteVideo: boolean;
  signalUp: boolean;
  otherWindow: boolean;
  error: string | null;
  mediaCode: string | null;
  connectedAt: number | null;
  /** lúc bắt đầu chờ người kia (vào cuộc gọi, hoặc người kia vừa rời) — đồng hồ "Đang gọi…" của bảng gọi */
  waitingAt: number | null;
  mics: { id: string; label: string }[];
  micId: string;
  cams: { id: string; label: string }[];
  /** camera đã chọn ("" = mặc định); camera đang tắt thì dùng ở lần bật sau */
  camId: string;
  /** ``error`` đang nói về thiết bị nào khi đang trong cuộc gọi (đổi micro / bật camera hỏng) — để bảng gọi đưa đúng nút thử lại */
  deviceError: "mic" | "camera" | null;
  turn: boolean;
  relay: boolean;
};

const NO_ANSWER_MS = 45_000;
const CANDIDATE_BATCH_MS = 60;
const PRESENCE_RETRY = 3;

export class CallEngine {
  readonly sid = globalThis.crypto.randomUUID().slice(0, 12);
  private gen = 0;
  private peer: PeerRef | null = null;
  private pc: RTCPeerConnection | null = null;
  private makingOffer = false;
  private ignoreOffer = false;
  private local: MediaStream | null = null;
  private cam: MediaStreamTrack | null = null;
  readonly remote = new MediaStream();
  private stop: (() => void) | null = null;
  private pending: RTCIceCandidateInit[] = [];
  private batchTimer: ReturnType<typeof setTimeout> | null = null;
  private watch: ReturnType<typeof setInterval> | null = null;
  private badSince = 0;
  private restarted = false;
  private reported = false;
  private droppedAt: Date | null = null;
  private waitingSince = 0;
  private wakeLock: { release(): Promise<void> } | null = null;
  private peerKey = "";
  private iceAt = Date.now();
  private listeners = new Set<(s: Snapshot) => void>();
  // thư xử lý LẦN LƯỢT: ICE tới trong lúc đang đặt offer phải chờ offer xong, không thì addIceCandidate hỏng và mất đường nối
  private inbox: Promise<void> = Promise.resolve();
  snap: Snapshot = {
    phase: "idle", peerHere: false, peerLeft: false, noAnswer: false, muted: false, listenOnly: false, camera: false, remoteVideo: false,
    signalUp: false, otherWindow: false, error: null, mediaCode: null, connectedAt: null, waitingAt: null, mics: [], micId: "", cams: [], camId: "",
    deviceError: null, turn: false, relay: false,
  };

  constructor(
    private readonly t: Transport,
    private st: DirectState,
  ) {
    this.snap.turn = !!st.ice?.turn;
    if (typeof window !== "undefined") (window as unknown as { __baristaCall?: CallEngine }).__baristaCall = this;   // cho kiểm thử (getStats)
  }

  subscribe(fn: (s: Snapshot) => void): () => void {
    this.listeners.add(fn);
    fn(this.snap);
    return () => this.listeners.delete(fn);
  }

  private set(p: Partial<Snapshot>): void {
    this.snap = { ...this.snap, ...p };
    for (const fn of this.listeners) fn(this.snap);
  }

  get polite(): boolean {
    return this.st.polite;
  }

  // ── Micro / camera ──

  /** Xin micro (người bấm "Vào cuộc gọi" — trình duyệt cần cử chỉ người dùng để phát tiếng trên điện thoại). ``listenOnly``: vào mà không gửi tiếng. */
  async start(listenOnly = false): Promise<boolean> {
    if (this.dead) return false;
    this.set({ phase: "media", error: null, mediaCode: null });
    if (!listenOnly) {
      try {
        const got = await navigator.mediaDevices.getUserMedia({ audio: this.audioConstraints(this.snap.micId) });
        // đóng trong lúc chờ người cho phép micro (rời / chuyển sang cuộc gọi khác): nhả micro vừa lấy, không vào cuộc gọi
        if (this.dead) {
          for (const t of got.getTracks()) t.stop();
          return false;
        }
        this.local = got;
        this.bindMic(this.local.getAudioTracks()[0]);
      } catch (e) {
        if (this.dead) return false;
        const m = mediaError(e, "micro");
        this.set({ phase: "idle", error: m.message, mediaCode: m.code });
        return false;
      }
    }
    this.set({ listenOnly });
    await this.listMics();
    if (this.dead) return this.abandon();
    navigator.mediaDevices.addEventListener?.("devicechange", this.onDevices);
    window.addEventListener("pagehide", this.onPageHide);
    document.addEventListener("visibilitychange", this.onVisible);
    window.addEventListener("online", this.onOnline);
    this.build();
    this.stop = this.t.listen(this.st.cursor, (s) => (this.inbox = this.inbox.then(() => this.onSignal(s)).catch(() => undefined)), (why) => this.onStreamEnd(why),
      (up) => this.set({ signalUp: up }), (key) => (this.inbox = this.inbox.then(() => this.onPeer(key)).catch(() => undefined)));
    await this.hello();
    if (this.dead) return false;
    this.waitingSince = Date.now();
    this.set({ phase: "waiting", waitingAt: this.waitingSince });
    this.watch = setInterval(() => void this.tick(), 1000);
    return true;
  }

  /** Bị đóng giữa chừng lúc đang vào: nhả micro (close đã chạy trước khi micro gắn vào bộ máy). */
  private abandon(): false {
    for (const t of this.local?.getTracks() ?? []) t.stop();
    this.local = null;
    return false;
  }

  private audioConstraints(id: string): MediaTrackConstraints {
    return { echoCancellation: true, noiseSuppression: true, autoGainControl: true, ...(id ? { deviceId: { exact: id } } : {}) };
  }

  private bindMic(track: MediaStreamTrack | undefined): void {
    if (!track) return;
    track.enabled = !this.snap.muted;
    track.onended = () => void this.replaceMic("");   // rút tai nghe / micro: lấy micro mặc định khác
    this.set({ micId: track.getSettings().deviceId ?? "" });
  }

  private async listMics(): Promise<void> {
    try {
      const all = await navigator.mediaDevices.enumerateDevices();
      const of = (kind: MediaDeviceKind, name: string) =>
        all.filter((d) => d.kind === kind && d.deviceId).map((d, i) => ({ id: d.deviceId, label: d.label || `${name} ${i + 1}` }));
      this.set({ mics: of("audioinput", "Micro"), cams: of("videoinput", "Camera") });
    } catch {
      /* trình duyệt không cho liệt kê: giữ micro hiện tại */
    }
  }

  private onDevices = async (): Promise<void> => {
    await this.listMics();
    const track = this.local?.getAudioTracks()[0];
    if (!this.snap.listenOnly && (!track || track.readyState === "ended" || !this.snap.mics.some((m) => m.id === this.snap.micId))) await this.replaceMic("");
  };

  /** Đổi micro giữa cuộc gọi: thay đường tiếng đang gửi, không thương lượng lại. */
  async replaceMic(id: string): Promise<void> {
    try {
      const fresh = await navigator.mediaDevices.getUserMedia({ audio: this.audioConstraints(id) });
      const track = fresh.getAudioTracks()[0];
      const old = this.local?.getAudioTracks()[0];
      const sender = this.pc?.getSenders().find((s) => s.track?.kind === "audio" || (s.track === null && old !== undefined));
      if (sender) await sender.replaceTrack(track);
      if (old) {
        this.local?.removeTrack(old);
        old.onended = null;
        old.stop();
      }
      if (!this.local) this.local = new MediaStream();
      this.local.addTrack(track);
      this.bindMic(track);
      this.set({ error: null, deviceError: null });
    } catch (e) {
      this.set({ error: mediaError(e, "micro").message, deviceError: "mic" });
    }
  }

  toggleMute(): void {
    const muted = !this.snap.muted;
    for (const t of this.local?.getAudioTracks() ?? []) t.enabled = !muted;
    this.set({ muted });
  }

  /** Bật / tắt camera: thêm đường hình vào CÙNG kết nối (thương lượng lại); tắt = thôi gửi, không thương lượng lại. */
  async toggleCamera(): Promise<void> {
    if (this.cam) {
      const sender = this.pc?.getSenders().find((s) => s.track === this.cam);
      await sender?.replaceTrack(null);
      this.cam.stop();
      this.cam = null;
      this.set({ camera: false });
      return;
    }
    try {
      const s = await this.openCamera();
      if (this.snap.phase === "ended" || this.snap.phase === "closed" || this.cam) {   // đóng / bật trùng trong lúc chờ camera ⇒ nhả ngay
        s.getTracks().forEach((t) => t.stop());
        return;
      }
      this.cam = s.getVideoTracks()[0] ?? null;
      if (!this.cam) return;
      this.attachCamera();
      this.set({ camera: true, error: null, deviceError: null, camId: this.cam.getSettings().deviceId ?? this.snap.camId });
      void this.listMics();   // tên camera chỉ có sau lần đầu được cho phép dùng camera
    } catch (e) {
      this.set({ error: mediaError(e, "camera").message, deviceError: "camera" });
    }
  }

  /** Chọn camera: đang bật ⇒ thay đường hình đang gửi (không thương lượng lại); đang tắt ⇒ nhớ để lần bật sau dùng. */
  async replaceCamera(id: string): Promise<void> {
    if (!this.cam) {
      this.set({ camId: id });
      return;
    }
    try {
      const s = await navigator.mediaDevices.getUserMedia({ video: this.videoConstraints(id) });
      const fresh = s.getVideoTracks()[0];
      if (!fresh || !this.cam || this.dead) {
        s.getTracks().forEach((t) => t.stop());
        return;
      }
      const old = this.cam;
      const sender = this.pc?.getSenders().find((x) => x.track === old);
      await sender?.replaceTrack(fresh);
      old.stop();
      this.cam = fresh;
      this.set({ camId: fresh.getSettings().deviceId ?? id, error: null, deviceError: null });
    } catch (e) {
      this.set({ error: mediaError(e, "camera").message, deviceError: "camera" });
    }
  }

  private videoConstraints(id: string): MediaTrackConstraints {
    return { width: { ideal: 640 }, height: { ideal: 480 }, ...(id ? { deviceId: { exact: id } } : { facingMode: "user" }) };
  }

  /** Mở camera; thiết bị khởi động chậm / vừa được nơi khác nhả (AbortError, NotReadableError) ⇒ thử lại MỘT lần sau 0,8 giây. */
  private async openCamera(): Promise<MediaStream> {
    const ask = () => navigator.mediaDevices.getUserMedia({ video: this.videoConstraints(this.snap.camId) });
    try {
      return await ask();
    } catch (e) {
      const name = e && typeof e === "object" && "name" in e ? String((e as { name: unknown }).name) : "";
      if (name !== "AbortError" && name !== "NotReadableError") throw e;
      await new Promise((r) => setTimeout(r, 800));
      return await ask();
    }
  }

  get cameraTrack(): MediaStreamTrack | null {
    return this.cam;
  }

  private attachCamera(): void {
    if (!this.pc || !this.cam) return;
    const idle = this.pc.getTransceivers().find((t) => t.receiver.track.kind === "video" && t.sender.track === null);
    if (idle) {
      void idle.sender.replaceTrack(this.cam);
      if (idle.direction === "recvonly" || idle.direction === "inactive") idle.direction = "sendrecv";   // ⇒ thương lượng lại
    } else this.pc.addTrack(this.cam, this.local ?? new MediaStream([this.cam]));
  }

  // ── Kết nối ──

  /** Dựng kết nối mới (thế hệ mới). Kết nối cũ đóng hẳn; thư của nó về sau bị bỏ (forCurrent). */
  private build(): void {
    this.pc?.close();
    this.gen += 1;
    this.pending = [];
    this.restarted = false;
    this.badSince = 0;
    this.makingOffer = false;
    this.ignoreOffer = false;
    const pc = new RTCPeerConnection({ iceServers: this.st.ice?.servers ?? [] });
    this.pc = pc;
    const mic = this.local?.getAudioTracks()[0];
    if (mic) pc.addTrack(mic, this.local!);
    else pc.addTransceiver("audio", { direction: "recvonly" });
    if (this.cam) pc.addTrack(this.cam, this.local ?? new MediaStream([this.cam]));
    for (const t of this.remote.getTracks()) this.remote.removeTrack(t);
    this.set({ remoteVideo: false });
    pc.ontrack = (e) => {
      if (pc !== this.pc) return;
      this.remote.addTrack(e.track);
      if (e.track.kind === "video") {
        const show = () => this.set({ remoteVideo: e.track.readyState === "live" && !e.track.muted });
        e.track.onunmute = show;
        e.track.onmute = show;
        e.track.onended = show;
        show();
      }
      this.set({});   // vẽ lại (phần tử audio / video gắn this.remote)
    };
    pc.onicecandidate = (e) => {
      if (pc !== this.pc || !e.candidate) return;
      this.pending.push(e.candidate.toJSON());
      this.batchTimer ??= setTimeout(() => void this.flushCandidates(), CANDIDATE_BATCH_MS);
    };
    pc.onnegotiationneeded = () => void this.negotiate(pc);
    pc.onconnectionstatechange = () => this.onState(pc);
    pc.oniceconnectionstatechange = () => this.onState(pc);
  }

  private async flushCandidates(): Promise<void> {
    this.batchTimer = null;
    const batch = this.pending.splice(0);
    if (!batch.length || !this.peer) return;
    await this.send("candidate", { candidates: batch, tg: this.peer.gen });
  }

  /** Gửi LẦN LƯỢT theo thứ tự gọi: lời chào phải tới trước offer của kết nối mới, offer trước ICE của nó (hai POST song song có thể đổi thứ tự ở
   *  máy chủ, bên kia sẽ bỏ offer vì chưa biết thế hệ mới). */
  private outbox: Promise<void> = Promise.resolve();
  private send(kind: Signal["kind"], body: Record<string, unknown>): Promise<void> {
    const msg = { sid: this.sid, gen: this.gen, ...body };
    this.outbox = this.outbox.then(() => this.t.send(kind, msg)).catch(() => {
      /* kênh tín hiệu rớt: lần thương lượng / khởi động lại ICE sau sẽ gửi lại */
    });
    return this.outbox;
  }

  private async hello(to?: string): Promise<void> {
    await this.send("hello", to ? { to } : {});
  }

  /** Gửi offer khi có việc cần thương lượng. Ghế b (nhường) không mở màn lần đầu — chờ offer của ghế a cho đỡ va. */
  private async negotiate(pc: RTCPeerConnection): Promise<void> {
    if (pc !== this.pc || !this.peer || pc.signalingState !== "stable") return;
    if (this.polite && !pc.remoteDescription) return;
    try {
      this.makingOffer = true;
      await pc.setLocalDescription();
      if (pc === this.pc && pc.localDescription) await this.send("description", { description: pc.localDescription.toJSON(), tg: this.peer.gen });
    } catch {
      /* va offer: bên kia sẽ giải theo luật nhường */
    } finally {
      this.makingOffer = false;
    }
  }

  private async onSignal(s: Signal): Promise<void> {
    const b = s.body;
    if (s.kind === "hello") {
      const k = helloKind(b, this.peer, this.sid);
      if (k === "other-window") return this.set({ otherWindow: true });
      if (k !== "new") return;
      const used = !!this.pc && (this.pc.remoteDescription !== null || this.pc.localDescription !== null);
      if (used) this.build();   // người kia mở lại / dựng lại kết nối ⇒ mình cũng dựng lại
      this.peer = { sid: String(b.sid), gen: Number(b.gen) };
      this.set({ peerHere: true, peerLeft: false, noAnswer: false, otherWindow: false, phase: this.snap.phase === "connected" ? "reconnecting" : "connecting" });
      if (typeof b.to !== "string") await this.hello(this.peer.sid);   // trả lời lời chào chung (không trả lời lời đáp — tránh vòng)
      if (this.pc) await this.negotiate(this.pc);
      return;
    }
    if (s.kind === "bye") {
      if (!this.peer || b.sid !== this.peer.sid) return;
      await this.dropPeer("peer-left");
      return;
    }
    const pc = this.pc;
    if (!pc || !forCurrent(b, this.peer, this.gen)) return;
    if (s.kind === "description") {
      const d = b.description as RTCSessionDescriptionInit | undefined;
      if (!d || (d.type !== "offer" && d.type !== "answer")) return;
      const collision = d.type === "offer" && (this.makingOffer || pc.signalingState !== "stable");
      this.ignoreOffer = !this.polite && collision;
      if (this.ignoreOffer) return;
      try {
        await pc.setRemoteDescription(d);   // bên nhường: tự hoàn offer của mình
        if (d.type === "offer") {
          await pc.setLocalDescription();
          if (pc.localDescription) await this.send("description", { description: pc.localDescription.toJSON(), tg: this.peer!.gen });
        }
      } catch {
        /* thư lệch trạng thái: lần khởi động lại ICE / dựng lại sẽ sửa */
      }
      return;
    }
    if (s.kind === "candidate") {
      for (const c of Array.isArray(b.candidates) ? (b.candidates as RTCIceCandidateInit[]) : []) {
        try {
          await pc.addIceCandidate(c);
        } catch {
          if (!this.ignoreOffer) {
            /* ICE của offer đã bỏ qua / của lần cũ — bỏ */
          }
        }
      }
    }
  }

  /** Bỏ kết nối với người kia NGAY (họ rời, hoặc máy chủ báo họ không còn được ở trong cuộc gọi): đóng kết nối — tiếng / hình thôi chạy — rồi chờ. */
  private async dropPeer(why: string): Promise<void> {
    this.peer = null;
    await this.lost(why);
    this.build();
    this.waitingSince = Date.now();
    this.set({ peerHere: false, peerLeft: true, phase: "waiting", waitingAt: this.waitingSince, remoteVideo: false });
  }

  /** Máy chủ báo tư cách người kia: rỗng = bị thu hồi / rút đồng ý / mất quyền / trả chỗ; đổi nhãn = người khác ngồi vào chỗ đó. */
  private async onPeer(key: string): Promise<void> {
    const before = this.peerKey;
    this.peerKey = key;
    if (this.peer && (!key || (before && key !== before))) await this.dropPeer(key ? "peer-changed" : "peer-removed");
  }

  private onState(pc: RTCPeerConnection): void {
    if (pc !== this.pc) return;
    const state = pc.connectionState === "connected" || pc.iceConnectionState === "connected" || pc.iceConnectionState === "completed" ? "connected" : pc.iceConnectionState;
    if (state === "connected") {
      this.badSince = 0;
      this.restarted = false;
      this.set({ phase: "connected", connectedAt: this.snap.connectedAt ?? Date.now(), noAnswer: false });
      // ghi đường đi (host = cùng mạng nội bộ, srflx = thẳng qua internet, relay = qua máy chuyển tiếp) vào nhật ký có mặt — để biết TURN có thật được dùng và khi nào cần đổi nhà cung cấp
      void this.flow().then((f) => this.report("connected", f.path), () => this.report("connected"));
      void this.keepAwake();
      return;
    }
    if (state === "disconnected" || state === "failed" || state === "closed") {
      if (!this.badSince) {
        this.badSince = Date.now();
        this.droppedAt = new Date();
      }
      if (this.snap.phase === "connected") this.set({ phase: "reconnecting" });
    }
  }

  /** Mỗi giây: chưa ai nghe máy quá lâu ⇒ báo; mất nối ⇒ khởi động lại ICE / dựng lại theo `recovery`. */
  private async tick(): Promise<void> {
    if (this.snap.phase === "waiting" && !this.peer && Date.now() - this.waitingSince > NO_ANSWER_MS) this.set({ noAnswer: true });
    // mật khẩu TURN sống vài phút: lấy cặp mới mỗi nửa hạn; đang đi qua máy chuyển tiếp thì khởi động lại ICE để dùng cặp mới (cặp cũ hết hạn là mất)
    const ttl = this.st.ice?.ttlS;
    if (ttl && this.peer && Date.now() - this.iceAt > Math.max(60, ttl / 2) * 1000) {
      this.iceAt = Date.now();
      await this.refreshIce();
      if ((await this.flow()).relay) this.pc?.restartIce();
    }
    const pc = this.pc;
    if (pc && this.peer && !this.badSince && pc.signalingState === "closed") this.badSince = Date.now();   // đóng ngang không phát sự kiện
    if (!pc || !this.peer || !this.badSince) return;
    const act = recovery(pc.signalingState === "closed" ? "closed" : pc.iceConnectionState, Date.now() - this.badSince, this.restarted);
    if (act === "wait") return;
    await this.lost("network");
    if (act === "restart") {
      this.restarted = true;
      this.badSince = Date.now();
      await this.refreshIce();
      pc.restartIce();   // ⇒ negotiationneeded ⇒ offer có ICE mới (bên nhường cũng được: đã có remoteDescription)
      if (this.polite && pc.signalingState === "stable") await this.negotiate(pc);
    } else {
      this.build();
      await this.hello();
    }
  }

  /** Mật khẩu TURN là tạm: lấy bản mới trước khi khởi động lại ICE / dựng lại kết nối. */
  private async refreshIce(): Promise<void> {
    try {
      const fresh = await this.t.state();
      if (fresh.ice) {
        this.st = { ...this.st, ice: fresh.ice };
        this.pc?.setConfiguration({ iceServers: fresh.ice.servers });
      }
      if (!fresh.active) this.onStreamEnd("ended");
    } catch {
      /* mạng chưa về: dùng cấu hình cũ */
    }
  }

  private onOnline = (): void => {
    if (this.pc && this.peer && this.snap.phase !== "connected") this.badSince = this.badSince || Date.now() - 3_001;
  };

  /** Người bấm "Nối lại ngay" khi đang mất nối: không chờ 3 giây — lần đếm kế tiếp (≤ 1 giây) khởi động lại ICE theo đúng đường tự hồi. */
  reconnectNow(): void {
    if (this.pc && this.peer && this.snap.phase === "reconnecting") this.badSince = Date.now() - (this.restarted ? 20_001 : 3_001);
  }

  private onVisible = (): void => {
    if (document.visibilityState !== "visible") return;
    // mở lại sau khoá màn hình (Safari điện thoại treo trang): kiểm lại kết nối, giữ màn hình sáng, phát lại tiếng nếu bị dừng
    if (this.pc && this.peer && this.snap.phase !== "connected") this.badSince = this.badSince || Date.now() - 3_001;
    void this.keepAwake();
    this.set({});
  };

  private async keepAwake(): Promise<void> {
    try {
      const nav = navigator as Navigator & { wakeLock?: { request(type: "screen"): Promise<{ release(): Promise<void> }> } };
      if (!this.wakeLock && nav.wakeLock && document.visibilityState === "visible") {
        this.wakeLock = await nav.wakeLock.request("screen");
        (this.wakeLock as unknown as EventTarget).addEventListener?.("release", () => (this.wakeLock = null));
      }
    } catch {
      /* không có / bị từ chối: màn hình có thể tự khoá — trang vẫn tự nối lại khi mở */
    }
  }

  // ── Có mặt (máy chỉ báo của chính nó) ──

  private async report(event: "connected" | "disconnected" | "ended", detail = "", at?: Date): Promise<void> {
    if (event === "connected") {
      if (this.reported) return;
      this.reported = true;
    } else {
      if (!this.reported && event === "disconnected") return;
      this.reported = false;
    }
    for (let i = 0; i < PRESENCE_RETRY; i++) {
      try {
        return await this.t.presence(event, detail, at);
      } catch {
        await new Promise((r) => setTimeout(r, 1000 * (i + 1)));
      }
    }
  }

  private async lost(detail: string): Promise<void> {
    if (this.reported) await this.report("disconnected", detail, this.droppedAt ?? new Date());
    this.droppedAt = null;
  }

  // ── Kết thúc ──

  /** Mình rời: báo "kết thúc" + tạm biệt; chờ tối đa 2 giây để bên kia kịp báo của nó trước khi bên gọi đóng cuộc gọi (số phút hai bên đủ). */
  async hangup(): Promise<void> {
    await this.report("ended", "hangup");
    if (this.peer) {
      await this.send("bye", {});
      await new Promise((r) => setTimeout(r, 1500));
    }
    this.close("closed");
  }

  private onPageHide = (): void => {
    if (this.snap.phase === "ended" || this.snap.phase === "closed") return;
    if (this.reported) this.t.beacon("presence", { event: "ended", detail: "pagehide" });
    this.t.beacon("signal", { kind: "bye", body: { sid: this.sid, gen: this.gen } });
  };

  private onStreamEnd(why: StreamEnd): void {
    if (why === "ended") {
      void this.report("ended", "call-ended");
      this.close("ended");
    } else this.close("closed", why === "auth" ? "Phiên đăng nhập đã hết — đăng nhập lại rồi mở lại cuộc gọi." : "Anh/chị không còn quyền vào cuộc gọi này (hoặc link đã hết hạn).");
  }

  /** Đã đóng: mọi bước còn dở của ``start`` (đang chờ micro…) dừng lại, không chào, không nghe tín hiệu. Bộ máy đã đóng không dùng lại. */
  private dead = false;

  close(phase: "ended" | "closed", error: string | null = null): void {
    this.dead = true;
    this.stop?.();
    this.stop = null;
    if (this.watch) clearInterval(this.watch);
    this.pc?.close();
    this.pc = null;
    for (const t of this.local?.getTracks() ?? []) t.stop();
    this.cam?.stop();
    this.cam = null;
    void this.wakeLock?.release().catch(() => undefined);
    this.wakeLock = null;
    navigator.mediaDevices?.removeEventListener?.("devicechange", this.onDevices);
    window.removeEventListener("pagehide", this.onPageHide);
    document.removeEventListener("visibilitychange", this.onVisible);
    window.removeEventListener("online", this.onOnline);
    this.set({ phase, error: error ?? this.snap.error, camera: false });
  }

  /** Số gói tin đang chạy (kiểm thử + chỉ báo "đang qua máy chuyển tiếp"). */
  async flow(): Promise<Flow> {
    if (!this.pc) return { audioIn: 0, audioOut: 0, videoIn: 0, videoOut: 0, relay: false, path: "" };
    const out: Record<string, unknown>[] = [];
    (await this.pc.getStats()).forEach((v) => out.push(v as Record<string, unknown>));
    const f = flowOf(out);
    if (f.relay !== this.snap.relay) this.set({ relay: f.relay });
    return f;
  }

  /** Cho kiểm thử: trạng thái ICE hiện tại + thế hệ kết nối. */
  debug(): { ice: string; conn: string; gen: number; peer: PeerRef | null } {
    return { ice: this.pc?.iceConnectionState ?? "none", conn: this.pc?.connectionState ?? "none", gen: this.gen, peer: this.peer };
  }

  /** Cho kiểm thử: giả lập rớt mạng phía này — kết nối hiện tại bị đóng ngang (không chào tạm biệt), như máy mất mạng. */
  simulateDrop(): void {
    this.pc?.close();
  }

  /** Cho kiểm thử: khởi động lại ICE ngay trên kết nối đang sống. */
  restartIce(): void {
    this.pc?.restartIce();
    if (this.polite && this.pc) void this.negotiate(this.pc);
  }
}
