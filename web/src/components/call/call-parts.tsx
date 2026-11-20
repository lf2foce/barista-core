"use client";

import { useEffect, useRef, useState } from "react";
import { Popover as PopoverPrimitive } from "@base-ui/react/popover";
import { Camera, CameraOff, Check, LoaderCircle, Mic, MicOff, PhoneOff, Settings2, TriangleAlert, UserRound } from "lucide-react";
import { callStatus, clock, initialOf, shortName } from "@/lib/call";
import type { Snapshot } from "@/lib/call-engine";
import type { EngineLike } from "@/lib/call-session";
import { cn } from "@/lib/utils";

// Phần vẽ dùng chung của cuộc gọi 1–1 (core 0114): bảng nổi (call-dock.tsx) và trang đứng riêng (direct-call.tsx). Chỉ vẽ trạng thái phiên / bộ máy —
// không quyết gì thay server. Thang chữ t20/t16/t14/t12, màu qua biến CSS (sáng + tối), không in hoa, không nghiêng.

export type StageSize = "page" | "card" | "full";

/** Đồng hồ chạy mỗi giây kể từ `since` (ms). Không có mốc ⇒ không vẽ. */
export function Timer({ since, className }: { since: number | null | undefined; className?: string }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const i = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(i);
  }, []);
  if (!since) return null;
  // eslint-disable-next-line react-hooks/purity -- đồng hồ cuộc gọi: vẽ lại mỗi giây
  return <span className={cn("tabular-nums", className)}>{clock((Date.now() - since) / 1000)}</span>;
}

const TONE = { ok: "text-ok", warn: "text-warn", muted: "text-muted-foreground" } as const;
const DOT = { ok: "bg-ok", warn: "bg-warn", muted: "bg-muted-foreground/60" } as const;

/** MỘT dòng trạng thái gọn ("Đang chờ Tùng vào…", "Đã kết nối · 03:12", "Mất kết nối, đang nối lại…"). Mang `data-phase` (kịch bản trình duyệt đọc). */
export function StatusLine({ snap, ended, peerName, className, dot = true }: { snap: Snapshot | null; ended: boolean; peerName: string | null | undefined; className?: string; dot?: boolean }) {
  const phase = snap?.phase ?? "idle";
  const s = callStatus({ phase: snap ? phase : null, ended, peerName, peerLeft: snap?.peerLeft, noAnswer: snap?.noAnswer });
  return (
    <p className={cn("flex min-w-0 items-center gap-1.5", TONE[s.tone], className)} data-phase={phase} aria-live="polite">
      {dot ? <span className={cn("size-2 shrink-0 rounded-full", DOT[s.tone])} aria-hidden /> : null}
      <span className="truncate">
        {s.text}
        {s.clock === "connected" && snap?.connectedAt ? <> · <Timer since={snap.connectedAt} /></> : null}
      </span>
    </p>
  );
}

/** Ảnh đại diện người kia: chữ cái đầu tên riêng trên nền trong suốt của khung hình; chưa biết tên ⇒ biểu tượng người. `ring`: vòng nhấp nháy lúc đang gọi. */
function Avatar({ name, size, ring }: { name: string | null | undefined; size: "sm" | "md" | "lg"; ring?: boolean }) {
  const ch = initialOf(name);
  const box = size === "sm" ? "size-7" : size === "md" ? "size-16" : "size-20";
  return (
    <span className={cn("relative grid shrink-0 place-items-center rounded-full bg-white/20 text-stage-fg ring-1 ring-white/30", box)}>
      {ring ? <span className="absolute inset-0 animate-ping rounded-full bg-white/25 [animation-duration:1.6s] motion-reduce:hidden" aria-hidden /> : null}
      {ch ? <span className={size === "sm" ? "t14m" : "t20"}>{ch}</span> : <UserRound className={size === "sm" ? "size-4" : "size-8"} aria-hidden />}
    </span>
  );
}

/** Nhãn nhỏ trên khung hình: tên + dấu micro / camera tắt. */
function Chip({ label, micOff, camOff, className }: { label: string; micOff?: boolean; camOff?: boolean; className?: string }) {
  return (
    <span className={cn("t12m flex max-w-[70%] items-center gap-1 rounded-full bg-black/45 px-2 py-0.5 text-white", className)}>
      <span className="truncate">{label}</span>
      {micOff ? <MicOff className="size-3.5 shrink-0" aria-label="micro đang tắt" /> : null}
      {camOff ? <CameraOff className="size-3.5 shrink-0" aria-label="camera đang tắt" /> : null}
    </span>
  );
}

