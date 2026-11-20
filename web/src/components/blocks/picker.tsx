"use client";

import { Check, Clock } from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api";
import { pickerOutcome } from "@/lib/autonomy";
import { newKey } from "@/lib/idempotency";
import type { PickerData, PickerItem } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Pill } from "@/components/primitives";
import { useDesk } from "@/components/desk/desk-context";

/**
 * Chọn MỘT mức đặt sẵn (0144 — mức tự chủ của trợ lý): server nói mục nào đang khớp dữ liệu, phiếu nào đang chờ; bấm một mục = chạy đúng lệnh của
 * mục bằng quyền người bấm (siết áp ngay, nới thành phiếu chủ tài khoản — executor quyết). Web không suy mục nào đang áp.
 */
export function PickerBlock({ block, onChanged }: { block: PickerData; onChanged?: () => void }) {
  const desk = useDesk();
  const [busy, setBusy] = useState<string | null>(null);
  const active = block.items.find((i) => i.key === block.current);

  async function pick(item: PickerItem) {
    if (!item.action || busy) return;
    setBusy(item.key);
    try {
      const r = await api.command(item.action.command, { ...item.action.preset }, newKey());
      const summary = r.output && typeof r.output === "object" && !Array.isArray(r.output) && typeof r.output.summary === "string" ? r.output.summary : "";
      const o = pickerOutcome(r.status, summary, r.error);
      desk.show(o.message, o.error);
      if (!o.error) onChanged?.();
    } catch (e) {
      desk.show(e instanceof Error ? e.message : "Không đổi được", true);
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="flex flex-col gap-2.5 rounded-xl border border-hairline bg-surface p-3.5" aria-label={block.title}>
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="t16">{block.title}</h3>
        <Pill text={active ? active.title : block.custom} tone={active ? "green" : "orange"} />
      </div>
      {block.pending ? (
        <p className="t12 flex items-center gap-1.5 text-warn" role="status">
          <Clock className="size-3.5 shrink-0" aria-hidden />
          {block.pending}
        </p>
      ) : null}
      <div className="grid gap-2 sm:grid-cols-3" role="radiogroup" aria-label={block.title}>
        {block.items.map((item) => {
          const on = item.key === block.current;
          return (
            <button
              key={item.key}
              type="button"
              role="radio"
              aria-checked={on}
              aria-label={`${item.title}. ${item.text}`}
              disabled={!item.action || busy !== null}
              onClick={() => (on ? undefined : void pick(item))}
              className={cn(
                "flex min-w-0 flex-col gap-1.5 rounded-xl border px-3 py-2.5 text-left outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-70",
                on ? "border-primary bg-primary/8" : "border-border hover:bg-foreground/[0.04]",
              )}
            >
              <span className="flex items-center gap-2">
                <span className={cn("grid size-4 shrink-0 place-items-center rounded-full border", on ? "border-primary bg-primary text-primary-foreground" : "border-muted-foreground/40")} aria-hidden>
                  {on ? <Check className="size-3" strokeWidth={3} /> : null}
                </span>
                <span className="t14m">{item.title}</span>
                {busy === item.key ? <span className="t12 text-muted-foreground">Đang đổi…</span> : null}
              </span>
              <span className="t12 text-muted-foreground">{item.text}</span>
              {item.facts.length ? (
                <ul className="t12 flex flex-col gap-0.5 text-muted-foreground/90">
                  {item.facts.map((f) => <li key={f}>· {f}</li>)}
                </ul>
              ) : null}
            </button>
          );
        })}
      </div>
      {block.note ? <p className="t12 text-muted-foreground">{block.note}</p> : null}
    </section>
  );
}
