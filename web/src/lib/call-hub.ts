import { memberSession, type CallSession, type Entry, type SessionPhase, type SessionView } from "./call-session";
import { memberTransport } from "./call-transport";

// Nơi giữ MỘT cuộc gọi đang mở trong trang (core 0114, ghi chú 07/11 "bảng gọi nổi"): gắn một lần ở khung ngoài cùng của web (components/call/call-provider),
// nên đổi trang không làm rớt cuộc gọi. Một người một cuộc gọi đang hoạt động: bấm gọi / tham gia cuộc thứ hai khi đang giữ cuộc thứ nhất ⇒ hỏi rời cuộc
// thứ nhất TRƯỚC khi gửi lệnh (`ask`), để server không tạo cuộc gọi thứ hai rồi bỏ đó. Không ghi hình, không giữ gì sau khi rời.

export type HubPhase = "idle" | SessionPhase;
/** Lệnh đã tạo / ngồi vào cuộc gọi mới thì "Ở lại" phải gỡ lại: người gọi huỷ (``end``), người tham gia rời (``leave``). */
export type Undo = "end" | "leave";
export type Conflict =
  | { kind: "ask"; inline: boolean }   // hỏi trước khi gửi lệnh gọi / tham gia; `inline`: form đang mở tự vẽ câu hỏi (hộp thoại form giữ tiêu điểm)
  | { kind: "opened"; callId: string; entry: Entry; undo: Undo | null };   // lệnh đã chạy (hai nơi bấm gần nhau): cuộc mới đã có ở server
export type HubView = {
  phase: HubPhase;
  callId: string | null;
  session: SessionView | null;
  collapsed: boolean;
  fullscreen: boolean;
  /** cuộc gọi mới đang chờ người dùng đồng ý rời cuộc gọi hiện tại */
  conflict: Conflict | null;
  /** đang rời cuộc cũ để sang cuộc mới (nút trong hộp hỏi khoá lại) */
  switching: boolean;
  notice: string | null;
};
export type OpenResult = "opened" | "same" | "conflict";
export type HubDeps = {
  makeSession?: (callId: string, entry: Entry) => CallSession;
  /** mở một thẻ trình duyệt mới (cử chỉ người dùng); false = bị chặn */
  openWindow?: (url: string) => boolean;
  /** gỡ cuộc gọi mới khi người dùng chọn ở lại cuộc cũ */
  undo?: (callId: string, how: Undo) => Promise<void>;
};

const PAGE = /^\/goi\/([0-9a-f-]{36})(?:\?(.*))?$/;

/** Địa chỉ trang gọi do server gửi (`/goi/<mã>[?auto=1[&video=1]]`) ⇒ mã cuộc gọi + cách vào; dạng khác ⇒ null. */
export function parseCallPage(page: string): { callId: string; entry: Entry } | null {
  const m = PAGE.exec(page);
  if (!m) return null;
  const q = new URLSearchParams(m[2] ?? "");
  const auto = q.get("auto") === "1";
  return { callId: m[1], entry: { auto, video: auto && q.get("video") === "1" } };
}

/** Địa chỉ trang đứng riêng của cùng cuộc gọi (đường lùi khi bảng nổi không dùng được micro / camera, và cho link gửi người ngoài). */
export function standalonePage(callId: string, entry: Entry): string {
  return `/goi/${callId}?auto=1${entry.video ? "&video=1" : ""}`;
}

/** Lệnh này có mở / ngồi vào một cuộc gọi trực tiếp KHÁC cuộc `current` không (để hỏi trước khi gửi). Gọi bằng link Meet / Zoom ngoài không tính. Nút Gọi
 *  hằng ngày là `meet.ring`, Tham gia là `meet.join` có `enter` (0114 ghi chú "nút Gọi một chạm"); `meet.start` là form đầy đủ của quản trị / API. */
export function startsCall(command: string, input: Record<string, unknown>, current: string | null): boolean {
  const link = typeof input.join_url === "string" && input.join_url.trim() !== "";
  if (command === "meet.ring") return !link && input.via_link !== true;
  if (command === "meet.start") return !link && input.provider !== "external";
  if (command === "meet.join") return input.enter === true && input.call_id !== current;
  return false;
}

