"use client";

import { FileSpreadsheet, FileUp, Pencil, Sparkles } from "lucide-react";
import { KbIntakeDialog } from "@/components/forms/kb-intake";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "@/lib/api";
import { saveBlob } from "@/lib/download";
import { applies, openInput, rowClick } from "@/lib/form";
import { ScreenFiltersContext } from "./filter-context";
import { text, number, toNumber } from "@/lib/format";
import type { BoardCard, BoardViewSpec, FieldSpec, Input, Option, Page, ScreenSpec } from "@/lib/types";
import { cn } from "@/lib/utils";
import { actionClass, LoadError, Spinner } from "@/components/primitives";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { usePage } from "@/hooks/use-page";
import { useDesk } from "@/components/desk/desk-context";
import { useChat } from "@/components/desk/chat-context";
import { SoloScreenFrame, useFullFrame } from "@/components/desk/full-frame";
import { FrameHeadProvider, isSelfFramed, useBareFrame, useFrameHead } from "@/components/desk/frame-context";
import { selectClass } from "@/components/forms/form-body";
import { DatePicker } from "@/components/forms/date-picker";
import { CardView } from "./card";
import { DetailView } from "./detail";
import { Empty } from "./page-content";
import { DataTable, HeaderCharts, KpiRow } from "./table";
import { Agenda } from "./agenda";
import { Board } from "@/components/blocks/board";
import { TimelineBlock } from "@/components/blocks/timeline";
import { PickerBlock } from "@/components/blocks/picker";
import { boardFromRows, formActionFor, movePayload, needsForm } from "@/lib/board";
import { newKey } from "@/lib/idempotency";
import { filterKey, recall, remember } from "@/lib/remembered-filters";
import { splitFilters } from "@/lib/filter-layout";
import { FilterChips, FilterPanel } from "./filter-panel";
import { ClampNote } from "@/components/clamp-note";

