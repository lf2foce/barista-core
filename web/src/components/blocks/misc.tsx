"use client";

import { BrandLogo, hasBrandLogo } from "@/components/brand-logos";
import { Check, ChevronRight, Circle, CircleDot, Clock, Lock, X } from "lucide-react";
import * as fmt from "@/lib/format";
import { cardToneVar } from "@/lib/format";
import type { ActionSpec, Input, ListItem, Stage, StepItem, Tile } from "@/lib/types";
import { IconTile, moduleIcon, moduleTint } from "@/components/icons";
import { cn } from "@/lib/utils";
import { actionClass, CopyButton } from "@/components/primitives";
import { useState } from "react";

/** Hành trình: các mốc nằm ngang — xong (✓ xanh), hiện tại (chấm xanh), dừng (✕ đỏ), chưa tới (vòng xám). */
export function StepStrip({ steps }: { steps: StepItem[] }) {
  const line = (prev: string) => (prev === "done" ? "bg-ok/60" : "bg-foreground/15");
  return (
    <ol className="flex overflow-x-auto py-1" aria-label="Hành trình">
      {steps.map((s, i) => (
        <li key={i} className="flex w-[104px] shrink-0 flex-col items-center gap-1.5" aria-current={s.state === "current" ? "step" : undefined}>
          <div className="flex w-full items-center">
            <span className={cn("h-0.5 flex-1", i === 0 ? "bg-transparent" : line(steps[i - 1].state))} />
            <StateIcon state={s.state} size={20} />
            <span className={cn("h-0.5 flex-1", i === steps.length - 1 ? "bg-transparent" : line(s.state))} />
          </div>
          <span className={cn("t12m line-clamp-1 text-center", s.state === "todo" && "text-muted-foreground")}>{s.label}</span>
          {typeof s.at === "string" && s.at ? <span className="t12 text-muted-foreground">{fmt.date(s.at)}</span> : null}
          {s.note ? <span className={cn("t12 line-clamp-2 text-center", s.state === "stopped" ? "text-bad" : "text-muted-foreground")}>{s.note}</span> : null}
          {s.owner && s.state !== "done" ? <span className="t12 text-muted-foreground/70">{s.owner}</span> : null}
        </li>
      ))}
    </ol>
  );
}

function StateIcon({ state, size = 16 }: { state: string; size?: number }) {
  const s = { width: size, height: size };
  switch (state) {
    case "done":
      return <span className="grid place-items-center rounded-full bg-ok text-white" style={s}><Check style={{ width: size * 0.65, height: size * 0.65 }} strokeWidth={3} aria-label="Xong" /></span>;
    case "current":
      return <CircleDot className="text-primary" style={s} aria-label="Đang ở đây" />;
    case "stopped":
      return <span className="grid place-items-center rounded-full bg-bad text-white" style={s}><X style={{ width: size * 0.65, height: size * 0.65 }} strokeWidth={3} aria-label="Dừng" /></span>;
    default:
      return <Circle className="text-foreground/25" style={s} aria-label="Chưa tới" />;
  }
}

const itemSymbol = (s: string) => (s === "verified" ? <Check className="size-3 text-ok" /> : s === "received" ? <Clock className="size-3 text-warn" /> : s === "rejected" ? <X className="size-3 text-bad" /> : <Circle className="size-3 text-foreground/30" />);

