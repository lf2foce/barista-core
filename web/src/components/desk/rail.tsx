"use client";

import { Layers, ListChecks, LogOut, MessagesSquare, Moon, Search, Sun, SunMoon } from "lucide-react";
import { useEffect, useRef } from "react";
import { badgeText, messagesBadge, railSections, sectionId, type Section } from "@/lib/talk";
import { setTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";
import { DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Initials } from "@/components/primitives";
import { SfIcon } from "@/components/icons";
import { Orb } from "./column";
import { useChat } from "./chat-context";
import { useDesk } from "./desk-context";

// Thanh bên trái kiểu Lark (Rail.swift): Tìm · Trợ lý · Việc (bàn việc, 0094) · Tin nhắn · một mục mỗi phân hệ của manifest · người dùng ở đáy.
// Server quyết ai thấy phân hệ / màn nào; web chỉ vẽ.

/** Phím tắt chuyển mục: Alt+1…9 (⌘/Ctrl+số là phím chuyển tab của trình duyệt — trang không chặn được). */
export const sectionKey = (i: number) => (i < 9 ? `Alt+${i + 1}` : "");

export function Rail() {
  const desk = useDesk();
  const chat = useChat();
  const sections = railSections((desk.manifest?.workspaces ?? []).map((w) => w.key));
  // trình tạo app (0098) là chỗ RIÊNG mở từ một mục (vd App của tôi): thanh bên vẫn sáng mục đó — không trông như "về trợ lý thường"
  const current = sectionId(chat.builder && desk.section.kind === "assistant" ? chat.builder.origin : desk.section);
  const badge = messagesBadge(desk.unread, desk.talk);
  const keyOf = (s: Section) => sectionKey(sections.findIndex((x) => sectionId(x) === sectionId(s)));
  const firstName = desk.manifest?.user.name.trim().split(/\s+/).pop() ?? "bạn";

  return (
    // < 640px: thanh dưới (order-last trong khung cột dọc của Shell), một hàng cuộn ngang; vạch ngăn ẩn, mục phân hệ trải phẳng vào hàng (contents)
    <nav className="no-scrollbar flex h-full w-20 shrink-0 flex-col items-center gap-1.5 border-r border-hairline bg-bar pt-3 pb-3.5 max-sm:order-last max-sm:h-auto max-sm:w-full max-sm:flex-row max-sm:gap-0.5 max-sm:overflow-x-auto max-sm:border-t max-sm:border-r-0 max-sm:px-1 max-sm:pt-1.5 max-sm:pb-[max(6px,env(safe-area-inset-bottom))]" aria-label="Thanh bên trái">
      <RailButton title="Tìm" help="Tìm khách, căn, giao dịch, màn (⌘K)" onClick={() => desk.setPaletteOpen(true)}>
        <Search className="size-[18px]" />
      </RailButton>
      <span className="my-1 h-px w-9 bg-foreground/10 max-sm:hidden" aria-hidden />
      <RailButton title="Trợ lý" selected={current === "assistant"} help={`Trợ lý của ${firstName} (${keyOf({ kind: "assistant" })})`} onClick={() => (chat.builder ? chat.exitBuilder() : desk.select({ kind: "assistant" }))}>   {/* đang ở trình tạo app ⇒ bấm Trợ lý là thoát về trợ lý thường */}
        <Orb size={26} busy={!!chat.live} />
      </RailButton>
      <RailButton
        title="Việc"
        badge={desk.deskPage?.count ?? 0}
        unknown={!desk.deskPage?.count && (!!desk.deskPage?.unchecked || desk.deskFailed)}
        selected={current === "desk"}
        help={`Mọi việc của bạn ở một chỗ: cần quyết, cần làm, đang theo dõi (${keyOf({ kind: "desk" })})`}
        onClick={() => desk.select({ kind: "desk" })}
      >
        <ListChecks className="size-[18px]" {...(current === "desk" ? { strokeWidth: 2.25 } : {})} />
      </RailButton>
      <RailButton
        title="Tin nhắn"
        badge={badge}
        selected={current === "messages"}
        help={`Phòng, trao đổi trên hồ sơ, thông báo, phiếu duyệt (${keyOf({ kind: "messages" })})`}
        onClick={() => desk.select({ kind: "messages" })}
      >
        <MessagesSquare className="size-[18px]" {...(current === "messages" ? { fill: "currentColor", fillOpacity: 0.22, strokeWidth: 2.25 } : {})} />
      </RailButton>
      <span className="my-1 h-px w-9 bg-foreground/10 max-sm:hidden" aria-hidden />
      <div className="no-scrollbar flex min-h-0 w-full flex-1 flex-col items-center gap-1.5 overflow-y-auto max-sm:contents">
        {(desk.manifest?.workspaces ?? []).map((w) => {
          const s: Section = { kind: "workspace", key: w.key };
          const on = current === sectionId(s);
          const k = keyOf(s);
          return (
            <RailButton key={w.key} title={w.title} selected={on} help={`${w.title} — ${w.screens.length} màn${k ? ` (${k})` : ""}`} onClick={() => desk.select(s)}>
              <SfIcon name={w.icon} className="size-[18px]" filled={on} />
            </RailButton>
          );
        })}
      </div>
      <PersonaMenu />
    </nav>
  );
}

function RailButton({ title, help, selected = false, badge = 0, unknown = false, onClick, children }: { title: string; help: string; selected?: boolean; badge?: number; unknown?: boolean; onClick: () => void; children: React.ReactNode }) {
  // mục đang chọn luôn trong tầm mắt (thanh dưới điện thoại cuộn ngang; thanh trái nhiều phân hệ cuộn dọc)
  const ref = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (selected) ref.current?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [selected]);
  return (
    <button
      ref={ref}
      type="button"
      onClick={onClick}
      title={help}
      aria-label={badge > 0 ? `${title}, ${badge} chưa đọc` : title}
      aria-current={selected ? "page" : undefined}
      className="group flex w-[76px] shrink-0 flex-col items-center gap-1 rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-ring max-sm:w-[64px] max-sm:gap-0.5"
    >
      <span
        className={cn(
          "relative grid h-[34px] w-11 place-items-center rounded-[10px] transition-colors max-sm:h-7",
          selected ? "bg-primary/15 text-primary" : "text-muted-foreground group-hover:bg-foreground/[0.07]",
        )}
      >
        {children}
        {unknown ? <span className="absolute -top-0.5 -right-0.5 size-2.5 rounded-full border-2 border-bar bg-muted-foreground" title="Có mục chưa kiểm được" /> : null}
        {badge > 0 ? (
          <span className="tnum absolute -top-1 -right-1.5 grid min-h-4 min-w-4 place-items-center rounded-full bg-bad px-1 text-[10px] leading-none font-semibold text-white">
            {badgeText(badge)}
          </span>
        ) : null}
      </span>
      <span className={cn("line-clamp-2 w-[76px] text-center text-[12px] leading-tight max-sm:line-clamp-1 max-sm:w-[64px]", selected ? "font-medium text-primary" : "text-muted-foreground")}>{title}</span>
    </button>
  );
}

