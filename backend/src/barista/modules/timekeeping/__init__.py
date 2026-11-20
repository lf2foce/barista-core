"""CHẤM CÔNG (0150 lát 4) — năng lực chung, không biết ngành nào; dùng được riêng (không cần gói Lương).

* GIỜ CHẤM là sự thật, chỉ thêm (``timekeeping.punches``). Luật chấm theo ĐỊA ĐIỂM là dữ liệu (``timekeeping.sites``): dải IP công khai của văn phòng
  (mặc định), tuỳ chọn vùng toạ độ. Trượt luật ⇒ VẪN GHI, gắn cờ cho quản lý xét (nhận / bỏ) — từ chối thì mất sự thật (quên chấm, mạng lạ). IP do
  máy chủ đọc ở đường HTTP riêng của gói (``POST /v1/timekeeping/punch``), không bao giờ do người chấm khai.
* GIỜ LÀM cố định theo công ty (``patterns``) + mỗi người một cách tính công (``enrolments``: theo giờ chấm / đủ công trừ ngoại lệ).
* BẢNG CÔNG là phép tính THUẦN (``sheet.py``), không lưu theo ngày. CHỐT theo tháng (một đội hoặc cả công ty) ⇒ tổng mỗi người thành DÒNG CÔNG của
  Lương qua ``pay.record_work`` (ngày có lương / không lương, giờ làm thêm theo hệ số) — gói Lương tắt ⇒ bỏ qua. Người lao động xác nhận / phản hồi.
* NGHỈ PHÉP: loại nghỉ (dữ liệu), đơn qua chuỗi ``leave``, sổ phép chỉ thêm (số dư = tổng dòng). LÀM THÊM: người lao động tự xin (đồng ý) + quản lý
  duyệt (``overtime``), trần ngày / tháng / năm là quy định ``vn-labour``. Quên chấm: xin bổ sung giờ (``time_correction``).
* QUYỀN: mỗi người thấy giờ / phép / bảng công của mình; trưởng đội thấy của đội (KHÔNG thấy lương — tài nguyên khác); Nhân sự cả công ty.
  Trợ lý: trả lời số trên thẻ, SOẠN đơn nghỉ / làm thêm / bổ sung giờ / xác nhận bảng công cho chính người hỏi (thẻ đề xuất người bấm) — không chấm thay,
  không duyệt, không xét giờ chấm, không chốt.
"""

import contextvars

from barista.api import Module, Pack, Role, regulations

LABOUR = "vn-labour"
# hợp đồng mã với bộ ``vn-labour`` (0154): ngày lễ, trần làm thêm, phép năm đọc theo tên ở ``common.py`` / ``leave.py``
regulations.declare(LABOUR, required=("PUBLIC_HOLIDAYS", "OT_CAPS", "ANNUAL_LEAVE"))
MODES = {"punch": "Theo giờ chấm vào / ra", "exception": "Đủ công theo lịch, chỉ trừ nghỉ / vắng", "off": "Không chấm công"}
DIRECTION = {"in": "Vào ca", "out": "Ra ca"}
METHOD = {"web": "Trên app", "import": "Nhập từ máy chấm / tệp", "correction": "Bổ sung có duyệt"}
FLAGS = {"ip_outside": "Ngoài mạng văn phòng", "ip_unknown": "Không đọc được địa chỉ mạng", "no_site": "Chưa gán địa điểm", "not_enrolled": "Chưa đăng ký chấm công",
         "repeat": "Chấm lặp trong 2 phút"}
PAID_BY = {"company": "Công ty trả lương", "insurance": "Quỹ bảo hiểm trả (công ty không trả)", "none": "Không lương"}
REQUEST_STATUS = {"requested": "Chờ duyệt", "approved": "Đã duyệt", "rejected": "Bị từ chối", "cancelled": "Đã huỷ"}
# Địa chỉ mạng của lần chấm do ĐƯỜNG HTTP của gói đọc từ kết nối (sau proxy tin cậy của bản cài) rồi đặt vào đây trước khi gọi lệnh qua executor —
# lệnh không có ô IP nào để người chấm tự khai. Gọi lệnh qua đường lệnh chung ⇒ không có IP ⇒ lần chấm gắn cờ "không đọc được địa chỉ mạng".
OBSERVED_IP: contextvars.ContextVar[str | None] = contextvars.ContextVar("timekeeping_observed_ip", default=None)

