"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { ChevronDown, ExternalLink, Maximize2, Mic, MicOff, Minimize2, Phone, PhoneOff, PictureInPicture2, X } from "lucide-react";
import { overlayConflict, type CallHub, type HubView } from "@/lib/call-hub";
import type { SessionView } from "@/lib/call-session";
import { cn } from "@/lib/utils";
import { CallAlert, CallControls, pickAlert, Stage, StatusLine } from "./call-parts";
import { Invite } from "./direct-call";
import { SwitchQuestion } from "./switch-question";

// Bảng gọi nổi (core 0114, ghi chú 07/11 "bảng gọi nổi"): cuộc gọi 1–1 bắt đầu từ thẻ / nút trao đổi chạy NGAY TRONG trang, không mở thẻ mới — trao đổi,
// hồ sơ, việc vẫn thấy và dùng được, đổi trang cuộc gọi vẫn sống (phiên nằm ở CallHub, gắn ở khung ngoài cùng). Máy tính: góc dưới phải, thu được về một
// thanh nhỏ (đồng hồ, micro, rời). Điện thoại: thanh gọn trên cùng, bấm để mở toàn màn hình. Nếu trình duyệt hỗ trợ, "Tách ra" đưa bảng vào cửa sổ
// nổi (Document Picture-in-Picture) — cùng phiên, tiếng / hình không dựng lại.

type Pip = Window;
type PipHost = { documentPictureInPicture?: { requestWindow(o?: { width?: number; height?: number }): Promise<Window> } };

const NARROW = "(max-width: 639px)";
function useNarrow(): boolean {
  return useSyncExternalStore(
    (cb) => {
      const m = window.matchMedia(NARROW);
      m.addEventListener("change", cb);
      return () => m.removeEventListener("change", cb);
    },
    () => window.matchMedia(NARROW).matches,
    () => false,
  );
}

export function CallDock({ hub }: { hub: CallHub }) {
  const v = useSyncExternalStore(hub.subscribe, hub.getSnapshot, hub.getSnapshot);
  if (v.phase === "idle" || !v.session) return null;
  return <Panel hub={hub} v={v} s={v.session} />;
}

function copyStyles(w: Window): void {
  for (const sheet of Array.from(document.styleSheets)) {
    try {
      const st = w.document.createElement("style");
      st.textContent = Array.from(sheet.cssRules).map((r) => r.cssText).join("");
      w.document.head.appendChild(st);
    } catch {
      if (sheet.href) {
        const l = w.document.createElement("link");
        l.rel = "stylesheet";
        l.href = sheet.href;
        w.document.head.appendChild(l);
      }
    }
  }
  w.document.documentElement.className = document.documentElement.className;
}

