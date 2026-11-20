"use client";

import { CalendarRange, ChevronLeft, ChevronRight } from "lucide-react";
import { useState } from "react";
import { isoMonth, parseMonth, today, vnMonth } from "@/lib/dates";
import { cn } from "@/lib/utils";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

/** Ô chọn THÁNG dùng chung (kỳ KPI, tháng chốt sổ…): bấm ⇒ chọn năm (mũi tên) rồi bấm tháng. Hiện "Tháng 11/2026", gửi "2026-11". Trống = lõi dùng mặc định của ô. */
export function MonthPicker({
  id, value, onChange, placeholder = "Chọn tháng", size = "md", className, invalid, disabled, label,
}: {
  id?: string;
  value: unknown;
  onChange: (isoMonth: string | undefined) => void;
  placeholder?: string;
  size?: "sm" | "md";
  className?: string;
  invalid?: boolean;
  disabled?: boolean;
  label?: string;
}) {
  const picked = parseMonth(value);
  const now = today();
  const [open, setOpen] = useState(false);
  const [year, setYear] = useState((picked ?? now).y);
  const pick = (v: string | undefined) => {
    onChange(v);
    setOpen(false);
  };

  return (
    <Popover open={open} onOpenChange={(o) => { setOpen(o); if (o) setYear((picked ?? now).y); }}>
      <PopoverTrigger
        id={id}
        disabled={disabled}
        aria-label={label}
        aria-invalid={invalid || undefined}
        className={cn(
          "flex items-center justify-between gap-2 rounded-lg border border-input bg-field px-2.5 text-left outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40 disabled:opacity-50 aria-invalid:border-bad data-popup-open:border-ring",
          size === "sm" ? "t12 h-8" : "t14 h-8 w-full",
          className,
        )}
      >
        <span className={cn("truncate", picked ? "text-foreground" : "text-muted-foreground")}>{picked ? vnMonth(picked) : placeholder}</span>
        <CalendarRange className="size-4 shrink-0 text-muted-foreground" aria-hidden />
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[15rem] gap-2 p-2.5">
        <div className="flex items-center justify-between">
          <button type="button" onClick={() => setYear((y) => y - 1)} className="grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground" aria-label="Năm trước">
            <ChevronLeft className="size-4" aria-hidden />
          </button>
          <span className="t14m tabular-nums">{year}</span>
          <button type="button" onClick={() => setYear((y) => y + 1)} className="grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground" aria-label="Năm sau">
            <ChevronRight className="size-4" aria-hidden />
          </button>
        </div>
        <div className="grid grid-cols-4 gap-1" role="group" aria-label={`Các tháng của năm ${year}`}>
          {Array.from({ length: 12 }, (_, i) => i + 1).map((m) => {
            const on = picked?.y === year && picked.m === m;
            const isNow = now.y === year && now.m === m;
            return (
              <button
                key={m}
                type="button"
                aria-pressed={on}
                aria-label={vnMonth({ y: year, m })}
                onClick={() => pick(isoMonth({ y: year, m }))}
                className={cn(
                  "t12 grid h-9 place-items-center rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  on ? "bg-primary font-medium text-primary-foreground" : "hover:bg-accent",
                  !on && isNow && "font-semibold text-primary ring-1 ring-primary/40",
                )}
              >
                Th{m}
              </button>
            );
          })}
        </div>
        <div className="flex items-center justify-between border-t border-hairline pt-2">
          <button type="button" className="t12m rounded-md px-2 py-1 text-primary hover:bg-primary/10" onClick={() => pick(isoMonth(now))}>Tháng này</button>
          {picked ? <button type="button" className="t12m rounded-md px-2 py-1 text-muted-foreground hover:bg-accent hover:text-foreground" onClick={() => pick(undefined)}>Xoá</button> : null}
        </div>
      </PopoverContent>
    </Popover>
  );
}
