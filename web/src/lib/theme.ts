/** Sáng / tối / theo hệ thống — lưu ở localStorage "barista-theme" (layout.tsx đọc trước khi vẽ để không nháy). */
export function setTheme(mode: "light" | "dark" | "system"): void {
  try {
    if (mode === "system") localStorage.removeItem("barista-theme");
    else localStorage.setItem("barista-theme", mode);
  } catch {
    /* không lưu được — vẫn đổi trong phiên */
  }
  const dark = mode === "dark" || (mode === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}
