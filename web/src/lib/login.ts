import type { DevLogin } from "./types";

/** Nhóm các vai dev theo `group` (giữ thứ tự xuất hiện) — cùng cách app Mac chia "Ban giám đốc / Quản lý / Chức năng / Nhân viên". */
export function groupLogins(logins: DevLogin[]): [string, DevLogin[]][] {
  const order: string[] = [];
  for (const l of logins) {
    const g = l.group || "Khác";
    if (!order.includes(g)) order.push(g);
  }
  return order.map((g) => [g, logins.filter((l) => (l.group || "Khác") === g)]);
}

/** Chỉ cho quay lại đường dẫn nội bộ (chống chuyển hướng mở ra trang ngoài). */
export function safeNext(next: string | null): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) return "/";
  return next;
}
