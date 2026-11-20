"use client";

import { Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";
import { useDesk } from "@/components/desk/desk-context";

/** Nút "Vì sao?" trên ô số / biểu đồ (0220 lát 7): câu hỏi do server điền sẵn từ chính khối (chỉ số, chiều, kỳ, lọc) ⇒ gửi vào chat; trợ lý dùng kỹ năng
 *  tìm vì sao trên số chuẩn bằng quyền người hỏi. Web không tự ghép câu. */
export function WhyButton({ prompt, className }: { prompt: string; className?: string }) {
  const desk = useDesk();
  return (
    <button type="button" onClick={(e) => { e.stopPropagation(); desk.ask(prompt); }} title={prompt} aria-label="Hỏi trợ lý vì sao số này thay đổi"
            className={cn("t12m inline-flex h-6 shrink-0 items-center gap-1 rounded-full px-2 text-primary outline-none hover:bg-primary/10 focus-visible:ring-2 focus-visible:ring-ring", className)}>
      <Sparkles className="size-3" aria-hidden />
      Vì sao?
    </button>
  );
}
