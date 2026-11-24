"""TÀI CHÍNH dùng chung mọi ngành: khoản phải thu (gói ngành lập — 0057 / 0079), tiền vào (một giao dịch ngân hàng = một dòng) ⇒ phân bổ ⇒
ghi sổ theo bản chất tiền, đối soát sao kê, lệnh chi trong công ty + chi trả thu hộ qua duyệt, dòng tiền dự phóng (gói góp dòng), sổ cái,
xuất bút toán theo mẫu nhập MISA. Module này KHÔNG biết ngành nào: phí CĐT, hoa hồng, tiền thuê… là nguồn (``source``) của khoản phải thu /
lệnh chi; gói nguồn nghe ``acct.payment_received`` / ``acct.payout_transferred`` để phản ứng.

Mọi đồng tiền ghi sổ cái kép (kernel/ledger). Tích hợp MISA / ngân hàng: xuất file (bước 1); đẩy qua API khi khách chốt gói MISA (bước 2).

File này CHỈ khai gói / module / tài khoản nền / cò / màn và nạp các file nghiệp vụ (mỗi file một khối của ``docs/MONEY-INVARIANTS.md``):
``common`` (phần dùng chung — không import file nào của package) · ``receivables`` (khoản phải thu) · ``cash_in`` (dòng tiền vào, phân bổ, phiếu
thu, phí thu hộ) · ``reconcile`` (đối soát sao kê) · ``payouts`` (lệnh chi, chi trả thu hộ) · ``reports`` (dòng tiền, số dư, xuất MISA) ·
``integrity`` (bất biến tiền trên dữ liệu thật, chặn tắt gói) · ``ledger_read`` (hợp đồng đọc sổ cho gói) · và các file theo nghiệp vụ còn lại.
"""

from barista.api import Action as A
from barista.api import Desk, Hints, Module, Pack, Role, Screen, Workspace
from barista.kernel import ledger

