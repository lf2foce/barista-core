"use client";

import { ChevronLeft, ChevronRight, ExternalLink, Plus, Repeat, Sparkles, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import {
  agendaDays, allDayIn, createPayload, creatorFor, dayOf, hhmm, layout, minutesOf, movePayload, shortDay, snap, toneVar, unique,
} from "@/lib/calendar";
import { decodeAction } from "@/lib/decode";
import { newKey } from "@/lib/idempotency";
import type { CalendarCreator, CalendarData, CalendarItem, CalendarLane, Input, Option } from "@/lib/types";
import { cn } from "@/lib/utils";
import { actionClass, Initials, Spinner } from "@/components/primitives";
import { useDesk } from "@/components/desk/desk-context";
import { useScreenFilters } from "@/components/pages/filter-context";

const HOUR = 52;   // px / giờ
const GUTTER = 52;

type Col = { key: string; day: string; lane: string; title: string; sub: string; laneInfo?: CalendarLane; off: string };
type Draft = { day: string; lane: string; start: number; end: number; x: number; y: number; seq?: number; title?: string; creator?: string; subject?: { value: string; label: string; hint?: string };
               place?: string; guessed?: boolean; dayGuessed?: boolean };
type Drag =
  | { kind: "create"; col: Col; from: number; to: number; x: number; y: number }
  | { kind: "move"; item: CalendarItem; col: Col; day: string; start: number; len: number; grab: number; moved: boolean; px: number; py: number };

const STATUS: Record<string, string> = { tentative: "Chờ xác nhận", maybe: "Bận (chưa chắc)", busy: "Bận", done: "Đã xong", marker: "Mốc hạn" };

/** Màn Lịch (0240): Ngày (người cạnh nhau) · Tuần · Lịch biểu. Server khai mọi thứ (mục, màu, ai kéo được gì, cách tạo); khối chỉ vẽ + chạy lệnh server khai. */
export function CalendarBlock({ block, onChanged }: { block: CalendarData; onChanged?: () => void }) {
  const desk = useDesk();
  const filters = useScreenFilters();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [detail, setDetail] = useState<{ item: CalendarItem; x: number; y: number } | null>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [busy, setBusy] = useState(false);
  const [drift, setDrift] = useState(0);
  const [wide, setWide] = useState(false);
  useEffect(() => {
    const m = window.matchMedia("(min-width: 900px)");
    const f = () => setWide(m.matches);
    f();
    m.addEventListener("change", f);
    return () => m.removeEventListener("change", f);
  }, []);
  const scroller = useRef<HTMLDivElement>(null);
  const press = useRef<{ x: number; y: number; t: number } | null>(null);
  const clickTimer = useRef<number | null>(null);
  const seq = useRef(0);
  const openFull = (item: CalendarItem) => {
    if (clickTimer.current) { clearTimeout(clickTimer.current); clickTimer.current = null; }
    setDetail(null);
    if (item.open) desk.openPage(item.open.query, item.open.input, item.open.title ?? item.title);
  };
  const creators = block.create?.creators ?? [];
  const canCreate = creators.length > 0;
  const nav = block.nav;
  const setFilter = (k: string, v: string | undefined) => filters?.set(k, v);

  // vạch "bây giờ": giờ server gửi + thời gian đã trôi kể từ lúc nhận (không đọc đồng hồ máy — giờ công ty có thể khác múi máy)
  useEffect(() => {
    const base = Date.now();
    const t = setInterval(() => setDrift((Date.now() - base) / 60000), 30000);
    return () => { clearInterval(t); setDrift(0); };
  }, [block.now]);
  const now = minutesOf(block.now) + drift;

  const [h0, h1] = block.hours;
  const height = (h1 - h0) * HOUR;
  const y = (mins: number) => ((mins - h0 * 60) * HOUR) / 60;

  // cột: chế độ Ngày có người ⇒ mỗi người một cột; còn lại mỗi ngày một cột
  const cols: Col[] = useMemo(() => {
    if (block.view === "day" && block.lanes.length) {
      const d = block.days[0];
      return block.lanes.map((l) => ({ key: l.key, day: block.start, lane: l.key, title: l.title, sub: l.subtitle, laneInfo: l, off: d?.off ?? "" }));
    }
    return block.days.map((d) => ({ key: d.on, day: d.on, lane: "", title: d.label, sub: "", off: d.off }));
  }, [block]);
  const items = useMemo(() => (block.view === "day" && block.lanes.length ? block.items : unique(block.items)), [block]);
  const itemsOf = (c: Col) => items.filter((i) => (c.lane ? i.lane === c.lane : true));
  const hasAllDay = cols.some((c) => allDayIn(itemsOf(c), c.day).length > 0);
  const timedCount = items.filter((i) => !i.all_day).length;

  // mở màn: cuộn tới giờ hiện tại (hôm nay) hoặc mục sớm nhất
  useEffect(() => {
    const el = scroller.current;
    if (!el || block.view === "agenda") return;
    const first = items.filter((i) => !i.all_day).map((i) => minutesOf(i.start)).sort((a, b) => a - b)[0];
    const target = block.today >= block.start && block.today <= block.end ? Math.min(now - 60, first ?? now) : (first ?? 8 * 60);
    el.scrollTop = Math.max(0, y(target) - 16);
    // chỉ khi đổi khung đang xem
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [block.start, block.end, block.view]);

  const minsAt = (el: HTMLElement, clientY: number) => {
    const r = el.getBoundingClientRect();
    return h0 * 60 + ((clientY - r.top) / HOUR) * 60;
  };

  function colAt(x: number, yy: number): { col: Col; el: HTMLElement } | null {
    const el = document.elementFromPoint(x, yy)?.closest<HTMLElement>("[data-cal-col]");
    const col = el ? cols.find((c) => c.key === el.dataset.calCol) : undefined;
    return el && col ? { col, el } : null;
  }

  function onColDown(e: React.PointerEvent<HTMLDivElement>, col: Col) {
    if (e.button !== 0 || (e.target as HTMLElement).closest("[data-cal-item]")) return;
    setDetail(null);
    const m = snap(minsAt(e.currentTarget, e.clientY), 30);
    if (!canCreate) return;
    if (e.pointerType !== "mouse") {   // cảm ứng: CHẠM GIỮ ⇒ thêm lịch một giờ; chạm / vuốt là cuộn
      const st = Math.floor(m / 30) * 30;
      const at = { x: e.clientX, y: e.clientY };
      if (press.current) clearTimeout(press.current.t);
      press.current = { ...at, t: window.setTimeout(() => { press.current = null; setDraft({ day: col.day, lane: col.lane, start: st, end: st + 60, ...at, seq: ++seq.current }); }, 450) };
      return;
    }
    e.currentTarget.setPointerCapture(e.pointerId);
    const from = Math.floor(minsAt(e.currentTarget, e.clientY) / 15) * 15;
    setDrag({ kind: "create", col, from, to: from + 15, x: e.clientX, y: e.clientY });
  }

  function onColMove(e: React.PointerEvent<HTMLDivElement>) {
    if (press.current && Math.abs(e.clientX - press.current.x) + Math.abs(e.clientY - press.current.y) > 8) {
      clearTimeout(press.current.t);
      press.current = null;
    }
    if (!drag) return;
    if (drag.kind === "create") {
      const to = snap(minsAt(e.currentTarget, e.clientY), 15);
      setDrag({ ...drag, to: Math.max(drag.from + 15, to), x: e.clientX, y: e.clientY });
    }
  }

  function onColUp(e: React.PointerEvent<HTMLDivElement>) {
    if (press.current) {   // nhấc tay trước khi đủ giữ: chỉ là chạm
      clearTimeout(press.current.t);
      press.current = null;
    }
    if (drag?.kind !== "create") return;
    const dragged = drag.to - drag.from > 15;
    const start = dragged ? drag.from : Math.floor(drag.from / 30) * 30;
    const end = dragged ? drag.to : start + 60;
    setDrag(null);
    setDraft({ day: drag.col.day, lane: drag.col.lane, start, end: Math.min(end, 24 * 60), x: e.clientX, y: e.clientY, seq: ++seq.current });
  }

  /** Nhấn đúp ô giờ trống (như Teams / Outlook) ⇒ thêm lịch một giờ tại đó. Bấm / kéo vẫn như cũ. */
  function onColDouble(e: React.MouseEvent<HTMLDivElement>, col: Col) {
    if (!canCreate || (e.target as HTMLElement).closest("[data-cal-item]")) return;
    const st = Math.floor(minsAt(e.currentTarget, e.clientY) / 30) * 30;
    setDrag(null);
    setDraft({ day: col.day, lane: col.lane, start: st, end: Math.min(st + 60, 24 * 60), x: e.clientX, y: e.clientY, seq: ++seq.current });
  }

  function onItemDown(e: React.PointerEvent<HTMLElement>, item: CalendarItem, col: Col, start: number, end: number) {
    e.stopPropagation();
    if (e.button !== 0) return;
    if (!item.move || e.pointerType !== "mouse") return;   // không kéo được ⇒ chỉ bấm mở
    e.currentTarget.setPointerCapture(e.pointerId);
    const colEl = (e.currentTarget.closest("[data-cal-col]") as HTMLElement) ?? e.currentTarget;
    setDrag({ kind: "move", item, col, day: col.day, start, len: end - start, grab: minsAt(colEl, e.clientY) - start, moved: false, px: e.clientX, py: e.clientY });
  }

  function onItemMove(e: React.PointerEvent<HTMLElement>) {
    if (drag?.kind !== "move") return;
    if (!drag.moved && Math.abs(e.clientY - drag.py) + Math.abs(e.clientX - drag.px) < 5) return;
    const hit = colAt(e.clientX, e.clientY);
    // Ngày có người: chỉ đổi giờ (dời sang người khác là lệnh khác); Tuần: đổi cả ngày
    const target = hit && (block.view !== "day" || !block.lanes.length || hit.col.lane === drag.col.lane) ? hit : null;
    const colEl = target?.el ?? (document.querySelector(`[data-cal-col="${CSS.escape(drag.col.key)}"]`) as HTMLElement | null);
    if (!colEl) return;
    const start = Math.max(0, Math.min(24 * 60 - drag.len, snap(minsAt(colEl, e.clientY) - drag.grab, 15)));
    setDrag({ ...drag, moved: true, start, day: target?.col.day ?? drag.day, col: target?.col ?? drag.col });
  }

  async function onItemUp(e: React.PointerEvent<HTMLElement>, item: CalendarItem) {
    e.stopPropagation();
    const d = drag?.kind === "move" ? drag : null;
    setDrag(null);
    if (!d || !d.moved) {
      // bấm một lần ⇒ hộp tóm tắt (chờ một nhịp để phân biệt với nhấn đúp ⇒ mở thẳng chi tiết đầy đủ)
      const at = { x: e.clientX, y: e.clientY };
      if (clickTimer.current) clearTimeout(clickTimer.current);
      clickTimer.current = window.setTimeout(() => { clickTimer.current = null; setDetail({ item, ...at }); }, e.detail === 0 ? 0 : 230);
      return;
    }
    const orig = minutesOf(item.start);
    if (d.day === dayOf(item.start) && d.start === orig) return;
    setBusy(true);
    try {
      const r = await api.command(item.move!.command, movePayload(item.move!, d.day, d.start, d.start + d.len), newKey());
      if (r.status === "ok") {
        const out = r.output as { summary?: string; warnings?: string[] } | null;
        desk.show([out?.summary || `Đã dời sang ${hhmm(d.start)} ${shortDay(d.day)}`, ...(out?.warnings ?? [])].join(". "));
        onChanged?.();
      } else desk.show(r.error || `Không dời được (${r.status})`, true);
    } catch (err) {
      desk.show(err instanceof Error ? err.message : "Không dời được", true);
    } finally {
      setBusy(false);
    }
  }

  function addNow() {
    const day = block.today >= block.start && block.today <= block.end ? block.today : block.start;
    const s = day === block.today ? Math.min(23 * 60, Math.ceil((now + 1) / 60) * 60) : 9 * 60;
    const lane = block.lanes.find((l) => l.me)?.key ?? "";
    setDraft({ day, lane, start: s, end: s + 60, x: window.innerWidth / 2, y: 160, seq: ++seq.current });
  }

  const laneEmail = (lane: string) => block.lanes.find((l) => l.key === lane)?.email || block.lanes.find((l) => l.me)?.email || "";

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-3">
      <Toolbar block={block} canNav={!!filters && !!nav} onNav={setFilter} canCreate={canCreate} onAdd={addNow}
               onGuess={(g) => setDraft({ ...g, seq: ++seq.current })} understand={block.create?.understand ?? ""} />
      {block.kinds.length ? <Legend block={block} /> : null}
      {block.note ? <p className="t12 text-muted-foreground">{block.note}</p> : null}

      {block.view === "agenda" ? (
        <Agenda block={block} items={items} onOpen={(item, x, yy) => setDetail({ item, x, y: yy })} />
      ) : (
        <div ref={scroller} className={cn("relative min-h-0 overflow-auto rounded-[14px] border border-hairline bg-surface", busy && "opacity-70")}
             style={{ maxHeight: "calc(100vh - 230px)", minHeight: 360 }}>
          <div className="relative" style={{ minWidth: cols.length > 1 ? GUTTER + cols.length * 132 : undefined }}>
            {/* đầu cột: người (Ngày) hoặc ngày (Tuần) */}
            <div className="sticky top-0 z-20 flex border-b border-hairline bg-surface">
              <div className="sticky left-0 z-10 shrink-0 bg-surface" style={{ width: GUTTER }}>
                <span className="t12 block px-1.5 pt-4 text-[11px] text-muted-foreground" title={block.tz}>{block.tz === "Giờ Việt Nam" ? "GMT+7" : ""}</span>
              </div>
              {cols.map((c) => (
                <ColHead key={c.key} col={c} today={block.today} view={block.view}
                         onOpen={c.laneInfo?.open && filters ? () => { setFilter("member_email", String(c.laneInfo!.open!.member_email ?? "")); setFilter(nav?.param_view ?? "view", "week"); } : undefined}
                         onDay={block.view === "week" && filters && nav ? () => { setFilter(nav.param_on, c.day); setFilter(nav.param_view, "day"); } : undefined} />
              ))}
            </div>
            {/* hàng cả ngày: nghỉ, mốc hạn (gọi lại, hạn việc…) */}
            {hasAllDay ? (
              <div className="sticky z-10 flex border-b border-hairline bg-surface" style={{ top: 49 }}>
                <div className="t12 sticky left-0 z-10 shrink-0 bg-surface px-1.5 py-1.5 text-[11px] text-muted-foreground" style={{ width: GUTTER }}>Cả ngày</div>
                {cols.map((c) => (
                  <div key={c.key} className="flex min-w-0 flex-1 flex-col gap-0.5 border-l border-hairline p-1">
                    {allDayIn(itemsOf(c), c.day).slice(0, 3).map((i) => (
                      <button key={i.id} type="button" onClick={(e) => setDetail({ item: i, x: e.clientX, y: e.clientY })}
                              className="t12 truncate rounded-md px-1.5 py-0.5 text-left" style={chip(i.tone, i.status === "marker")} title={`${i.title}${i.subtitle ? ` — ${i.subtitle}` : ""}`}>
                        {i.title}
                      </button>
                    ))}
                    {allDayIn(itemsOf(c), c.day).length > 3 ? <span className="t12 px-1 text-[11px] text-muted-foreground">+{allDayIn(itemsOf(c), c.day).length - 3} mục</span> : null}
                  </div>
                ))}
              </div>
            ) : null}
            {/* lưới giờ */}
            <div className="flex">
              <div className="sticky left-0 z-10 shrink-0 bg-surface" style={{ width: GUTTER, height }} aria-hidden>
                {Array.from({ length: h1 - h0 }, (_, k) => (
                  <span key={k} className="t12 tnum absolute right-2 -translate-y-1/2 text-[11px] text-muted-foreground" style={{ top: k * HOUR }}>
                    {k === 0 ? "" : `${String(h0 + k).padStart(2, "0")}:00`}
                  </span>
                ))}
              </div>
              {cols.map((c) => {
                const placed = layout(itemsOf(c), c.day);
                const isToday = c.day === block.today;
                return (
                  <div key={c.key} data-cal-col={c.key} role="grid" aria-label={`${c.title} ${c.day}`}
                       className={cn("relative min-w-0 flex-1 touch-pan-y select-none border-l border-hairline", canCreate && "cursor-cell", c.off && "bg-surface2/60")}
                       style={{ height }} onPointerDown={(e) => onColDown(e, c)} onPointerMove={onColMove} onPointerUp={onColUp}
                       onPointerCancel={() => { if (press.current) { clearTimeout(press.current.t); press.current = null; } }} onDoubleClick={(e) => onColDouble(e, c)}
                       onContextMenu={(e) => { if (press.current) e.preventDefault(); }}>
                    {c.laneInfo?.me && canCreate && placed.length === 0 && cols.length > 1 ? (
                      <p className="t12 pointer-events-none absolute inset-x-2 z-[4] rounded-lg bg-primary/8 px-2 py-1.5 text-center text-primary" style={{ top: y(Math.max(h0 * 60, 9 * 60)) + 6 }}>
                        Chưa có lịch của bạn — nhấn đúp vào giờ trống để thêm
                      </p>
                    ) : null}
                    {Array.from({ length: h1 - h0 }, (_, k) => (
                      <div key={k} className="pointer-events-none absolute inset-x-0 border-t border-hairline" style={{ top: k * HOUR }}>
                        <div className="absolute inset-x-0 border-t border-dashed border-hairline/60" style={{ top: HOUR / 2 }} />
                      </div>
                    ))}
                    {placed.map((p) => {
                      const moving = drag?.kind === "move" && drag.item.id === p.item.id && drag.moved;
                      return (
                        <ItemBox key={`${p.item.id}:${c.key}`} p={p} top={y(p.start)} h={((p.end - p.start) * HOUR) / 60} ghost={moving}
                                 onDown={(e) => onItemDown(e, p.item, c, p.start, p.end)} onMove={onItemMove} onUp={(e) => void onItemUp(e, p.item)} onDouble={() => openFull(p.item)} />
                      );
                    })}
                    {drag?.kind === "move" && drag.moved && drag.col.key === c.key ? (
                      <div className="pointer-events-none absolute inset-x-1 z-10 rounded-md border-2 px-1.5 py-1" style={{ top: y(drag.start), height: Math.max(20, (drag.len * HOUR) / 60), ...box(drag.item.tone, false) }}>
                        <span className="t12m block truncate">{hhmm(drag.start)} – {hhmm(drag.start + drag.len)}</span>
                        <span className="t12 block truncate">{drag.item.title}</span>
                      </div>
                    ) : null}
                    {drag?.kind === "create" && drag.col.key === c.key ? (
                      <div className="pointer-events-none absolute inset-x-1 z-10 rounded-md border-2 border-primary bg-primary/15 px-1.5 py-0.5" style={{ top: y(drag.from), height: ((drag.to - drag.from) * HOUR) / 60 }}>
                        <span className="t12m tnum text-primary">{hhmm(drag.from)} – {hhmm(drag.to)}</span>
                      </div>
                    ) : null}
                    {draft && draft.day === c.day && (draft.lane === c.lane || !c.lane) ? (
                      // khung xem trước của lịch đang thêm: giãn / dời theo ngày giờ đang sửa trong hộp; cột rộng (một người) chừa chỗ bên phải cho hộp
                      <div data-cal-draft className="pointer-events-none absolute z-10 rounded-md border-2 border-dashed border-primary bg-primary/10 px-1.5 py-0.5"
                           style={{ top: y(draft.start), height: Math.max(14, ((draft.end - draft.start) * HOUR) / 60), left: 4, right: cols.length === 1 && wide ? 372 : 4 }}>
                        <span className="t12m tnum text-primary">{hhmm(draft.start)} – {hhmm(draft.end)}</span>
                      </div>
                    ) : null}
                    {isToday && now >= h0 * 60 && now <= h1 * 60 ? (
                      <div className="pointer-events-none absolute inset-x-0 z-[5]" style={{ top: y(now) }} aria-label={`Bây giờ ${hhmm(now)}`}>
                        <div className="h-[2px] bg-bad" />
                        <div className="absolute -left-[5px] -top-[4px] size-2.5 rounded-full bg-bad" />
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
            {timedCount === 0 && !hasAllDay ? (
              <div className="pointer-events-none absolute inset-x-0 flex justify-center" style={{ top: 120 }}>
                <p className="t14 max-w-sm rounded-xl bg-surface/90 px-4 py-3 text-center text-muted-foreground">{block.empty}</p>
              </div>
            ) : null}
          </div>
        </div>
      )}

      {draft && canCreate ? (
        <QuickAdd key={draft.seq ?? 0} draft={draft} onEdit={(v) => setDraft((d) => (d ? { ...d, ...v } : d))} creators={creators} laneTitle={draft.lane && !block.lanes.find((l) => l.key === draft.lane)?.me ? block.lanes.find((l) => l.key === draft.lane)?.title : undefined}
                  attendee={laneEmail(draft.lane) || block.create?.me || ""} invite={block.create?.invite ?? null} onClose={() => setDraft(null)}
                  onSaved={(msg, day) => { setDraft(null); desk.show(msg); if (filters && nav && (day < block.start || day > block.end)) setFilter(nav.param_on, day); else onChanged?.(); }} />
      ) : null}
      {detail ? <Detail item={detail.item} x={detail.x} y={detail.y} kinds={block.kinds} onClose={() => setDetail(null)}
                        onDone={(msg) => { setDetail(null); desk.show(msg); onChanged?.(); }}
                        onOpen={(q, input, title) => { setDetail(null); desk.openPage(q, input, title); }} /> : null}
    </div>
  );
}

function box(tone: string, busy: boolean, status = "booked"): React.CSSProperties {
  const c = toneVar(busy ? "gray" : tone);
  if (busy)
    return { borderColor: "transparent", color: "var(--muted-foreground)", background: `repeating-linear-gradient(135deg, color-mix(in oklab, ${c} 14%, transparent) 0 6px, color-mix(in oklab, ${c} 7%, transparent) 6px 12px)` };
  if (status === "tentative")
    return { borderColor: c, borderStyle: "dashed", color: "var(--foreground)", background: `color-mix(in oklab, ${c} 8%, var(--surface))` };
  return { borderColor: "transparent", borderLeftColor: c, color: "var(--foreground)", background: `color-mix(in oklab, ${c} 16%, var(--surface))` };
}

function chip(tone: string, marker: boolean): React.CSSProperties {
  const c = toneVar(tone);
  return marker ? { color: "var(--foreground)", background: "transparent", boxShadow: `inset 0 0 0 1px color-mix(in oklab, ${c} 55%, transparent)` }
    : { color: "var(--foreground)", background: `color-mix(in oklab, ${c} 18%, var(--surface))` };
}

function ItemBox({ p, top, h, ghost, onDown, onMove, onUp, onDouble }: {
  p: ReturnType<typeof layout>[number]; top: number; h: number; ghost: boolean;
  onDown: (e: React.PointerEvent<HTMLElement>) => void; onMove: (e: React.PointerEvent<HTMLElement>) => void; onUp: (e: React.PointerEvent<HTMLElement>) => void;
  onDouble: () => void;
}) {
  const i = p.item;
  const w = 100 / p.cols;
  const short = h < 34;
  return (
    <button type="button" data-cal-item onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onDoubleClick={(e) => { e.stopPropagation(); onDouble(); }}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onUp(e as unknown as React.PointerEvent<HTMLElement>); } }}
            className={cn("absolute z-[6] flex flex-col overflow-hidden rounded-md border border-l-[3px] px-1.5 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring",
                          short ? "justify-center py-0" : "py-1", i.move ? "cursor-grab active:cursor-grabbing" : "cursor-pointer", ghost && "opacity-35",
                          i.status === "done" && "opacity-60")}
            style={{ top: top + 1, height: Math.max(18, h - 2), left: `calc(${p.col * w}% + 2px)`, width: `calc(${w}% - 4px)`, ...box(i.tone, i.busy, i.status) }}
            title={`${hhmm(p.start)} – ${hhmm(p.end)} · ${i.title}${i.subtitle ? `\n${i.subtitle}` : ""}${STATUS[i.status] ? `\n${STATUS[i.status]}` : ""}`}>
      {short ? (
        <span className="t12 block truncate leading-tight"><span className="t12m">{i.title}</span> <span className="tnum text-muted-foreground">{hhmm(p.start)}</span></span>
      ) : (
        <>
          <span className="t12m block truncate leading-tight">{i.title}</span>
          <span className="t12 tnum block truncate leading-tight text-muted-foreground">
            {hhmm(p.start)} – {hhmm(p.end)}{i.repeats ? " · lặp" : ""}{STATUS[i.status] && i.status !== "busy" && i.status !== "maybe" ? ` · ${STATUS[i.status]}` : ""}
            {h <= 60 && i.subtitle ? ` · ${i.subtitle}` : ""}
          </span>
          {h > 60 && i.subtitle ? <span className="t12 block truncate leading-tight text-muted-foreground">{i.subtitle}</span> : null}
        </>
      )}
    </button>
  );
}

function ColHead({ col, today, view, onOpen, onDay }: { col: Col; today: string; view: string; onOpen?: () => void; onDay?: () => void }) {
  const lane = col.laneInfo;
  const isToday = col.day === today && !lane;
  const inner = lane ? (
    <span className="flex min-w-0 items-center gap-2">
      <Initials name={lane.subtitle || lane.title} size={24} />
      <span className="flex min-w-0 flex-col items-start">
        <span className={cn("t12m truncate", lane.me && "text-primary")}>{lane.title}</span>
        {lane.subtitle ? <span className="t12 truncate text-[11px] text-muted-foreground">{lane.subtitle}</span> : null}
      </span>
    </span>
  ) : (
    <span className="flex flex-col items-center">
      <span className={cn("t12 text-muted-foreground", isToday && "text-primary")}>{col.title.split(" ").slice(0, -1).join(" ")}</span>
      <span className={cn("t16 tnum mt-0.5 grid h-7 min-w-7 place-items-center rounded-full px-1", isToday ? "bg-primary text-primary-foreground" : col.off ? "text-muted-foreground" : "")}>
        {col.day.slice(8, 10).replace(/^0/, "")}
      </span>
    </span>
  );
  const click = onOpen ?? onDay;
  return (
    <div className={cn("flex h-[49px] min-w-0 flex-1 items-center border-l border-hairline px-2", view === "week" && "justify-center")}>
      {click ? (
        <button type="button" onClick={click} className="min-w-0 rounded-md px-1 py-0.5 hover:bg-accent" title={onOpen ? `Xem tuần của ${lane?.subtitle || lane?.title}` : "Xem ngày này"}>{inner}</button>
      ) : inner}
    </div>
  );
}

function Toolbar({ block, canNav, onNav, canCreate, onAdd, onGuess, understand }: {
  block: CalendarData; canNav: boolean; onNav: (k: string, v: string | undefined) => void; canCreate: boolean; onAdd: () => void;
  onGuess: (d: Draft) => void; understand: string;
}) {
  const nav = block.nav;
  const isToday = block.today >= block.start && block.today <= block.end;
  const [text, setText] = useState("");
  const [thinking, setThinking] = useState(false);
  const [err, setErr] = useState("");
  async function guess() {
    if (!text.trim() || !understand) return;
    setThinking(true);
    setErr("");
    try {
      const g = await api.queryRaw<{ day: string; day_said: boolean; start: string; minutes: number; title: string; creator: string; creator_said: boolean;
                                     subject?: { value: string; label: string; hint?: string }; place?: string }>(understand, { text, on: block.start });
      const [hh, mm] = (g.start || "09:00").split(":").map(Number);
      const s = hh * 60 + mm;
      onGuess({ day: g.day, lane: block.lanes.find((l) => l.me)?.key ?? "", start: s, end: s + (g.minutes || 60), x: window.innerWidth / 2, y: 150,
                title: g.title, creator: g.creator, subject: g.subject, place: g.place, guessed: true, dayGuessed: g.day_said });
      setText("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Không hiểu câu này");
    } finally {
      setThinking(false);
    }
  }
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        {canNav && nav ? (
          <>
            <button type="button" className={actionClass("default")} disabled={isToday && block.view !== "agenda"} onClick={() => onNav(nav.param_on, undefined)}>Hôm nay</button>
            <div className="flex">
              <button type="button" aria-label="Trước" className={cn(actionClass("default"), "rounded-r-none px-2")} onClick={() => onNav(nav.param_on, nav.prev)}><ChevronLeft className="size-4" /></button>
              <button type="button" aria-label="Sau" className={cn(actionClass("default"), "-ml-px rounded-l-none px-2")} onClick={() => onNav(nav.param_on, nav.next)}><ChevronRight className="size-4" /></button>
            </div>
          </>
        ) : null}
        <h2 className="t16 min-w-0 truncate font-semibold">{block.title}</h2>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {canNav && nav?.who ? <Segment items={nav.who.options} current={nav.who.current} onPick={(k) => onNav(nav.who!.param, k)} label="Lịch của" /> : null}
          {canNav && nav?.unit ? (
            <select aria-label="Đơn vị" value={nav.unit.current} onChange={(e) => onNav(nav.unit!.param, e.target.value || undefined)}
                    className="t12m h-8 max-w-[220px] rounded-lg border border-border bg-surface px-2 outline-none focus-visible:ring-2 focus-visible:ring-ring">
              {nav.unit.options.map((o) => <option key={o.key} value={o.key}>{o.title}</option>)}
            </select>
          ) : null}
          {canNav && nav ? <Segment items={nav.views} current={block.view} onPick={(k) => onNav(nav.param_view, k)} label="Chế độ xem" /> : null}
          {canCreate ? (
            <button type="button" className={actionClass("primary")} onClick={onAdd}><Plus className="size-3.5" aria-hidden />Thêm lịch</button>
          ) : null}
        </div>
      </div>
      {canCreate && understand ? (
        <form className="flex items-center gap-2" onSubmit={(e) => { e.preventDefault(); void guess(); }}>
          <label className="flex h-9 min-w-0 flex-1 items-center gap-2 rounded-lg border border-input bg-field px-2.5 focus-within:border-ring focus-within:ring-2 focus-within:ring-ring/40">
            <Sparkles className="size-4 shrink-0 text-primary" aria-hidden />
            <input value={text} onChange={(e) => setText(e.target.value)} aria-label="Thêm nhanh bằng một câu"
                   placeholder="Thêm nhanh: “Thứ 5 2h chiều dẫn khách Lan xem Sun Feliza”"
                   className="t14 min-w-0 flex-1 bg-transparent outline-none placeholder:text-muted-foreground" />
            {thinking ? <Spinner className="size-3.5" /> : null}
          </label>
          <button type="submit" className={actionClass("default")} disabled={!text.trim() || thinking}>Đoán</button>
        </form>
      ) : null}
      {err ? <p className="t12 text-bad">{err}</p> : null}
    </div>
  );
}

function Segment({ items, current, onPick, label }: { items: { key: string; title: string }[]; current: string; onPick: (k: string) => void; label: string }) {
  return (
    <div className="flex w-fit gap-0.5 rounded-lg bg-surface2 p-0.5" role="tablist" aria-label={label}>
      {items.map((v) => (
        <button key={v.key} type="button" role="tab" aria-selected={current === v.key} onClick={() => onPick(v.key)}
                className={cn("t12m rounded-md px-3 py-1", current === v.key ? "bg-surface shadow-sm" : "text-muted-foreground hover:text-foreground")}>
          {v.title}
        </button>
      ))}
    </div>
  );
}

function Legend({ block }: { block: CalendarData }) {
  const used = new Set(block.items.map((i) => i.kind));
  const ks = block.kinds.filter((k) => used.has(k.key));
  if (!ks.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1" aria-label="Loại lịch">
      {ks.map((k) => (
        <span key={k.key} className="t12 inline-flex items-center gap-1.5 text-muted-foreground">
          <span className="size-2.5 rounded-full" style={{ background: toneVar(k.tone) }} aria-hidden />{k.label}
        </span>
      ))}
      {block.items.some((i) => i.busy) ? (
        <span className="t12 inline-flex items-center gap-1.5 text-muted-foreground"><span className="size-2.5 rounded-full" style={box("gray", true)} aria-hidden />Bận</span>
      ) : null}
    </div>
  );
}

function Agenda({ block, items, onOpen }: { block: CalendarData; items: CalendarItem[]; onOpen: (i: CalendarItem, x: number, y: number) => void }) {
  const groups = agendaDays(items, block.days.map((d) => d.on), block.today);
  const label = (day: string) => block.days.find((d) => d.on === day)?.label ?? day;
  if (!groups.some((g) => g.items.length)) return <p className="t14 rounded-[14px] border border-hairline bg-surface px-4 py-6 text-center text-muted-foreground">{block.empty}</p>;
  return (
    <div className="flex flex-col gap-4">
      {groups.map((g) => (
        <section key={g.day} className="flex flex-col gap-1.5">
          <h3 className={cn("t12m", g.day === block.today ? "text-primary" : "text-muted-foreground")}>{g.day === block.today ? `Hôm nay · ${label(g.day)}` : label(g.day)}</h3>
          {g.items.length === 0 ? <p className="t12 rounded-xl border border-hairline bg-surface px-3 py-2.5 text-muted-foreground">Hôm nay chưa có lịch.</p> : (
            <ul className="flex flex-col divide-y divide-border/60 rounded-xl border border-hairline bg-surface">
              {g.items.map((i) => (
                <li key={i.id}>
                  <button type="button" onClick={(e) => onOpen(i, e.clientX, e.clientY)} className="flex w-full items-start gap-3 px-3 py-2.5 text-left hover:bg-accent/60">
                    <span className="t12 tnum w-[86px] shrink-0 pt-0.5 text-muted-foreground">{i.all_day ? "Cả ngày" : `${hhmm(minutesOf(i.start))} – ${hhmm(minutesOf(i.end))}`}</span>
                    <span className="mt-1.5 size-2.5 shrink-0 rounded-full" style={i.busy ? box("gray", true) : { background: toneVar(i.tone) }} aria-hidden />
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className={cn("t14m truncate", i.busy && "text-muted-foreground")}>{i.title}</span>
                      {i.subtitle || STATUS[i.status] ? <span className="t12 truncate text-muted-foreground">{[i.subtitle, i.status !== "busy" ? STATUS[i.status] : ""].filter(Boolean).join(" · ")}</span> : null}
                    </span>
                    {i.repeats ? <Repeat className="mt-1 size-3.5 shrink-0 text-muted-foreground" aria-label="Lặp lại" /> : null}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      ))}
    </div>
  );
}

/** Hộp nổi gần chỗ bấm (màn rộng) / tấm dưới đáy (điện thoại). */
function Floating({ x, y, width = 340, onClose, children, label, anchor, track }: {
  x: number; y: number; width?: number; onClose: () => void; children: React.ReactNode; label: string;
  /** đặt hộp BÊN CẠNH phần tử này (khung xem trước), không che nó; ``track`` đổi ⇒ đặt lại */
  anchor?: string; track?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const [grown, setGrown] = useState(0);   // hộp cao thêm (mời người, gợi ý) ⇒ đặt lại cho không tràn đáy màn
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => setGrown(el.offsetHeight));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  useEffect(() => {
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const h = ref.current?.offsetHeight ?? 320;
    const r = anchor ? document.querySelector(anchor)?.getBoundingClientRect() : undefined;
    let left = x + 16 + width > vw - 12 ? Math.max(12, x - width - 16) : x + 16;
    let top = y - 40;
    if (r && r.height > 0) {
      left = r.right + 12 + width <= vw - 12 ? r.right + 12 : r.left - width - 12 >= 12 ? r.left - width - 12 : left;
      top = r.top;
    }
    setPos({ left, top: Math.max(12, Math.min(top, vh - h - 12)) });
  }, [x, y, width, anchor, track, grown]);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    const out = (e: PointerEvent) => { if (ref.current && !ref.current.contains(e.target as Node) && !(e.target as HTMLElement).closest?.("[data-cal-keep]")) onClose(); };
    window.addEventListener("keydown", esc);
    const t = setTimeout(() => window.addEventListener("pointerdown", out), 0);
    return () => { window.removeEventListener("keydown", esc); window.removeEventListener("pointerdown", out); clearTimeout(t); };
  }, [onClose]);
  return (
    <div ref={ref} role="dialog" aria-label={label}
         className="fixed z-50 flex flex-col gap-3 rounded-[14px] border border-hairline bg-popover p-3.5 text-popover-foreground shadow-xl max-sm:inset-x-0 max-sm:bottom-0 max-sm:!top-auto max-sm:!left-0 max-sm:max-h-[85vh] max-sm:!w-full max-sm:overflow-y-auto max-sm:rounded-b-none"
         style={{ width, left: pos?.left ?? -9999, top: pos?.top ?? -9999, maxHeight: "calc(100vh - 24px)", overflowY: "auto" }}>
      {children}
    </div>
  );
}

function QuickAdd({ draft, creators, laneTitle, attendee, invite, onClose, onSaved, onEdit }: {
  draft: Draft; creators: CalendarCreator[]; laneTitle?: string; attendee: string; invite: NonNullable<CalendarData["create"]>["invite"];
  onClose: () => void; onSaved: (msg: string, day: string) => void; onEdit: (v: { day: string; start: number; end: number }) => void;
}) {
  const [invited, setInvited] = useState<string[]>([]);
  const desk = useDesk();
  const [kind, setKind] = useState(creatorFor(creators, draft.creator ?? "")?.key ?? "");
  const c = creatorFor(creators, kind)!;
  const [title, setTitle] = useState(draft.title ?? "");
  const [day, setDay] = useState(draft.day);
  const [start, setStart] = useState(hhmm(draft.start));
  const [end, setEnd] = useState(hhmm(draft.end));
  const [subject, setSubject] = useState<{ value: string; label: string; hint?: string } | undefined>(draft.subject);
  const [place, setPlace] = useState(draft.place ?? "");
  const [err, setErr] = useState("");
  const [saving, setSaving] = useState(false);
  const key = useRef(newKey());
  const toMin = (t: string) => { const [h, m] = t.split(":").map(Number); return (h || 0) * 60 + (m || 0); };
  const payload = () => createPayload(c, { day, start: toMin(start), end: Math.max(toMin(end), toMin(start) + 15), title, subject: subject?.value, place, attendees: [attendee, ...invited].filter(Boolean) });
  // khung xem trước trên lưới theo đúng ngày / giờ đang sửa
  useEffect(() => {
    const a = toMin(start);
    const b = Math.max(toMin(end), a + 15);
    if (/^\d{4}-\d{2}-\d{2}$/.test(day) && /^\d{2}:\d{2}/.test(start)) onEdit({ day, start: a, end: Math.min(b, 24 * 60) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [day, start, end]);
  const missing = c.subject?.required && !subject ? `Chọn ${c.subject.label.toLowerCase()}` : !c.subject && !title.trim() ? "Nhập tiêu đề" : "";

  async function save() {
    if (missing) { setErr(missing); return; }
    setSaving(true);
    setErr("");
    try {
      const r = await api.command(c.command, payload(), key.current);
      if (r.status === "ok") {
        const out = r.output as { summary?: string; warnings?: string[] } | null;
        onSaved([out?.summary || "Đã thêm lịch", ...(out?.warnings ?? [])].join(". "), day);
      } else if (r.status === "pending_approval") onSaved("Đã gửi chờ duyệt", day);
      else {
        setErr(r.error || `Không thêm được (${r.status})`);
        key.current = newKey();
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Không thêm được");
      key.current = newKey();
    } finally {
      setSaving(false);
    }
  }

  const guessed = (on: boolean) => (draft.guessed && on ? <Sparkles className="size-3 shrink-0 text-primary" aria-label="Máy đoán" /> : null);
  return (
    <Floating x={draft.x} y={draft.y} onClose={onClose} label="Thêm lịch" anchor="[data-cal-draft]" track={`${day}${start}${end}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="flex flex-col">
          <span className="t14m">Thêm lịch</span>
          {draft.guessed ? <span className="t12 text-muted-foreground">Máy đoán từ câu của bạn — sửa chỗ chưa đúng rồi bấm Lưu.</span> : null}
        </div>
        <button type="button" aria-label="Đóng" onClick={onClose} className="rounded-md p-1 text-muted-foreground hover:bg-accent"><X className="size-4" /></button>
      </div>
      <input autoFocus value={title} onChange={(e) => setTitle(e.target.value)} onKeyDown={(e) => e.key === "Enter" && void save()}
             placeholder={c.subject ? "Ghi chú (vd dẫn khách xem căn mẫu)" : "Tiêu đề (vd Họp đội)"} aria-label="Tiêu đề"
             className="t14 h-9 rounded-lg border border-input bg-field px-2.5 outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40" />
      <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Loại lịch">
        {creators.map((x) => (
          <button key={x.key} type="button" role="radio" aria-checked={x.key === c.key} onClick={() => setKind(x.key)}
                  className={cn("t12m inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1", x.key === c.key ? "border-transparent" : "border-border text-muted-foreground hover:text-foreground")}
                  style={x.key === c.key ? { background: `color-mix(in oklab, ${toneVar(x.tone)} 18%, var(--surface))`, color: "var(--foreground)" } : undefined}>
            <span className="size-2 rounded-full" style={{ background: toneVar(x.tone) }} aria-hidden />{x.label}
            {x.key === c.key ? guessed(!!draft.creator) : null}
          </button>
        ))}
      </div>
      <div className="grid grid-cols-[auto_1fr] items-center gap-x-3 gap-y-2">
        <span className="t12 text-muted-foreground">Ngày</span>
        <span className="flex items-center gap-1.5">
          <input type="date" value={day} onChange={(e) => e.target.value && setDay(e.target.value)} aria-label="Ngày"
                 className="t14 h-8 rounded-lg border border-input bg-field px-2 outline-none focus-visible:border-ring" />
          {guessed(!!draft.dayGuessed)}
        </span>
        <span className="t12 text-muted-foreground">Giờ</span>
        <span className="flex items-center gap-1.5">
          <input type="time" step={900} value={start} onChange={(e) => setStart(e.target.value)} aria-label="Từ" className="t14 tnum h-8 rounded-lg border border-input bg-field px-2 outline-none focus-visible:border-ring" />
          <span className="text-muted-foreground">–</span>
          <input type="time" step={900} value={end} onChange={(e) => setEnd(e.target.value)} aria-label="Đến" className="t14 tnum h-8 rounded-lg border border-input bg-field px-2 outline-none focus-visible:border-ring" />
          {guessed(true)}
        </span>
        {c.subject ? (
          <>
            <span className="t12 text-muted-foreground">{c.subject.label}</span>
            <SubjectPick search={c.subject.search} value={subject} onChange={setSubject} hint={guessed(!!draft.subject && subject?.value === draft.subject.value)} />
          </>
        ) : null}
        {c.place ? (
          <>
            <span className="t12 text-muted-foreground">Ở đâu</span>
            <span className="flex items-center gap-1.5">
              <input value={place} onChange={(e) => setPlace(e.target.value)} placeholder="Dự án / địa điểm" aria-label="Địa điểm"
                     className="t14 h-8 min-w-0 flex-1 rounded-lg border border-input bg-field px-2 outline-none focus-visible:border-ring" />
              {guessed(!!draft.place && place === draft.place)}
            </span>
          </>
        ) : null}
        {laneTitle && c.fields.attendee ? (<><span className="t12 text-muted-foreground">Cho</span><span className="t14">{laneTitle}</span></>) : null}
        {c.fields.attendee && invite ? (
          <>
            <span className="t12 self-start pt-1.5 text-muted-foreground">Mời</span>
            <InvitePick source={invite.options} groups={invite.groups} value={invited} onChange={setInvited} />
          </>
        ) : null}
      </div>
      {err ? <p className="t12 text-bad">{err}</p> : null}
      <div className="flex items-center justify-between gap-2">
        <button type="button" className="t12 text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                onClick={() => { onClose(); desk.openForm(decodeAction({ command: c.command, title: c.label }), { initial: payload() }); }}>
          Thêm chi tiết…
        </button>
        <button type="button" className={actionClass("primary")} disabled={saving} onClick={() => void save()}>{saving ? <Spinner className="size-3.5" /> : null}Lưu</button>
      </div>
    </Floating>
  );
}

function SubjectPick({ search, value, onChange, hint }: { search: string; value?: { value: string; label: string; hint?: string }; onChange: (v?: { value: string; label: string; hint?: string }) => void; hint?: React.ReactNode }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [opts, setOpts] = useState<{ value: string; label: string; hint?: string }[]>([]);
  useEffect(() => {
    if (!open) return;
    let alive = true;
    const t = setTimeout(() => {
      void api.queryRaw<{ value: string; label: string; hint?: string }[]>(search, { q }).then((o) => alive && setOpts(Array.isArray(o) ? o : []), () => alive && setOpts([]));
    }, 180);
    return () => { alive = false; clearTimeout(t); };
  }, [q, open, search]);
  if (value && !open)
    return (
      <span className="flex min-w-0 items-center gap-1.5">
        <button type="button" onClick={() => { setOpen(true); setQ(""); }} className="t14 inline-flex min-w-0 items-center gap-1.5 rounded-full bg-primary/10 px-2.5 py-1 text-left hover:bg-primary/15" title="Đổi">
          <span className="truncate">{value.label}</span>{value.hint ? <span className="t12 shrink-0 text-muted-foreground">{value.hint}</span> : null}
        </button>
        {hint}
      </span>
    );
  return (
    <div className="relative min-w-0" data-cal-keep>
      <input autoFocus={!!value} value={q} onFocus={() => setOpen(true)} onChange={(e) => { setQ(e.target.value); setOpen(true); }} placeholder="Gõ vài chữ tên khách"
             aria-label="Tìm khách" className="t14 h-8 w-full rounded-lg border border-input bg-field px-2 outline-none focus-visible:border-ring" />
      {open ? (
        <ul className="absolute inset-x-0 top-9 z-10 max-h-52 overflow-y-auto rounded-lg border border-hairline bg-popover py-1 shadow-lg" role="listbox">
          {opts.length === 0 ? <li className="t12 px-2.5 py-1.5 text-muted-foreground">Không thấy khách khớp</li> : null}
          {opts.map((o) => (
            <li key={o.value}>
              <button type="button" role="option" aria-selected={value?.value === o.value} onClick={() => { onChange(o); setOpen(false); }}
                      className="flex w-full items-baseline justify-between gap-2 px-2.5 py-1.5 text-left hover:bg-accent">
                <span className="t14 truncate">{o.label}</span>{o.hint ? <span className="t12 shrink-0 text-muted-foreground">{o.hint}</span> : null}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function Detail({ item, x, y, kinds, onClose, onOpen, onDone }: {
  item: CalendarItem; x: number; y: number; kinds: CalendarData["kinds"]; onClose: () => void; onOpen: (q: string, input: Input, title?: string) => void; onDone: (msg: string) => void;
}) {
  const kind = kinds.find((k) => k.key === item.kind);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  async function run(command: string, input: Input, tag: string) {
    setBusy(tag);
    setErr("");
    try {
      const r = await api.command(command, input, newKey());
      if (r.status === "ok") onDone(((r.output as { summary?: string } | null)?.summary) || "Đã lưu");
      else setErr(r.error || `Không được (${r.status})`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Không được");
    } finally {
      setBusy("");
    }
  }
  const people = item.people ?? [];
  const tone: Record<string, string> = { accepted: "text-ok", declined: "text-muted-foreground line-through", tentative: "text-warn", invited: "text-warn" };
  const when = item.all_day ? (dayOf(item.start) === dayOf(item.end) ? `Cả ngày ${shortDay(dayOf(item.start))}` : `${shortDay(dayOf(item.start))} – ${shortDay(dayOf(item.end))}`)
    : `${shortDay(dayOf(item.start))} · ${hhmm(minutesOf(item.start))} – ${hhmm(minutesOf(item.end))}`;
  return (
    <Floating x={x} y={y} width={320} onClose={onClose} label={item.title}>
      <div className="flex items-start gap-2.5">
        <span className="mt-1.5 size-3 shrink-0 rounded-[4px]" style={item.busy ? box("gray", true) : { background: toneVar(item.tone) }} aria-hidden />
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="t16 font-semibold">{item.title}</span>
          <span className="t14 tnum text-muted-foreground">{when}{item.repeats ? " · lặp lại" : ""}</span>
          {[kind?.label, STATUS[item.status]].filter(Boolean).length ? <span className="t12 text-muted-foreground">{[kind?.label, STATUS[item.status]].filter(Boolean).join(" · ")}</span> : null}
          {item.subtitle ? <span className="t14 text-foreground/80">{item.subtitle}</span> : null}
          {item.busy ? <span className="t12 text-muted-foreground">Bạn không được xem chi tiết lịch này — chỉ biết giờ đó đã bận.</span> : null}
          {item.organizer ? <span className="t12 text-muted-foreground">Người tổ chức: <span className="text-foreground">{item.organizer}</span></span> : null}
        </div>
        <button type="button" aria-label="Đóng" onClick={onClose} className="rounded-md p-1 text-muted-foreground hover:bg-accent"><X className="size-4" /></button>
      </div>
      {item.links.length || item.open ? (
        <div className="flex flex-wrap gap-2">
          {item.links.map((l) => (
            <button key={l.query + JSON.stringify(l.input)} type="button" className={actionClass("default")} onClick={() => onOpen(l.query, l.input, l.title)}>
              <ExternalLink className="size-3.5" aria-hidden />{l.title}
            </button>
          ))}
          {item.open && !item.links.some((l) => l.query === item.open!.query) ? (
            <button type="button" className={actionClass(item.links.length ? "default" : "primary")} onClick={() => onOpen(item.open!.query, item.open!.input, item.open!.title)}>
              {item.status === "marker" ? "Mở" : "Xem chi tiết"}
            </button>
          ) : null}
        </div>
      ) : null}
      {people.length > 1 ? (
        <div className="flex flex-col gap-1">
          <span className="t12m text-muted-foreground">
            {people.length} người · {people.filter((p) => p.rsvp === "accepted").length} đồng ý{people.some((p) => p.rsvp === "declined") ? ` · ${people.filter((p) => p.rsvp === "declined").length} từ chối` : ""}
          </span>
          <ul className="flex max-h-36 flex-col gap-0.5 overflow-y-auto">
            {people.map((p) => (
              <li key={p.name} className="t12 flex items-center justify-between gap-2">
                <span className="truncate">{p.name}{p.organizer ? <span className="text-muted-foreground"> · tổ chức</span> : null}</span>
                <span className={cn("shrink-0", tone[p.rsvp] ?? "text-muted-foreground")}>{p.label}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {item.respond ? (
        <div className="flex flex-col gap-1.5">
          <span className="t12m">Bạn tham dự?</span>
          <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Trả lời lời mời">
            {item.respond.options.map((o) => (
              <button key={o.key} type="button" role="radio" aria-checked={item.respond!.current === o.key} disabled={!!busy}
                      className={cn(actionClass(item.respond!.current === o.key ? "primary" : "default"), "h-7 text-[13px]")}
                      onClick={() => void run(item.respond!.command, { ...item.respond!.preset, [item.respond!.param]: o.key }, o.key)}>
                {busy === o.key ? <Spinner className="size-3" /> : null}{o.title}
              </button>
            ))}
          </div>
        </div>
      ) : null}
      {err ? <p className="t12 text-bad">{err}</p> : null}
      {item.move || item.cancel ? (
        <div className="flex items-center justify-between gap-2">
          {item.move ? <p className="t12 text-muted-foreground">Kéo mục trên lịch để đổi giờ.</p> : <span />}
          {item.cancel ? (
            <button type="button" className={cn(actionClass("destructive"), "h-7 text-[13px]")} disabled={!!busy}
                    onClick={() => { if (window.confirm(`Huỷ “${item.title}”? Người được mời sẽ nhận thông báo.`)) void run(item.cancel!.command, item.cancel!.preset, "cancel"); }}>
              Huỷ lịch
            </button>
          ) : null}
        </div>
      ) : null}
    </Floating>
  );
}


const optionCache = new Map<string, Promise<Option[]>>();

/** Ô "Mời": chọn người từ danh mục thành viên (không gõ email) + nút gợi ý cả đội. */
function InvitePick({ source, groups, value, onChange }: { source: string; groups: { title: string; emails: string[] }[]; value: string[]; onChange: (v: string[]) => void }) {
  const [all, setAll] = useState<Option[]>([]);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  useEffect(() => {
    let alive = true;
    if (!source) return;
    if (!optionCache.has(source)) optionCache.set(source, api.options(source).catch(() => []));
    void optionCache.get(source)!.then((o) => alive && setAll(o));
    return () => { alive = false; };
  }, [source]);
  const label = (email: string) => all.find((o) => o.value === email)?.label ?? email;
  const fold = (t: string) => t.normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/đ/g, "d").replace(/Đ/g, "D").toLowerCase();
  const hits = q.trim() ? all.filter((o) => !value.includes(o.value) && fold(o.label).includes(fold(q.trim()))).slice(0, 8) : [];
  const add = (emails: string[]) => onChange([...new Set([...value, ...emails])]);
  return (
    <div className="flex min-w-0 flex-col gap-1.5" data-cal-keep>
      {value.length ? (
        <div className="flex max-h-20 flex-wrap gap-1 overflow-y-auto">
          {value.slice(0, 12).map((e) => (
            <span key={e} className="t12 inline-flex items-center gap-1 rounded-full bg-primary/10 py-0.5 pr-1 pl-2">
              {label(e).replace(/\s*\(.*\)$/, "")}
              <button type="button" aria-label={`Bỏ ${label(e)}`} onClick={() => onChange(value.filter((x) => x !== e))} className="rounded-full p-0.5 hover:bg-primary/15"><X className="size-3" /></button>
            </span>
          ))}
          {value.length > 12 ? <span className="t12 text-muted-foreground">+{value.length - 12} người</span> : null}
          <button type="button" className="t12 text-muted-foreground underline-offset-2 hover:underline" onClick={() => onChange([])}>Bỏ hết</button>
        </div>
      ) : null}
      <div className="relative">
        <input value={q} onChange={(e) => { setQ(e.target.value); setOpen(true); }} onFocus={() => setOpen(true)} placeholder="Gõ tên người để mời" aria-label="Mời người"
               className="t14 h-8 w-full rounded-lg border border-input bg-field px-2 outline-none focus-visible:border-ring" />
        {open && hits.length ? (
          <ul className="absolute inset-x-0 top-9 z-10 max-h-48 overflow-y-auto rounded-lg border border-hairline bg-popover py-1 shadow-lg" role="listbox">
            {hits.map((o) => (
              <li key={o.value}>
                <button type="button" role="option" aria-selected={false} onClick={() => { add([o.value]); setQ(""); }} className="t14 w-full truncate px-2.5 py-1.5 text-left hover:bg-accent">{o.label}</button>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      {groups.length ? (
        <div className="flex flex-wrap gap-1.5">
          {groups.map((g) => (
            <button key={g.title} type="button" onClick={() => add(g.emails)} className="t12m inline-flex items-center gap-1 rounded-full border border-border px-2.5 py-1 text-muted-foreground hover:text-foreground">
              <Plus className="size-3" aria-hidden />{g.title} ({g.emails.length})
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
