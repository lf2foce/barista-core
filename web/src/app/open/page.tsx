import type { Metadata } from "next";
import { StandalonePage } from "@/components/pages/standalone";
import { parseStandalone } from "@/lib/standalone";

// Một trang của máy chủ mở riêng ở tab mới, toàn màn hình (vd trình soạn văn bản Word — 0227). Chưa đăng nhập ⇒ proxy đưa về /login rồi quay lại.
export const metadata: Metadata = { title: "Văn bản", robots: { index: false, follow: false }, referrer: "no-referrer" };

export default async function Page({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  return <StandalonePage target={parseStandalone(await searchParams)} />;
}
