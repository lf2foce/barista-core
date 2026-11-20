"use client";

import { useState } from "react";
import { Check, Sparkles, Undo2, X } from "lucide-react";
import { api } from "@/lib/api";
import { newKey } from "@/lib/idempotency";
import { decodeAssist, splitParagraphs, type AssistResult } from "@/lib/word";
import { actionClass } from "@/components/primitives";

// Trợ lý soạn thảo trong trình sửa Word (0227 §2.8). Model chỉ ĐỀ XUẤT chữ cho các đoạn gửi lên; người xem từng đề xuất, bấm áp dụng (đổi chữ trên trang
// giấy, chưa ghi file), rồi tự bấm Lưu như mọi lần sửa ⇒ thành một phiên bản mới, khôi phục được.

const QUICK = ["Sửa lỗi chính tả, ngữ pháp", "Viết trang trọng hơn", "Viết ngắn gọn hơn", "Viết rõ ràng, dễ hiểu hơn", "Viết tiếp đoạn sau"];
const MAX_WHOLE = 400;

type Para = { id: string; text: string };
type Undo = { edits: { el: HTMLElement; html: string }[]; added: HTMLElement[] };

/** Các đoạn sửa được (mã gốc, lần gặp đầu) — trong vùng chọn nếu có, không thì cả văn bản. */
function collect(sheet: HTMLElement, range: Range | null): { paras: Para[]; scope: "selection" | "document" } {
  const all = [...sheet.querySelectorAll<HTMLElement>("[data-seg] > p[data-id]")];
  const seen = new Set<string>();
  const uniq = all.filter((p) => !seen.has(p.dataset.id!) && seen.add(p.dataset.id!));
  const picked = range && !range.collapsed ? uniq.filter((p) => range.intersectsNode(p)) : [];
  const use = picked.length ? picked : uniq.slice(0, MAX_WHOLE);
  const paras = use.map((p) => ({ id: p.dataset.id!, text: p.innerText.replace(/\n$/, "") }));
  const filled = picked.length ? paras : paras.filter((p) => p.text.trim());
  // Trang trắng (văn bản mới): gửi đoạn trống đầu tiên để trợ lý có chỗ viết vào — viết thành chữ của đoạn đó + các đoạn sau
  return { paras: filled.length ? filled : paras.slice(0, 1), scope: picked.length ? "selection" : "document" };
}

/** Đặt chữ mới cho một đoạn trên trang giấy: giữ định dạng của lượt chữ đầu (mã nguồn + kiểu), bỏ phần còn lại. */
function setText(p: HTMLElement, text: string) {
  const first = p.querySelector<HTMLElement>("span[data-pid]");
  const span = first ? (first.cloneNode(false) as HTMLElement) : document.createElement("span");
  span.textContent = text;
  p.replaceChildren(span);
}

/** Đoạn MỚI ngay sau ``ref`` (chép định dạng đoạn + lượt chữ đầu của ``model``; bỏ mã ⇒ lúc lưu là đoạn mới). */
function newParaAfter(ref: HTMLElement, model: HTMLElement, text: string): HTMLElement {
  const p = model.cloneNode(false) as HTMLElement;
  delete p.dataset.id;
  const first = model.querySelector<HTMLElement>("span[data-pid]");
  const span = first ? (first.cloneNode(false) as HTMLElement) : document.createElement("span");
  span.textContent = text;
  p.appendChild(span);
  ref.after(p);
  return p;
}

function flash(el: HTMLElement) {
  el.animate([{ backgroundColor: "#fef08a" }, { backgroundColor: "transparent" }], { duration: 1600 });
}

