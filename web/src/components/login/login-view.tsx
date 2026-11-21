"use client";

import { ChevronRight, TriangleAlert } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import Script from "next/script";
import { useCallback, useEffect, useRef, useState } from "react";
import { auth } from "@/lib/api";
import { groupLogins, safeNext } from "@/lib/login";
import { cn } from "@/lib/utils";
import type { AuthConfig, DevLogin, SessionInfo, TenantChoice } from "@/lib/types";
import { Initials, Spinner } from "@/components/primitives";
import { EmailCode } from "@/components/login/email-code";
import { preloadCharts } from "@/components/blocks/lazy";

declare global {
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize: (o: { client_id: string; callback: (r: { credential: string }) => void; nonce?: string; ux_mode?: string; auto_select?: boolean }) => void;
          renderButton: (el: HTMLElement, o: Record<string, unknown>) => void;
        };
      };
    };
  }
}

/** Đăng nhập: Google (nút Google Identity Services, chỉ khi core công bố client) + chọn công ty; dev: chọn vai theo nhóm, kèm "Thử hỏi" (LoginView của app Mac). */
export function LoginView() {
  const router = useRouter();
  const next = safeNext(useSearchParams().get("next"));
  // đăng nhập ỨNG DỤNG first-party (0205: server gắn signin=1 + tên ứng dụng vào đường dẫn đồng ý) ⇒ chỉ Google, không màn chọn vai
  // (chọn vai không đồng ý được danh tính — server từ chối), tiêu đề theo tên ứng dụng
  const signinReq = (() => {
    const u = new URL(next, "http://x");
    return u.pathname === "/connect/ai" && u.searchParams.get("signin") === "1" ? u.searchParams.get("req") : null;
  })();
  // tên ứng dụng hỏi server theo mã yêu cầu (tên nhà vận hành khai); chưa có / không thấy ⇒ chữ chung
  const [appName, setAppName] = useState<string | null>(null);
  useEffect(() => {
    if (!signinReq) return;
    auth.signinApp(signinReq).then((r) => setAppName(r.app)).catch(() => setAppName(null));
  }, [signinReq]);
  const signinApp = signinReq ? appName ?? "ứng dụng" : null;
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [logins, setLogins] = useState<DevLogin[]>([]);
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [picked, setPicked] = useState<string | null>(null);   // dòng vai vừa chạm: hiện vòng quay ngay tại dòng đó tới khi trang chủ hiện
  const [tenants, setTenants] = useState<TenantChoice[] | null>(null);
  const [gsi, setGsi] = useState(false);
  const btn = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      const [cfg, sess] = await Promise.all([auth.config().catch((e: Error) => (setError(`Không kết nối được core: ${e.message}`), null)), auth.session().catch(() => null)]);
      if (!alive) return;
      setConfig(cfg);
      setSession(sess);
      if (sess && !sess.tenant_id && sess.tenants.length) setTenants(sess.tenants); // đã qua Google nhưng chưa chọn công ty
      if (cfg?.dev) setLogins(cfg.dev_logins ?? (await auth.devLogins().catch(() => [])));   // BFF thường kèm sẵn danh sách vai
    })();
    return () => {
      alive = false;
    };
  }, []);

  const go = useCallback(() => router.replace(next), [router, next]);

  // Trong lúc người đọc màn đăng nhập: tải sẵn mã của trang chủ (khung bàn làm việc + phần vẽ biểu đồ — màn chủ của nhiều vai là dashboard) ⇒ chạm chọn
  // xong chỉ còn chờ đăng nhập + dữ liệu, không chờ tải mã. KHÔNG dùng router.prefetch("/"): khi chưa có cookie, proxy.ts chuyển "/" về /login và
  // router NHỚ chuyển hướng đó ⇒ đăng nhập xong router.replace("/") vẫn đứng ở /login (Next không cho proxy phân biệt yêu cầu tải sẵn).
  // Bắt đầu SAU khi danh sách đã hiện (có cấu hình) ⇒ không giành đường truyền với lượt hỏi cấu hình.
  const ready = config !== null;
  useEffect(() => {
    if (signinReq || !ready) return;
    void import("@/components/desk/shell");
    preloadCharts();
  }, [signinReq, ready]);

  const onCredential = useCallback(
    async (credential: string, nonce: string) => {
      setBusy(true);
      setError(null);
      try {
        const r = await auth.google(credential, nonce);
        if (r.tenant_id) go();
        else setTenants(r.tenants);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Đăng nhập không thành công");
      } finally {
        setBusy(false);
      }
    },
    [go],
  );

  // nút Google: nonce ngẫu nhiên gắn vào id_token; core kiểm lại nonce
  const clientId = config?.google?.client_id;
  useEffect(() => {
    if (!gsi || !clientId || !btn.current || !window.google) return;
    const nonce = crypto.randomUUID().replace(/-/g, "");
    window.google.accounts.id.initialize({ client_id: clientId, nonce, ux_mode: "popup", callback: (r) => void onCredential(r.credential, nonce) });
    window.google.accounts.id.renderButton(btn.current, { type: "standard", theme: "outline", size: "large", text: "signin_with", locale: "vi", width: 280 });
  }, [gsi, clientId, onCredential]);

  async function pickDev(l: DevLogin) {
    setBusy(true);
    setPicked(l.tenant_id + l.email);
    setError(null);
    try {
      await auth.devLogin(l.email, l.tenant_id);
      go();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không đăng nhập được");
      setBusy(false);
      setPicked(null);
    }
  }

  async function pickTenant(t: TenantChoice) {
    setBusy(true);
    try {
      await auth.tenant(t.id);
      go();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không chọn được công ty");
      setBusy(false);
    }
  }

  const groups = groupLogins(logins);
  return (
    <main className="h-full overflow-y-auto bg-canvas">
      {clientId ? <Script src="https://accounts.google.com/gsi/client" strategy="afterInteractive" onLoad={() => setGsi(true)} onReady={() => setGsi(true)} /> : null}
      <div className="mx-auto flex max-w-[640px] flex-col gap-5 px-7 py-12">
        <header className="flex flex-col gap-1">
          <h1 className="t20">{signinApp ? `Đăng nhập ${signinApp}` : "Barista"}</h1>
          <p className="t14 text-muted-foreground">
            {signinApp ? "Bằng tài khoản Google hoặc mã gửi tới email đã được thêm vào hệ thống." : "Mỗi người một trợ lý — thấy đúng phần dữ liệu của mình."}
          </p>
        </header>
        {error ? <p className="t14 flex items-start gap-2 text-bad" role="alert"><TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />{error}</p> : null}
        {!config && !error ? <Spinner /> : null}

        {session?.tenant_id ? (
          <div className="flex items-center justify-between gap-3 rounded-xl border border-border bg-surface px-4 py-3">
            <span className="t14">Đang đăng nhập là <b className="font-medium">{session.email}</b></span>
            <button type="button" className="t14m h-8 rounded-lg bg-primary px-3 text-primary-foreground" onClick={go}>Tiếp tục</button>
          </div>
        ) : null}

        {tenants ? (
          <section className="flex flex-col gap-2" aria-label="Chọn công ty">
            <h2 className="t12m text-muted-foreground">Chọn công ty</h2>
            <ul className="divide-y divide-border overflow-hidden rounded-xl bg-surface">
              {tenants.map((t) => (
                <li key={t.id}>
                  <button type="button" disabled={busy} onClick={() => void pickTenant(t)} className="flex w-full items-center justify-between px-4 py-2.5 text-left outline-none hover:bg-accent focus-visible:bg-accent">
                    <span className="flex flex-col"><span className="t14m">{t.name}</span><span className="t12 text-muted-foreground">{t.role_label}</span></span>
                    <ChevronRight className="size-3.5 text-muted-foreground" aria-hidden />
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ) : (
          <>
            {clientId ? (
              <section className="flex flex-col gap-2">
                <div ref={btn} aria-label="Đăng nhập bằng Google" className="min-h-10" />
                {signinApp ? null : <p className="t12 text-muted-foreground">Dùng tài khoản Google mà quản trị đã mời. Chưa được mời thì nhờ quản trị thêm email của bạn.</p>}
              </section>
            ) : null}
            {config?.email ? (
              <EmailCode onError={setError} onDone={(r) => (r.tenant_id ? go() : setTenants(r.tenants))} />
            ) : null}
            {config?.dev && groups.length && !signinApp ? (
              <>
                {clientId ? <p className="t12m pt-2 text-muted-foreground">Đăng nhập dev — chỉ trên máy chạy core</p> : null}
                {groups.map(([group, rows]) => (
                  <section key={group} className="flex flex-col gap-2" aria-label={group}>
                    <h2 className="t12m text-muted-foreground">{group}</h2>
                    <ul className="divide-y divide-border overflow-hidden rounded-xl bg-surface">
                      {rows.map((l) => (
                        <li key={l.tenant_id + l.email}>
                          <button type="button" disabled={busy} aria-busy={picked === l.tenant_id + l.email} onClick={() => void pickDev(l)} className={cn("flex w-full items-start gap-3 px-4 py-2.5 text-left outline-none hover:bg-accent focus-visible:bg-accent disabled:opacity-60", picked === l.tenant_id + l.email && "bg-accent disabled:opacity-100")} data-login={l.email}>
                            <Initials name={l.name} size={36} />
                            <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                              <span className="flex items-baseline gap-1.5"><span className="t14m">{l.name}</span><span className="t12 text-muted-foreground">{l.role}</span></span>
                              {l.hint ? <span className="t12 text-muted-foreground">{l.hint}</span> : null}
                              {l.ask ? <span className="t12 text-bad">Thử hỏi: “{l.ask}”</span> : null}
                            </span>
                            {picked === l.tenant_id + l.email ? <Spinner className="mt-2.5 size-3.5 shrink-0" /> : <ChevronRight className="mt-2.5 size-3.5 shrink-0 text-muted-foreground" aria-hidden />}
                          </button>
                        </li>
                      ))}
                    </ul>
                  </section>
                ))}
              </>
            ) : null}
            {config && !config.google && !config.dev ? (
              <p className="t12 select-text text-muted-foreground">Core chưa cấu hình đăng nhập Google (BARISTA_GOOGLE_CLIENT_IDS, BARISTA_GOOGLE_APP_CLIENT_ID).</p>
            ) : null}
          </>
        )}
      </div>
    </main>
  );
}
