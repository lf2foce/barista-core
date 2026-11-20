import { afterEach, describe, expect, it, vi } from "vitest";
import { decodeDirect } from "../call";
import { CallEngine } from "../call-engine";
import type { Transport } from "../call-transport";

// Bộ máy gọi THẬT (call-engine.ts) với trình duyệt giả: RTCPeerConnection / MediaStream / micro / window là bản giả tối thiểu. Kiểm một điều: bộ máy bị đóng
// trong lúc người dùng còn đang ở hộp "cho phép micro" thì micro lấy được sau đó bị nhả ngay, không chào người kia, không nghe tín hiệu (review 07/11).

class FakeTrack {
  kind = "audio";
  enabled = true;
  readyState = "live";
  stopped = false;
  onended: (() => void) | null = null;
  stop() {
    this.stopped = true;
    this.readyState = "ended";
  }
  getSettings() {
    return { deviceId: "m1" };
  }
}

class FakeStream {
  private tracks: FakeTrack[];
  constructor(tracks: FakeTrack[] = []) {
    this.tracks = [...tracks];
  }
  getTracks() {
    return [...this.tracks];
  }
  getAudioTracks() {
    return this.tracks.filter((t) => t.kind === "audio");
  }
  addTrack(t: FakeTrack) {
    this.tracks.push(t);
  }
  removeTrack(t: FakeTrack) {
    this.tracks = this.tracks.filter((x) => x !== t);
  }
}

class FakePeer {
  static made = 0;
  signalingState = "stable";
  connectionState = "new";
  iceConnectionState = "new";
  remoteDescription = null;
  localDescription = null;
  ontrack = null;
  onicecandidate = null;
  onnegotiationneeded = null;
  onconnectionstatechange = null;
  oniceconnectionstatechange = null;
  constructor() {
    FakePeer.made += 1;
  }
  addTrack() {}
  addTransceiver() {}
  getSenders() {
    return [];
  }
  getTransceivers() {
    return [];
  }
  setConfiguration() {}
  restartIce() {}
  close() {
    this.signalingState = "closed";
  }
}

function browser() {
  const track = new FakeTrack();
  let allow: () => void = () => undefined;
  const mic = new Promise<FakeStream>((r) => (allow = () => r(new FakeStream([track]))));
  const noop = () => undefined;
  vi.stubGlobal("MediaStream", FakeStream);
  vi.stubGlobal("RTCPeerConnection", FakePeer);
  vi.stubGlobal("window", { addEventListener: noop, removeEventListener: noop });
  vi.stubGlobal("document", { addEventListener: noop, removeEventListener: noop, visibilityState: "visible" });
  vi.stubGlobal("navigator", { mediaDevices: { getUserMedia: () => mic, enumerateDevices: async () => [], addEventListener: noop, removeEventListener: noop } });
  const sent: string[] = [];
  let listening = 0;
  const t: Transport = {
    state: async () => decodeDirect({ active: true, seat: "a" }),
    send: async (kind) => void sent.push(kind),
    presence: async () => undefined,
    beacon: () => undefined,
    listen: () => {
      listening += 1;
      return () => undefined;
    },
  };
  return { t, track, allow, sent, listening: () => listening };
}

afterEach(() => {
  vi.unstubAllGlobals();
  FakePeer.made = 0;
});

describe("bộ máy gọi: đóng lúc đang chờ micro", () => {
  it("đối chứng: không đóng ⇒ lấy micro, dựng kết nối, nghe tín hiệu, chào", async () => {
    const b = browser();
    const e = new CallEngine(b.t, decodeDirect({ active: true, seat: "a" }));
    const started = e.start(false);
    b.allow();
    expect(await started).toBe(true);
    expect(e.snap.phase).toBe("waiting");
    expect(b.sent).toEqual(["hello"]);
    expect(b.listening()).toBe(1);
    expect(FakePeer.made).toBe(1);
    expect(b.track.stopped).toBe(false);
    e.close("closed");
    expect(b.track.stopped).toBe(true);
  });

  it("đóng trước khi người dùng cho phép micro ⇒ micro nhả ngay, không chào, không nghe, không dựng kết nối", async () => {
    const b = browser();
    const e = new CallEngine(b.t, decodeDirect({ active: true, seat: "a" }));
    const started = e.start(false);
    expect(e.snap.phase).toBe("media");
    e.close("closed");
    b.allow();
    expect(await started).toBe(false);
    expect(b.track.stopped).toBe(true);
    expect(b.sent).toEqual([]);
    expect(b.listening()).toBe(0);
    expect(FakePeer.made).toBe(0);
    expect(e.snap.phase).toBe("closed");
    expect(await e.start(false)).toBe(false);   // bộ máy đã đóng không dùng lại
  });
});
