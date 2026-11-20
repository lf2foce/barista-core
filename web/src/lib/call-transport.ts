import { decodeDirect, decodeSignal, type DirectState, type Signal } from "./call";
import { newKey } from "./idempotency";
import { connectAction, SseParser } from "./sse";

// Đường tới core cho trang gọi trực tiếp: thành viên qua BFF có phiên (/api/core), người ngoài qua BFF link công khai (/api/public/<mã>, không
// cookie, không danh tính — core 0064). Hai bên dùng chung một bộ máy gọi (call-engine.ts).

export type StreamEnd = "gone" | "auth" | "ended";
export type Transport = {
  /** Trạng thái lúc mở (ghế, người kia, máy STUN / TURN tạm, mốc tín hiệu). */
  state(): Promise<DirectState>;
  send(kind: Signal["kind"], body: Record<string, unknown>): Promise<void>;
  presence(event: "connected" | "disconnected" | "ended", detail?: string, clientAt?: Date): Promise<void>;
  /** Gửi khi trang đang đóng (pagehide): sendBeacon — không chờ được trả lời. */
  beacon(kind: "presence" | "signal", body: Record<string, unknown>): void;
  /** Nghe tín hiệu gửi cho mình từ mốc `after`; tự nối lại khi rớt mạng (không mất thư — nối lại từ thư cuối đã nhận). Trả hàm dừng. */
  listen(after: number, on: (s: Signal) => void, end: (why: StreamEnd) => void, onLink?: (up: boolean) => void, onPeer?: (key: string) => void): () => void;
};

export class CallError extends Error {
  constructor(
    message: string,
    public status = 0,
  ) {
    super(message);
  }
}

async function envelope(res: Response): Promise<unknown> {
  const body = (await res.json().catch(() => ({}))) as { status?: string; output?: unknown; error?: string; detail?: string };
  if (res.status === 401 && typeof window !== "undefined" && !location.pathname.startsWith("/c/"))
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    location.href = `/login?next=${encodeURIComponent(location.pathname)}`;
  if (!res.ok || (body.status && body.status !== "ok")) throw new CallError(body.error || body.detail || `Lỗi (${res.status})`, res.status);
  return body.output;
}

const json = (b: unknown, key = false): RequestInit => ({
  method: "POST",
  headers: { "content-type": "application/json", ...(key ? { "idempotency-key": newKey() } : {}) },
  body: JSON.stringify(b),
  credentials: "same-origin",
  cache: "no-store",
});

function beaconTo(url: string, body: unknown): void {
  const blob = new Blob([JSON.stringify(body)], { type: "application/json" });
  if (!navigator.sendBeacon?.(url, blob)) void fetch(url, { ...json(body), keepalive: true }).catch(() => undefined);
}

/** Vòng nghe SSE chung: thư `signal` ⇒ `on`; `ended` / `gone` / `auth` ⇒ dừng; `retry` / mất mạng ⇒ nối lại sau 1→8 giây từ thư cuối đã nhận. */
function listenSse(urlOf: (after: number) => string, after: number, on: (s: Signal) => void, end: (why: StreamEnd) => void,
  onLink?: (up: boolean) => void, onPeer?: (key: string) => void): () => void {
  const ctl = new AbortController();
  let last = after;
  (async () => {
    let wait = 1;
    while (!ctl.signal.aborted) {
      try {
        const res = await fetch(urlOf(last), { credentials: "same-origin", cache: "no-store", signal: ctl.signal });
        const act = connectAction(res.status);
        if (act === "gone" || act === "auth") return end(act);
        if (act !== "ok" || !res.body) throw new Error(String(res.status));
        onLink?.(true);
        wait = 1;
        const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
        const parser = new SseParser();
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          for (const ev of parser.push(value)) {
            if (ev.event === "signal") {
              const s = decodeSignal(ev.data);
              if (s && s.id > last) {
                last = s.id;
                on(s);
              }
            } else if (ev.event === "peer") {
              // nhãn người kia CÒN được ở trong cuộc gọi; rỗng = bị thu hồi / rút đồng ý / mất quyền / trả chỗ ⇒ trang đóng kết nối với họ
              onPeer?.(typeof ev.data.peer === "string" ? ev.data.peer : "");
            } else if (ev.event === "ended" || ev.event === "gone" || ev.event === "auth") return end(ev.event);
            // "retry": lỗi tạm phía server — kênh tự đóng, vòng dưới nối lại
          }
        }
      } catch {
        if (ctl.signal.aborted) return;
      }
      onLink?.(false);
      await new Promise((r) => setTimeout(r, wait * 1000));
      wait = Math.min(wait * 2, 8);
    }
  })();
  return () => ctl.abort();
}

