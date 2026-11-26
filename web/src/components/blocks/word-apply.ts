import { splitParagraphs, type AssistResult } from "@/lib/word";

// Đặt chữ trợ lý đề xuất lên TRANG GIẤY (chưa ghi file — người / màn Soạn bấm Lưu thành phiên bản). Dùng chung: trợ lý soạn (word-assist), rà soát
// (word-review), màn Soạn (studio).

/** Đặt chữ mới cho một đoạn trên trang giấy: giữ định dạng của lượt chữ đầu (mã nguồn + kiểu), bỏ phần còn lại. */
export function setText(p: HTMLElement, text: string) {
  const first = p.querySelector<HTMLElement>("span[data-pid]");
  const span = first ? (first.cloneNode(false) as HTMLElement) : document.createElement("span");
  span.textContent = text;
  p.replaceChildren(span);
}

/** Đoạn MỚI ngay sau ``ref`` (chép định dạng đoạn + lượt chữ đầu của ``model``; bỏ mã ⇒ lúc lưu là đoạn mới). */
export function newParaAfter(ref: HTMLElement, model: HTMLElement, text: string): HTMLElement {
  const p = model.cloneNode(false) as HTMLElement;
  delete p.dataset.id;
  const first = model.querySelector<HTMLElement>("span[data-pid]");
  const span = first ? (first.cloneNode(false) as HTMLElement) : document.createElement("span");
  span.textContent = text;
  p.appendChild(span);
  ref.after(p);
  return p;
}

/** Áp TẤT CẢ đề xuất lên trang giấy (màn "Soạn": agent viết thẳng, người hoàn tác bằng phiên bản) — cùng cách đặt chữ / thêm đoạn như "Áp dụng". Trả số mục đã áp. */
export function applyAll(root: HTMLElement, result: Pick<AssistResult, "edits" | "additions">): number {
  const find = (id: string) => root.querySelector<HTMLElement>(`[data-seg] > p[data-id="${CSS.escape(id)}"]`);
  let n = 0;
  for (const e of result.edits) {
    const p = find(e.id);
    if (!p) continue;
    const [head, ...rest] = splitParagraphs(e.text);
    setText(p, head);
    flash(p);
    let tail = p;
    for (const line of rest) {
      tail = newParaAfter(tail, p, line);
      flash(tail);
    }
    n += 1;
  }
  const tail = new Map<string, HTMLElement>();
  for (const a of result.additions) {
    const ref = tail.get(a.after) ?? find(a.after);
    const model = find(a.after) ?? ref;
    if (!ref || !model) continue;
    let last = ref;
    for (const line of splitParagraphs(a.text)) {
      last = newParaAfter(last, model, line);
      flash(last);
    }
    tail.set(a.after, last);
    n += 1;
  }
  return n;
}

export function flash(el: HTMLElement) {
  el.animate([{ backgroundColor: "#fef08a" }, { backgroundColor: "transparent" }], { duration: 1600 });
}
