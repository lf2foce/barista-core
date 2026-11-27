import type { Metadata } from "next";

// Đường cũ "mở riêng ở tab mới" (trình soạn văn bản Word kiểu cũ — 0227). Từ 27/11 chỉ còn MỘT giao diện soạn: màn Soạn thảo trong bàn làm việc (mở văn
// bản từ "File Word đã mở" cũng vào màn đó, phóng to bằng nút Phóng to). Địa chỉ cũ còn lưu ở đâu đó ⇒ báo rõ và đưa về bàn làm việc.
export const metadata: Metadata = { title: "Văn bản", robots: { index: false, follow: false }, referrer: "no-referrer" };

export default function Page() {
  return (
    <main className="h-dvh bg-background p-6 text-foreground">
      <p className="t14">Văn bản giờ mở trong màn Soạn thảo của bàn làm việc: Văn bản &amp; tài liệu → File Word đã mở, rồi chọn văn bản.</p>
      {/* eslint-disable-next-line @next/next/no-html-link-for-pages -- về bàn làm việc bằng tải trang đầy đủ (nạp lại phiên) */}
      <a className="t14m mt-3 inline-block text-primary" href="/">Về bàn làm việc</a>
    </main>
  );
}
