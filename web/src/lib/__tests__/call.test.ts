import { describe, expect, it } from "vitest";
import { callStatus, clock, decodeDirect, decodeSignal, flowOf, forCurrent, helloKind, initialOf, mediaError, recovery, shortName } from "../call";
import { callPage } from "../form";
import { publicLinkPath } from "../server/bff";

describe("gọi trực tiếp (0114 bổ sung 07/11)", () => {
  it("giải mã trạng thái trang gọi; máy ICE chỉ nhận stun/turn, thiếu trường ⇒ mặc định an toàn", () => {
    const s = decodeDirect({
      call_id: "c", title: "Gọi khách", active: true, seat: "b", polite: true, peer: { name: "An", outsider: false }, cursor: 41,
      once_ice: { servers: [{ urls: ["stun:stun.l.google.com:19302"] }, { urls: ["turn:t.vd:3478", "javascript:x"], username: "1:ab", credential: "k" }, { urls: ["http://x"] }], turn: true, ttl_s: 3600 },
    });
    expect(s.seat).toBe("b");
    expect(s.ice?.servers).toEqual([{ urls: ["stun:stun.l.google.com:19302"] }, { urls: ["turn:t.vd:3478"], username: "1:ab", credential: "k" }]);
    const bare = decodeDirect({});
    expect([bare.seat, bare.active, bare.ice, bare.peer, bare.cursor]).toEqual([null, false, null, null, 0]);
  });

  it("thư của kết nối cũ / cửa sổ cũ bị bỏ; lời chào phân biệt người mới và cửa sổ khác của mình", () => {
    const peer = { sid: "p", gen: 2 };
    expect(forCurrent({ sid: "p", gen: 2, tg: 3 }, peer, 3)).toBe(true);
    expect(forCurrent({ sid: "p", gen: 1, tg: 3 }, peer, 3)).toBe(false);   // thế hệ cũ của bên kia
    expect(forCurrent({ sid: "p", gen: 2, tg: 2 }, peer, 3)).toBe(false);   // gửi cho kết nối cũ của mình
    expect(forCurrent({ sid: "q", gen: 2, tg: 3 }, peer, 3)).toBe(false);
    expect(forCurrent({ sid: "p", gen: 2, tg: 3 }, null, 3)).toBe(false);
    expect(helloKind({ sid: "p", gen: 2 }, peer, "me")).toBe("same");
    expect(helloKind({ sid: "p", gen: 3 }, peer, "me")).toBe("new");
    expect(helloKind({ sid: "z", gen: 1, to: "me" }, null, "me")).toBe("new");
    expect(helloKind({ sid: "z", gen: 1, to: "khac" }, null, "me")).toBe("other-window");
    expect(helloKind({ gen: 1 }, null, "me")).toBe("bad");
    expect(decodeSignal({ id: 5, kind: "bye", body: { sid: "p" } })).toEqual({ id: 5, kind: "bye", body: { sid: "p" } });
    expect(decodeSignal({ id: 5, kind: "text", body: {} })).toBeNull();
  });

  it("mất nối: đợi, khởi động lại ICE, rồi dựng lại", () => {
    expect(recovery("disconnected", 1000, false)).toBe("wait");
    expect(recovery("disconnected", 3500, false)).toBe("restart");
    expect(recovery("failed", 0, false)).toBe("restart");
    expect(recovery("checking", 10_000, true)).toBe("wait");
    expect(recovery("disconnected", 21_000, true)).toBe("rebuild");
    expect(recovery("closed", 0, false)).toBe("rebuild");
  });

  it("lỗi micro ⇒ việc người dùng làm được", () => {
    expect(mediaError({ name: "NotAllowedError" }, "micro").code).toBe("denied");
    expect(mediaError({ name: "NotFoundError" }, "micro").message).toContain("Không thấy micro");
    expect(mediaError({ name: "NotReadableError" }, "camera").message).toContain("Camera đang bị ứng dụng hoặc tab khác dùng");
    expect(mediaError({ name: "AbortError" }, "camera").code).toBe("busy");
    expect(mediaError({ name: "AbortError" }, "camera").message).toContain("Cuộc gọi tiếng vẫn chạy");
    expect(mediaError(new Error("x"), "micro").code).toBe("other");
  });

  it("đếm gói tin từ getStats và biết khi đi qua máy chuyển tiếp", () => {
    const f = flowOf([
      { id: "i", type: "inbound-rtp", kind: "audio", packetsReceived: 120 },
      { id: "o", type: "outbound-rtp", kind: "audio", packetsSent: 118 },
      { id: "v", type: "inbound-rtp", kind: "video", packetsReceived: 30 },
      { id: "p", type: "candidate-pair", state: "succeeded", nominated: true, localCandidateId: "l" },
      { id: "l", type: "local-candidate", candidateType: "relay" },
    ]);
    expect(f).toEqual({ audioIn: 120, audioOut: 118, videoIn: 30, videoOut: 0, relay: true, path: "relay" });
    const pair = (l: string, r: string) => flowOf([
      { id: "p", type: "candidate-pair", state: "succeeded", nominated: true, localCandidateId: "l", remoteCandidateId: "r" },
      { id: "l", type: "local-candidate", candidateType: l }, { id: "r", type: "remote-candidate", candidateType: r },
    ]).path;
    expect([pair("host", "host"), pair("srflx", "host"), pair("prflx", "srflx"), pair("host", "relay")]).toEqual(["host", "srflx", "srflx", "relay"]);
    expect(flowOf([]).path).toBe("");
    expect(clock(65)).toBe("01:05");
    expect(clock(3725)).toBe("1:02:05");
  });

  it("trang gọi là đường tương đối của chính web; link công khai chỉ mở thêm đúng tín hiệu / có mặt / nghe", () => {
    expect(callPage({ call_page: "/goi/0b9d6f0e-7a51-4b6c-9d0e-2b8f3c1d4e5f", once_open_url: "http://localhost:3100/goi/x" })).toBe("/goi/0b9d6f0e-7a51-4b6c-9d0e-2b8f3c1d4e5f");
    for (const bad of ["https://ac.vn/goi/x", "//ac.vn/goi/0b9d6f0e-7a51-4b6c-9d0e-2b8f3c1d4e5f", "/goi/x", 3]) expect(callPage({ call_page: bad })).toBeNull();
    const t = "a".repeat(43);
    expect(publicLinkPath([t, "signal"], "POST")).toBe(`/v1/public/links/${t}/signal`);
    expect(publicLinkPath([t, "presence"], "POST")).toBe(`/v1/public/links/${t}/presence`);
    expect(publicLinkPath([t, "stream"], "GET")).toBe(`/v1/public/links/${t}/stream`);
    expect(publicLinkPath([t, "stream"], "POST")).toBeNull();
    expect(publicLinkPath([t, "signal"], "GET")).toBeNull();
    expect(publicLinkPath([t, "end"], "POST")).toBeNull();
  });

  it("bảng gọi: một dòng trạng thái gọn theo pha, tên ngắn và chữ cái ảnh đại diện", () => {
    expect(shortName("Bùi Thanh Tùng")).toBe("Tùng");
    expect(shortName("Chị Lan (khách)")).toBe("Lan");
    expect(shortName(null)).toBe("người kia");
    expect(initialOf("Bùi Thanh Tùng")).toBe("T");
    expect(initialOf("phạm minh đức")).toBe("Đ");
    expect(initialOf("")).toBe("");
    const peer = "Bùi Thanh Tùng";
    expect(callStatus({ phase: "waiting", ended: false, peerName: peer })).toEqual({ text: "Đang chờ Tùng vào…", tone: "muted", clock: "waiting" });
    expect(callStatus({ phase: "waiting", ended: false, peerName: peer, noAnswer: true }).tone).toBe("warn");
    expect(callStatus({ phase: "waiting", ended: false, peerName: peer, peerLeft: true }).text).toBe("Tùng đã rời, đang chờ vào lại…");
    expect(callStatus({ phase: "connected", ended: false, peerName: peer })).toEqual({ text: "Đã kết nối", tone: "ok", clock: "connected" });
    expect(callStatus({ phase: "reconnecting", ended: false, peerName: peer }).text).toBe("Mất kết nối, đang nối lại…");
    expect(callStatus({ phase: "connected", ended: true, peerName: peer }).text).toBe("Cuộc gọi đã kết thúc");
    expect(callStatus({ phase: null, ended: false, peerName: null }).text).toBe("Chưa vào cuộc gọi");
    expect(callStatus({ phase: "connecting", ended: false, peerName: null }).text).toBe("Đang nối với người kia…");
  });
});
