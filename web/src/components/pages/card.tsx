"use client";

import { Check, MessagesSquare, Sparkles } from "lucide-react";
import type { ChartData } from "@/lib/chart";
import type { Block } from "@/lib/types";
import { ChartCard, ChartGrid } from "@/components/blocks/chart";
import { SourceLine } from "@/components/pages/source-line";
import { isGridRow, layoutRows, type Placed } from "@/lib/layout";
import { layoutOf } from "@/lib/arrange";
import { DesignProvider } from "./block-panel";
import { DashboardEditor, type Selection } from "./dashboard-editor";
import { LayoutGrid } from "lucide-react";
import { api } from "@/lib/api";
import { formActionFor, movePayload, needsForm } from "@/lib/board";
import { newKey } from "@/lib/idempotency";
import type { BoardCard, BoardColumn, BoardMove, CardPage } from "@/lib/types";
import { actionClass, Spinner } from "@/components/primitives";
import { Markdown } from "@/components/markdown";
import { Board } from "@/components/blocks/board";
import { TimelineBlock } from "@/components/blocks/timeline";
import { CalendarBlock } from "@/components/blocks/calendar";
import { PickerBlock } from "@/components/blocks/picker";
import { Callout, ListBlock, StageList, StepStrip, TilesBlock } from "@/components/blocks/misc";
import { FindingCard } from "@/components/blocks/finding";
import { RecordFilesBlock } from "@/components/blocks/record-files";
import { ThreadBlock } from "@/components/blocks/thread";
import { ComposerBlock } from "@/components/blocks/composer";
import { WordDocBlock } from "@/components/blocks/word-doc";
import { useDesk } from "@/components/desk/desk-context";
import { useChat } from "@/components/desk/chat-context";
import { KpiRow, DataTable } from "./table";
import { useState } from "react";
import { createPortal } from "react-dom";
import { useFrameSub, type FrameHead } from "@/components/desk/frame-context";
import { useLater } from "@/hooks/use-later";
import { cn } from "@/lib/utils";
import { applies, openInput } from "@/lib/form";
import type { ActionSpec, Question, Row, TablePage } from "@/lib/types";
import { EditFrame } from "./edit-block";
import { ResponsiveGrid } from "./responsive-grid";
import { ClampNote } from "@/components/clamp-note";

/** Câu hỏi TRƯỚC khi làm (0098, như Claude Design hỏi lại): chọn đáp án cho từng câu (+ ghi thêm nếu muốn) rồi gửi MỘT tin nhắn — trợ lý
 *  ghép tiếp theo lựa chọn. Gửi rồi thì khoá lại (lượt mới đã chạy). */
function ChoicesBlock({ block, onSend }: { block: { questions: Question[]; submit: string; prefix: string }; onSend: (text: string) => void }) {
  const [picked, setPicked] = useState<Record<string, string[]>>({});
  const [note, setNote] = useState("");
  const [sent, setSent] = useState(false);
  const toggle = (q: Question, o: string) =>
    setPicked((cur) => {
      const now = cur[q.key] ?? [];
      return { ...cur, [q.key]: q.multi ? (now.includes(o) ? now.filter((x) => x !== o) : [...now, o]) : [o] };
    });
  const ready = block.questions.every((q) => (picked[q.key] ?? []).length > 0);
  const send = () => {
    const lines = block.questions.map((q) => `- ${q.question} ${(picked[q.key] ?? []).join(", ")}`);
    onSend([block.prefix || "Trả lời:", ...lines, ...(note.trim() ? [`- Ghi thêm: ${note.trim()}`] : [])].join("\n"));
    setSent(true);
  };
  return (
    <div className="flex flex-col gap-4">
      {block.questions.map((q, qi) => (
        <fieldset key={q.key} className="flex flex-col gap-2" disabled={sent}>
          <legend className="t14m mb-2">
            <span className="t12m mr-2 text-muted-foreground tnum">{qi + 1}/{block.questions.length}</span>
            {q.question}
            {q.multi ? <span className="t12 font-normal text-muted-foreground"> · chọn được nhiều</span> : null}
          </legend>
          <div className="flex flex-col gap-1.5" role={q.multi ? "group" : "radiogroup"} aria-label={q.question}>
            {q.options.map((o) => {
              const on = (picked[q.key] ?? []).includes(o);
              return (
                // thẻ lựa chọn (như hộp hỏi của Claude): cả hàng bấm được, dấu chọn đầu hàng — tròn: chọn một · vuông: chọn nhiều
                <button key={o} type="button" role={q.multi ? "checkbox" : "radio"} aria-checked={on} onClick={() => toggle(q, o)}
                        className={cn("flex w-full items-start gap-2.5 rounded-xl border px-3 py-2.5 text-left outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60",
                                      on ? "border-primary bg-primary/8" : "border-border bg-surface hover:bg-foreground/[0.04]")}>
                  <span className={cn("mt-[2px] grid size-4 shrink-0 place-items-center border", q.multi ? "rounded-[5px]" : "rounded-full",
                                      on ? "border-primary bg-primary text-primary-foreground" : "border-muted-foreground/40")} aria-hidden>
                    {on ? <Check className="size-3" strokeWidth={3} /> : null}
                  </span>
                  <span className="t14 min-w-0 flex-1">{o}</span>
                </button>
              );
            })}
          </div>
        </fieldset>
      ))}
      <input value={note} onChange={(e) => setNote(e.target.value)} disabled={sent} placeholder="Ghi thêm (không bắt buộc)" aria-label="Ghi thêm"
             className="t14 h-10 rounded-xl border border-border bg-surface px-3 outline-none focus-visible:ring-2 focus-visible:ring-ring" />
      <div className="flex items-center gap-2.5">
        <button type="button" disabled={!ready || sent} onClick={send} className={cn(actionClass("primary"), "h-9 px-4 disabled:opacity-50")}>{sent ? "Đã gửi" : block.submit}</button>
        {!ready && !sent ? <span className="t12 text-muted-foreground">Chọn mỗi câu ít nhất một ý</span> : null}
      </div>
    </div>
  );
}