/** Khung hình cuộc gọi. Chờ người kia: camera mình bật ⇒ hình mình chiếm khung (lật như gương); camera tắt ⇒ ảnh đại diện người kia + "Đang gọi…" + đồng hồ.
 *  Người kia đã vào: hình họ chiếm khung (camera họ tắt ⇒ ảnh đại diện), hình mình thành ô nhỏ góc dưới phải. `silent`: bảng nổi giữ phần tử tiếng ở khung
 *  ngoài (không dựng lại khi chuyển cửa sổ nổi) nên ở đây chỉ vẽ hình. */
export function Stage({ engine, snap, peerName, inCall, ended, silent = false, size = "page" }: {
  engine: EngineLike | null;
  snap: Snapshot | null;
  peerName: string | null | undefined;
  inCall: boolean;
  ended: boolean;
  silent?: boolean;
  size?: StageSize;
}) {
  const audio = useRef<HTMLAudioElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const self = useRef<HTMLVideoElement>(null);
  const [blocked, setBlocked] = useState(false);
  useEffect(() => {
    if (!engine) return;
    if (audio.current && audio.current.srcObject !== engine.remote) audio.current.srcObject = engine.remote;
    if (video.current && video.current.srcObject !== engine.remote) video.current.srcObject = engine.remote;
    // Safari điện thoại có thể dừng tiếng sau khi khoá màn hình: phát lại; bị chặn ⇒ hiện nút cho người bấm
    audio.current?.play().then(() => setBlocked(false), () => setBlocked(engine.snap.phase === "connected"));
    const cam = engine.cameraTrack;
    const el = self.current;
    if (el) {
      const cur = el.srcObject instanceof MediaStream ? el.srcObject.getVideoTracks()[0] : null;
      if (cam && cur !== cam) el.srcObject = new MediaStream([cam]);
      if (!cam && el.srcObject) el.srcObject = null;
    }
  });

  const phase = snap?.phase ?? "idle";
  const live = inCall && !ended;
  const peerIn = live && !!snap && (snap.peerHere || phase === "connected" || phase === "reconnecting");
  const camera = live && !!snap?.camera;
  const remoteVideo = peerIn && !!snap?.remoteVideo;
  const selfMain = camera && !peerIn;
  const waiting = live && phase === "waiting";
  const name = peerName?.trim() || "Người kia";
  const box = size === "full" ? "min-h-0 flex-1" : size === "card" ? "aspect-[4/3]" : "aspect-[3/4] sm:aspect-video";
  const tile = size === "card" ? "w-24" : "w-28 sm:w-36";

  return (
    <div className={cn("relative isolate overflow-hidden rounded-2xl bg-[linear-gradient(160deg,var(--call-stage-from),var(--call-stage-to))] text-stage-fg", box)}>
      {silent ? null : <audio ref={audio} autoPlay playsInline />}
      <video ref={video} autoPlay playsInline muted className={cn("absolute inset-0 size-full object-cover", remoteVideo ? "" : "hidden")} />

      {!remoteVideo && !selfMain ? (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 px-4 text-center">
          <Avatar name={peerName} size={size === "card" ? "md" : "lg"} ring={waiting} />
          <div className="flex min-w-0 max-w-full flex-col items-center gap-0.5">
            <p className="t16 max-w-full truncate">{name}</p>
            {waiting ? (
              <p className="t14 text-stage-fg/85">{snap?.noAnswer ? "Chưa vào" : snap?.peerLeft ? "Đã rời" : "Đang gọi…"} · <Timer since={snap?.waitingAt} /></p>
            ) : phase === "connecting" && live ? (
              <p className="t14 text-stage-fg/85">Đang nối…</p>
            ) : phase === "media" && live ? (
              <p className="t14 text-stage-fg/85">Đang mở micro…</p>
            ) : null}
          </div>
        </div>
      ) : null}

      {/* hình mình: MỘT phần tử, đổi chỗ (khung lớn ⇄ ô nhỏ) để không gắn lại luồng hình */}
      <video
        ref={self}
        autoPlay
        playsInline
        muted
        aria-label="Hình của bạn"
        className={cn(
          "-scale-x-100 object-cover",
          !camera && "hidden",
          selfMain ? "absolute inset-0 size-full" : cn("absolute bottom-2.5 right-2.5 z-10 aspect-[4/3] rounded-xl ring-1 ring-white/40", tile),
        )}
      />

      {selfMain ? (
        <div className="absolute inset-x-0 top-0 flex justify-center p-2.5">
          <span className="flex max-w-full items-center gap-2 rounded-full bg-black/45 py-1 pl-1 pr-3 text-white">
            <Avatar name={peerName} size="sm" ring={waiting} />
            <span className="t14 truncate">{waiting ? `Đang gọi ${shortName(peerName)}…` : "Đang chờ…"}</span>
            {waiting ? <Timer since={snap?.waitingAt} className="t14m" /> : null}
          </span>
        </div>
      ) : null}

      {/* nhãn: người kia (đã vào) góc dưới trái; mình ở ô nhỏ — hoặc ở góc dưới trái khi hình mình chiếm khung */}
      {peerIn ? <Chip className="absolute bottom-2.5 left-2.5" label={shortName(peerName) === "người kia" ? name : shortName(peerName)} camOff={!remoteVideo} /> : null}
      {selfMain ? <Chip className="absolute bottom-2.5 left-2.5" label="Bạn" micOff={snap?.muted} /> : null}
      {live && !selfMain && camera ? <Chip className={cn("absolute bottom-3.5 right-3.5 z-20", size === "card" ? "max-w-20" : "max-w-24")} label="Bạn" micOff={snap?.muted} /> : null}
      {live && !camera ? (
        <span className="t12m absolute bottom-2.5 right-2.5 z-10 flex items-center gap-1 rounded-full bg-black/45 px-2 py-0.5 text-white">
          Bạn
          {snap?.muted || snap?.listenOnly ? <MicOff className="size-3.5" aria-label="micro đang tắt" /> : null}
          <CameraOff className="size-3.5" aria-label="camera đang tắt" />
        </span>
      ) : null}

      {live && phase === "reconnecting" ? (
        <div className="absolute inset-0 z-20 flex justify-center bg-black/40 pt-3" aria-hidden>
          <LoaderCircle className="size-6 animate-spin text-white motion-reduce:animate-none" />
        </div>
      ) : null}

      {blocked && !silent ? (
        <button type="button" onClick={() => void audio.current?.play().then(() => setBlocked(false))}
          className="t14m absolute inset-x-4 bottom-12 z-30 h-10 rounded-xl bg-primary text-primary-foreground outline-none focus-visible:ring-2 focus-visible:ring-white">
          Bấm để nghe tiếp
        </button>
      ) : null}
    </div>
  );
}

