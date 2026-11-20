"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { Copy, Link2, Share2 } from "lucide-react";
import type { DirectState } from "@/lib/call";
import { linkSession, memberSession, type CallSession, type Entry, type Member } from "@/lib/call-session";
import { CallAlert, CallControls, pickAlert, Stage, StatusLine } from "./call-parts";

// Trang gọi trực tiếp 1–1 đứng riêng (core 0114 bổ sung 07/11) — cho người ngoài (/c/goi/<mã link>), cho link gửi tay và làm đường lùi của bảng nổi
// (/goi/<mã>). Trong web, thành viên gọi / tham gia thì cuộc gọi chạy trong bảng nổi (call-dock.tsx), không mở thẻ mới. Cả hai dùng CHUNG một phiên
// (lib/call-session.ts); trang chỉ vẽ trạng thái phiên và nút. Bề ngang điện thoại trước.

/** ``entry``: địa chỉ do server gửi sau khi người bấm Gọi / Tham gia một chạm — vào cuộc gọi ngay (``auto``, chỉ micro), gợi ý bật camera (``video``: một nút "Bật camera", không tự bật). */
export function MemberCall({ callId, entry }: { callId: string; entry?: Entry }) {
  const [s] = useState(() => memberSession(callId, entry));
  return <DirectCall session={s} />;
}

export function GuestCall({ token }: { token: string }) {
  const [s] = useState(() => linkSession(token));
  return <DirectCall session={s} />;
}

/** Đọc một phiên gọi trong React (dùng cho trang đứng riêng và bảng nổi). */
export function useSession(session: CallSession) {
  return useSyncExternalStore(session.subscribe, session.getSnapshot, session.getSnapshot);
}

function DirectCall({ session }: { session: CallSession }) {
  const v = useSession(session);
  const { st, snap, engine, busy, loadError } = v;
  const member = session.member;

  // trang đứng riêng sở hữu phiên: mở khi vào trang, nhả micro khi rời trang
  useEffect(() => {
    session.init();
    return () => session.dispose();
  }, [session]);

  if (loadError && !st)
    return (
      <Shell>
        <CallAlert tone="bad" action={{ label: "Tải lại", onClick: () => void session.reload() }}>{loadError}</CallAlert>
      </Shell>
    );
  if (!st) return <Shell><p className="t14 text-muted-foreground">Đang tải…</p></Shell>;

  const phase = snap?.phase ?? "idle";
  const inCall = v.inCall;
  const ended = !st.active || phase === "ended";
  const peerName = st.peer?.name ?? null;
  const entry = v.entry;
  const alert = pickAlert({
    snap, engine, inCall, ended, loadError, hasState: true, busy, peerName,
    onRetry: () => void session.enter(false), onReload: () => void session.reload(), onClose: () => session.dispose(),
  });

  return (
    <Shell>
      <header className="flex flex-col gap-1">
        {st.company ? <p className="t12m text-muted-foreground">{st.company}</p> : null}
        <h1 className="t20">{st.peer ? `Cuộc gọi với ${st.peer.name}${st.peer.outsider ? " (người ngoài)" : ""}` : st.title}</h1>
        <StatusLine snap={snap} ended={ended} peerName={peerName} className="t14" />
      </header>

      <Stage engine={engine} snap={snap} peerName={peerName} inCall={inCall} ended={ended} />

      {alert ? <CallAlert tone={alert.tone} action={alert.action}>{alert.text}</CallAlert> : null}
      {snap?.relay && inCall ? <p className="t12 text-muted-foreground">Đang đi qua máy chuyển tiếp (mạng chặn kết nối thẳng)</p> : null}
      {snap?.listenOnly && inCall ? <p className="t12 text-muted-foreground">Chỉ nghe — người kia không nghe được anh/chị</p> : null}

      {!inCall && !ended ? (
        <div className="flex flex-col gap-2">
          {snap?.mediaCode ? null : (
            <button type="button" disabled={busy} onClick={() => void session.enter(false)}
              className="t16 h-12 w-full rounded-xl bg-ok text-white outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:opacity-60">
              {phase === "closed" ? "Vào lại cuộc gọi" : "Vào cuộc gọi"}
            </button>
          )}
          {snap?.mediaCode ? (
            <button type="button" disabled={busy} onClick={() => void session.enter(true)}
              className="t14 h-10 w-full rounded-xl text-primary outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring">
              Vào chỉ nghe (không dùng micro)
            </button>
          ) : (
            <p className="t12 text-center text-muted-foreground">Trình duyệt sẽ hỏi quyền dùng micro. Không cần cài gì thêm.</p>
          )}
        </div>
      ) : null}

      {inCall && engine && snap && entry?.video && !snap.camera && !snap.listenOnly && !v.cameraOffered ? (
        <button type="button" className="t16 h-12 w-full rounded-xl bg-primary text-primary-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2" onClick={() => session.offerCamera()}>
          Bật camera
        </button>
      ) : null}

      {inCall && engine && snap ? (
        <div className="flex flex-col items-center gap-3">
          <CallControls engine={engine} snap={snap} busy={busy} size="large" onLeave={() => void session.hangup(false)} />
          {member ? (
            <button type="button" disabled={busy} onClick={() => void session.hangup(true)} className="t14 h-9 rounded-lg px-3 text-bad outline-none hover:bg-bad/10 focus-visible:ring-2 focus-visible:ring-ring">
              Kết thúc cuộc gọi cho cả hai
            </button>
          ) : null}
        </div>
      ) : null}

      {member && !ended ? <Invite member={member} st={st} onChange={() => void session.reload()} /> : null}
      {ended ? <p className="t14 text-center text-muted-foreground">Có thể đóng trang này.</p> : null}
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <main className="min-h-full bg-canvas">
      <div className="mx-auto flex max-w-[560px] flex-col gap-5 px-4 py-6">{children}</div>
    </main>
  );
}

