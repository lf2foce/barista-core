import {
  EyeOff, MessageCircleQuestion, MessageSquareText,
  RefreshCw,
  ArrowLeftRight, ArrowUpRight, Banknote, Building, Building2, BookOpen, CalendarDays, CalendarRange, ChartBar, ChartLine, CheckCheck,
  CircleCheck, Clock, FileText, FileType, Gauge, GitBranch, Grid3x3, LayoutGrid, ListChecks, ListTodo, Lock, LockKeyhole, Megaphone, MessagesSquare,
  Percent, Search, ShieldCheck, Signpost, Sparkles, Sun, WandSparkles, Palette, TriangleAlert, Upload, UserRoundCog, UserSquare, Users, Wallet, Waypoints, Columns3,
  Inbox, Layers, Receipt, Settings2, History, Mail, Bell, House, GraduationCap, CalendarClock, SlidersHorizontal, Undo2, ScrollText,
  BookText, Brain, Briefcase, ChartBarBig, Settings, Send, Stethoscope, Paperclip, ToggleRight, SquarePen, Files, Hourglass,
  AppWindow, ArrowRightLeft, ShoppingBag, Zap, Book, CalendarPlus, ShoppingCart, ChartPie, ClipboardCheck, ShieldAlert, Folder, SquareFunction,
  Globe, ListTree, ListOrdered, Star, UserCog, UsersRound, CalendarSearch, IdCard, Phone, PhoneCall, Grid2x2, Table, Tag, Target, Type, Timer,
  Trophy, Video, FileSearch, Image, Flag, List, Link, PanelLeft, Puzzle,
  type LucideIcon,
} from "lucide-react";

