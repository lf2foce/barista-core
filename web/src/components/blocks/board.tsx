"use client";

import { MoveRight } from "lucide-react";
import { useState } from "react";
import { columnOf, visibleColumns } from "@/lib/board";
import { cardToneVar } from "@/lib/format";
import type { BoardCard, BoardColumn, BoardMove, OpenRef } from "@/lib/types";
import { cn } from "@/lib/utils";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";

/**
 * Bảng kanban (việc theo trạng thái, hồ sơ theo chặng). Kéo thẻ bằng HTML5 drag & drop, hoặc nút "Chuyển sang…" (bàn phím / màn cảm ứng).
 * Thẻ nhảy cột ngay (lạc quan); lệnh lỗi ⇒ về chỗ cũ kèm lý do server trả. `onMove` trả false ⇒ không áp (đã mở form hoặc lỗi).
 */
export function Board({
  columns,
  move,
  onMove,
  onOpen,
}: {
  columns: BoardColumn[];
  move: BoardMove | null;
  onMove: (card: BoardCard, column: string) => Promise<boolean>;
  onOpen: (o: OpenRef) => void;
}) {
  const [moved, setMoved] = useState<Record<string, string>>({});
  const [target, setTarget] = useState<string | null>(null);
  const [lastColumns, setLastColumns] = useState(columns);
  if (lastColumns !== columns) {
    // trang tải lại ⇒ dữ kiện server thay chỗ lạc quan
    setLastColumns(columns);
    setMoved({});
  }
  const canMove = !!move;
  const shown = visibleColumns(columns, moved);

  async function go(card: BoardCard, col: string) {
    const needsForm = !!move?.form_columns?.includes(col);
    if (!needsForm) setMoved((m) => ({ ...m, [card.id]: col }));
    const ok = await onMove(card, col);
    if (!ok && !needsForm)
      setMoved((m) => {
        const n = { ...m };
        delete n[card.id];
        return n;
      });
  }

  return (
    <div className="flex items-start gap-3 overflow-x-auto pb-2" role="list" aria-label="Bảng theo cột">
      {shown.map((col) => (
        <section
          key={col.key}
          role="listitem"
          aria-label={`${col.title}, ${col.items.length} thẻ`}
          className={cn("flex w-[248px] shrink-0 flex-col gap-2 rounded-xl border-[1.5px] p-2.5 transition-colors", target === col.key ? "border-primary/60 bg-primary/10" : "border-transparent bg-surface2")}
          onDragOver={(e) => {
            if (!canMove) return;
            e.preventDefault();
            e.dataTransfer.dropEffect = "move";
            if (target !== col.key) setTarget(col.key);
          }}
          onDragLeave={(e) => {
            if (!e.currentTarget.contains(e.relatedTarget as Node | null) && target === col.key) setTarget(null);
          }}
          onDrop={(e) => {
            e.preventDefault();
            setTarget(null);
            const id = e.dataTransfer.getData("text/plain");
            const card = columns.flatMap((c) => c.items).find((c) => c.id === id);
            if (card && columnOf(columns, moved, id) !== col.key) void go(card, col.key);
          }}
        >
          <header className="flex items-center gap-1.5">
            <span className="size-2 shrink-0 rounded-full" style={{ background: cardToneVar(col.tone) }} />
            <h4 className="t14m truncate">{col.title}</h4>
            <span className="t12 tnum text-muted-foreground">{col.items.length}</span>
          </header>
          {col.subtitle ? <p className="t12 truncate text-muted-foreground">{col.subtitle}</p> : null}
          <div className="flex flex-col gap-2">
            {col.items.map((card) => (
              <article
                key={card.id}
                draggable={canMove}
                onDragStart={(e) => {
                  e.dataTransfer.setData("text/plain", card.id);
                  e.dataTransfer.effectAllowed = "move";
                }}
                className={cn("group relative flex gap-2 rounded-[10px] border border-hairline bg-surface p-2.5", canMove && "cursor-grab active:cursor-grabbing")}
              >
                <span className="w-[3px] shrink-0 rounded-sm" style={{ background: cardToneVar(card.tone) }} />
                <div className="flex min-w-0 flex-1 flex-col gap-1">
                  {card.open ? (
                    <button type="button" className="t14 line-clamp-3 text-left outline-none hover:underline focus-visible:underline" onClick={() => onOpen(card.open!)}>
                      {card.title}
                    </button>
                  ) : (
                    <p className="t14 line-clamp-3">{card.title}</p>
                  )}
                  {card.subtitle ? <p className="t12 line-clamp-2 text-muted-foreground">{card.subtitle}</p> : null}
                  {card.badge ? (
                    <span className="t12m w-fit rounded-full px-1.5 py-0.5" style={{ color: cardToneVar(card.tone), background: `color-mix(in oklab, ${cardToneVar(card.tone)} 12%, transparent)` }}>
                      {card.badge}
                    </span>
                  ) : null}
                </div>
                {canMove ? (
                  <DropdownMenu>
                    <DropdownMenuTrigger className="absolute top-1.5 right-1.5 rounded-md p-1 text-muted-foreground opacity-0 outline-none hover:bg-accent focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-ring group-hover:opacity-100 data-popup-open:opacity-100" aria-label={`Chuyển thẻ “${card.title}” sang cột khác`}>
                      <MoveRight className="size-3.5" />
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="w-52">
                      {columns
                        .filter((c) => c.key !== columnOf(columns, moved, card.id))
                        .map((c) => (
                          <DropdownMenuItem key={c.key} onClick={() => void go(card, c.key)}>
                            Chuyển sang “{c.title}”
                          </DropdownMenuItem>
                        ))}
                    </DropdownMenuContent>
                  </DropdownMenu>
                ) : null}
              </article>
            ))}
            {col.items.length === 0 ? <p className="t12 grid min-h-11 place-items-center text-muted-foreground/70">{canMove ? "Kéo thẻ vào đây" : "Trống"}</p> : null}
          </div>
        </section>
      ))}
    </div>
  );
}
