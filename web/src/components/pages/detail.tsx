"use client";

import { FileDown, FileText, FileUp, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { KbIntakeDialog, PdfDialog } from "@/components/blocks/lazy";
import { isInlinePdf, linkUrl } from "@/lib/links";
import { pipeTable } from "@/lib/markdown";
import { api } from "@/lib/api";
import { saveBlob } from "@/lib/download";
import * as fmt from "@/lib/format";
import type { ActionSpec, DetailField, DetailLink, DetailOriginal, DetailPage, DetailSection, SectionTable, Sheet } from "@/lib/types";
import { actionClass, CopyButton } from "@/components/primitives";
import { Markdown } from "@/components/markdown";
import { useDesk } from "@/components/desk/desk-context";
import { cn } from "@/lib/utils";

/** Hồ sơ một bản ghi: nhãn – giá trị (bỏ ô trống), đoạn chữ trong khung; style "document" vẽ như văn bản liền mạch (tài liệu kho). */
export function DetailView({ page, onClose, onChanged }: { page: DetailPage; onClose?: () => void; onChanged?: () => void }) {
  const desk = useDesk();
  const [pdf, setPdf] = useState<DetailLink | null>(null);
  const [replacing, setReplacing] = useState(false);
  // "Thay bằng bản mới" (kb.add_document + replaces_document_id) ⇒ cho tải luôn FILE bản mới (PDF / Word), không chỉ dán chữ
  const replaceAction = page.actions.find((a) => a.command === "kb.add_document" && typeof a.preset.replaces_document_id === "string");
  const replaces = replaceAction ? { id: String(replaceAction.preset.replaces_document_id), title: page.title } : undefined;
  const isDoc = page.style === "document";
  const documentText = page.sections.filter((s) => s.text !== null).map((s) => `## ${s.title}\n${s.text}`).join("\n\n");
  // 0102: có bản gốc ⇒ hai góc nhìn. Chữ bóc / OCR ("extracted") là bản phụ ⇒ mặc định mở BẢN GỐC; giấy tờ tự soạn ⇒ mặc định NỘI DUNG, thêm "Bản in"
  const extracted = page.textRole === "extracted";
  const [view, setView] = useState<"original" | "content">(page.original && extracted ? "original" : "content");
  const sections = page.sections.map((s, i) => <Record key={`${s.title}-${i}`} s={s} onChanged={onChanged} />);
  // 0251: giấy lập từ hồ sơ ⇒ CHÍNH TỜ GIẤY là nội dung (ô thiếu chạm điền ngay trên giấy); các mục (Thuộc, còn thiếu, nguồn số) đi kèm dưới / trên
  const content = isDoc ? <Markdown text={documentText} /> : page.sheet ? <>{sections.slice(0, 1)}<SheetView sheet={page.sheet} onChanged={onChanged} />{sections.slice(1)}</> : sections;

  return (
    <div className={cn("flex flex-col gap-4", isDoc && "max-w-[720px]")}>
      {page.subtitle ? <p className="t14 select-text text-muted-foreground">{page.subtitle}</p> : null}
      {page.ask || page.actions.length || page.links.length || isDoc ? (
        <div className="flex flex-wrap items-center gap-2">
          {page.ask ? (
            <button type="button" className={actionClass("primary")} onClick={() => { desk.ask(page.ask!.prompt); onClose?.(); }}>
              <Sparkles className="size-3.5" aria-hidden />
              {page.ask.title}
            </button>
          ) : null}
          {page.actions.filter((a) => a !== replaceAction && a.style !== "destructive").map((a) => (
            <button key={a.command + a.title} type="button" className={actionClass(a.style)} onClick={() => desk.openForm(a, { onDone: onChanged })}>
              {a.title}
            </button>
          ))}
          {replaces ? (
            // 0225: bản mới = thả file (cửa chính); dán chữ chỉ là lối phụ nhỏ
            <>
              <button type="button" className={actionClass("default")} onClick={() => setReplacing(true)}>
                <FileUp className="size-3.5" aria-hidden />
                Thay bằng file bản mới
              </button>
              <button type="button" className="t12 text-muted-foreground underline-offset-2 hover:text-foreground hover:underline" onClick={() => desk.openForm(replaceAction!, { onDone: onChanged })}>
                Dán chữ
              </button>
            </>
          ) : null}
          {page.links.map((l) => (
            <button
              key={l.path}
              type="button"
              className={actionClass("default")}
              onClick={async () => {
                // PDF (bản gốc tài liệu kho, 0052) ⇒ xem ngay trong trình duyệt, đúng trang; file khác ⇒ tải về như cũ
                if (isInlinePdf(l)) return setPdf(l);
                try {
                  saveBlob(await api.download(l.path), l.filename ?? "tai-lieu");
                  desk.show("Đã tải xuống");
                } catch (e) {
                  desk.show(e instanceof Error ? e.message : "Không tải được", true);
                }
              }}
            >
              {isInlinePdf(l) ? <FileText className="size-3.5" aria-hidden /> : <FileDown className="size-3.5" aria-hidden />}
              {l.title}
            </button>
          ))}
          {isDoc ? <CopyButton text={`${page.title}\n\n${documentText}`} label="Sao chép cả tài liệu" /> : null}
          {/* nút huỷ / hết hiệu lực đứng CUỐI hàng, sau Tải — nút chính luôn là nút đầu tiên người thấy */}
          {page.actions.filter((a) => a !== replaceAction && a.style === "destructive").map((a) => (
            <button key={a.command + a.title} type="button" className={actionClass(a.style)} onClick={() => desk.openForm(a, { onDone: onChanged })}>
              {a.title}
            </button>
          ))}
        </div>
      ) : null}
      {page.original ? (
        <div className="flex w-fit gap-0.5 rounded-lg bg-surface2 p-0.5" role="tablist" aria-label="Góc nhìn tài liệu">
          {(extracted
            ? [["original", page.original.title], ["content", "Văn bản trích"]]
            : [["content", page.sheet ? "Tờ giấy" : "Nội dung"], ["original", page.original.title]]).map(([k, label]) => (
            <button key={k} type="button" role="tab" aria-selected={view === k} onClick={() => setView(k as "original" | "content")}
                    className={cn("t12m rounded-md px-3 py-1", view === k ? "bg-surface shadow-sm" : "text-muted-foreground hover:text-foreground")}>
              {label}
            </button>
          ))}
        </div>
      ) : null}
      {page.original && view === "original" ? <OriginalViewer original={page.original} /> : null}
      {!page.original || view === "content" ? (
        <>
          {extracted ? (
            <p className="t12 text-muted-foreground">Chữ bóc từ bản gốc — dùng để tìm kiếm và để trợ lý trích dẫn; bản đúng là bản gốc.</p>
          ) : null}
          {content}
        </>
      ) : null}
      {pdf ? <PdfDialog title={pdf.title} url={linkUrl(pdf)} filename={pdf.filename} onClose={() => setPdf(null)} /> : null}
      {replacing && replaces ? (
        <KbIntakeDialog
          replaces={replaces}
          onOpenChange={setReplacing}
          onOpenSimilar={(id, title) => desk.openPage("kb.document", { document_id: id }, title)}
          onDone={(m) => {
            desk.show(m);
            onChanged?.();
          }}
        />
      ) : null}
    </div>
  );
}

/** Bản gốc nhúng ngay trên trang (PDF qua BFF — Word / Excel đã đổi ở máy chủ). Tải trước để biết lỗi: máy chủ chưa xem trước được (409 —
 *  chưa cài LibreOffice…) ⇒ hiện lý do (nút tải ở hàng liên kết), không để khung trống / trang lỗi trong khung. */
function OriginalViewer({ original }: { original: DetailOriginal }) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    let made: string | null = null;
    api
      .download(original.path)
      .then((b) => {
        if (!alive) return;
        made = URL.createObjectURL(b.type ? b : new Blob([b], { type: "application/pdf" }));
        setUrl(made);
      })
      .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : "Chưa xem trước được"));
    return () => {
      alive = false;
      if (made) URL.revokeObjectURL(made);
    };
  }, [original.path]);
  if (error) {
    // không tự ghép đường tải ở app (app chỉ vẽ) — nút tải bản gốc / .docx nằm ở hàng liên kết phía trên, do server gửi
    return (
      <div className="t14 flex flex-col gap-1 rounded-lg bg-surface2 p-4">
        <p className="select-text">{error}</p>
        <p className="t12 text-muted-foreground">Dùng nút tải ở trên để mở file trên máy.</p>
      </div>
    );
  }
  if (!url) return <div className="h-[70vh] w-full animate-pulse rounded-lg bg-surface2" aria-label="Đang mở bản gốc" />;
  return (
    <iframe src={`${url}${original.page ? `#page=${original.page}` : ""}`} title={original.title}
            className="h-[75vh] w-full rounded-lg border border-border bg-surface2" />
  );
}

