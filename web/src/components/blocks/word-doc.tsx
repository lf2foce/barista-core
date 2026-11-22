"use client";

import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlignCenter, AlignJustify, AlignLeft, AlignRight, Baseline, Bold, Download, ExternalLink, FileText, FilePlus, History, IndentDecrease, IndentIncrease, Italic, Minus, Plus,
  Printer, Redo2, RefreshCw, Save, Send, Sparkles, Underline, Undo2, Upload,
} from "lucide-react";
import { AssistPanel } from "@/components/blocks/word-assist";
import { api } from "@/lib/api";
import { saveBlob } from "@/lib/download";
import { standaloneHref } from "@/lib/standalone";
import { datetime } from "@/lib/format";
import { newKey } from "@/lib/idempotency";
import {
  buildOps, indexParas, type EditedPara, type WordAlign, type EditedRun, type Segment, type WordBlock, type WordCell, type WordData, type WordFormat, type WordPara,
  type WordOps, type WordRun, type WordTable,
} from "@/lib/word";
import { actionClass } from "@/components/primitives";
import { cn } from "@/lib/utils";
import { useDesk } from "@/components/desk/desk-context";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

/**
 * Văn bản Word sửa trong app (0227). Hai chế độ theo khối server gửi: THƯ VIỆN (chưa chọn văn bản: tải lên + danh sách) và TRÌNH SỬA (trang giấy).
 * Trình sửa chỉ VẼ mô hình server dựng từ file gốc và THU LẠI chữ người gõ; lưu = gửi danh sách thay đổi, máy chủ áp vào file gốc rồi trả mô hình mới.
 * Trang giấy luôn nền trắng chữ đen (như Word), không theo giao diện sáng / tối.
 */
export function WordDocBlock({ block }: { block: WordData }) {
  return block.documentId ? <DeskEditor initial={block} /> : <Library initial={block} />;
}

function DeskEditor({ initial }: { initial: WordData }) {
  const desk = useDesk();
  return <WordEditor initial={initial} notify={desk.show} />;
}

/** Chạy một truy vấn và lấy khối `word` của trang trả về (không có ⇒ null). */
export async function loadWord(query: string, input: Record<string, string>): Promise<WordData | null> {
  const page = await api.query(query, input);
  const b = page.kind === "card" ? page.blocks.find((x) => x.type === "word") : undefined;
  return b?.type === "word" ? b : null;
}

// ── Thư viện ──

