"use client";

import { useEffect, useId, useRef } from "react";
import type { CallHub } from "@/lib/call-hub";

/**
 * Câu hỏi "đang trong một cuộc gọi — rời để vào cuộc mới?" (luật một người một cuộc gọi, 0114 bảng gọi nổi). Vẽ BÊN TRONG hộp thoại form khi hỏi trước lúc
 * gửi lệnh (hộp thoại giữ tiêu điểm trong nó, câu hỏi nằm ngoài sẽ bị che với trình đọc màn hình và không tới được bằng bàn phím), hoặc thành lớp phủ của
 * bảng nổi khi không có form nào mở. Mở ra ⇒ tiêu điểm vào nút an toàn "Ở lại"; Esc = ở lại (không để Esc đóng luôn hộp thoại form, mất chữ đã nhập).
 */
export function SwitchQuestion({ hub, switching }: { hub: CallHub; switching: boolean }) {
  const stay = useRef<HTMLButtonElement>(null);
  const title = useId();
  const desc = useId();
  useEffect(() => {
    stay.current?.focus();
  }, []);
  return (
    <div
      role="alertdialog"
      aria-labelledby={title}
      aria-describedby={desc}
      className="flex flex-col gap-3"
      onKeyDown={(e) => {
        if (e.key !== "Escape") return;
        e.preventDefault();
        e.stopPropagation();   // chặn cả Esc của hộp thoại form (nó nghe ở document, sau gốc React)
        hub.cancelSwitch();
      }}
    >
      <p id={title} className="t16">Anh/chị đang trong một cuộc gọi</p>
      <p id={desc} className="t14 text-muted-foreground">Chỉ gọi được một cuộc một lúc. Rời cuộc gọi hiện tại để vào cuộc gọi mới?</p>
      <div className="flex flex-wrap justify-end gap-2">
        <button ref={stay} type="button" disabled={switching} className="t14 h-9 rounded-lg border border-border bg-surface px-3 outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
          onClick={() => hub.cancelSwitch()}>
          Ở lại cuộc gọi này
        </button>
        <button type="button" disabled={switching} className="t14m h-9 rounded-lg bg-bad px-3 text-white outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
          onClick={() => void hub.confirmSwitch()}>
          {switching ? "Đang rời…" : "Rời và vào cuộc gọi mới"}
        </button>
      </div>
    </div>
  );
}