/** Bảng trong thẻ. Có `row_actions` / `open` (app tự ghép — 0098) ⇒ chọn dòng như trên màn: nút của dòng (server đã lọc theo quyền), mở
 *  chi tiết. Không có ⇒ chỉ xem như trước. */
function TableBlock({ page, onChanged, fill = false }: { page: TablePage; onChanged?: () => void; fill?: boolean }) {
  const desk = useDesk();
  const [selected, setSelected] = useState<string | null>(null);
  const actions = page.row_actions ?? [];
  const interactive = actions.length > 0 || !!page.open;
  const row = interactive ? (page.rows.find((r) => r.id === selected) ?? null) : null;
  const open = page.open ? (r: Row) => desk.openPage(page.open!.query, openInput(page.open!.bind, r), page.open!.title) : undefined;
  return (
    <div className={cn("flex flex-col gap-2", fill && "min-h-[260px] flex-1")}>
      {page.note ? <ClampNote text={page.note} /> : null}
      {/* trong ô lưới (fill): hộp cuộn KHÔNG góp chiều cao cho hàng (absolute) ⇒ hàng cao bằng ô còn lại, bảng cuộn bên trong */}
      <div className={cn(fill ? "relative min-h-0 flex-1" : "max-h-[420px] overflow-auto rounded-xl border border-hairline")}>
        <div className={cn(fill && "absolute inset-0 overflow-auto rounded-xl border border-hairline")}>
          {page.rows.length ? (
            interactive ? <DataTable page={page} selected={selected} onSelect={setSelected} onOpen={open} /> : <DataTable page={page} />
          ) : (
            <p className="t14 p-4 text-muted-foreground">{page.empty}</p>
          )}
        </div>
      </div>
      {row ? (
        <div className="flex flex-wrap items-center gap-2 rounded-xl bg-surface2 px-3 py-2" role="toolbar" aria-label="Thao tác cho dòng đang chọn">
          <span className="t12 text-muted-foreground">Dòng đang chọn:</span>
          {actions.filter((a) => applies(a, row)).map((a) => (
            <button key={a.command + a.title} type="button" className={cn(actionClass(a.style), "h-7 text-[13px]")} onClick={() => desk.openForm(a, { row, onDone: onChanged })}>{a.title}</button>
          ))}
          {open ? <button type="button" className={cn(actionClass("default"), "h-7 text-[13px]")} onClick={() => open(row)}>{page.open?.title || "Mở"}</button> : null}
          {actions.every((a) => !applies(a, row)) && !open ? <span className="t12 text-muted-foreground/70">Không có thao tác cho dòng này</span> : null}
        </div>
      ) : null}
    </div>
  );
}

type Segment = { i: number; block: Block; charts?: (ChartData & { edit?: ActionSpec })[]; row?: Placed[]; solo?: ChartData & { edit?: ActionSpec } };