function Library({ initial }: { initial: WordData }) {
  const desk = useDesk();
  const [data, setData] = useState(initial);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const reload = useCallback(async () => {
    const next = await loadWord(data.ops.home, {});
    if (next) setData(next);
  }, [data.ops.home]);

  async function upload(file: File) {
    setBusy(true);
    try {
      const r = await api.wordUpload(data.uploadUrl, file);
      const out = (r.output ?? {}) as { document_id?: unknown; title?: unknown };
      if (r.status !== "ok" || typeof out.document_id !== "string") {
        desk.show(r.error || "Không tải được file", true);
        return;
      }
      await reload();
      desk.openPage(data.ops.view, { document_id: out.document_id }, typeof out.title === "string" ? out.title : "Văn bản");
    } catch (e) {
      desk.show(e instanceof Error ? e.message : "Không tải được file", true);
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  async function createBlank() {
    setBusy(true);
    try {
      const r = await api.command(data.ops.create, {}, newKey());
      const out = (r.output ?? {}) as { document_id?: unknown; title?: unknown };
      if (r.status !== "ok" || typeof out.document_id !== "string") {
        desk.show(r.error || "Không tạo được văn bản", true);
        return;
      }
      await reload();
      desk.openPage(data.ops.view, { document_id: out.document_id }, typeof out.title === "string" ? out.title : "Văn bản mới");
    } catch (e) {
      desk.show(e instanceof Error ? e.message : "Không tạo được văn bản", true);
    } finally {
      setBusy(false);
    }
  }

  async function archive(id: string) {
    const r = await api.command(data.ops.archive, { document_id: id }, newKey());
    if (r.status !== "ok") desk.show(r.error || "Không cất được", true);
    await reload();
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        {data.ops.create && (
          <button type="button" className={actionClass("primary")} disabled={busy} onClick={() => void createBlank()} title="Trang trắng khổ A4 — tự viết hoặc nhờ trợ lý AI viết">
            <FilePlus className="size-4" />
            Văn bản mới
          </button>
        )}
        <button type="button" className={actionClass("default")} disabled={busy} onClick={() => input.current?.click()}>
          <Upload className="size-4" />
          {busy ? "Đang xử lý…" : "Tải file Word lên"}
        </button>
        <input ref={input} type="file" accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document" className="hidden"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f); }} />
        <span className="t12 text-muted-foreground">Chỉ nhận .docx, tối đa 20 MB</span>
      </div>
      {data.documents.length === 0 ? (
        <p className="t14 text-muted-foreground">Chưa có văn bản nào.</p>
      ) : (
        <ul className="divide-y divide-hairline rounded-xl border border-border bg-surface">
          {data.documents.map((d) => (
            <li key={d.id} className="flex items-center gap-3 px-3 py-2.5">
              <FileText className="size-4 shrink-0 text-muted-foreground" />
              <button type="button" className="min-w-0 flex-1 text-left outline-none" onClick={() => desk.openPage(data.ops.view, { document_id: d.id }, d.title)}>
                <span className="t14m block truncate">{d.title}</span>
                <span className="t12 text-muted-foreground">Phiên bản {d.version} · {datetime(d.updatedAt)}</span>
              </button>
              <ArchiveButton onConfirm={() => archive(d.id)} />
            </li>
          ))}
        </ul>
      )}
      {data.notes.map((n, i) => <p key={i} className="t12 text-muted-foreground">{n}</p>)}
    </div>
  );
}

function ArchiveButton({ onConfirm }: { onConfirm: () => Promise<void> }) {
  const [asking, setAsking] = useState(false);
  if (!asking) return <button type="button" className="t12 text-muted-foreground hover:text-foreground" onClick={() => setAsking(true)}>Cất</button>;
  return (
    <span className="flex items-center gap-2">
      <button type="button" className="t12m text-bad" onClick={() => void onConfirm()}>Cất văn bản này</button>
      <button type="button" className="t12 text-muted-foreground" onClick={() => setAsking(false)}>Thôi</button>
    </span>
  );
}

// ── Trình sửa ──

type Version = { version: number; note: string; created_at: string; current: string };

type Notify = (text: string, isError?: boolean) => void;

// Nhóm phổ biến nhất của một trình soạn văn bản (không phải mọi thứ Word có)
const FONTS = ["Times New Roman", "Arial", "Calibri", "Tahoma", "Verdana", "Courier New"];
const SIZES = [8, 9, 10, 11, 12, 13, 14, 16, 18, 20, 24, 28, 36];
const COLORS = ["000000", "7F7F7F", "C00000", "FF0000", "ED7D31", "FFC000", "00B050", "0070C0", "002060", "7030A0"];
const LINES = [1, 1.15, 1.5, 2];
const INDENT_STEP = 36;   // nửa inch, như Word
const tool = "grid size-8 shrink-0 place-items-center rounded-md text-muted-foreground outline-none hover:bg-foreground/5 hover:text-foreground disabled:opacity-40";
// nút biểu tượng trong viên nút (đầu ngăn văn bản của màn Soạn) — cùng dáng nhóm nút đầu ngăn của Soạn với AI
const PILL = "grid h-full w-9 shrink-0 place-items-center text-foreground/80 outline-none hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring disabled:opacity-40";
const pick = "t14 h-8 shrink-0 rounded-md border border-border bg-surface px-1.5 outline-none hover:bg-foreground/5";

const countWords = (el: HTMLElement | null): number => (el?.innerText.match(/\S+/g) ?? []).length;

/**
 * Trình sửa. `full` = trang riêng ở tab mới: thanh tiêu đề + dải công cụ + vùng giấy cuộn riêng + thanh trạng thái (thu phóng, số từ);
 * không `full` = nằm trong hộp của bàn làm việc, cùng dải công cụ, thêm nút mở tab mới. `notify` = cách báo ngắn của nơi chứa.
 */
/** Điều khiển trình soạn từ BÊN NGOÀI (màn "Soạn" — agent bên trái): trang giấy, vùng chọn gần nhất, đánh dấu đã sửa, lưu thành phiên bản. */
export type WordHandle = { sheet: () => HTMLDivElement | null; range: () => Range | null; markDirty: () => void; save: () => Promise<void> };

export function WordEditor({ initial, notify, full = false, handleRef, agentOutside = false, pane = false, lead }: {
  initial: WordData; notify: Notify; full?: boolean; handleRef?: React.RefObject<WordHandle | null>; agentOutside?: boolean;
  /** `pane`: nút đặt ĐẦU hàng nút (vd chọn "Trò chuyện / Bản xem" khi màn hẹp). */
  lead?: React.ReactNode;
  /** Ngăn bên phải của màn "Soạn" (agent bên trái): đầu ngăn một hàng (Lưu · trạng thái · viên nút biểu tượng), dải định dạng một hàng cuộn ngang, trang
   *  giấy đặt thẳng trên nền ngăn (không hộp xám bọc ngoài). */
  pane?: boolean;
}) {
  const [data, setData] = useState(initial);
  const [nonce, setNonce] = useState(0);        // đổi ⇒ vẽ lại trang giấy từ mô hình (sau lưu / tải lại / khôi phục)
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [caret, setCaret] = useState({ font: "", sz: 0 });   // phông / cỡ tại con trỏ — hiện trên dải công cụ
  const [words, setWords] = useState(0);
  const [zoom, setZoom] = useState<number | null>(null);     // null = vừa khung
  const [assist, setAssist] = useState(full);                 // khung trợ lý soạn thảo: tab riêng mở sẵn, hộp thoại mở khi bấm
  const sheet = useRef<HTMLDivElement>(null);
  const range = useRef<Range | null>(null);                  // vùng chọn gần nhất trong trang giấy (ô chọn phông / cỡ lấy mất tiêu điểm)
  const paras = useMemo(() => indexParas(data.blocks), [data]);

  const reload = useCallback(async () => {
    const next = await loadWord(data.ops.view, { document_id: data.documentId });
    if (!next) return;
    setData(next);
    setNonce((n) => n + 1);
    setDirty(false);
    setError("");
  }, [data.documentId, data.ops.view]);

  const save = useCallback(async () => {
    if (!sheet.current || saving) return;
    const ops = buildOps(paras, readSegments(sheet.current));
    if (!ops.length) {
      setDirty(false);
      return;
    }
    setSaving(true);
    setError("");
    try {
      const r = await api.command(data.ops.save, { document_id: data.documentId, base_version: data.version, ops_json: JSON.stringify(ops) }, newKey());
      if (r.status === "ok") {
        await reload();
        notify("Đã lưu phiên bản mới");
      } else setError(r.error || "Không lưu được");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không lưu được");
    } finally {
      setSaving(false);
    }
  }, [data.documentId, data.ops.save, data.version, notify, paras, reload, saving]);

  // Trang giấy là memo: hai hàm gọi lại phải GIỮ NGUYÊN danh tính, không thì mỗi lần đổi trạng thái React vẽ lại trang và xoá chữ đang gõ
  const count = useRef<ReturnType<typeof setTimeout> | null>(null);
  const markDirty = useCallback(() => {
    setDirty(true);
    if (count.current) clearTimeout(count.current);
    count.current = setTimeout(() => setWords(countWords(sheet.current)), 400);
  }, []);
  const saveRef = useRef(save);
  useEffect(() => { saveRef.current = save; }, [save]);
  const saveNow = useCallback(() => void saveRef.current(), []);
  useEffect(() => {
    if (!handleRef) return;
    handleRef.current = { sheet: () => sheet.current, range: () => range.current, markDirty, save: () => saveRef.current() };
    return () => {
      handleRef.current = null;
    };
  }, [handleRef, markDirty]);

  useEffect(() => {
    const t = setTimeout(() => setWords(countWords(sheet.current)), 0);   // sau khi trang giấy vẽ xong
    return () => clearTimeout(t);
  }, [nonce, data.version]);

  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();   // rời trang khi còn thay đổi chưa lưu ⇒ trình duyệt hỏi lại
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  // Nhớ vùng chọn + đọc phông / cỡ tại con trỏ
  useEffect(() => {
    const onSel = () => {
      const sel = window.getSelection();
      const node = sel && sel.rangeCount ? sel.anchorNode : null;
      const el = node instanceof Element ? node : node?.parentElement ?? null;
      if (!sel || !el || !sheet.current?.contains(el) || !el.closest("[data-seg]")) return;
      range.current = sel.getRangeAt(0).cloneRange();
      const cs = getComputedStyle(el);
      const font = cs.fontFamily.split(",")[0].trim().replace(/^["']|["']$/g, "");
      const sz = Math.round(Number.parseFloat(cs.fontSize) * 0.75 * 2) / 2;
      setCaret((c) => (c.font === font && c.sz === sz ? c : { font, sz }));
    };
    document.addEventListener("selectionchange", onSel);
    return () => document.removeEventListener("selectionchange", onSel);
  }, []);

  /** Trả vùng chọn về trang giấy trước khi áp định dạng (bấm ô chọn / bảng màu làm mất tiêu điểm). */
  function restore(): Range | null {
    const r = range.current;
    const sel = window.getSelection();
    if (!r || !sel || !sheet.current?.contains(r.commonAncestorContainer)) return null;
    const host = r.startContainer instanceof Element ? r.startContainer : r.startContainer.parentElement;
    host?.closest<HTMLElement>("[data-seg]")?.focus({ preventScroll: true });
    sel.removeAllRanges();
    sel.addRange(r);
    return r;
  }

  function exec(cmd: string, value?: string) {
    if (!restore()) return;
    document.execCommand("styleWithCSS", false, "true");
    document.execCommand(cmd, false, value);
    setDirty(true);
  }

  function setSize(pt: number) {
    if (!restore() || !sheet.current) return;
    document.execCommand("styleWithCSS", false, "true");
    document.execCommand("fontSize", false, "7");   // trình duyệt chỉ có 7 nấc cỡ: đánh dấu bằng nấc 7 rồi đổi thành cỡ pt thật
    sheet.current.querySelectorAll<HTMLElement>('font[size="7"], [style*="xxx-large"]').forEach((el) => {
      el.removeAttribute("size");
      el.style.fontSize = `${pt}pt`;
      el.querySelectorAll<HTMLElement>("[style]").forEach((inner) => { if (inner.style.fontSize) inner.style.fontSize = `${pt}pt`; });
    });
    setDirty(true);
  }

  /** Các đoạn đang chọn (trong vùng sửa được). */
  function eachPara(fn: (p: HTMLElement) => void) {
    const r = restore();
    if (!r || !sheet.current) return;
    let n = 0;
    sheet.current.querySelectorAll<HTMLElement>("[data-seg] > *").forEach((p) => {
      if (!r.intersectsNode(p)) return;
      fn(p);
      n += 1;
    });
    if (n) setDirty(true);
  }
  // Đổi ngay trên trang giấy + đánh dấu `data-…` để lúc lưu biết người sửa đã chọn
  const setAlign = (to: WordAlign) => eachPara((p) => { p.style.textAlign = to; p.dataset.align = to; });
  const setLine = (x: number) => eachPara((p) => { p.style.lineHeight = String(x * 1.17); p.dataset.line = String(x); });
  const indent = (by: number) => eachPara((p) => {
    const next = Math.max(0, (Number.parseFloat(p.style.marginLeft) || 0) + by);
    p.style.marginLeft = `${next}pt`;
    p.dataset.indent = String(next);
  });

  async function download() {
    try {
      saveBlob(await api.download(data.docxUrl), `${data.title}.docx`);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Không tải được file", true);
    }
  }

  async function rename(title: string) {
    const next = title.trim();
    if (!next || next === data.title || !data.ops.rename) return;
    const r = await api.command(data.ops.rename, { document_id: data.documentId, title: next }, newKey());
    if (r.status !== "ok") {
      notify(r.error || "Không đổi tên được", true);
      return;
    }
    setData((d) => ({ ...d, title: next }));
    document.title = next;
    notify("Đã đổi tên");
  }

  const keep = (e: React.MouseEvent) => e.preventDefault();   // nút bấm không lấy tiêu điểm khỏi trang giấy
  const sep = <span className="mx-1 h-5 w-px shrink-0 bg-hairline" />;
  const fonts = caret.font && !FONTS.includes(caret.font) ? [caret.font, ...FONTS] : FONTS;
  const sizes = caret.sz && !SIZES.includes(caret.sz) ? [...SIZES, caret.sz].sort((a, b) => a - b) : SIZES;

  const ribbon = data.editable && (
    <>
      <button type="button" title="Hoàn tác (Ctrl+Z)" aria-label="Hoàn tác" className={tool} onMouseDown={keep} onClick={() => exec("undo")}><Undo2 className="size-4" /></button>
      <button type="button" title="Làm lại (Ctrl+Y)" aria-label="Làm lại" className={tool} onMouseDown={keep} onClick={() => exec("redo")}><Redo2 className="size-4" /></button>
      {sep}
      <select aria-label="Phông chữ" title="Phông chữ" className={`${pick} w-40`} value={caret.font} onChange={(e) => exec("fontName", e.target.value)}>
        {!caret.font && <option value="">Phông chữ</option>}
        {fonts.map((f) => <option key={f} value={f}>{f}</option>)}
      </select>
      <select aria-label="Cỡ chữ" title="Cỡ chữ" className={`${pick} w-16`} value={caret.sz || ""} onChange={(e) => setSize(Number(e.target.value))}>
        {!caret.sz && <option value="">Cỡ</option>}
        {sizes.map((s) => <option key={s} value={s}>{s}</option>)}
      </select>
      {sep}
      <button type="button" title="Đậm (Ctrl+B)" aria-label="Đậm" className={tool} onMouseDown={keep} onClick={() => exec("bold")}><Bold className="size-4" /></button>
      <button type="button" title="Nghiêng (Ctrl+I)" aria-label="Nghiêng" className={tool} onMouseDown={keep} onClick={() => exec("italic")}><Italic className="size-4" /></button>
      <button type="button" title="Gạch chân (Ctrl+U)" aria-label="Gạch chân" className={tool} onMouseDown={keep} onClick={() => exec("underline")}><Underline className="size-4" /></button>
      <Popover>
        <PopoverTrigger className={tool} title="Màu chữ" aria-label="Màu chữ"><Baseline className="size-4" /></PopoverTrigger>
        <PopoverContent align="start" className="w-auto p-2">
          <div className="grid grid-cols-5 gap-1.5">
            {COLORS.map((hex) => (
              <button key={hex} type="button" aria-label={`Màu ${hex}`} className="size-6 rounded-md border border-border outline-none hover:scale-110"
                style={{ backgroundColor: `#${hex}` }} onClick={() => exec("foreColor", `#${hex}`)} />
            ))}
          </div>
        </PopoverContent>
      </Popover>
      {sep}
      <button type="button" title="Căn trái" aria-label="Căn trái" className={tool} onMouseDown={keep} onClick={() => setAlign("left")}><AlignLeft className="size-4" /></button>
      <button type="button" title="Căn giữa" aria-label="Căn giữa" className={tool} onMouseDown={keep} onClick={() => setAlign("center")}><AlignCenter className="size-4" /></button>
      <button type="button" title="Căn phải" aria-label="Căn phải" className={tool} onMouseDown={keep} onClick={() => setAlign("right")}><AlignRight className="size-4" /></button>
      <button type="button" title="Căn đều hai bên" aria-label="Căn đều hai bên" className={tool} onMouseDown={keep} onClick={() => setAlign("justify")}><AlignJustify className="size-4" /></button>
      {sep}
      <select aria-label="Giãn dòng" title="Giãn dòng" className={`${pick} w-28`} value="" onChange={(e) => { if (e.target.value) setLine(Number(e.target.value)); }}>
        <option value="">Giãn dòng</option>
        {LINES.map((x) => <option key={x} value={x}>{x.toLocaleString("vi-VN", { minimumFractionDigits: 1 })}</option>)}
      </select>
      <button type="button" title="Giảm thụt lề" aria-label="Giảm thụt lề" className={tool} onMouseDown={keep} onClick={() => indent(-INDENT_STEP)}><IndentDecrease className="size-4" /></button>
      <button type="button" title="Tăng thụt lề" aria-label="Tăng thụt lề" className={tool} onMouseDown={keep} onClick={() => indent(INDENT_STEP)}><IndentIncrease className="size-4" /></button>
      {data.ops.assist && !agentOutside && (
        <>
          {sep}
          <button type="button" aria-pressed={assist} title="Trợ lý soạn thảo: sửa chính tả, viết lại, viết tiếp…" onMouseDown={keep} onClick={() => setAssist((a) => !a)}
            className={`t14m inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md px-2.5 outline-none ${assist ? "bg-primary/10 text-primary" : "text-primary hover:bg-primary/10"}`}>
            <Sparkles className="size-4" />
            Trợ lý AI
          </button>
        </>
      )}
    </>
  );
  const helper = data.editable && data.ops.assist && assist && (
    <AssistPanel op={data.ops.assist} documentId={data.documentId} sheet={sheet} range={range} onChanged={markDirty} notify={notify} onClose={() => setAssist(false)} />
  );

  const saveButton = data.editable && (
    <button type="button" className={actionClass(dirty ? "primary" : "default")} disabled={!dirty || saving} onClick={() => void save()} title="Lưu thành phiên bản mới (Ctrl+S)">
      <Save className="size-4" />
      <span className={full ? "max-lg:hidden" : ""}>{saving ? "Đang lưu…" : "Lưu"}</span>
    </button>
  );
  const status = saving ? "Đang lưu…" : dirty ? "Có thay đổi chưa lưu" : data.editable ? `Đã lưu · phiên bản ${data.version}` : `Phiên bản ${data.version} · đã cất, chỉ xem`;
  const actions = (
    <>
      {data.share && data.ops.send && (
        <SendButton op={data.ops.send} documentId={data.documentId} version={data.version} targets={data.share.targets} dirty={dirty} notify={notify} compact={full} />
      )}
      <VersionsButton documentId={data.documentId} ops={data.ops} disabled={dirty} onRestored={reload} notify={notify} compact={full ? "narrow" : "always"} />
      <a className={actionClass("default")} href={`/api/core${data.pdfUrl}`} target="_blank" rel="noreferrer" title="Bản in đúng từng trang (PDF)">
        <Printer className="size-4" />
        <span className={full ? "max-lg:hidden" : "sr-only"}>Bản in</span>
      </a>
      <button type="button" className={actionClass("default")} onClick={() => void download()} title="Tải file Word của phiên bản đã lưu">
        <Download className="size-4" />
        <span className={full ? "max-lg:hidden" : "sr-only"}>Tải Word</span>
      </button>
    </>
  );
  const problem = error && (
    <div className="t14 flex items-center gap-3 rounded-lg bg-bad/10 px-3 py-2 text-bad">
      <span className="min-w-0 flex-1">{error}</span>
      <button type="button" className="t14m inline-flex items-center gap-1.5" onClick={() => void reload()}><RefreshCw className="size-4" />Tải lại</button>
    </div>
  );
  const paper = <Sheet key={`${data.version}:${nonce}`} data={data} sheetRef={sheet} onDirty={markDirty} onSave={saveNow} />;

  if (pane) {
    return (
      <div className="flex h-full min-h-0 flex-col">
        {/* MỘT khối đầu ngăn (nền trắng, cùng lề): hàng nút + dải định dạng — không ba lớp rời nhau */}
        <div className="shrink-0 border-b border-hairline bg-surface">
        <div className="flex items-center gap-2 px-3 pt-2 pb-1.5">
          {lead}
          {data.editable ? (
            <button type="button" className={cn(actionClass(dirty ? "primary" : "default"), "h-8 px-3")} disabled={!dirty || saving} onClick={() => void save()} title="Lưu thành phiên bản mới (Ctrl+S)">
              <Save className="size-4" />{saving ? "Đang lưu…" : "Lưu"}
            </button>
          ) : null}
          <span className="t12 min-w-0 flex-1 truncate text-muted-foreground"><span className="@max-md:hidden">{status} · {words.toLocaleString("vi-VN")} từ</span></span>
          <span className="inline-flex h-8 shrink-0 items-center overflow-hidden rounded-full border border-border bg-surface">
            {data.ops.view && (
              <a className={PILL} href={standaloneHref(data.ops.view, { document_id: data.documentId })} target="_blank" rel="noreferrer" title="Mở trình soạn toàn màn hình ở tab mới" aria-label="Mở tab mới">
                <ExternalLink className="size-4" />
              </a>
            )}
            {data.share && data.ops.send && (
              <SendButton op={data.ops.send} documentId={data.documentId} version={data.version} targets={data.share.targets} dirty={dirty} notify={notify} compact pill />
            )}
            <VersionsButton documentId={data.documentId} ops={data.ops} disabled={dirty} onRestored={reload} notify={notify} compact="always" pill />
            <a className={PILL} href={`/api/core${data.pdfUrl}`} target="_blank" rel="noreferrer" title="Bản in đúng từng trang (PDF)" aria-label="Bản in">
              <Printer className="size-4" />
            </a>
            <button type="button" className={PILL} onClick={() => void download()} title="Tải file Word của phiên bản đã lưu" aria-label="Tải Word">
              <Download className="size-4" />
            </button>
          </span>
        </div>
        {ribbon && (
          // hẹp: ô chọn phông / cỡ thu gọn, cuối dải mờ dần (còn cuộn ngang được)
          <div className="no-scrollbar flex items-center gap-0.5 overflow-x-auto px-2 pb-1.5 [&_select]:w-auto [&_select]:max-w-32 @max-md:[mask-image:linear-gradient(to_right,black_85%,transparent)]">{ribbon}</div>
        )}
        </div>
        {problem && <div className="px-3 pt-2">{problem}</div>}
        <div className="min-h-0 flex-1 overflow-auto">
          <FitWidth width={data.page.w} fixed={null} bare edge>{paper}</FitWidth>
        </div>
      </div>
    );
  }

  if (!full) {
    return (
      <div className="flex min-h-0 flex-col gap-3">
        <div className="sticky top-0 z-10 flex flex-col gap-1.5 rounded-xl border border-border bg-surface px-2 py-1.5">
          <div className="flex flex-wrap items-center gap-2">
            {saveButton}
            <span className="t12 min-w-0 flex-1 truncate px-1 text-muted-foreground">{status}</span>
            {data.ops.view && (
              <a className={actionClass("default")} href={standaloneHref(data.ops.view, { document_id: data.documentId })} target="_blank" rel="noreferrer" title="Mở trình soạn toàn màn hình ở tab mới">
                <ExternalLink className="size-4" />
                Mở tab mới
              </a>
            )}
            {actions}
          </div>
          {ribbon && <div className="flex flex-wrap items-center gap-1 border-t border-hairline pt-1.5">{ribbon}</div>}
        </div>
        {problem}
        <div className="@container flex min-h-0 flex-1 flex-col gap-3">
          <div className="flex min-h-0 flex-col gap-3 @3xl:flex-row">
            {helper && <aside aria-label="Trợ lý soạn văn bản" className="flex max-h-[65vh] min-h-0 shrink-0 flex-col rounded-xl border border-border bg-surface p-3 @3xl:w-[320px]">{helper}</aside>}
            <div className="min-w-0 flex-1"><FitWidth width={data.page.w} fixed={null}>{paper}</FitWidth></div>
          </div>
        </div>
        {data.notes.map((n, i) => <p key={i} className="t12 text-muted-foreground">{n}</p>)}
      </div>
    );
  }

  const shown = zoom ?? 100;
  return (
    <div className="flex h-dvh flex-col">
      <header className="flex items-center gap-3 border-b border-border bg-surface px-4 py-2">
        <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary max-sm:hidden"><FileText className="size-5" /></span>
        <div className="min-w-0 flex-1">
          <input key={data.title} aria-label="Tên văn bản" defaultValue={data.title} readOnly={!data.editable || !data.ops.rename} maxLength={200}
            className="t16 w-full truncate rounded-md bg-transparent px-1 outline-none hover:bg-foreground/5 focus:bg-foreground/5"
            onBlur={(e) => void rename(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }} />
          <p className="t12 truncate px-1 text-muted-foreground">{status}</p>
        </div>
        {saveButton}
        {actions}
      </header>
      {ribbon && <div className="flex flex-wrap items-center gap-1 border-b border-border bg-surface px-3 py-1.5">{ribbon}</div>}
      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        {helper && <aside aria-label="Trợ lý soạn văn bản" className="flex max-h-[45vh] shrink-0 flex-col overflow-y-auto border-b border-border bg-surface p-4 md:max-h-none md:w-[340px] md:border-r md:border-b-0">{helper}</aside>}
        <div className="min-h-0 min-w-0 flex-1 overflow-auto bg-foreground/5">
          {problem && <div className="mx-auto max-w-3xl px-4 pt-4">{problem}</div>}
          <FitWidth width={data.page.w} fixed={zoom === null ? null : zoom / 100} bare>{paper}</FitWidth>
        </div>
      </div>
      <footer className="t12 flex items-center gap-3 border-t border-border bg-surface px-4 py-1.5 text-muted-foreground">
        <span>{words.toLocaleString("vi-VN")} từ</span>
        {data.notes.length > 0 && <span className="min-w-0 flex-1 truncate" title={data.notes.join(" ")}>{data.notes[0]}</span>}
        <span className="ml-auto flex items-center gap-1">
          <button type="button" className={tool} aria-label="Thu nhỏ" title="Thu nhỏ" onClick={() => setZoom(Math.max(50, shown - 10))}><Minus className="size-4" /></button>
          <button type="button" className="t12m w-20 rounded-md py-1 text-center outline-none hover:bg-foreground/5" title="Bấm để về vừa khung" onClick={() => setZoom(null)}>
            {zoom === null ? "Vừa khung" : `${zoom}%`}
          </button>
          <button type="button" className={tool} aria-label="Phóng to" title="Phóng to" onClick={() => setZoom(Math.min(200, shown + 10))}><Plus className="size-4" /></button>
        </span>
      </footer>
    </div>
  );
}

/**
 * Khung quanh trang giấy. `fixed` = hệ số thu phóng người chọn; null ⇒ co cho vừa bề rộng khung (hẹp hơn khổ giấy thì thu nhỏ đều, rộng hơn giữ đúng cỡ).
 * `bare` = nằm trong vùng cuộn của trang riêng (không tự có nền bo góc).
 */
function FitWidth({ width, fixed, bare = false, edge = false, children }: { width: number; fixed: number | null; bare?: boolean; edge?: boolean; children: React.ReactNode }) {
  const box = useRef<HTMLDivElement>(null);
  const [fit, setFit] = useState(1);
  const [tight, setTight] = useState(false);   // `edge`: khung hẹp hơn khổ giấy ⇒ trang tràn trọn bề ngang, không lề (màn nhỏ đọc được chữ to hơn)
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => {
      const page = (width * 96) / 72 + 2;
      const narrow = edge && el.clientWidth < page + 48;
      setTight(narrow);
      setFit(Math.min(1, Math.max(0.3, (el.clientWidth - (narrow ? 0 : 48)) / page)));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [width, edge]);
  return (
    <div ref={box} className={tight ? "" : bare ? "p-6" : "rounded-xl bg-foreground/5 p-4"}>
      <div className="mx-auto w-fit" style={{ zoom: fixed ?? fit }}>{children}</div>
    </div>
  );
}

/**
 * Gửi PHIÊN BẢN ĐANG LƯU vào một phòng / cuộc nhắn riêng (máy chủ liệt kê nơi gửi theo quyền người xem): thành một tin kèm tệp .docx — người
 * trong phòng xem / tải về; bản của mình vẫn ở khu vực riêng, sửa tiếp được. Còn thay đổi chưa lưu ⇒ phải lưu trước (gửi đúng thứ người khác sẽ thấy).
 */
function SendButton({ op, documentId, version, targets, dirty, notify, compact, pill = false }: {
  op: string; documentId: string; version: number; targets: { value: string; label: string }[]; dirty: boolean; notify: Notify; compact: boolean; pill?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [target, setTarget] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const key = useRef(newKey());   // một khoá cho một lần gửi (bấm hai lần không thành hai tin)

  async function send() {
    if (!target || busy) return;
    setBusy(true);
    try {
      const r = await api.command(op, { document_id: documentId, target, note }, key.current);
      if (r.status !== "ok") {
        notify(r.error || "Chưa gửi được", true);
        key.current = newKey();
        return;
      }
      notify(`Đã gửi phiên bản ${version} vào ${targets.find((t) => t.value === target)?.label ?? "phòng"}`);
      setOpen(false);
      setNote("");
      key.current = newKey();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Chưa gửi được", true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Popover open={open} onOpenChange={(o) => { setOpen(o); if (o && !target && targets.length === 1) setTarget(targets[0].value); }}>
      <PopoverTrigger className={pill ? PILL : actionClass("default")} title={dirty ? "Lưu thay đổi trước khi gửi" : "Gửi phiên bản đang lưu vào một phòng"} disabled={dirty}>
        <Send className="size-4" />
        <span className={pill ? "sr-only" : compact ? "max-lg:hidden" : ""}>Gửi vào phòng</span>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 gap-3 p-3">
        <p className="t14m">Gửi phiên bản {version} vào phòng</p>
        {targets.length === 0 ? (
          <p className="t12 text-muted-foreground">Anh/chị chưa ở phòng nào. Tạo phòng hoặc nhắn riêng ở mục Tin nhắn rồi quay lại gửi.</p>
        ) : (
          <>
            <select aria-label="Gửi vào" className="t14 h-8 w-full rounded-lg border border-input bg-field px-2 outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40"
              value={target} onChange={(e) => setTarget(e.target.value)}>
              <option value="" disabled>Chọn phòng…</option>
              {targets.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
            </select>
            <textarea aria-label="Lời nhắn" value={note} onChange={(e) => setNote(e.target.value)} rows={2} maxLength={2000} placeholder="Lời nhắn kèm (không bắt buộc)"
              className="t14 resize-none rounded-lg border border-input bg-field px-2.5 py-2 outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40" />
            <p className="t12 text-muted-foreground">Người trong phòng xem và tải về được; bản của anh/chị vẫn ở đây, sửa tiếp không ảnh hưởng bản đã gửi.</p>
            <button type="button" className={actionClass("primary")} disabled={!target || busy} onClick={() => void send()}>
              <Send className="size-4" />
              {busy ? "Đang gửi…" : "Gửi"}
            </button>
          </>
        )}
      </PopoverContent>
    </Popover>
  );
}

/** `compact`: "narrow" = ẩn chữ khi cửa sổ hẹp (trang riêng) · "always" = chỉ biểu tượng (trong hộp thoại, chỗ hẹp) · không có = luôn có chữ. */
function VersionsButton({ documentId, ops, disabled, onRestored, notify, compact, pill = false }: {
  documentId: string; ops: WordOps; disabled: boolean; onRestored: () => Promise<void>; notify: Notify; compact?: "narrow" | "always"; pill?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<Version[] | null>(null);

  async function load(next: boolean) {
    setOpen(next);
    if (!next) return;
    setRows(null);
    try {
      const page = await api.queryRaw<{ rows?: Version[] }>(ops.versions, { document_id: documentId });
      setRows(Array.isArray(page.rows) ? page.rows : []);
    } catch {
      setRows([]);
    }
  }

  async function restore(version: number) {
    const r = await api.command(ops.restore, { document_id: documentId, version }, newKey());
    if (r.status !== "ok") {
      notify(r.error || "Không khôi phục được", true);
      return;
    }
    setOpen(false);
    await onRestored();
    notify(`Đã khôi phục bản ${version}`);
  }

  return (
    <Popover open={open} onOpenChange={(o) => void load(o)}>
      <PopoverTrigger className={pill ? PILL : actionClass("default")} title="Các phiên bản đã lưu" aria-label="Các phiên bản đã lưu">
        <History className="size-4" />
        <span className={compact === "always" ? "sr-only" : compact === "narrow" ? "max-lg:hidden" : ""}>Phiên bản</span>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 p-2">
        {rows === null ? <p className="t12 p-2 text-muted-foreground">Đang tải…</p> : (
          <ul className="max-h-80 overflow-y-auto">
            {rows.map((v) => (
              <li key={v.version} className="flex items-center gap-2 rounded-md px-2 py-1.5">
                <span className="min-w-0 flex-1">
                  <span className="t14m block truncate">Bản {v.version}{v.current === "yes" ? " · đang dùng" : ""}</span>
                  <span className="t12 block truncate text-muted-foreground">{v.note} · {datetime(String(v.created_at))}</span>
                </span>
                {v.current !== "yes" && (
                  <button type="button" className="t12m text-primary disabled:opacity-40" disabled={disabled} title={disabled ? "Lưu thay đổi trước khi khôi phục" : ""}
                    onClick={() => void restore(v.version)}>Khôi phục</button>
                )}
              </li>
            ))}
          </ul>
        )}
      </PopoverContent>
    </Popover>
  );
}

// ── Trang giấy: vẽ MỘT lần từ mô hình, sau đó trình duyệt giữ nội dung người gõ (React không vẽ lại — memo + key theo phiên bản) ──

const HIGHLIGHT: Record<string, string> = {
  yellow: "#ffff00", green: "#00ff00", cyan: "#00ffff", magenta: "#ff00ff", blue: "#0000ff", red: "#ff0000", darkBlue: "#000080", darkCyan: "#008080",
  darkGreen: "#008000", darkMagenta: "#800080", darkRed: "#800000", darkYellow: "#808000", darkGray: "#808080", lightGray: "#c0c0c0", black: "#000000",
};

function runStyle(f: WordFormat): React.CSSProperties {
  const deco = [f.u ? "underline" : "", f.s ? "line-through" : ""].filter(Boolean).join(" ");
  return {
    fontWeight: f.b ? 700 : 400, fontStyle: f.i ? "italic" : "normal", textDecoration: deco || "none",
    ...(f.sz ? { fontSize: `${f.va ? f.sz * 0.7 : f.sz}pt` } : {}), ...(f.color ? { color: `#${f.color}` } : {}),
    ...(f.font ? { fontFamily: `"${f.font}", "Times New Roman", serif` } : {}), ...(f.hl && HIGHLIGHT[f.hl] ? { backgroundColor: HIGHLIGHT[f.hl] } : {}),
    ...(f.va ? { verticalAlign: f.va === "sup" ? "super" : "sub" } : {}),
  };
}

function paraStyle(p: WordPara): React.CSSProperties {
  const s = p.style;
  const indL = s.indL || p.list?.indL || 0;
  const first = s.indFirst || (p.list?.hang ? -p.list.hang : 0);
  return {
    margin: `${s.spBefore}pt 0 ${s.spAfter}pt`, marginLeft: `${indL}pt`, marginRight: `${s.indR}pt`, textIndent: `${first}pt`,
    textAlign: (s.align || "left") as React.CSSProperties["textAlign"], lineHeight: s.linePt ? `${s.linePt}pt` : (s.line ?? 1) * 1.17,
    whiteSpace: "pre-wrap", overflowWrap: "anywhere", tabSize: "36pt", minHeight: "1em",
    ...(s.rule ? { borderBottom: "0.75pt solid #000", minHeight: 0, lineHeight: 0.4 } : {}),
  };
}

function Runs({ p }: { p: WordPara }) {
  if (!p.runs.length) return <span data-pid={p.id} data-src={0} style={runStyle(p.base)}><br /></span>;   // đoạn trống: giữ chiều cao + cỡ chữ để gõ
  return (
    <>
      {p.runs.map((r, i) => (r.img !== null ? <Img key={i} r={r} /> : <span key={i} data-pid={p.id} data-src={r.src} style={runStyle(r)}>{r.text}</span>))}
    </>
  );
}

function Img({ r }: { r: WordRun }) {
  const size = { width: r.w ? `${r.w}pt` : undefined, height: r.h ? `${r.h}pt` : undefined, maxWidth: "100%" };
  // eslint-disable-next-line @next/next/no-img-element -- ảnh nhúng sẵn trong file Word (data URI), không qua trình tối ưu ảnh
  if (r.img) return <img src={r.img} alt="" style={size} />;
  return <span style={{ ...size, display: "inline-block", minWidth: "24pt", minHeight: "18pt", border: "1px dashed #a1a1aa", color: "#71717a", fontSize: "9pt", textAlign: "center" }}>Hình</span>;
}

function Para({ p }: { p: WordPara }) {
  return <p data-id={p.id} data-label={p.list?.label || undefined} className={p.list?.label ? "word-li" : undefined} style={paraStyle(p)}><Runs p={p} /></p>;
}

type Group = { kind: "seg"; paras: WordPara[] } | { kind: "locked"; p: WordPara } | { kind: "table"; t: WordTable };

function groups(blocks: WordBlock[]): Group[] {
  const out: Group[] = [];
  for (const b of blocks) {
    if (b.t === "table") out.push({ kind: "table", t: b });
    else if (b.locked) out.push({ kind: "locked", p: b });
    else {
      const last = out[out.length - 1];
      if (last?.kind === "seg") last.paras.push(b);
      else out.push({ kind: "seg", paras: [b] });
    }
  }
  return out;
}

function Blocks({ blocks, editable }: { blocks: WordBlock[]; editable: boolean }) {
  return (
    <>
      {groups(blocks).map((g, i) => {
        if (g.kind === "table") return <Table key={i} t={g.t} editable={editable} />;
        if (g.kind === "locked") {
          return <div key={i} title={`Chỉ xem: ${g.p.locked}`} style={{ boxShadow: "-3px 0 0 #e4e4e7" }}><Para p={g.p} /></div>;
        }
        return (
          <div key={i} data-seg={g.paras.map((p) => p.id).join(",")} contentEditable={editable} suppressContentEditableWarning spellCheck={false}
            style={{ outline: "none" }}>
            {g.paras.map((p) => <Para key={p.id} p={p} />)}
          </div>
        );
      })}
    </>
  );
}

function Table({ t, editable }: { t: WordTable; editable: boolean }) {
  const total = t.cols.reduce((a, b) => a + b, 0) || 1;   // bảng rộng theo %: cột là tỷ lệ
  const line = (on: boolean) => (on ? "1px solid #000" : "1px dotted #e4e4e7");   // ô không viền: nét chấm mờ để thấy ô khi sửa (không có trong file)
  return (
    <table style={{ borderCollapse: "collapse", tableLayout: t.cols.length ? "fixed" : "auto", margin: t.wPct ? 0 : "0 0 0 -5.4pt", width: t.wPct ? `${t.wPct}%` : undefined }}>
      {t.cols.length > 0 && <colgroup>{t.cols.map((w, i) => <col key={i} style={{ width: t.wPct ? `${(w / total) * 100}%` : `${w}pt` }} />)}</colgroup>}
      <tbody>
        {t.rows.map((r, i) => (
          <tr key={i}>
            {r.cells.map((c: WordCell, j) => (
              <td key={j} colSpan={c.span} rowSpan={c.rowspan}
                style={{ border: line(c.borders ?? t.borders), padding: "0 5.4pt", verticalAlign: c.valign === "center" ? "middle" : c.valign || "top", backgroundColor: c.fill ? `#${c.fill}` : undefined }}>
                <Blocks blocks={c.blocks} editable={editable} />
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const Sheet = memo(function Sheet({ data, sheetRef, onDirty, onSave }: {
  data: WordData; sheetRef: React.RefObject<HTMLDivElement | null>; onDirty: () => void; onSave: () => void;
}) {
  const pg = data.page;
  useEffect(() => {
    try {
      document.execCommand("defaultParagraphSeparator", false, "p");   // Enter tạo <p> mới (không phải <div>)
      document.execCommand("styleWithCSS", false, "true");             // định dạng ghi bằng style (đọc lại được), không bằng thẻ <font>
    } catch {
      /* trình duyệt không hỗ trợ — bộ đọc vẫn nhận <div> */
    }
  }, []);
  return (
    <div ref={sheetRef} className="word-sheet mx-auto"
      style={{ width: `${pg.w}pt`, minHeight: `${Math.min(pg.h, 1200)}pt`, padding: `${pg.mt}pt ${pg.mr}pt ${pg.mb}pt ${pg.ml}pt`, background: "#fff", color: "#000",
        border: "1px solid #d4d4d8", fontFamily: '"Times New Roman", serif', fontSize: "11pt", boxSizing: "border-box" }}
      onInput={(e) => { keepParagraph(e.target); onDirty(); }}
      onPaste={(e) => {
        e.preventDefault();   // chỉ dán CHỮ: định dạng lạ từ nơi khác không vào được file
        document.execCommand("insertText", false, e.clipboardData.getData("text/plain"));
      }}
      onKeyDown={(e) => {
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
          e.preventDefault();
          onSave();
        }
      }}>
      <style>{`.word-sheet p.word-li::before{content:attr(data-label) "\\00a0\\00a0";} .word-sheet [contenteditable="true"]:focus-within{background:#fafafa}`}</style>
      <Blocks blocks={data.blocks} editable={data.editable} />
    </div>
  );
});

/** Vùng sửa bị xoá sạch ⇒ trả lại một đoạn trống để còn chỗ gõ. */
function keepParagraph(target: EventTarget) {
  const seg = target instanceof Element ? target.closest<HTMLElement>("[data-seg]") : null;
  if (seg && !seg.querySelector("p, div")) {
    const p = document.createElement("p");
    p.appendChild(document.createElement("br"));
    while (seg.firstChild) p.insertBefore(seg.firstChild, p.firstChild);
    seg.appendChild(p);
  }
}

// ── Đọc lại trang giấy sau khi người sửa ──

type Inherit = { pid: string; src: number; b: boolean; i: boolean; u: boolean };

function inherit(el: HTMLElement, from: Inherit): Inherit {
  const next = { ...from };
  const tag = el.tagName;
  if (tag === "B" || tag === "STRONG") next.b = true;
  if (tag === "I" || tag === "EM") next.i = true;
  if (tag === "U") next.u = true;
  const st = el.style;
  if (st.fontWeight) next.b = st.fontWeight === "bold" || Number(st.fontWeight) >= 600;
  if (st.fontStyle) next.i = st.fontStyle === "italic" || st.fontStyle === "oblique";
  const deco = st.textDecorationLine || st.textDecoration;
  if (deco) next.u = deco.includes("underline");
  if (el.dataset.src !== undefined && el.dataset.pid !== undefined) {
    next.pid = el.dataset.pid;
    next.src = Number.parseInt(el.dataset.src, 10);
    if (!Number.isFinite(next.src)) next.src = -1;
  }
  return next;
}

/** Màu CSS (`rgb(…)` / `#rrggbb`) ⇒ sáu chữ số hex viết hoa; không đọc được ⇒ rỗng. */
function hexColor(css: string): string {
  const m = css.match(/^rgba?\((\d+),\s*(\d+),\s*(\d+)/);
  if (m) return [m[1], m[2], m[3]].map((n) => Number(n).toString(16).padStart(2, "0")).join("").toUpperCase();
  return /^#[0-9a-fA-F]{6}$/.test(css) ? css.slice(1).toUpperCase() : "";
}

function readPara(el: HTMLElement): EditedRun[] {
  const runs: EditedRun[] = [];
  let endsWithBr = false;
  const walk = (node: Node, from: Inherit) => {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = (node.textContent ?? "").replace(/ /g, " ").replace(/\r/g, "");
      if (text) {
        // Cỡ / màu / phông = thứ ĐANG HIỂN THỊ của chữ: trình duyệt hay bỏ hẳn thuộc tính khi người chọn trùng giá trị kế thừa (vd chọn đen trên trang
        // chữ đen ⇒ không còn ghi màu) — đọc kiểu đã tính mới biết. Chỉ số trên / dưới vẽ nhỏ đi nên không đọc cỡ.
        const cs = node.parentElement ? getComputedStyle(node.parentElement) : null;
        const sz = cs && cs.verticalAlign !== "super" && cs.verticalAlign !== "sub" ? Math.round(Number.parseFloat(cs.fontSize) * 0.75 * 2) / 2 : 0;
        const font = cs ? cs.fontFamily.split(",")[0].trim().replace(/^["']|["']$/g, "") : "";
        runs.push({ pid: from.pid, src: from.src, text, b: from.b, i: from.i, u: from.u, sz: sz > 0 ? sz : null, color: (cs && hexColor(cs.color)) || null, font: font || null });
        endsWithBr = false;
      }
      return;
    }
    if (!(node instanceof HTMLElement)) return;
    if (node.tagName === "BR") {
      runs.push({ pid: from.pid, src: from.src, text: "\n", b: from.b, i: from.i, u: from.u });
      endsWithBr = true;
      return;
    }
    if (node.tagName === "IMG") return;
    const next = inherit(node, from);
    node.childNodes.forEach((c) => walk(c, next));
  };
  walk(el, { pid: "", src: -1, b: false, i: false, u: false });
  if (endsWithBr) runs.pop();   // <br> cuối đoạn là chỗ giữ dòng của trình duyệt, không phải xuống dòng người gõ
  return runs;
}

const ALIGNS: WordAlign[] = ["left", "center", "right", "justify"];

/** Mọi vùng sửa của trang giấy ⇒ dãy đoạn như người dùng đang thấy. */
function readSegments(sheet: HTMLElement): Segment[] {
  const out: Segment[] = [];
  sheet.querySelectorAll<HTMLElement>("[data-seg]").forEach((seg) => {
    const paras: EditedPara[] = [];
    seg.childNodes.forEach((n) => {
      if (n instanceof HTMLElement) {
        const num = (v: string | undefined) => (v !== undefined && Number.isFinite(Number(v)) ? Number(v) : null);
        paras.push({
          id: n.dataset.id ?? null, runs: readPara(n), align: ALIGNS.includes(n.dataset.align as WordAlign) ? (n.dataset.align as WordAlign) : null,
          line: num(n.dataset.line), indent: num(n.dataset.indent),
        });
      }
      else if (n.nodeType === Node.TEXT_NODE && (n.textContent ?? "").trim()) {
        paras.push({ id: null, runs: [{ pid: "", src: -1, text: n.textContent ?? "", b: false, i: false, u: false }] });
      }
    });
    out.push({ ids: (seg.dataset.seg ?? "").split(",").filter(Boolean), paras });
  });
  return out;
}

