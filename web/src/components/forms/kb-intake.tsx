"use client";

import { AlertTriangle, CheckCircle2, FileText, FileUp, Sparkles } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { acceptAll, ACCEPT, longDate, shelfInfo, withoutSiblingReplace, nameClipboardFile, pendingProposals, pool, rejectReason, reviewOf, sizeText, updateInput, type IntakeOptions, type Review } from "@/lib/kb-intake";
import { newKey } from "@/lib/idempotency";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { actionClass, ErrorLine, Spinner } from "@/components/primitives";
import { cn } from "@/lib/utils";
import { DatePicker } from "./date-picker";

type Item = {
  id: number;
  file: File;
  phase: "queued" | "sending" | "reading" | "done" | "error";
  pct: number;
  error?: string;
  reviews: Review[];
  skipped: { filename: string; reason: string }[];
};

const PARALLEL = 3;
const chip = "t12m h-7 max-w-[11rem] rounded-full border border-border bg-surface px-2 outline-none focus-visible:ring-2 focus-visible:ring-ring";
const proposal = (on: boolean) =>
  cn("t12m inline-flex h-7 items-center gap-1 rounded-full border px-2.5 outline-none focus-visible:ring-2 focus-visible:ring-ring", on ? "border-primary/40 bg-primary/10 text-primary" : "border-dashed border-primary/50 text-primary hover:bg-primary/5");

/**
 * Thêm tài liệu = THẢ FILE (0225). Không có gì phải gõ trước: thả / chọn nhiều file cùng lúc (hoặc dán file từ clipboard), mỗi file một hàng có
 * tiến độ; core đọc + đoán tên, loại, phạm vi, kệ (kệ = kệ dùng gần nhất) rồi tài liệu đã vào kho. Danh sách xác nhận hiện giá trị máy đoán dưới dạng
 * chip sửa được; ngày hiệu lực và "thay bản cũ" là ĐỀ XUẤT — một nút "Chấp nhận tất cả". ZIP bung thành nhiều tài liệu. Trùng nội dung ⇒ bỏ qua, nói rõ.
 * `replaces` = tải bản mới thay một tài liệu đang dùng (kệ / loại / phạm vi / tên theo bản cũ).
 */
