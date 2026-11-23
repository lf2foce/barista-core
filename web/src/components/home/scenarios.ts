/** Tình huống minh hoạ của trang giới thiệu (dữ liệu GIẢ — tên, số tiền đều là ví dụ). Mỗi tình huống là một cây: sự kiện ⇒ trợ lý ⇒ các ô theo CỘT
 *  (nguồn dữ liệu, hoặc khả năng giữ / loại — ``hyp``), nối với ô cha bằng ``parent`` ⇒ vạch kiểm soát ⇒ thẻ kết quả (``from`` = ô dẫn tới việc đó).
 *  ``y`` là toạ độ dọc trong khung 660 điểm (giữa = 340). ``say`` có (số cột + 1) câu: một câu cho mỗi cột, câu cuối khi ra kết quả. */

export type Kind = "auto" | "wait" | "ask";

export type Node = { id: string; col: number; y: number; t: string; res: string; parent?: string; hyp?: boolean; rej?: boolean };

export type Action = { from: string; kind: Kind; title: string; why: string; rule?: string; who?: string; opts?: [string, string] };

export type Scenario = {
  key: string;
  tab: string;
  hint: string;
  time: string;
  event: string;
  role: string;
  rawK: string;
  raw: string;
  flag: string;       // chỗ phần mềm thường dừng lại
  chain: number[];    // các khâu của quy trình tình huống này chạm tới
  nodes: Node[];
  say: string[];
  actions: Action[];  // tối đa 4, xếp theo thứ tự trên → dưới
};

export type Flow = { key: string; title: string; who: string; chain: string[]; scenarios: Scenario[] };

