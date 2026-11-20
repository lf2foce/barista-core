"use client";

import { CheckCircle2, Link2 } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { actionClass, ErrorLine, Spinner } from "@/components/primitives";

/** Khoá module hợp lệ (chữ thường, số, gạch dưới) — trang chỉ gọi lệnh ``<module>.finish_oauth`` của đúng gói đó, không lệnh tuỳ ý. */
const MODULE = /^[a-z][a-z0-9_]{1,39}$/;

/**
 * Trang nhà cung cấp OAuth trả người dùng về (api 1.67). Không tự xử lý gì: gửi ``code`` + ``state`` cho lệnh ``<module>.finish_oauth`` của gói, CHẠY
 * BẰNG PHIÊN của người đang đăng nhập — gói kiểm ``state`` (đúng công ty + người bấm, còn hạn), đổi mã lấy token và lưu ở khung kết nối. Người dùng từ
 * chối ở nhà cung cấp ⇒ báo lại, không gọi gì.
 */
export function OauthReturn({ module }: { module: string }) {
  const q = useSearchParams();
  const code = q.get("code") ?? "";
  const state = q.get("state") ?? "";
  const denied = q.get("error") ?? "";
  // lỗi biết ngay từ đường dẫn (không gọi máy chủ)
  const early = !MODULE.test(module)
    ? "Đường dẫn kết nối không hợp lệ"
    : denied
      ? "Bạn đã không cấp quyền — đóng tab này và bấm kết nối lại khi sẵn sàng"
      : !code || !state
        ? "Thiếu mã từ nhà cung cấp — đóng tab này và bấm kết nối lại"
        : null;
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(early);
  const sent = useRef(false);

  useEffect(() => {
    if (early || sent.current) return; // mã chỉ dùng được một lần — không gửi lại khi vẽ lại
    sent.current = true;
    api
      .command(`${module}.finish_oauth`, { code, state }, `oauth:${state.slice(-24)}`)
      .then((r) => {
        if (r.status === "ok") setDone(String((r.output as Record<string, unknown> | null)?.summary ?? "Đã kết nối"));
        else setError(r.error || "Không hoàn tất được kết nối");
      })
      .catch((e: unknown) => {
        // 401 ⇒ thư viện API tự sang trang đăng nhập kèm next (quay lại đây — mã còn hạn vài phút)
        if (!(e instanceof ApiError && e.status === 401)) setError(e instanceof Error ? e.message : "Không hoàn tất được kết nối");
      });
  }, [module, code, state, early]);

  return (
    <main className="grid min-h-dvh place-items-center bg-background p-6">
      <section className="flex w-full max-w-md flex-col gap-4 rounded-2xl border border-hairline bg-surface p-6" aria-label="Hoàn tất kết nối">
        <div className="flex items-center gap-2.5">
          {done ? <CheckCircle2 className="size-6 text-ok" aria-hidden /> : <Link2 className="size-6 text-primary" aria-hidden />}
          <h1 className="t20">{done ? "Đã kết nối" : error ? "Chưa kết nối được" : "Đang hoàn tất kết nối…"}</h1>
        </div>
        {!done && !error ? <div className="grid place-items-center py-8"><Spinner className="size-6" /></div> : null}
        {error ? <ErrorLine>{error}</ErrorLine> : null}
        {done ? <p className="t14">{done}</p> : null}
        {done || error ? <p className="t12 text-muted-foreground">Bạn có thể đóng tab này và quay lại Barista.</p> : null}
        {done || error ? (
          <div className="flex justify-end">
            <button type="button" className={actionClass("primary")} onClick={() => window.close()}>Đóng tab</button>
          </div>
        ) : null}
      </section>
    </main>
  );
}