function Record({ s, onChanged }: { s: DetailSection; onChanged?: () => void }) {
  // ô trống bỏ — trừ ô bấm được (ô thiếu chạm để điền: chỗ trống là chính thứ cần thấy)
  const fields = s.fields.filter((f) => fmt.cell(f.value, f.format).trim() !== "" || f.action);
  return (
    <section className="flex flex-col gap-2">
      {s.title || s.text ? (
        <div className="flex items-center justify-between">
          <h3 className="t16">{s.title}</h3>
          {s.text ? <CopyButton text={s.text} label="Sao chép" /> : null}
        </div>
      ) : null}
      {fields.length ? (
        <dl className="grid grid-cols-[minmax(0,max-content)_1fr] gap-x-6 gap-y-1.5">
          {fields.map((f) => (
            <div key={f.label} className="contents">
              <dt className="t14 text-muted-foreground">{f.label}</dt>
              <dd className="t14m tnum select-text"><FieldValue f={f} onChanged={onChanged} /></dd>
            </div>
          ))}
        </dl>
      ) : null}
      {s.text ? <SectionText text={s.text} /> : null}
      {s.table ? <TableBlock table={s.table} /> : null}
    </section>
  );
}

/** Giá trị một ô; ô bấm được (0251): `action` ⇒ mở form của đúng ô (ô trống hiện `placeholder` nổi, chạm để điền), `open` ⇒ mở trang (vd hồ sơ). */
function FieldValue({ f, onChanged }: { f: DetailField; onChanged?: () => void }) {
  const desk = useDesk();
  const text = fmt.cell(f.value, f.format);
  if (f.action) {
    const a = f.action;
    return (
      <button type="button" onClick={() => desk.openForm(a, { onDone: onChanged })}
              className={cn("rounded px-1.5 py-0.5 text-left", text ? "underline decoration-dotted underline-offset-4 hover:bg-surface2" : "bg-amber-400/20 text-amber-700 hover:bg-amber-400/30 dark:text-amber-300")}>
        {text || f.placeholder || a.title}
      </button>
    );
  }
  if (f.open) {
    const o = f.open;
    return (
      <button type="button" onClick={() => desk.openPage(o.query, o.input, o.title ?? f.label)} className="text-left text-primary underline-offset-4 hover:underline">
        {text}
      </button>
    );
  }
  return <>{text}</>;
}

