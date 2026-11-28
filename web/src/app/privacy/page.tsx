import { Contact, DocPage, Section } from "@/components/legal/doc-page";

// email liên hệ là cấu hình của bản cài, đọc lúc chạy (một ảnh web cho mọi bản cài)
export const dynamic = "force-dynamic";

export const metadata = {
  title: "Chính sách quyền riêng tư · Barista Work",
  description: "Barista Work thu thập dữ liệu gì, dùng vào việc gì và quyền của bạn với dữ liệu của mình.",
};

export default function PrivacyPage() {
  return (
    <DocPage title="Chính sách quyền riêng tư" updated="28 tháng 11, 2026">
      <Section title="Phạm vi">
        <p>
          Chính sách này áp dụng cho ứng dụng Barista Work (iPhone, iPad, Mac) và bản web của máy chủ này. Barista Work là phần mềm vận hành doanh
          nghiệp: tài khoản do quản trị viên của công ty cấp (mời bằng email), không có đăng ký đại chúng.
        </p>
        <p>
          Công ty sử dụng Barista là bên quyết định mục đích xử lý dữ liệu của công ty mình; chúng tôi vận hành phần mềm và xử lý dữ liệu thay mặt công
          ty theo cấu hình công ty đặt ra.
        </p>
      </Section>

      <Section title="Dữ liệu chúng tôi thu thập">
        <ul className="list-disc space-y-1 pl-5">
          <li>Thông tin tài khoản: họ tên, email, số điện thoại (nếu công ty nhập), vai trò và đội trong công ty.</li>
          <li>Dữ liệu nghiệp vụ do công ty và người dùng nhập: khách hàng, đơn hàng, phiếu, chứng từ, sao kê, lương và các khoản tài chính khác.</li>
          <li>Tin nhắn, tệp và ảnh người dùng gửi trong ứng dụng (ảnh chỉ được chụp khi người dùng chủ động dùng máy ảnh).</li>
          <li>Giọng nói khi người dùng chủ động dùng trợ lý giọng nói, để chuyển thành câu hỏi cho trợ lý.</li>
          <li>Mã thiết bị để gửi thông báo đẩy, nếu người dùng cho phép thông báo.</li>
          <li>Địa chỉ mạng (IP) tại thời điểm chấm công để đối chiếu với mạng văn phòng. Ứng dụng không lấy vị trí GPS và không theo dõi vị trí liên tục.</li>
          <li>Nhật ký thao tác trong hệ thống (ai làm gì, lúc nào) để kiểm soát và truy vết.</li>
        </ul>
      </Section>

      <Section title="Chúng tôi dùng dữ liệu để làm gì">
        <p>
          Chỉ để cung cấp các chức năng của ứng dụng: đăng nhập, phân quyền, hiển thị và xử lý công việc, trợ lý AI trả lời và soạn sẵn việc theo đúng
          quyền của người hỏi, gửi thông báo, chấm công và đối soát tài chính.
        </p>
        <p>
          Chúng tôi không bán dữ liệu, không dùng dữ liệu để quảng cáo và không theo dõi người dùng qua ứng dụng hay trang web của bên khác.
        </p>
      </Section>

      <Section title="Bên xử lý dữ liệu thay chúng tôi">
        <ul className="list-disc space-y-1 pl-5">
          <li>Nhà cung cấp mô hình AI: nội dung câu hỏi và dữ liệu cần để trả lời được gửi tới mô hình để xử lý trong lượt hỏi đó.</li>
          <li>Hạ tầng máy chủ và lưu trữ tệp nơi bản cài của công ty chạy.</li>
          <li>Dịch vụ gửi email (mã đăng nhập, thông báo).</li>
          <li>Apple, để chuyển thông báo đẩy tới thiết bị.</li>
        </ul>
      </Section>

      <Section title="Lưu giữ và xoá">
        <p>
          Dữ liệu được lưu trong thời gian công ty sử dụng Barista. Công ty có thể khoá tài khoản người dùng, xuất hoặc yêu cầu xoá dữ liệu. Số liệu tài
          chính đã ghi sổ được giữ theo quy định kế toán; bút toán sai được sửa bằng bút toán đảo thay vì xoá.
        </p>
      </Section>

      <Section title="Quyền của bạn">
        <p>
          Bạn có quyền được biết, truy cập, chỉnh sửa, yêu cầu xoá và hạn chế xử lý dữ liệu cá nhân của mình theo Nghị định 13/2023/NĐ-CP về bảo vệ
          dữ liệu cá nhân. Hãy gửi yêu cầu tới quản trị viên công ty bạn hoặc liên hệ <Contact />.
        </p>
      </Section>

      <Section title="Trẻ em">
        <p>Barista Work là ứng dụng dành cho doanh nghiệp, không hướng tới trẻ em và không cố ý thu thập dữ liệu của trẻ em.</p>
      </Section>

      <Section title="Liên hệ">
        <p>Câu hỏi về quyền riêng tư: <Contact />.</p>
      </Section>

      <hr className="border-border" />

      <Section title="Privacy Policy (English summary)">
        <p>
          Barista Work is business software. Accounts are created by each company&apos;s administrator; the company decides how its data is used and we
          process it on the company&apos;s behalf.
        </p>
        <p>
          We collect account details (name, email, phone if entered by the company), business data entered by the company, messages, files and photos
          users send, voice input when the voice assistant is used, a push notification token, the IP address at time clock punches (no GPS location,
          no continuous tracking), and an audit log of actions. Data is used only to provide the app&apos;s features. We do not sell data, use it for
          advertising, or track users across other companies&apos; apps or websites. Processors: AI model provider, server and file hosting, email
          delivery, Apple Push Notification service.
        </p>
        <p>To access, correct or delete your data, contact your company administrator or <Contact />.</p>
      </Section>
    </DocPage>
  );
}
