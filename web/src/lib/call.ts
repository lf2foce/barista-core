// Gọi trực tiếp 1–1 (core 0114 bổ sung 07/11): phần THUẦN (kiểm thử được, không cần trình duyệt) — giải mã trạng thái server gửi, lọc tín hiệu cũ,
// đổi lỗi micro / camera thành câu cho người dùng, đọc số gói tin từ getStats. Phần chạy WebRTC ở call-engine.ts.

export type IceServer = { urls: string[]; username?: string; credential?: string };
export type DirectState = {
  callId: string;
  title: string;
  active: boolean;
  seat: "a" | "b" | null;
  polite: boolean;
  peer: { name: string; outsider: boolean } | null;
  cursor: number;
  ice: { servers: IceServer[]; turn: boolean; ttlS: number | null } | null;
  invite: { name: string; expiresAt: string } | null;
  // chỉ trang người ngoài
  company?: string;
  invitee?: string;
};

const str = (v: unknown, d = ""): string => (typeof v === "string" ? v : d);
const obj = (v: unknown): Record<string, unknown> => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {});

/** Trạng thái từ `meet.direct` (thành viên) hoặc `meet.link_view` (người ngoài). Trường thiếu ⇒ mặc định an toàn (chưa có ghế, chưa có máy TURN). */
export function decodeDirect(raw: unknown): DirectState {
  const o = obj(raw);
  const seat = o.seat === "a" || o.seat === "b" ? o.seat : null;
  const peer = o.peer ? obj(o.peer) : null;
  const ice = o.once_ice ? obj(o.once_ice) : null;
  const inv = o.invite ? obj(o.invite) : null;
  return {
    callId: str(o.call_id),
    title: str(o.title, "Cuộc gọi"),
    active: o.active === true,
    seat,
    polite: o.polite === true,
    peer: peer ? { name: str(peer.name, "Người kia"), outsider: peer.outsider === true } : null,
    cursor: typeof o.cursor === "number" ? o.cursor : 0,
    ice: ice
      ? {
          servers: (Array.isArray(ice.servers) ? ice.servers : []).flatMap((s) => {
            const x = obj(s);
            const urls = (Array.isArray(x.urls) ? x.urls : []).filter((u): u is string => typeof u === "string" && /^(stun|turns?):/.test(u));
            if (!urls.length) return [];
            return [{ urls, ...(typeof x.username === "string" ? { username: x.username } : {}), ...(typeof x.credential === "string" ? { credential: x.credential } : {}) }];
          }),
          turn: ice.turn === true,
          ttlS: typeof ice.ttl_s === "number" ? ice.ttl_s : null,
        }
      : null,
    invite: inv ? { name: str(inv.name), expiresAt: str(inv.expires_at) } : null,
    company: typeof o.company === "string" ? o.company : undefined,
    invitee: typeof o.invitee === "string" ? o.invitee : undefined,
  };
}

/** Một tín hiệu nhận qua kênh SSE (`event: signal`). */
export type Signal = { id: number; kind: "hello" | "description" | "candidate" | "bye"; body: Record<string, unknown> };
export function decodeSignal(data: Record<string, unknown>): Signal | null {
  const kind = data.kind;
  if (typeof data.id !== "number" || (kind !== "hello" && kind !== "description" && kind !== "candidate" && kind !== "bye")) return null;
  return { id: data.id, kind, body: obj(data.body) };
}

/** Phiên của người kia mà mình đang nối (mã cửa sổ + thế hệ kết nối — dựng lại kết nối ⇒ thế hệ mới). */
export type PeerRef = { sid: string; gen: number };

/** Thư mô tả / ICE có dành cho kết nối HIỆN TẠI của mình không: đúng người kia, đúng thế hệ của họ, gửi tới đúng thế hệ của mình. Thư cũ (trước khi
 *  một bên dựng lại kết nối, hay của cửa sổ cũ) bỏ qua — đưa vào kết nối mới sẽ làm hỏng nó. */
