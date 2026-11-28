"""LỊCH CHẠY của luật tự động (ADR 0265 mục 1) — hàm thuần. Người đặt luật CHỌN (hằng ngày / ngày làm việc / hằng tuần thứ … / hằng tháng ngày …, giờ),
không gõ chuỗi quy tắc; lưu thành chuỗi kiểu RFC 5545 để đọc lại được:

    FREQ=DAILY;INTERVAL=1                    mỗi ngày
    FREQ=WEEKLY;INTERVAL=1;BYDAY=MO,…,FR     ngày làm việc (thứ Hai – thứ Sáu)
    FREQ=WEEKLY;INTERVAL=1;BYDAY=MO          mỗi tuần vào thứ Hai
    FREQ=MONTHLY;BYMONTHDAY=5                mỗi tháng ngày 5 (1–28)        FREQ=MONTHLY;BYMONTHDAY=-1   ngày cuối tháng

Theo ngày / tuần dùng lại bộ lặp của Lịch (``modules/schedule/rrule.py`` — cùng luật múi giờ: giờ ĐỒNG HỒ của công ty giữ nguyên qua các lần lặp, 0169);
bộ đó không làm ``MONTHLY`` nên theo tháng tính ở đây (chỉ ``BYMONTHDAY`` 1–28 hoặc −1 — ngày nào tháng cũng có, không lượt nào bị bỏ âm thầm).
Múi giờ là múi của công ty đang chạy (``zone()`` — lõi đặt mỗi giao dịch của công ty)."""

import calendar
from datetime import date, datetime, time, timedelta

from barista.api import Invalid, zone

REPEAT = {"daily": "Hằng ngày", "weekdays": "Ngày làm việc (thứ Hai – thứ Sáu)", "weekly": "Hằng tuần", "monthly": "Hằng tháng"}
WEEKDAY = {"MO": "Thứ Hai", "TU": "Thứ Ba", "WE": "Thứ Tư", "TH": "Thứ Năm", "FR": "Thứ Sáu", "SA": "Thứ Bảy", "SU": "Chủ nhật"}
LAST = "last"
MONTH_DAY = {**{str(d): f"Ngày {d}" for d in range(1, 29)}, LAST: "Ngày cuối tháng"}
AT = {f"{h:02d}:{mi:02d}": f"{h:02d}:{mi:02d}" for h in range(24) for mi in (0, 30)}   # chọn giờ theo nửa tiếng — vòng quét chạy mỗi phút


def build(repeat: str, weekday: str = "MO", month_day: str = "1") -> str:
    """Lựa chọn trên form ⇒ chuỗi quy tắc chuẩn hoá. Lựa chọn lạ ⇒ ``Invalid`` nêu lựa chọn dùng được."""
    if repeat == "daily":
        return "FREQ=DAILY;INTERVAL=1"
    if repeat == "weekdays":
        return "FREQ=WEEKLY;INTERVAL=1;BYDAY=MO,TU,WE,TH,FR"
    if repeat == "weekly":
        if weekday not in WEEKDAY:
            raise Invalid(f"Thứ “{weekday}” không có — chọn: {', '.join(WEEKDAY.values())}")
        return f"FREQ=WEEKLY;INTERVAL=1;BYDAY={weekday}"
    if repeat == "monthly":
        if month_day not in MONTH_DAY:
            raise Invalid("Ngày trong tháng: 1–28 hoặc ngày cuối tháng (ngày 29–31 không phải tháng nào cũng có)")
        return f"FREQ=MONTHLY;BYMONTHDAY={-1 if month_day == LAST else int(month_day)}"
    raise Invalid(f"Lịch “{repeat}” không có — chọn: {', '.join(REPEAT.values())}")


def clock_of(at: str) -> time:
    """“08:00” ⇒ giờ đồng hồ. Chỉ giờ có trong danh sách chọn (nửa tiếng một)."""
    if at not in AT:
        raise Invalid("Giờ chạy: chọn trong danh sách (nửa tiếng một, vd 08:00, 17:30)")
    return time(int(at[:2]), int(at[3:]))


def choices(schedule: str) -> dict[str, str]:
    """Chuỗi đã lưu ⇒ lựa chọn trên form (mở lại luật để sửa)."""
    parts = dict(bit.split("=", 1) for bit in schedule.split(";"))
    if parts["FREQ"] == "DAILY":
        return {"repeat": "daily"}
    if parts["FREQ"] == "MONTHLY":
        return {"repeat": "monthly", "month_day": LAST if parts["BYMONTHDAY"] == "-1" else parts["BYMONTHDAY"]}
    days = parts.get("BYDAY", "MO")
    return {"repeat": "weekdays"} if "," in days else {"repeat": "weekly", "weekday": days}


def describe(schedule: str, at: time) -> str:
    """Câu người đọc: “mỗi tuần vào thứ Hai lúc 08:00”."""
    c = choices(schedule)
    when = {"daily": "mỗi ngày", "weekdays": "ngày làm việc (thứ Hai – thứ Sáu)",
            "weekly": f"mỗi tuần vào {WEEKDAY.get(c.get('weekday', ''), '').lower()}",
            "monthly": "ngày cuối mỗi tháng" if c.get("month_day") == LAST else f"ngày {c.get('month_day')} mỗi tháng"}[c["repeat"]]
    return f"{when} lúc {at:%H:%M}"


def _month_day(year: int, month: int, by: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1] if by == -1 else by)


def _monthly(by: int, at: time, after: datetime, upto: datetime) -> list[datetime]:
    out: list[datetime] = []
    local = after.astimezone(zone())
    y, mo = local.year, local.month
    while True:
        t = datetime.combine(_month_day(y, mo, by), at, zone())
        if t > upto:
            return out
        if t > after:
            out.append(t)
        y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)


def between(schedule: str, at: time, after: datetime, upto: datetime) -> list[datetime]:
    """Các lượt ``after < t <= upto`` theo thứ tự, giờ đồng hồ ``at`` của công ty. Hai mốc phải kèm múi giờ."""
    if after.tzinfo is None or upto.tzinfo is None:
        raise Invalid("Mốc thời gian phải kèm múi giờ")
    parts = dict(bit.split("=", 1) for bit in schedule.split(";"))
    if parts["FREQ"] == "MONTHLY":
        return _monthly(int(parts["BYMONTHDAY"]), at, after, upto)
    # nạp lúc gọi: bộ lặp của Lịch (gói khác) — hàm thuần, không đụng dữ liệu của gói đó
    from barista.modules.schedule import rrule

    # quy tắc ở đây luôn INTERVAL=1 và nêu rõ thứ ⇒ mốc bắt đầu chỉ cần đứng trước ``after`` (một tuần cho chắc qua mọi múi giờ)
    start = datetime.combine(after.astimezone(zone()).date() - timedelta(days=7), at, zone())
    return rrule.starts(rrule.parse(schedule), start, upto=upto, after=after)


def next_after(schedule: str, at: time, after: datetime) -> datetime:
    """Lượt kế tiếp SAU ``after`` (không tính đúng ``after``)."""
    got = between(schedule, at, after, after + timedelta(days=63))   # đủ cho mọi lịch ở đây (xa nhất: tháng sau nữa)
    if not got:
        raise Invalid("Lịch không có lượt nào trong hai tháng tới")
    return got[0]
