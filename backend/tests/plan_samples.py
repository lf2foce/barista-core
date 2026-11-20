"""File kế hoạch MẪU cho test và demo nhập kế hoạch từ Excel (0222 §15/11) — dựng bằng openpyxl, lộn xộn như file thật.
``sample_plan_rows``: kế hoạch tự soạn (tên kế hoạch ở đầu, dòng nhóm WBS, người phụ trách viết tắt / không dấu, việc trước theo STT, số ngày).
``ms_project_rows``: kiểu xuất từ MS Project (ID, Task Name, Duration "5 days", Start "Mon 11/16/26", Predecessors "3FS+2 days", Resource Names, % Complete, WBS).
``revised_plan_rows``: bản SỬA của ``sample_plan_rows`` — dời ngày, đổi tên, thêm việc, bỏ một việc (nhập lại ⇒ cập nhật, không xoá).
Ghi file demo: ``python backend/scripts/sample_plan_xlsx.py <thư mục>``."""

import io
from datetime import date, timedelta
from typing import Any

from openpyxl import Workbook


def xlsx(rows: list[list[Any]], sheet: str = "Kế hoạch") -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = sheet
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def sample_plan_rows(t0: date) -> list[list[Any]]:
    """Kế hoạch ra mắt chiến dịch như một chị điều phối gửi: tên kế hoạch ở đầu, dòng nhóm, người phụ trách viết tắt / không dấu, việc trước theo STT, số ngày."""
    def d(n: int) -> date:
        return t0 + timedelta(days=n)


    return [
        ["KẾ HOẠCH RA MẮT CHIẾN DỊCH THÁNG 12", None, None, None, None, None, None, None, None],
        ["Người lập: Chị Hạnh", None, None, None, None, None, None, None, None],
        [None] * 9,
        ["STT", "Hạng mục / Công việc", "Người phụ trách", "Bắt đầu", "Kết thúc", "Số ngày", "Việc trước", "% hoàn thành", "Ghi chú"],
        ["1", "Chuẩn bị", None, None, None, None, None, None, None],
        ["1.1", "Chốt ngân sách", "Huy", d(1), d(3), None, None, 1, "đã duyệt"],
        ["1.2", "Thiết kế key visual", "hanh", None, None, "5 ngày", "1.1", 0.4, None],
        ["1.3", "Viết nội dung bài", "Bảo", d(4), None, 3, "1.1", None, None],
        ["2", "Triển khai", None, None, None, None, None, None, None],
        ["2.1", "Dựng landing page", "Bao", None, None, "4", "1.2FS+1d; 1.3", None, None],
        ["2.2", "Chạy quảng cáo", "DQH", None, None, "2 tuần", "2.1", None, None],
        ["2.3", "Nghiệm thu", "Huy", None, None, "0", "2.2", None, "mốc"],
        [None] * 9,
        ["Tổng cộng", None, None, None, None, None, None, None, None],
    ]


def xlsx_unused(rows: list[list[Any]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Kế hoạch"
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def revised_plan_rows(t0: date, title: str | None = None) -> list[list[Any]]:
    """Bản sửa: "Chốt ngân sách" (đã xong) có ngày mới — giữ nguyên; "Thiết kế key visual" có ngày rõ; "Viết nội dung bài" lùi; "Dựng landing page" đổi tên; thêm 2.4; bỏ 2.2."""
    rows = sample_plan_rows(t0)
    if title:
        rows[0][0] = title
    rows[5][3], rows[5][4] = t0 + timedelta(days=2), t0 + timedelta(days=5)
    rows[6][3], rows[6][4] = t0 + timedelta(days=4), t0 + timedelta(days=8)
    rows[7][3] = t0 + timedelta(days=6)
    rows[9][1] = "Dựng landing page v2"
    del rows[10]
    rows.insert(10, ["2.4", "Báo cáo kết quả", "Hanh", None, None, "2", "2.1", None, None])
    return rows


def ms_project_rows(t0: date) -> list[list[Any]]:
    """Kiểu Excel xuất từ MS Project: cột ID + WBS, tên task thụt lề, Duration có đơn vị, ngày có thứ, Predecessors "3FS+2 days"."""
    def wd(n: int) -> str:
        d = t0 + timedelta(days=n)
        return f"{d:%a} {d:%d/%m/%y}"
    return [
        ["ID", "WBS", "Task Name", "Duration", "Start", "Finish", "Predecessors", "Resource Names", "% Complete"],
        [1, "1", "Khảo sát hiện trạng", "6 days", wd(0), wd(8), None, None, "50%"],
        [2, "1.1", "Phỏng vấn các phòng", "3 days", wd(0), wd(2), None, "Hạnh", "100%"],
        [3, "1.2", "Tổng hợp yêu cầu", "3 days", wd(3), wd(8), "2", "Huy", "0%"],
        [4, "2", "Thiết kế giải pháp", "2 wks", wd(9), wd(22), "3FS+2 days", None, "0%"],
        [5, "2.1", "Thiết kế dữ liệu", "5 days", wd(11), wd(15), "3FS+2 days", "Bảo", "0%"],
        [6, "2.2", "Thiết kế màn hình", "5 days", None, None, "5SS+2 days", "Hạnh", "0%"],
        [7, "3", "Bàn giao", "0 days", None, None, "5;6", "Huy", "0%"],
    ]
