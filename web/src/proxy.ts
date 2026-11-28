import { NextResponse, type NextRequest } from "next/server";
import { LANDING, landingOn } from "@/lib/landing";

const PUBLIC = new Set(["/privacy", "/support"]);

// Chưa có cookie phiên ⇒ về /login (chỉ kiểm có mặt; chữ ký + hạn kiểm ở route handler). API tự trả 401 nên không đi qua đây
// (cũng tránh proxy đệm thân yêu cầu lớn như file Excel).
export function proxy(request: NextRequest) {
  const has = request.cookies.has("barista_session");
  const { pathname, search } = request.nextUrl;
  if (!has && pathname === "/" && landingOn()) return NextResponse.rewrite(new URL(LANDING, request.url));
  // /c/<mã>: link gửi khách (core 0064) — khách không có tài khoản, quyền là chính mã link. /privacy, /support: trang công khai mà App Store
  // (và người chưa có tài khoản) phải mở được
  if (!has && pathname !== "/login" && pathname !== LANDING && !PUBLIC.has(pathname) && !pathname.startsWith("/c/")) {
    const url = new URL("/login", request.url);
    if (pathname !== "/") url.searchParams.set("next", pathname + search);
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico|.*\\..*).*)"],
};
