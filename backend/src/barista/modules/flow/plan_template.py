"""MẪU EXCEL "Nhập kế hoạch" (27/11/2026) — thay mẫu tự sinh (12 cột, một dòng ví dụ một ô) bằng một kế hoạch mẫu TRỌN VẸN để người điền — và trợ lý AI
đọc file — thấy ngay cách viết: dòng nhóm = giai đoạn, mã WBS 1 / 1.1, người phụ trách, số ngày làm việc, việc trước, % hoàn thành, mốc 0 ngày.

* Chỉ các cột người hay dùng; tên cột ĐÚNG nhãn trường của lệnh nhập ⇒ máy tự khớp chắc chắn. Cột kỹ thuật của MS Project (ID, Cấp) vẫn nhận khi nhập file
  MS Project, chỉ không có trong mẫu.
* Dòng 1 = tên dự án (máy lấy làm tên kế hoạch khi nhập). Trang "Hướng dẫn" là câu chữ thường, KHÔNG phải bảng (máy nhập không nhầm là trang dữ liệu).
* Ngày ví dụ tính từ thứ Hai tới ⇒ mẫu tải hôm nào cũng là ngày sắp tới. Tên người là giả — người điền thay bằng người trong công ty.
"""
from datetime import date, timedelta
from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation

from barista.api import today

HEADERS = ("STT", "Công việc", "Người phụ trách", "Bắt đầu", "Số ngày", "Kết thúc / Hạn", "Việc trước", "% hoàn thành", "Ghi chú")
WIDTHS = (7, 42, 22, 13, 9, 15, 13, 13, 40)
TITLE = "Dự án mẫu — sửa dòng này thành tên dự án của anh/chị"

GUIDE = (
    "CÁCH ĐIỀN KẾ HOẠCH",
    "",
    "1. Dòng 1 của trang “Kế hoạch” là tên dự án — khi nhập thành tên kế hoạch.",
    "2. Mỗi dòng một việc. Dòng chỉ có STT + tên (không người, không ngày) là GIAI ĐOẠN; các việc STT con bên dưới (1.1, 1.2…) thuộc giai đoạn đó.",
    "3. STT: 1, 1.1, 1.2, 2, 2.1… Giữ nguyên STT khi sửa file rồi nhập lại — máy cập nhật đúng việc cũ, không tạo trùng, không xoá việc file bỏ đi.",
    "4. Người phụ trách: tên, email hoặc tên viết tắt của người trong công ty. Để trống = chưa giao. Tên máy chưa nhận ra thì hỏi anh/chị chọn một lần.",
    "5. Ngày: chỉ cần ngày bắt đầu của việc đầu tiên. Các việc sau ghi Số ngày (ngày làm việc) + Việc trước — máy tự tính ngày, bỏ cuối tuần và ngày lễ.",
    "   Việc có hạn cố định thì ghi cột Kết thúc / Hạn. Ngày có sẵn trong file luôn được giữ nguyên.",
    "6. Việc trước: STT của việc phải xong trước. Nhiều việc cách nhau dấu ; (vd 2.1; 2.2). Chờ thêm vài ngày: 2.1+2d.",
    "7. Số ngày 0 = mốc (nghiệm thu, go-live…).",
    "8. % hoàn thành: 100 = đã xong, 30 = đang làm, để trống = chưa làm.",
    "9. Xoá các dòng mẫu rồi điền việc của mình. File Excel / MS Project sẵn có cũng nhập được — máy tự khớp cột, anh/chị chỉ xác nhận.",
)


def _next_monday() -> date:
    t = today()
    return t + timedelta(days=(7 - t.weekday()) % 7 or 7)


def _rows(start: date) -> list[tuple[Any, ...]]:
    """(STT, Công việc, Người phụ trách, Bắt đầu, Số ngày, Kết thúc / Hạn, Việc trước, % hoàn thành, Ghi chú) — dòng nhóm chỉ có STT + tên."""
    return [
        ("1", "Khởi động", None, None, None, None, None, None, None),
        ("1.1", "Họp khởi động, chốt phạm vi", "Nguyễn Văn A", start, 1, None, None, None, "Việc đầu tiên ghi ngày bắt đầu — các việc sau máy tự tính"),
        ("1.2", "Khảo sát hiện trạng", "Trần Thị B", None, 3, None, "1.1", None, None),
        ("2", "Thực hiện", None, None, None, None, None, None, None),
        ("2.1", "Chuẩn bị dữ liệu", "Trần Thị B", None, 5, None, "1.2", None, None),
        ("2.2", "Cấu hình theo quy trình đã chốt", "Lê Văn C", None, 5, None, "1.2", None, "Hai việc cùng chờ 1.2 ⇒ làm song song"),
        ("2.3", "Kiểm tra cùng người dùng", "Nguyễn Văn A", None, 3, None, "2.1; 2.2", None, "Chờ cả hai việc trước"),
        ("3", "Bàn giao", None, None, None, None, None, None, None),
        ("3.1", "Đào tạo người dùng", "Lê Văn C", None, 2, None, "2.3", None, None),
        ("3.2", "Nghiệm thu", "Nguyễn Văn A", None, 0, None, "3.1", None, "Số ngày 0 = mốc"),
    ]


def build() -> bytes:
    """Mẫu nhập kế hoạch (tải ở màn nhập: "Chưa có file? Tải mẫu Excel")."""
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Kế hoạch"
    ws["A1"] = TITLE
    ws["A1"].font = Font(bold=True, size=14)
    line = Side(style="thin", color="BFBFBF")
    box = Border(left=line, right=line, top=line, bottom=line)
    head_row = 3
    for j, (h, w) in enumerate(zip(HEADERS, WIDTHS, strict=True), 1):
        c = ws.cell(row=head_row, column=j, value=h)
        c.font, c.border = Font(bold=True), box
        c.fill = PatternFill("solid", fgColor="DDEBF7")
        c.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[c.column_letter].width = w
    group = PatternFill("solid", fgColor="F2F2F2")
    for i, row in enumerate(_rows(_next_monday()), head_row + 1):
        is_group = row[2] is None and row[4] is None
        for j, v in enumerate(row, 1):
            c = ws.cell(row=i, column=j, value=v)
            c.border = box
            if j == 4:
                c.number_format = "dd/mm/yyyy"
            if is_group:
                c.font, c.fill = Font(bold=True), group
            elif j == 9:
                c.font = Font(color="7F7F7F")
    last = head_row + 200
    days = DataValidation(type="whole", operator="greaterThanOrEqual", formula1="0", allow_blank=True,
                          error="Số ngày làm việc: số nguyên ≥ 0 (0 = mốc)", showErrorMessage=True)
    pct = DataValidation(type="whole", operator="between", formula1="0", formula2="100", allow_blank=True,
                         error="% hoàn thành: 0–100", showErrorMessage=True)
    ws.add_data_validation(days)
    ws.add_data_validation(pct)
    days.add(f"E{head_row + 1}:E{last}")
    pct.add(f"H{head_row + 1}:H{last}")
    ws.freeze_panes = f"C{head_row + 1}"
    guide = wb.create_sheet("Hướng dẫn")
    for k, text in enumerate(GUIDE, 1):
        guide.cell(row=k, column=1, value=text).font = Font(bold=k == 1, size=13 if k == 1 else 11)
    guide.column_dimensions["A"].width = 150
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
