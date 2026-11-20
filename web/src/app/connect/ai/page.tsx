import { Suspense } from "react";
import { ConnectAi } from "@/components/login/connect-ai";

export const metadata = { title: "Cho phép ứng dụng AI kết nối · Barista" };

export default function ConnectAiPage() {
  return (
    <Suspense>
      <ConnectAi />
    </Suspense>
  );
}
