"use client";

import { useEffect } from "react";

/**
 * Giữ khung ứng dụng ĐỨNG YÊN khi bàn phím điện thoại bật (iOS Safari không co khung nhìn bố cục như Chrome Android: nó cuộn cả trang lên để lộ
 * ô nhập ⇒ đầu trang trượt mất, khung "trôi" như web thường). Cách làm: chiều cao ứng dụng = VÙNG NHÌN THỰC (`visualViewport`, đã trừ bàn phím) qua biến
 * `--app-h`; trang không bao giờ cuộn (`overflow: hidden` ở CSS), nên chỉ danh sách tin nhắn cuộn bên trong. Máy tính: bằng chiều cao cửa sổ, không đổi gì.
 */
export function ViewportFix() {
  useEffect(() => {
    const vv = window.visualViewport;
    const root = document.documentElement;
    const apply = () => {
      root.style.setProperty("--app-h", `${Math.round(vv?.height ?? window.innerHeight)}px`);
      if (window.scrollY !== 0) window.scrollTo(0, 0);   // iOS tự cuộn trang để lộ ô nhập — trả về đầu, khung đã co theo bàn phím
    };
    apply();
    vv?.addEventListener("resize", apply);
    vv?.addEventListener("scroll", apply);
    window.addEventListener("orientationchange", apply);
    return () => {
      vv?.removeEventListener("resize", apply);
      vv?.removeEventListener("scroll", apply);
      window.removeEventListener("orientationchange", apply);
      root.style.removeProperty("--app-h");
    };
  }, []);
  return null;
}
