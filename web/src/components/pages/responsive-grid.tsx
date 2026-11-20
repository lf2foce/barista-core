"use client";

import { useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { effectiveSpans } from "@/lib/layout";

/** Lưới 12 cột đo bề rộng CÓ SẴN của chính nó (khung làm việc, thu nhỏ hay phóng to — không theo cửa sổ) và chia lại số ô một hàng (`effectiveSpans`). */
export function ResponsiveGrid({ ws, children }: { ws: number[]; children: (spans: number[]) => ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(el.getBoundingClientRect().width);
    const ro = new ResizeObserver((entries) => setWidth(entries[0]?.contentRect.width ?? 0));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const style: CSSProperties = { gridTemplateColumns: "repeat(12, minmax(0, 1fr))" };
  return <div ref={ref} className="grid items-stretch gap-3" style={style}>{children(effectiveSpans(ws, width))}</div>;
}
