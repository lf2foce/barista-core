import Link from "next/link";
import { ArrowRight, Camera, Landmark, MonitorSmartphone, ShieldCheck, Sparkles, Wallet } from "lucide-react";
import { AgentStage } from "@/components/home/agent-stage";

/** Trang giới thiệu sản phẩm ở "/" cho người chưa đăng nhập (bản cài khai ``"landing": true`` — lib/landing.ts). Hai quy trình chính: bán hàng → thu tiền
 *  và sổ sách cho hộ kinh doanh một người; sân khấu "trợ lý phán đoán" (agent-stage.tsx) là phần trung tâm. Không gọi core; dữ liệu minh hoạ là GIẢ.
 *  Thân trang của app không cuộn (globals.css) ⇒ trang tự cuộn trong khung của nó. */

const FEATURES = [
  { icon: Wallet, title: "Tiền về là khớp đơn", text: "Đọc sao kê, khớp từng khoản vào đúng đơn dù nội dung chuyển khoản lộn xộn; khoản nào chưa chắc thì hỏi lại." },
  { icon: Sparkles, title: "Nhắc nợ như người thật", text: "Biết khách quên hay hoá đơn sai trước khi nhắc. Lời nhắc soạn riêng từng khách, bạn chỉ việc xem." },
  { icon: Camera, title: "Chụp phiếu là xong", text: "Phiếu viết tay, hoá đơn, sao kê — máy đọc và điền sẵn, người chỉ xác nhận. Ảnh gốc luôn đi kèm từng dòng." },
  { icon: Landmark, title: "Sổ sách đúng từ đầu", text: "Mọi khoản thu chi ghi kép, chỉ thêm không sửa đè; sai thì có bút toán đảo. Cuối tháng khoá sổ trong vài phút." },
  { icon: ShieldCheck, title: "Trợ lý không vượt quyền", text: "Việc trong hạn mức thì làm ngay; chi tiền, đổi giá, đổi hạn mức nợ luôn chờ người duyệt. Mọi thao tác có dấu vết." },
  { icon: MonitorSmartphone, title: "Web, Mac và iPhone", text: "Một tài khoản, cùng dữ liệu trên mọi máy. Đăng nhập bằng email công ty hoặc Gmail mà quản trị đã mời." },
];

const STEPS = [
  { title: "Mời người vào", text: "Chủ hoặc quản trị thêm email từng người và chọn vai: chủ, kế toán, kinh doanh…" },
  { title: "Nối nguồn tiền", text: "Tải sao kê, chụp phiếu, nhập đơn từ Excel — không phải gõ lại." },
  { title: "Trợ lý làm, bạn duyệt", text: "Việc trong hạn mức chạy ngay; việc cần người thì soạn sẵn để bạn bấm duyệt." },
];

export function Landing() {
  return (
    <div className="h-full overflow-y-auto bg-canvas text-foreground">
      <header className="sticky top-0 z-10 border-b border-hairline bg-canvas/85 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-4 sm:px-6">
          <Wordmark />
          <LoginLink small />
        </div>
      </header>

      <main>
        <section className="mx-auto max-w-6xl px-4 pt-12 pb-8 sm:px-6 md:pt-16">
          <p className="t12m inline-flex items-center gap-1.5 rounded-full bg-primary/10 px-3 py-1 text-primary">
            <Sparkles className="size-3.5" /> Trợ lý làm, bạn duyệt
          </p>
          <h1 className="mt-4 max-w-3xl text-[32px] leading-[1.2] font-semibold tracking-tight sm:text-[44px]">
            Bán hàng, thu tiền, sổ sách — <span className="text-primary">trợ lý đối chiếu trước khi làm</span>
          </h1>
          <p className="mt-4 max-w-2xl text-[16px] leading-relaxed text-muted-foreground">
            Dữ liệu thật hiếm khi gọn gàng: chuyển khoản không ghi số đơn, phiếu viết tay nhoè, hoá đơn sai giá. Barista đọc đủ nguồn, thử từng khả năng, loại khả năng
            sai — rồi mới làm việc trong hạn mức, hoặc soạn sẵn để bạn duyệt.
          </p>
          <div className="mt-6 flex flex-wrap items-center gap-3">
            <LoginLink />
            <a href="#xem-tro-ly" className="t14m rounded-xl px-4 py-2.5 text-primary hover:bg-primary/10">
              Xem trợ lý làm việc
            </a>
          </div>
        </section>

        <section id="xem-tro-ly" className="mx-auto max-w-6xl scroll-mt-16 px-4 pb-16 sm:px-6">
          <AgentStage />
          <p className="t12 mt-3 text-muted-foreground">Tên khách, số tiền, số đơn đều là ví dụ minh hoạ.</p>
        </section>

        <section className="mx-auto max-w-6xl px-4 pb-16 sm:px-6">
          <h2 className="t20">Làm được gì</h2>
          <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {FEATURES.map((f) => (
              <div key={f.title} className="rounded-2xl bg-surface p-5">
                <span className="inline-flex size-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
                  <f.icon className="size-[18px]" />
                </span>
                <h3 className="t16 mt-3">{f.title}</h3>
                <p className="t14 mt-1.5 text-muted-foreground">{f.text}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mx-auto max-w-6xl px-4 pb-16 sm:px-6">
          <h2 className="t20">Bắt đầu thế nào</h2>
          <ol className="mt-5 grid gap-3 md:grid-cols-3">
            {STEPS.map((s, i) => (
              <li key={s.title} className="rounded-2xl bg-surface p-5">
                <span className="t14m tnum inline-flex size-8 items-center justify-center rounded-full bg-primary text-primary-foreground">{i + 1}</span>
                <h3 className="t16 mt-3">{s.title}</h3>
                <p className="t14 mt-1.5 text-muted-foreground">{s.text}</p>
              </li>
            ))}
          </ol>
        </section>

        <section className="mx-auto max-w-6xl px-4 pb-20 sm:px-6">
          <div className="flex flex-col items-start justify-between gap-4 rounded-2xl bg-primary p-6 text-primary-foreground sm:flex-row sm:items-center sm:p-8">
            <div>
              <h2 className="t20">Đã được mời vào công ty?</h2>
              <p className="t14 mt-1 opacity-90">Đăng nhập bằng email của bạn — mã đăng nhập gửi thẳng vào hộp thư.</p>
            </div>
            <Link href="/login" className="t14m inline-flex items-center gap-2 rounded-xl bg-surface px-5 py-2.5 text-foreground hover:opacity-90">
              Đăng nhập <ArrowRight className="size-4" />
            </Link>
          </div>
        </section>
      </main>

      <footer className="border-t border-hairline">
        <div className="t12 mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-2 px-4 py-6 text-muted-foreground sm:px-6">
          <Wordmark small />
          <span>Mã nguồn mở · Apache 2.0</span>
        </div>
      </footer>
    </div>
  );
}

function Wordmark({ small = false }: { small?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span className={small ? "size-5 rounded-md" : "size-7 rounded-lg"} style={{ background: "linear-gradient(180deg, #FFA45C, #F0642D)" }} aria-hidden />
      <span className={small ? "t14m text-foreground" : "t16"}>Barista</span>
    </span>
  );
}

function LoginLink({ small = false }: { small?: boolean }) {
  return (
    <Link
      href="/login"
      className={`t14m inline-flex items-center gap-1.5 rounded-xl bg-primary text-primary-foreground hover:opacity-90 ${small ? "px-3.5 py-1.5" : "px-5 py-2.5"}`}
    >
      Đăng nhập {small ? null : <ArrowRight className="size-4" />}
    </Link>
  );
}
