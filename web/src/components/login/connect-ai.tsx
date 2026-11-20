"use client";

import { Bot, ShieldCheck } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { McpRequest } from "@/lib/types";
import { actionClass, ErrorLine, Spinner } from "@/components/primitives";

/**
 * Đồng ý cấp quyền cho ứng dụng AI (MCP chỉ đọc — 0093). Ứng dụng (Claude, ChatGPT…) chuyển người dùng tới đây kèm mã yêu cầu; quyền cấp cho
 * NGƯỜI đang đăng nhập + CÔNG TY đang chọn, chỉ đọc. Chưa đăng nhập ⇒ sang trang đăng nhập rồi quay lại đúng chỗ.
 */
export function ConnectAi() {
  const req = useSearchParams().get("req") ?? "";
  const [info, setInfo] = useState<McpRequest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    api
      .mcpRequest(req)
      .then((r) => {
        if (!alive) return;
        setInfo(r);
        if (r.auto) void decide(true); // ứng dụng first-party chỉ xin danh tính: tự đồng ý (server vẫn kiểm đăng nhập Google thật)
      })
      .catch((e: unknown) => {
        // 401 ⇒ thư viện API tự sang trang đăng nhập kèm next (quay lại đây sau khi đăng nhập)
        if (alive && !(e instanceof ApiError && e.status === 401)) setError(e instanceof Error ? e.message : "Không đọc được yêu cầu kết nối");
      });
    return () => {
      alive = false;
    };
    // chạy một lần theo mã yêu cầu (tự đồng ý gọi decide — hàm khai bên dưới, không đổi theo lượt vẽ)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [req]);

  async function decide(approve: boolean) {
    setBusy(true);
    setError(null);
    try {
      const { redirect } = await api.mcpApprove(req, approve);
      window.location.assign(redirect); // quay về ứng dụng AI (mã cấp quyền / lỗi access_denied)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không gửi được");
      setBusy(false);
    }
  }

  return (
    <main className="grid min-h-dvh place-items-center bg-background p-6">
      <section className="flex w-full max-w-md flex-col gap-4 rounded-2xl border border-hairline bg-surface p-6" aria-label="Cho phép ứng dụng AI kết nối">
        <div className="flex items-center gap-2.5">
          <Bot className="size-6 text-primary" aria-hidden />
          <h1 className="t20">{info?.auto ? (error ? `Chưa đăng nhập được ${info.client_name}` : `Đang đăng nhập ${info.client_name}…`) : info?.title ?? "Cho phép ứng dụng AI kết nối"}</h1>
        </div>
        {error ? <ErrorLine>{error}</ErrorLine> : null}
        {!info && !error ? <div className="grid place-items-center py-8"><Spinner className="size-6" /></div> : null}
        {info?.auto && !error ? <div className="grid place-items-center py-6"><Spinner className="size-6" /></div> : null}
        {info && !info.auto ? (
          <>
            <p className="t14">
              <span className="t14m">{info.client_name}</span>{" "}
              {info.action ? <>{info.action} — tài khoản</> : <>xin <span className="t14m">đọc</span> dữ liệu của</>}{" "}
              <span className="t14m">{info.user}</span> tại <span className="t14m">{info.company}</span>.
            </p>
            {/* Tên ứng dụng do ứng dụng tự khai — tên miền quay về mới là thứ không giả được */}
            <p className="t12 text-muted-foreground">
              Sau khi cho phép, quyền được gửi về <span className="t12m text-foreground">{info.redirect_host}</span>. Không nhận ra tên miền này thì
              bấm Từ chối.
            </p>
            <ul className="t14 flex flex-col gap-1.5 rounded-xl bg-surface2 p-3 text-muted-foreground">
              {(info.points ?? [
                "Chỉ đọc — không tạo, sửa, duyệt hay xoá được gì.",
                "Chỉ thấy đúng phần bạn được xem trong Barista.",
                "Chỉ công ty đang chọn — muốn công ty khác thì đổi công ty rồi kết nối lại.",
              ]).map((t) => (
                <li key={t} className="flex gap-2"><ShieldCheck className="mt-0.5 size-4 shrink-0 text-ok" aria-hidden />{t}</li>
              ))}
            </ul>
            <div className="flex justify-end gap-2">
              <button type="button" className={actionClass("default")} disabled={busy} onClick={() => void decide(false)}>Từ chối</button>
              <button type="button" className={actionClass("primary")} disabled={busy} onClick={() => void decide(true)}>
                {busy ? <Spinner className="size-3.5" /> : null}
                Cho phép
              </button>
            </div>
          </>
        ) : null}
      </section>
    </main>
  );
}