# Gói 'finance' tự khai (0069) — kernel không biết tên gói; vai / đội / mẫu mặc định xem presets, màn & "cần chú ý" khai ngay dưới.
Pack("finance", "Tài chính",
     "Sổ cái, sao kê & đối soát tiền về, dòng tiền, xuất MISA, việc trợ lý theo dõi — dùng chung mọi ngành",
     roles={
         "accountant": Role("Kế toán", ai_tier="manager",
                            tools=("acct.receivables", "acct.receivable_detail", "acct.cashflow", "acct.reconcile", "acct.unapplied", "acct.cash_brief",
                                   "acct.cash_moves", "acct.payouts", "acct.remittances", "acct.payout_checks", "acct.out_lines", "acct.payables",
                                   "acct.misa_export", "acct.vat_summary", "acct.pnl", "acct.balances", "acct.expenses", "acct.apply_statement",
                                   "acct.apply_line", "acct.record_payment", "acct.confirm_payout", "flow.tasks",
                                   "acct.dossiers", "acct.dossier"),   # 0218; 0223 hồ sơ khoản lệch
                            home=("accounting", ("receivables", "cashflow", "reconcile", "cash_brief", "payout_checks", "flow_board", "payouts")),
                            picker=("Chức năng", "Khoản phải thu, tiền về & đối soát, lệnh chi, dòng tiền, xuất MISA.", "Dòng tiền 4 tháng tới có tháng nào âm không?"),
                            grants=(("ledger", "all", "read write"), ("acct", "all", "read write manage"),
                                    ("kb.common", "all", "read write"),
                                    # 0251: kế toán ban hành đề nghị thanh toán / đối chiếu / tạm ứng (giấy hành chính do gói lập)
                                    ("papers", "all", "read write"), ("papers.office", "all", "read write manage"), ("work", "all", "read write"),
                                    ("flow", "all", "read write"), ("flow.templates", "all", "read"),
                                    ("talk", "all", "read write"), ("training", "own", "read write"), ("books", "all", "read write"),
                                    ("books.setup", "all", "read write"),),
                            suggest=(("acct.payouts", "Lệnh chi nào đang chờ chuyển khoản?"),
                                     ("acct.cashflow", "Dòng tiền 4 tháng tới thế nào?"),
                                     ("acct.misa_export", "Xuất bút toán tháng này để nhập MISA"))),
     },
     hints={"director": Hints(suggest=(("acct.cashflow", "Dòng tiền 4 tháng tới có tháng nào âm không?"),))},
     tools={"director": ("acct.cashflow", "acct.receivables", "acct.pnl")},   # 0218
     grants=(("director", "acct", "all", "read"),),   # việc theo dõi ("work") là năng lực nền — quyền của quản trị / giám đốc khai ở gói nền
     teams=(("KT", "Kế toán", "accounting"),),
     chains=(("settlement_payment", "Chi nghĩa vụ", ({"kind": "role", "role": "director"},)),
             ("remittance", "Chi trả tiền thu hộ / hoàn cọc", ({"kind": "role", "role": "director"},)),
             ("period_reopen", "Mở lại kỳ kế toán đã khoá", ({"kind": "role", "role": "director"},)),
             # 0156: sửa khoản phải thu / đảo phiếu thu là tiền đi ngược — người khác duyệt (người xin không tự duyệt)
             ("receivable_correction", "Sửa khoản phải thu / đảo phiếu thu", ({"kind": "role", "role": "director"},)),),
     approval_rules=(("acct.credit_receivable", "receivable_correction"), ("acct.reverse_receipt", "receivable_correction"),
                     # 0165: huỷ / điều chỉnh giảm hoá đơn GTGT cũng là tiền đi ngược — cùng chuỗi
                     ("acct.cancel_vat_invoice", "receivable_correction"), ("acct.adjust_vat_invoice", "receivable_correction"),
                     # 0170: huỷ phiếu chi phí (đảo bút toán chi / phải trả bên bán) — cùng chuỗi sửa tiền
                     ("acct.cancel_expense", "receivable_correction"),
                     # 0183: chuyển pháp nhân của phiếu chi phí = đảo + ghi lại — cùng chuỗi sửa tiền
                     ("acct.transfer_expense_entity", "receivable_correction"),
                     # 0223: xoá phần thiếu của khoản lệch = giảm phải thu qua giấy báo có — cùng chuỗi, người duyệt khác người xin
                     ("acct.write_off_dossier", "receivable_correction")),
     kind="capability", requires=("directory",),
     rules="""- ĐỐI SOÁT TIỀN VỀ: ảnh / PDF SAO KÊ TÀI KHOẢN CỦA CÔNG TY ⇒ ĐỀ XUẤT acct__add_statement_line cho TỪNG dòng tiền VÀO (người xác nhận; source="image"; ngày, số tiền,
  nội dung chuyển khoản, bên chuyển, mã giao dịch chép đúng như ảnh — ô không đọc rõ để trống, KHÔNG đoán), rồi acct__reconcile để code khớp
  với khoản phải thu. Dòng khớp đủ ⇒ đề xuất acct__apply_statement (một lần bấm); dòng trả thiếu / thừa / chưa khớp ⇒ nói số chênh + gợi ý trên
  thẻ, để kế toán chọn từng dòng (acct__apply_line). Ảnh uỷ nhiệm chi / bảng đối soát do BÊN TRẢ gửi KHÔNG phải sao kê của công ty: chỉ đối
  chiếu với khoản phải thu (acct__receivables) và nói khớp / lệch — không nạp, không ghi thu.
- TIỀN THEO BẢN CHẤT: khoản phải thu (acct__receivables) ghi rõ doanh thu / THU HỘ / tiền cọc. Tiền thu hộ (tiền nhà của chủ nhà…) và tiền cọc
  KHÔNG phải doanh thu của công ty — nói doanh thu thì chỉ tính phần phí. Khách hỏi chuyển khoản ⇒ nhắc ghi MÃ THANH TOÁN (TT…) của khoản đó.
""")

