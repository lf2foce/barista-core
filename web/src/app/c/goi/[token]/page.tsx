import type { Metadata } from "next";
import { GuestCall } from "@/components/call/direct-call";

// Người ngoài vào cuộc gọi trực tiếp bằng link mời (core 0114 bổ sung 07/11, kiểu link 0064): không đăng nhập, chỉ đúng một cuộc gọi, có hạn.
// Không đưa mã link ra ngoài (không referrer), không cho máy tìm kiếm lập chỉ mục.
export const metadata: Metadata = { title: "Cuộc gọi", robots: { index: false, follow: false }, referrer: "no-referrer" };

export default async function Page({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  return <GuestCall token={token} />;
}
