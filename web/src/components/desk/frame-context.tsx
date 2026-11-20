"use client";

import { createContext, useContext, useEffect, type ReactNode } from "react";
import type { Page } from "@/lib/types";

/**
 * Khung thẻ của khung làm việc (CardFrame) và nội dung TỰ VẼ KHUNG (0231): khối soạn với AI có đầu ngăn + viền riêng ⇒ thẻ bọc ngoài không vẽ đầu /
 * viền nữa (tránh lồng hai lớp), mà giao bốn nút của khung (tải lại · phóng to · ghim · đóng) cho khối đặt vào góc đầu của nó.
 */
type FrameShell = { actions: ReactNode; setBare: (bare: boolean) => void };

const FrameCtx = createContext<FrameShell | null>(null);

export const FrameShellProvider = FrameCtx.Provider;

/** Trang tự vẽ khung: thẻ có khối soạn với AI. Một chỗ định nghĩa — dùng cho cả phân hệ một màn (FullFrame) lẫn thẻ trong khung làm việc. */
export function isSelfFramed(page: Page | null | undefined): boolean {
  return page?.kind === "card" && page.blocks.some((b) => b.type === "composer");
}

/** Nội dung báo cho thẻ bọc ngoài rằng nó tự vẽ khung (hoặc thôi) — ngoài khung làm việc thì không làm gì. */
export function useBareFrame(bare: boolean): void {
  const shell = useContext(FrameCtx);
  useEffect(() => {
    shell?.setBare(bare);
  }, [shell, bare]);
}

/** Nút của khung để khối tự vẽ khung đặt vào đầu của nó; ngoài khung làm việc (hoặc thẻ bọc còn tự vẽ đầu) ⇒ null. */
export function useFrameActions(): ReactNode | null {
  return useContext(FrameCtx)?.actions ?? null;
}

/**
 * Đầu thẻ gọn (rà bố cục 18/11): màn đặt BỘ LỌC CHÍNH (vd Kỳ) và nút hỏi trợ lý lên cùng hàng tên thẻ, khoảng ngày của thẻ thành phụ đề —
 * ô số lên sát đầu thẻ. `tools` là chỗ cắm (portal) trong đầu thẻ; `setSub` thay phụ đề của đầu thẻ. Chỉ MỘT màn nhận (màn ngoài cùng của thẻ):
 * màn nhận xong đặt lại null cho cây con ⇒ thẻ / màn lồng bên trong không giành chỗ. Ngoài khung làm việc ⇒ null, màn vẽ tại chỗ như cũ.
 */
export type FrameHead = { tools: HTMLElement | null; setSub: (sub: string | null) => void };

const HeadCtx = createContext<FrameHead | null>(null);

export const FrameHeadProvider = HeadCtx.Provider;

export function useFrameHead(): FrameHead | null {
  return useContext(HeadCtx);
}

/** Đặt phụ đề đầu thẻ theo nội dung (vd "Tháng này · 01/11 → 30/11"); rời màn / đổi sang rỗng ⇒ trả lại phụ đề mặc định. */
export function useFrameSub(head: FrameHead | null | undefined, sub: string | null | undefined): void {
  useEffect(() => {
    if (!head) return;
    head.setSub(sub || null);
    return () => head.setSub(null);
  }, [head, sub]);
}