# Nhóm màn trên menu Kế toán (Screen.group) — theo nhịp việc kế toán; gói góp màn dùng đúng các chữ này để màn của gói vào cùng nhóm
G_IN, G_AR, G_OUT = "Tiền về & đối soát", "Phải thu", "Chi & phải trả"
G_BOOK, G_REPORT, G_SETUP = "Sổ sách & thuế", "Báo cáo & kiểm soát", "Thiết lập"

m = Module("acct", "Kế toán", pack="finance", resources={"acct": "Kế toán (khoản phải thu, tiền về, chi, dòng tiền)"})
# NGHIỆP VỤ CÔNG TY CÓ DÙNG (api 1.63, ``Screen(feature=)``): công ty không làm việc này thì tắt ⇒ màn không lên menu của vai nào. Mặc định BẬT.
USE_REMITTANCES = m.setting("use_remittances", "Công ty có thu hộ rồi chi trả lại", default=True,
                            help="Bật: Kế toán có màn Chi trả thu hộ (tiền thu hộ bên khác, chờ chuyển trả). Tắt: ẩn với mọi vai (dữ liệu đã có giữ nguyên, "
                                 "bật lại là thấy).")
USE_COST_OBJECTS = m.setting("use_cost_objects", "Công ty tập hợp chi phí theo công trình / hợp đồng", default=True,
                             help="Bật: Kế toán có màn Đối tượng tập hợp chi phí (gom chi phí theo từng công trình, hợp đồng, đơn hàng). Tắt: ẩn với mọi "
                                  "vai (dữ liệu đã có giữ nguyên, bật lại là thấy).")
m.consumes("plug")   # cảnh báo chi (acct.payout_flags) hỏi gói nguồn bằng quyền hệ thống — không trống vì người xin thiếu quyền đọc nguồn
# Tài khoản của Tài chính dùng chung (mọi ngành). Gói ngành khai tài khoản riêng của mình (vd phải thu CĐT / doanh thu phí môi giới — gói Môi
# giới) và chọn khi lập khoản phải thu. held:<người nhận>, deposit:<khoản cọc> tạo theo đối tượng lúc ghi (ledger.ensure_account).
ledger.declare({
    "cash:bank": ("Tiền gửi ngân hàng", "asset"),
    "payout:clearing": ("Phải trả — chờ chuyển khoản", "liability"),
    "revenue:advance_charge": ("Phí ứng theo chính sách", "revenue"),
    "tax:withheld": ("Thuế đã khấu trừ phải nộp", "liability"),
    "unapplied:bank": ("Tiền về chưa phân bổ", "liability"),
    "receivable:customers": ("Phải thu khách hàng", "asset"),
    "revenue:services": ("Doanh thu dịch vụ", "revenue"),
    "equity:opening": ("Số dư đầu kỳ (đối ứng)", "equity"),
    "revenue:opening_adjustment": ("Điều chỉnh giảm phải thu đầu kỳ", "revenue"),
    # 0156: tài khoản chung cho danh mục khoản thu / chi mặc định (công ty khai danh mục riêng, mỗi mục chọn một tài khoản đã khai)
    "revenue:other_income": ("Thu nhập khác", "revenue"),
    "expense:bonus": ("Chi thưởng", "expense"),
    "expense:marketing": ("Chi phí marketing", "expense"),
    "expense:other": ("Chi phí khác", "expense"),   # 0119: huỷ khoản phải thu đầu kỳ ghi vào kỳ hiện tại   # 0119: đối ứng của mọi dòng đầu kỳ; ánh xạ sang hệ thống tài khoản khách: còn mở
}, mechanism=("revenue:advance_charge", "revenue:opening_adjustment"))   # 0189: tài khoản cơ chế — không vào danh mục khoản thu / chi
m.trigger("acct.receivable_overdue", "Khoản phải thu quá hạn", {"code": "Mã thanh toán", "label": "Nội dung", "rest": "Còn phải thu (đồng)"},
          open=("acct.receivables", {}), owner="owner_id")