/** Mời người ngoài (phụ huynh, học viên): link hẹp có hạn, chỉ vào đúng cuộc gọi này. Link hiện một lần — copy / chia sẻ ngay. */
export function Invite({ member, st, onChange }: { member: Member; st: DirectState; onChange: () => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [hours, setHours] = useState(2);
  const [adult, setAdult] = useState(false);
  const [made, setMade] = useState<{ url: string; summary: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  if (st.invite && !made)
    return (
      <div className="flex items-center justify-between gap-2 rounded-xl border border-border bg-surface px-3 py-2">
        <p className="t14">Đã mời {st.invite.name}</p>
        <button type="button" className="t14 text-bad" onClick={() => void member.revokeInvite().then(onChange, (e: Error) => setErr(e.message))}>Thu hồi</button>
      </div>
    );
  if (st.peer && !st.peer.outsider) return null;   // ghế còn lại đã có thành viên
  if (made)
    return (
      <div className="flex flex-col gap-2 rounded-xl border border-border bg-surface p-3">
        <p className="t14m">{made.summary}</p>
        <p className="t12 break-all text-muted-foreground">{made.url}</p>
        <div className="flex gap-2">
          <button type="button" className="t14 flex h-9 flex-1 items-center justify-center gap-2 rounded-lg border border-border"
            onClick={() => void navigator.clipboard.writeText(made.url).then(() => setCopied(true))}>
            <Copy className="size-4" aria-hidden />{copied ? "Đã copy" : "Copy link"}
          </button>
          {typeof navigator !== "undefined" && "share" in navigator ? (
            <button type="button" className="t14 flex h-9 flex-1 items-center justify-center gap-2 rounded-lg border border-border"
              onClick={() => void navigator.share({ title: st.title, url: made.url }).catch(() => undefined)}>
              <Share2 className="size-4" aria-hidden />Gửi
            </button>
          ) : null}
        </div>
        <p className="t12 text-muted-foreground">Link chỉ hiện lần này. Mất thì thu hồi rồi mời lại.</p>
        <button type="button" className="t12 self-start text-muted-foreground underline" onClick={() => { setMade(null); onChange(); }}>Xong</button>
      </div>
    );
  if (!open)
    return (
      <button type="button" className="t14 flex h-10 items-center justify-center gap-2 rounded-xl border border-border bg-surface" onClick={() => setOpen(true)}>
        <Link2 className="size-4" aria-hidden />Mời người ngoài bằng link
      </button>
    );
  return (
    <form className="flex flex-col gap-2 rounded-xl border border-border bg-surface p-3"
      onSubmit={(e) => {
        e.preventDefault();
        setErr(null);
        void member.invite(name, hours, adult).then(setMade, (x: Error) => setErr(x.message));
      }}>
      <label className="t12m flex flex-col gap-1">Tên người được mời
        <input required maxLength={120} value={name} onChange={(e) => setName(e.target.value)} className="t14 h-9 rounded-lg border border-border bg-field px-2" placeholder="Chị Lan (khách)" />
      </label>
      <label className="t12m flex flex-col gap-1">Link dùng được trong
        <select value={hours} onChange={(e) => setHours(Number(e.target.value))} className="t14 h-9 rounded-lg border border-border bg-field px-2">
          {[1, 2, 4, 8, 24].map((h) => <option key={h} value={h}>{h} giờ</option>)}
        </select>
      </label>
      <label className="t14 flex items-start gap-2">
        <input type="checkbox" required checked={adult} onChange={(e) => setAdult(e.target.checked)} className="mt-1" />
        Tôi xác nhận người được mời đã đủ tuổi thành niên
      </label>
      <p className="t12 text-muted-foreground">Người được mời là trẻ em: mời từ thẻ cuộc gọi và chọn định danh, để kiểm sự đồng ý của người giám hộ.</p>
      {err ? <p className="t12 text-bad" role="alert">{err}</p> : null}
      <div className="flex justify-end gap-2">
        <button type="button" className="t14 h-9 rounded-lg px-3" onClick={() => setOpen(false)}>Huỷ</button>
        <button type="submit" className="t14m h-9 rounded-lg bg-primary px-3 text-primary-foreground">Tạo link</button>
      </div>
    </form>
  );
}
