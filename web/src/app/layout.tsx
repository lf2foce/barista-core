import { ViewportFix } from "@/components/viewport-fix";
import type { Metadata, Viewport } from "next";
import { Be_Vietnam_Pro } from "next/font/google";
import { CallProvider } from "@/components/call/call-provider";
import "./globals.css";

// Be Vietnam Pro có bộ chữ tiếng Việt đầy đủ; chỉ dùng ba độ đậm 400 / 500 / 600 (thang chữ của app Mac).
const font = Be_Vietnam_Pro({
  variable: "--font-be-vietnam",
  subsets: ["vietnamese", "latin"],
  weight: ["400", "500", "600"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Barista",
  description: "Vận hành sàn môi giới bất động sản — trợ lý và khung làm việc",
};

export const viewport: Viewport = {
  // bàn phím bật ⇒ Chrome Android co NỘI DUNG (không phủ lên ô nhập); iOS Safari không hỗ trợ ⇒ ViewportFix lo. Không đặt maximumScale: vẫn phóng to tay được
  interactiveWidget: "resizes-content",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f2f2f7" },
    { media: "(prefers-color-scheme: dark)", color: "#000000" },
  ],
};

// Đặt lớp .dark trước khi vẽ để không nháy sáng → tối. "barista-theme": light | dark | (trống = theo hệ thống).
const themeScript = `(function(){try{var t=localStorage.getItem('barista-theme');var d=t==='dark'||(t!=='light'&&window.matchMedia('(prefers-color-scheme: dark)').matches);document.documentElement.classList.toggle('dark',d);}catch(e){}})();`;

// Mở bằng địa chỉ có kèm mật khẩu cổng (https://user:pass@host — cách vào khi trình duyệt không hiện hộp thoại basic auth) ⇒ Chrome CẤM fetch
// đường dẫn tương đối ("Request cannot be constructed from a URL that includes credentials") ⇒ app báo "không kết nối được core". Bỏ phần user:pass
// khỏi địa chỉ NGAY trước khi vẽ: trình duyệt đã nhớ mật khẩu cổng cho trang này nên vào thẳng (11/11/2026). Trình duyệt nhúng (VS Code) có thể
// không cho đổi địa chỉ ⇒ thêm lớp đỡ: fetch tới đường dẫn tương đối được gắn vào location.origin (KHÔNG bao giờ chứa user:pass) — chỉ khi địa chỉ có mật khẩu.
const cleanUrlScript = `(function(){try{var l=window.location;if(l.username||l.password){var f=window.fetch;window.fetch=function(i,o){if(typeof i==='string'&&i.charAt(0)==='/'&&i.charAt(1)!=='/'){i=l.origin+i;}return f.call(this,i,o);};l.replace(l.protocol+'//'+l.host+l.pathname+l.search+l.hash);}}catch(e){}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="vi" className={`${font.variable} h-full`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: cleanUrlScript }} />
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="h-full">
        <ViewportFix />
        <CallProvider>{children}</CallProvider>
      </body>
    </html>
  );
}
