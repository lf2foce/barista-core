"use client";

import { CalendarDays, ChevronLeft, ChevronRight } from "lucide-react";
import { useState } from "react";
import { addMonths, iso, monthGrid, parseDay, sameDay, today, vn, vnLong, WEEKDAYS } from "@/lib/dates";
import { cn } from "@/lib/utils";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

/**
 * Ô chọn ngày dùng chung (form, bộ lọc, tải tài liệu): bấm ⇒ lịch thả xuống — chọn tháng / năm bằng danh sách, bấm ngày; "Hôm nay", "Xoá".
 * Hiện "1 tháng 12, 2026" (ô nhỏ trong bảng: dd/mm/yyyy), gửi ISO yyyy-mm-dd (lõi nhận cả hai). Không có giá trị ⇒ hiện chữ gợi ý, không hiện khuôn "dd/mm/yyyy" của trình duyệt.
 */
export function DatePicker({
  id, value, onChange, placeholder = "Chọn ngày", size = "md", className, invalid, disabled, label,
}: {
  id?: string;
  value: unknown;
  onChange: (iso: string | undefined) => void;
  placeholder?: string;
  size?: "sm" | "md";
  className?: string;
  invalid?: boolean;
  disabled?: boolean;
  label?: string;
}) {
  const picked = parseDay(value);
  const now = today();
  const [open, setOpen] = useState(false);
  const [view, setView] = useState(() => ({ y: (picked ?? now).y, m: (picked ?? now).m }));
  const base = Array.from({ length: 21 }, (_, i) => now.y - 10 + i);
  const years = base.includes(view.y) ? base : [...base, view.y].sort((a, b) => a - b);
  const pick = (v: string | undefined) => {
    onChange(v);
    setOpen(false);
  };
  const go = (n: number) => setView((v) => addMonths(v.y, v.m, n));
  const sel = "t12m h-7 rounded-md border border-input bg-field px-1.5 outline-none focus-visible:ring-2 focus-visible:ring-ring/40";

  return (
    <Popover open={open} onOpenChange={(o) => { setOpen(o); if (o) setView({ y: (picked ?? now).y, m: (picked ?? now).m }); }}>
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
        <span className={cn("truncate", picked ? "text-foreground" : "text-muted-foreground")}>{picked ? (size === "sm" ? vn(picked) : vnLong(picked)) : placeholder}</span>
        <CalendarDays className="size-4 shrink-0 text-muted-foreground" aria-hidden />
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[17.5rem] gap-2 p-2.5">
        <div className="flex items-center gap-1">
          <button type="button" onClick={() => go(-1)} className="grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground" aria-label="Tháng trước">
            <ChevronLeft className="size-4" aria-hidden />
          </button>
          <select aria-label="Tháng" className={cn(sel, "flex-1")} value={view.m} onChange={(e) => setView((v) => ({ ...v, m: Number(e.target.value) }))}>
            {Array.from({ length: 12 }, (_, i) => <option key={i} value={i + 1}>Tháng {i + 1}</option>)}
          </select>
          <select aria-label="Năm" className={sel} value={view.y} onChange={(e) => setView((v) => ({ ...v, y: Number(e.target.value) }))}>
            {years.map((y) => <option key={y} value={y}>{y}</option>)}
          </select>
          <button type="button" onClick={() => go(1)} className="grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground" aria-label="Tháng sau">
            <ChevronRight className="size-4" aria-hidden />
          </button>
        </div>
        <div className="grid grid-cols-7 gap-0.5" role="grid" aria-label={`Tháng ${view.m}/${view.y}`}>
          {WEEKDAYS.map((w) => <span key={w} className="t12 grid h-7 place-items-center text-muted-foreground">{w}</span>)}
          {monthGrid(view.y, view.m).map((c) => {
            const on = sameDay(c, picked);
            const isToday = sameDay(c, now);
            return (
              <button
                key={iso(c)}
                type="button"
                onClick={() => pick(iso(c))}
                aria-pressed={on}
                aria-label={vn(c)}
                className={cn(
                  "t12 grid h-8 place-items-center rounded-md tabular-nums outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  on ? "bg-primary font-medium text-primary-foreground" : "hover:bg-accent",
                  !on && c.out && "text-muted-foreground/50",
                  !on && isToday && "font-semibold text-primary ring-1 ring-primary/40",
                )}
              >
                {c.d}
              </button>
            );
          })}
        </div>
        <div className="flex items-center justify-between border-t border-hairline pt-2">
          <button type="button" className="t12m rounded-md px-2 py-1 text-primary hover:bg-primary/10" onClick={() => pick(iso(now))}>Hôm nay</button>
          {picked ? <button type="button" className="t12m rounded-md px-2 py-1 text-muted-foreground hover:bg-accent hover:text-foreground" onClick={() => pick(undefined)}>Xoá</button> : null}
        </div>
      </PopoverContent>
    </Popover>
  );
}
