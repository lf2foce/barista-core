import type { Metadata } from "next";
import { ClientRequest } from "@/components/public/client-request";

// Link gửi khách (core 0064). Không đăng nhập; không đưa mã link ra ngoài (không referrer), không cho máy tìm kiếm lập chỉ mục.
export const metadata: Metadata = { title: "Gửi chứng từ", robots: { index: false, follow: false }, referrer: "no-referrer" };

export default async function Page({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  return <ClientRequest token={token} />;
}
