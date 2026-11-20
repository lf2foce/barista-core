"use client";

import { useState } from "react";
import { auth } from "@/lib/api";
import { actionClass, Spinner } from "@/components/primitives";
import { Input } from "@/components/ui/input";

type Done = { email: string; tenants: { id: string; name: string; role_label: string }[]; tenant_id: string | null };

/**
 * Đăng nhập bằng MÃ GỬI QUA EMAIL (0205 §5c) — cho người không dùng Google. Hai bước: nhập email ⇒ server gửi mã 6 số (email chưa được mời: server
 * trả lời y hệt nhưng không gửi — trang không biết, không nói) ⇒ nhập mã ⇒ phiên như đăng nhập Google.
 */
export function EmailCode({ onDone, onError }: { onDone: (r: Done) => void; onError: (message: string | null) => void }) {
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function send() {
    setBusy(true);
    onError(null);
    try {
      await auth.emailStart(email.trim());
      setSentTo(email.trim());
      setCode("");
    } catch (e) {
      onError(e instanceof Error ? e.message : "Chưa gửi được mã");
    } finally {
      setBusy(false);
    }
  }

  async function verify() {
    if (!sentTo) return;
    setBusy(true);
    onError(null);
    try {
      onDone(await auth.emailVerify(sentTo, code.trim()));
    } catch (e) {
      onError(e instanceof Error ? e.message : "Mã không đúng");
      setBusy(false);
    }
  }

  if (!sentTo) {
    return (
      <form className="flex flex-col gap-2" onSubmit={(e) => (e.preventDefault(), void send())} aria-label="Đăng nhập bằng mã qua email">
        <label className="t12m text-muted-foreground" htmlFor="login-email">Hoặc nhận mã đăng nhập qua email</label>
        <div className="flex gap-2">
          <Input id="login-email" type="email" autoComplete="email" inputMode="email" required placeholder="ban@congty.vn" value={email}
                 onChange={(e) => setEmail(e.target.value)} className="h-9 max-w-[280px]" />
          <button type="submit" className={actionClass("default")} disabled={busy || !email.includes("@")}>
            {busy ? <Spinner className="size-3.5" /> : null}
            Gửi mã
          </button>
        </div>
      </form>
    );
  }
  return (
    <form className="flex flex-col gap-2" onSubmit={(e) => (e.preventDefault(), void verify())} aria-label="Nhập mã đăng nhập">
      <label className="t14" htmlFor="login-code">
        Nếu <b className="font-medium">{sentTo}</b> đã được thêm vào hệ thống, mã 6 số vừa được gửi tới đó (hết hạn sau 10 phút).
      </label>
      <div className="flex gap-2">
        <Input id="login-code" autoFocus inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} placeholder="123456" value={code}
               onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} className="t16 h-9 w-32 tracking-[0.3em]" />
        <button type="submit" className={actionClass("primary")} disabled={busy || code.length !== 6}>
          {busy ? <Spinner className="size-3.5" /> : null}
          Đăng nhập
        </button>
      </div>
      <p className="t12 text-muted-foreground">
        Không thấy thư? Xem mục Spam, hoặc{" "}
        <button type="button" className="t12m text-primary hover:underline" disabled={busy} onClick={() => void send()}>gửi lại mã</button>
        {" · "}
        <button type="button" className="t12m text-primary hover:underline" disabled={busy} onClick={() => (setSentTo(null), onError(null))}>đổi email</button>
      </p>
    </form>
  );
}
