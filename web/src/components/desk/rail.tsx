"use client";

import { Layers, LayoutGrid, ListChecks, LogOut, MessagesSquare, Moon, Pin, Search, Sun, SunMoon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { newKey } from "@/lib/idempotency";
import { railCurrent, railLayout, railWorkspaceKeys, type RailItem, type RailLayout } from "@/lib/rail";
import { badgeText, messagesBadge, railSections, sectionId, type Section } from "@/lib/talk";
import { setTheme } from "@/lib/theme";
import type { WorkspaceSpec } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useMedia } from "@/hooks/use-media";
import { DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Initials } from "@/components/primitives";
import { SfIcon } from "@/components/icons";
import { Orb } from "./column";
import { useChat } from "./chat-context";
import { useDesk } from "./desk-context";

// Thanh bên trái kiểu Lark (Rail.swift): Tìm · Trợ lý · Việc (bàn việc, 0094) · Tin nhắn · phân hệ · người dùng ở đáy.
// ADR 0255: phân hệ KHÔNG vẽ hết — "Theo vai" (gói / công ty khai) + "Đã ghim" (người dùng ghim cả phân hệ) + nút "Tất cả mục" (panel liệt kê hết, mỗi dòng có nút
// ghim; tìm = ⌘K có sẵn). Vai không có mặc định nào ⇒ vẽ hết như trước. Server quyết ai thấy phân hệ / màn nào và phân hệ nào ở nhóm nào; web chỉ vẽ.

/** Tên ngắn dưới biểu tượng thanh bên: phần trước "&" / "—" (vd "Kết nối & kỹ năng" ⇒ "Kết nối") — ô hẹp 76px. */
const railTitle = (t: string) => t.split(/\s+[&—]\s+/)[0];

/** Phím tắt chuyển mục: Alt+1…9 (⌘/Ctrl+số là phím chuyển tab của trình duyệt — trang không chặn được). */
export const sectionKey = (i: number) => (i < 9 ? `Alt+${i + 1}` : "");

export function Rail() {
  const desk = useDesk();
  const chat = useChat();
  const layout = railLayout(desk.manifest);
  const sections = railSections(railWorkspaceKeys(desk.manifest));
  // trình tạo app (0098) là chỗ RIÊNG mở từ một mục (vd App của tôi): thanh bên vẫn sáng mục đó — không trông như "về trợ lý thường"
  const current = sectionId(chat.builder && desk.section.kind === "assistant" ? chat.builder.origin : desk.section);
  const badge = messagesBadge(desk.unread, desk.talk);
  const keyOf = (s: Section) => sectionKey(sections.findIndex((x) => sectionId(x) === sectionId(s)));
  const firstName = desk.manifest?.user.name.trim().split(/\s+/).pop() ?? "bạn";
  // phân hệ đang mở nhưng thanh bên không hiện (mở từ "Tất cả mục") vẫn có một mục sáng; màn hẹp chỉ có phần cốt lõi nên cần cả phân hệ đang mở dù đã ở nhóm
  const here = railCurrent(layout, current.startsWith("ws:") ? current.slice(3) : null);
  const workspaceButton = (w: WorkspaceSpec, className?: string) => {
    const s: Section = { kind: "workspace", key: w.key };
    const on = current === sectionId(s);
    const k = keyOf(s);
    return (
      <RailButton key={w.key} title={w.fixed ? railTitle(w.title) : w.title} selected={on} className={className} help={`${w.title} — ${w.screens.length} màn${k ? ` (${k})` : ""}`} onClick={() => desk.select(s)}>
        <SfIcon name={w.icon} className="size-[18px]" filled={on} />
      </RailButton>
    );
  };

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
        {layout.grouped ? (
          <>
            {/* phân hệ theo vai nằm ngay dưới vạch sau "Tin nhắn" — không thêm vạch, không chữ nhóm (tự hiểu — anh Anh 24/11/2026) */}
            <div role="group" aria-label="Theo vai" className="flex w-full flex-col items-center gap-1.5 max-sm:hidden">{layout.role.map((w) => workspaceButton(w))}</div>
            {layout.pinned.length ? <RailGroup label="Đã ghim">{layout.pinned.map((w) => workspaceButton(w))}</RailGroup> : null}
            {here?.onRail ? workspaceButton(here.ws, "sm:hidden") : null}
            {here && !here.onRail ? (
              <div role="group" aria-label="Đang mở" className="flex w-full flex-col items-center gap-1.5 max-sm:contents">
                <span className="my-0.5 h-px w-9 bg-foreground/10 max-sm:hidden" aria-hidden />
                <span className="t12 text-muted-foreground max-sm:hidden">Đang mở</span>
                {workspaceButton(here.ws)}
              </div>
            ) : null}
          </>
        ) : (
          layout.all.filter((i) => !i.ws.fixed).map((i) => workspaceButton(i.ws))
        )}
      </div>
      {/* phân hệ cố định (``fixed`` — vd "Kết nối & kỹ năng"): mọi vai, ngay trên "Tất cả mục" */}
      {layout.fixed.map((w) => workspaceButton(w))}
      {layout.grouped ? <AllItems layout={layout} /> : null}   {/* ngoài vùng cuộn: luôn thấy, ngay trên người dùng */}
      <PersonaMenu />
    </nav>
  );
}

/** Một nhóm phân hệ trên thanh bên (vạch ngăn + tên nhóm). Màn hẹp (thanh dưới) chỉ có phần cốt lõi + "Tất cả mục" ⇒ nhóm ẩn. */
function RailGroup({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div role="group" aria-label={label} className="flex w-full flex-col items-center gap-1.5 max-sm:hidden">
      <span className="my-0.5 h-px w-9 bg-foreground/10" aria-hidden />
      {children}
    </div>
  );
}

