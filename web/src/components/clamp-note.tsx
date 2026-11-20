"use client";

import { useLayoutEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";

/**
 * Chú thích / ghi chú dài của khối (rà bố cục 18/11): mặc định gấp còn MỘT dòng, có "Xem thêm" khi chữ thật sự bị cắt — chú thích không đẩy số
 * xuống. Ngắn vừa một dòng thì không có nút. Chữ đủ vẫn nằm trong DOM (đọc màn hình, chọn chữ, tìm trong trang).
 */
export function ClampNote({ text, className }: { text: string; className?: string }) {
  const ref = useRef<HTMLParagraphElement>(null);
  const [open, setOpen] = useState(false);
  const [cut, setCut] = useState(false);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || open) return;
    const check = () => setCut(el.scrollWidth > el.clientWidth + 1);
    check();
    const ro = new ResizeObserver(check);
    ro.observe(el);
    return () => ro.disconnect();
  }, [open, text]);
  return (
    <div className={cn("t12 flex min-w-0 items-baseline gap-1.5 text-muted-foreground", className)}>
      <p ref={ref} className={cn("min-w-0 flex-1 select-text", open ? "whitespace-pre-line" : "truncate")} title={open || !cut ? undefined : text}>{text}</p>
      {cut || open ? (
        <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
                className="t12m shrink-0 self-end rounded text-primary outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring">
          {open ? "Thu gọn" : "Xem thêm"}
        </button>
      ) : null}
    </div>
  );
}
