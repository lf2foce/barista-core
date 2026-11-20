"use client";

import {
  closestCenter, DndContext, DragOverlay, KeyboardSensor, PointerSensor, useSensor, useSensors, type DragEndEvent, type DragStartEvent,
} from "@dnd-kit/core";
import { SortableContext, sortableKeyboardCoordinates, useSortable, type SortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { Check, GripVertical, Pencil, Plus, Sparkles, Trash2 } from "lucide-react";
import { useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import { indexAfter, layoutInput, move, type Layout, type Tile } from "@/lib/arrange";
import { newKey } from "@/lib/idempotency";
import type { ActionSpec, Block } from "@/lib/types";
import { cn } from "@/lib/utils";
import { actionClass, Spinner } from "@/components/primitives";
import { useDesk } from "@/components/desk/desk-context";
import { DesignPane, useBlockDesign, type BlockDesign, type DesignTarget } from "./block-panel";

// Chế độ "Sửa dashboard" (0220 lát 3b — kiểu Power BI): sửa NGAY trên dashboard thật. Mỗi khối luôn có thanh nhỏ (tay nắm kéo, tên, bề rộng, AI sửa,
// xoá) — không phải rê chuột mới thấy. Kéo thả bằng @dnd-kit (các khối dạt ra nhường chỗ, dùng được bàn phím / cảm ứng); mỗi lần thả / đổi bề rộng
// lưu ngay bằng `apps.layout` (lõi giữ phiên bản). Bấm một khối ⇒ khung chọn trường bên phải, khối đó hiện bản xem trước tại chỗ cho tới khi Lưu.
// Bố cục vẫn là thứ tự + bề rộng trên lưới 12 cột (app Mac / điện thoại vẽ như cũ) — không phải toạ độ tự do.

export type Selection = number | "new" | null;

/** Khối to nhỏ khác nhau trên lưới tự xuống hàng ⇒ dồn chỗ khi đang kéo làm các khối chồng nhau; giữ nguyên chỗ, chỉ sáng khối đích + vạch chỗ thả. */
const stay: SortingStrategy = () => null;   // khối gốc thứ mấy (1…), khối mới, hoặc chưa chọn

const editOf = (b: Block): ActionSpec | undefined => (b.type === "kpis" ? b.items[0]?.edit : b.edit);
// đang sửa: bỏ nút sửa / "Vì sao?" của khối (thao tác của chế độ sửa nằm trên thanh của ô)
const stripEdit = (b: Block): Block => (b.type === "kpis" ? { ...b, items: b.items.map((k) => ({ ...k, edit: undefined, why: undefined })) } : { ...b, edit: undefined, why: undefined });

export function DashboardEditor({ page, layout, blocks, selected, onSelect, onReload, onExit, render }: {
  page: string;
  layout: Layout;
  blocks: Block[];                     // cùng thứ tự với layout.tiles (layoutOf dựng mỗi khối thẻ một Ô)
  selected: Selection;
  onSelect: (s: Selection) => void;
  onReload: () => void;
  onExit: () => void;
  render: (b: Block) => ReactNode;
}) {
  const desk = useDesk();
  const [tiles, setTiles] = useState(layout.tiles);
  const [dragging, setDragging] = useState<string | null>(null);
  const [over, setOver] = useState<string | null>(null);
  // thả lên khối đứng SAU khối đang kéo ⇒ chèn sau nó; đứng trước ⇒ chèn trước (đúng như move())
  const dropSide = (id: string): "before" | "after" => (tiles.findIndex((t) => t.id === id) > tiles.findIndex((t) => t.id === dragging) ? "after" : "before");
  const [saving, setSaving] = useState(false);
  const byId = useMemo(() => new Map(layout.tiles.map((t, k) => [t.id, blocks[k]])), [layout.tiles, blocks]);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }), useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }));
  const target = useMemo<DesignTarget | null>(
    () => (selected === null ? null : { app_id: layout.app_id, page, index: selected === "new" ? null : selected }),
    [layout.app_id, page, selected],
  );

  async function saveLayout(next: Tile[]) {
    setTiles(next);   // hiện ngay, lưu sau
    setSaving(true);
    try {
      const r = await api.command("apps.layout", layoutInput({ ...layout, tiles: next }), newKey());
      if (r.status !== "ok") {
        desk.show(r.error || `Chưa lưu được bố cục (${r.status})`, true);
        setTiles(layout.tiles);
        return;
      }
      if (typeof selected === "number") onSelect(indexAfter(next, selected));   // khối đang chọn đổi số thứ tự theo chỗ mới
      onReload();
    } catch (e) {
      desk.show(e instanceof Error ? e.message : "Chưa lưu được bố cục", true);
      setTiles(layout.tiles);
    } finally {
      setSaving(false);
    }
  }

  async function remove(t: Tile) {
    const r = await api.command("apps.remove_block", { app_id: layout.app_id, page, index: t.indices[0] }, newKey());
    if (r.status !== "ok") {
      desk.show(r.error || `Chưa xoá được (${r.status})`, true);
      return;
    }
    desk.show("Đã bỏ khối");
    onSelect(null);
    onReload();
  }

  // Ô "nói muốn xem gì" (0220 lát 4): đang chọn một khối ⇒ sửa ĐÚNG khối đó bằng lời (apps.revise_block); không chọn ⇒ dựng MỘT khối mới từ số
  // chuẩn (apps.compose_block). Cả hai chạy thử ở server bằng quyền người dùng rồi mới lưu.
  const [ask, setAsk] = useState("");
  const [asking, setAsking] = useState(false);
  async function runAsk() {
    const instruction = ask.trim();
    if (!instruction || asking) return;
    setAsking(true);
    try {
      const base = { app_id: layout.app_id, page, instruction };
      const r = typeof selected === "number" ? await api.command("apps.revise_block", { ...base, index: selected }, newKey())
                                             : await api.command("apps.compose_block", base, newKey());
      if (r.status !== "ok") {
        desk.show(r.error || `Chưa làm được (${r.status})`, true);
        return;
      }
      setAsk("");
      const out = r.output as { index?: number } | null;
      desk.show(typeof selected === "number" ? "Đã sửa khối" : "Đã thêm khối");
      onSelect(typeof selected === "number" ? null : typeof out?.index === "number" ? out.index : null);   // sửa xong bỏ chọn ⇒ thấy khối thật sau khi lưu
      onReload();
    } catch (e) {
      desk.show(e instanceof Error ? e.message : "Chưa làm được", true);
    } finally {
      setAsking(false);
    }
  }

  const onDragEnd = (e: DragEndEvent) => {
    setDragging(null);
    setOver(null);
    const from = tiles.findIndex((t) => t.id === e.active.id);
    const to = tiles.findIndex((t) => t.id === e.over?.id);
    if (from < 0 || to < 0 || from === to) return;
    void saveLayout(move(tiles, from, to));
  };
  const dragged = tiles.find((t) => t.id === dragging);

  const view = (state: BlockDesign | null) => (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2 rounded-xl bg-primary/8 px-3 py-2">
        <span className="t14m">Đang sửa dashboard</span>
        <span className="t12 text-muted-foreground">Kéo ⠿ để đổi chỗ · kéo mép phải để đổi bề rộng · bấm một khối để chọn trường · bố cục lưu ngay</span>
        {saving ? <Spinner className="size-3.5" /> : null}
        <div className="ml-auto flex gap-2">
          <button type="button" className={actionClass("default")} onClick={() => onSelect("new")} disabled={selected === "new"}>
            <Plus className="size-3.5" aria-hidden />
            Thêm khối
          </button>
          <button type="button" className={actionClass("primary")} onClick={onExit}>
            <Check className="size-3.5" aria-hidden />
            Xong
          </button>
        </div>
      </div>
      <form className="flex items-center gap-2 rounded-xl border border-border bg-surface px-2.5 py-1.5 focus-within:border-ring focus-within:ring-2 focus-within:ring-ring/30"
            onSubmit={(e) => { e.preventDefault(); void runAsk(); }}>
        <Sparkles className="size-4 shrink-0 text-primary" aria-hidden />
        <input value={ask} onChange={(e) => setAsk(e.target.value)} disabled={asking} maxLength={500} aria-label="Nói muốn xem gì"
               placeholder={typeof selected === "number" ? `Nói muốn đổi gì ở khối ${selected} — vd: chia theo đội, chỉ 3 tháng gần nhất` : "Nói muốn xem gì để thêm khối — vd: chi phí quảng cáo theo kênh quý này"}
               className="t14 h-7 min-w-0 flex-1 bg-transparent outline-none placeholder:text-muted-foreground" />
        {asking ? <Spinner className="size-4" /> : null}
        <button type="submit" className={cn(actionClass("primary"), "h-7 px-2.5")} disabled={asking || !ask.trim()}>
          {asking ? "Đang dựng…" : typeof selected === "number" ? "Sửa khối" : "Thêm khối"}
        </button>
      </form>
      <div className="@container">
        <div className={cn("grid grid-cols-1 items-start gap-4", target && "@min-[760px]:grid-cols-[minmax(0,1fr)_272px]")}>
          <DndContext sensors={sensors} collisionDetection={closestCenter} onDragStart={(e: DragStartEvent) => setDragging(String(e.active.id))}
                      onDragOver={(e) => setOver(e.over ? String(e.over.id) : null)} onDragEnd={onDragEnd} onDragCancel={() => { setDragging(null); setOver(null); }}>
            <SortableContext items={tiles.map((t) => t.id)} strategy={stay}>
              <div className="@container min-w-0">
                <div className="grid grid-cols-12 gap-3">
                  {tiles.map((t) => {
                    const block = byId.get(t.id);
                    const on = typeof selected === "number" && t.indices.includes(selected);
                    return (
                      <SortableTile key={t.id} tile={t} selected={on} dropSide={dragging && over === t.id && dragging !== t.id ? dropSide(t.id) : null} onSelect={() => onSelect(t.indices[0])}
                                    onWidth={(w) => void saveLayout(tiles.map((x) => (x.id === t.id ? { ...x, w } : x)))}
                                    onRevise={block && editOf(block) ? () => desk.openForm(editOf(block)!, { onDone: onReload }) : undefined}
                                    onRemove={t.indices.length === 1 ? () => void remove(t) : undefined}>
                        {on && state ? <Preview state={state} render={render} /> : block ? render(stripEdit(block)) : null}
                      </SortableTile>
                    );
                  })}
                  {selected === "new" && state ? (
                    <div className="col-span-12 rounded-[20px] bg-primary/6 p-1.5 ring-2 ring-primary">
                      <Preview state={state} render={render} />
                    </div>
                  ) : null}
                </div>
              </div>
            </SortableContext>
            <DragOverlay>
              {dragged ? <div className="t14m w-fit max-w-64 truncate rounded-[14px] border border-primary bg-surface px-3 py-2 opacity-90">{dragged.title}</div> : null}
            </DragOverlay>
          </DndContext>
          {target && state ? (
            <aside className="flex min-w-0 flex-col rounded-[14px] border border-hairline bg-surface p-3 @min-[760px]:sticky @min-[760px]:top-0" aria-label="Bảng sửa khối">
              <DesignPane state={state} title={target.index ? `Khối ${target.index}` : "Khối mới"} onClose={() => onSelect(null)} />
            </aside>
          ) : null}
        </div>
      </div>
    </div>
  );
  // khối đang chọn: MỘT trạng thái sửa (bản nháp + xem trước) dùng chung cho ô trên lưới (xem trước tại chỗ) và khung chọn bên phải
  return target ? (
    <WithDesign key={`${target.index ?? "new"}`} target={target} onSaved={(i) => { onSelect(i); onReload(); }}>{view}</WithDesign>
  ) : view(null);
}

