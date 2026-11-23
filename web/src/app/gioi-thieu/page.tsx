import { notFound } from "next/navigation";
import { Landing } from "@/components/home/landing";
import { landingOn } from "@/lib/landing";

// cờ của bản cài đọc lúc chạy (một ảnh web cho mọi bản cài) ⇒ không dựng tĩnh lúc build
export const dynamic = "force-dynamic";

export const metadata = {
  title: "Barista — vận hành doanh nghiệp, mỗi người một trợ lý",
  description: "Khách hàng, việc, lịch, kế toán, nhân sự trong một bàn làm việc theo vai; trợ lý AI soạn sẵn, người duyệt.",
};

export default function LandingPage() {
  if (!landingOn()) notFound();   // bản cài không bật trang giới thiệu ⇒ như không có trang này
  return <Landing />;
}
