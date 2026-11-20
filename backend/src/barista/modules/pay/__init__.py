"""LƯƠNG & THÙ LAO (0150 lát 1 "Trả theo công") — năng lực chung, không biết ngành nào.

* SỔ CÔNG chỉ thêm (``pay.work_entries``): nguồn đẩy công vào bằng MỘT khe ``pay.record_work`` (hợp đồng quyền hệ thống, khoá của nguồn) — gói nào
  tắt thì bên gọi nhận None; ngoài ra nhập Excel (``pay.import_work``) và khoản nhập tay qua duyệt (``pay.request_entry``). Nguồn quyết "thế nào là một
  đơn vị công"; gói này chỉ biết tiền.
* KHOẢN LƯƠNG có phiên bản + MỨC có hiệu lực theo ngày (``policy.py``): máy tính chọn từ tập đóng, không ô công thức.
* KỲ nháp → gửi duyệt (chuỗi ``pay_run``) → chốt = ghi nghĩa vụ từng phiếu qua ``acct.recognize_payable`` (``runs.py``); chi qua lô đề nghị chi của
  Tài chính. Phiếu tính bằng hàm THUẦN (``engine.py``) trên bản chụp đầu vào lưu cùng phiếu.
* LUẬT: bộ quy định ``vn-payroll``; quy tắc chưa xác minh không bao giờ tính ở kỳ thật — chỉ kỳ chạy bóng (không chốt, không ghi sổ, người lao động không
  thấy).
* QUYỀN: phiếu (tiền) là tài nguyên ``pay.slips`` riêng — mỗi người thấy phiếu đã chốt của mình, phụ trách lương thấy hết, kế toán đọc; trưởng đội
  không thấy tiền. Trợ lý chỉ soạn kỳ nháp, giải thích, báo bất thường.
"""

from barista.api import InfoGroup, Module, Pack, Role, ledger, regulations
from barista.modules.pay import engine

UNITS = {"session": "Buổi", "hour": "Giờ", "shift": "Ca", "day": "Ngày", "piece": "Lượt / sản phẩm"}
RATE_UNITS = {**UNITS, "month": "Tháng (mức cố định của một người)"}
CALCULATORS = {"rate_x_qty": "Công × mức", "from_source": "Tiền nguồn đã tính", "fixed": "Cố định theo tháng (mức của người)",
               "prorata": "Theo ngày công (mức tháng × ngày có lương / ngày công chuẩn)", "overtime": "Làm thêm giờ (giờ × đơn giá giờ × hệ số luật)",
               "manual": "Nhập tay có duyệt", "advance_offset": "Cấn tiền ứng đang mở"}
COMPONENT_KIND = {"earning": "Thu nhập", "deduction": "Khấu trừ"}
RUN_STATUS = {"draft": "Nháp", "submitted": "Chờ duyệt", "locked": "Đã chốt"}
RUN_KIND = {"regular": "Kỳ thật", "shadow": "Chạy bóng (chỉ so số)", "offcycle": "Ngoài kỳ", "correction": "Sửa sai"}
LINE_KIND = {"earning": "Thu nhập", "deduction": "Khấu trừ", "withholding": "Khấu trừ theo luật", "offset": "Cấn ứng",
             "employer": "Doanh nghiệp đóng"}
LAW = "vn-payroll"
regulations.declare(LAW, required=engine.RULE_CODES, open_kind=engine.CONTRIBUTION)   # hợp đồng mã (0154): mã lạ / thiếu mã bắt buộc chặn lúc nạp

