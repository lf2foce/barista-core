"use client";

import { Pin } from "lucide-react";
import { cn } from "@/lib/utils";
import { IconTile, screenTint } from "@/components/icons";
import { ColumnHeader } from "./column";
import { screenItemId, useDesk } from "./desk-context";

/** Cột một phân hệ (Rail.swift WorkspaceColumn): các màn theo nhóm, ghim theo vai đánh dấu ghim; bấm ⇒ mở trên khung làm việc. */
export function WorkspaceColumn({ wsKey }: { wsKey: string }) {
  const desk = useDesk();
  const w = desk.manifest?.workspaces.find((x) => x.key === wsKey);
  const screens = w?.screens ?? [];
  const groups = [...new Set(screens.map((s) => s.group))];
  const pinned = new Set(desk.quickScreens.map((s) => screenItemId(s)));
  return (
    <nav aria-label={`Màn ${w?.title ?? "phân hệ"}`} className="flex h-full min-h-0 flex-col bg-surface">
      <ColumnHeader title={w?.title ?? ""} subtitle={`${screens.length} màn · dữ liệu đúng quyền của bạn`} />
      <div className="min-h-0 flex-1 overflow-y-auto px-1.5 py-2">
        {groups.map((g, gi) => {
          // GẤP LẠI (rà menu 20/11/2026): "Nâng cao" / "Thiết lập" là màn dùng hiếm; phân hệ nhiều nhóm (> 4) chỉ mở 3 nhóm đầu. Nhóm gấp tự mở khi đang đứng
          // ở một màn của nó, và luôn ghi số màn để người biết còn gì bên trong.
          const fold = !!g && (g === "Nâng cao" || g === "Thiết lập" || (groups.length > 4 && gi >= 3));
          const Wrap = fold ? "details" : "div";
          return (
          <section key={g || "_"} aria-label={g || w?.title}>
            <Wrap {...(fold ? { open: screens.some((s) => s.group === g && desk.activeId === screenItemId(s)) } : {})}>
            {g ? (fold ? <summary className="t12m cursor-pointer select-none px-3 pt-3 pb-1 text-muted-foreground">{g} · {screens.filter((s) => s.group === g).length} màn</summary> : <h3 className="t12m px-3 pt-3 pb-1 text-muted-foreground">{g}</h3>) : null}
            <ul>
              {screens
                .filter((s) => s.group === g)
                .map((s) => {
                  const id = screenItemId(s);
                  const on = desk.activeId === id;
                  return (
                    <li key={id}>
                      <button
                        type="button"
                        onClick={() => desk.openScreen(s)}
                        aria-current={on ? "true" : undefined}
                        className={cn(
                          "flex w-full items-center gap-2.5 rounded-[10px] px-2.5 py-[7px] text-left outline-none focus-visible:ring-2 focus-visible:ring-ring",
                          on ? "bg-primary/12" : "hover:bg-foreground/5",
                        )}
                      >
                        <IconTile icon={s.icon} tint={screenTint(s)} size={28} />
                        <span className={cn("min-w-0 flex-1 truncate text-[14px]", on ? "font-medium" : "font-normal")}>{s.title}</span>
                        {s.badge ? <span className="t12m shrink-0 rounded-full bg-primary/12 px-2 py-px text-primary">{s.badge}</span> : null}
                        {pinned.has(id) ? (
                          <Pin className="size-3 shrink-0 text-muted-foreground/70" fill="currentColor" aria-label="Màn ghim của vai">
                            <title>Màn ghim của vai</title>
                          </Pin>
                        ) : null}
                      </button>
                    </li>
                  );
                })}
            </ul>
            </Wrap>
          </section>
          );
        })}
      </div>
    </nav>
  );
}
