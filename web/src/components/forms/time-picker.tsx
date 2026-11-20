"use client";

import { Clock as ClockIcon } from "lucide-react";
import { useState } from "react";
import { hhmm, parseClock } from "@/lib/dates";
import { cn } from "@/lib/utils";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

const HOURS = Array.from({ length: 24 }, (_, i) => i);
const MINUTES = Array.from({ length: 12 }, (_, i) => i * 5);

/**
 * Ô giờ dùng chung (24 giờ, HH:MM): gõ được ("14h30", "1430", "9" ⇒ 09:00 — hiểu khi rời ô) HOẶC bấm đồng hồ chọn giờ / phút (cách 5 phút).
 * Gửi lên "HH:MM" (đúng chữ lõi đang nhận). Trống ⇒ undefined. Chữ không đọc được ⇒ trả lại giờ cũ, không gửi rác.
 */
export function TimePicker({
  id, value, onChange, placeholder = "HH:MM", size = "md", className, invalid, disabled, label,
}: {
  id?: string;
  value: unknown;
  onChange: (hhmm: string | undefined) => void;
  placeholder?: string;
  size?: "sm" | "md";
  className?: string;
  invalid?: boolean;
  disabled?: boolean;
  label?: string;
}) {
  const picked = parseClock(value);
  const [draft, setDraft] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const shown = draft ?? (picked ? hhmm(picked) : typeof value === "string" ? value : "");

  const commit = () => {
    if (draft === null) return;
    const t = draft.trim();
    setDraft(null);
    if (t === "") return onChange(undefined);
    const c = parseClock(t);
    if (c) onChange(hhmm(c));   // không đọc được ⇒ giữ giờ cũ
  };
  const set = (h: number, m: number, close: boolean) => {
    onChange(hhmm({ h, m }));
    if (close) setOpen(false);
  };
  const cell = "t12 grid h-8 place-items-center rounded-md tabular-nums outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring";

  return (
    <div className={cn("relative", size === "md" && "w-full", className)}>
      <input
        id={id}
        disabled={disabled}
        aria-label={label}
        aria-invalid={invalid || undefined}
        inputMode="numeric"
        autoComplete="off"
        value={shown}
        placeholder={placeholder}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => { if (e.key === "Enter" && draft !== null) { e.preventDefault(); commit(); } }}
        className={cn(
          "h-8 w-full min-w-0 rounded-lg border border-input bg-field pl-2.5 pr-8 tabular-nums outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40 disabled:opacity-50 aria-invalid:border-bad",
          size === "sm" ? "t12" : "t14",
        )}
      />
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger disabled={disabled} aria-label="Chọn giờ" className="absolute inset-y-0 right-0 grid w-8 place-items-center rounded-r-lg text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring">
          <ClockIcon className="size-4" aria-hidden />
        </PopoverTrigger>
        <PopoverContent align="end" className="w-[15rem] gap-2 p-2.5">
          <p className="t12m text-muted-foreground">Giờ</p>
          <div className="grid grid-cols-6 gap-0.5" role="group" aria-label="Giờ">
            {HOURS.map((h) => (
              <button key={h} type="button" aria-pressed={picked?.h === h} onClick={() => set(h, picked?.m ?? 0, false)}
                className={cn(cell, picked?.h === h && "bg-primary font-medium text-primary-foreground hover:bg-primary")}>
                {String(h).padStart(2, "0")}
              </button>
            ))}
          </div>
          <p className="t12m mt-1 text-muted-foreground">Phút</p>
          <div className="grid grid-cols-6 gap-0.5" role="group" aria-label="Phút">
            {MINUTES.map((m) => (
              <button key={m} type="button" aria-pressed={picked?.m === m} onClick={() => set(picked?.h ?? 9, m, true)}
                className={cn(cell, picked?.m === m && "bg-primary font-medium text-primary-foreground hover:bg-primary")}>
                {String(m).padStart(2, "0")}
              </button>
            ))}
          </div>
          <p className="t12 text-muted-foreground">Phút lẻ (vd 17:35): gõ thẳng vào ô.</p>
        </PopoverContent>
      </Popover>
    </div>
  );
}
