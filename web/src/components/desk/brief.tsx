"use client";

import { ArrowRight, Check, ChevronRight, RotateCcw, ShieldAlert, XCircle } from "lucide-react";
import type { Settled } from "@/lib/brief";
import type { Brief } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useDesk } from "./desk-context";

/**
 * BẢN CHẠY THỬ CHUẨN (ADR 0253) — MỘT hình cho mọi thẻ chờ duyệt (thẻ đề xuất ở chat, thẻ buổi sáng, phiếu duyệt, thông báo): hồ sơ nào (bấm mở) ·
 * đổi gì từ gì thành gì · vì sao · đảo được không. Đọc xong trong vài giây; không có phần nào thì không vẽ phần đó.
 */
export function BriefView({ brief, showTitle = false, className }: { brief: Brief; showTitle?: boolean; className?: string }) {
  const desk = useDesk();
  const s = brief.subject;
  const rev = brief.reversible;
  return (
    <div className={cn("flex min-w-0 flex-col gap-1.5", className)}>
      {showTitle ? <p className="t14m">{brief.title}</p> : null}
      {s ? (
        s.open ? (
          <button type="button" onClick={() => void desk.openTarget(s.open!.query, s.open!.input, s.label)} className="t12m flex w-fit max-w-full items-center gap-1 rounded-md text-primary outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring">
            <span className="truncate">{s.label}</span>
            <ChevronRight className="size-3 shrink-0" aria-hidden />
          </button>
        ) : (
          <p className="t12m truncate text-muted-foreground">{s.label}</p>
        )
      ) : null}
      {brief.changes.length ? (
        <ul className="flex flex-col gap-0.5" aria-label="Thay đổi">
          {brief.changes.map((c) => (
            <li key={c.label} className="t12 flex min-w-0 flex-wrap items-center gap-x-1.5">
              <span className="text-muted-foreground">{c.label}</span>
              {c.before ? <span className="text-muted-foreground line-through decoration-muted-foreground/40">{c.before}</span> : null}
              {c.before ? <ArrowRight className="size-3 shrink-0 text-muted-foreground" aria-hidden /> : null}
              <span className="t12m">{c.after}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {brief.reason ? <p className="t12 text-muted-foreground">Vì sao: {brief.reason}</p> : null}
      {rev.ok !== null ? (
        <p className={cn("t12 flex items-start gap-1", rev.ok ? "text-muted-foreground" : "text-warn")}>
          {rev.ok ? <RotateCcw className="mt-0.5 size-3 shrink-0" aria-hidden /> : <ShieldAlert className="mt-0.5 size-3 shrink-0" aria-hidden />}
          <span>{rev.ok ? "Đảo được" : "Không đảo được"}{rev.how ? ` — ${rev.how}` : ""}</span>
        </p>
      ) : null}
    </div>
  );
}

/** Dòng kết quả của thẻ đã quyết (server nói trạng thái): "Đã xong" · "Đã bỏ đề xuất" · "Đã gửi duyệt" kèm đường mở ĐÚNG phiếu. */
export function SettledLine({ settled, className }: { settled: Settled; className?: string }) {
  const desk = useDesk();
  return (
    <p className={cn("t12m flex flex-wrap items-center gap-1.5", settled.tone === "ok" ? "text-ok" : "text-muted-foreground", className)}>
      {settled.tone === "ok" ? <Check className="size-3.5" aria-hidden /> : <XCircle className="size-3.5" aria-hidden />}
      {settled.label}
      {settled.approvalId ? (
        <button type="button" onClick={() => void desk.openTarget("core.approval", { approval_id: settled.approvalId! }, "Phiếu duyệt")} className="inline-flex items-center gap-0.5 rounded-md text-primary outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring">
          Mở phiếu <ChevronRight className="size-3" aria-hidden />
        </button>
      ) : null}
    </p>
  );
}