/** Đầu ra lệnh ⇒ "Ở lại" phải gỡ gì: mình vừa tạo cuộc gọi (ghế a, không phải vào chung) ⇒ huỷ; mình vừa ngồi vào cuộc của người khác ⇒ rời. */
export function undoOf(output: unknown): Undo | null {
  if (!output || typeof output !== "object") return null;
  const o = output as Record<string, unknown>;
  if (o.already === false && o.seat === "a") return "end";
  return typeof o.seat === "string" ? "leave" : null;
}

/** Bảng nổi tự vẽ câu hỏi thành lớp phủ? Không, khi form đang mở đã vẽ nó bên trong hộp thoại của form. */
export function overlayConflict(c: Conflict | null): boolean {
  return !!c && !(c.kind === "ask" && c.inline);
}

function browserOpen(url: string): boolean {
  const w = window.open(url, "_blank");
  if (!w) return false;
  w.opener = null;
  return true;
}

async function serverUndo(callId: string, how: Undo): Promise<void> {
  const t = memberTransport(callId);
  await (how === "end" ? t.end() : t.leave());
}

export class CallHub {
  private current: CallSession | null = null;
  private unsub: (() => void) | null = null;
  private collapsed = false;
  private fullscreen = false;
  private conflict: Conflict | null = null;
  private asked: ((ok: boolean) => void) | null = null;
  private switching = false;
  private notice: string | null = null;
  private listeners = new Set<() => void>();
  private view: HubView;
  private readonly makeSession: (callId: string, entry: Entry) => CallSession;
  private readonly openWindow: (url: string) => boolean;
  private readonly undo: (callId: string, how: Undo) => Promise<void>;

  constructor(deps: HubDeps = {}) {
    this.makeSession = deps.makeSession ?? ((id, entry) => memberSession(id, entry));
    this.openWindow = deps.openWindow ?? browserOpen;
    this.undo = deps.undo ?? serverUndo;
    this.view = this.build();
  }

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  getSnapshot = (): HubView => this.view;

  private build(): HubView {
    const s = this.current?.getSnapshot() ?? null;
    return {
      phase: s ? s.phase : "idle",
      callId: this.current?.key ?? null,
      session: s,
      collapsed: this.collapsed,
      fullscreen: this.fullscreen,
      conflict: this.conflict,
      switching: this.switching,
      notice: this.notice,
    };
  }

  private emit(): void {
    this.view = this.build();
    for (const fn of [...this.listeners]) fn();
  }

  /** Đang giữ một cuộc gọi (đang vào / đã vào, chưa kết thúc). Bảng mới mở mà chưa vào (vd micro bị chặn) không tính: cuộc mới thay thẳng. */
  holds(): boolean {
    return !!this.current?.getSnapshot().holds;
  }

  private start(callId: string, entry: Entry): void {
    const s = this.makeSession(callId, entry);
    this.current = s;
    this.unsub = s.subscribe(() => this.emit());
    this.collapsed = false;
    this.fullscreen = false;
    this.notice = null;
    this.emit();
    s.init();
  }

  private drop(): void {
    this.unsub?.();
    this.unsub = null;
    this.current?.dispose();
    this.current = null;
    this.collapsed = false;
    this.fullscreen = false;
  }

  /** Mở bảng nổi cho một cuộc gọi (vào ngay nếu `entry.auto`). `undo`: lệnh vừa tạo / ngồi vào cuộc này — chọn ở lại cuộc cũ thì gỡ lại. */
  open(callId: string, entry: Entry = { auto: false, video: false }, undo: Undo | null = null): OpenResult {
    const cur = this.current;
    if (cur) {
      if (cur.key === callId) {
        this.collapsed = false;
        this.emit();
        return "same";
      }
      if (this.holds()) {
        this.conflict = { kind: "opened", callId, entry, undo };
        this.emit();
        return "conflict";
      }
      this.drop();
    }
    this.start(callId, entry);
    return "opened";
  }

  /** Địa chỉ `/goi/...` do server gửi; không đọc được ⇒ null (người gọi tự xử lý). */
  openPage(page: string, undo: Undo | null = null): OpenResult | null {
    const p = parseCallPage(page);
    return p ? this.open(p.callId, p.entry, undo) : null;
  }

