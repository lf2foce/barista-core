"use client";

import { useCallback, useState } from "react";

// Độ rộng CỘT GIỮA của khung (danh sách phân hệ / trò chuyện…): kéo mép phải để đổi, nhớ riêng cho từng loại cột trong trình duyệt.
// Cột trò chuyện cần rộng (mặc định như cũ); cột chọn màn của phân hệ chỉ là danh sách nút nên mặc định hẹp hơn.

export type ColumnKind = "chat" | "menu";

const LIMITS: Record<ColumnKind, { min: number; max: number; def: string }> = {
  chat: { min: 300, max: 720, def: "clamp(330px,34%,440px)" },
  menu: { min: 200, max: 520, def: "264px" },
};
const key = (k: ColumnKind) => `barista.col.${k}`;

function read(kind: ColumnKind): number | null {
  try {
    const v = Number(localStorage.getItem(key(kind)));
    return Number.isFinite(v) && v >= LIMITS[kind].min && v <= LIMITS[kind].max ? v : null;
  } catch {
    return null;
  }
}

/** Độ rộng đang dùng (`css` cho biến CSS) + tay nắm kéo; mặc định theo loại cột cho tới khi người dùng kéo. */
export function useColumnWidth(kind: ColumnKind) {
  // cả hai loại cột đọc một lần khi mở khung (khung chỉ vẽ sau khi tải xong menu ⇒ không lệch với bản dựng sẵn); đổi loại cột ⇒ dùng giá trị của loại đó
  const [saved, setSaved] = useState<Record<ColumnKind, number | null>>(() => ({ chat: read("chat"), menu: read("menu") }));
  const px = saved[kind];

  const set = useCallback(
    (v: number | null) => {
      const next = v === null ? null : Math.min(LIMITS[kind].max, Math.max(LIMITS[kind].min, Math.round(v)));
      setSaved((cur) => ({ ...cur, [kind]: next }));
      try {
        if (next === null) localStorage.removeItem(key(kind));
        else localStorage.setItem(key(kind), String(next));
      } catch {
        /* không lưu được thì thôi — vẫn dùng trong phiên */
      }
    },
    [kind],
  );

  const css = px === null ? LIMITS[kind].def : `${px}px`;
  const onPointerDown = (e: React.PointerEvent<HTMLElement>) => {
    const col = e.currentTarget.parentElement;
    if (!col) return;
    e.preventDefault();
    const left = col.getBoundingClientRect().left;
    const move = (ev: PointerEvent) => set(ev.clientX - left);
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      document.body.style.removeProperty("cursor");
    };
    document.body.style.cursor = "col-resize";
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  const onKeyDown = (e: React.KeyboardEvent<HTMLElement>) => {
    const cur = px ?? (e.currentTarget.parentElement?.getBoundingClientRect().width ?? LIMITS[kind].min);
    if (e.key === "ArrowLeft") set(cur - 24);
    else if (e.key === "ArrowRight") set(cur + 24);
    else if (e.key === "Home") set(null);
    else return;
    e.preventDefault();
  };
  return { css, onPointerDown, onKeyDown, reset: () => set(null) };
}

/** Mép kéo ở bên phải cột (chỉ từ 980px — dưới đó cột chiếm cả màn). Nhấp đúp ⇒ về độ rộng mặc định; ← → chỉnh bằng bàn phím. */
export function ColumnResizer({ w }: { w: ReturnType<typeof useColumnWidth> }) {
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label="Kéo để đổi độ rộng cột (nhấp đúp: về mặc định)"
      tabIndex={0}
      onPointerDown={w.onPointerDown}
      onKeyDown={w.onKeyDown}
      onDoubleClick={w.reset}
      className="absolute inset-y-0 -right-1 z-20 hidden w-2 cursor-col-resize touch-none outline-none after:absolute after:inset-y-0 after:left-[3px] after:w-px after:bg-transparent hover:after:bg-primary/40 focus-visible:after:bg-primary min-[980px]:block"
    />
  );
}