/** Một màn: bộ lọc + nút lệnh + KPI + bảng (hoặc trang chi tiết / thẻ). Mọi thứ theo `ScreenSpec` server gửi (ScreenView.swift). */
export function ScreenView({
  spec,
  initialFilters = {},
  initialPage,
  onChanged,
  refresh,
}: {
  spec: ScreenSpec;
  initialFilters?: Input;
  initialPage?: Page | null;
  onChanged?: () => void;
  /** Tăng ⇒ tải lại tại chỗ (giữ nội dung đang thấy tới khi có bản mới) — trình tạo app vừa sửa xong một bước (0098). */
  refresh?: number;
}) {
  const desk = useDesk();
  const chat = useChat();
  // bộ lọc đã áp (gọi truy vấn) và bộ lọc đang gõ (ô chữ chỉ áp khi Enter / rời ô)
  // lựa chọn lọc người này dùng lần trước (rà vai 18/11/2026) — chỉ khi mở màn từ menu: mở từ chat / ô số mang đầu vào riêng thì không nhớ, không ghi
  const own = spec.filters.filter((fs) => !(fs.key in (spec.input ?? {})));
  const memoKey =
    desk.manifest && !initialPage && !Object.keys(initialFilters).length ? filterKey(desk.manifest.tenant.id, desk.manifest.user.id, spec.query) : null;
  const [filters, setFilters] = useState<Input>(() => {
    const f: Input = { ...(spec.input ?? {}) }; // đầu vào cố định của màn (0098); thẻ mở từ chat mang đầu vào riêng ⇒ ghi đè
    for (const [k, v] of Object.entries(initialFilters)) if (v !== null) f[k] = v;
    for (const fs of spec.filters) if (f[fs.key] === undefined && fs.default !== undefined && fs.default !== null && !initialPage) f[fs.key] = fs.default;
    return memoKey ? { ...f, ...recall(memoKey, own) } : f;
  });
  useEffect(() => {
    if (memoKey) remember(memoKey, own, filters);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- ghi khi bộ lọc đổi; danh sách ô lọc theo cùng màn
  }, [filters, memoKey]);
  const [options, setOptions] = useState<Record<string, Option[]>>({});
  const [selected, setSelected] = useState<string | null>(null);
  const full = useFullFrame();   // đang chiếm trọn khung (phân hệ một màn)
  const [mode, setMode] = useState("table");   // góc nhìn bảng: table | board | calendar | timeline (0087, 0222)
  const { page, error, errorAt, loading, reload, more } = usePage(spec.query, filters, initialPage, mode === "table");
  useBareFrame(isSelfFramed(page));   // trong thẻ của khung làm việc: khối tự vẽ khung ⇒ thẻ không vẽ đầu / viền (0231)
  const lastRefresh = useRef(refresh);
  useEffect(() => {
    if (refresh === lastRefresh.current) return;
    lastRefresh.current = refresh;
    void reload();
  }, [refresh, reload]);
  const [uploading, setUploading] = useState(false);
  const [panelOpen, setPanelOpen] = useState(false);   // nút "Bộ lọc" (ô lọc phụ — rà bố cục 18/11)

  useEffect(() => {
    let alive = true;
    for (const f of spec.filters) {
      if (!f.options && f.options_query) {
        void api.options(f.options_query).then((o) => alive && setOptions((cur) => ({ ...cur, [f.key]: o })), () => null);
      }
    }
    return () => {
      alive = false;
    };
  }, [spec.filters]);

  const table = page?.kind === "table" ? page : null;
  const selectedRow = table?.rows.find((r) => r.id === selected) ?? null;
  const changed = () => {
    void reload();
    onChanged?.();
  };

  async function exportXlsx() {
    try {
      saveBlob(await api.xlsx(spec.query, filters), `${page?.title || spec.title}.xlsx`);
      desk.show("Đã xuất Excel");
    } catch (e) {
      desk.show(e instanceof Error ? e.message : "Xuất Excel lỗi", true);
    }
  }

  /** Kéo thẻ Kanban = chạy lệnh server khai (kiểm quyền / luật như mọi lệnh); cột cần hỏi thêm ⇒ form điền sẵn. */
  async function moveCard(b: BoardViewSpec, card: BoardCard, column: string): Promise<boolean> {
    if (!b.move) return false;
    if (needsForm(b.move, column)) {
      const col = b.columns.find((c) => c.key === column);
      desk.openForm(formActionFor(b.move, card, col ? { ...col, items: [] } : undefined), { onDone: changed });
      return false;
    }
    try {
      const r = await api.command(b.move.command, movePayload(b.move, card.id, column), newKey());
      if (r.status !== "ok") {
        desk.show(r.error || `Không đổi được (${r.status})`, true);
        return false;
      }
      changed();
      return true;
    } catch (e) {
      desk.show(e instanceof Error ? e.message : "Không đổi được", true);
      return false;
    }
  }

  function openRow(row: NonNullable<typeof selectedRow>) {
    if (!spec.open) return;
    desk.openPage(spec.open.query, openInput(spec.open.bind, row), spec.open.title);
  }

  /** Bấm một lần (server khai `row_click`): mở chi tiết / form nút mặc định; dòng không thoả `when` của nút ⇒ chỉ chọn. */
  function activateRow(row: NonNullable<typeof selectedRow>) {
    const t = rowClick(spec, row);
    if (t?.kind === "open") openRow(row);
    else if (t?.kind === "action") desk.openForm(t.action, { row, onDone: changed });
  }

  const applyFilter = (key: string) => (v: Input[string] | undefined) =>
    setFilters((cur) => {
      const next = { ...cur };
      if (v === undefined) delete next[key];
      else next[key] = v;
      return next;
    });
  // lọc chéo kiểu báo cáo BI (0173): biểu đồ trong thẻ đọc / đặt bộ lọc của MÀN này qua ngữ cảnh
  const bridge = useMemo(
    () => ({
      get: (k: string) => (typeof filters[k] === "string" ? (filters[k] as string) : undefined),
      set: (k: string, v: string | undefined) =>
        setFilters((cur) => {
          const next = { ...cur };
          if (v === undefined) delete next[k];
          else next[k] = v;
          return next;
        }),
    }),
    [filters],
  );
  const side = !!spec.filters_side && spec.filters.some((f) => !f.group);
  const head = useFrameHead();
  const tools = head?.tools ?? null;
  const renderFilter = (f: FieldSpec, stacked = false) => (
    <Filter key={f.key} f={f} value={filters[f.key]} options={f.options ?? options[f.key]} onApply={applyFilter(f.key)} stacked={stacked} />
  );
  // BỘ LỌC (rà bố cục 18/11, kiểu Linear / Notion / Airtable): ô CHÍNH ở ngoài (server khai `primary`, thiếu ⇒ đoán 2 ô đầu); màn > 3 ô thì mọi ô còn
  // lại — kể cả nhóm gấp `group` — vào nút "Bộ lọc" (mở nguyên bộ form, Áp dụng / Xoá lọc); ô phụ đang bật hiện thành chip dưới hàng đầu.
  // Màn kiểu báo cáo BI (`filters_side`): cột lọc bên trái giữ nguyên, chỉ nhóm gấp vào nút.
  const split = side ? { inline: [] as FieldSpec[], more: spec.filters.filter((f) => f.group) } : splitFilters(spec.filters);
  const applyMany = (next: Record<string, Input[string] | undefined>) =>
    setFilters((cur) => {
      const out = { ...cur };
      for (const [k, v] of Object.entries(next)) {
        if (v === undefined) delete out[k];
        else out[k] = v;
      }
      return out;
    });
  const panel = split.more.length ? (
    <FilterPanel key="filters" fields={split.more} values={filters} open={panelOpen} onOpenChange={setPanelOpen} onApply={applyMany}
                 render={(f, v, set) => <Filter f={f} value={v} options={f.options ?? options[f.key]} onApply={set} stacked />} />
  ) : null;

  // ĐẦU THẺ GỌN (rà bố cục 18/11): trong khung làm việc, bộ lọc chính + nút hỏi trợ lý (+ ít nút lệnh) lên hàng tên thẻ ⇒ ô số lên sát đầu.
  // Màn nhiều bộ lọc / nhiều nút thì chỉ nút hỏi trợ lý lên đầu, hàng lọc vẫn nằm trong thân như cũ (đầu thẻ không thành một bức tường ô).
  const primary = split.inline;
  const inHead = !!tools && primary.length <= 3 && spec.actions.length <= 2;
  const showAsk = !!spec.ask && !(spec.ask.app_id && chat.builder?.appId === spec.ask.app_id);
  const askEl = showAsk && spec.ask ? (
    // nút của MÀN (vd "Tạo app" / "Sửa app" ⇒ trình tạo — 0098); đang sửa đúng app này trong trình tạo thì không cần. Trên đầu thẻ: nút phụ (viền), không giành mắt với số
    <button key="ask" type="button" className={cn(actionClass(spec.ask.app_id || tools ? "default" : "primary"), tools && !spec.ask.app_id && "[&>svg]:text-primary")}
            onClick={() => (spec.ask!.mode === "builder" ? chat.openBuilder({ prompt: spec.ask!.prompt, appId: spec.ask!.app_id }) : desk.ask(spec.ask!.prompt))}
            title={spec.ask.mode === "builder" ? "Mở trình tạo app" : "Nói với trợ lý điều bạn cần"}>
      {spec.ask.app_id ? <Pencil className="size-3.5" aria-hidden /> : <Sparkles className="size-3.5" aria-hidden />}
      {spec.ask.title}
    </button>
  ) : null;
  const actionEls = spec.actions.map((a) =>
    a.command === "kb.add_document" ? (
      // 0225: Kho tài liệu — cửa chính là THẢ FILE (máy đọc + đoán, người xác nhận); dán chữ chỉ là lối phụ, nhỏ, cho ca hiếm
      <div key={a.command + a.title} className="flex items-center gap-2">
        <button type="button" className={actionClass(a.style)} onClick={() => setUploading(true)}>
          <FileUp className="size-3.5" aria-hidden />
          {a.title}
        </button>
        <button type="button" className="t12 text-muted-foreground underline-offset-2 hover:text-foreground hover:underline" onClick={() => desk.openForm(a, { onDone: changed })}>
          Dán chữ
        </button>
      </div>
    ) : a.importable ? (
      <div key={a.command + a.title} className="flex">
        <button type="button" className={cn(actionClass(a.style), "rounded-r-none")} onClick={() => desk.openForm(a, { onDone: changed })}>{a.title}</button>
        <DropdownMenu>
          <DropdownMenuTrigger className={cn(actionClass(a.style), "rounded-l-none border-l border-white/30 px-1.5")} aria-label={`Thêm cách nhập: ${a.title}`}>▾</DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-44">
            <DropdownMenuItem onClick={() => desk.openForm(a, { onDone: changed })}>Nhập một</DropdownMenuItem>
            <DropdownMenuItem onClick={() => desk.openImport(a, changed)}>Nhập từ Excel…</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    ) : (
      <button key={a.command + a.title} type="button" className={actionClass(a.style)} onClick={() => desk.openForm(a, { onDone: changed })}>{a.title}</button>
    ),
  );
  const exportButton = (
    <button key="xlsx" type="button" className={actionClass("default")} onClick={() => void exportXlsx()} title="Xuất bảng đang xem ra tệp Excel">
      <FileSpreadsheet className="size-3.5" aria-hidden />
      Xuất Excel
    </button>
  );
  const headTools = tools ? createPortal(<>{inHead ? primary.map((f) => renderFilter(f)) : null}{inHead ? panel : null}{askEl}{inHead ? actionEls : null}</>, tools) : null;
  // màn không có bộ lọc / nút nào (vd màn tự vẽ trọn khung) ⇒ không để một hàng trống chiếm chỗ
  const bar = !inHead && (primary.length > 0 || !!panel || (!tools && showAsk) || spec.actions.length > 0);
  // Xuất Excel không có góc nhìn đi cùng ⇒ không đứng một mình một hàng: lên đầu thẻ (đầu thẻ đã cầm bộ lọc / thẻ không có hàng lọc) hoặc vào cuối hàng lọc
  const excelAt: "row" | "head" | "bar" = !table || table.views ? "row" : tools && (inHead || !bar) ? "head" : bar ? "bar" : "row";
  const content = (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-3">
      {headTools}
      <div className={cn("flex flex-wrap items-center gap-x-2.5 gap-y-2", !bar && "hidden")}>
        {inHead ? null : primary.map((f) => renderFilter(f))}
        {inHead ? null : panel}
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {tools ? null : askEl}
          {inHead ? null : actionEls}
          {excelAt === "bar" ? exportButton : null}
        </div>
      </div>
      <FilterChips fields={split.more} values={filters} options={options} onEdit={() => setPanelOpen(true)} onApply={applyMany} />
      {error ? <LoadError message={error} at={errorAt} busy={loading} compact={!!page} onRetry={() => void reload()} /> : null}
      {!page && loading ? <div className="grid flex-1 place-items-center py-16"><Spinner className="size-6" /></div> : null}
      {page?.kind === "detail" ? <DetailView page={page} onChanged={changed} /> : null}
      {page?.kind === "card" && page.tabs ? (
        // thẻ nhiều trang (app tự ghép — 0098): tab = chạy lại truy vấn của màn với tham số trang
        <div className="flex w-fit max-w-full shrink-0 gap-0.5 overflow-x-auto rounded-lg bg-surface2 p-0.5" role="tablist" aria-label="Trang">
          {page.tabs.items.map((t) => (
            <button key={t.key} type="button" role="tab" aria-selected={page.tabs!.current === t.key}
                    onClick={() => setFilters((cur) => ({ ...cur, [page.tabs!.param]: t.key }))}
                    className={cn("t14m whitespace-nowrap rounded-md px-3.5 py-1.5", page.tabs!.current === t.key ? "bg-surface shadow-sm" : "text-muted-foreground hover:text-foreground")}>
              {t.title}
            </button>
          ))}
        </div>
      ) : null}
      {page?.kind === "card" ? <CardView page={page} onChanged={changed} head={head} /> : null}
      {table ? (
        <>
          {table.header?.map((b, i) => (b.type === "picker" ? <PickerBlock key={i} block={b} onChanged={changed} /> : null))}
          <HeaderCharts page={table} />
          <KpiRow kpis={table.kpis} inset />
          {table.note ? <ClampNote text={table.note} /> : null}
          {/* hàng ngay TRÊN bảng: góc nhìn (nếu có) bên trái, Xuất Excel bên phải — nút thuộc về cái bảng nó xuất. Không có góc nhìn ⇒ `excelAt` */}
          {excelAt === "head" && tools ? createPortal(exportButton, tools) : null}
          <div className={cn("flex flex-wrap items-center justify-between gap-2", excelAt !== "row" && "hidden")}>
            {table.views ? (
              <div className="flex w-fit gap-0.5 rounded-lg bg-surface2 p-0.5" role="tablist" aria-label="Góc nhìn">
                {([["table", "Bảng"], ...(table.views.board ? [["board", "Kanban"]] : []), ...(table.views.calendar ? [["calendar", "Lịch"]] : []), ...(table.views.timeline ? [["timeline", "Dòng thời gian"]] : [])] as [string, string][]).map(([k, label]) => (
                  <button key={k} type="button" role="tab" aria-selected={mode === k} onClick={() => setMode(k)} className={cn("t12m rounded-md px-3 py-1", mode === k ? "bg-surface shadow-sm" : "text-muted-foreground hover:text-foreground")}>{label}</button>
                ))}
              </div>
            ) : <span />}
            {exportButton}
          </div>
          {table.rows.length === 0 ? (
            <Empty text={table.empty}>
              {table.empty_actions?.map((a) => (
                <button key={a.command + a.title} type="button" className={actionClass(a.style)} onClick={() => desk.openForm(a, { onDone: changed })}>{a.title}</button>
              ))}
            </Empty>
          ) : mode === "board" && table.views?.board ? (
            <div className="min-h-0 flex-1 overflow-auto">
              <Board
                columns={boardFromRows(table.rows, table.views.board, (r) => (spec.open ? { query: spec.open.query, input: openInput(spec.open.bind, r), title: spec.open.title } : undefined))}
                move={table.views.board.move}
                onMove={(card, col) => moveCard(table.views!.board!, card, col)}
                onOpen={(o) => desk.openPage(o.query, o.input, o.title)}
              />
            </div>
          ) : mode === "timeline" && table.views?.timeline ? (
            <TimelineBlock data={table.views.timeline} onOpen={(o) => desk.openPage(o.query, o.input, o.title)} />
          ) : mode === "calendar" && table.views?.calendar ? (
            <div className="min-h-0 flex-1 overflow-auto rounded-xl border border-hairline">
              <Agenda rows={table.rows} spec={table.views.calendar} onOpen={spec.open ? openRow : undefined} />
            </div>
          ) : (
            // bảng cao ít nhất ~8 dòng: ô số / bộ lọc nhiều thì THẺ cuộn, không bóp bảng còn ba bốn dòng cuộn lồng trong khung nhỏ
            <div className="min-h-[320px] flex-1 overflow-auto rounded-xl border border-hairline">
              <DataTable page={table} selected={selected} onSelect={setSelected} onOpen={spec.open ? openRow : undefined} onActivate={spec.row_click ? activateRow : undefined}
                         onMore={more} />
            </div>
          )}
          {selectedRow ? (
            <div className="flex flex-wrap items-center gap-2 rounded-xl bg-surface2 px-3 py-2" role="toolbar" aria-label="Thao tác cho dòng đang chọn">
              <span className="t12 text-muted-foreground">Dòng đang chọn:</span>
              {spec.row_actions.filter((a) => applies(a, selectedRow)).map((a) => (
                <button key={a.command + a.title} type="button" className={cn(actionClass(a.style), "h-7 text-[13px]")} onClick={() => desk.openForm(a, { row: selectedRow, onDone: changed })}>{a.title}</button>
              ))}
              {spec.open ? <button type="button" className={cn(actionClass("default"), "h-7 text-[13px]")} onClick={() => openRow(selectedRow)}>{spec.open.title || "Mở"}</button> : null}
              {spec.row_actions.every((a) => !applies(a, selectedRow)) && !spec.open ? <span className="t12 text-muted-foreground/70">Không có thao tác cho dòng này</span> : null}
            </div>
          ) : null}
        </>
      ) : null}
      {uploading ? (
        <KbIntakeDialog
          onOpenChange={setUploading}
          onOpenSimilar={(id, title) => desk.openPage("kb.document", { document_id: id }, title)}
          onPasteText={() => {
            setUploading(false);
            const a = spec.actions.find((x) => x.command === "kb.add_document");
            if (a) desk.openForm(a, { onDone: changed });
          }}
          onDone={(m) => {
            desk.show(m);
            changed();
          }}
        />
      ) : null}
    </div>
  );
  // bộ lọc kiểu báo cáo BI (0166): cột bên trái, nội dung bên phải; hẹp (< md) thì cột lọc nằm trên
  // KHÔNG đặt min-h-0 / flex-1 theo chiều dọc ở đây: hàng phải cao BẰNG nội dung thì cột lọc (sticky) mới dính suốt chiều cuộn — bó vào chiều cao khung cuộn thì nó chỉ dính được một màn rồi trôi mất
  const body = side ? (
    <div className="flex w-full flex-col gap-3 md:flex-row md:gap-4">
      <aside className="flex shrink-0 flex-col gap-3 rounded-[14px] border border-hairline bg-surface p-3 md:sticky md:top-0 md:max-h-[calc(100vh-140px)] md:w-[190px] md:self-start md:overflow-y-auto" aria-label="Bộ lọc">
        <h3 className="t12m text-muted-foreground">Bộ lọc</h3>
        {spec.filters.filter((f) => !f.group).map((f) => renderFilter(f, true))}
      </aside>
      {content}
    </div>
  ) : (
    content
  );
  // đầu thẻ chỉ một màn nhận (màn này) ⇒ cây con (thẻ lồng, chi tiết…) thấy null
  const framed = <FrameHeadProvider value={null}><ScreenFiltersContext.Provider value={bridge}>{body}</ScreenFiltersContext.Provider></FrameHeadProvider>;
  // phân hệ một màn (khung để trần): chỉ màn tự vẽ ngăn (khối soạn với AI) dùng trần; màn thường có đầu + thẻ bọc như mọi màn
  const selfFramed = isSelfFramed(page);
  return full && !selfFramed ? <SoloScreenFrame>{framed}</SoloScreenFrame> : framed;
}

