"use client";

import { ArrowUp, Square } from "lucide-react";
import { cn } from "@/lib/utils";

/** Viên ô nhập kiểu trợ lý (trợ lý + Soạn với AI): MỘT khối nổi trên nền màn — không dải nền / đường kẻ riêng (anh Anh 18/11, kiểu ChatGPT);
 *  nút ảnh · chữ · mic · gửi nằm trong viên, viền sáng khi đang gõ. */
export const COMPOSER_PILL =
  "flex items-end gap-1 rounded-[24px] border border-border bg-field p-1.5 transition-colors focus-within:border-ring focus-within:ring-2 focus-within:ring-ring/30";
/** Ô chữ trong viên: trong suốt, không viền riêng; cao tối đa ~7 dòng rồi cuộn. */
export const COMPOSER_TEXT =
  "t14 block max-h-[160px] min-h-9 min-w-0 flex-1 resize-none border-0 bg-transparent px-1.5 py-[7px] leading-[20px] outline-none placeholder:text-muted-foreground";

/**
 * Nút gửi / dừng của ô nhập kiểu trợ lý (dùng chung cho trợ lý và Soạn với AI): đang chạy VÀ dừng được ⇒ hình vuông "Dừng"; còn lại ⇒ mũi tên "Gửi",
 * xám khi chưa có gì để gửi / đang bận. Một nguồn cho dáng và trạng thái — đổi ở đây là đổi cả hai màn.
 */
export function SendStopButton({ canSend, running, canStop, stopping, onSend, onStop }: {
  canSend: boolean; running: boolean; canStop: boolean; stopping?: boolean; onSend: () => void; onStop: () => void;
}) {
  if (running && canStop) {
    return (
      <button type="button" onClick={onStop} disabled={stopping} aria-label={stopping ? "Đang dừng…" : "Dừng"} title={stopping ? "Đang dừng…" : "Dừng"}
              className="grid size-9 shrink-0 place-items-center rounded-full bg-primary text-white transition-colors hover:opacity-85 disabled:opacity-60">
        <Square className="size-3.5" fill="currentColor" strokeWidth={0} />
      </button>
    );
  }
  return (
    <button type="button" onClick={onSend} disabled={!canSend || running} aria-label="Gửi (Enter)" title="Gửi (Enter)"
            className={cn("grid size-9 shrink-0 place-items-center rounded-full text-white transition-colors", canSend && !running ? "bg-primary" : "bg-muted-foreground/30")}>
      <ArrowUp className="size-4" strokeWidth={2.5} />
    </button>
  );
}