Pack(
    "pay",
    "Lương & thù lao",
    "Sổ công từ nguồn (buổi đã xác nhận, hoa hồng đã mở, nhập Excel), mức trả theo ngày hiệu lực, kỳ lương nháp → duyệt → chốt ghi sổ, phiếu "
    "lương từng người, bảo hiểm và thuế thu nhập theo quy tắc luật đã xác minh",
    kind="capability",
    requires=("directory", "finance", "hr"),
    info_groups=(InfoGroup("payslips", "Lương từng người", "pay.slips", "Phiếu lương và số tiền lương của từng người trong kỳ lương"),),   # 0239 phần 3
    roles={
        "payroll": Role(
            "Phụ trách lương", ai_tier="manager",
            home=("pay", ("pay_runs", "pay_entries")),
            picker=("Nhân sự", "Phụ trách lương: sổ công, mức trả, lập kỳ lương và gửi kế toán duyệt — không tự duyệt, không tự chi.",
                    "Kỳ lương tháng này còn ai chưa tính được?"),
            grants=(("pay", "all", "read write manage"), ("pay.slips", "all", "read write manage"), ("pay.policy", "all", "read write manage"),
                    ("hr.terms", "all", "read write manage"), ("parties", "all", "read"), ("hr", "all", "read")),
        ),
    },
    # mỗi người: dòng công + phiếu đã chốt của CHÍNH mình (vai có sẵn của công ty); kế toán đọc để duyệt chốt và chi; trưởng đội chỉ phiếu của mình
    grants=(("accountant", "pay", "all", "read"), ("accountant", "pay.slips", "all", "read"), ("accountant", "pay.policy", "all", "read"),
            ("staff", "pay", "own", "read"), ("staff", "pay.slips", "own", "read"),
            ("sale", "pay", "own", "read"), ("sale", "pay.slips", "own", "read"),
            ("team_lead", "pay", "own", "read"), ("team_lead", "pay.slips", "own", "read")),
    chains=(("pay_run", "Chốt kỳ lương", ({"kind": "role", "role": "accountant"},)),
            ("pay_entry", "Khoản lương nhập tay", ({"kind": "role", "role": "accountant"},))),
    rules="""- LƯƠNG & THÙ LAO: trợ lý chỉ SOẠN kỳ nháp (pay__draft_run, pay__compute_run), GIẢI THÍCH phiếu (pay__slip — số nằm trên thẻ, đọc từ
  bản chụp đầu vào và mã quy tắc từng dòng; "vì sao lương tháng này thấp / cao hơn" ⇒ pay__compare_slips, nói theo các khoản chênh và dữ kiện trên
  thẻ: ngày công, ngày không lương, giờ làm thêm, người phụ thuộc) và BÁO BẤT THƯỜNG (pay__anomalies). KHÔNG gửi duyệt, không chốt, không nhập tay khoản lương, không đổi
  mức / khoản / loại hợp đồng, không đề nghị chi. Phiếu "dừng" vì quy tắc chưa xác minh ⇒ nói rõ "chờ kế toán xác minh", không ước số thay.
""",
)

m = Module("pay", "Lương & thù lao", pack="pay",
           resources={"pay": "Kỳ lương & sổ công", "pay.slips": "Phiếu lương (tiền từng người)", "pay.policy": "Khoản lương & mức trả"},
           setup=("pay.policy",))
# Nợ phải trả GỘP của mọi phiếu lương (review 09/11): sổ cái không mở tài khoản theo tên người, nên người đọc sổ cái (giám đốc) không thấy lương từng người;
# chi tiết từng người ở nghĩa vụ đánh dấu mật "pay.slips" (acct) và ở phiếu (RLS của gói này).
ledger.declare({"expense:payroll": ("Chi phí lương & thù lao", "expense"), "payable:payroll": ("Phải trả lương & thù lao", "liability"),
                # lát 3: phần người lao động đóng giữ lại trên phiếu (nộp cơ quan bảo hiểm / công đoàn); phần doanh nghiệp đóng là chi phí — ghi bút toán
                # phần doanh nghiệp đóng cần lệnh của Tài chính (chưa có: số nằm ở dòng ``employer`` của phiếu)
                "payable:insurance": ("Phải trả bảo hiểm bắt buộc (BHXH, BHYT, BHTN)", "liability"),
                "payable:union": ("Phải trả kinh phí / đoàn phí công đoàn", "liability"),
                "expense:insurance": ("Chi phí bảo hiểm bắt buộc doanh nghiệp đóng", "expense"),
                "expense:union": ("Chi phí kinh phí công đoàn", "expense")})
PAYABLE = "payable:payroll"

from barista.modules.pay import cashflow, policy, runs, screens, work  # noqa: E402,F401 — đăng ký thao tác của gói
