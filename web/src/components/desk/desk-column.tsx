"use client";

import { Check, ChevronRight, RefreshCw, Sparkles } from "lucide-react";
import { useState } from "react";
import { cardToneVar } from "@/lib/format";
import { batchSummary, groupItems, type DecideGroup, type DecideItem } from "@/lib/to-decide";
import type { DeskSection } from "@/lib/types";
import { cn } from "@/lib/utils";
import { IconTile, screenTint } from "@/components/icons";
import { ColumnHeader } from "./column";
import { CompactCard } from "./compact-card";
import { useChat } from "./chat-context";
import { screenItemId, useDesk } from "./desk-context";

/**
 * BÀN VIỆC (0094, DeskColumn.swift): MỘT điểm vào "tôi phải làm gì" — cần tôi quyết · cần tôi làm · đang theo dõi. Mỗi mục là một màn đúng quyền
 * (bấm ⇒ mở trên khung làm việc, đủ nút như cũ), câu tín hiệu do server tính từ chính truy vấn của màn. Web không tự đếm, không tự lọc quyền.
 */
export function DeskColumn() {
  const desk = useDesk();
  const chat = useChat();
  const page = desk.deskPage;
  const [loading, setLoading] = useState(false);
  const reload = async () => {
    setLoading(true);
    try {
      await desk.refreshDesk();
    } finally {
      setLoading(false);
    }
  };
  const groups = page?.groups ?? [];
  // thẻ đề xuất sáng (trợ lý soạn sẵn, người bấm mới chạy) thuộc nhóm "Cần tôi quyết" — có thẻ mà chưa có nhóm ⇒ vẫn dựng nhóm đó
  const shown = chat.toDecide.length && !groups.some((g) => g.key === "decide") ? [{ key: "decide", title: "Chờ tôi duyệt", sections: [] }, ...groups] : groups;
  const waiting = page?.count ?? 0;
  return (
    <div className="flex h-full min-h-0 flex-col bg-surface">
      <ColumnHeader title="Việc" subtitle={subtitle(waiting, page?.unchecked ?? 0)}>
        <button
          type="button"
          onClick={() => void reload()}
          disabled={loading}
          className="grid size-8 shrink-0 place-items-center rounded-lg border border-border bg-surface outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
          aria-label="Tải lại"
          title="Tải lại"
        >
          <RefreshCw className={cn("size-4", loading && "animate-spin")} />
        </button>
      </ColumnHeader>
      <div className="min-h-0 flex-1 overflow-y-auto px-1.5 py-2">
        {desk.deskFailed ? <p className="t14 px-3 py-6 text-muted-foreground">Chưa tải được bàn việc — bấm tải lại.</p> : null}
        {shown.map((g) => (
          <section key={g.key} aria-label={g.title} className="pb-2">
            <h3 className="t12m flex items-center gap-1.5 px-3 pt-3 pb-1 text-muted-foreground">
              {g.title}
              {hotCount(g.sections, g.key === "decide" ? chat.toDecide.length : 0) ? (
                <span className="t12m grid h-[18px] min-w-[18px] place-items-center rounded-full bg-foreground/8 px-1.5 text-foreground tnum">
                  {hotCount(g.sections, g.key === "decide" ? chat.toDecide.length : 0)}
                </span>
              ) : null}
            </h3>
            {g.key === "decide" ? <DecideList /> : null}
            <ul className="flex flex-col gap-0.5">
              {g.sections
                .filter((s) => s.lines.length || s.unchecked)
                .map((s) => (
                  <DeskRow key={`${s.workspace}:${s.screen.key}`} section={s} />
                ))}
            </ul>
            <QuietRow sections={g.sections.filter((s) => !s.lines.length && !s.unchecked)} />
          </section>
        ))}
      </div>
    </div>
  );
}

/** Số mục ĐANG có tín hiệu trong nhóm (server đã quyết mục nào có câu) — không tự đếm việc. */
function hotCount(sections: DeskSection[], extra: number): number {
  return sections.filter((x) => x.lines.length || x.unchecked).length + extra;
}