/** Hồ sơ theo chặng: mỗi chặng một khối (ai giữ, mục cần đủ). Mục tiền công ty người xem không có quyền ⇒ chỉ thấy trạng thái. */
export function StageList({ stages }: { stages: Stage[] }) {
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-2.5">
      {stages.map((st) => (
        <div key={st.key} className={cn("flex flex-col gap-2 rounded-xl border p-3", st.state === "current" ? "border-primary/50 bg-primary/10" : "border-transparent bg-surface2")}>
          <div className="flex items-center gap-1.5">
            <StateIcon state={st.state} />
            <span className="t14m line-clamp-1">{st.name}</span>
          </div>
          <span className="t12 text-muted-foreground">{st.owner}</span>
          {st.items.map((it) => (
            <div key={it.label} className="flex flex-col gap-0.5">
              <div className="flex items-start gap-1.5">
                <span className="mt-1">{itemSymbol(it.status)}</span>
                <span className={cn("t12", it.status === "missing" && "text-muted-foreground")}>{it.label}</span>
              </div>
              {it.hidden ? (
                <span className="t12 flex items-center gap-1 pl-[18px] text-muted-foreground/70"><Lock className="size-3" aria-hidden />chỉ đội phụ trách thấy số</span>
              ) : (
                <>
                  {it.value ? <span className="t12 tnum pl-[18px] text-muted-foreground">{it.value}</span> : null}
                  {it.note ? <span className={cn("t12 pl-[18px]", it.status === "rejected" ? "text-bad" : "text-muted-foreground")}>{it.note}</span> : null}
                </>
              )}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

/** Khung lưu ý: tông màu + lý do + dữ kiện + (tuỳ chọn) tin nhắn soạn sẵn để sale tự gửi + nút thao tác. */
export function Callout({ tone, title, text, facts, draft, actions, onAction }: { tone: string; title: string; text: string; facts: string[]; draft: string; actions: ActionSpec[]; onAction: (a: ActionSpec) => void }) {
  const c = cardToneVar(tone);
  // ghi chú nhạt (muted / không tông) mà chỉ là chữ: gấp còn tiêu đề + một dòng, "Xem thêm" mở — không đẩy số xuống (rà bố cục 18/11).
  // Cảnh báo thật (warn / bad), callout có nút / tin soạn sẵn: luôn mở.
  const quiet = (tone === "muted" || !tone) && !actions.length && !draft;
  const [open, setOpen] = useState(false);
  if (quiet && !open && (text || facts.length)) {
    const line = [text, ...facts].filter(Boolean).join(" · ");
    return (
      <div className="flex min-w-0 flex-col gap-0.5 rounded-xl p-3" style={{ background: `color-mix(in oklab, ${c} 6%, transparent)` }}>
        {title ? <p className="t14m">{title}</p> : null}
        <div className="t12 flex min-w-0 items-baseline gap-1.5 text-muted-foreground">
          <p className="min-w-0 flex-1 truncate" title={line}>{line}</p>
          <button type="button" onClick={() => setOpen(true)} aria-expanded={false} className="t12m shrink-0 rounded text-primary outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring">Xem thêm</button>
        </div>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2 rounded-xl p-3" style={{ background: `color-mix(in oklab, ${c} ${tone === "muted" ? 6 : 9}%, transparent)` }}>
      <p className="t14m" style={{ color: tone === "muted" ? undefined : c }}>{title}</p>
      {text ? <p className="t14 select-text">{text}</p> : null}
      {facts.map((f, i) => (
        <p key={i} className="t12 flex gap-1.5 text-muted-foreground"><span aria-hidden>•</span>{f}</p>
      ))}
      {quiet && open ? (
        <button type="button" onClick={() => setOpen(false)} aria-expanded className="t12m w-fit rounded text-primary outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring">Thu gọn</button>
      ) : null}
      {draft ? (
        <div className="flex flex-col gap-1.5">
          <span className="t12 text-muted-foreground">Tin nhắn gợi ý — anh/chị tự gửi</span>
          <p className="t14 select-text whitespace-pre-wrap rounded-lg bg-surface p-2.5">{draft}</p>
        </div>
      ) : null}
      {actions.length || draft ? (
        <div className="flex flex-wrap gap-2">
          {actions.map((a) => (
            <button key={a.command + a.title} type="button" className={actionClass(a.style)} onClick={() => onAction(a)}>{a.title}</button>
          ))}
          {draft ? <CopyButton text={draft} label="Sao chép tin" className="h-8" /> : null}
        </div>
      ) : null}
    </div>
  );
}

export function ListBlock({ items, onOpen, onAsk }: { items: ListItem[]; onOpen: (q: string, input: Input, title?: string) => void; onAsk: (q: string) => void }) {
  return (
    <ul className="divide-y divide-border rounded-xl bg-surface2 px-3">
      {items.map((it, i) => {
        const inner = (
          <>
            <span className="mt-1.5 size-[7px] shrink-0 rounded-full" style={{ background: cardToneVar(it.tone) }} />
            <span className="flex min-w-0 flex-1 flex-col gap-0.5 text-left">
              <span className="t14">{it.title}</span>
              {it.subtitle ? <span className="t12 text-muted-foreground">{it.subtitle}</span> : null}
            </span>
            {it.badge ? <span className="t12 tnum shrink-0 text-muted-foreground">{it.badge}</span> : null}
            {it.open || it.ask ? <ChevronRight className="mt-1 size-3 shrink-0 text-muted-foreground/70" aria-hidden /> : null}
          </>
        );
        const cls = "flex w-full items-start gap-2.5 py-2";
        return (
          <li key={i}>
            {it.open ? (
              <button type="button" className={cn(cls, "outline-none hover:opacity-80 focus-visible:underline")} onClick={() => onOpen(it.open!.query, it.open!.input, it.open!.title ?? it.title)}>{inner}</button>
            ) : it.ask ? (
              <button type="button" className={cn(cls, "outline-none hover:opacity-80 focus-visible:underline")} onClick={() => onAsk(it.ask!)}>{inner}</button>
            ) : (
              <div className={cls}>{inner}</div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** Lưới thẻ (chợ gói…): biểu tượng + màu theo phân hệ (``module``), nhãn trạng thái tô theo ``tone`` — như TileView.swift. */
export function TilesBlock({ items, onOpen, rows = false }: { items: Tile[]; rows?: boolean; onOpen: (q: string, input: Input, title?: string) => void }) {
  if (rows) {
    // kiểu cửa hàng ứng dụng (``style: "rows"``): hai cột hàng — biểu tượng · tên · một dòng mô tả · nút nhãn bên phải; vạch mảnh giữa các hàng
    return (
      <div className="grid gap-x-8 md:grid-cols-2">
        {items.map((t, i) => {
          const tone = cardToneVar(t.tone);
          const inner = (
            <>
              {hasBrandLogo(t.logo) ? <BrandLogo name={t.logo!} size={44} /> : <IconTile icon={moduleIcon(`${t.module ?? ""}.`)} tint={moduleTint(`${t.module ?? ""}.`)} size={44} />}
              <span className="flex min-w-0 flex-1 flex-col gap-0.5 self-stretch border-b border-hairline py-3 text-left">
                <span className="t14m truncate">{t.title}</span>
                {t.subtitle ? <span className="t12 line-clamp-1 text-muted-foreground">{t.subtitle}</span> : null}
              </span>
              {t.badge ? (
                <span className="t12m shrink-0 rounded-full px-3 py-1" style={{ color: tone, background: `color-mix(in oklab, ${tone} 12%, transparent)` }}>
                  {t.badge}
                </span>
              ) : null}
            </>
          );
          const cls = "flex w-full items-center gap-3";
          return t.open ? (
            <button key={i} type="button" className={cn(cls, "rounded-lg outline-none hover:opacity-85 focus-visible:ring-2 focus-visible:ring-ring")} onClick={() => onOpen(t.open!.query, t.open!.input, t.title)}>
              {inner}
            </button>
          ) : (
            <div key={i} className={cls}>{inner}</div>
          );
        })}
      </div>
    );
  }
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(230px,1fr))] gap-3">
      {items.map((t, i) => {
        const inner = (
          <>
            <span className="flex w-full items-start justify-between gap-2">
              <IconTile icon={moduleIcon(`${t.module ?? ""}.`)} tint={moduleTint(`${t.module ?? ""}.`)} size={34} />
              {t.badge ? (
                <span className="t12m truncate rounded-full px-2 py-0.5" style={{ color: cardToneVar(t.tone), background: `color-mix(in oklab, ${cardToneVar(t.tone)} 12%, transparent)` }}>
                  {t.badge}
                </span>
              ) : null}
            </span>
            <span className="t16 text-left">{t.title}</span>
            {t.subtitle ? <span className="t12 line-clamp-4 text-left text-muted-foreground">{t.subtitle}</span> : null}
          </>
        );
        const cls = "flex min-h-[150px] flex-col items-start gap-2 rounded-xl bg-surface2 p-3.5";
        return t.open ? (
          <button key={i} type="button" className={cn(cls, "outline-none hover:opacity-85 focus-visible:ring-2 focus-visible:ring-ring")} onClick={() => onOpen(t.open!.query, t.open!.input, t.title)}>
            {inner}
          </button>
        ) : (
          <div key={i} className={cls}>{inner}</div>
        );
      })}
    </div>
  );
}
