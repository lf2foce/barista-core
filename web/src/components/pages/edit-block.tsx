"use client";

import { Pencil, SlidersHorizontal } from "lucide-react";
import type { ReactNode } from "react";
import type { ActionSpec } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useDesk } from "@/components/desk/desk-context";
import { useDesignOpener } from "./block-panel";

/** Nút "Sửa khối này" (app tự ghép — 0098): hiện khi rê chuột / chọn bằng bàn phím; bấm ⇒ form một ô "muốn đổi gì" (mã app / trang / khối là
 *  giá trị cố định server gửi). Server chỉ cho model trả MỘT khối thay đúng khối này. Trong màn có bảng sửa ⇒ thêm nút "Chỉnh khối" (chọn tay). */
export function EditButton({ edit, onChanged, className }: { edit: ActionSpec; onChanged?: () => void; className?: string }) {
  const desk = useDesk();
  const design = useDesignOpener(edit);   // bảng chọn trường kiểu BI (0220) — cạnh bút chì, cùng khối
  const btn = "grid size-7 place-items-center rounded-md border border-border bg-surface text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring";
  return (
    <div className={cn("absolute right-2 top-2 z-10 flex gap-1 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100", className)}>
      {design ? (
        <button type="button" onClick={(e) => { e.stopPropagation(); design(); }} aria-label="Chỉnh khối (chọn trường)" title="Chỉnh khối (chọn trường)" className={btn}>
          <SlidersHorizontal className="size-3.5" aria-hidden />
        </button>
      ) : null}
      <button type="button" onClick={(e) => { e.stopPropagation(); desk.openForm(edit, { onDone: onChanged }); }} aria-label={edit.title} title={edit.title} className={btn}>
        <Pencil className="size-3.5" aria-hidden />
      </button>
    </div>
  );
}

/** Bọc một khối: có `edit` ⇒ thêm nút sửa ở góc; không có ⇒ trả nguyên khối. */
export function EditFrame({ edit, onChanged, children }: { edit?: ActionSpec; onChanged?: () => void; children: ReactNode }) {
  if (!edit) return <>{children}</>;
  return (
    <div className="group relative">
      {children}
      <EditButton edit={edit} onChanged={onChanged} />
    </div>
  );
}