export function KbIntakeDialog({
  onOpenChange,
  onDone,
  replaces,
  onOpenSimilar,
  onPasteText,
  initialFiles,
}: {
  onOpenChange: (v: boolean) => void;
  onDone: (message: string) => void;
  replaces?: { id: string; title: string };
  onOpenSimilar?: (id: string, title: string) => void;
  /** Hiếm: không có file, chỉ có chữ ⇒ mở form dán chữ (lối phụ). */
  onPasteText?: () => void;
  initialFiles?: File[];
}) {
  const [opts, setOpts] = useState<IntakeOptions | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [over, setOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const ctl = useRef(new AbortController());
  const seq = useRef(0);
  const picked = useRef(""); // kệ người đã CHỌN trong hộp này — các file thả sau theo kệ đó (mặc định là kệ chung, không bao giờ ngầm là kệ hạn chế)

  useEffect(() => {
    let alive = true;
    api.queryRaw<IntakeOptions>("kb.intake_options").then((o) => alive && setOpts(o), (e) => alive && setError(e instanceof Error ? e.message : "Không tải được danh sách lựa chọn"));
    return () => {
      alive = false;
    };
  }, []);

  const patch = useCallback((id: number, fn: (it: Item) => Partial<Item>) => setItems((cur) => cur.map((it) => (it.id === id ? { ...it, ...fn(it) } : it))), []);

  const run = useCallback(
    async (it: Item) => {
      patch(it.id, () => ({ phase: "sending", pct: 0, error: undefined }));
      try {
        const out = await api.kbDrop(it.file, { replaces_document_id: replaces?.id, shelf: picked.current || undefined }, {
          signal: ctl.current.signal,
          onProgress: (sent, total) => patch(it.id, () => ({ pct: Math.round((sent / total) * 100) })),
          onSent: () => patch(it.id, () => ({ phase: "reading", pct: 100 })),
        });
        patch(it.id, () => ({ phase: "done", reviews: out.documents.map(reviewOf), skipped: out.skipped }));
      } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") return;
        patch(it.id, () => ({ phase: "error", error: e instanceof Error ? e.message : "Không tải được" }));
      }
    },
    [patch, replaces?.id],
  );

  const add = useCallback(
    (list: File[]) => {
      if (!list.length) return;
      const files = replaces ? list.slice(0, 1) : list; // thay bản cũ = đúng một file
      const made: Item[] = files.map((f) => {
        const why = rejectReason(f);
        return { id: ++seq.current, file: f, phase: why ? "error" : "queued", pct: 0, error: why ?? undefined, reviews: [], skipped: [] };
      });
      setItems((cur) => (replaces ? made : [...cur, ...made]));
      void pool(made.filter((x) => x.phase === "queued"), PARALLEL, run);
    },
    [replaces, run],
  );

  const started = useRef(false);
  useEffect(() => {
    if (!started.current && initialFiles?.length) {
      started.current = true;
      add(initialFiles);
    }
  }, [initialFiles, add]);

  // dán file từ clipboard (ảnh chụp màn hình, file copy từ Finder) khi hộp đang mở
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const fs = Array.from(e.clipboardData?.files ?? []);
      if (!fs.length) return; // dán chữ vào ô tên: để yên
      e.preventDefault();
      add(fs.map((f) => nameClipboardFile(f)));
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, [add]);

  const flat = items.flatMap((it) => it.reviews.map((r, i) => ({ itemId: it.id, i, r })));
  const shown = withoutSiblingReplace(flat.map((x) => x.r));
  const reviews = flat.map((x, k) => ({ ...x, r: shown[k] }));
  const busy = items.some((it) => it.phase === "queued" || it.phase === "sending" || it.phase === "reading");
  const pending = reviews.reduce((n, x) => n + pendingProposals(x.r), 0);
  const edits = reviews.filter((x) => !x.r.saved && updateInput(x.r)).length;
  const added = reviews.filter((x) => x.r.doc.status === "added").length;
  const dups = reviews.filter((x) => x.r.doc.status === "duplicate").length;

  function setReview(itemId: number, i: number, p: Partial<Review>) {
    if (p.shelf) picked.current = p.shelf;
    patch(itemId, (it) => ({ reviews: it.reviews.map((r, k) => (k === i ? { ...r, error: undefined, saved: false, ...p } : r)) }));
  }

  async function confirm() {
    setSaving(true);
    setError(null);
    let failed = 0;
    const all = pending ? acceptAll(reviews.map((x) => x.r)) : reviews.map((x) => x.r);
    for (let k = 0; k < reviews.length; k++) {
      const body = updateInput(all[k]);
      if (!body) continue;
      try {
        const r = await api.command("kb.update_document", body as Record<string, never>, newKey());
        if (r.status !== "ok") {
          failed++;
          setReview(reviews[k].itemId, reviews[k].i, { error: r.error || "Không lưu được" });
        } else setReview(reviews[k].itemId, reviews[k].i, { ...all[k], saved: true });
      } catch (e) {
        failed++;
        setError(e instanceof Error ? e.message : "Không lưu được");
      }
    }
    setSaving(false);
    if (!failed) finish();
  }

  function finish() {
    onDone(replaces ? `Đã thay “${replaces.title}” bằng bản mới` : added === 1 ? `Đã thêm tài liệu: ${reviews.find((x) => x.r.doc.status === "added")?.r.doc.title ?? ""}` : `Đã thêm ${added} tài liệu`);
    onOpenChange(false);
  }

  const empty = items.length === 0;
  return (
    <Dialog
      open
      onOpenChange={(o) => {
        if (!o) {
          ctl.current.abort(); // đóng giữa chừng ⇒ thôi chờ (core có thể vẫn ghi xong file đang đọc)
          if (added) onDone(`Đã thêm ${added} tài liệu`);
        }
        onOpenChange(o);
      }}
    >
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="t20">{replaces ? `Tải bản mới thay “${replaces.title}”` : "Thêm tài liệu"}</DialogTitle>
          <DialogDescription className="t12">
            {replaces ? "Thả file bản mới — bản cũ hết hiệu lực từ ngày bản mới bắt đầu (đổi ngày ở bước xác nhận)." : "Thả file vào đây, máy tự đọc và điền thông tin. Bạn chỉ cần xem lại rồi xác nhận."}
          </DialogDescription>
        </DialogHeader>
        <div className="flex max-h-[68vh] min-h-0 flex-col gap-3 overflow-y-auto pr-0.5">
          <label
            onDragOver={(e) => {
              e.preventDefault();
              setOver(true);
            }}
            onDragLeave={() => setOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setOver(false);
              add(Array.from(e.dataTransfer.files));
            }}
            className={cn(
              "flex cursor-pointer flex-col items-center justify-center gap-1.5 rounded-xl border-2 border-dashed text-center transition-colors",
              empty ? "px-6 py-10" : "px-4 py-3",
              over ? "border-primary bg-primary/5" : "border-border bg-surface2 hover:border-primary/50",
            )}
          >
            <FileUp className={cn("text-muted-foreground", empty ? "size-7" : "size-4")} aria-hidden />
            <span className="t14m">{empty ? (replaces ? "Thả file bản mới vào đây, hoặc bấm để chọn" : "Thả file vào đây, hoặc bấm để chọn nhiều file") : "Thả thêm file…"}</span>
            {empty ? (
              <span className="t12 max-w-md text-muted-foreground">
                PDF, Word, Excel / CSV, PowerPoint, ảnh, ZIP (bung thành nhiều tài liệu) — tối đa 30 MB mỗi file. Có thể dán file (Ctrl/⌘ V). PDF scan và ảnh do trợ lý đọc, mất 10–30 giây.
              </span>
            ) : null}
            <input
              ref={input}
              type="file"
              multiple={!replaces}
              accept={ACCEPT}
              className="sr-only"
              aria-label="Chọn file tài liệu"
              onChange={(e) => {
                add(Array.from(e.target.files ?? []));
                e.target.value = "";
              }}
            />
          </label>
          {items.map((it) => (
            <ItemView key={it.id} it={{ ...it, reviews: reviews.filter((x) => x.itemId === it.id).map((x) => x.r) }} opts={opts} replacing={!!replaces} onChange={(i, p) => setReview(it.id, i, p)} onRetry={() => void run(it)} onOpen={onOpenSimilar} />
          ))}
          {error ? <ErrorLine>{error}</ErrorLine> : null}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="t12 text-muted-foreground">
            {empty ? (
              onPasteText && !replaces ? (
                <button type="button" className="underline-offset-2 hover:underline" onClick={onPasteText}>
                  Không có file? Dán chữ
                </button>
              ) : null
            ) : busy ? (
              "Đang đọc file…"
            ) : (
              `${added} tài liệu đã thêm${dups ? ` · ${dups} trùng, bỏ qua` : ""}`
            )}
          </span>
          <div className="flex gap-2">
            {!empty && !busy && (pending || edits) ? (
              <button type="button" className={actionClass("default")} onClick={() => onOpenChange(false)} disabled={saving}>
                Để sau
              </button>
            ) : null}
            <button
              type="button"
              className={actionClass(empty ? "default" : "primary")}
              disabled={busy || saving}
              onClick={() => (empty ? onOpenChange(false) : pending || edits ? void confirm() : finish())}
            >
              {saving ? <Spinner /> : null}
              {empty ? "Huỷ" : pending ? `Chấp nhận tất cả (${pending})` : edits ? "Lưu thay đổi" : "Xong"}
            </button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function ItemView({
  it,
  opts,
  replacing,
  onChange,
  onRetry,
  onOpen,
}: {
  it: Item;
  opts: IntakeOptions | null;
  replacing: boolean;
  onChange: (i: number, p: Partial<Review>) => void;
  onRetry: () => void;
  onOpen?: (id: string, title: string) => void;
}) {
  const head = (
    <div className="flex items-center gap-2">
      <FileText className="size-4 shrink-0 text-muted-foreground" aria-hidden />
      <span className="t14 min-w-0 flex-1 truncate">{it.file.name}</span>
      <span className="t12 shrink-0 text-muted-foreground">{sizeText(it.file.size)}</span>
    </div>
  );
  if (it.phase === "queued" || it.phase === "sending" || it.phase === "reading")
    return (
      <div className="flex flex-col gap-1.5 rounded-xl border border-border bg-surface px-3 py-2.5" data-testid="kb-item" data-phase={it.phase}>
        {head}
        {it.phase === "reading" ? (
          <p className="t12 flex items-center gap-2 text-muted-foreground" role="status">
            <Spinner className="size-3" />
            Đang đọc và đoán thông tin…
          </p>
        ) : (
          <div className="flex items-center gap-2">
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-border" role="progressbar" aria-valuenow={it.pct} aria-valuemin={0} aria-valuemax={100}>
              <div className="h-full rounded-full bg-primary transition-[width]" style={{ width: `${it.phase === "queued" ? 0 : it.pct}%` }} />
            </div>
            <span className="t12 w-20 text-right text-muted-foreground">{it.phase === "queued" ? "Đang chờ" : `Đang gửi ${it.pct}%`}</span>
          </div>
        )}
      </div>
    );
  if (it.phase === "error")
    return (
      <div className="flex flex-col gap-1.5 rounded-xl border border-bad/30 bg-bad/5 px-3 py-2.5" data-testid="kb-item" data-phase="error">
        {head}
        <ErrorLine>
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <span>{it.error}</span>
        </ErrorLine>
        {rejectReason(it.file) ? null : (
          <button type="button" className={cn(actionClass("default"), "h-7 w-fit text-[13px]")} onClick={onRetry}>
            Thử lại
          </button>
        )}
      </div>
    );
  return (
    <div className="flex flex-col gap-2" data-testid="kb-item" data-phase="done">
      {it.reviews.length > 1 || it.skipped.length ? head : null}
      {it.reviews.map((r, i) => (
        <ReviewCard key={i} r={r} opts={opts} replacing={replacing} onChange={(p) => onChange(i, p)} onOpen={onOpen} single={it.reviews.length === 1 && !it.skipped.length} filename={it.file.name} size={it.file.size} />
      ))}
      {it.skipped.map((s) => (
        <p key={s.filename} className="t12 flex items-start gap-1.5 text-muted-foreground">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <span>
            Bỏ qua <b className="font-medium text-foreground">{s.filename}</b>: {s.reason}
          </span>
        </p>
      ))}
    </div>
  );
}

