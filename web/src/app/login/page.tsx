import { Suspense } from "react";
import { LoginView } from "@/components/login/login-view";

export const metadata = { title: "Đăng nhập · Barista" };

export default function LoginPage() {
  return (
    <Suspense>
      <LoginView />
    </Suspense>
  );
}