/** Thành viên đã đăng nhập: cuộc gọi theo mã. */
export function memberTransport(callId: string): Transport & {
  join(): Promise<void>;
  /** Ghi mình rời (``meet.leave``) — dùng khi bị đóng giữa lúc đang vào, chưa kịp báo có mặt. */
  leave(): Promise<void>;
  end(): Promise<void>;
  invite(name: string, hours: number, adult: boolean): Promise<{ url: string; summary: string }>;
  revokeInvite(): Promise<void>;
} {
  const id = encodeURIComponent(callId);
  const cmd = async (name: string, input: Record<string, unknown>, key = true) =>
    envelope(await fetch(`/api/core/v1/commands/${name}`, json({ call_id: callId, ...input }, key)));
  return {
    state: async () => decodeDirect(await envelope(await fetch("/api/core/v1/queries/meet.direct", json({ call_id: callId })))),
    send: async (kind, body) => {
      await envelope(await fetch(`/api/core/v1/meet/calls/${id}/signal`, json({ kind, body })));
    },
    presence: async (event, detail = "", clientAt) => {
      await cmd("meet.presence", { event, detail, ...(clientAt ? { client_at: clientAt.toISOString() } : {}) }, false);
    },
    beacon: (kind, body) =>
      beaconTo(kind === "signal" ? `/api/core/v1/meet/calls/${id}/signal` : "/api/core/v1/commands/meet.presence", kind === "signal" ? body : { call_id: callId, ...body }),
    listen: (after, on, end, onLink, onPeer) => listenSse((a) => `/api/core/v1/meet/calls/${id}/stream?after=${a}`, after, on, end, onLink, onPeer),
    join: async () => {
      await cmd("meet.join", {});
    },
    leave: async () => {
      await cmd("meet.leave", {});
    },
    end: async () => {
      await cmd("meet.end", {});
    },
    invite: async (name, hours, adult) => {
      const o = (await cmd("meet.invite", { name, hours, adult_declared: adult })) as { once_url?: string; summary?: string };
      return { url: o.once_url ?? "", summary: o.summary ?? "" };
    },
    revokeInvite: async () => {
      await cmd("meet.revoke_invite", {});
    },
  };
}

/** Người ngoài cầm link mời: mọi đường chỉ là /api/public/<mã>/… (không cookie). */
export function linkTransport(token: string): Transport {
  const base = `/api/public/${encodeURIComponent(token)}`;
  return {
    state: async () => decodeDirect(await envelope(await fetch(base, { cache: "no-store" }))),
    send: async (kind, body) => {
      await envelope(await fetch(`${base}/signal`, json({ kind, body })));
    },
    presence: async (event, detail = "", clientAt) => {
      await envelope(await fetch(`${base}/presence`, json({ event, detail, ...(clientAt ? { client_at: clientAt.toISOString() } : {}) })));
    },
    beacon: (kind, body) => beaconTo(`${base}/${kind}`, body),
    listen: (after, on, end, onLink, onPeer) => listenSse((a) => `${base}/stream?after=${a}`, after, on, end, onLink, onPeer),
  };
}