export function forCurrent(body: Record<string, unknown>, peer: PeerRef | null, myGen: number): boolean {
  return !!peer && body.sid === peer.sid && body.gen === peer.gen && body.tg === myGen;
}

/** Lời chào: "new" = người kia mới / vừa dựng lại kết nối (mình dựng lại theo), "same" = đã biết, "other-window" = trả lời một cửa sổ khác của chính
 *  mình (cuộc gọi đang mở ở cửa sổ khác). */
export function helloKind(body: Record<string, unknown>, peer: PeerRef | null, mySid: string): "new" | "same" | "other-window" | "bad" {
  if (typeof body.sid !== "string" || typeof body.gen !== "number") return "bad";
  if (typeof body.to === "string" && body.to !== mySid) return "other-window";
  return peer && peer.sid === body.sid && peer.gen === body.gen ? "same" : "new";
}

/** Lỗi getUserMedia ⇒ việc người dùng làm được. */
export function mediaError(e: unknown, what: "micro" | "camera"): { code: "denied" | "missing" | "busy" | "insecure" | "other"; message: string } {
  const name = e && typeof e === "object" && "name" in e ? String((e as { name: unknown }).name) : "";
  if (name === "NotAllowedError" || name === "PermissionDeniedError")
    return { code: "denied", message: `Trình duyệt đang chặn ${what}. Bấm biểu tượng ổ khoá cạnh địa chỉ trang, cho phép ${what}, rồi bấm Thử lại.` };
  if (name === "NotFoundError" || name === "DevicesNotFoundError" || name === "OverconstrainedError")
    return { code: "missing", message: `Không thấy ${what} nào trên máy. Cắm ${what} rồi bấm Thử lại.` };
  // AbortError: Chrome trên macOS báo khi thiết bị không khởi động kịp — thường vì một ứng dụng / trình duyệt / tab khác đang giữ nó
  // (thử gọi hai tài khoản trên cùng một máy là gặp ngay).
  if (name === "NotReadableError" || name === "TrackStartError" || name === "AbortError")
    return {
      code: "busy",
      message: `${what === "micro" ? "Micro" : "Camera"} đang bị ứng dụng hoặc tab khác dùng (kể cả cửa sổ trình duyệt khác trên cùng máy). Tắt nơi đó rồi bấm lại.${what === "camera" ? " Cuộc gọi tiếng vẫn chạy." : ""}`,
    };
  if (name === "SecurityError" || name === "TypeError")
    return { code: "insecure", message: "Trang phải mở bằng https mới dùng được micro / camera." };
  return { code: "other", message: `Không mở được ${what}${name ? ` (${name})` : ""}.` };
}

/** Số gói tin tiếng / hình đã nhận và đã gửi, đọc từ RTCPeerConnection.getStats() (đã chuyển thành mảng). */
/** Đường đang dùng: `host` = cùng mạng nội bộ (cùng văn phòng), `srflx` = thẳng qua internet, `relay` = vòng qua máy chuyển tiếp (TURN); "" = chưa nối. */
export type CallPath = "host" | "srflx" | "relay" | "";
export type Flow = { audioIn: number; audioOut: number; videoIn: number; videoOut: number; relay: boolean; path: CallPath };
export function flowOf(stats: Record<string, unknown>[]): Flow {
  const f: Flow = { audioIn: 0, audioOut: 0, videoIn: 0, videoOut: 0, relay: false, path: "" };
  const byId = new Map(stats.map((s) => [str(s.id), s]));
  for (const s of stats) {
    const kind = s.kind ?? s.mediaType;
    if (s.type === "inbound-rtp" && typeof s.packetsReceived === "number") {
      if (kind === "audio") f.audioIn += s.packetsReceived;
      if (kind === "video") f.videoIn += s.packetsReceived;
    }
    if (s.type === "outbound-rtp" && typeof s.packetsSent === "number") {
      if (kind === "audio") f.audioOut += s.packetsSent;
      if (kind === "video") f.videoOut += s.packetsSent;
    }
    if (s.type === "candidate-pair" && s.state === "succeeded" && s.nominated === true) {
      const types = [byId.get(str(s.localCandidateId))?.candidateType, byId.get(str(s.remoteCandidateId))?.candidateType];
      f.relay = types[0] === "relay";
      // một đầu qua máy chuyển tiếp ⇒ relay; cả hai địa chỉ nội bộ ⇒ cùng mạng; còn lại (srflx / prflx) ⇒ thẳng qua internet
      f.path = types.includes("relay") ? "relay" : types.every((t) => t === "host") ? "host" : "srflx";
    }
  }
  return f;
}