function Panel({ hub, v, s }: { hub: CallHub; v: HubView; s: SessionView }) {
  const narrow = useNarrow();
  const engine = s.engine;
  const audio = useRef<HTMLAudioElement>(null);
  const [blocked, setBlocked] = useState(false);
  const [pip, setPip] = useState<Pip | null>(null);
  const pipRef = useRef<Pip | null>(null);
  useEffect(() => {
    pipRef.current = pip;
  });
  useEffect(() => () => pipRef.current?.close(), []);

  // Tiếng người kia ở khung ngoài cùng, KHÔNG nằm trong thân bảng: chuyển sang cửa sổ nổi / thu gọn không làm tiếng đứt
  useEffect(() => {
    if (!engine || !audio.current) return;
    if (audio.current.srcObject !== engine.remote) audio.current.srcObject = engine.remote;
    // Safari điện thoại có thể dừng tiếng sau khi khoá màn hình: phát lại; bị chặn ⇒ nút "Bấm để nghe tiếp"
    audio.current.play().then(() => setBlocked(false), () => setBlocked(engine.snap.phase === "connected"));
  });

  const canPip = !narrow && typeof window !== "undefined" && !!(window as unknown as PipHost).documentPictureInPicture;
  async function popOut() {
    try {
      const w = await (window as unknown as PipHost).documentPictureInPicture!.requestWindow({ width: 360, height: 420 });
      copyStyles(w);
      w.document.body.className = "bg-surface text-foreground";
      w.addEventListener("pagehide", () => setPip(null));
      setPip(w);
    } catch {
      /* không hỗ trợ / bị từ chối: giữ bảng như cũ, không báo lỗi */
    }
  }

  const mode = pip ? "pip" : narrow ? (v.fullscreen ? "full" : "bar") : v.collapsed ? "bar" : "card";
  const body = (layout: "card" | "full" | "pip") => (
    <Body hub={hub} v={v} s={s} layout={layout} blocked={blocked} onResume={() => void audio.current?.play().then(() => setBlocked(false))}
      onPip={canPip ? () => void popOut() : undefined} />
  );

  return (
    <>
      <audio ref={audio} autoPlay playsInline className="hidden" />
      {overlayConflict(v.conflict) ? <Conflict hub={hub} switching={v.switching} /> : null}
      {pip ? (
        <>
          <div className="fixed bottom-4 right-4 z-[60] flex items-center gap-3 rounded-full border border-border bg-surface py-2 pl-4 pr-2" role="region" aria-label="Cuộc gọi đang mở">
            <span className="t14">Cuộc gọi đang ở cửa sổ nổi</span>
            <button type="button" className="t14m h-8 rounded-full bg-primary px-3 text-primary-foreground" onClick={() => pip.close()}>Đưa về</button>
          </div>
          {createPortal(body("pip"), pip.document.body)}
        </>
      ) : mode === "bar" ? (
        <Bar hub={hub} v={v} s={s} narrow={narrow} />
      ) : mode === "full" ? (
        <div className="fixed inset-0 z-[60] flex flex-col bg-canvas pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)]" role="region" aria-label="Cuộc gọi">
          {body("full")}
        </div>
      ) : (
        <div className="fixed bottom-4 right-4 z-[60] w-[340px] max-w-[calc(100vw-2rem)] rounded-2xl border border-border bg-surface" role="region" aria-label="Cuộc gọi">
          {body("card")}
        </div>
      )}
    </>
  );
}

/** Người đang trong cuộc gọi này gọi thêm một cuộc nữa (không có form nào đang mở) ⇒ hỏi rời cuộc gọi hiện tại (một người một cuộc gọi đang hoạt động). */
function Conflict({ hub, switching }: { hub: CallHub; switching: boolean }) {
  return (
    <div className="fixed inset-0 z-[70] grid place-items-center bg-black/40 p-4" aria-modal="true">
      <div className="w-full max-w-sm rounded-2xl border border-border bg-surface p-4">
        <SwitchQuestion hub={hub} switching={switching} />
      </div>
    </div>
  );
}

function labelOf(s: SessionView): string {
  const st = s.st;
  if (st?.peer) return `Cuộc gọi với ${st.peer.name}`;
  return st?.title ?? "Cuộc gọi";
}

const ROUND_SM = "grid size-9 shrink-0 place-items-center rounded-full outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-surface disabled:opacity-60";
const NEUTRAL_SM = "bg-secondary text-secondary-foreground hover:bg-[color-mix(in_oklch,var(--secondary),var(--foreground)_8%)]";