const ROUND = "grid shrink-0 place-items-center rounded-full outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-surface disabled:opacity-50";
/** Bật (bình thường) = nền trung tính; tắt = đảo màu + biểu tượng gạch chéo (không chỉ đổi màu). */
const ON = "bg-secondary text-secondary-foreground hover:bg-[color-mix(in_oklch,var(--secondary),var(--foreground)_8%)]";
const OFF = "bg-foreground text-background hover:bg-foreground/85";

function Control({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex w-[4.5rem] flex-col items-center gap-1">
      {children}
      <span className="t12 whitespace-nowrap text-muted-foreground" aria-hidden>{label}</span>
    </div>
  );
}

/** Hàng điều khiển: Micro · Camera · Thiết bị · Rời — cùng một họ nút tròn, cùng cỡ; nhãn chữ dưới mỗi nút, chú thích khi rê chuột. */
export function CallControls({ engine, snap, busy, size, onLeave }: { engine: EngineLike; snap: Snapshot; busy: boolean; size: "card" | "large"; onLeave: () => void }) {
  const dim = size === "card" ? "size-12" : "size-14";
  const icon = size === "card" ? "size-5" : "size-6";
  const micOff = snap.muted || snap.listenOnly;
  const micLabel = snap.listenOnly ? "Đang vào chỉ nghe (không dùng micro)" : snap.muted ? "Bật micro" : "Tắt micro";
  return (
    <div className="flex items-start justify-center gap-1" role="toolbar" aria-label="Điều khiển cuộc gọi">
      <Control label={snap.listenOnly ? "Chỉ nghe" : snap.muted ? "Micro tắt" : "Micro"}>
        <button type="button" className={cn(ROUND, dim, micOff ? OFF : ON)} onClick={() => engine.toggleMute()} disabled={snap.listenOnly}
          aria-label={micLabel} title={micLabel}>
          {micOff ? <MicOff className={icon} aria-hidden /> : <Mic className={icon} aria-hidden />}
        </button>
      </Control>
      <Control label={snap.camera ? "Camera" : "Camera tắt"}>
        <button type="button" className={cn(ROUND, dim, snap.camera ? ON : OFF)} onClick={() => void engine.toggleCamera()}
          aria-label={snap.camera ? "Tắt camera" : "Bật camera"} title={snap.camera ? "Tắt camera" : "Bật camera"}>
          {snap.camera ? <Camera className={icon} aria-hidden /> : <CameraOff className={icon} aria-hidden />}
        </button>
      </Control>
      <Control label="Thiết bị">
        <DeviceMenu engine={engine} snap={snap} className={cn(ROUND, dim, ON)} iconClass={icon} />
      </Control>
      <Control label="Rời">
        <button type="button" disabled={busy} className={cn(ROUND, dim, "bg-bad text-white hover:bg-bad/85")} onClick={onLeave} aria-label="Rời cuộc gọi" title="Rời cuộc gọi">
          <PhoneOff className={icon} aria-hidden />
        </button>
      </Control>
    </div>
  );
}

