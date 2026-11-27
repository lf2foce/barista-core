"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BookOpen, Check, CircleAlert, Undo2, X } from "lucide-react";
import { applyAll } from "@/components/blocks/word-apply";
import { actionClass } from "@/components/primitives";
import { changedSpans, clipSame, diffWords, type AssistCheck, type AssistEdit } from "@/lib/word";
import { cn } from "@/lib/utils";

// RÀ SOÁT trong trình soạn (0227 §2.8, `word.assist` cách làm `review`). Hai loại thẻ:
// * NÊN SỬA — lý do · chữ bỏ gạch đỏ · chữ thêm tô xanh · Nhận / Bỏ. Nhận = đặt chữ mới lên trang (như "Áp dụng" của trợ lý soạn), người bấm Lưu như mọi lần
//   sửa ⇒ một phiên bản mới, khôi phục được.
// * CẦN KIỂM — chỗ nghi sai mà trợ lý không biết giá trị đúng (số tiền bằng số ≠ bằng chữ, số hợp đồng ghi hai kiểu…): chỉ chỉ chỗ + lý do, người tự sửa.
// Chỗ sai gạch dưới NGAY TRÊN TRANG GIẤY bằng CSS Custom Highlight (đỏ: nên sửa, cam: cần kiểm tra) — không chèn thẻ nào vào trang ⇒ lúc lưu không lẫn gì.

const FIX = "word-review";
const CHECK = "word-review-check";
const ACTIVE = "word-review-active";

type State = "ok" | "skip" | "stale";
type Snap = { el: HTMLElement; html: string; added: HTMLElement[]; key: string };
type Item = { key: string; id: string; why: string } & ({ kind: "fix"; edit: AssistEdit } | { kind: "check"; quote: string; source: string });

const NO_CHECKS: AssistCheck[] = [];
const paraOf = (root: HTMLElement, id: string) => root.querySelector<HTMLElement>(`[data-seg] > p[data-id="${CSS.escape(id)}"]`);
const textOf = (p: HTMLElement) => p.innerText.replace(/\n$/, "");
const registry = () => (typeof CSS !== "undefined" && "highlights" in CSS ? CSS.highlights : null);

/** Range của từng khoảng [đầu, cuối) mà ``spans`` tìm trong chữ hiện có của đoạn (đi theo nút chữ). Không tìm được khoảng nào (chữ đã đổi sau khi rà, cụm
 *  trích không còn) ⇒ gạch cả đoạn. */
function ranges(p: HTMLElement, spans: (text: string) => [number, number][]): Range[] {
  const nodes: Text[] = [];
  const walk = document.createTreeWalker(p, NodeFilter.SHOW_TEXT);
  for (let n = walk.nextNode(); n; n = walk.nextNode()) nodes.push(n as Text);
  if (!nodes.length) return [];
  const found = spans(nodes.map((n) => n.data).join(""));
  if (!found.length) {
    const r = document.createRange();
    r.selectNodeContents(p);
    return [r];
  }
  const at = (off: number): [Text, number] => {
    let acc = 0;
    for (const n of nodes) {
      if (off <= acc + n.data.length) return [n, off - acc];
      acc += n.data.length;
    }
    const last = nodes[nodes.length - 1];
    return [last, last.data.length];
  };
  return found.map(([s, e]) => {
    const r = document.createRange();
    r.setStart(...at(s));
    r.setEnd(...at(e));
    return r;
  });
}

/** Khoảng cần gạch của một thẻ trong chữ ``text`` đang có trên trang. */
function spansOf(item: Item, text: string): [number, number][] {
  if (item.kind === "fix") return text === item.edit.before ? changedSpans(diffWords(item.edit.before, item.edit.text)) : [];
  const at = item.quote ? text.indexOf(item.quote) : -1;
  return at >= 0 ? [[at, at + item.quote.length]] : [];
}

