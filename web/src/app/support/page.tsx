import Link from "next/link";
import { Contact, DocPage, Section } from "@/components/legal/doc-page";

// email liên hệ là cấu hình của bản cài, đọc lúc chạy (một ảnh web cho mọi bản cài)
export const dynamic = "force-dynamic";

export const metadata = {
  title: "Hỗ trợ · Barista Work",
  description: "Cách đăng nhập và liên hệ hỗ trợ Barista Work.",
};

export default function SupportPage() {
  return (
    <DocPage title="Hỗ trợ" updated="28 tháng 11, 2026">
      <Section title="Barista Work là gì">
        <p>
          Ứng dụng vận hành doanh nghiệp, mỗi người một trợ lý AI riêng: việc cần làm, phiếu duyệt, khách hàng, tiền về và đối soát công nợ, dòng tiền,
          trao đổi trong công ty. Dùng chung dữ liệu trên iPhone, iPad, Mac và web.
        </p>
      </Section>

      <Section title="Đăng nhập">
        <ul className="list-disc space-y-1 pl-5">
          <li>Tài khoản do quản trị viên công ty bạn cấp: nhập email đã được mời, nhận mã đăng nhập qua email.</li>
          <li>Công ty có máy chủ Barista riêng: ở màn đăng nhập, lắc điện thoại để mở Cài đặt máy chủ và nhập địa chỉ quản trị gửi.</li>
          <li>Muốn xem thử: chọn một vai trong công ty mẫu ở màn đăng nhập (dữ liệu mẫu, không phải dữ liệu thật).</li>
        </ul>
      </Section>

      <Section title="Câu hỏi thường gặp">
        <p><b className="font-medium">Không nhận được mã đăng nhập?</b> Kiểm tra thư rác, hoặc nhờ quản trị viên kiểm tra email của bạn đã được mời.</p>
        <p><b className="font-medium">Không thấy dữ liệu của đội mình?</b> Quyền xem do công ty đặt theo vai; hãy hỏi quản trị viên.</p>
        <p><b className="font-medium">Xoá tài khoản?</b> Gửi yêu cầu tới quản trị viên công ty hoặc liên hệ bên dưới.</p>
      </Section>

      <Section title="Liên hệ">
        <p>Email: <Contact /></p>
        <p>Chính sách quyền riêng tư: <Link className="text-primary hover:underline" href="/privacy">/privacy</Link></p>
      </Section>

      <hr className="border-border" />

      <Section title="Support (English)">
        <p>
          Barista Work is business software with a personal AI assistant for each employee. Sign in with the email your company administrator invited;
          a sign-in code is sent by email. To try the app, pick a role in the sample company on the sign-in screen. Contact: <Contact />.
        </p>
      </Section>
    </DocPage>
  );
}