function WithDesign({ target, onSaved, children }: { target: DesignTarget; onSaved: (index: number) => void; children: (s: BlockDesign) => ReactNode }) {
  const state = useBlockDesign(target, onSaved);
  return <>{children(state)}</>;
}

/** Khối đang chọn hiện BẢN XEM TRƯỚC tại chỗ (số do server tính, chưa lưu). */
function Preview({ state, render }: { state: BlockDesign; render: (b: Block) => ReactNode }) {
  return (
    <div className={cn("flex min-w-0 flex-col gap-3 transition-opacity", state.loading && "opacity-60")} aria-busy={state.loading}>
      {state.shown ? state.shown.blocks.map((b, i) => <div key={i} className="flex min-w-0 flex-col">{render(b)}</div>)
                   : <div className="grid min-h-[160px] place-items-center"><Spinner /></div>}
    </div>
  );
}

// góc ngoài = góc khối bên trong (14px) + đệm (6px) = 20px — bo đồng tâm, không "cấn" ở bốn góc
function SortableTile({ tile, selected, dropSide, onSelect, onWidth, onRevise, onRemove, children }: {
  tile: Tile;
  selected: boolean;
  dropSide: "before" | "after" | null;
  onSelect: () => void;
  onWidth: (w: number) => void;
  onRevise?: () => void;
  onRemove?: () => void;
  children: ReactNode;
}) {
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } = useSortable({ id: tile.id });
  const [confirm, setConfirm] = useState(false);
  // KÉO MÉP PHẢI để đổi bề rộng (kiểu Power BI): bắt dính lưới 12 cột, hiện số cột khi kéo, thả ⇒ lưu một lần. Bàn phím: ← → trên tay nắm mép, Enter lưu.
  const [live, setLive] = useState<number | null>(null);
  const node = useRef<HTMLDivElement | null>(null);
  const w = live ?? tile.w;
  const colsAt = (clientX: number): number => {
    const el = node.current, grid = el?.parentElement;
    if (!el || !grid) return tile.w;
    const gap = parseFloat(getComputedStyle(grid).columnGap) || 0;
    const col = (grid.clientWidth - 11 * gap) / 12;
    return Math.max(2, Math.min(12, Math.round((clientX - el.getBoundingClientRect().left + gap) / (col + gap))));
  };
  const startResize = (e: React.PointerEvent<HTMLElement>) => {
    e.preventDefault();
    e.stopPropagation();
    let last = tile.w;
    const move = (ev: PointerEvent) => { last = colsAt(ev.clientX); setLive(last); };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      setLive(null);
      if (last !== tile.w) onWidth(last);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  const keyResize = (e: React.KeyboardEvent<HTMLElement>) => {
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      setLive(Math.max(2, Math.min(12, w + (e.key === "ArrowRight" ? 1 : -1))));
    } else if ((e.key === "Enter" || e.key === " ") && live !== null) {
      e.preventDefault();
      if (live !== tile.w) onWidth(live);
      setLive(null);
    } else if (e.key === "Escape") setLive(null);
  };
  return (
    <div
      ref={(el) => { setNodeRef(el); node.current = el; }}
      style={{ transform: CSS.Translate.toString(transform), transition, gridColumn: `span ${w} / span ${w}` }}
      className={cn("@container/tile relative flex min-w-0 flex-col gap-1.5 rounded-[20px] p-1.5 @max-[480px]:![grid-column:span_12/span_12]",
                    selected || dropSide ? "bg-primary/6 ring-2 ring-primary" : "ring-1 ring-border hover:ring-primary/50", isDragging && "opacity-40")}
    >
      {dropSide ? <span aria-hidden className={cn("absolute inset-y-2 w-1 rounded-full bg-primary", dropSide === "before" ? "-left-2" : "-right-2")} /> : null}
      <span role="slider" tabIndex={0} aria-label={`Bề rộng ${tile.title}`} aria-valuemin={2} aria-valuemax={12} aria-valuenow={w}
            aria-valuetext={`${w}/12 cột`} title="Kéo mép để đổi bề rộng (← → rồi Enter bằng bàn phím)" onPointerDown={startResize} onKeyDown={keyResize}
            onBlur={() => setLive(null)}
            className="group/edge absolute inset-y-3 -right-1.5 z-10 flex w-3 cursor-col-resize touch-none items-center justify-center outline-none @max-[480px]:hidden">
        <span className={cn("h-10 w-1 rounded-full transition-colors", live !== null ? "bg-primary" : "bg-border group-hover/edge:bg-primary/60 group-focus-visible/edge:bg-primary")} />
      </span>
      {live !== null ? (
        <span className="t12m pointer-events-none absolute right-3 top-1.5 z-10 rounded-md bg-primary px-1.5 py-0.5 text-primary-foreground tabular-nums">{live}/12</span>
      ) : null}
      <div className="flex min-w-0 items-center gap-1">
        <button type="button" ref={setActivatorNodeRef} {...attributes} {...listeners} aria-label={`Kéo để đổi chỗ: ${tile.title}`} title="Kéo để đổi chỗ"
                className="grid size-7 shrink-0 cursor-grab touch-none place-items-center rounded-md text-muted-foreground hover:bg-accent active:cursor-grabbing">
          <GripVertical className="size-4" aria-hidden />
        </button>
        <button type="button" onClick={onSelect} className="t12m min-w-0 flex-1 truncate text-left text-muted-foreground hover:text-foreground @max-[200px]/tile:sr-only" title="Chọn trường cho khối này">
          {tile.title}
        </button>
        {onRevise ? (
          <button type="button" onClick={onRevise} aria-label="Nhờ AI sửa khối này" title="Nhờ AI sửa khối này" className="@max-[260px]/tile:hidden grid size-7 shrink-0 place-items-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground">
            <Pencil className="size-3.5" aria-hidden />
          </button>
        ) : null}
        {onRemove ? (
          confirm ? (
            <button type="button" onClick={onRemove} onBlur={() => setConfirm(false)} className="t12m h-7 shrink-0 rounded-md bg-bad/10 px-2 text-bad hover:bg-bad/20">Bỏ khối?</button>
          ) : (
            <button type="button" onClick={() => setConfirm(true)} aria-label="Bỏ khối này" title="Bỏ khối này" className="grid size-7 shrink-0 place-items-center rounded-md text-muted-foreground hover:bg-accent hover:text-bad">
              <Trash2 className="size-3.5" aria-hidden />
            </button>
          )
        ) : null}
      </div>
      <div role="button" tabIndex={0} onClick={onSelect} onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onSelect()}
           className="flex min-w-0 flex-1 cursor-pointer flex-col [&>*]:flex-1" aria-label={`Chọn khối ${tile.title}`}>
        {children}
      </div>
    </div>
  );
}
