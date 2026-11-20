import type { NextRequest } from "next/server";
import { coreBase, downstreamHeaders, publicHeaders, publicLinkPath } from "@/lib/server/bff";
import { detail, originOk } from "@/lib/server/handler";

// Link gửi khách (core 0064): khách mở từ Zalo, KHÔNG đăng nhập. Không đọc cookie phiên, không gửi danh tính — quyền là chính mã link, core kiểm.
// Chỉ các đường publicLinkPath cho phép: xem (GET), trả lời / nộp file (POST); cuộc gọi qua link mời: tín hiệu / có mặt (POST), nghe tín hiệu
// (SSE, chạy thẳng). Thân nộp file chạy thẳng (stream), core giới hạn 20 MB.

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

async function forward(request: NextRequest, ctx: { params: Promise<{ path: string[] }> }): Promise<Response> {
  if (!(await originOk(request))) return detail("Yêu cầu từ trang khác bị từ chối", 403);
  const { path } = await ctx.params;
  const target = publicLinkPath(path, request.method);
  if (!target) return detail("Link không hợp lệ", 404);
  const hasBody = request.method === "POST";
  let upstream: Response;
  try {
    upstream = await fetch(coreBase() + target + request.nextUrl.search, {
      method: request.method,
      headers: publicHeaders(request.headers),
      body: hasBody ? request.body : undefined,
      // @ts-expect-error — Node fetch cần duplex khi gửi thân dạng stream
      duplex: hasBody ? "half" : undefined,
      signal: request.signal,
      cache: "no-store",
      redirect: "manual",
    });
  } catch (e) {
    if (request.signal.aborted) return new Response(null, { status: 499 });
    return detail(`Không nối được máy chủ (${e instanceof Error ? e.message : "lỗi mạng"})`, 502);
  }
  return new Response(upstream.body, { status: upstream.status, headers: downstreamHeaders(upstream.headers) });
}

export { forward as GET, forward as POST };