/** Danh sách rà soát gắn với trang giấy ``sheet``. ``onChanged``: trang vừa đổi chữ (đánh dấu chưa lưu). */
export function ReviewList({ edits, checks = NO_CHECKS, sheet, onChanged }: {
  edits: AssistEdit[]; checks?: AssistCheck[]; sheet: () => HTMLElement | null; onChanged: () => void;
}) {
  const [state, setState] = useState<Record<string, State>>({});
  const [active, setActive] = useState("");
  const [undo, setUndo] = useState<Snap[]>([]);
  const [typed, setTyped] = useState(0);   // người gõ trên trang ⇒ vẽ lại vệt gạch (chữ dời chỗ)
  const box = useRef<HTMLDivElement>(null);
  const items = useMemo<Item[]>(() => [
    ...edits.map((e): Item => ({ key: e.id, id: e.id, why: e.why, kind: "fix", edit: e })),
    ...checks.map((c, i): Item => ({ key: `?${i}`, id: c.id, why: c.why, kind: "check", quote: c.quote, source: c.source })),
  ], [edits, checks]);
  const fixes = items.filter((x) => x.kind === "fix" && !state[x.key]).map((x) => x.key);
  const open = items.filter((x) => !state[x.key]);

  // vệt gạch trên trang giấy cho mọi thẻ còn chờ; thẻ đang xem tô nền
  useEffect(() => {
    const root = sheet();
    const reg = registry();
    if (!root || !reg) return;
    const marks = { [FIX]: new Highlight(), [CHECK]: new Highlight(), [ACTIVE]: new Highlight() };
    for (const it of items) {
      if (state[it.key]) continue;
      const p = paraOf(root, it.id);
      if (!p) continue;
      for (const r of ranges(p, (text) => spansOf(it, text))) {
        marks[it.kind === "fix" ? FIX : CHECK].add(r);
        if (it.key === active) marks[ACTIVE].add(r);
      }
    }
    for (const [name, h] of Object.entries(marks)) reg.set(name, h);
    return () => {
      for (const name of Object.keys(marks)) reg.delete(name);
    };
  }, [items, state, active, sheet, typed]);

  // bấm vào một đoạn đang có thẻ trên trang ⇒ mở đúng thẻ; gõ chữ ⇒ vẽ lại vệt gạch
  useEffect(() => {
    const root = sheet();
    if (!root) return;
    const click = (ev: MouseEvent) => {
      const id = (ev.target as Element | null)?.closest<HTMLElement>("p[data-id]")?.dataset.id;
      const it = id ? items.find((x) => x.id === id && !state[x.key]) : undefined;
      if (!it) return;
      setActive(it.key);
      box.current?.querySelector(`[data-review="${CSS.escape(it.key)}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    };
    const input = () => setTyped((n) => n + 1);
    root.addEventListener("click", click);
    root.addEventListener("input", input);
    return () => {
      root.removeEventListener("click", click);
      root.removeEventListener("input", input);
    };
  }, [items, state, sheet]);

  const show = useCallback((it: Item) => {
    setActive(it.key);
    const root = sheet();
    if (root) paraOf(root, it.id)?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [sheet]);

  /** Nhận các thẻ NÊN SỬA ``keys``: chỉ khi chữ trên trang còn đúng chữ lúc rà — đã sửa tay sau đó thì không ghi đè (thẻ báo "đoạn đã đổi"). */
  function accept(keys: string[]) {
    const root = sheet();
    if (!root) return;
    const next = { ...state };
    const snaps = [...undo];
    let n = 0;
    for (const it of items) {
      if (it.kind !== "fix" || !keys.includes(it.key) || next[it.key]) continue;
      const p = paraOf(root, it.id);
      if (!p || textOf(p) !== it.edit.before) {
        next[it.key] = "stale";
        continue;
      }
      const html = p.innerHTML;
      const after = p.nextElementSibling;
      applyAll(root, { edits: [it.edit], additions: [] });
      const added: HTMLElement[] = [];
      for (let x = p.nextElementSibling; x && x !== after; x = x.nextElementSibling) added.push(x as HTMLElement);
      snaps.push({ el: p, html, added, key: it.key });
      next[it.key] = "ok";
      n += 1;
    }
    setState(next);
    setUndo(snaps);
    if (n) onChanged();
  }

  function revert() {
    for (const s of [...undo].reverse()) {
      s.el.innerHTML = s.html;
      for (const el of s.added) el.remove();
    }
    const keys = new Set(undo.map((s) => s.key));
    setState((st) => Object.fromEntries(Object.entries(st).filter(([k]) => !keys.has(k))));
    setUndo([]);
    onChanged();
  }

  if (!items.length) return null;
  const small = "h-7 px-2.5";
  return (
    <div ref={box} className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="t12m flex-1 text-muted-foreground">{open.length ? `${open.length} chỗ cần xem` : "Đã xem hết"}</span>
        {undo.length > 0 && <button type="button" className={cn(actionClass("default"), small)} onClick={revert}><Undo2 className="size-3.5" />Hoàn tác</button>}
        {fixes.length > 1 && (
          <button type="button" className={cn(actionClass("primary"), small)} onClick={() => accept(fixes)}><Check className="size-3.5" />Nhận tất cả ({fixes.length})</button>
        )}
      </div>
      {items.map((it) => {
        const st = state[it.key];
        const check = it.kind === "check";
        return (
          <div key={it.key} data-review={it.key}
               className={cn("rounded-lg border bg-surface p-2.5 transition-colors", st && "opacity-60",
                             active === it.key && !st ? (check ? "border-warn" : "border-bad/60") : "border-border")}>
            <button type="button" className="flex w-full flex-col gap-1 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => show(it)}
                    title="Xem chỗ này trên văn bản">
              {check ? <span className="t12m inline-flex items-center gap-1 text-warn"><CircleAlert className="size-3.5" aria-hidden />Cần anh/chị kiểm tra</span> : null}
              {/* lệch với tài liệu tham chiếu nào — tích nhiều tài liệu vẫn biết ngay phải mở tài liệu nào để so */}
              {it.kind === "check" && it.source ? (
                <span className="t12 inline-flex min-w-0 items-center gap-1 text-muted-foreground"><BookOpen className="size-3.5 shrink-0" aria-hidden /><span className="truncate">Theo: {it.source}</span></span>
              ) : null}
              {it.why ? <span className="t12m">{it.why}</span> : null}
              {it.kind === "fix" ? (
                <span className="t14 break-words">
                  {clipSame(diffWords(it.edit.before, it.edit.text)).map((x, i) =>
                    x.kind === "del" ? <del key={i} className="rounded-sm bg-bad/10 text-bad">{x.text}</del>
                    : x.kind === "ins" ? <ins key={i} className="rounded-sm bg-ok/15 text-ok no-underline">{x.text}</ins>
                    : <span key={i}>{x.text}</span>)}
                </span>
              ) : it.quote ? <span className="t14 break-words"><mark className="rounded-sm bg-warn/15 text-foreground">{it.quote}</mark></span> : null}
            </button>
            {st ? (
              <p className={cn("t12m mt-2", st === "ok" ? "text-ok" : "text-muted-foreground")}>
                {st === "ok" ? "Đã nhận" : st === "stale" ? "Đoạn này đã sửa sau khi rà — rà soát lại nếu cần" : check ? "Đã kiểm tra" : "Đã bỏ qua"}
              </p>
            ) : (
              <div className="mt-2 flex items-center gap-1.5">
                {check ? (
                  <button type="button" className={cn(actionClass("default"), small)} onClick={() => setState((s) => ({ ...s, [it.key]: "skip" }))}>
                    <Check className="size-3.5" />Đã kiểm tra
                  </button>
                ) : (
                  <>
                    <button type="button" className={cn(actionClass("primary"), small)} onClick={() => accept([it.key])}><Check className="size-3.5" />Nhận</button>
                    <button type="button" className={cn(actionClass("default"), small)} onClick={() => setState((s) => ({ ...s, [it.key]: "skip" }))}>
                      <X className="size-3.5" />Bỏ
                    </button>
                  </>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