/** Chọn micro / camera trong một bảng nhỏ (thay ô chọn của trình duyệt dưới hàng nút). Bảng mở trong CÙNG tài liệu với nút (kể cả cửa sổ nổi). */
export function DeviceMenu({ engine, snap, className, iconClass }: { engine: EngineLike; snap: Snapshot; className: string; iconClass: string }) {
  const [host, setHost] = useState<HTMLElement | null>(null);
  return (
    <PopoverPrimitive.Root>
      <PopoverPrimitive.Trigger ref={(el: HTMLButtonElement | null) => setHost(el?.ownerDocument.body ?? null)} className={className}
        aria-label="Chọn micro và camera" title="Chọn micro và camera">
        <Settings2 className={iconClass} aria-hidden />
      </PopoverPrimitive.Trigger>
      <PopoverPrimitive.Portal container={host}>
        <PopoverPrimitive.Positioner side="top" align="center" sideOffset={8} collisionPadding={12} className="isolate z-[80]">
          <PopoverPrimitive.Popup className="flex w-72 max-w-[calc(100vw-24px)] flex-col gap-3 rounded-2xl border border-border bg-popover p-3 text-popover-foreground outline-none shadow-lg">
            <PopoverPrimitive.Title className="t14m">Thiết bị</PopoverPrimitive.Title>
            <DeviceGroup title="Micro" empty={snap.listenOnly ? "Đang vào chỉ nghe — không dùng micro." : "Không thấy micro nào."}
              items={snap.listenOnly ? [] : snap.mics} current={snap.micId} onPick={(id) => void engine.replaceMic(id)} />
            <DeviceGroup title="Camera" empty="Không thấy camera nào." items={snap.cams} current={snap.camId} onPick={(id) => void engine.replaceCamera(id)}
              note={snap.camera ? undefined : "Camera đang tắt — lựa chọn dùng khi bật."} />
          </PopoverPrimitive.Popup>
        </PopoverPrimitive.Positioner>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  );
}

function DeviceGroup({ title, items, current, onPick, empty, note }: { title: string; items: { id: string; label: string }[]; current: string; onPick: (id: string) => void; empty: string; note?: string }) {
  // chưa biết thiết bị đang dùng (trình duyệt không báo mã) ⇒ đánh dấu cái đầu tiên, là thiết bị mặc định
  const sel = items.some((d) => d.id === current) ? current : items[0]?.id;
  return (
    <div className="flex flex-col gap-1" role="group" aria-label={title}>
      <p className="t12m text-muted-foreground">{title}</p>
      {items.length ? (
        items.map((d) => (
          <button key={d.id} type="button" onClick={() => onPick(d.id)} aria-pressed={d.id === sel}
            className={cn("t14 flex min-h-9 items-center gap-2 rounded-lg px-2 text-left outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring", d.id === sel && "text-primary")}>
            <Check className={cn("size-4 shrink-0", d.id === sel ? "" : "invisible")} aria-hidden />
            <span className="min-w-0 flex-1 truncate">{d.label}</span>
          </button>
        ))
      ) : (
        <p className="t14 px-2 text-muted-foreground">{empty}</p>
      )}
      {note && items.length ? <p className="t12 px-2 text-muted-foreground">{note}</p> : null}
    </div>
  );
}

