import Link from "next/link";
import type { ReactNode } from "react";

/** Email liên hệ của BẢN CÀI (barista-ops: ``"support_email"`` ⇒ BARISTA_SUPPORT_EMAIL, đọc lúc chạy) — lõi không ghi email nào. Vắng ⇒ trang
 *  hướng người dùng tới quản trị công ty của họ. */
export const supportEmail = () => (process.env.BARISTA_SUPPORT_EMAIL ?? "").trim();

/** Khung trang công khai (chính sách quyền riêng tư, hỗ trợ): không cần đăng nhập (web/src/proxy.ts), chữ theo thang của app. */
export function DocPage({ title, updated, children }: { title: string; updated: string; children: ReactNode }) {
  return (
    <main className="h-full overflow-y-auto bg-canvas">
      <article className="mx-auto flex max-w-[720px] flex-col gap-6 px-7 py-12">
        <header className="flex flex-col gap-1">
          <Link href="/" className="t12m text-muted-foreground hover:underline">Barista Work</Link>
          <h1 className="t20">{title}</h1>
          <p className="t12 text-muted-foreground">Cập nhật {updated}</p>
        </header>
        {children}
      </article>
    </main>
  );
}

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h2 className="t16">{title}</h2>
      <div className="t14 flex flex-col gap-2 leading-relaxed text-foreground/90">{children}</div>
    </section>
  );
}

export function Contact() {
  const email = supportEmail();
  return email ? (
    <a className="text-primary hover:underline" href={`mailto:${email}`}>{email}</a>
  ) : (
    <span>quản trị viên Barista của công ty bạn</span>
  );
}
