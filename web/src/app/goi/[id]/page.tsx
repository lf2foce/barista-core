import type { Metadata } from "next";
import { MemberCall } from "@/components/call/direct-call";
import { callEntry } from "@/lib/form";

// Gọi trực tiếp 1–1 của thành viên (core 0114 bổ sung 07/11). App Mac mở trang này bằng trình duyệt mặc định (không chạy WebRTC trong app).
export const metadata: Metadata = { title: "Cuộc gọi", robots: { index: false, follow: false }, referrer: "no-referrer" };

export default async function Page({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const { id } = await params;
  return <MemberCall callId={id} entry={callEntry(await searchParams)} />;
}