function ReviewCard({
  r,
  opts,
  replacing,
  onChange,
  onOpen,
  single,
  filename,
  size,
}: {
  r: Review;
  opts: IntakeOptions | null;
  replacing: boolean;
  onChange: (p: Partial<Review>) => void;
  onOpen?: (id: string, title: string) => void;
  single: boolean;
  filename: string;
  size: number;
}) {
  const d = r.doc;
  if (d.status === "duplicate")
    return (
      <div className="flex items-start gap-2 rounded-xl border border-border bg-surface2 px-3 py-2.5" data-testid="kb-review" data-status="duplicate">
        <AlertTriangle className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="t14 truncate">{d.filename}</p>
          <p className="t12 text-muted-foreground">{d.note}</p>
        </div>
        {d.duplicate_of && onOpen ? (
          <button type="button" className={cn(actionClass("default"), "h-7 text-[13px]")} onClick={() => onOpen(d.duplicate_of!.id, d.duplicate_of!.title)}>
            Mở bản đang có
          </button>
        ) : null}
      </div>
    );
  const sg = d.suggestion;
  const guessed = (k: string) => d.guessed?.includes(k);
  const shelf = shelfInfo(opts, r.shelf, d);
  return (
    <div className="flex flex-col gap-2 rounded-xl border border-border bg-surface px-3 py-2.5" data-testid="kb-review" data-status="added">
      <div className="flex items-center gap-2">
        <CheckCircle2 className={cn("size-4 shrink-0", r.saved ? "text-ok" : "text-muted-foreground")} aria-hidden />
        <input
          value={r.title}
          onChange={(e) => onChange({ title: e.target.value })}
          aria-label="Tên tài liệu"
          className="t14m min-w-0 flex-1 rounded-md bg-transparent px-1 py-0.5 outline-none hover:bg-accent focus-visible:bg-field focus-visible:ring-2 focus-visible:ring-ring"
          maxLength={200}
        />
        {guessed("title") ? (
          <span className="t12 inline-flex shrink-0 items-center gap-1 text-muted-foreground" title="Tên do trợ lý đoán từ nội dung — sửa nếu chưa đúng">
            <Sparkles className="size-3" aria-hidden />
            gợi ý
          </span>
        ) : null}
      </div>
      {single ? (
        <p className="t12 truncate pl-7 text-muted-foreground">
          {filename} · {sizeText(size)}
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-1.5 pl-7">
        <select aria-label="Loại" className={chip} value={r.kind} onChange={(e) => onChange({ kind: e.target.value })}>
          {(opts?.kinds ?? [{ value: r.kind, label: r.kind }]).map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        {opts && opts.contexts.length ? (
          <select aria-label="Phạm vi" className={chip} value={r.context_code} onChange={(e) => onChange({ context_code: e.target.value })}>
            <option value="">Áp dụng chung</option>
            {opts.contexts.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        ) : null}
        {opts && opts.shelves.length > 1 ? (
          <select
            aria-label="Kệ"
            title="Kệ quyết định ai đọc được tài liệu"
            className={cn(chip, "max-w-[16rem]", shelf.restricted && "border-warn bg-warn/10 text-foreground")}
            value={r.shelf}
            onChange={(e) => onChange({ shelf: e.target.value })}
            disabled={replacing || !!d.replaced}
          >
            {opts.shelves.map((o) => (
              <option key={o.value} value={o.value}>
                Kệ: {o.label}
                {o.everyone === false ? " (hạn chế)" : ""}
              </option>
            ))}
          </select>
        ) : (
          <span className={cn("t12", shelf.restricted ? "rounded-full border border-warn bg-warn/10 px-2 py-0.5 text-foreground" : "text-muted-foreground")}>Kệ: {shelf.label}</span>
        )}
        <label className="t12 inline-flex items-center gap-1 text-muted-foreground">
          Hiệu lực từ
          <DatePicker size="sm" label="Hiệu lực từ" placeholder="Chọn ngày" className="h-7 w-32 rounded-full"
                      value={r.from || (r.acceptFrom ? (sg?.effective_from ?? "") : "")} onChange={(v) => onChange({ from: v ?? "", acceptFrom: false })} />
          <span>{r.from || r.acceptFrom ? `= ${longDate(r.from || (sg?.effective_from ?? ""))}` : "(trống = dùng ngay)"}</span>
        </label>
      </div>
      {(sg?.effective_from && !r.from) || sg?.replaces_id ? (
        <div className="flex flex-wrap items-center gap-1.5 pl-7">
          {sg?.effective_from && !r.from ? (
            <button type="button" aria-pressed={r.acceptFrom} className={proposal(r.acceptFrom)} onClick={() => onChange({ acceptFrom: !r.acceptFrom })} title={sg.evidence ? `Trong văn bản: “${sg.evidence}”` : undefined}>
              <Sparkles className="size-3" aria-hidden />
              {r.acceptFrom ? "✓ " : ""}Hiệu lực từ {longDate(sg.effective_from)}
            </button>
          ) : null}
          {sg?.replaces_id ? (
            <button type="button" aria-pressed={r.acceptReplace} className={proposal(r.acceptReplace)} onClick={() => onChange({ acceptReplace: !r.acceptReplace })} title={sg.replaces_reason || undefined}>
              <Sparkles className="size-3" aria-hidden />
              {r.acceptReplace ? "✓ " : ""}Thay “{sg.replaces_title}”
            </button>
          ) : null}
        </div>
      ) : null}
      {shelf.restricted ? (
        <p className="t12 pl-7 text-foreground" data-testid="kb-restricted">
          <span className="mr-1 rounded-full border border-warn bg-warn/10 px-2 py-0.5 t12m">Hạn chế</span>
          Chỉ những người này đọc được: {shelf.audience || "một số người"}. Đổi kệ ở trên nếu cần cả công ty thấy.
        </p>
      ) : null}
      {d.replaced ? <p className="t12 pl-7 text-ok">Đã thay “{d.replaced}” — bản cũ hết hiệu lực từ ngày bản mới bắt đầu.</p> : null}
      {d.note ? <p className="t12 pl-7 text-muted-foreground">{d.note}</p> : null}
      {r.error ? <p className="t12 pl-7 text-bad" role="alert">{r.error}</p> : null}
    </div>
  );
}