/** Thanh nhỏ: trạng thái + đồng hồ, micro, rời — cùng một họ nút tròn. Máy tính: viên thuốc ở góc dưới phải; điện thoại: thanh trên cùng, bấm để mở toàn màn hình. */
function Bar({ hub, v, s, narrow }: { hub: CallHub; v: HubView; s: SessionView; narrow: boolean }) {
  const { snap, engine } = s;
  const ended = v.phase === "ended";
  const expand = () => (narrow ? hub.setFullscreen(true) : hub.setCollapsed(false));
  return (
    <div
      className={cn(
        "fixed z-[60] flex items-center gap-2 bg-surface",
        narrow ? "inset-x-0 top-0 border-b border-border px-3 pb-2 pt-[calc(0.5rem+env(safe-area-inset-top))]" : "bottom-4 right-4 max-w-[calc(100vw-2rem)] rounded-full border border-border py-1.5 pl-4 pr-1.5",
      )}
      role="region"
      aria-label="Cuộc gọi đang mở"
    >
      <button type="button" className="min-w-0 flex-1 rounded-lg text-left outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={expand} aria-label="Mở rộng cuộc gọi">
        <span className="t14m block truncate">{labelOf(s)}</span>
        <StatusLine snap={snap} ended={ended} peerName={s.st?.peer?.name} className="t12" />
      </button>
      {ended ? (
        <button type="button" className={cn(ROUND_SM, NEUTRAL_SM)} onClick={() => hub.dismiss()} aria-label="Đóng" title="Đóng"><X className="size-4" aria-hidden /></button>
      ) : engine && snap && s.inCall ? (
        <>
          <button type="button" className={cn(ROUND_SM, snap.muted || snap.listenOnly ? "bg-foreground text-background" : NEUTRAL_SM)} onClick={() => engine.toggleMute()} disabled={snap.listenOnly}
            aria-label={snap.muted ? "Bật micro" : "Tắt micro"} title={snap.muted ? "Bật micro" : "Tắt micro"}>
            {snap.muted || snap.listenOnly ? <MicOff className="size-4" aria-hidden /> : <Mic className="size-4" aria-hidden />}
          </button>
          <button type="button" className={cn(ROUND_SM, "bg-bad text-white hover:bg-bad/85")} disabled={s.busy} onClick={() => void hub.leave(false)} aria-label="Rời cuộc gọi" title="Rời cuộc gọi">
            <PhoneOff className="size-4" aria-hidden />
          </button>
        </>
      ) : (
        <>
          <button type="button" className={cn(ROUND_SM, "bg-ok text-white hover:bg-ok/85")} disabled={s.busy} onClick={() => hub.enter(false)} aria-label="Vào cuộc gọi" title="Vào cuộc gọi">
            <Phone className="size-4" aria-hidden />
          </button>
          <button type="button" className={cn(ROUND_SM, NEUTRAL_SM)} disabled={s.busy} onClick={() => hub.dismiss()} aria-label="Đóng" title="Đóng"><X className="size-4" aria-hidden /></button>
        </>
      )}
      <button type="button" className={cn(ROUND_SM, "hover:bg-accent")} onClick={expand} aria-label="Mở rộng cuộc gọi" title="Mở rộng">
        <Maximize2 className="size-4" aria-hidden />
      </button>
    </div>
  );
}

const ICON_BTN = "grid size-8 shrink-0 place-items-center rounded-full outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring";
const QUIET = "t14 rounded-lg px-2 py-1 text-primary outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60";