export const FLOWS: Flow[] = [
  {
    key: "sales",
    title: "Bán hàng → thu tiền",
    who: "Doanh nghiệp nhỏ và vừa",
    chain: ["Báo giá", "Đơn hàng", "Giao hàng", "Hoá đơn", "Thu tiền", "Đối chiếu", "Nhắc nợ"],
    scenarios: [
      {
        key: "tien-ve",
        tab: "Tiền về, nội dung lộn xộn",
        hint: "Không có số đơn — khớp vào đơn nào?",
        time: "09:30",
        event: "48,5 triệu vào tài khoản",
        role: "vai Kế toán",
        rawK: "Nội dung chuyển khoản",
        raw: "CTY AN PHAT CK TT T11",
        flag: "Phần mềm thường dừng ở đây: không có số đơn, đẩy vào “chưa khớp”",
        chain: [4, 5],
        nodes: [
          { id: "sk", col: 1, y: 340, t: "Sao kê ngân hàng", res: "48.500.000 đ · không ghi số đơn" },
          { id: "h1", col: 2, y: 140, parent: "sk", hyp: true, t: "Là đơn DH-1182?", res: "An Phát · đúng số tiền · hạn 20/11" },
          { id: "h2", col: 2, y: 330, parent: "sk", hyp: true, rej: true, t: "Gộp hai đơn nhỏ?", res: "DH-1175 + DH-1179 lệch 300.000 đ" },
          { id: "tn", col: 2, y: 500, parent: "sk", t: "Tin nhắn của khách", res: "kế toán An Phát hẹn chuyển hôm nay" },
          { id: "hd", col: 3, y: 110, parent: "h1", t: "Hoá đơn", res: "HĐ 0001182 đã xuất ngày 18/11" },
          { id: "cn", col: 3, y: 270, parent: "h1", t: "Công nợ của khách", res: "DH-1150 · 12 triệu quá hạn 25 ngày" },
          { id: "hm", col: 3, y: 520, parent: "tn", t: "Hạn mức bán chịu", res: "An Phát đang bị khoá vì nợ quá hạn" },
        ],
        say: [
          "Tiền vào 48,5 triệu nhưng nội dung không có số đơn. Đọc sao kê.",
          "Có hai cách khớp: một đơn DH-1182, hoặc gộp hai đơn nhỏ. Gộp hai đơn lệch 300.000 đ — loại. Tin nhắn hôm qua của khách hẹn chuyển hôm nay.",
          "Khớp đơn DH-1182, hoá đơn đã xuất. An Phát còn một đơn quá hạn và đang bị khoá bán chịu.",
          "Ghi nhận khoản thu, gửi xác nhận nằm trong hạn mức — làm ngay. Mở lại bán chịu cần trưởng phòng duyệt. Cách nhắc đơn quá hạn: hỏi bạn.",
        ],
        actions: [
          { from: "hd", kind: "auto", title: "Ghi nhận đã thu đơn DH-1182", why: "khớp số tiền, tên công ty và tin nhắn hẹn chuyển", rule: "được tự ghi khi khoản thu khớp đúng một đơn" },
          { from: "cn", kind: "ask", title: "DH-1150 quá hạn 25 ngày — nhắc thế nào?", why: "khách vừa trả đơn mới, nhắc gắt dễ mất lòng", opts: ["Nhắn nhẹ", "Gọi điện"] },
          { from: "tn", kind: "auto", title: "Gửi An Phát xác nhận đã nhận tiền", why: "“Em đã nhận 48.500.000 đ cho đơn DH-1182 ạ.”", rule: "được nhắn khách của đơn mình phụ trách" },
          { from: "hm", kind: "wait", who: "trưởng phòng", title: "Mở lại bán chịu cho An Phát", why: "khách vừa trả đúng hẹn", rule: "đổi hạn mức công nợ cần trưởng phòng duyệt" },
        ],
      },
      {
        key: "cong-no",
        tab: "Nợ quá hạn đầu tuần",
        hint: "Khách quên, khách hết tiền, hay hoá đơn sai?",
        time: "08:00",
        event: "Sáng thứ Hai",
        role: "vai Kế toán",
        rawK: "Chỉ số công nợ",
        raw: "7 hoá đơn quá hạn · 186.000.000 đ · tăng 40% so với tháng trước",
        flag: "Báo cáo thường dừng ở đây: một con số đỏ, không nói vì sao",
        chain: [3, 6],
        nodes: [
          { id: "cn", col: 1, y: 340, t: "Công nợ phải thu", res: "7 hoá đơn quá hạn · 5 khách" },
          { id: "h1", col: 2, y: 140, parent: "cn", hyp: true, t: "Khách quên trả?", res: "3 khách chưa được nhắc lần nào" },
          { id: "h2", col: 2, y: 330, parent: "cn", hyp: true, rej: true, t: "Khách hết tiền?", res: "4/5 khách vẫn đặt hàng đều tháng này" },
          { id: "ht", col: 2, y: 500, parent: "cn", t: "Hộp thư", res: "Phú Gia báo sai đơn giá ngày 12/11" },
          { id: "nh", col: 3, y: 100, parent: "h1", t: "Lịch sử nhắc nợ", res: "3 khách · tổng 74.000.000 đ" },
          { id: "ls", col: 3, y: 260, parent: "h1", t: "Lịch sử thanh toán", res: "Bình Minh trả chậm 3 kỳ liên tiếp" },
          { id: "h3", col: 3, y: 500, parent: "ht", hyp: true, t: "Phú Gia: hoá đơn sai?", res: "hoá đơn 125.000 đ, báo giá 118.000 đ" },
        ],
        say: [
          "Nợ quá hạn tăng 40%. Xem từng hoá đơn, từng khách.",
          "Hai khả năng: khách quên, hoặc khách hết tiền. Bốn trên năm khách vẫn đặt hàng đều — không phải hết tiền. Hộp thư có một khách phản hồi sai giá.",
          "Ba khách chưa được nhắc lần nào. Bình Minh trả chậm có hệ thống. Hoá đơn Phú Gia lệch báo giá — khách chờ sửa nên chưa trả.",
          "Nhắc ba khách — làm ngay. Dừng bán chịu và hoá đơn điều chỉnh cần người duyệt. Bình Minh xin giãn hạn: hỏi bạn.",
        ],
        actions: [
          { from: "nh", kind: "auto", title: "Gửi lời nhắc riêng cho 3 khách", why: "soạn theo từng khách: số hoá đơn, số tiền, ngày hạn", rule: "được nhắc nợ khách mình phụ trách" },
          { from: "ls", kind: "wait", who: "trưởng phòng", title: "Tạm dừng bán chịu cho Bình Minh", why: "trả chậm 3 kỳ, đang nợ 64.000.000 đ", rule: "đổi hạn mức công nợ cần trưởng phòng duyệt" },
          { from: "ls", kind: "ask", title: "Bình Minh xin giãn 30 ngày", why: "đồng ý thì giữ được khách, nhưng nợ đã kéo 3 kỳ", opts: ["Trả trước một nửa", "Đồng ý giãn"] },
          { from: "h3", kind: "wait", who: "kế toán trưởng", title: "Xuất hoá đơn điều chỉnh cho Phú Gia", why: "báo giá 118.000 đ, hoá đơn ghi 125.000 đ", rule: "hoá đơn điều chỉnh cần kế toán trưởng duyệt" },
        ],
      },
    ],
  },
  {
    key: "solo",
    title: "Sổ sách cho hộ kinh doanh",
    who: "Cửa hàng, hộ kinh doanh một người",
    chain: ["Chụp phiếu", "Máy đọc", "Chủ xác nhận", "Sổ thu chi", "Đối chiếu ngân hàng", "Khoá sổ tháng"],
    scenarios: [
      {
        key: "chup-phieu",
        tab: "Chụp phiếu viết tay",
        hint: "Chữ nhoè, phiếu trùng, giá lạ",
        time: "18:40",
        event: "Chủ cửa hàng chụp 3 phiếu",
        role: "sổ của chủ",
        rawK: "Ảnh phiếu giao hàng",
        raw: "Xi măng 50 bao × 92 · Cát 3 khối × 380 · Thép D10 … · Tổng: 6.2?0.000 (nhoè)",
        flag: "Phần mềm thường dừng ở đây: ảnh nhoè, phải gõ lại tay",
        chain: [0, 1, 2],
        nodes: [
          { id: "doc", col: 1, y: 340, t: "Đọc ảnh phiếu", res: "3 phiếu · 11 dòng · 1 số tổng nhoè" },
          { id: "h1", col: 2, y: 140, parent: "doc", hyp: true, t: "Tổng = 6.260.000 đ?", res: "cộng 4 dòng ra đúng số này" },
          { id: "h2", col: 2, y: 330, parent: "doc", hyp: true, rej: true, t: "Tổng = 6.200.000 đ?", res: "không khớp tổng các dòng" },
          { id: "h3", col: 2, y: 500, parent: "doc", hyp: true, t: "Phiếu 3 bị trùng?", res: "trùng số phiếu 0457" },
          { id: "snh", col: 3, y: 110, parent: "h1", t: "Sổ nhập hàng", res: "lần trước cát 350.000 đ/khối" },
          { id: "ncc", col: 3, y: 270, parent: "h1", t: "Nhà cung cấp", res: "Đại lý Minh Phát · 2 phiếu mới" },
          { id: "stc", col: 3, y: 520, parent: "h3", t: "Sổ thu chi", res: "phiếu 0457 đã ghi lúc 17:12 hôm qua" },
        ],
        say: [
          "Ba ảnh phiếu viết tay. Đọc từng dòng: tên hàng, số lượng, đơn giá.",
          "Tổng phiếu 1 bị nhoè: 6.200.000 hay 6.260.000? Cộng các dòng ra 6.260.000 — chọn số này. Phiếu 3 có số trùng một phiếu cũ.",
          "Sổ nhập hàng: giá cát tăng từ 350.000 lên 380.000 đ mỗi khối. Phiếu 3 đã ghi hôm qua.",
          "Bỏ phiếu trùng, giữ ảnh gốc — làm ngay. Ghi sổ cần chị xác nhận. Giá cát tăng: hỏi chị trước khi ghi.",
        ],
        actions: [
          { from: "snh", kind: "ask", title: "Cát 380.000 đ, lần trước 350.000 đ", why: "đại lý tăng giá, hay phiếu ghi nhầm?", opts: ["Ghi theo phiếu", "Hỏi lại đại lý"] },
          { from: "ncc", kind: "wait", who: "chủ cửa hàng", title: "Ghi 2 phiếu nhập hàng 9.410.000 đ", why: "8 dòng đã đọc; tổng phiếu 1 lấy theo cộng dòng", rule: "máy đọc, chủ xác nhận mới ghi sổ" },
          { from: "doc", kind: "auto", title: "Giữ ảnh gốc kèm từng dòng", why: "bấm dòng nào cũng mở đúng ảnh phiếu để đối chiếu", rule: "chứng từ gốc luôn đi kèm sổ" },
          { from: "stc", kind: "auto", title: "Bỏ qua phiếu 3 — đã ghi hôm qua", why: "trùng số phiếu, cùng người bán, cùng số tiền", rule: "không ghi trùng chứng từ" },
        ],
      },
      {
        key: "khoa-so",
        tab: "Cuối tháng khoá sổ",
        hint: "Tiền vào không có phiếu, hoá đơn còn thiếu",
        time: "30/11",
        event: "Cuối tháng 11",
        role: "sổ của chủ",
        rawK: "Sổ tháng 11",
        raw: "214 phiếu thu · 96 phiếu chi · 2 tài khoản ngân hàng",
        flag: "Phần mềm thường dừng ở đây: số dư lệch, không biết lệch ở đâu",
        chain: [3, 4, 5],
        nodes: [
          { id: "stc", col: 1, y: 340, t: "Sổ thu chi", res: "thu 182,4 triệu · chi 141,9 triệu" },
          { id: "ske", col: 2, y: 170, parent: "stc", t: "Sao kê 2 tài khoản", res: "3 khoản tiền vào không có phiếu" },
          { id: "ct", col: 2, y: 480, parent: "stc", t: "Chứng từ", res: "4 khoản chi chưa có hoá đơn" },
          { id: "h1", col: 3, y: 100, parent: "ske", hyp: true, t: "Là tiền bán hàng?", res: "2 khoản từ khách quen, khớp sổ tay" },
          { id: "h2", col: 3, y: 260, parent: "ske", hyp: true, rej: true, t: "Cả 3 là tiền bán?", res: "20 triệu từ tài khoản cá nhân của chị" },
          { id: "tt", col: 3, y: 480, parent: "ct", t: "Tự tính", res: "lãi tạm tính 40.500.000 đ" },
        ],
        say: [
          "Cuối tháng 11. Đối chiếu sổ thu chi với sao kê hai tài khoản.",
          "Ba khoản tiền vào chưa có phiếu; bốn khoản chi chưa có hoá đơn.",
          "Hai khoản từ khách quen — là tiền bán hàng. Khoản 20 triệu từ tài khoản cá nhân của chị — không phải doanh thu. Thu trừ chi: lãi tạm tính 40,5 triệu.",
          "Lập bảng tổng hợp, nhắc hoá đơn — làm ngay. Gửi sổ cho văn phòng kế toán cần chị bấm. Khoản 20 triệu ghi thế nào: hỏi chị.",
        ],
        actions: [
          { from: "h1", kind: "auto", title: "Ghi 2 khoản vào doanh thu tháng 11", why: "khớp đơn khách quen trong sổ tay của chị", rule: "được tự ghi khi khoản tiền khớp đúng một đơn" },
          { from: "h2", kind: "ask", title: "20 triệu từ tài khoản cá nhân", why: "chị góp thêm vốn, hay cho cửa hàng vay?", opts: ["Góp thêm vốn", "Cho cửa hàng vay"] },
          { from: "tt", kind: "auto", title: "Lập bảng tổng hợp, nhắc 4 hoá đơn", why: "thu chi theo nhóm hàng; liệt kê hoá đơn còn thiếu", rule: "báo cáo và nhắc việc, không đổi dữ liệu" },
          { from: "tt", kind: "wait", who: "chủ cửa hàng", title: "Gửi sổ cho văn phòng kế toán", why: "đã đối chiếu sao kê; hoá đơn sẽ bổ sung sau", rule: "gửi dữ liệu ra ngoài cần chủ đồng ý" },
        ],
      },
    ],
  },
];