/** Khối có `w` (0166) nằm cùng hàng trên lưới 12 cột; khối biểu đồ liền nhau KHÔNG có `w` gộp thành một lưới 1–2 cột; khối khác giữ nguyên chỗ. */
function segments(blocks: Block[]): Segment[] {
  const out: Segment[] = [];
  for (const r of layoutRows(blocks)) {
    if (isGridRow(r)) {
      out.push({ i: r.cells[0].index, block: r.cells[0].block, row: r.cells });
      continue;
    }
    const { index, block: b } = r.cells[0];
    const last = out[out.length - 1];
    if (b.type === "chart") {
      // biểu đồ đứng một mình trong hàng (có `w` mà không có bạn cùng hàng) vẫn phải được vẽ; chỉ biểu đồ KHÔNG có `w` mới gộp với biểu đồ liền trước
      if (b.w === undefined && last?.charts && last.block.w === undefined) last.charts.push(b);
      else if (b.w !== undefined) out.push({ i: index, block: b, solo: b });   // có `w` mà đứng một mình: chiếm trọn hàng (w=12) — không bị lưới 2 cột bó nửa
      else out.push({ i: index, block: b, charts: [b] });
    } else out.push({ i: index, block: b });
  }
  return out;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      {title ? <h3 className="t16">{title}</h3> : null}
      {children}
    </section>
  );
}