/** Sau khi mất nối: đợi bao lâu rồi làm gì. "disconnected" thường tự hồi trong vài giây; "failed" thì khởi động lại ICE ngay; khởi động lại ICE
 *  không xong sau 20 giây (hoặc kết nối đã đóng) thì dựng lại kết nối mới. */
export function recovery(ice: string, sinceMs: number, restarted: boolean): "wait" | "restart" | "rebuild" {
  if (ice === "closed") return "rebuild";
  if (restarted) return sinceMs > 20_000 ? "rebuild" : "wait";
  if (ice === "failed") return "restart";
  if (ice === "disconnected") return sinceMs > 3_000 ? "restart" : "wait";
  return "wait";
}

/** Giây ⇒ "mm:ss" / "h:mm:ss" cho đồng hồ cuộc gọi. */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const mm = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

/** Tên gọi ngắn trong câu trạng thái ("Đang chờ Tùng vào…"): bỏ phần trong ngoặc, lấy chữ cuối (tên riêng tiếng Việt). Trống ⇒ "người kia". */
export function shortName(name: string | null | undefined): string {
  const words = (name ?? "").replace(/\([^)]*\)/g, " ").trim().split(/\s+/).filter(Boolean);
  return words.length ? words[words.length - 1] : "người kia";
}

/** Chữ cái trên ảnh đại diện của người kia: chữ đầu của tên riêng (chữ cuối), viết hoa; không có tên ⇒ "". */
export function initialOf(name: string | null | undefined): string {
  const n = shortName(name);
  return n === "người kia" && !name?.trim() ? "" : n.charAt(0).toLocaleUpperCase("vi");
}

/** Một dòng trạng thái gọn của cuộc gọi (đầu bảng gọi, thanh thu gọn). ``clock``: đồng hồ nào nên chạy kèm (đã nối / đang chờ). */
export type CallTone = "ok" | "warn" | "muted";
export function callStatus(o: {
  phase: string | null;
  ended: boolean;
  peerName: string | null | undefined;
  peerLeft?: boolean;
  noAnswer?: boolean;
}): { text: string; tone: CallTone; clock: "connected" | "waiting" | null } {
  const n = shortName(o.peerName);
  if (o.ended || o.phase === "ended") return { text: "Cuộc gọi đã kết thúc", tone: "muted", clock: null };
  switch (o.phase) {
    case "media":
      return { text: "Đang mở micro…", tone: "muted", clock: null };
    case "waiting":
      if (o.peerLeft) return { text: `${n} đã rời, đang chờ vào lại…`, tone: "muted", clock: "waiting" };
      if (o.noAnswer) return { text: `${n} chưa vào`, tone: "warn", clock: "waiting" };
      return { text: `Đang chờ ${n} vào…`, tone: "muted", clock: "waiting" };
    case "connecting":
      return { text: `Đang nối với ${n}…`, tone: "muted", clock: null };
    case "connected":
      return { text: "Đã kết nối", tone: "ok", clock: "connected" };
    case "reconnecting":
      return { text: "Mất kết nối, đang nối lại…", tone: "warn", clock: null };
    case "closed":
      return { text: "Đã rời cuộc gọi", tone: "muted", clock: null };
    default:
      return { text: "Chưa vào cuộc gọi", tone: "muted", clock: null };
  }
}