// Server mô tả icon bằng tên SF Symbols (app Mac) — web ánh xạ sang lucide; tên lạ ⇒ ô lưới. Mọi tên server khai phải có ở đây
// (``backend/tests/test_web_icons.py`` đỏ khi thiếu — trước đây 36 tên rơi về cùng một ô lưới).
const MAP: Record<string, LucideIcon> = {
  "eye.trianglebadge.exclamationmark": EyeOff,   // Ai thấy thông tin gì (0239)
  "questionmark.bubble": MessageCircleQuestion,   // Câu hỏi cấu hình công ty
  "text.bubble": MessageSquareText,               // Câu gợi ý trợ lý theo vai
  "sidebar.left": PanelLeft,                      // Thanh bên theo vai (0255)
  "arrow.left.arrow.right.square": ArrowLeftRight,
  "list.bullet.clipboard": ListTodo,
  "photo.on.rectangle": Image,
  "calendar.circle": CalendarDays,
  "chart.bar": ChartBar,
  "chart.line.uptrend.xyaxis.circle": ChartLine,
  "checklist.checked": ListChecks,
  "checkmark.circle": CircleCheck,
  "clock.badge.checkmark": Clock,
  "lock.rectangle.stack": LockKeyhole,
  paperplane: Send,
  "person.2.wave.2": Users,
  stethoscope: Stethoscope,
  "sun.max": Sun,
  "person.crop.rectangle.stack": Users,
  "person.2.fill": Users,
  "arrow.triangle.branch": GitBranch,
  "tray.and.arrow.down": Inbox,
  "signpost.right": Signpost,
  "building.2": Building2,
  "building.2.fill": Building2,
  "lock.badge.clock": Lock,
  "square.grid.3x3": Grid3x3,
  "doc.text": FileText,
  "doc.text.magnifyingglass": FileSearch,   // gói ngoài lõi (thẩm định chứng từ)
  "doc.text.fill": FileText,
  calendar: CalendarDays,
  "calendar.day.timeline.left": CalendarRange,
  "wallet.pass": Wallet,
  "wallet.pass.fill": Wallet,
  percent: Percent,
  "megaphone.fill": Megaphone,
  megaphone: Megaphone,
  "chart.line.uptrend.xyaxis": ChartLine,
  "chart.bar.xaxis": ChartBar,
  "banknote.fill": Banknote,
  banknote: Banknote,
  "doc.plaintext": Receipt,
  "arrow.up.right.square": ArrowUpRight,
  "tray.full": Layers,
  "books.vertical": BookOpen,
  "books.vertical.fill": BookOpen,
  "square.and.arrow.up": Upload,
  "arrow.left.arrow.right": ArrowLeftRight,
  checklist: ListChecks,
  "rectangle.split.3x1": Columns3,
  "bubble.left.and.bubble.right": MessagesSquare,
  "bubble.left.and.bubble.right.fill": MessagesSquare,
  "list.bullet": List,
  "list.bullet.rectangle": ListTodo,
  "clock.arrow.circlepath": History,
  "arrow.triangle.2.circlepath": RefreshCw,
  "doc.richtext": FileType,
  "doc.richtext.fill": FileType,
  "square.and.pencil": SquarePen,
  "doc.on.doc": Files,
  "gearshape.2.fill": Settings2,
  "person.badge.key": UserRoundCog,
  "point.3.connected.trianglepath.dotted": Waypoints,
  "lock.shield": ShieldCheck,
  "square.stack.3d.up": Layers,
  "paperclip": Paperclip,
  "switch.2": ToggleRight,
  "rectangle.and.pencil.and.ellipsis": SquarePen,   // Trường riêng của công ty (0060)
  sparkles: Sparkles,
  "wand.and.stars": WandSparkles,
  paintpalette: Palette,
  "exclamationmark.triangle": TriangleAlert,
  "building.columns": Building,
  checkmark: CircleCheck,
  "checkmark.seal": CheckCheck,
  "checkmark.seal.fill": CheckCheck,
  magnifyingglass: Search,
  "person.crop.circle": UserSquare,
  clock: Clock,
  hourglass: Hourglass,
  gauge: Gauge,
  "gauge.with.dots.needle.bottom.50percent": Gauge,
  "lock.shield.fill": LockKeyhole,
  mail: Mail,
  bell: Bell,
  "bell.fill": Bell,
  "person.3.fill": Users,
  "square.grid.2x2.fill": LayoutGrid,
  house: House,
  graduationcap: GraduationCap,
  "graduationcap.fill": GraduationCap,
  "house.fill": House,
  building: Building,
  "calendar.badge.clock": CalendarClock,
  "person.crop.square": UserSquare,
  "slider.horizontal.3": SlidersHorizontal,
  "arrow.uturn.up.square": Undo2,
  "list.bullet.rectangle.portrait": ScrollText,
  "book.closed": BookText,
  "book.closed.fill": BookText,
  "briefcase.fill": Briefcase,
  "chart.bar.doc.horizontal": ChartBarBig,
  gearshape: Settings,
  "brain.head.profile": Brain,
  "puzzlepiece.extension": Puzzle,
  link: Link,   // Kết nối ngoài (0244)
  "app.connected.to.app.below": AppWindow,
  "arrow.left.arrow.right.circle": ArrowRightLeft,
  "arrow.up.forward.square": ArrowUpRight,
  "bag": ShoppingBag,
  "bolt": Zap,
  "book": Book,
  "calendar.badge.plus": CalendarPlus,
  cart: ShoppingCart,
  "chart.pie": ChartPie,
  "checkmark.rectangle.stack": ClipboardCheck,
  "exclamationmark.shield": ShieldAlert,
  folder: Folder,
  function: SquareFunction,
  globe: Globe,
  flag: Flag,
  "list.bullet.indent": ListTree,
  "list.number": ListOrdered,
  "list.star": Star,
  lock: Lock,
  "person.2": Users,
  "person.2.badge.gearshape": UserCog,
  "person.3": UsersRound,
  "person.badge.clock": CalendarSearch,
  "person.text.rectangle": IdCard,
  phone: Phone,
  "phone.connection": PhoneCall,
  "square.grid.2x2": LayoutGrid,
  "square.split.2x2": Grid2x2,
  tablecells: Table,
  tag: Tag,
  target: Target,
  textformat: Type,
  timer: Timer,
  trophy: Trophy,
  video: Video,
};

/** `filled`: biểu tượng đang chọn trên thanh bên trái — tô nhạt phần kín + nét đậm hơn (SF Symbols ".fill"). */
export function SfIcon({ name, className, filled = false }: { name: string; className?: string; filled?: boolean }) {
  const Cmp = MAP[name] ?? MAP[name.replace(/\.fill$/, "")] ?? MAP[`${name}.fill`] ?? LayoutGrid;
  return filled ? <Cmp className={className} fill="currentColor" fillOpacity={0.22} strokeWidth={2.25} aria-hidden /> : <Cmp className={className} aria-hidden />;
}

