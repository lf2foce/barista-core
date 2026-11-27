"use client";

import { ChevronDown, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { arrows, barBox, barText, type Arrow, flatten, HEAD_H, itemLabel, rangeOf, ROW_H, weekTicks, xOf, type FlatRow, type Range } from "@/lib/timeline";
import { cardToneVar } from "@/lib/format";
import type { OpenRef, TimelineData, TimelineItem } from "@/lib/types";
import { cn } from "@/lib/utils";

const LABEL_W = 232;
const BAR_H = 20;   // thanh việc cao h-5

/**
 * Dòng thời gian / Gantt (0222). Server dựng nhóm, thanh, mốc, ngày nghỉ, mũi tên — chỗ này chỉ vẽ. Cột trái = tên (đọc được bằng bàn phím / trình đọc
 * màn hình, mỗi dòng nói đủ người · ngày · trạng thái); cột phải = trục ngày cuộn ngang. Chỉ xem (kéo thả để sau). Màu chỉ qua biến CSS của giao diện.
 */
export function TimelineBlock({ data, onOpen }: { data: TimelineData; onOpen: (o: OpenRef) => void }) {
  const [opened, setOpened] = useState<Record<string, boolean>>({});
  const [hot, setHot] = useState("");   // việc đang rê chuột (cột tên hoặc thanh) ⇒ mũi tên "chờ việc" của nó đậm lên, còn lại mờ
  const r = rangeOf(data);
  const rows = flatten(data.groups, opened);
  const height = rows.length * ROW_H;
  const ticks = weekTicks(r);
  const todayX = data.today ? xOf(r, data.today) + r.px / 2 : null;
  const lines = arrows(rows, r);
  const toggle = (key: string, open: boolean) => setOpened((cur) => ({ ...cur, [key]: !open }));

  if (data.groups.every((g) => g.items.length === 0 && !g.start)) {
    return <p className="t12 rounded-xl bg-surface2 px-3 py-2.5 text-muted-foreground">Chưa có việc nào để vẽ.</p>;
  }
  return (
    <figure className="flex min-w-0 flex-col gap-2" aria-label={data.title || "Dòng thời gian"}>
      <div className="flex overflow-hidden rounded-xl border border-hairline bg-surface">
        {/* cột tên — đứng yên khi cuộn ngang */}
        <div className="shrink-0 border-r border-hairline" style={{ width: LABEL_W }}>
          <div className="t12m flex items-end border-b border-hairline px-3 pb-1.5 text-muted-foreground" style={{ height: HEAD_H }}>
            {data.title || "Việc"}
          </div>
          <ul>
            {rows.map((row) => (row.kind === "group" ? <GroupLabel key={`g${row.group.key}`} row={row} onToggle={toggle} onOpen={onOpen} /> : <ItemLabel key={row.item.id} item={row.item} onOpen={onOpen} onHot={setHot} />))}
          </ul>
        </div>
        {/* trục ngày */}
        <div className="min-w-0 flex-1 overflow-x-auto" tabIndex={0} role="group" aria-label="Trục thời gian, cuộn ngang để xem thêm">
          <div className="relative" style={{ width: r.width, height: HEAD_H + height }}>
            <Off r={r} off={data.off} />
            {/* đầu trục: nhãn tuần + mốc chung */}
            <div className="absolute inset-x-0 top-0 border-b border-hairline" style={{ height: HEAD_H }} aria-hidden>
              {ticks.map((t) => (
                <span key={t.x} className={cn("t12 absolute top-0 h-full border-l border-hairline pl-1 pt-1 tnum", t.month ? "text-foreground" : "text-muted-foreground")} style={{ left: t.x }}>{t.label}</span>
              ))}
              {data.milestones.map((m) => (
                <Diamond key={m.on + m.title} x={xOf(r, m.on) + r.px / 2} y={HEAD_H - 12} tone={m.tone} title={`${m.title} ${m.on}`} />
              ))}
            </div>
            <div className="absolute inset-x-0" style={{ top: HEAD_H, height }}>
              {rows.map((row) => (row.kind === "group" ? <GroupTrack key={`g${row.group.key}`} row={row} r={r} /> : <ItemTrack key={row.item.id} item={row.item} y={row.y} r={r} onOpen={onOpen} onHot={setHot} />))}
              {data.milestones.map((m) => (
                <span key={`v${m.on}${m.title}`} aria-hidden className="absolute top-0 h-full border-l border-dashed" style={{ left: xOf(r, m.on) + r.px / 2, borderColor: cardToneVar(m.tone) }} />
              ))}
              {/* mũi tên "chờ việc trước" LUÔN vẽ (nhìn là biết việc nào chờ việc nào), xám vừa đủ thấy; rê chuột vào một việc ⇒ mũi tên của nó tô xanh, vẽ sau cùng */}
              {lines.length ? (
                <svg className="pointer-events-none absolute inset-0" width={r.width} height={height} aria-hidden>
                  <defs>
                    <marker id="tlarrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
                      <path d="M0,0 L8,4 L0,8 z" fill="var(--muted-foreground)" />
                    </marker>
                    <marker id="tlarrowhot" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
                      <path d="M0,0 L8,4 L0,8 z" fill="var(--primary)" />
                    </marker>
                  </defs>
                  {lines.filter((a) => a.pre !== hot && a.next !== hot).map((a) => (
                    <path key={`${a.pre}>${a.next}`} d={arrowPath(a)} fill="none" stroke="var(--muted-foreground)" strokeOpacity="0.5" strokeWidth="1.25" markerEnd="url(#tlarrow)" />
                  ))}
                  {hot ? lines.filter((a) => a.pre === hot || a.next === hot).map((a) => (
                    <path key={`hot${a.pre}>${a.next}`} d={arrowPath(a)} fill="none" stroke="var(--primary)" strokeWidth="1.75" markerEnd="url(#tlarrowhot)" />
                  )) : null}
                </svg>
              ) : null}
            </div>
            {todayX !== null && data.today >= data.start && data.today <= data.end ? (
              <span aria-hidden className="absolute top-0 w-px bg-bad" style={{ left: todayX, height: HEAD_H + height }} />
            ) : null}
          </div>
        </div>
      </div>
      <figcaption className="t12 flex flex-wrap items-center gap-x-4 gap-y-1 text-muted-foreground">
        <span className="flex items-center gap-1.5"><span aria-hidden className="inline-block h-3 w-px bg-bad" />Hôm nay</span>
        <span className="flex items-center gap-1.5"><span aria-hidden className="inline-block h-3 w-4 rounded-sm bg-surface2 ring-1 ring-hairline" />Ngày nghỉ</span>
        <span className="flex items-center gap-1.5"><span aria-hidden className="inline-block size-2.5 rotate-45 bg-[var(--cold)]" />Mốc</span>
        {lines.length ? <span className="flex items-center gap-1.5"><span aria-hidden className="inline-block h-0 w-4 border-t border-muted-foreground" />Chờ việc trước (rê chuột vào một việc để tô rõ)</span> : null}
        {data.note ? <span>{data.note}</span> : null}
      </figcaption>
    </figure>
  );
}

/** Mũi tên "chờ việc trước" (anh Anh chọn 27/11 — gọn hơn kiểu MS Project đi vòng):
 *  * việc sau bắt đầu NGAY sau / chồng lên việc trước (hai đầu trùng nhau theo chiều ngang — hay gặp nhất) ⇒ đi thẳng xuống MÉP TRÊN thanh việc sau, ngay sau
 *    đầu thanh;
 *  * việc sau ở xa ⇒ ra khỏi cuối thanh một chút, đi xuống (lên) hàng việc sau, rẽ ngang vào đầu thanh. */
function arrowPath(a: Arrow): string {
  const { from, to } = a;
  const down = to.y >= from.y ? 1 : -1;
  if (to.x >= from.x + 16) return `M${from.x},${from.y} H${from.x + 8} V${to.y} H${to.x}`;
  const x = to.x + 6;
  const edge = to.y - down * (BAR_H / 2 + 1);
  return x > from.x ? `M${from.x},${from.y} H${x} V${edge}` : `M${x},${from.y + down * (BAR_H / 2 + 1)} V${edge}`;
}

function GroupLabel({ row, onToggle, onOpen }: { row: Extract<FlatRow, { kind: "group" }>; onToggle: (key: string, open: boolean) => void; onOpen: (o: OpenRef) => void }) {
  const g = row.group;
  return (
    <li className="flex items-center gap-1 border-b border-hairline bg-surface2/60 pl-1.5 pr-2" style={{ height: ROW_H }}>
      <button type="button" aria-expanded={row.open} aria-label={`${row.open ? "Gấp" : "Mở"} nhóm ${g.title}`} onClick={() => onToggle(g.key, row.open)}
              className="grid size-6 shrink-0 place-items-center rounded-md text-muted-foreground hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60">
        <ChevronDown className={cn("size-3.5 transition-transform", !row.open && "-rotate-90")} aria-hidden />
      </button>
      {g.open ? (
        <button type="button" onClick={() => onOpen(g.open!)} className="t12m min-w-0 flex-1 truncate rounded-sm text-left hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60" title={g.subtitle ? `${g.title} — ${g.subtitle}` : g.title}>{g.title}</button>
      ) : (
        <span className="t12m min-w-0 flex-1 truncate" title={g.subtitle ? `${g.title} — ${g.subtitle}` : g.title}>{g.title}</span>
      )}
      <span className="t12 tnum shrink-0 text-muted-foreground">{g.items.length}</span>
    </li>
  );
}

function ItemLabel({ item, onOpen, onHot }: { item: TimelineItem; onOpen: (o: OpenRef) => void; onHot: (id: string) => void }) {
  const inner = (
    <>
      <span className="t12 min-w-0 flex-1 truncate">{item.title}</span>
      {item.flag ? <TriangleAlert className="size-3.5 shrink-0 text-warn" aria-label={item.flag} /> : null}
    </>
  );
  return (
    <li className="border-b border-hairline" style={{ height: ROW_H }} onMouseEnter={() => onHot(item.id)} onMouseLeave={() => onHot("")}>
      {item.open ? (
        <button type="button" onClick={() => onOpen(item.open!)} aria-label={itemLabel(item)} title={itemLabel(item)}
                className="flex h-full w-full items-center gap-1.5 pl-8 pr-2 text-left hover:bg-accent/60 focus-visible:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/60">
          {inner}
        </button>
      ) : (
        <div className="flex h-full items-center gap-1.5 pl-8 pr-2 text-muted-foreground" title={itemLabel(item)} aria-label={itemLabel(item)} role="group">{inner}</div>
      )}
    </li>
  );
}

function Off({ r, off }: { r: Range; off: TimelineData["off"] }) {
  return (
    <>
      {off.map((o) => (
        <span key={o.from + o.kind} aria-hidden className={cn("absolute inset-y-0", o.kind === "holiday" ? "bg-warn/15" : "bg-surface2/80")}
              style={{ left: xOf(r, o.from), width: Math.max(r.px, xOf(r, o.to) + r.px - xOf(r, o.from)) }} />
      ))}
    </>
  );
}

function GroupTrack({ row, r }: { row: Extract<FlatRow, { kind: "group" }>; r: Range }) {
  const g = row.group;
  return (
    <div aria-hidden className="absolute inset-x-0 border-b border-hairline" style={{ top: row.y, height: ROW_H }}>
      {g.start && g.end && !row.open ? (
        <span className="absolute top-1/2 h-2 -translate-y-1/2 rounded-full bg-foreground/20" style={{ left: barBox(r, g.start, g.end).left, width: barBox(r, g.start, g.end).width }} />
      ) : null}
      {g.milestones.map((m) => (
        <Diamond key={m.on} x={xOf(r, m.on) + r.px / 2} y={ROW_H / 2 - 6} tone={m.tone} title={`${m.title} ${m.on}`} />
      ))}
    </div>
  );
}

function ItemTrack({ item, y, r, onOpen, onHot }: { item: TimelineItem; y: number; r: Range; onOpen: (o: OpenRef) => void; onHot: (id: string) => void }) {
  const color = cardToneVar(item.tone);
  const noEnd = item.end === null;
  const box = barBox(r, item.start, item.end);
  const text = barText(item);
  return (
    <div className="absolute inset-x-0 border-b border-hairline" style={{ top: y, height: ROW_H }} aria-hidden title={itemLabel(item)}
         onMouseEnter={() => onHot(item.id)} onMouseLeave={() => onHot("")}>
      {item.kind === "milestone" ? (
        <Diamond x={box.left + box.width / 2} y={ROW_H / 2 - 6} tone={item.tone} title={itemLabel(item)} />
      ) : (
        <>
          <button type="button" tabIndex={-1} disabled={!item.open} onClick={() => item.open && onOpen(item.open)} title={itemLabel(item)}
                  className={cn("absolute top-1/2 h-5 -translate-y-1/2 rounded-md disabled:cursor-default", noEnd && "border border-dashed bg-transparent")}
                  style={{ left: box.left, width: box.width, ...(noEnd ? { borderColor: color } : { backgroundColor: color, opacity: item.tone === "muted" ? 0.45 : 0.9 }) }} />
          <span className="t12 absolute top-1/2 -translate-y-1/2 whitespace-nowrap text-muted-foreground" style={{ left: box.left + box.width + 14 }}>{text}</span>
        </>
      )}
    </div>
  );
}

function Diamond({ x, y, tone, title }: { x: number; y: number; tone: string; title: string }) {
  return (
    <span className="absolute size-3 rotate-45" title={title} style={{ left: x - 6, top: y, backgroundColor: cardToneVar(tone) }} />
  );
}