/** Người đang dùng (ảnh cam như app Mac) ở đáy thanh: vai, đổi công ty, giao diện sáng / tối, đăng xuất / đổi vai. */
export function PersonaMenu() {
  const desk = useDesk();
  const m = desk.manifest;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger className="mt-2 shrink-0 rounded-full outline-none max-sm:mx-2 max-sm:mt-0 max-sm:self-center focus-visible:ring-2 focus-visible:ring-ring" aria-label="Tài khoản" title={m ? `${m.user.name} · ${m.user.role_label}` : "Tài khoản"}>
        <Initials name={m?.user.name ?? "?"} size={34} />
      </DropdownMenuTrigger>
      <DropdownMenuContent side="right" align="end" className="w-64">
        {m ? (
          <DropdownMenuGroup>
            <DropdownMenuLabel>
              <span className="t14m block text-foreground">{m.user.name}</span>
              <span className="t12 block text-muted-foreground">
                {m.user.role_label} · {m.tenant.name}
              </span>
            </DropdownMenuLabel>
          </DropdownMenuGroup>
        ) : null}
        <DropdownMenuSeparator />
        {desk.session?.mode === "token" && desk.session.tenants.length > 1
          ? desk.session.tenants
              .filter((t) => t.id !== m?.tenant.id)
              .map((t) => (
                <DropdownMenuItem key={t.id} onClick={() => void desk.switchTenant(t.id)}>
                  <Layers /> Đổi sang {t.name}
                </DropdownMenuItem>
              ))
          : null}
        {(m?.account ?? []).map((s) => (
          <DropdownMenuItem key={s.key} onClick={() => desk.openScreen(s)}>
            <SfIcon name={s.icon} /> {s.title}
          </DropdownMenuItem>
        ))}
        {m?.account?.length ? <DropdownMenuSeparator /> : null}
        <DropdownMenuItem onClick={() => setTheme("light")}>
          <Sun /> Giao diện sáng
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => setTheme("dark")}>
          <Moon /> Giao diện tối
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => setTheme("system")}>
          <SunMoon /> Theo hệ thống
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => void desk.logout()}>
          <LogOut /> {desk.session?.mode === "token" ? "Đăng xuất" : "Đổi vai…"}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
