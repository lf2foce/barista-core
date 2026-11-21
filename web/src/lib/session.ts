import type { Manifest, SessionInfo } from "./types";

/**
 * Rời phiên hỏng (hết hạn / không còn là thành viên): XOÁ phiên ở BFF trước, CHỈ khi xoá được mới về trang đăng nhập. Xoá không được (cookie còn đó)
 * mà vẫn điều hướng ⇒ trang đăng nhập thấy còn phiên, đẩy lại vào app, app lại lỗi — vòng lặp. Khi đó trả false để màn hiện "Đăng nhập lại".
 */
export async function leaveSession(logout: () => Promise<unknown>, go: (path: string) => void): Promise<boolean> {
  try {
    await logout();
  } catch {
    return false;
  }
  go("/login");
  return true;
}


/**
 * Manifest + phiên mà đăng nhập dev vừa đọc (BFF kiểm vai bằng chính /v1/manifest): trang chủ lấy ngay MỘT lần thay vì hỏi lại core. Chỉ sống trong bộ
 * nhớ của lần tải trang này (đăng nhập → trang chủ là điều hướng phía client; đăng xuất / đổi công ty tải lại cả trang ⇒ mất), và chỉ ~30 giây —
 * quá hạn / đã lấy ⇒ trang chủ hỏi core như thường.
 */
export const PRIMED_TTL_MS = 30_000;
let primed: { manifest: Manifest; session: SessionInfo; at: number } | null = null;

export function primeDesk(manifest: unknown, session: unknown, now = Date.now()): void {
  const ok = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
  primed = ok(manifest) && Array.isArray(manifest.workspaces) && ok(session) && typeof session.email === "string"
    ? { manifest: manifest as Manifest, session: session as SessionInfo, at: now }
    : null;
}

export function takePrimedDesk(now = Date.now()): { manifest: Manifest; session: SessionInfo } | null {
  const p = primed;
  primed = null;
  return p && now - p.at <= PRIMED_TTL_MS ? { manifest: p.manifest, session: p.session } : null;
}