/** Biểu tượng theo phân hệ của một thao tác / loại đối tượng (ModuleStyle.swift): "crm.lead" → khách. */
const MODULE_ICON: Record<string, string> = {
  crm: "person.2.fill", catalog: "building.2.fill", deals: "doc.text.fill", commission: "wallet.pass.fill", acct: "banknote.fill",
  mkt: "megaphone.fill", admin: "gearshape.2.fill", core: "checkmark.seal.fill", kb: "books.vertical.fill", papers: "doc.richtext.fill",
  talk: "bubble.left.and.bubble.right.fill", flow: "checklist", leadflow: "arrow.triangle.branch", work: "clock.arrow.circlepath",
  hosting: "house.fill",
  training: "graduationcap.fill",
  books: "book.closed.fill",
  accounting_service: "briefcase.fill",
  parties: "person.crop.rectangle.stack", hr: "person.text.rectangle", kpi: "target", schedule: "calendar", meet: "video", mgmt: "chart.bar.doc.horizontal",
  pay: "banknote", findings: "doc.text.magnifyingglass", compose: "paintpalette", telephony: "phone", timekeeping: "clock.badge.checkmark",
  word: "doc.richtext", connect: "app.connected.to.app.below", connectors: "arrow.triangle.2.circlepath", agent: "sparkles",
};
export function moduleIcon(op: string): string {
  return MODULE_ICON[op.split(".")[0] ?? ""] ?? "square.grid.2x2.fill";
}

/** Màu theo phân hệ của thao tác (ModuleStyle.swift): crm → khách… */
const TINT: Record<string, string> = {
  crm: "#007aff",
  catalog: "#30b0c7",
  deals: "#5856d6",
  commission: "#34c759",
  acct: "#ff9500",
  mkt: "#ff2d55",
  admin: "#8e8e93",
  core: "#af52de",
  kb: "#a2845e",
  papers: "#5856d6",
  talk: "#32ade6",
  flow: "#00c7be",
  leadflow: "#007aff",
  work: "#ff9500",
  hosting: "#30b0c7",
  training: "#af52de",
  books: "#34c759",
  accounting_service: "#5856d6",
  parties: "#007aff", hr: "#5856d6", kpi: "#ff2d55", schedule: "#ff9500", meet: "#34c759", mgmt: "#5856d6", pay: "#34c759", findings: "#a2845e",
  compose: "#ff2d55", telephony: "#34c759", timekeeping: "#00c7be", word: "#007aff", connect: "#30b0c7", connectors: "#30b0c7", agent: "#af52de",
};
export function moduleTint(op: string): string {
  return TINT[op.split(".")[0] ?? ""] ?? "#007aff";
}

// Màu hệ thống theo tên server khai trên màn (kernel/screens.py TINTS) — như Color.named trong app Mac.
const NAMED: Record<string, string> = {
  blue: "#007aff", teal: "#30b0c7", green: "#34c759", orange: "#ff9500", red: "#ff3b30", pink: "#ff2d55", purple: "#af52de",
  indigo: "#5856d6", mint: "#00c7be", brown: "#a2845e", gray: "#8e8e93", yellow: "#ffcc00", cyan: "#32ade6",
};
/** Màu của một màn: server khai theo ý nghĩa; chưa khai ⇒ màu phân hệ của truy vấn. */
export function screenTint(s: { tint?: string | null; query: string }): string {
  return (s.tint && NAMED[s.tint]) || moduleTint(s.query);
}

/** Ô biểu tượng tô màu (đầu thẻ, dòng xem trước) — như IconTile.swift. */
export function IconTile({ icon, tint, size = 30, node }: { icon?: string; tint: string; size?: number; node?: React.ReactNode }) {
  return (
    <span
      className="inline-flex shrink-0 items-center justify-center text-white"
      style={{
        width: size,
        height: size,
        borderRadius: size * 0.3,
        background: `linear-gradient(135deg, ${tint}, color-mix(in oklab, ${tint} 62%, transparent))`,
      }}
    >
      {node ?? <SfIcon name={icon ?? ""} className="size-[46%]" />}
    </span>
  );
}