/** Một cảnh báo / lỗi với MỘT việc người dùng làm được để gỡ (nút chính). */
export function CallAlert({ tone, children, action }: { tone: "bad" | "warn" | "info"; children: React.ReactNode; action?: { label: string; onClick: () => void; disabled?: boolean } }) {
  return (
    <div role={tone === "info" ? "status" : "alert"}
      className={cn("flex flex-col gap-2 rounded-xl px-3 py-2.5", tone === "bad" ? "bg-bad/12 dark:bg-bad/20" : tone === "warn" ? "bg-warn/15 dark:bg-warn/20" : "bg-surface2")}>
      <p className="t14 flex items-start gap-2">
        {tone === "info" ? null : <TriangleAlert className={cn("mt-0.5 size-4 shrink-0", tone === "bad" ? "text-bad" : "text-warn")} aria-hidden />}
        <span className={tone === "info" ? "text-muted-foreground" : ""}>{children}</span>
      </p>
      {action ? (
        <button type="button" onClick={action.onClick} disabled={action.disabled}
          className="t14m h-9 self-start rounded-lg bg-primary px-3 text-primary-foreground outline-none hover:bg-primary/85 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-surface disabled:opacity-60">
          {action.label}
        </button>
      ) : null}
    </div>
  );
}

/** Cảnh báo nào hiện (một cái, theo mức quan trọng) và việc gỡ của nó. Chỉ đọc trạng thái phiên / bộ máy. */
export function pickAlert(o: {
  snap: Snapshot | null;
  engine: EngineLike | null;
  inCall: boolean;
  ended: boolean;
  loadError: string | null;
  hasState: boolean;
  busy: boolean;
  peerName: string | null | undefined;
  onRetry: () => void;
  onReload: () => void;
  onClose: () => void;
}): { tone: "bad" | "warn" | "info"; text: string; action?: { label: string; onClick: () => void; disabled?: boolean } } | null {
  const { snap, engine } = o;
  const n = shortName(o.peerName);
  if (snap?.mediaCode && snap.error && !o.inCall && !o.ended) return { tone: "bad", text: snap.error, action: { label: "Thử lại", onClick: o.onRetry, disabled: o.busy } };
  if (snap?.error && o.inCall && engine && snap.deviceError === "camera")
    return { tone: "bad", text: snap.error, action: { label: "Thử lại camera", onClick: () => void engine.toggleCamera() } };
  if (snap?.error && o.inCall && engine && snap.deviceError === "mic")
    return { tone: "bad", text: snap.error, action: { label: "Thử lại micro", onClick: () => void engine.replaceMic("") } };
  if (o.ended && snap?.error) return { tone: "bad", text: snap.error };   // nút "Đóng" của trạng thái kết thúc là việc gỡ
  if (o.loadError) return o.hasState ? { tone: "bad", text: o.loadError } : { tone: "bad", text: o.loadError, action: { label: "Tải lại", onClick: o.onReload } };
  if (o.inCall && snap?.phase === "reconnecting" && engine)
    return { tone: "warn", text: "Cuộc gọi đang tự nối lại. Chờ lâu thì bấm nối lại ngay.", action: { label: "Nối lại ngay", onClick: () => engine.reconnectNow() } };
  if (snap?.otherWindow) return { tone: "warn", text: "Cuộc gọi đang mở ở một cửa sổ khác của anh/chị — dùng cửa sổ đó.", action: { label: "Đóng ở đây", onClick: o.onClose } };
  if (snap?.noAnswer && snap.phase === "waiting") return { tone: "warn", text: `${n === "người kia" ? "Người kia" : n} chưa vào sau 45 giây. Nhắn họ bấm Tham gia, hoặc rời cuộc gọi.` };
  if (o.inCall && snap && !snap.signalUp) return { tone: "info", text: "Đang nối lại máy chủ…" };
  if (snap?.error && !o.ended) return { tone: "bad", text: snap.error };
  return null;
}