  /** Trước khi gửi lệnh gọi / tham gia cuộc khác: đang giữ cuộc gọi ⇒ hỏi; true = được đi tiếp (không có cuộc nào, hoặc đã rời cuộc cũ). */
  ask(inline = false): Promise<boolean> {
    if (!this.holds()) return Promise.resolve(true);
    this.asked?.(false);
    return new Promise((resolve) => {
      this.asked = resolve;
      this.conflict = { kind: "ask", inline };
      this.emit();
    });
  }

  /** Đồng ý rời cuộc gọi hiện tại để sang cuộc mới. Bấm hai lần chỉ chạy một lần. */
  async confirmSwitch(): Promise<void> {
    const next = this.conflict;
    if (!next || this.switching) return;
    this.switching = true;
    this.conflict = null;
    this.emit();
    try {
      await this.current?.hangup(false);
      this.drop();
      if (next.kind === "opened") this.start(next.callId, next.entry);
      else {
        this.emit();
        this.asked?.(true);
        this.asked = null;
      }
    } finally {
      this.switching = false;
      this.emit();
    }
  }

  /** Ở lại cuộc gọi hiện tại. Cuộc mới đã có ở server (hai nơi bấm gần nhau) ⇒ gỡ lại để nó không đổ chuông mãi. */
  cancelSwitch(): void {
    const c = this.conflict;
    if (!c || this.switching) return;
    this.conflict = null;
    this.emit();
    if (c.kind === "ask") {
      this.asked?.(false);
      this.asked = null;
    } else if (c.undo) void this.undo(c.callId, c.undo).catch(() => undefined);
  }

  /** "Rời": báo kết thúc phần của mình (có mặt, tạm biệt), nhả micro / camera, đóng bảng. `endForAll`: đóng cuộc gọi cho cả hai. */
  async leave(endForAll = false): Promise<void> {
    const cur = this.current;
    if (!cur) return;
    if (cur.getSnapshot().engine) await cur.hangup(endForAll);
    if (this.current === cur) {
      this.drop();
      this.emit();
    }
  }

  /** Rời trước khi đổi phiên đăng nhập / công ty (lúc đó tín hiệu "tạm biệt" khi đóng trang sẽ đi bằng phiên sai): chờ tối đa `ms`. */
  async leaveWithin(ms = 2500): Promise<void> {
    if (!this.current) return;
    await Promise.race([this.leave(false).catch(() => undefined), new Promise<void>((r) => setTimeout(r, ms))]);
  }

  /** Đóng bảng (cuộc gọi đã kết thúc, hoặc chưa vào). Chưa vào thì không có gì để báo; thẻ cuộc gọi vẫn còn nút Vào lại / Huỷ. */
  dismiss(): void {
    if (!this.current) return;
    this.drop();
    if (this.conflict?.kind === "opened") this.conflict = null;
    this.emit();
  }

  /** Đường lùi: mở cùng cuộc gọi ở thẻ riêng (trình duyệt không cho micro / camera trong bảng nổi). Thẻ mở được thì phần của bảng nổi rời đi trước. */
  async openInTab(): Promise<boolean> {
    const cur = this.current;
    if (!cur) return false;
    const url = standalonePage(cur.key, cur.entry ?? { auto: true, video: false });
    if (!this.openWindow(url)) {
      this.notice = "Trình duyệt chặn cửa sổ mới. Cho phép cửa sổ bật lên cho trang này rồi bấm lại.";
      this.emit();
      return false;
    }
    await this.leave(false);
    return true;
  }

  /** Vào cuộc gọi (khi chưa vào hoặc vào lại); `listenOnly`: không dùng micro. */
  enter(listenOnly = false): void {
    void this.current?.enter(listenOnly);
  }

  /** Phần mời / kết thúc của thành viên (chỉ có khi phiên là của thành viên đăng nhập). */
  member(): CallSession["member"] {
    return this.current?.member;
  }

  reload(): void {
    void this.current?.reload();
  }

  offerCamera(): void {
    this.current?.offerCamera();
  }

  setCollapsed(v: boolean): void {
    this.collapsed = v;
    this.emit();
  }

  setFullscreen(v: boolean): void {
    this.fullscreen = v;
    this.emit();
  }
}
