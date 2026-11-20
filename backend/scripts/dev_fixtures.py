"""File Excel GIẢ để thử Nhập dữ liệu (runbook 18): ghi vào ``backend/dev-fixtures/`` (gitignore) — gọi từ ``dev_reset.sh`` sau khi nạp DB dev.

* ``khach-moi.xlsx``           — lead mới, tiêu đề đúng như ô của lệnh ``crm.create_lead`` (nhập thẳng được).
* ``khach-tu-file-cu.xlsx``     — lead với tiêu đề lộn xộn + SĐT nhiều dạng + vài dòng lỗi (thử cột tự khớp / báo lỗi từng dòng).
* ``ke-hoach-mo-ban.xlsx``      — kế hoạch dự án (STT, giai đoạn, việc trước, người phụ trách) cho Nhập kế hoạch vào Gantt.
* ``ngan-sach-nam-sau.xlsx``    — ngân sách năm sau: khoản mục × 12 tháng (``budget.set_line``).
* ``giao-dich.xlsx``            — giao dịch mới trên căn còn trống thật của DB (``deals.create``).
Mọi tên / SĐT là GIẢ (SĐT ``0100300001`` trở lên — đầu 010 không tồn tại ở VN); ngày tính theo hôm nay.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

import psycopg
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

OUT = Path(__file__).resolve().parent.parent / "dev-fixtures"
NAMES = ["Lê Thu Hà", "Phạm Minh Quân", "Đỗ Ngọc Anh", "Bùi Văn Tâm", "Vũ Thanh Hằng", "Ngô Gia Bảo", "Đặng Mỹ Linh", "Hồ Quốc Việt", "Dương Thị Nga",
         "Trịnh Hữu Phúc", "Mai Thu Trang", "Phan Đức Long", "Võ Bảo Ngọc", "Tạ Minh Khôi", "Lý Thanh Vân", "Nguyễn Hoàng Sơn", "Trần Thị Yến", "Hoàng Gia Huy",
         "Lương Ngọc Mai", "Châu Quốc Bình", "Kiều Thảo Nguyên", "Đinh Văn Hiếu", "Cao Phương Thảo", "La Đức Anh", "Quách Thị Hoa"]


def _sheet(name: str, header: list[str], rows: list[list[object]], widths: list[int] | None = None) -> None:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Dữ liệu"
    ws.append(header)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in rows:
        ws.append(r)
    for i, w in enumerate(widths or [], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    wb.save(OUT / name)


def main(owner_url: str) -> None:
    OUT.mkdir(exist_ok=True)
    t = date.today()
    d = lambda n: (t + timedelta(days=n)).strftime("%d/%m/%Y")  # noqa: E731
    phone = lambda i: f"0{100300001 + i}"  # noqa: E731
    projects = ["HSR", "DTP"]
    sources = ["ads", "self", "referral", "event", "telesales"]
    _sheet("khach-moi.xlsx", ["Họ tên khách", "SĐT", "Dự án quan tâm", "Nguồn", "Ngân sách", "Mong muốn", "Giao cho (email)"],
           [[NAMES[i], phone(i), projects[i % 2], sources[i % 5], f"{3 + i % 5} tỷ", ["cần tư vấn cuối tuần", "muốn xem căn 2PN", "hỏi chính sách vay", ""][i % 4],
             f"sale{1 + i % 4}@sanmau.demo"] for i in range(20)], [24, 14, 16, 12, 12, 28, 26])
    messy = [[NAMES[i], ["0100 300 " + f"{100 + i:03d}", "+84 100 300 " + f"{200 + i:03d}", f"0{100300101 + i}"][i % 3], ["Hồ Sen", "HSR", "Đồi Thông", "DTP"][i % 4],
              ["Facebook", "Giới thiệu", "Tự đến", "Sự kiện"][i % 4], [3.5, 4, 5.2, "4 tỷ", "", 6][i % 6], d(-(i % 9)), ["Gọi lại chiều", "", "Khách từ file cũ"][i % 3]] for i in range(18)]
    messy += [["", "0100300999", "HSR", "Facebook", 4, d(-2), "THIẾU TÊN — dòng này phải báo lỗi"], ["Khách sai số", "12345", "DTP", "Tự đến", 3, d(-1), "SĐT sai — dòng lỗi"]]
    _sheet("khach-tu-file-cu.xlsx", ["Tên KH", "Điện thoại", "Dự án", "Kênh", "Ngân sách (tỷ)", "Ngày nhận", "Ghi chú"], messy, [22, 18, 14, 14, 16, 12, 34])

    plan = [("1", "Chuẩn bị", "Chốt chính sách bán hàng với CĐT", "ketoan", -14, -7, ""), ("2", "Chuẩn bị", "Bộ nhận diện + tờ giới thiệu", "mkt2", -10, 0, "1"),
            ("3", "Chuẩn bị", "Landing page + form", "digital", -3, 6, "2"), ("4", "Truyền thông", "Chạy quảng cáo Facebook / Google", "mkt3", 4, 30, "3"),
            ("5", "Truyền thông", "Đào tạo đội kinh doanh", "truonga", 1, 8, "1"), ("6", "Bán hàng", "Sự kiện giới thiệu căn mẫu", "hcns", 9, 20, "5"),
            ("7", "Bán hàng", "Mở giữ chỗ khách VIP", "truongb", 21, 27, "6"), ("8", "Bán hàng", "Ký HĐMB đợt đầu", "salesadmin", 28, 45, "7"),
            ("9", "Nghiệm thu", "Đối chiếu công nợ phí với CĐT", "ketoan", 46, 55, "8")]
    _sheet("ke-hoach-mo-ban.xlsx", ["STT", "Giai đoạn", "Công việc", "Người phụ trách (email)", "Bắt đầu", "Hạn", "Việc trước (STT)"],
           [[a, b, c, f"{e}@sanmau.demo", d(s), d(h), p] for a, b, c, e, s, h, p in plan], [6, 16, 38, 26, 12, 12, 16])

    y = t.year + 1
    budget = [("Doanh thu phí môi giới", "", 700), ("Chi phí hoa hồng", "", 320), ("Chi phí marketing", "HSR", 70), ("Chi phí marketing", "DTP", 45), ("Thuê văn phòng", "", 36)]
    _sheet("ngan-sach-nam-sau.xlsx", ["Năm", "Khoản mục", "Đối tượng chi phí", *[f"T{i}" for i in range(1, 13)]],
           [[y, a, b, *[c * 1_000_000 + (i % 4) * 2_000_000 for i in range(12)]] for a, b, c in budget], [8, 26, 18, *[14] * 12])

    with psycopg.connect(owner_url) as conn:
        free = conn.execute("SELECT p.code, u.code FROM catalog.units u JOIN catalog.projects p ON p.id = u.project_id WHERE u.status = 'available' ORDER BY p.code, u.code DESC LIMIT 400").fetchall()
    by: dict[str, list[str]] = {}
    for p, c in free:
        by.setdefault(p, []).append(c)
    rows = []
    for i in range(8):
        p = projects[i % 2]
        if by.get(p):
            rows.append([p, by[p].pop(5 + i), NAMES[(i + 7) % len(NAMES)], phone(300 + i), f"sale{1 + i % 4}@sanmau.demo", d(-(i % 5))])
    _sheet("giao-dich.xlsx", ["Mã dự án", "Mã căn", "Tên khách", "SĐT khách", "Sale phụ trách (email)", "Ngày giữ chỗ"], rows, [12, 12, 24, 14, 28, 14])
    print(f"Excel thử nhập: {OUT} ({len(list(OUT.glob('*.xlsx')))} file)")


if __name__ == "__main__":
    main(sys.argv[1])
