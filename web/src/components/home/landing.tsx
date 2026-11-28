import Image from "next/image";
import Link from "next/link";
import { ArrowRight, BellRing, BookCheck, CircleCheck, MonitorSmartphone, ShieldCheck } from "lucide-react";
import { AgentStage } from "@/components/home/agent-stage";
import { HeroDemo } from "@/components/home/hero-demo";
import "./landing.css";

/** Trang giới thiệu sản phẩm ở "/" cho người chưa đăng nhập (bản cài khai ``"landing": true`` — lib/landing.ts). Không gọi core; mọi tên, số tiền,
 *  mã đơn trong ảnh minh hoạ là GIẢ. Màu chỉ qua biến giao diện (sáng + tối), một màu nhấn (--primary); không dải màu, không đổ bóng ở thẻ.
 *  Thân trang của app không cuộn (globals.css) ⇒ trang tự cuộn trong khung của nó. */

const PRINCIPLES = [
  { icon: ShieldCheck, title: "Trợ lý không vượt quyền", text: "Làm bằng đúng quyền của người giao việc. Chi tiền, đổi giá, đổi hạn mức nợ luôn chờ người duyệt." },
  { icon: BookCheck, title: "Tiền chỉ đi qua sổ cái", text: "Ghi kép, chỉ thêm, sửa bằng bút toán đảo. Mỗi con số truy được về chứng từ gốc." },
  { icon: MonitorSmartphone, title: "Web, Mac và iPhone", text: "Một dữ liệu, ba nơi dùng. Duyệt đề xuất của trợ lý chỉ bằng một chạm trên điện thoại." },
];

const STEPS = [
  { title: "Thiết lập tổ chức và phân quyền", text: "Quản trị viên mời nhân sự bằng email và gán vai trò: chủ doanh nghiệp, kế toán, kinh doanh…" },
  { title: "Kết nối dữ liệu hiện có", text: "Nhập sao kê, chứng từ, đơn hàng và kế hoạch từ Excel. Hệ thống tự khớp cột, người phụ trách xác nhận." },
  { title: "Vận hành cùng trợ lý", text: "Việc trong hạn mức được xử lý ngay; việc cần phê duyệt được soạn sẵn để người có thẩm quyền xác nhận." },
];