/** Thẻ khối (kind "card"): server ghép trang từ khối có kiểu, web chỉ vẽ. Khối lạ ⇒ bỏ qua. `onChanged`: trang chứa thẻ tải lại sau thao tác. */
/** `head`: đầu thẻ của khung làm việc (màn ngoài cùng trao) — có thì phụ đề thành phụ đề đầu thẻ, nút hỏi trợ lý lên hàng tên thẻ (nhỏ, phụ). */
export function CardView({ page: raw, onClose, onChanged, head }: { page: CardPage; onClose?: () => void; onChanged?: () => void; head?: FrameHead | null }) {
  const page = useLater(raw);   // khối tải sau: phần nhanh vẽ ngay, phần nặng thay vào khi về (theo thứ tự khối)
  const desk = useDesk();
  const chat = useChat();
  const tools = head?.tools ?? null;
  useFrameSub(tools ? head : null, page.subtitle);

  async function move(move: BoardMove, columns: BoardColumn[], card: BoardCard, column: string): Promise<boolean> {
    if (needsForm(move, column)) {
      desk.openForm(formActionFor(move, card, columns.find((c) => c.key === column)), { onDone: onChanged });
      return false;
    }
    try {
      const r = await api.command(move.command, movePayload(move, card.id, column), newKey());
      if (r.status !== "ok") {
        desk.show(r.error || `Không đổi được (${r.status})`, true);
        return false;
      }
      onChanged?.();
      return true;
    } catch (e) {
      desk.show(e instanceof Error ? e.message : "Không đổi được", true);
      return false;
    }
  }

  // Soạn với AI (thử nghiệm): thẻ có khối composer ⇒ chỉ vẽ khối đó (các khối còn lại là bản chữ dự phòng cho app chưa vẽ được composer).
  // Khối tự mang tiêu đề / lịch sử / trạng thái và tự tải lại bản đang mở (đổi bản soạn ngay trong khối) — không vẽ phụ đề của thẻ.
  const composer = page.blocks.find((b) => b.type === "composer");
  const [editing, setEditing] = useState<{ selected: Selection } | null>(null);   // chế độ "Sửa dashboard" (0220 lát 3b)
  if (composer?.type === "composer") return <ComposerBlock block={composer} onChanged={onChanged} />;
  // Văn bản Word (0227): thẻ có khối word ⇒ chỉ vẽ khối đó (khối chữ còn lại là bản dự phòng cho app chưa vẽ được trình sửa)
  const word = page.blocks.find((b) => b.type === "word");
  if (word?.type === "word") return <WordDocBlock block={word} />;
  // Lịch (0240): khối tự vẽ thanh điều hướng + lưới giờ — chiếm trọn thẻ
  const calendar = page.blocks.find((b) => b.type === "calendar");
  if (calendar?.type === "calendar") return <CalendarBlock block={calendar} onChanged={onChanged} />;
  // dashboard của app tự ghép (0175): mọi khối thuộc app ⇒ có nút "Sắp xếp" — kéo thả thứ tự / bề rộng rồi lưu bằng apps.layout
  const arrange = onChanged ? layoutOf(page.blocks) : null;
  if (editing && arrange)
    return (
      <DashboardEditor key={JSON.stringify(arrange.tiles)} page={arrange.page} layout={arrange} blocks={page.blocks} selected={editing.selected}
                       onSelect={(sel) => setEditing({ selected: sel })} onReload={() => onChanged?.()} onExit={() => setEditing(null)}
                       render={(b) => (b.type === "chart" ? <ChartCard block={b} /> : blockView(0, b, true))} />
    );

  const askButton = page.ask ? (
    <button type="button" className={cn(actionClass(tools ? "default" : "primary"), tools && "[&>svg]:text-primary")}
            onClick={() => { if (page.ask!.mode === "builder") chat.openBuilder({ prompt: page.ask!.prompt, appId: page.ask!.app_id }); else desk.ask(page.ask!.prompt); onClose?.(); }}>
      <Sparkles className="size-3.5" aria-hidden />
      {page.ask.title}
    </button>
  ) : null;
  const content = (
    <div className="flex flex-col gap-4">
      {page.subtitle && !tools ? <p className="t14 select-text text-muted-foreground">{page.subtitle}</p> : null}
      {tools && askButton ? createPortal(askButton, tools) : null}
      {(page.ask && !tools) || page.actions.length || page.open.length || arrange ? (
        <div className="flex flex-wrap items-center gap-2">
          {arrange ? (
            <button type="button" className={actionClass("default")} onClick={() => setEditing({ selected: null })} title="Kéo thả, đổi bề rộng, chọn trường, thêm / bỏ khối">
              <LayoutGrid className="size-3.5" aria-hidden />
              Sửa dashboard
            </button>
          ) : null}
          {tools ? null : askButton}
          {page.actions.map((a) => (
            <button key={a.command + a.title} type="button" className={actionClass(a.style)} onClick={() => desk.openForm(a, { onDone: onChanged })}>
              {a.title}
            </button>
          ))}
          {page.open.map((o, i) => (
            <button key={i} type="button" className={actionClass("default")} onClick={() => desk.openPage(o.query, o.input, o.title)}>
              {o.title ?? o.query}
            </button>
          ))}
        </div>
      ) : null}
      {segments(page.blocks).map(({ i, block: b, charts, row, solo }) => {
        if (solo) return <EditFrame key={i} edit={solo.edit} onChanged={onChanged}><ChartCard block={solo} /></EditFrame>;
        if (row) {
          // hàng chỉ gồm ô số ⇒ xếp bằng flex-wrap (mỗi ô ≥ 170px): hẹp thì xuống hàng, KHÔNG cắt số / nhãn; hàng có biểu đồ ⇒ lưới 12 cột
          if (row.every((c) => c.block.type === "kpis")) {
            return (
              <div key={i} className="flex flex-wrap gap-3 [&>*]:min-w-[170px] [&>*]:flex-1">
                {row.map(({ index, block: cell }) => <div key={index} className="flex flex-col [&>*]:flex-1">{blockView(index, cell)}</div>)}
              </div>
            );
          }
          return (
            <ResponsiveGrid key={i} ws={row.map((c) => c.block.w ?? 12)}>
              {(spans) => row.map(({ index, block: cell }, k) => (
                <div key={index} className="grid-cell flex min-w-0 flex-col [&>*]:flex-1" style={{ gridColumn: `span ${spans[k]} / span ${spans[k]}` }}>
                  {cell.type === "chart" ? <EditFrame edit={cell.edit} onChanged={onChanged}><ChartCard block={cell} /></EditFrame> : blockView(index, cell, true)}
                </div>
              ))}
            </ResponsiveGrid>
          );
        }
        if (charts) return <ChartGrid key={i} blocks={charts} onChanged={onChanged} />;
        if (b.edit && b.type !== "kpis") return <EditFrame key={i} edit={b.edit} onChanged={onChanged}>{blockView(i, { ...b, edit: undefined })}</EditFrame>;
        return blockView(i, b);
      })}
    </div>
  );
  return arrange ? <DesignProvider open={(t) => setEditing({ selected: t.index ?? "new" })}>{content}</DesignProvider> : content;

  function blockView(i: number, b: Block, fill = false) {
        switch (b.type) {
          case "kpis":
            return b.source ? (
              <div key={i} className="flex min-w-0 flex-col gap-1.5">
                <KpiRow kpis={b.items} inset onChanged={onChanged} />
                <SourceLine source={b.source} />
              </div>
            ) : <KpiRow key={i} kpis={b.items} inset onChanged={onChanged} />;
          case "steps":
            return <Section key={i} title={b.title}><StepStrip steps={b.steps} /></Section>;
          case "stages":
            return <Section key={i} title={b.title}><StageList stages={b.stages} /></Section>;
          case "callout":
            return <Callout key={i} {...b} onAction={(a) => desk.openForm(a, { onDone: onChanged })} />;
          case "finding":
            return (
              <FindingCard key={i} {...b} onAction={(a) => desk.openForm(a, { onDone: onChanged })}
                           onOpenDocument={(id, page, title) => desk.openPage("kb.document", page ? { document_id: id, page } : { document_id: id }, title)} />
            );
          case "list":
            if (b.attach) {   // khối "Tệp" của hồ sơ (ADR 0249): cùng danh sách + gắn tệp
              return (
                <RecordFilesBlock key={i} title={b.title} items={b.items} attach={b.attach} onChanged={onChanged}
                  onOpen={(q, input, title) => desk.openPage(q, input, title)}
                  onAsk={(q) => { desk.select({ kind: "assistant" }); chat.send(q); }} />
              );
            }
            return (
              <Section key={i} title={b.title}>
                <ListBlock
                  items={b.items}
                  // mục trỏ tới cả một màn (không tham số, vd "Sức khoẻ dữ liệu" → Khách & lead) ⇒ mở màn đó trên khung làm việc;
                  // trỏ tới một bản ghi ⇒ hộp chi tiết như cũ
                  onOpen={(q, input, title) => (Object.keys(input).length === 0 && desk.screenFor(q) ? void desk.openTarget(q, input, title) : desk.openPage(q, input, title))}
                  onAsk={(q) => { desk.select({ kind: "assistant" }); chat.send(q); }}
                />
              </Section>
            );
          case "tiles":
            return (
              <Section key={i} title={b.title}>
                <TilesBlock items={b.items} onOpen={(q, input, title) => desk.openPage(q, input, title)} />
              </Section>
            );
          case "text":
            return <Section key={i} title={b.title}><Markdown text={b.markdown} /></Section>;
          case "audio":
            return (
              <Section key={i} title={b.title}>
                {/* tải khi bấm phát (preload none): tệp đi qua BFF, server kiểm quyền mỗi lần */}
                <audio controls preload="none" src={`/api/core${b.src}`} className="w-full" aria-label={b.title || "Ghi âm"} />
              </Section>
            );
          case "choices":
            return (
              <Section key={i} title={b.title}>
                <ChoicesBlock block={b} onSend={(text) => { desk.select({ kind: "assistant" }); chat.send(text); }} />
              </Section>
            );
          case "picker":
            return <PickerBlock key={i} block={b} onChanged={onChanged} />;
          case "table":
            if (fill) {   // trong ô lưới cạnh biểu đồ: thẻ có khung + tiêu đề trong khung như biểu đồ, cùng chiều cao hàng, bảng cuộn bên trong
              return (
                <section key={i} className="flex h-full min-w-0 flex-col gap-3 rounded-[14px] border border-hairline bg-surface p-4">
                  {b.page.title ? <h3 className="t14m">{b.page.title}</h3> : null}
                  <TableBlock page={b.page} onChanged={onChanged} fill />
                  <SourceLine source={b.source} />
                </section>
              );
            }
            return (
              <Section key={i} title={b.page.title}>
                <TableBlock page={b.page} onChanged={onChanged} />
                <SourceLine source={b.source} />
              </Section>
            );
          case "board":
            return (
              <Section key={i} title={b.title}>
                <Board columns={b.columns} move={b.move} onMove={(card, col) => move(b.move!, b.columns, card, col)} onOpen={(o) => desk.openPage(o.query, o.input, o.title)} />
              </Section>
            );
          case "timeline":
            return (
              <Section key={i} title={b.title}>
                <TimelineBlock data={b} onOpen={(o) => desk.openPage(o.query, o.input, o.title)} />
              </Section>
            );
          case "thread":
            // luồng này đang mở ở khung trò chuyện bên cạnh ⇒ không hiện một luồng hai lần
            if (desk.talkOpenSubject === b.subject_id) {
              return (
                <p key={i} className="t12 flex items-center gap-2 rounded-xl bg-surface2 px-3 py-2.5 text-muted-foreground">
                  <MessagesSquare className="size-4" aria-hidden />
                  Trao đổi về hồ sơ này đang mở bên cạnh
                </p>
              );
            }
            return <ThreadBlock key={`${b.subject_kind}:${b.subject_id}`} block={b} />;
          case "later":   // chỗ chờ của khối tải sau — cùng khung với khối thật để thẻ không nhảy bố cục nhiều khi khối về
            return (
              <Section key={i} title={b.title}>
                <div className="t12 flex h-24 items-center justify-center gap-2 rounded-[14px] border border-hairline bg-surface2 text-muted-foreground" role="status" aria-live="polite">
                  <Spinner className="size-4" /> Đang tải…
                </div>
              </Section>
            );
          default:
            return null; // khối lạ (server thêm sau) — bỏ qua, không vỡ thẻ
        }
  }
}
