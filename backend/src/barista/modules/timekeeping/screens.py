"""Màn của gói Chấm công (app chỉ vẽ): "Chấm công" hai nút lớn cho điện thoại; bảng công của tôi; đơn; giờ chấm cần xét; bảng công đội / công ty;
số dư phép; địa điểm & giờ làm. Bàn việc: giờ chấm cần xét, đơn đang chờ, bảng công chờ xác nhận."""

from typing import Any

from barista.api import Action as A
from barista.api import Desk, Line, Screen, Workspace, kpi, line
from barista.modules.timekeeping import m


def _say_flagged(o: Any) -> Line | None:
    n = int(kpi(o, "Chờ xét"))
    return line("warn", f"{n} giờ chấm có cờ chờ xét", "Ngoài mạng văn phòng / không đọc được mạng — nhận hoặc bỏ trước khi chốt bảng công",
                ask="Ai chấm công ngoài văn phòng tuần này?") if n else None


def _say_requests(o: Any) -> Line | None:
    n = int(kpi(o, "Đang chờ duyệt"))
    return line("accent", f"{n} đơn nghỉ / làm thêm / bổ sung giờ đang chờ duyệt", "Duyệt ở hộp duyệt; chốt bảng công cần xử lý hết đơn của tháng") if n else None


def _say_confirm(o: Any) -> Line | None:
    n = int(kpi(o, "Chờ xác nhận"))
    return line("accent", f"{n} bảng công đã chốt chờ anh/chị xác nhận", "Đồng ý hoặc ghi phản hồi trước hạn") if n else None


m.workspace(Workspace("timekeeping", "Chấm công", "clock", (
    Screen("tk_today", "Chấm công", "clock", "timekeeping.today", tint="green"),
    Screen("tk_my_month", "Công tháng này", "calendar", "timekeeping.my_month", tint="blue"),
    Screen("tk_pending_confirm", "Bảng công chờ xác nhận", "checkmark.circle", "timekeeping.pending_confirm", tint="blue",
           desk=Desk("do", say=_say_confirm, rank=30),
           row_actions=(A("timekeeping.confirm_timesheet", "Xác nhận", bind={"month": "period"}, style="primary"),)),
    Screen("tk_requests", "Đơn nghỉ & làm thêm", "doc.text", "timekeeping.requests", tint="orange",
           actions=(A("timekeeping.request_leave", style="primary"), A("timekeeping.request_overtime"), A("timekeeping.request_correction")),
           desk=Desk("watch", say=_say_requests, menu=True, rank=60)),
    Screen("tk_flagged", "Giờ chấm cần xét", "exclamationmark.triangle", "timekeeping.flagged", tint="red",
           row_actions=(A("timekeeping.review_punch", "Nhận", bind={"punch_id": "id"}, preset={"decision": "accept"}, when={"state": ["pending"]},
                          style="primary"),
                        A("timekeeping.review_punch", "Bỏ", bind={"punch_id": "id"}, preset={"decision": "reject"}, when={"state": ["pending"]},
                          style="destructive")),
           desk=Desk("decide", say=_say_flagged, menu=True, rank=40)),
    Screen("tk_timesheet", "Bảng công", "tablecells", "timekeeping.timesheet", tint="teal", min_scope="team",
           actions=(A("timekeeping.close_period", style="primary"),)),
    Screen("tk_balances", "Số dư phép", "calendar.badge.clock", "timekeeping.balances", tint="indigo",
           actions=(A("timekeeping.grant_leave"), A("timekeeping.grant_annual_leave"))),
    Screen("tk_enrolments", "Ai chấm công thế nào", "person.2", "timekeeping.enrolments", min_scope="all",
           actions=(A("timekeeping.enrol", style="primary"), A("timekeeping.import_punch"))),
    Screen("tk_policy", "Địa điểm & giờ làm", "gearshape", "timekeeping.policy", pinnable=False),
), order=45))

