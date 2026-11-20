"use client";

import { agendaDays, agendaHeader } from "@/lib/board";
import type { CalendarViewSpec, Row } from "@/lib/types";
import { cn } from "@/lib/utils";

const TONE = { muted: "text-muted-foreground", today: "text-primary", bad: "text-bad" } as const;

/** Lịch (0087): CÙNG các dòng bảng xếp theo ngày của cột server khai — hôm nay nổi lên, ngày đã qua là quá hạn chỉ khi server khai; bấm dòng mở như bảng. */
export function Agenda({ rows, spec, onOpen }: { rows: Row[]; spec: CalendarViewSpec; onOpen?: (row: Row) => void }) {
  const now = new Date();
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  return (
    <div className="flex flex-col gap-4 p-3">
      {agendaDays(rows, spec).map(({ day, rows: items }) => {
        const h = agendaHeader(day, today, spec.overdue);
        return (
          <section key={day || "none"} className="flex flex-col gap-1">
            <h3 className={cn("t12m", TONE[h.tone])}>{h.text}</h3>
            <ul className="flex flex-col divide-y divide-border/60 rounded-xl border border-hairline bg-surface">
              {items.map((r) => (
                <li key={r.id}>
                  <button type="button" disabled={!onOpen} onClick={() => onOpen?.(r)} className="flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left hover:bg-accent/60 focus-visible:bg-accent focus-visible:outline-none disabled:cursor-default">
                    <span className="t14m">{String(r.values[spec.title] ?? "")}</span>
                    {spec.subtitle && r.values[spec.subtitle] ? <span className="t12 text-muted-foreground">{String(r.values[spec.subtitle])}</span> : null}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
