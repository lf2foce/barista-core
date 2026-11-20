import type { NextRequest } from "next/server";
import { coreBase, downstreamHeaders, targetPath, upstreamHeaders } from "@/lib/server/bff";
import { detail, originOk, readSession } from "@/lib/server/handler";

// BFF: chuyển yêu cầu của trình duyệt tới core kèm danh tính từ cookie httpOnly. Thân phản hồi chạy thẳng (không đệm) để NDJSON / SSE
// tới đúng lúc. Trình duyệt không bao giờ thấy mã phiên của core.

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

async function forward(request: NextRequest, ctx: { params: Promise<{ path: string[] }> }): Promise<Response> {
  const t0 = performance.now();
  if (!(await originOk(request))) return detail("Yêu cầu từ trang khác bị từ chối", 403);
  const session = await readSession();
  const t1 = performance.now();
  if (!session) return detail("Chưa đăng nhập", 401);
  if (!session.tenant) return detail("Chưa chọn công ty", 401);
  const { path } = await ctx.params;
  const target = targetPath(path);
  if (!target) return detail("Đường dẫn không được phép", 404);

  const url = coreBase() + target + request.nextUrl.search;
  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  let upstream: Response;
  try {
    upstream = await fetch(url, {
      method: request.method,
      headers: upstreamHeaders(session, request.headers, target),
      body: hasBody ? request.body : undefined,
      // @ts-expect-error — Node fetch cần duplex khi gửi thân dạng stream
      duplex: hasBody ? "half" : undefined,
      signal: request.signal,
      cache: "no-store",
      redirect: "manual",
    });
  } catch (e) {
    if (request.signal.aborted) return new Response(null, { status: 499 });
    return detail(`Không nối được máy chủ lõi (${e instanceof Error ? e.message : "lỗi mạng"})`, 502);
  }
  // Server-Timing: chặng của core + giải mã cookie / chờ core trả tiêu đề / tổng ở BFF (luồng NDJSON / SSE: tới lúc mở luồng)
  const t2 = performance.now();
  const timing = { "bff-session": t1 - t0, "bff-upstream": t2 - t1, bff: t2 - t0 };
  return new Response(upstream.body, { status: upstream.status, headers: downstreamHeaders(upstream.headers, timing) });
}

export { forward as GET, forward as POST, forward as PUT, forward as PATCH, forward as DELETE };