const LAYER: Record<string, string> = {
  record: "border-b border-sky-500/60 bg-sky-500/10",
  company: "border-b border-dashed border-neutral-400",
  missing: "rounded bg-amber-300/60 px-1 text-amber-900",   // tờ giấy luôn nền sáng (cả giao diện tối) ⇒ màu chữ cố định, không theo dark
  person: "border-b border-violet-500/60 bg-violet-500/10",
  ai: "rounded bg-fuchsia-500/10 ring-1 ring-fuchsia-500/30",   // đoạn tự do agent / người lập viết (0251 lát 2) — sửa thoải mái
};

/** CHÍNH TỜ GIẤY (0251): nền giấy, chữ chia mẩu theo lớp — số từ hồ sơ (khoá, sửa ở hồ sơ) · ô thiếu (chạm điền) · người nhập. Server quyết hết; ô thiếu
 *  chỉ bấm được khi server gửi nút điền của ô đó (`sheet.actions`, khoá = `preset.key`). */
function SheetView({ sheet, onChanged }: { sheet: Sheet; onChanged?: () => void }) {
  const desk = useDesk();
  const fills = new Map<string, ActionSpec>(sheet.actions.map((a) => [String(a.preset.key ?? ""), a]));
  const compose = sheet.compose;
  const { setWorking } = desk;
  // "Soạn với agent" (0251 lát 2): tờ giấy đang lập ⇒ khung chat bên cạnh biết đang lập gì (dòng "Về", chip); rời tờ giấy ⇒ thôi
  const composeKey = compose ? JSON.stringify(compose) : "";
  useEffect(() => {
    if (!compose) return;
    setWorking(compose);
    return () => setWorking(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- theo nội dung (server gửi bản mới mỗi lần tải lại), không theo tham chiếu
  }, [composeKey, setWorking]);
  const runs = (b: Sheet["blocks"][number]) =>
    (b.runs ?? [{ t: b.text }]).map((r, i) => {
      if (!r.k) return <span key={i}>{r.t}</span>;
      const fill = r.layer === "missing" ? fills.get(r.k) : undefined;
      if (fill) {
        return (
          <button key={i} type="button" className={cn(LAYER.missing, "hover:bg-amber-400/50")} onClick={() => desk.openForm(fill, { onDone: onChanged })}>
            chạm để điền
          </button>
        );
      }
      const open = sheet.open;
      if (r.layer === "record" && open) {
        return (
          <button key={i} type="button" title="Số từ hồ sơ — chạm để mở hồ sơ và sửa ở đó" className={cn(LAYER.record, "hover:bg-sky-500/20")}
                  onClick={() => desk.openPage(open.query, open.input, open.title ?? "")}>
            {r.t}
          </button>
        );
      }
      return <span key={i} className={LAYER[r.layer ?? "record"]} title={r.layer === "company" ? "Thông tin công ty — sửa ở Quản trị" : undefined}>{r.t}</span>;
    });
  return (
    <div className="flex flex-col gap-2">
      {compose ? (
        <div className="mx-auto flex w-full max-w-[720px] items-center gap-2">
          {/* lối vào rõ ràng: cột trái chuyển sang chat trợ lý (dòng "Về" + chip của tờ giấy này hiện ở ô chat) */}
          <button type="button" onClick={() => desk.ask("")} className={cn(actionClass("primary"), "h-8 px-3")}>
            <Sparkles className="size-3.5" aria-hidden /> Soạn với agent
          </button>
          <span className="t12 flex-1 text-right text-muted-foreground">{compose.status}</span>
        </div>
      ) : null}
      <article className="t14 mx-auto flex w-full max-w-[720px] flex-col gap-2 rounded-lg border border-border bg-[#fbfaf6] p-8 text-neutral-900 select-text">
        {sheet.draftMark ? <p className="t12m self-end text-neutral-500">{sheet.draftMark}</p> : null}
        <h2 className="t16 text-center">{sheet.title}</h2>
        <p className="t12 text-center text-neutral-500">{sheet.number}</p>
        {sheet.blocks.map((b, i) =>
          b.slot ? <SlotParagraph key={`${b.slot}-${i}`} block={b} write={compose?.write} onChanged={onChanged} />
          : b.type === "heading" ? <h3 key={i} className="t14m mt-2">{runs(b)}</h3>
          : b.type === "table" && b.columns ? <PaperTable key={i} block={b} />
          : <p key={i} className={cn("leading-relaxed", b.type === "bullet" && "pl-4")}>{b.type === "bullet" ? "– " : null}{runs(b)}</p>)}
        {sheet.notes.map((n) => <p key={n} className="t12 text-neutral-500">{n}</p>)}
        {sheet.signers.length ? (
          <div className="mt-6 grid gap-4" style={{ gridTemplateColumns: `repeat(${sheet.signers.length}, minmax(0, 1fr))` }}>
            {sheet.signers.map((s) => <p key={s} className="t14m text-center">{s}</p>)}
          </div>
        ) : null}
      </article>
      {sheet.legend.length ? (
        <div className="t12 flex flex-wrap justify-center gap-4 text-muted-foreground">
          {sheet.legend.map((l) => <span key={l.layer} className="flex items-center gap-1.5"><span className={cn("inline-block size-3", LAYER[l.layer])} />{l.label}</span>)}
        </div>
      ) : null}
    </div>
  );
}

/** ĐOẠN TỰ DO trên tờ giấy (0251 lát 2): chưa viết ⇒ chỗ trống có gợi ý; đã viết ⇒ đoạn tô lớp "agent viết". Bấm ⇒ sửa tay ngay tại chỗ (lệnh
 *  `papers.write_paragraph` server gửi ở `compose.write`) hoặc "Bảo agent sửa" (đưa câu mở đầu vào ô chat bên cạnh). Không có nút ghi ⇒ chỉ đọc. */
function SlotParagraph({ block, write, onChanged }: { block: Sheet["blocks"][number]; write?: ActionSpec; onChanged?: () => void }) {
  const desk = useDesk();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(block.text);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const what = block.hint || "Đoạn tự do";
  const askAgent = () =>
    desk.ask(block.empty ? `Viết đoạn “${what}” trên giấy này (đoạn ${block.slot}): ` : `Sửa đoạn “${what}” trên giấy này (đoạn ${block.slot}): `);
  const save = async () => {
    if (!write) return;
    setBusy(true);
    setError("");
    try {
      const r = await api.command(write.command, { ...write.preset, key: block.slot ?? "", text }, crypto.randomUUID());
      if (r.status === "ok") {
        setEditing(false);
        onChanged?.();
        void desk.refreshInbox();
      } else setError(r.error || r.status);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không lưu được");
    } finally {
      setBusy(false);
    }
  };
  if (editing) {
    return (
      <div className="flex flex-col gap-1.5 rounded-md bg-fuchsia-500/5 p-2 ring-1 ring-fuchsia-500/40">
        <label className="t12m text-neutral-600" htmlFor={`slot-${block.slot}`}>{what}</label>
        <textarea id={`slot-${block.slot}`} value={text} onChange={(e) => setText(e.target.value)} rows={4} maxLength={2000} autoFocus
                  className="t14 w-full resize-y rounded-md border border-neutral-300 bg-white p-2 leading-relaxed text-neutral-900 outline-none focus-visible:ring-2 focus-visible:ring-fuchsia-500/50" />
        {error ? <p className="t12 text-red-700" role="alert">{error}</p> : null}
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" disabled={busy} onClick={() => void save()} className={cn(actionClass("primary"), "h-8 px-3")}>{busy ? "Đang lưu…" : "Lưu đoạn"}</button>
          <button type="button" disabled={busy} onClick={() => { setEditing(false); askAgent(); }} className={cn(actionClass("default"), "h-8 px-3")}>
            <Sparkles className="size-3.5" aria-hidden /> Bảo agent sửa
          </button>
          <button type="button" disabled={busy} onClick={() => { setText(block.text); setEditing(false); setError(""); }} className="t12m h-8 rounded-md px-2 text-neutral-500 hover:bg-neutral-200/60">
            Huỷ
          </button>
        </div>
      </div>
    );
  }
  if (!write) return block.empty ? null : <p className={cn("leading-relaxed", LAYER.ai, "px-1")}>{block.text}</p>;
  return block.empty ? (
    <div className="flex flex-wrap items-center gap-2 rounded-md border border-dashed border-fuchsia-500/50 px-2 py-1.5">
      <span className="t12 flex-1 text-neutral-500">{what} — chưa viết</span>
      <button type="button" onClick={askAgent} className="t12m inline-flex items-center gap-1 rounded-md px-2 py-1 text-fuchsia-800 outline-none hover:bg-fuchsia-500/10 focus-visible:ring-2 focus-visible:ring-fuchsia-500/50">
        <Sparkles className="size-3.5" aria-hidden /> Nhờ agent viết
      </button>
      <button type="button" onClick={() => setEditing(true)} className="t12m rounded-md px-2 py-1 text-neutral-600 outline-none hover:bg-neutral-200/60 focus-visible:ring-2 focus-visible:ring-fuchsia-500/50">
        Tự viết
      </button>
    </div>
  ) : (
    <button type="button" onClick={() => { setText(block.text); setEditing(true); }} title="Đoạn tự do — bấm để sửa, hoặc bảo agent sửa"
            className={cn("t14 w-full cursor-text px-1 text-left leading-relaxed outline-none hover:bg-fuchsia-500/15 focus-visible:ring-2 focus-visible:ring-fuchsia-500/50", LAYER.ai)}>
      {block.text}
    </button>
  );
}

/** Bảng trên tờ giấy: nền giấy sáng ở cả giao diện tối (không dùng màu bề mặt của app), cột số canh phải theo ``align`` server gửi, hàng tổng đậm. */
function PaperTable({ block }: { block: Sheet["blocks"][number] }) {
  const align = (j: number) => ({ r: "text-right", c: "text-center" } as Record<string, string>)[block.align?.[j] ?? "l"] ?? "text-left";
  const total = block.total ?? [];
  const span = (block.columns?.length ?? 0) - Math.max(total.length - 1, 0);
  return (
    <table className="t14 my-1 w-full border-collapse border border-neutral-300">
      <thead className="bg-neutral-100">
        <tr>{block.columns?.map((c, j) => <th key={j} scope="col" className={cn("t12m border border-neutral-300 px-2 py-1 text-neutral-600", align(j))}>{c}</th>)}</tr>
      </thead>
      <tbody>
        {(block.rows ?? []).map((r, i) => (
          <tr key={i}>{r.map((c, j) => <td key={j} className={cn("tnum border border-neutral-300 px-2 py-1", align(j))}>{c}</td>)}</tr>
        ))}
        {total.length ? (
          <tr className="font-medium">
            <td colSpan={span} className="border border-neutral-300 px-2 py-1 text-center">{total[0]}</td>
            {total.slice(1).map((c, j) => <td key={j} className="tnum border border-neutral-300 px-2 py-1 text-right">{c}</td>)}
          </tr>
        ) : null}
      </tbody>
    </table>
  );
}

/** Bảng thật của mục (0102 — vd bảng phí công văn): hàng tổng cộng đậm. */
function TableBlock({ table }: { table: SectionTable }) {
  return <Grid rows={[table.columns, ...table.rows, ...(table.total.length ? [table.total] : [])]} />;
}

/** Đoạn chữ của mục; dạng "a | b | c" mỗi dòng (vd lịch thanh toán của bản tư vấn căn) ⇒ bảng, hàng đầu là tiêu đề, cột số canh phải. */
function SectionText({ text }: { text: string }) {
  const rows = pipeTable(text);
  if (!rows) return <p className="t14 select-text whitespace-pre-wrap rounded-lg bg-surface2 p-3">{text}</p>;
  return <Grid rows={rows} />;
}

function Grid({ rows }: { rows: string[][] }) {
  const [head, ...body] = rows;
  const numeric = (c: string) => /^[\d.,]+%?$/.test(c.replace(/\s/g, ""));
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="t14 w-full border-collapse select-text">
        <thead className="bg-surface2">
          <tr>
            {head.map((c, i) => (
              <th key={i} scope="col" className="t12m px-3 py-2 text-left text-muted-foreground">{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((r, ri) => (
            <tr key={ri} className="border-t border-border">
              {r.map((c, ci) => (
                <td key={ci} className={cn("px-3 py-1.5 align-top", numeric(c) && "tnum text-right", ri === body.length - 1 && /^(tổng|cộng)/i.test(r[0] ?? "") && "font-medium")}>
                  {c}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
