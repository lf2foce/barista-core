"use client";

import { ListFilter, X } from "lucide-react";
import { useState } from "react";
import { chipText, cleared, isActive } from "@/lib/filter-layout";
import type { FieldSpec, Input, Json, Option } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useMedia } from "@/hooks/use-media";
import { actionClass } from "@/components/primitives";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";

type RenderField = (f: FieldSpec, value: Json | undefined, set: (v: Json | undefined) => void) => React.ReactNode;

/**
 * Nút "Bộ lọc" (rà bố cục 18/11/2026, kiểu Linear / Notion / Airtable): mở NGUYÊN bộ ô lọc phụ của màn — popover trên máy tính, tấm trượt từ dưới lên
 * trên điện thoại. Sửa trong bản nháp; "Áp dụng" mới chạy lại truy vấn (một lần cho nhiều ô), "Xoá lọc" đưa mọi ô phụ về mặc định server khai.
 * Huy hiệu = số ô phụ đang lọc. Ô phụ đang bật hiện thành chip ở `FilterChips`.
 */
export function FilterPanel({ fields, values, open, onOpenChange, onApply, render }: {
  fields: FieldSpec[];
  values: Input;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onApply: (next: Record<string, Json | undefined>) => void;
  render: RenderField;
}) {
  const phone = useMedia("(max-width: 639px)");
  const [draft, setDraft] = useState<Record<string, Json | undefined>>({});
  const count = fields.filter((f) => isActive(f, values[f.key])).length;
  const toggle = (next: boolean) => {
    if (next) setDraft(Object.fromEntries(fields.map((f) => [f.key, values[f.key]])));
    onOpenChange(next);
  };
  const apply = () => {
    onApply(draft);
    onOpenChange(false);
  };
  const clear = () => {
    onApply(Object.fromEntries(fields.map((f) => [f.key, cleared(f)])));
    onOpenChange(false);
  };
  // ô cùng nhóm gấp (`group`, vd "Thêm điều kiện") đứng chung dưới một tiêu đề nhỏ; ô không nhóm đứng đầu
  const sections: [string, FieldSpec[]][] = [];
  for (const f of fields) {
    const g = f.group ?? "";
    const last = sections.find(([k]) => k === g);
    if (last) last[1].push(f);
    else sections.push([g, [f]]);
  }
  sections.sort(([a], [b]) => (a ? 1 : 0) - (b ? 1 : 0));
  const body = (
    <>
      <div className="flex min-h-0 flex-col gap-4 overflow-y-auto">
        {sections.map(([g, fs]) => (
          <section key={g || "-"} className="flex flex-col gap-2.5">
            {g ? <h4 className="t12m text-muted-foreground">{g}</h4> : null}
            <div className="grid grid-cols-1 gap-x-3 gap-y-2.5 sm:grid-cols-2">
              {fs.map((f) => (
                <div key={f.key} className="min-w-0">{render(f, draft[f.key], (v) => setDraft((d) => ({ ...d, [f.key]: v })))}</div>
              ))}
            </div>
          </section>
        ))}
      </div>
      <div className="flex shrink-0 items-center gap-2 border-t border-hairline pt-3">
        <button type="button" onClick={clear} className="t14m rounded-md px-1 text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring">Xoá lọc</button>
        <span className="flex-1" />
        <button type="button" onClick={() => onOpenChange(false)} className={actionClass("default")}>Huỷ</button>
        <button type="button" onClick={apply} className={actionClass("primary")}>Áp dụng</button>
      </div>
    </>
  );
  const trigger = (
    <>
      <ListFilter className="size-3.5" aria-hidden />
      Bộ lọc
      {count ? <span className="t12m tnum grid min-w-5 place-items-center rounded-full bg-primary px-1.5 text-primary-foreground">{count}</span> : null}
    </>
  );
  const label = count ? `Bộ lọc, ${count} đang bật` : "Bộ lọc";
  if (phone) {
    return (
      <>
        <button type="button" className={cn(actionClass("default"), count && "border-primary/50")} onClick={() => toggle(true)} aria-label={label} aria-haspopup="dialog">{trigger}</button>
        <Dialog open={open} onOpenChange={toggle}>
          {/* tấm trượt từ dưới lên: rộng hết màn, bo hai góc trên, cao tối đa 85% — ngón cái với tới "Áp dụng" */}
          <DialogContent showCloseButton={false}
                         className="top-auto bottom-0 left-0 flex max-h-[85vh] max-w-none translate-x-0 translate-y-0 flex-col gap-3 rounded-none rounded-t-2xl p-4 pb-[max(16px,env(safe-area-inset-bottom))] sm:max-w-none">
            <div className="flex items-center gap-2">
              <DialogTitle className="t16 flex-1">Bộ lọc</DialogTitle>
              <button type="button" onClick={() => toggle(false)} className="grid size-8 place-items-center rounded-md text-muted-foreground hover:bg-accent" aria-label="Đóng"><X className="size-4" /></button>
            </div>
            <DialogDescription className="sr-only">Chọn điều kiện lọc rồi bấm Áp dụng</DialogDescription>
            {body}
          </DialogContent>
        </Dialog>
      </>
    );
  }
  return (
    <Popover open={open} onOpenChange={toggle}>
      <PopoverTrigger className={cn(actionClass("default"), count && "border-primary/50")} aria-label={label}>{trigger}</PopoverTrigger>
      <PopoverContent align="end" className="max-h-[min(70vh,560px)] w-[min(560px,calc(100vw-2rem))] gap-3 rounded-xl p-4">
        {body}
      </PopoverContent>
    </Popover>
  );
}

/** Ô phụ đang lọc thành chip dưới hàng đầu: bấm chip ⇒ mở bộ lọc để sửa; × ⇒ bỏ đúng ô đó (về mặc định server). Không ô nào ⇒ không vẽ gì. */
export function FilterChips({ fields, values, options, onEdit, onApply }: {
  fields: FieldSpec[];
  values: Input;
  options: Record<string, Option[]>;
  onEdit: () => void;
  onApply: (next: Record<string, Json | undefined>) => void;
}) {
  const on = fields.filter((f) => isActive(f, values[f.key]));
  if (!on.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5" aria-label="Bộ lọc đang bật">
      {on.map((f) => {
        const t = chipText(f, values[f.key], f.options ?? options[f.key]);
        return (
          <span key={f.key} className="t12 inline-flex max-w-full items-center rounded-full bg-primary/10 text-primary">
            <button type="button" onClick={onEdit} className="min-w-0 truncate rounded-l-full py-1 pr-1 pl-2.5 outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring" title={`Sửa: ${t}`}>{t}</button>
            <button type="button" onClick={() => onApply({ [f.key]: cleared(f) })} className="grid size-6 shrink-0 place-items-center rounded-r-full outline-none hover:bg-primary/15 focus-visible:ring-2 focus-visible:ring-ring" aria-label={`Bỏ lọc ${f.label}`}>
              <X className="size-3" aria-hidden />
            </button>
          </span>
        );
      })}
      {on.length > 1 ? (
        <button type="button" onClick={() => onApply(Object.fromEntries(on.map((f) => [f.key, cleared(f)])))} className="t12m rounded px-1 text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring">Xoá lọc</button>
      ) : null}
    </div>
  );
}