export function AssistPanel({ op, documentId, sheet, range, onChanged, notify, onClose }: {
  op: string; documentId: string; sheet: React.RefObject<HTMLDivElement | null>; range: React.RefObject<Range | null>;
  onChanged: () => void; notify: (text: string, isError?: boolean) => void; onClose: () => void;
}) {
  const [ask, setAsk] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<AssistResult | null>(null);
  const [done, setDone] = useState<Set<string>>(new Set());
  const [undo, setUndo] = useState<Undo | null>(null);
  const [scope, setScope] = useState("");

  async function run(instruction: string) {
    if (!sheet.current || !instruction.trim() || busy) return;
    const { paras, scope: s } = collect(sheet.current, range.current);
    if (!paras.length) {
      notify("Không có đoạn nào sửa được", true);
      return;
    }
    setBusy(true);
    setResult(null);
    setDone(new Set());
    setUndo(null);
    setScope(s === "selection" ? `${paras.length} đoạn đang chọn` : "cả văn bản");
    try {
      const r = await api.command(op, { document_id: documentId, instruction, paragraphs_json: JSON.stringify(paras), scope: s }, newKey());
      if (r.status !== "ok") notify(r.error || "Trợ lý chưa trả lời được", true);
      else setResult(decodeAssist(r.output));
    } catch (e) {
      notify(e instanceof Error ? e.message : "Trợ lý chưa trả lời được", true);
    } finally {
      setBusy(false);
    }
  }

  /** Áp dụng các đề xuất có khoá trong `keys` (mã đoạn hoặc `+i` cho đoạn thêm thứ i). */
  function apply(keys: string[]) {
    const root = sheet.current;
    if (!root || !result) return;
    const next: Undo = { edits: [...(undo?.edits ?? [])], added: [...(undo?.added ?? [])] };
    const find = (id: string) => root.querySelector<HTMLElement>(`[data-seg] > p[data-id="${CSS.escape(id)}"]`);
    let n = 0;
    for (const e of result.edits) {
      const p = find(e.id);
      if (!keys.includes(e.id) || done.has(e.id) || !p) continue;
      next.edits.push({ el: p, html: p.innerHTML });
      const [head, ...rest] = splitParagraphs(e.text);
      setText(p, head);
      flash(p);
      let tail = p;
      for (const line of rest) {
        tail = newParaAfter(tail, p, line);
        next.added.push(tail);
        flash(tail);
      }
      n += 1;
    }
    const tail = new Map<string, HTMLElement>();   // nhiều đoạn thêm sau cùng một mốc giữ đúng thứ tự
    result.additions.forEach((a, i) => {
      const key = `+${i}`;
      const ref = tail.get(a.after) ?? find(a.after);
      const model = find(a.after) ?? ref;
      if (!keys.includes(key) || done.has(key) || !ref || !model) return;
      let last = ref;
      for (const line of splitParagraphs(a.text)) {   // chép định dạng đoạn của đoạn mốc; bỏ mã ⇒ lúc lưu là đoạn MỚI
        last = newParaAfter(last, model, line);
        next.added.push(last);
        flash(last);
      }
      tail.set(a.after, last);
      n += 1;
    });
    if (!n) return;
    setDone((d) => new Set([...d, ...keys]));
    setUndo(next);
    onChanged();
  }

  function revert() {
    if (!undo) return;
    for (const { el, html } of [...undo.edits].reverse()) el.innerHTML = html;
    for (const el of undo.added) el.remove();
    setUndo(null);
    setDone(new Set());
    onChanged();
  }

  const keys = result ? [...result.edits.map((e) => e.id), ...result.additions.map((_, i) => `+${i}`)] : [];
  const left = keys.filter((k) => !done.has(k));

  return (
    <aside className="flex min-h-0 flex-col gap-3 overflow-y-auto" aria-label="Trợ lý soạn thảo">
      <div className="flex items-center gap-2">
        <Sparkles className="size-4 text-primary" />
        <span className="t14m flex-1">Trợ lý soạn thảo</span>
        <button type="button" aria-label="Đóng trợ lý" className="grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-foreground/5" onClick={onClose}><X className="size-4" /></button>
      </div>
      <p className="t12 text-muted-foreground">Bôi đen đoạn cần sửa rồi chọn việc; không chọn gì thì trợ lý làm trên cả văn bản. Đề xuất chỉ đổi chữ trên trang — bấm Lưu mới thành phiên bản mới.</p>
      <div className="flex flex-wrap gap-1.5">
        {QUICK.map((qk) => (
          <button key={qk} type="button" disabled={busy} className="t12 rounded-full border border-border px-2.5 py-1 hover:bg-foreground/5 disabled:opacity-40"
            onMouseDown={(e) => e.preventDefault()} onClick={() => void run(qk)}>{qk}</button>
        ))}
      </div>
      <form className="flex flex-col gap-2" onSubmit={(e) => { e.preventDefault(); void run(ask); }}>
        <textarea value={ask} onChange={(e) => setAsk(e.target.value)} rows={3} maxLength={2000} placeholder="Hoặc nhập yêu cầu, vd: thêm điều khoản phạt chậm thanh toán 0,05%/ngày"
          className="t14 resize-none rounded-lg border border-border bg-surface px-2.5 py-2 outline-none focus:border-primary"
          onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void run(ask); } }} />
        <button type="submit" className={actionClass("primary")} disabled={busy || !ask.trim()}>
          <Sparkles className="size-4" />
          {busy ? "Trợ lý đang viết…" : "Gửi cho trợ lý"}
        </button>
      </form>
      {result && (
        <div className="flex flex-col gap-2">
          <p className="t12 text-muted-foreground">Phạm vi: {scope}</p>
          {result.reply && <p className="t14 rounded-lg bg-foreground/5 px-3 py-2">{result.reply}</p>}
          {keys.length > 0 && (
            <div className="flex items-center gap-2">
              <button type="button" className={actionClass("primary")} disabled={!left.length} onClick={() => apply(left)}><Check className="size-4" />Áp dụng tất cả ({left.length})</button>
              {undo && <button type="button" className={actionClass("default")} onClick={revert}><Undo2 className="size-4" />Hoàn tác</button>}
            </div>
          )}
          {result.edits.map((e) => (
            <div key={e.id} className="rounded-lg border border-border p-2.5">
              <p className="t12 text-muted-foreground line-through">{e.before}</p>
              <p className="t14 mt-1">{e.text}</p>
              <Choice applied={done.has(e.id)} onApply={() => apply([e.id])} />
            </div>
          ))}
          {result.additions.map((a, i) => (
            <div key={`+${i}`} className="rounded-lg border border-dashed border-border p-2.5">
              <p className="t12 text-muted-foreground">Đoạn mới</p>
              <p className="t14 mt-1">{a.text}</p>
              <Choice applied={done.has(`+${i}`)} onApply={() => apply([`+${i}`])} />
            </div>
          ))}
        </div>
      )}
    </aside>
  );
}

function Choice({ applied, onApply }: { applied: boolean; onApply: () => void }) {
  return applied ? <p className="t12m mt-2 text-ok">Đã áp dụng</p> : (
    <button type="button" className="t12m mt-2 text-primary" onClick={onApply}>Áp dụng</button>
  );
}