Pack(
    "timekeeping",
    "Chấm công",
    "Chấm công vào / ra kiểm theo IP văn phòng (gắn cờ, không từ chối), giờ làm cố định, bảng công tính từ giờ chấm, nghỉ phép có sổ phép, làm thêm "
    "giờ có duyệt và trần, ngày lễ theo luật lao động, chốt bảng công theo tháng và người lao động xác nhận — đẩy công đã chốt sang Lương",
    kind="capability",
    requires=("hr",),
    roles={
        "hr_officer": Role(
            "Nhân sự", ai_tier="manager",
            grants=(("timekeeping", "all", "read write manage"), ("timekeeping.policy", "all", "read write manage"), ("hr", "all", "read write manage"),
                    ("parties", "all", "read")),
            home=("timekeeping", ("tk_flagged", "tk_timesheet")),
            picker=("Nhân sự", "Nhân sự: địa điểm và giờ làm, giờ chấm cần xét, nghỉ phép, chốt bảng công cả công ty — không thấy lương nếu không được cấp.",
                    "Ai chấm công ngoài văn phòng tuần này?"),
        ),
    },
    # mỗi người: chấm, xin nghỉ / làm thêm / bổ sung giờ, xác nhận bảng công của CHÍNH mình; trưởng đội: xét + duyệt + chốt của đội (không thấy lương)
    grants=(("office", "timekeeping", "all", "read write manage"), ("office", "timekeeping.policy", "all", "read write manage"),
            ("admin", "timekeeping.policy", "all", "read write manage"), ("admin", "timekeeping", "all", "read"),
            ("director", "timekeeping", "all", "read"), ("accountant", "timekeeping", "all", "read"), ("payroll", "timekeeping", "all", "read"),
            ("team_lead", "timekeeping", "team", "read write manage"),
            ("staff", "timekeeping", "own", "read write"), ("sale", "timekeeping", "own", "read write")),
    chains=(("leave", "Đơn nghỉ", ({"kind": "team_lead"},)),
            ("overtime", "Làm thêm giờ", ({"kind": "team_lead"},)),
            ("time_correction", "Bổ sung giờ chấm", ({"kind": "team_lead"},))),
    rules="""- CHẤM CÔNG: số giờ chấm, đi muộn, ngày phép, giờ làm thêm nằm trên THẺ (timekeeping__my_month, timekeeping__flagged, timekeeping__timesheet) — đọc
  từ thẻ, không tự cộng. Xin nghỉ: TRA loại nghỉ (timekeeping__leave_types) và phép còn lại (timekeeping__my_month) TRƯỚC khi soạn đơn — chọn đúng mã loại nghỉ
  theo lý do người hỏi nói (ốm, việc riêng, phép năm…), không mặc định phép năm. Trợ lý chỉ SOẠN cho CHÍNH người hỏi: đơn nghỉ (timekeeping__request_leave), làm thêm (timekeeping__request_overtime), bổ
  sung giờ quên chấm (timekeeping__request_correction), xác nhận / phản hồi bảng công (timekeeping__confirm_timesheet) — thành thẻ người bấm. KHÔNG
  chấm công thay, không xét giờ chấm có cờ, không duyệt đơn, không chốt bảng công, không cấp phép. Ngày tương đối ("thứ Sáu này") ⇒ đổi ra ngày cụ
  thể theo lịch hôm nay rồi mới soạn đơn.
""",
)

m = Module("timekeeping", "Chấm công", pack="timekeeping",
           resources={"timekeeping": "Chấm công, nghỉ phép, làm thêm, bảng công", "timekeeping.policy": "Địa điểm, giờ làm, loại nghỉ"},
           setup=("timekeeping.policy",))

from barista.modules.timekeeping import closing, leave, policy, punches, screens  # noqa: E402,F401 — đăng ký thao tác của gói
