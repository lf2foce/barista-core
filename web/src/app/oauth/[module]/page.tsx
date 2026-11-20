import { Suspense } from "react";
import { OauthReturn } from "@/components/login/oauth-return";

// Nhà cung cấp OAuth (Facebook, Google…) trả người dùng về đây sau khi cấp quyền (api 1.67). Chưa đăng nhập ⇒ proxy đưa về /login rồi quay lại đúng chỗ.
export const metadata = { title: "Hoàn tất kết nối · Barista", robots: { index: false, follow: false }, referrer: "no-referrer" };

export default async function OauthPage({ params }: { params: Promise<{ module: string }> }) {
  const { module } = await params;
  return (
    <Suspense>
      <OauthReturn module={module} />
    </Suspense>
  );
}