/** Dòng dưới tiêu đề: số MỤC cần để ý (không phải số việc) + mục chưa kiểm được — đừng để 0 trông như hết việc. */
function subtitle(hot: number, unknown: number): string {
  const parts = [hot ? `${hot} mục cần để ý` : unknown ? "" : "Mọi việc của bạn ở một chỗ", unknown ? `${unknown} mục chưa kiểm được` : ""].filter(Boolean);
  return `${parts.join(" · ")} · đúng quyền của bạn`;
}

/**
 * "CHỜ TÔI DUYỆT" (ADR 0253, thay MorningCards): TỪNG việc người này phải quyết — đề xuất trợ lý soạn sẵn + phiếu duyệt — nhóm theo loại. Mỗi dòng nói việc +
 * hồ sơ + vì sao (bản chạy thử chuẩn); bấm mở đúng thẻ / đúng phiếu. Nhóm cùng loại gộp lô được thì có "Duyệt cả N": lần lượt từng thẻ, thẻ nào căn cứ đã đổi ở lại.
 */
function DecideList() {
  const desk = useDesk();
  const chat = useChat();
  const [busy, setBusy] = useState<string | null>(null);
  const groups = groupItems(chat.toDecide);
  if (!groups.length) return null;
  const open = (it: DecideItem) => (it.card ? desk.openCard(it.card, true) : void desk.openTarget("core.approval", { approval_id: it.id }, "Phiếu duyệt"));
  const approveAll = async (g: DecideGroup) => {
    setBusy(g.key);
    try {
      const res = await chat.decideMany(g.kind, g.items.map((x) => x.id));
      const sum = batchSummary(res);
      desk.show(sum.text, sum.failed);
    } finally {
      setBusy(null);
    }
  };
  return (
    <div className="flex flex-col gap-1.5 pb-0.5">
      {groups.map((g) => (
        <div key={g.key} className="flex flex-col">
          <div className="flex items-center gap-2 px-2.5 pt-1">
            <span className="t12m flex-1 truncate text-muted-foreground">{g.title} · {g.items.length}</span>
            {g.batchable && g.items.length >= 2 ? (
              <button type="button" disabled={busy !== null} onClick={() => void approveAll(g)} className="t12m rounded-md px-2 py-0.5 text-primary outline-none hover:bg-primary/10 focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50">
                {busy === g.key ? "Đang duyệt…" : `Duyệt cả ${g.items.length}`}
              </button>
            ) : null}
          </div>
          <ul className="flex flex-col gap-0.5">
            {g.items.map((it) => (
              <li key={it.id}>
                <button type="button" onClick={() => open(it)} className="flex w-full items-start gap-2.5 rounded-[10px] px-2.5 py-[7px] text-left outline-none hover:bg-foreground/5 focus-visible:ring-2 focus-visible:ring-ring">
                  <span className="grid size-7 shrink-0 place-items-center rounded-lg bg-primary/12 text-primary"><Sparkles className="size-3.5" aria-hidden /></span>
                  <span className="flex min-w-0 flex-1 flex-col gap-0.5 pt-[5px]">
                    <span className="text-[14px]">{it.title}</span>
                    {it.requester ? <span className="t12 text-muted-foreground">{it.requester} xin duyệt</span> : null}
                    {it.card && desk.settled(it.card) ? <span className="t12 text-muted-foreground">{desk.settled(it.card)?.label}</span> : it.reason ? <span className="t12 line-clamp-2 text-muted-foreground">{it.reason}</span> : null}
                  </span>
                  <ChevronRight className="mt-2 size-3 shrink-0 text-muted-foreground/70" aria-hidden />
                </button>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function DeskRow({ section }: { section: DeskSection }) {
  const desk = useDesk();
  const chat = useChat();
  const [open, setOpen] = useState(false); // bấm mới bung — không biến bàn việc thành "40 thẻ"
  const s = section.screen;
  const on = desk.activeId === screenItemId(s);
  const quiet = section.lines.length === 0;
  const ask = section.lines.find((l) => l.ask)?.ask;
  return (
    <li>
      <div className={cn("flex items-start gap-2.5 rounded-[10px] px-2.5 py-[7px]", on ? "bg-primary/12" : "hover:bg-foreground/5")}>
        <button type="button" onClick={() => desk.openScreen(s)} aria-current={on ? "true" : undefined} className="flex min-w-0 flex-1 items-start gap-2.5 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring">
          <IconTile icon={s.icon} tint={screenTint(s)} size={32} />
          <span className="flex min-w-0 flex-1 flex-col gap-1.5 pt-[6px]">
            <span className={cn("truncate text-[14px] font-medium", quiet && !section.unchecked && "font-normal text-muted-foreground")}>{s.title}</span>
            {section.unchecked ? <Signal tone="warn" title="Chưa kiểm được — mở để xem" /> : null}
            {section.lines.length ? (
              // tín hiệu = NHÃN ngắn có màu theo mức (đỏ / cam…); giải thích để ở chú thích khi rê chuột — liếc là biết, không phải đọc đoạn văn
              <span className="flex flex-wrap gap-1">
                {section.lines.map((l) => <Signal key={l.title} tone={l.tone} title={l.title} hint={l.detail} />)}
              </span>
            ) : null}
          </span>
        </button>
        {ask ? (
          <button
            type="button"
            onClick={() => {
              desk.select({ kind: "assistant" });
              void chat.send(ask);
            }}
            className="mt-1 grid size-6 shrink-0 place-items-center rounded-md text-primary outline-none hover:bg-primary/10 focus-visible:ring-2 focus-visible:ring-ring"
            aria-label="Nhờ trợ lý xử lý"
            title="Nhờ trợ lý xử lý"
          >
            <Sparkles className="size-3.5" />
          </button>
        ) : null}
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          className="mt-1 grid size-6 shrink-0 place-items-center rounded-md text-muted-foreground outline-none hover:bg-foreground/5 focus-visible:ring-2 focus-visible:ring-ring"
          aria-label={open ? "Thu gọn" : "Xem nhanh"}
          title={open ? "Thu gọn" : "Xem nhanh, làm ngay tại đây"}
        >
          <ChevronRight className={cn("size-3.5 transition-transform", open && "rotate-90")} />
        </button>
      </div>
      {open ? (
        <div className="px-2.5 pt-1 pb-2">
          <CompactCard title={s.title} query={s.query} input={EMPTY} onOpen={() => desk.openScreen(s)} />
        </div>
      ) : null}
    </li>
  );
}

const EMPTY = {};

/** Một tín hiệu: nhãn tròn, nền nhạt theo màu mức độ + chấm màu; chữ giữ màu thường cho dễ đọc. */
function Signal({ tone, title, hint }: { tone: string; title: string; hint?: string }) {
  const c = cardToneVar(tone);
  return (
    <span className="t12 inline-flex max-w-full items-center gap-1.5 rounded-full px-2 py-[3px]" style={{ background: `color-mix(in oklab, ${c} 13%, transparent)` }} title={hint || undefined}>
      <span className="size-1.5 shrink-0 rounded-full" style={{ background: c }} aria-hidden />
      <span className="truncate">{title}</span>
    </span>
  );
}

/** Mục không có gì mới: một dòng mờ "✓ N mục khác ổn" — bấm mới bung lối vào từng màn; không chiếm chỗ của việc đang cần làm. */
function QuietRow({ sections }: { sections: DeskSection[] }) {
  const desk = useDesk();
  const [open, setOpen] = useState(false);
  if (!sections.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5 px-2.5 pt-1">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
              className="t12 inline-flex items-center gap-1 rounded-md px-1 py-0.5 text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring">
        <Check className="size-3.5 text-ok" aria-hidden />
        {sections.length} mục khác ổn
        <ChevronRight className={cn("size-3 transition-transform", open && "rotate-90")} aria-hidden />
      </button>
      {open && sections.map((s) => (
        <button
          key={`${s.workspace}:${s.screen.key}`}
          type="button"
          onClick={() => desk.openScreen(s.screen)}
          className="t12 rounded-full border border-border bg-surface px-2 py-0.5 text-muted-foreground outline-none hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
        >
          {s.screen.title}
        </button>
      ))}
    </div>
  );
}