const RAIL_BUTTON = "group flex w-[76px] shrink-0 flex-col items-center gap-1 rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-ring max-sm:w-[60px] max-sm:gap-0.5";

/** Nút "Tất cả mục": panel liệt kê MỌI phân hệ người này thấy, bấm tên ⇒ mở, bấm ghim ⇒ ghim / bỏ ghim cả phân hệ lên thanh bên (của chính mình). Ô tìm là ⌘K sẵn có. */
function AllItems({ layout }: { layout: RailLayout }) {
  const desk = useDesk();
  const narrow = useMedia("(max-width: 639px)");
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const toggle = async (i: RailItem) => {
    setBusy(i.ws.key);
    try {
      const r = await api.command(i.pinned ? "core.unpin" : "core.pin", { workspace: i.ws.key }, newKey());
      if (r.status !== "ok") desk.show(r.error || "Không ghim được", true);
      else await desk.reloadManifest();
    } catch (e) {
      desk.show(e instanceof Error ? e.message : "Không ghim được", true);
    } finally {
      setBusy(null);
    }
  };
  return (
    <>
      <span className="my-0.5 h-px w-9 bg-foreground/10 max-sm:hidden" aria-hidden />
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger className={cn(RAIL_BUTTON, "max-sm:w-[72px]")} title="Mọi phân hệ bạn được dùng — ghim cái hay dùng lên thanh bên" aria-label="Tất cả mục">
          <RailFace title="Tất cả mục" selected={open}>
            <LayoutGrid className="size-[18px]" />
          </RailFace>
        </PopoverTrigger>
        <PopoverContent side={narrow ? "top" : "right"} align={narrow ? "center" : "end"} sideOffset={8}
                        className="max-h-[min(70vh,560px)] w-80 max-w-[calc(100vw-1.5rem)] gap-1 overflow-y-auto p-1.5">
          <button type="button" onClick={() => { setOpen(false); desk.setPaletteOpen(true); }}
                  className="t14 flex h-9 w-full items-center gap-2 rounded-lg border border-border bg-surface px-2.5 text-left text-muted-foreground outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring">
            <Search className="size-4 shrink-0" aria-hidden />
            <span className="min-w-0 flex-1 truncate">Tìm màn, khách, giao dịch…</span>
            <span className="t12 shrink-0" aria-hidden>⌘K</span>
          </button>
          <p className="t12m px-2 pt-2 pb-0.5 text-muted-foreground">Tất cả mục · {layout.all.length}</p>
          <ul>
            {layout.all.map((i) => (
              <li key={i.ws.key} className="flex items-center gap-0.5 rounded-lg hover:bg-accent">
                <button type="button" onClick={() => { setOpen(false); desk.select({ kind: "workspace", key: i.ws.key }); }}
                        className="flex min-w-0 flex-1 items-center gap-2.5 rounded-lg px-2 py-1.5 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring">
                  <span className="grid size-7 shrink-0 place-items-center rounded-lg bg-foreground/[0.06] text-muted-foreground"><SfIcon name={i.ws.icon} className="size-4" /></span>
                  <span className="min-w-0 flex-1">
                    <span className="t14 block truncate">{i.ws.title}</span>
                    <span className="t12 block truncate text-muted-foreground">{allSub(i)}</span>
                  </span>
                </button>
                <button type="button" disabled={busy === i.ws.key} onClick={() => void toggle(i)} aria-pressed={i.pinned}
                        aria-label={`${i.pinned ? "Bỏ ghim" : "Ghim"} ${i.ws.title}`} title={i.pinned ? "Bỏ ghim khỏi thanh bên" : "Ghim lên thanh bên"}
                        className={cn("grid size-8 shrink-0 place-items-center rounded-md outline-none hover:bg-foreground/[0.07] focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50",
                                      i.pinned ? "text-primary" : "text-muted-foreground")}>
                  <Pin className="size-4" {...(i.pinned ? { fill: "currentColor" } : {})} aria-hidden />
                </button>
              </li>
            ))}
          </ul>
        </PopoverContent>
      </Popover>
    </>
  );
}

/** Dòng phụ của một mục trong "Tất cả mục": nằm ở đâu trên thanh bên (một mục chỉ hiện một lần — mặc định theo vai thắng, ghim vẫn ghi nhận). */
function allSub(i: RailItem): string {
  if (i.place === "role") return i.pinned ? "Hiện sẵn theo vai · đã ghim" : "Hiện sẵn theo vai";
  if (i.place === "pinned") return "Đã ghim";
  return `${i.ws.screens.length} màn`;
}

function RailFace({ title, selected, badge = 0, unknown = false, children }: { title: string; selected: boolean; badge?: number; unknown?: boolean; children: React.ReactNode }) {
  return (
    <>
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
      <span className={cn("line-clamp-2 w-full text-center text-[12px] leading-tight max-sm:line-clamp-1", selected ? "font-medium text-primary" : "text-muted-foreground")}>{title}</span>
    </>
  );
}

function RailButton({ title, help, selected = false, badge = 0, unknown = false, className, onClick, children }: { title: string; help: string; selected?: boolean; badge?: number; unknown?: boolean; className?: string; onClick: () => void; children: React.ReactNode }) {
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
      className={cn(RAIL_BUTTON, className)}
    >
      <RailFace title={title} selected={selected} badge={badge} unknown={unknown}>
        {children}
      </RailFace>
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
