"use client";

import { ChevronDown } from "lucide-react";
import { useState } from "react";
import { IconTile, screenTint } from "@/components/icons";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import { screenItemId, useDesk } from "./desk-context";
import { useFullScreen } from "./solo";

/**
 * Màn TRỌN KHUNG (`full_frame`, vd "Soạn") không còn cột danh sách màn của phân hệ ⇒ tên ở đầu ngăn là MENU sang các màn khác của phân hệ (cùng danh sách,
 * cùng nhóm, cùng biểu tượng như cột). Chọn màn khác ⇒ về bố cục thường (cột danh sách + khung làm việc); quay lại = chọn màn này ở đầu danh sách.
 * Không phải màn trọn khung (thẻ trong khung làm việc, hộp thoại) ⇒ chỉ tên.
 */
export function ScreensMenu({ title }: { title: string }) {
  const desk = useDesk();
  const full = useFullScreen();
  const [open, setOpen] = useState(false);
  const screens = full?.ws.screens ?? [];
  if (!full || screens.length < 2) return <h1 className="t16 truncate">{title}</h1>;
  const groups = [...new Set(screens.map((s) => s.group))];
  return (
    <>
    {/* màn rộng: cột danh sách màn đang hiện bên cạnh (Shell) ⇒ chỉ tên; hẹp hơn ⇒ tên là menu */}
    <h1 className="t16 hidden truncate min-[1600px]:block">{title}</h1>
    <span className="min-[1600px]:hidden">
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger className="t16 -mx-1.5 inline-flex max-w-full items-center gap-1 rounded-md px-1.5 outline-none hover:bg-foreground/5 focus-visible:ring-2 focus-visible:ring-ring"
                      aria-label={`${full.ws.title} — chọn màn`}>
        <span className="truncate">{title}</span>
        <ChevronDown className="size-4 shrink-0 text-muted-foreground" aria-hidden />
      </PopoverTrigger>
      <PopoverContent align="start" className="w-72 gap-0.5 p-1.5">
        <p className="t12m px-2 pt-1 text-muted-foreground">{full.ws.title}</p>
        {groups.map((g) => (
          <div key={g || "_"} className="flex flex-col gap-0.5">
            {g ? <p className="t12m px-2 pt-2 pb-0.5 text-muted-foreground">{g}</p> : null}
            {screens.filter((s) => s.group === g).map((s) => {
              const on = screenItemId(s) === full.id;
              return (
                <button key={s.key} type="button" aria-current={on ? "page" : undefined}
                        onClick={() => { setOpen(false); if (!on) desk.openScreen(s); }}
                        className={cn("t14 flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring",
                                      on && "t14m bg-primary/10 text-primary")}>
                  <IconTile icon={s.icon} tint={screenTint(s)} size={24} />
                  <span className="min-w-0 flex-1 truncate">{s.title}</span>
                </button>
              );
            })}
          </div>
        ))}
      </PopoverContent>
    </Popover>
    </span>
    </>
  );
}