m.trigger("acct.payment_received", "Có tiền về", {"code": "Mã thanh toán", "payment_vnd": "Số tiền vừa thu (đồng)", "amount_vnd": "Phải thu (đồng)"},
          open=("acct.receivables", {}))

# Khối nghiệp vụ của Tài chính — mỗi file một khối của docs/MONEY-INVARIANTS.md; nạp TRƯỚC khi khai màn (màn trỏ thao tác của chúng)
from barista.modules.acct import cash_in, integrity, payouts, receivables, reconcile, reports  # noqa: E402, F401 — đăng ký thao tác
from barista.modules.acct.reports import _say_cashflow  # noqa: E402

# ---- Màn hình (0069): module tự khai phân hệ / góp màn vào phân hệ khác; workspaces.py chỉ ráp và lọc theo quyền
m.workspace(Workspace("accounting", "Kế toán", "banknote.fill", (
    Screen("reconcile", "Đối soát sao kê", "arrow.left.arrow.right.square.fill", "acct.reconcile", tint="purple", min_scope="all", group=G_IN,
           about="Ghép từng khoản tiền về trong sao kê ngân hàng với khoản phải thu: dòng khớp đủ ghi một lần, dòng lệch có gợi ý để bạn quyết.",
           # "Ghi các dòng khớp đủ" không còn trên thanh công cụ: màn tự đưa nút vào khối đầu bảng khi có dòng khớp đủ (reconcile.py)
           actions=(A("acct.add_statement_line", "Nạp sao kê", style="primary"), A("acct.record_unapplied", "Tiền về chưa có khoản")),
           row_actions=(A("acct.apply_line", "Ghi phiếu thu", bind={"line_id": "id"}, prefill={"code": "code"},
                          when={"state": ["full", "short", "over", "amount_only", "unmatched"]}, style="primary"),
                        A("acct.ignore_line", "Bỏ qua", bind={"line_id": "id"}))),
    Screen("receivables", "Khoản phải thu", "list.bullet.rectangle.portrait.fill", "acct.receivables", tint="teal", min_scope="all", group=G_AR,
           row_actions=(A("acct.record_payment", "Ghi thu", bind={"code": "code"}, when={"status": ["open", "partial"]}, style="primary"),
                        A("acct.credit_receivable", "Giảm phải thu", bind={"code": "code"}, when={"status": ["open", "partial", "paid"]}),
                        A("acct.reverse_receipt", "Đảo phiếu thu", bind={"code": "code"}, when={"status": ["partial", "paid"]}),
                        A("acct.issue_vat_invoice", "Ghi hoá đơn GTGT", bind={"code": "code"}, when={"vat": ["ready"]}))),
), order=70))
# Menu Kế toán theo nhịp việc (rà vai kế toán 18/11/2026): tiền về & đối soát → phải thu → chi & phải trả → sổ sách & thuế → báo cáo → thiết lập.
# Thứ tự nhóm trên menu = thứ tự màn ĐẦU TIÊN của nhóm (app vẽ theo thứ tự xuất hiện) ⇒ màn thiết lập / báo cáo khai ở hạng cao (90 / 70).
m.screens("accounting", (
    Screen("unapplied", "Tiền về chưa phân bổ", "tray.and.arrow.down", "acct.unapplied", tint="orange", min_scope="all", group=G_IN),
    Screen("payables", "Phải trả & ứng", "banknote", "acct.payables", tint="orange", min_scope="all", group=G_OUT,
           row_actions=(A("acct.request_payment", "Đề nghị chi", bind={"payable_id": "id"}, when={"kind": ["payable"], "open": ["yes"]}, style="primary"),
                        A("acct.recover_payment", "Ghi nhận hoàn tiền", bind={"payable_id": "id"}, when={"kind": ["advance", "recovery"], "open": ["yes"]}))),
    Screen("payouts", "Lệnh chi", "arrow.up.right.square.fill", "acct.payouts", tint="orange", group=G_OUT,
           row_actions=(A("acct.confirm_payout", "Đã chuyển khoản", bind={"payout_id": "id"}, when={"status": ["pending_transfer", "unknown"]},
                          style="primary"),
                        A("acct.mark_payout_unknown", "Cần tra soát", bind={"payout_id": "id"}, when={"status": ["pending_transfer"]}),
                        A("acct.cancel_payout", "Huỷ lệnh", bind={"payout_id": "id"}, when={"status": ["pending_transfer", "unknown"]}, style="destructive"))),
    Screen("remittances", "Chi trả thu hộ", "arrow.uturn.up.square", "acct.remittances", group=G_OUT, feature=USE_REMITTANCES,
           row_actions=(A("acct.confirm_remittance", "Đã chuyển khoản", bind={"remittance_id": "id"}, when={"status": ["pending_transfer"]},
                          style="primary"),)),
), rank=20)
m.screens("accounting", (
    Screen("cashflow", "Dòng tiền dự phóng", "chart.line.uptrend.xyaxis.circle.fill", "acct.cashflow", tint="green", group=G_REPORT,
           desk=Desk("decide", say=_say_cashflow, menu=True, rank=35)),
), rank=70)
m.screens("accounting", (
    Screen("balances", "Số dư sổ cái", "books.vertical", "acct.balances", group=G_BOOK),
    Screen("misa", "Xuất MISA", "square.and.arrow.up", "acct.misa_export", group=G_BOOK),
), rank=40)
m.screens("accounting", (
    Screen("account_map", "Ánh xạ tài khoản", "arrow.left.arrow.right", "acct.account_map", group=G_SETUP,
           actions=(A("acct.set_account_map", style="primary"),)),
    Screen("categories", "Danh mục khoản thu / chi", "list.bullet.indent", "acct.category_list", group=G_SETUP,
           actions=(A("acct.set_category", style="primary"),)),
    Screen("tax_categories", "Loại thuế GTGT", "tag", "acct.tax_category_list", min_scope="all", pinnable=False, group=G_SETUP,
           actions=(A("acct.set_tax_category", style="primary"),)),
), rank=90)
# 0165: thuế GTGT đầu ra — màn có sẵn, nhưng ghi được chỉ khi công ty bật acct.vat_enabled (mặc định tắt)
m.screens("accounting", (
    Screen("vat_invoices", "Hoá đơn GTGT", "doc.text", "acct.vat_invoices", min_scope="all", pinnable=False, group=G_BOOK,
           actions=(A("acct.issue_vat_invoice", "Ghi hoá đơn GTGT", style="primary"),),
           row_actions=(A("acct.adjust_vat_invoice", "Điều chỉnh giảm", bind={"code": "code"}, when={"state": ["active"]}),
                        A("acct.cancel_vat_invoice", "Huỷ hoá đơn", bind={"code": "code"}, when={"state": ["active"]}, style="destructive"))),
    Screen("vat_summary", "Tổng hợp thuế GTGT", "percent", "acct.vat_summary", min_scope="all", pinnable=False, group=G_BOOK),
), rank=45)


# Báo cáo quản trị (0166): dòng tiền dự phóng trong menu của ban giám đốc — cùng truy vấn
m.screens("mgmt", (Screen("mgmt_cashflow", "Dòng tiền dự phóng", "chart.line.uptrend.xyaxis", "acct.cashflow", group="Dòng tiền & công nợ", tint="green", min_scope="all"),), rank=20)


from barista.modules.acct import (  # noqa: E402, F401 — registers bank accounts / cash in-out, payables, period locks, corrections, VAT, expenses, money placed with counterparties (0182), goods books (0217), money assurance over discrepancy dossiers (0228 §D)
    assurance,
    bank,
    corrections,
    dossier,
    expenses,
    goods,
    ledger_read,
    periods,
    placements,
    settlements,
    vat,
)
