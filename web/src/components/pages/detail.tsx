"use client";

import { FileDown, FileText, FileUp, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { KbIntakeDialog, PdfDialog } from "@/components/blocks/lazy";
import { isInlinePdf, linkUrl } from "@/lib/links";
import { pipeTable } from "@/lib/markdown";
import { api } from "@/lib/api";
import { saveBlob } from "@/lib/download";
import * as fmt from "@/lib/format";
import type { DetailLink, DetailOriginal, DetailPage, DetailSection, SectionTable } from "@/lib/types";
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
  const content = isDoc ? <Markdown text={documentText} /> : page.sections.map((s, i) => <Record key={`${s.title}-${i}`} s={s} />);

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
          {page.actions.filter((a) => a !== replaceAction).map((a) => (
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
        </div>
      ) : null}
      {page.original ? (
        <div className="flex w-fit gap-0.5 rounded-lg bg-surface2 p-0.5" role="tablist" aria-label="Góc nhìn tài liệu">
          {(extracted
            ? [["original", page.original.title], ["content", "Văn bản trích"]]
            : [["content", "Nội dung"], ["original", page.original.title]]).map(([k, label]) => (
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

function Record({ s }: { s: DetailSection }) {
  const fields = s.fields.filter((f) => fmt.cell(f.value, f.format).trim() !== "");
  return (
    <section className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <h3 className="t16">{s.title}</h3>
        {s.text ? <CopyButton text={s.text} label="Sao chép" /> : null}
      </div>
      {fields.length ? (
        <dl className="grid grid-cols-[minmax(0,max-content)_1fr] gap-x-6 gap-y-1.5">
          {fields.map((f) => (
            <div key={f.label} className="contents">
              <dt className="t14 text-muted-foreground">{f.label}</dt>
              <dd className="t14m tnum select-text">{fmt.cell(f.value, f.format)}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {s.text ? <SectionText text={s.text} /> : null}
      {s.table ? <TableBlock table={s.table} /> : null}
    </section>
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
