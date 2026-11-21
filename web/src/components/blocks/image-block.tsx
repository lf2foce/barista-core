"use client";

import { X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

/** Khối ảnh (ui.b_image): ảnh nguồn trong thẻ — bấm ⇒ xem to trong lớp phủ (nút Đóng, Esc, bấm nền để đóng; con trỏ bàn phím vào nút Đóng rồi trả về ảnh).
 *  `src` là link ký NGẮN HẠN (≤ 60 giây) ⇒ tải NGAY khi thẻ hiện, không tải lười (link có thể hết hạn trước khi cuộn tới — rà Codex 21/11); tải hỏng ⇒ báo + gợi ý tải lại thẻ.
 *  Chưa có ảnh ⇒ hiện `note`. */
export function ImageBlock({ src, caption, alt, note }: { src: string; caption: string; alt: string; note: string }) {
  const [big, setBig] = useState(false);
  const [failed, setFailed] = useState(false);
  const opener = useRef<HTMLButtonElement>(null);
  const closer = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!big) return;
    closer.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setBig(false);
    };
    window.addEventListener("keydown", onKey);
    const back = opener.current;
    return () => {
      window.removeEventListener("keydown", onKey);
      back?.focus();
    };
  }, [big]);

  if (!src || failed) {
    return (
      <div className="t14 grid min-h-32 place-items-center rounded-xl bg-surface2 p-4 text-center text-muted-foreground">
        {failed ? "Link ảnh đã hết hạn hoặc không tải được — bấm tải lại thẻ" : note || "Chưa có ảnh"}
      </div>
    );
  }
  const label = alt || caption || "Ảnh";
  return (
    <figure className="flex min-w-0 flex-col gap-1.5">
      <button ref={opener} type="button" onClick={() => setBig(true)}
              className="group block overflow-hidden rounded-xl border border-hairline bg-surface2 outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label={`Xem to: ${label}`}>
        {/* eslint-disable-next-line @next/next/no-img-element -- link ký ngoài miền, không qua trình tối ưu ảnh của Next */}
        <img src={src} alt={alt} onError={() => setFailed(true)} className="block h-auto w-full object-contain transition group-hover:opacity-90" />
      </button>
      {caption ? <figcaption className="t12 text-muted-foreground">{caption}</figcaption> : null}
      {big ? (
        <div role="dialog" aria-modal="true" aria-label={label} className="fixed inset-0 z-50 grid place-items-center bg-black/75 p-4" onClick={() => setBig(false)}>
          <button ref={closer} type="button" onClick={() => setBig(false)} aria-label="Đóng ảnh"
                  className="absolute right-4 top-4 grid size-10 place-items-center rounded-full bg-white/90 text-black outline-none hover:bg-white focus-visible:ring-2 focus-visible:ring-ring">
            <X className="size-5" />
          </button>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={src} alt={alt} className="max-h-full max-w-full rounded-lg object-contain" onClick={(e) => e.stopPropagation()} />
        </div>
      ) : null}
    </figure>
  );
}
