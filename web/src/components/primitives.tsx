"use client";

import { Check, CloudAlert, Copy, RefreshCw } from "lucide-react";
import { useState } from "react";
import { clock, pillStyle } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Nhãn trạng thái server gửi (badge) — viên thuốc tô nhạt theo tông. */
export function Pill({ text, tone, className }: { text: string; tone?: string | null; className?: string }) {
  if (!text) return null;
  return (
    <span className={cn("t12m inline-block max-w-full truncate rounded-full px-2 py-[3px]", className)} style={pillStyle(tone)}>
      {text}
    </span>
  );
}

export function Initials({ name, size = 34 }: { name: string; size?: number }) {
  const letter = name.trim().split(/\s+/).pop()?.[0]?.toUpperCase() ?? "?";
  return (
    <span
      className="inline-flex shrink-0 items-center justify-center rounded-full font-semibold text-white"
      style={{ width: size, height: size, fontSize: size * 0.42, background: "linear-gradient(180deg, #ffa45c, #f0642d)" }}
      aria-hidden
    >
      {letter}
    </span>
  );
}

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  }
}

export function CopyButton({ text, label = "Sao chép", className }: { text: string; label?: string; className?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      onClick={async () => {
        if (await copyText(text)) {
          setDone(true);
          setTimeout(() => setDone(false), 1500);
        }
      }}
      className={cn("t12m inline-flex items-center gap-1.5 rounded-lg border border-border bg-surface px-2.5 py-1.5 hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring", className)}
      aria-label={label}
    >
      {done ? <Check className="size-3.5 text-ok" /> : <Copy className="size-3.5" />}
      {done ? "Đã chép" : label}
    </button>
  );
}

/** Nút theo `style` của hành động server: primary · destructive · còn lại. */
export function actionClass(style: string): string {
  const base = "t14m inline-flex h-8 items-center justify-center gap-1.5 whitespace-nowrap rounded-lg px-3 outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50";
  if (style === "primary") return cn(base, "bg-primary text-primary-foreground hover:bg-primary/90");
  if (style === "destructive") return cn(base, "bg-bad/10 text-bad hover:bg-bad/20");
  // nút một chạm (điểm danh, tham gia cuộc gọi…): trên điện thoại cao 44px và giãn hết hàng — chạm bằng ngón tay, không trượt sang nút bên cạnh
  if (style === "instant") return cn(base, "border border-border bg-surface hover:bg-accent max-sm:h-11 max-sm:grow max-sm:px-4");
  return cn(base, "border border-border bg-surface hover:bg-accent");
}

export function Spinner({ className }: { className?: string }) {
  return <span className={cn("inline-block size-4 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-primary", className)} role="status" aria-label="Đang tải" />;
}

export function ErrorLine({ children }: { children: React.ReactNode }) {
  return (
    <p className="t14 flex items-start gap-2 text-bad" role="alert">
      {children}
    </p>
  );
}

/**
 * Màn / thẻ không tải được (rà bố cục 18/11): thay một dòng "Lỗi hệ thống…" trơ trọi bằng thông báo + nút "Thử lại" + giờ lỗi (giờ Việt Nam) — người
 * biết lỗi lúc nào, bấm lại ngay không phải đóng mở màn. `compact`: màn VẪN đang hiện bản cũ (tải lại hỏng) ⇒ một dòng mỏng trên nội dung cũ.
 */
export function LoadError({ message, at, onRetry, busy = false, compact = false }: { message: string; at?: number | null; onRetry?: () => void; busy?: boolean; compact?: boolean }) {
  const when = at ? `lúc ${clock(new Date(at))}` : "";
  const retry = onRetry ? (
    <button type="button" onClick={onRetry} disabled={busy} className={actionClass("default")}>
      <RefreshCw className={cn("size-3.5", busy && "animate-spin")} aria-hidden />
      {busy ? "Đang thử lại…" : "Thử lại"}
    </button>
  ) : null;
  if (compact) {
    return (
      <div role="alert" className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5 rounded-xl bg-bad/8 px-3 py-2">
        <CloudAlert className="size-4 shrink-0 text-bad" aria-hidden />
        <p className="t12 min-w-0 flex-1"><span className="t12m">Chưa tải lại được</span> — đang xem bản trước. {message}{when ? ` · ${when}` : ""}</p>
        {retry}
      </div>
    );
  }
  return (
    <div role="alert" className="flex flex-col items-center gap-2 rounded-[14px] bg-surface2 px-4 py-10 text-center">
      <CloudAlert className="size-7 text-bad" aria-hidden />
      <p className="t16">Chưa tải được</p>
      <p className="t14 max-w-prose text-muted-foreground select-text">{message}</p>
      {when ? <p className="t12 tnum text-muted-foreground">{when} — thử lại sau ít phút; còn lỗi thì báo quản trị kèm giờ này</p> : null}
      {retry ? <div className="mt-1">{retry}</div> : null}
    </div>
  );
}