function Body({ hub, v, s, layout, blocked, onResume, onPip }: { hub: CallHub; v: HubView; s: SessionView; layout: "card" | "full" | "pip"; blocked: boolean; onResume: () => void; onPip?: () => void }) {
  const { st, snap, engine, busy, loadError } = s;
  const ended = v.phase === "ended";
  const peerName = st?.peer?.name ?? null;
  const startedHere = !!engine && s.inCall;
  const alert = pickAlert({
    snap, engine, inCall: startedHere, ended, loadError, hasState: !!st, busy, peerName,
    onRetry: () => hub.enter(false), onReload: () => hub.reload(), onClose: () => hub.dismiss(),
  });

  return (
    <div className={cn("flex min-h-0 flex-col gap-3", layout === "full" ? "flex-1 p-4" : layout === "pip" ? "h-screen overflow-auto p-3" : "p-3")}>
      <header className="flex items-start gap-1">
        <div className="min-w-0 flex-1">
          {st?.company ? <p className="t12m truncate text-muted-foreground">{st.company}</p> : null}
          <h2 className="t16 truncate">{labelOf(s)}</h2>
          <StatusLine snap={snap} ended={ended} peerName={peerName} className="t14" />
        </div>
        {onPip && !ended ? (
          <button type="button" className={ICON_BTN} onClick={onPip} aria-label="Tách ra cửa sổ nổi" title="Tách ra cửa sổ nổi">
            <PictureInPicture2 className="size-4" aria-hidden />
          </button>
        ) : null}
        {layout === "card" ? (
          <button type="button" className={ICON_BTN} onClick={() => hub.setCollapsed(true)} aria-label="Thu gọn" title="Thu gọn">
            <ChevronDown className="size-4" aria-hidden />
          </button>
        ) : null}
        {layout === "full" ? (
          <button type="button" className={ICON_BTN} onClick={() => hub.setFullscreen(false)} aria-label="Thu nhỏ" title="Thu nhỏ">
            <Minimize2 className="size-4" aria-hidden />
          </button>
        ) : null}
      </header>

      <Stage engine={engine} snap={snap} peerName={peerName} inCall={startedHere} ended={ended} silent size={layout === "full" ? "full" : "card"} />

      {blocked ? (
        <button type="button" onClick={onResume} className="t14m h-10 rounded-xl bg-primary text-primary-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2">Bấm để nghe tiếp</button>
      ) : null}

      {alert ? <CallAlert tone={alert.tone} action={alert.action}>{alert.text}</CallAlert> : null}
      {v.notice ? <CallAlert tone="warn">{v.notice}</CallAlert> : null}
      {snap?.relay && startedHere ? <p className="t12 text-muted-foreground">Đang đi qua máy chuyển tiếp (mạng chặn kết nối thẳng)</p> : null}

      {ended ? (
        <button type="button" className="t14m h-11 rounded-xl bg-secondary text-secondary-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => hub.dismiss()}>Đóng</button>
      ) : null}

      {!ended && !startedHere ? (
        <div className="flex flex-col gap-2">
          {snap?.mediaCode ? null : (
            <button type="button" disabled={busy || !st} onClick={() => hub.enter(false)}
              className="t14m h-11 rounded-xl bg-ok text-white outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:opacity-60">
              {snap?.phase === "closed" ? "Vào lại cuộc gọi" : "Vào cuộc gọi"}
            </button>
          )}
          <div className="flex flex-wrap items-center justify-center gap-1">
            {snap?.mediaCode ? <button type="button" disabled={busy} onClick={() => hub.enter(true)} className={QUIET}>Vào chỉ nghe</button> : null}
            {snap?.mediaCode ? (
              <button type="button" className={cn(QUIET, "flex items-center gap-1")} onClick={() => void hub.openInTab()}>
                <ExternalLink className="size-3.5" aria-hidden />Mở trong tab riêng
              </button>
            ) : null}
            {/* chưa vào thì không có gì để báo: đóng bảng; thẻ cuộc gọi vẫn còn nút Vào lại / Huỷ / Từ chối */}
            <button type="button" disabled={busy} onClick={() => hub.dismiss()} className={cn(QUIET, "text-muted-foreground")}>Đóng</button>
          </div>
        </div>
      ) : null}

      {startedHere && engine && snap && s.entry?.video && !snap.camera && !snap.listenOnly && !s.cameraOffered ? (
        <button type="button" className="t14m h-10 rounded-xl bg-primary text-primary-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2" onClick={() => hub.offerCamera()}>Bật camera</button>
      ) : null}

      {startedHere && engine && snap ? (
        <div className={cn("flex flex-col items-center gap-2", layout === "full" && "pb-1")}>
          <CallControls engine={engine} snap={snap} busy={busy} size={layout === "full" ? "large" : "card"} onLeave={() => void hub.leave(false)} />
          {s.canEnd && snap.peerHere ? (
            <button type="button" disabled={busy} onClick={() => void hub.leave(true)} className="t12 h-8 rounded-lg px-3 text-bad outline-none hover:bg-bad/10 focus-visible:ring-2 focus-visible:ring-ring">
              Kết thúc cuộc gọi cho cả hai
            </button>
          ) : null}
        </div>
      ) : null}

      {s.canEnd && st && !ended && layout !== "pip" ? <Invite member={memberOf(hub)} st={st} onChange={() => hub.reload()} /> : null}
    </div>
  );
}

function memberOf(hub: CallHub) {
  return hub.member()!;
}
