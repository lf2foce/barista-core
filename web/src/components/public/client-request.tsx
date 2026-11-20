"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { CircleCheck, TriangleAlert, Upload } from "lucide-react";

// Trang người ngoài mở từ link (core 0064 / 0075): không đăng nhập, chỉ thấy ĐÚNG một yêu cầu. Câu trả lời / file gửi bên gửi yêu cầu — bên đó nhận
// mới thành căn cứ. recipient / due_on trống ⇒ không hiện (mọi yêu cầu đi qua năng lực chung asks — 0075 / 0077).

type Item = { id: string; kind: "file" | "choice" | "text"; prompt: string; options: string[]; answer: string; files: number; done: boolean };
type View = { company: string; title: string; note: string; open: boolean; items: Item[]; recipient?: string; due_on?: string | null };

function dmy(iso: string): string {
  const [y, m, d] = iso.split("-");
  return `${d}/${m}/${y}`;
}

async function readError(r: Response): Promise<string> {
  const j = (await r.json().catch(() => ({}))) as { error?: string; detail?: string };
  return j.error || j.detail || `Lỗi ${r.status}`;
}

export function ClientRequest({ token }: { token: string }) {
  const [view, setView] = useState<View | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);   // tăng ⇒ tải lại sau mỗi lần gửi
  const reload = useCallback(() => setVersion((v) => v + 1), []);

  useEffect(() => {
    let alive = true;
    (async () => {
      const r = await fetch(`/api/public/${token}`, { cache: "no-store" });
      if (!alive) return;
      if (!r.ok) setError(await readError(r));
      else setView(((await r.json()) as { output: View }).output);
    })();
    return () => {
      alive = false;
    };
  }, [token, version]);

  if (error && !view)
    return (
      <Shell>
        <p className="t16 flex items-start gap-2 text-bad" role="alert"><TriangleAlert className="mt-1 size-4 shrink-0" aria-hidden />{error}</p>
      </Shell>
    );
  if (!view) return <Shell><p className="t14 text-muted-foreground">Đang tải…</p></Shell>;
  const left = view.items.filter((i) => !i.done).length;
  return (
    <Shell>
      <header className="flex flex-col gap-1">
        <p className="t12m text-muted-foreground">{view.company}</p>
        <h1 className="t20">{view.title}</h1>
        {view.recipient || view.due_on ? (
          <p className="t14 text-muted-foreground">
            {[view.recipient ? `Gửi ${view.recipient}` : "", view.due_on ? `Hạn nộp ${dmy(view.due_on)}` : ""].filter(Boolean).join(" · ")}
          </p>
        ) : null}
        {view.note ? <p className="t14 whitespace-pre-line text-muted-foreground">{view.note}</p> : null}
      </header>
      {!view.open ? <p className="t14 text-muted-foreground">Yêu cầu đã đóng — cảm ơn anh/chị.</p> : null}
      {error ? <p className="t14 flex items-start gap-2 text-bad" role="alert"><TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />{error}</p> : null}
      <ol className="flex flex-col gap-3">
        {view.items.map((it, i) => (
          <ItemCard key={it.id} n={i + 1} item={it} token={token} open={view.open} onDone={reload} onError={setError} />
        ))}
      </ol>
      <p className="t12 text-muted-foreground">
        {left ? `Còn ${left} mục.` : "Đã gửi đủ — bên gửi yêu cầu sẽ xem và liên hệ nếu cần."} Anh/chị không cần đăng nhập; link chỉ dùng cho yêu cầu này.
      </p>
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <main className="min-h-full bg-canvas">
      <div className="mx-auto flex max-w-[560px] flex-col gap-4 px-4 py-6">{children}</div>
    </main>
  );
}

function ItemCard({ n, item, token, open, onDone, onError }: {
  n: number; item: Item; token: string; open: boolean; onDone: () => void; onError: (e: string | null) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [text, setText] = useState(item.kind === "text" ? item.answer : "");
  const fileRef = useRef<HTMLInputElement>(null);

  async function answer(value: string) {
    setBusy(true);
    onError(null);
    const r = await fetch(`/api/public/${token}/answer`, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ item_id: item.id, answer: value }),
    });
    if (!r.ok) onError(await readError(r));
    onDone();
    setBusy(false);
  }

  async function upload(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true);
    onError(null);
    for (const f of Array.from(files)) {
      const r = await fetch(`/api/public/${token}/upload?item_id=${encodeURIComponent(item.id)}`, {
        method: "POST", headers: { "content-type": "application/octet-stream", "x-filename": encodeURIComponent(f.name) }, body: f,
      });
      if (!r.ok) {
        onError(`${f.name}: ${await readError(r)}`);
        break;
      }
    }
    if (fileRef.current) fileRef.current.value = "";
    onDone();
    setBusy(false);
  }

  return (
    <li className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4">
      <div className="flex items-start gap-2">
        {item.done ? <CircleCheck className="mt-0.5 size-4 shrink-0 text-ok" aria-label="Đã gửi" /> : <span className="t14m w-4 shrink-0 text-muted-foreground">{n}.</span>}
        <p className="t16">{item.prompt}</p>
      </div>
      {item.kind === "file" ? (
        <div className="flex flex-col gap-2">
          {item.files ? <p className="t12 text-muted-foreground">Đã nộp {item.files} file{open ? " — nộp thêm nếu còn" : ""}.</p> : null}
          {open ? (
            <>
              <input ref={fileRef} type="file" multiple className="hidden" disabled={busy}
                     accept=".pdf,.jpg,.jpeg,.png,.heic,.webp,.xml,.xlsx,.xls,.csv,.docx,.zip,.txt,image/*"
                     onChange={(e) => void upload(e.target.files)} />
              <button type="button" disabled={busy} onClick={() => fileRef.current?.click()}
                      className="t14m flex h-11 items-center justify-center gap-2 rounded-lg bg-primary px-4 text-primary-foreground disabled:opacity-60">
                <Upload className="size-4" aria-hidden />{busy ? "Đang gửi…" : "Chụp ảnh / chọn file"}
              </button>
            </>
          ) : null}
        </div>
      ) : item.kind === "choice" ? (
        <div className="flex flex-wrap gap-2">
          {item.options.map((o) => (
            <button key={o} type="button" disabled={busy || !open} onClick={() => void answer(o)} aria-pressed={item.answer === o}
                    className={`t14m h-10 rounded-lg border px-3 disabled:opacity-60 ${item.answer === o ? "border-primary bg-primary text-primary-foreground" : "border-border bg-canvas"}`}>
              {o}
            </button>
          ))}
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <textarea value={text} onChange={(e) => setText(e.target.value)} disabled={busy || !open} rows={3} maxLength={2000}
                    className="t14 rounded-lg border border-border bg-canvas p-3 outline-none focus-visible:ring-2 focus-visible:ring-ring" placeholder="Anh/chị trả lời ở đây" />
          {open ? (
            <button type="button" disabled={busy || !text.trim() || text.trim() === item.answer} onClick={() => void answer(text.trim())}
                    className="t14m h-10 self-end rounded-lg bg-primary px-4 text-primary-foreground disabled:opacity-60">
              {busy ? "Đang gửi…" : "Gửi"}
            </button>
          ) : null}
        </div>
      )}
    </li>
  );
}
