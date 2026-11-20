"use client";

import { Bell, BellOff, CheckCheck, CircleAlert, Clock, ListChecks, MessageSquare, UserPlus, Undo2, Users } from "lucide-react";
import * as fmt from "@/lib/format";
import type { Notice } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useDesk } from "./desk-context";

/** Hộp thông báo hệ thống trong cột "Tin nhắn" (0044). Tín hiệu SSE chỉ báo "có mới" — nội dung đọc lại qua quyền người dùng. */
export function NoticeList() {
  const desk = useDesk();
  if (desk.notices.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center text-muted-foreground">
        <BellOff className="size-7" aria-hidden />
        <p className="t14">Chưa có thông báo</p>
        <p className="t12">Việc mới, lead mới, phiếu chờ duyệt, hồ sơ bị trả lại sẽ hiện ở đây.</p>
      </div>
    );
  }
  return (
    <ul className="min-h-0 flex-1 overflow-y-auto" aria-label="Thông báo">
      {desk.notices.map((n) => (
        <li key={n.id} className="border-b border-border/60 last:border-0">
          <button
            type="button"
            onClick={() => desk.openNotice(n)}
            className={cn("flex w-full items-start gap-2.5 px-3.5 py-2.5 text-left outline-none hover:bg-accent focus-visible:bg-accent", !n.read && "bg-primary/5")}
          >
            <KindIcon kind={n.kind} />
            <span className="flex min-w-0 flex-1 flex-col gap-0.5">
              <span className={cn("text-[14px] leading-snug", n.read ? "font-normal" : "font-medium")}>{n.title}</span>
              {n.body ? <span className="t12 line-clamp-4 text-muted-foreground">{n.body}</span> : null}
              <span className="t12 text-muted-foreground/80">{fmt.datetime(n.created_at)}</span>
            </span>
            {!n.read ? <span className="mt-1.5 size-2 shrink-0 rounded-full bg-primary" aria-label="Chưa đọc" /> : null}
          </button>
        </li>
      ))}
    </ul>
  );
}

const KIND: Record<string, { icon: typeof Bell; color: string }> = {
  lead: { icon: UserPlus, color: "var(--cold)" },
  lead_queue: { icon: UserPlus, color: "var(--cold)" },
  lead_recalled: { icon: Undo2, color: "var(--bad)" },
  approval: { icon: CheckCheck, color: "var(--warn)" },
  approval_done: { icon: CheckCheck, color: "var(--warn)" },
  returned: { icon: CircleAlert, color: "var(--bad)" },
  due: { icon: Clock, color: "var(--bad)" },
  mention: { icon: MessageSquare, color: "var(--cold)" },
  task: { icon: ListChecks, color: "var(--ok)" },
  room: { icon: Users, color: "var(--cold)" },
};

function KindIcon({ kind }: { kind: Notice["kind"] }) {
  const k = KIND[kind] ?? { icon: ListChecks, color: "var(--ok)" };
  const Icon = k.icon;
  return (
    <span className="grid size-7 shrink-0 place-items-center rounded-[7px] text-white" style={{ background: k.color }} aria-hidden>
      <Icon className="size-3.5" />
    </span>
  );
}