function Filter({ f, value, options, onApply, stacked = false }: { f: FieldSpec; value: Input[string] | undefined; options?: Option[]; onApply: (v: Input[string] | undefined) => void; stacked?: boolean }) {
  const [draft, setDraft] = useState<string | null>(null);
  const shown = useMemo(() => {
    if (draft !== null) return draft;
    if (f.kind === "money") {
      const n = toNumber(value);
      if (n !== null) return number(n);
    }
    return text(value);
  }, [draft, f.kind, value]);
  const id = `flt-${f.key}`;
  const row = stacked ? "t12 flex flex-col items-stretch gap-1 text-muted-foreground" : "t12 flex items-center gap-1.5 text-muted-foreground";   // cột lọc bên trái: nhãn trên, ô dưới, rộng hết cột
  if (f.kind === "bool") {
    // vd kb.search include_history: "Gồm bản cũ / hết hiệu lực"
    return (
      <label className={row} htmlFor={id}>
        <input id={id} type="checkbox" className="size-4 accent-[var(--primary)]" checked={value === true} onChange={(e) => onApply(e.target.checked ? true : undefined)} />
        {f.label}
      </label>
    );
  }
  if (options) {
    return (
      <label className={row} htmlFor={id}>
        <span className={stacked ? undefined : "shrink-0 whitespace-nowrap"}>{f.label}</span>
        <select id={id} className={cn(selectClass, "t12 h-8 text-foreground", stacked ? "w-full" : "w-auto max-w-[220px] truncate")} value={text(value ?? f.default ?? "")} onChange={(e) => onApply(e.target.value)}>
          {!options.some((o) => o.value === "") ? <option value="">Tất cả</option> : null}
          {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </label>
    );
  }
  if (f.kind === "date") {
    // ngày: ô chọn ngày dùng chung (lịch thả xuống); giá trị gửi lên là ISO yyyy-mm-dd (lõi nhận)
    return (
      <label className={row} htmlFor={id}>
        <span className={stacked ? undefined : "shrink-0 whitespace-nowrap"}>{f.label}</span>
        <DatePicker id={id} size="sm" value={text(value)} placeholder="Chọn ngày" onChange={(v) => onApply(v)} className={stacked ? "w-full" : "w-36"} />
      </label>
    );
  }
  const commit = () => {
    if (draft === null) return;
    const t = draft.trim();
    setDraft(null);
    if (t === "") onApply(undefined);
    else if (f.kind === "number" && Number.isFinite(Number(t))) onApply(Number(t));
    else onApply(t);
  };
  return (
    <label className={row} htmlFor={id}>
      <span className={stacked ? undefined : "shrink-0 whitespace-nowrap"}>{f.label}</span>
      <input
        id={id}
        value={shown}
        placeholder={f.placeholder || (f.kind === "money" ? "VD 4,5 tỷ" : "Tất cả")}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === "Enter" && commit()}
        className={cn("t12 h-8 rounded-lg border border-input bg-field px-2 text-foreground outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40", stacked ? "w-full" : f.kind === "number" ? "w-16" : "w-36")}
      />
    </label>
  );
}
