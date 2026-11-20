// Phím tắt của khung (Rail.swift): ⌘K / Ctrl+K tìm; ẩn / hiện thanh bên trái; chuyển mục 1…9.
// Trình duyệt giữ ⌘/Ctrl+số để chuyển tab (trang không chặn được) ⇒ chuyển mục bằng Alt+số (Mac: ⌥ số, đọc theo `code` vì ⌥ đổi ký tự).

export type KeyLike = { key: string; code: string; altKey: boolean; ctrlKey: boolean; metaKey: boolean; shiftKey: boolean };
export type Shortcut = { kind: "palette" } | { kind: "rail" } | { kind: "section"; index: number };

export function shortcutFor(e: KeyLike): Shortcut | null {
  const k = e.key.toLowerCase();
  if ((e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && k === "k") return { kind: "palette" };
  // Ctrl+Shift+S (mọi máy) hoặc ⌃⌘S như app Mac
  if (e.code === "KeyS" && e.ctrlKey && !e.altKey && (e.shiftKey || e.metaKey)) return { kind: "rail" };
  const digit = /^Digit([1-9])$/.exec(e.code);
  if (digit && e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey) return { kind: "section", index: Number(digit[1]) - 1 };
  return null;
}
