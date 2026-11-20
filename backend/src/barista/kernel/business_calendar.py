"""Lịch nghiệp vụ dùng chung (0068), độc lập luật: calendars/<lịch>/<năm>/<phiên bản>.json.

Ngày nghỉ và ngày làm bù là dữ liệu. Kết quả ghi tham chiếu lịch đã dùng; thêm lịch mới không tính lại hạn đã lưu.
Năm chưa có lịch giữ lịch tuần của bản gần nhất và báo thiếu lịch lễ, không giả vờ đã biết lịch lễ của năm đó.
"""

import json
from dataclasses import dataclass
from datetime import date, timedelta
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "calendars"


@dataclass(frozen=True)
class Year:
    calendar: str
    year: int
    version: str
    status: str
    weekend: frozenset[int]
    holidays: frozenset[date]
    working_days: frozenset[date]

    @property
    def ref(self) -> str:
        return f"{self.calendar}/{self.year}@{self.version}"

    def is_workday(self, day: date) -> bool:
        return day in self.working_days or (day.weekday() not in self.weekend and day not in self.holidays)


@dataclass(frozen=True)
class Adjusted:
    day: date
    refs: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@cache
def years() -> dict[tuple[str, int], tuple[Year, ...]]:
    out: dict[tuple[str, int], list[Year]] = {}
    for path in sorted(ROOT.glob("*/*/*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        cid, year, version = path.parents[1].name, int(path.parent.name), path.stem
        date.fromisoformat(version)
        if (raw.get("calendar"), raw.get("year"), raw.get("version")) != (cid, year, version):
            raise ValueError(f"{path}: lịch / năm / phiên bản phải khớp đường dẫn")
        status = raw.get("status")
        if status not in ("assumption", "verified") or (status == "verified" and not raw.get("verified_by")):
            raise ValueError(f"{path}: trạng thái xác nhận không hợp lệ")
        if not raw.get("sources") or not raw.get("basis"):
            raise ValueError(f"{path}: thiếu nguồn / căn cứ lịch")
        weekend = frozenset(raw["weekend"])
        holidays = frozenset(date.fromisoformat(d) for d in raw["holidays"])
        working = frozenset(date.fromisoformat(d) for d in raw["working_days"])
        if not weekend <= set(range(7)) or holidays & working or any(d.year != year for d in holidays | working):
            raise ValueError(f"{path}: ngày nghỉ / ngày làm bù không hợp lệ")
        out.setdefault((cid, year), []).append(Year(cid, year, version, status, weekend, holidays, working))
    return {k: tuple(sorted(v, key=lambda x: x.version)) for k, v in out.items()}


def calendars() -> list[str]:
    """Các lịch đang có (để chọn khi nghĩa vụ dời theo ngày làm việc)."""
    return sorted({cid for cid, _ in years()})


def holidays(calendar: str) -> frozenset[date]:
    """Mọi NGÀY NGHỈ lễ / Tết đã biết của một lịch (bản mới nhất của từng năm) — cho đồng hồ giờ làm việc (``workhours.Hours.off``) mà lịch tuần
    do bên dùng tự đặt (vd ca bán hàng T2–T7). Ngày làm bù không trả: đó là lịch tuần của cơ quan hành chính, không phải của ca bên dùng."""
    known = [v[-1] for (cid, _), v in years().items() if cid == calendar]
    if not known:
        raise ValueError(f"Không có lịch {calendar}")
    return frozenset().union(*(y.holidays for y in known))


def reload(root: Path | None = None) -> None:
    global ROOT
    if root is not None:
        ROOT = root
    years.cache_clear()


def next_workday(calendar: str, day: date) -> Adjusted:
    """Ngày làm việc từ ``day`` trở đi — luật "hạn rơi ngày nghỉ thì sang ngày làm việc kế tiếp" (hạn nộp theo luật)."""
    return _walk(calendar, day, 1)


def previous_workday(calendar: str, day: date) -> Adjusted:
    """Ngày làm việc từ ``day`` lùi về — hạn việc NỘI BỘ rơi ngày nghỉ phải xong TRƯỚC (vd chuẩn bị cho sự kiện thứ Bảy ⇒ thứ Sáu)."""
    return _walk(calendar, day, -1)


def _walk(calendar: str, day: date, step: int) -> Adjusted:
    known = years()
    available = [v for (cid, _), versions in known.items() if cid == calendar for v in versions]
    if not available:
        raise ValueError(f"Không có lịch {calendar}")
    refs: list[str] = []
    warnings: list[str] = []
    for _ in range(370):
        versions = known.get((calendar, day.year), ())
        v = versions[-1] if versions else None
        weekly = max((x for x in available if x.year <= day.year), key=lambda x: (x.year, x.version), default=None)
        weekly = weekly or min(available, key=lambda x: (x.year, x.version))
        fallback = "weekends-only" if weekly.weekend == frozenset({5, 6}) else "weekly-only"
        ref = v.ref if v else f"{calendar}/{day.year}@{fallback}"
        warning = (f"Lịch {calendar} năm {day.year} chưa được xác nhận" if v and v.status == "assumption" else
                   f"Chưa có lịch {calendar} năm {day.year} — dùng lịch tuần từ {weekly.ref}, chưa xét ngày lễ" if v is None else "")
        if ref not in refs:
            refs.append(ref)
        if warning and warning not in warnings:
            warnings.append(warning)
        if v.is_workday(day) if v else day.weekday() not in weekly.weekend:
            return Adjusted(day, tuple(refs), tuple(warnings))
        day += timedelta(days=step)
    raise ValueError(f"Lịch {calendar}: không tìm được ngày làm việc trong 370 ngày")


def non_working(calendar: str, start: date, end: date) -> list[tuple[date, str]]:
    """Các NGÀY NGHỈ trong [start, end] (gồm hai đầu) để tô bóng dòng thời gian (0222): ``"holiday"`` = lễ / Tết, ``"weekend"`` = nghỉ tuần.
    Cùng nguồn với ``previous_workday`` — năm chưa có lịch lễ giữ lịch tuần của bản gần nhất (không bịa ngày lễ). Lịch không tồn tại ⇒ ValueError."""
    known = years()
    available = [v for (cid, _), versions in known.items() if cid == calendar for v in versions]
    if not available:
        raise ValueError(f"Không có lịch {calendar}")
    out: list[tuple[date, str]] = []
    day = start
    while day <= end:
        versions = known.get((calendar, day.year), ())
        v = versions[-1] if versions else None
        weekly = max((x for x in available if x.year <= day.year), key=lambda x: (x.year, x.version), default=None) or min(
            available, key=lambda x: (x.year, x.version))
        if v is not None:
            if not v.is_workday(day):
                out.append((day, "holiday" if day in v.holidays else "weekend"))
        elif day.weekday() in weekly.weekend:
            out.append((day, "weekend"))
        day += timedelta(days=1)
    return out
