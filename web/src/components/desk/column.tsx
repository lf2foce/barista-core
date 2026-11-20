"use client";

import { ChevronLeft, Menu, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";
import { useDesk } from "./desk-context";

/** Quả cầu trợ lý (Orb.swift gọn): gradient xanh + lấp lánh; `busy` ⇒ nhịp thở. */
export function Orb({ size = 40, busy = false }: { size?: number; busy?: boolean }) {
  return (
    <span
      className={cn("grid shrink-0 place-items-center rounded-full text-white shadow-sm", busy && "animate-pulse")}
      style={{ width: size, height: size, background: "radial-gradient(circle at 30% 25%, #7fb5ff, #007aff 55%, #5856d6)" }}
      aria-hidden
    >
      <Sparkles style={{ width: size * 0.45, height: size * 0.45 }} />
    </span>
  );
}

export const headerBtn =
  "grid size-8 shrink-0 place-items-center rounded-md text-muted-foreground outline-none hover:bg-foreground/5 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring";   // không nền đặc: chỉ biểu tượng, nền nhạt khi rê chuột

/** Ẩn / hiện thanh bên trái (Ctrl+Shift+S) — góc trên trái của cột, như nút sidebar của Notes / Finder. */
export function RailToggle() {
  const desk = useDesk();
  const label = desk.railHidden ? "Hiện thanh bên trái (Ctrl+Shift+S)" : "Ẩn thanh bên trái (Ctrl+Shift+S)";
  return (
    <button type="button" onClick={desk.toggleRail} className="grid size-8 shrink-0 place-items-center rounded-md text-muted-foreground outline-none hover:bg-foreground/5 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring" aria-label={label} title={label} aria-pressed={!desk.railHidden}>
      <Menu className="size-4" />
    </button>
  );
}

/** MỌI đầu ngăn (cột, khung làm việc, ngăn tin nhắn / trò chuyện) dùng đúng lớp này: cao cố định 64px, NỔI trên nền của chính ngăn — không dải nền,
 *  không đường kẻ dưới (anh Anh 18/11: cùng kiểu ô nhập trợ lý / đầu khung làm việc). Thêm đệm ngang riêng ở nơi dùng. */
export const PANE_HEADER = "flex h-16 shrink-0 items-center gap-2.5 bg-transparent";

/** Đầu cột: cùng chiều cao với đầu khung làm việc (PANE_HEADER) ⇒ các đường kẻ ngang thẳng hàng. */
export function ColumnHeader({
  title,
  subtitle,
  back,
  leading,
  children,
}: {
  title: string;
  subtitle?: string;
  back?: () => void;
  leading?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <header className={cn(PANE_HEADER, "px-3")}>
      <RailToggle />
      {back ? (
        <button type="button" onClick={back} className="grid size-8 shrink-0 place-items-center rounded-lg border border-border bg-surface outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring" aria-label="Về danh sách" title="Về danh sách">
          <ChevronLeft className="size-4" />
        </button>
      ) : null}
      {leading}
      <div className="min-w-0 flex-1">
        <h2 className="t16 truncate">{title}</h2>
        {subtitle ? <p className="t12 truncate text-muted-foreground">{subtitle}</p> : null}
      </div>
      {children}
    </header>
  );
}