export function Landing() {
  return (
    <div className="bl relative h-full overflow-x-hidden overflow-y-auto bg-canvas text-foreground">
      {/* nền đầu trang: quầng xanh nhạt + lưới chấm, mờ dần xuống ảnh sản phẩm — chỉ trang trí */}
      <div aria-hidden className="bl-backdrop" />
      <header className="sticky top-0 z-20 border-b border-hairline bg-canvas/70 backdrop-blur-md">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between gap-4 px-4 sm:px-6">
          <Wordmark />
          <nav className="t14 hidden items-center gap-7 text-muted-foreground md:flex" aria-label="Mục của trang">
            <a href="#tinh-nang" className="hover:text-foreground">Tính năng</a>
            <a href="#xem-tro-ly" className="hover:text-foreground">Xem trợ lý</a>
            <a href="#cach-lam" className="hover:text-foreground">Bắt đầu</a>
          </nav>
          <Link href="/login" className="t14m inline-flex h-9 shrink-0 items-center whitespace-nowrap rounded-full bg-primary px-4 text-primary-foreground hover:opacity-90">
            Đăng nhập
          </Link>
        </div>
      </header>

      <main className="relative">
        {/* ---- đầu trang: chữ lớn, rồi chính sản phẩm đang chạy ---- */}
        <section className="mx-auto grid max-w-6xl items-center gap-12 px-4 pt-12 sm:px-6 md:pt-20 lg:grid-cols-[minmax(0,1fr)_400px]">
          <div>
            <a href="#du-an" className="bl-small inline-flex items-center gap-2 rounded-full border border-hairline bg-surface py-1 pr-3 pl-1 font-medium text-muted-foreground hover:text-foreground">
              <span className="rounded-full bg-primary/12 px-2 py-0.5 text-primary">Mới</span>
              Quản trị dự án từ file Excel đang có
              <ArrowRight className="size-3.5" />
            </a>
            <h1 className="bl-display mt-6">
              Trợ lý làm.
              <br />
              <span className="bl-grad">Bạn duyệt.</span>
            </h1>
            <p className="bl-lead mt-6 max-w-xl text-muted-foreground">
              Một nền tảng cho bán hàng, thu chi, kế toán, dự án và văn bản. Trợ lý AI làm việc trên dữ liệu thật của doanh nghiệp, tự xử lý trong hạn mức được giao và chuẩn bị sẵn những việc cần bạn phê duyệt.
            </p>
            <div className="mt-8 flex flex-wrap items-center gap-x-6 gap-y-3">
              <Link href="/login" className="t14m inline-flex h-11 items-center gap-2 rounded-full bg-primary px-5 text-primary-foreground hover:opacity-90">
                Đăng nhập <ArrowRight className="size-4" />
              </Link>
              <a href="#xem-tro-ly" className="t14m inline-flex items-center gap-1.5 text-foreground hover:text-primary">
                Xem trợ lý suy luận <ArrowRight className="size-4" />
              </a>
            </div>
          </div>
          <HeroPhoto />
        </section>

        <section className="mx-auto mt-12 max-w-6xl md:mt-16" aria-label="Minh hoạ: tiền về, trợ lý đề xuất khớp đơn, người bấm duyệt">
          <HeroDemo />
        </section>

        <section className="border-y border-hairline bg-surface">
          <div className="mx-auto grid max-w-6xl gap-8 px-4 py-12 sm:px-6 md:grid-cols-3 md:gap-10">
            {PRINCIPLES.map((p) => (
              <div key={p.title}>
                <p.icon className="size-5 text-primary" aria-hidden />
                <h3 className="bl-title mt-3">{p.title}</h3>
                <p className="bl-body mt-1.5 text-muted-foreground">{p.text}</p>
              </div>
            ))}
          </div>
        </section>

        {/* ---- tính năng: lưới ô, mỗi ô một hình khác loại ---- */}
        <section id="tinh-nang" className="mx-auto max-w-6xl scroll-mt-20 px-4 py-20 sm:px-6 md:py-28">
          <SectionTitle lead="Một bàn làm việc cho cả công ty." rest="Mỗi người thấy việc của vai mình, trợ lý đọc cùng dữ liệu đó." />
          <div className="bl-bento mt-12">
            <Tile id="du-an" className="bl-span2" lead="Kế hoạch & dự án." rest="Nhập file Excel hoặc MS Project đang có. Máy dựng tiến độ, chỉ ra ai đang chậm, bấm một lần để nhắc.">
              <PlanPicture />
            </Tile>
            <div className="bl-tile bl-tile--ink">
              {/* ảnh minh hoạ do AI tạo — không phải người thật có danh tính */}
              <Image src="/landing/duyet.webp" alt="" fill sizes="(max-width: 860px) 100vw, 380px" className="bl-photo" />
              <p className="bl-statement">Duyệt luôn là người.</p>
              <p className="bl-photo__note bl-body">Trợ lý chạy thử mọi đề xuất trên dữ liệu thật trước khi đưa bạn xem. Bấm Duyệt mới ghi.</p>
            </div>
            <Tile lead="Nhắc nợ như người thật." rest="Lời nhắc soạn riêng từng khách; bạn đọc lại rồi gửi.">
              <RemindPicture />
            </Tile>
            <Tile lead="Chụp phiếu là xong." rest="Phiếu viết tay, hoá đơn, sao kê: máy đọc và điền sẵn, ảnh gốc đi kèm.">
              <ReceiptPicture />
            </Tile>
            <Tile lead="Soạn & rà văn bản." rest="Soạn thẳng vào Word, đối chiếu với số liệu công ty trước khi gửi.">
              <DocPicture />
            </Tile>
          </div>
        </section>

        <section id="xem-tro-ly" className="mx-auto max-w-6xl scroll-mt-20 px-4 pb-20 sm:px-6 md:pb-28">
          <SectionTitle lead="Bên trong một lượt của trợ lý." rest="Đọc nguồn, thử từng khả năng, rồi mới quyết việc nào tự làm, việc nào chờ bạn." />
          <div className="mt-12">
            <AgentStage />
            <p className="bl-small mt-3 text-muted-foreground">Tên khách, số tiền, số đơn trên trang này đều là ví dụ minh hoạ.</p>
          </div>
        </section>

        <section id="cach-lam" className="border-t border-hairline">
          <div className="mx-auto max-w-6xl scroll-mt-20 px-4 py-20 sm:px-6 md:py-28">
            <SectionTitle lead="Triển khai theo ba bước." rest="Kế thừa dữ liệu sẵn có của doanh nghiệp, không phải nhập lại từ đầu." />
            <ol className="mt-12 grid gap-px overflow-hidden rounded-2xl border border-hairline bg-hairline md:grid-cols-3">
              {STEPS.map((s, i) => (
                <li key={s.title} className="bg-surface p-6 md:p-8">
                  <span className="tnum bl-small font-medium text-primary">0{i + 1}</span>
                  <h3 className="bl-title mt-6">{s.title}</h3>
                  <p className="bl-body mt-1.5 text-muted-foreground">{s.text}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section className="border-t border-hairline">
          <div className="mx-auto flex max-w-6xl flex-col items-start gap-6 px-4 py-20 sm:px-6 md:flex-row md:items-end md:justify-between md:py-24">
            <h2 className="bl-h2 max-w-xl">
              Sẵn sàng vận hành cùng trợ lý?
              <span className="block text-muted-foreground">Đăng nhập bằng email đã được doanh nghiệp cấp quyền.</span>
            </h2>
            <Link href="/login" className="t14m inline-flex h-11 shrink-0 items-center gap-2 rounded-full bg-primary px-5 text-primary-foreground hover:opacity-90">
              Đăng nhập <ArrowRight className="size-4" />
            </Link>
          </div>
        </section>
      </main>

      <footer className="border-t border-hairline">
        <div className="bl-small mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-4 py-8 text-muted-foreground sm:px-6">
          <Wordmark small />
          <nav className="flex flex-wrap items-center gap-5" aria-label="Liên kết cuối trang">
            <a href="#tinh-nang" className="hover:text-foreground">Tính năng</a>
            <a href="#xem-tro-ly" className="hover:text-foreground">Xem trợ lý</a>
            <a href="#cach-lam" className="hover:text-foreground">Bắt đầu</a>
            <span>Mã nguồn mở · Apache 2.0</span>
          </nav>
        </div>
      </footer>
    </div>
  );
}

/** Ảnh bên phải tiêu đề (màn rộng): tách latte và điện thoại trên bàn quán cà phê — duyệt việc thư thái; ảnh do AI tạo.
 *  Thẻ thông báo vắt qua mép trái ảnh (việc chờ duyệt), chip nhỏ ở góc trên (việc trợ lý tự làm trong hạn mức). Chữ là ví dụ. */
function HeroPhoto() {
  return (
    <div className="relative hidden lg:block" aria-hidden>
      <div className="relative aspect-[4/5] overflow-hidden rounded-[28px] border border-hairline">
        <Image src="/landing/cafe-xanh.webp" alt="" fill priority sizes="400px" className="object-cover object-[40%_50%]" />
      </div>
      <span className="t12m absolute -top-3 right-6 inline-flex items-center gap-1.5 rounded-full border border-hairline bg-surface px-3 py-1.5 shadow-[0_8px_20px_-10px_rgb(0_0_0/0.25)]">
        <CircleCheck className="size-3.5 text-ok" /> Đã tự khớp 12 khoản tiền về
      </span>
      <div className="absolute bottom-10 -left-16 w-[288px] rounded-2xl border border-hairline bg-surface p-4 shadow-[0_18px_40px_-16px_rgb(0_0_0/0.3)]">
        <div className="t12 flex items-center gap-2 text-muted-foreground">
          <span className="size-2 rounded-full bg-primary" /> Trợ lý · vừa xong
        </div>
        <p className="t14m mt-2">Chờ chị duyệt: tạm ứng công tác</p>
        <p className="t12 mt-1 leading-relaxed text-muted-foreground">5.000.000 ₫ cho anh Quân. Trợ lý đã đối chiếu hạn mức và chứng từ.</p>
        <div className="mt-3 flex gap-2">
          <span className="t12m inline-flex h-8 items-center rounded-full bg-primary px-4 text-primary-foreground">Duyệt</span>
          <span className="t12m inline-flex h-8 items-center rounded-full border border-hairline px-4">Xem chi tiết</span>
        </div>
      </div>
    </div>
  );
}

function Wordmark({ small = false }: { small?: boolean }) {
  return <span className={`font-semibold tracking-tight text-foreground ${small ? "text-[15px]" : "text-[18px]"}`}>Barista</span>;
}

/** Tiêu đề mục kiểu "câu dẫn đậm + phần xám nối tiếp", căn trái. */
function SectionTitle({ lead, rest }: { lead: string; rest: string }) {
  return (
    <h2 className="bl-h2 max-w-3xl">
      {lead} <span className="text-muted-foreground">{rest}</span>
    </h2>
  );
}

function Tile({ id, className = "", lead, rest, children }: { id?: string; className?: string; lead: string; rest: string; children: React.ReactNode }) {
  return (
    <div id={id} className={`bl-tile scroll-mt-20 ${className}`}>
      <p className="bl-tile__copy">
        <span className="text-foreground">{lead}</span> {rest}
      </p>
      <div className="bl-pic" aria-hidden>{children}</div>
    </div>
  );
}

/* ---- hình trong các ô (tĩnh, chữ là ví dụ) ---- */

const PLAN = [
  { name: "Khảo sát hiện trạng", who: "Lan", start: 0, len: 3, state: "done" },
  { name: "Chuẩn bị dữ liệu", who: "Minh", start: 3, len: 5, state: "late" },
  { name: "Cấu hình quy trình", who: "Hà", start: 3, len: 4, state: "doing" },
  { name: "Đào tạo người dùng", who: "Lan", start: 8, len: 2, state: "todo" },
] as const;

function PlanPicture() {
  const col = 100 / 12;
  return (
    <div className="bl-plan">
      <div className="bl-plan__grid" style={{ backgroundSize: `${col}% 100%` }} />
      {PLAN.map((t) => (
        <div key={t.name} className="bl-plan__row">
          <span className="bl-plan__name">{t.name}</span>
          <span className="bl-plan__track">
            <span className="bl-plan__bar" data-state={t.state} style={{ left: `${t.start * col}%`, width: `${t.len * col}%` }}>
              <span className="bl-plan__who">{t.who}</span>
            </span>
            {t.state === "late" ? <span className="bl-plan__flag" style={{ left: `${(t.start + t.len) * col}%` }}>Trễ 2 ngày</span> : null}
          </span>
        </div>
      ))}
      <div className="bl-plan__row">
        <span className="bl-plan__name">Nghiệm thu</span>
        <span className="bl-plan__track"><span className="bl-plan__ms" style={{ left: `${10 * col}%` }} /></span>
      </div>
      <span className="bl-plan__nudge"><BellRing className="size-3.5" /> Nhắc Minh</span>
    </div>
  );
}

function RemindPicture() {
  return (
    <div className="bl-remind">
      <div className="bl-remind__to"><span className="bl-ava">H</span><span><b>Chị Hạnh</b> · Tạp hoá Phúc An</span></div>
      <p className="bl-remind__msg">Chào chị Hạnh, hoá đơn HĐ-0213 (8.400.000 ₫) đến hạn ngày 30/11. Em gửi lại mã QR để chị tiện chuyển khoản ạ.</p>
      <div className="bl-remind__acts"><span className="bl-btn bl-btn--quiet">Sửa</span><span className="bl-btn">Gửi</span></div>
    </div>
  );
}

function ReceiptPicture() {
  return (
    <div className="bl-receipt">
      <div className="bl-paper">
        <span className="bl-paper__title">Phiếu chi</span>
        {[78, 56, 70, 44, 62].map((w, i) => <span key={i} className="bl-ink" style={{ width: `${w}%` }} />)}
        <span className="bl-paper__sum">1.250.000</span>
      </div>
      <div className="bl-fields">
        <span className="bl-field"><span>Số tiền</span><b>1.250.000 ₫</b></span>
        <span className="bl-field"><span>Ngày</span><b>26 tháng 11</b></span>
        <span className="bl-field"><span>Khoản mục</span><b>Văn phòng phẩm</b></span>
      </div>
    </div>
  );
}

function DocPicture() {
  return (
    <div className="bl-doc">
      <span className="bl-doc__h">Phụ lục hợp đồng số 07</span>
      {[92, 84, 88].map((w, i) => <span key={i} className="bl-ink" style={{ width: `${w}%` }} />)}
      <p className="bl-doc__p">Giá trị hợp đồng là <mark>120.000.000 ₫</mark>, thanh toán làm hai đợt.</p>
      {[86, 64].map((w, i) => <span key={i} className="bl-ink" style={{ width: `${w}%` }} />)}
      <span className="bl-doc__note">Đơn gốc ghi 112.000.000 ₫</span>
    </div>
  );
}
