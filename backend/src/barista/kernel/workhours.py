"""Đồng hồ hạn xử lý chỉ chạy trong GIỜ LÀM VIỆC (TMS đếm phút thẳng từ lúc chia ⇒ lead 22h đêm đã "trễ" từ sáng).

Hàm thuần, theo múi giờ CÔNG TY (``clock.zone()``, 0169); thứ trong tuần theo ISO (1 = thứ Hai … 7 = Chủ nhật). Giờ mở / đóng tính bằng PHÚT trong ngày
(0..1440) để biểu diễn được cả ca 24/7 (0 → 1440) mà không dính TIME 24:00. ``off``: NGÀY NGHỈ (lễ, Tết) — ngày làm việc trong tuần mà rơi
vào tập này thì đồng hồ không chạy (tập lấy từ lịch nghiệp vụ có phiên bản, ``kernel/business_calendar.py`` — bên gọi chọn lịch).
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from barista.kernel.clock import zone


@dataclass(frozen=True)
class Hours:
    days: tuple[int, ...]
    open_min: int
    close_min: int
    off: frozenset[date] = frozenset()

    def __post_init__(self) -> None:
        if not self.days or any(d < 1 or d > 7 for d in self.days):
            raise ValueError("ngày làm việc phải thuộc 1..7")
        if not 0 <= self.open_min < self.close_min <= 1440:
            raise ValueError("giờ mở phải nhỏ hơn giờ đóng (0..1440 phút)")


def _day_start(at: datetime) -> datetime:
    return at.replace(hour=0, minute=0, second=0, microsecond=0)


def _window(day: datetime, h: Hours) -> tuple[datetime, datetime] | None:
    """Khoảng làm việc của ngày chứa ``day`` (None nếu không phải ngày làm việc)."""
    if day.isoweekday() not in h.days or day.date() in h.off:
        return None
    base = _day_start(day)
    return base + timedelta(minutes=h.open_min), base + timedelta(minutes=h.close_min)


def next_open(at: datetime, h: Hours) -> datetime:
    """Thời điểm đồng hồ bắt đầu chạy: chính ``at`` nếu đang trong giờ làm việc, không thì giờ mở cửa kế tiếp."""
    at = at.astimezone(zone())
    for i in range(0, 60):   # Tết + cuối tuần + lịch ít ngày làm ⇒ có thể vài tuần mới mở lại
        day = at + timedelta(days=i)
        w = _window(day, h)
        if w is None:
            continue
        start, end = w
        if i == 0:
            if at < start:
                return start
            if at < end:
                return at
            continue
        return start
    raise ValueError("lịch làm việc không có ngày nào mở")


def add_working_minutes(at: datetime, minutes: int, h: Hours) -> datetime:
    """Hạn = ``at`` + ``minutes`` phút LÀM VIỆC. 0 phút = ngay lúc đồng hồ bắt đầu chạy."""
    cur = next_open(at, h)
    left = minutes
    while True:
        w = _window(cur, h)
        assert w is not None
        room = int((w[1] - cur).total_seconds() // 60)
        if left <= room:
            return cur + timedelta(minutes=left)
        left -= room
        cur = next_open(w[1] + timedelta(seconds=1), h)


def is_open(at: datetime, h: Hours) -> bool:
    """``at`` đang trong giờ làm việc (đồng hồ đang chạy)."""
    return next_open(at, h) == at.astimezone(zone())
