"""Thời gian (0169): LƯU là mốc tuyệt đối (``timestamptz`` = UTC bên trong), TÍNH và HIỆN theo MÚI GIỜ CÔNG TY.

* Múi giờ công ty là DỮ LIỆU (``core.tenants.timezone``, tên IANA, mặc định của bản cài ``BARISTA_DEFAULT_TIMEZONE`` = Asia/Ho_Chi_Minh). Lõi nạp
  MỘT lần mỗi giao dịch của công ty (``db.tenant_tx``): đặt ``TimeZone`` của phiên Postgres theo công ty và ``zone()`` ở đây — code nghiệp vụ không
  tự tra, không truyền tay.
* ``zone()``: múi của công ty đang chạy (ngoài giao dịch công ty ⇒ múi mặc định của bản cài). ``today()`` / ``day_start()`` / ``local()`` theo múi đó.
* ``VN``: múi giờ của LUẬT Việt Nam (bộ quy định ``vn-*``: hạn nộp, ngày hiệu lực) — KHÔNG dùng cho giờ làm việc / ngày / hiển thị của công ty.
  Dùng ở chỗ khác ⇒ ``tests/test_timezone.py`` chặn.
* Không bao giờ dựa vào giờ địa phương của TIẾN TRÌNH (biến ``TZ``): không ``datetime.now()`` / ``date.today()`` / ``astimezone()`` không đối số.

``BARISTA_DEMO_TODAY=YYYY-MM-DD``: ĐỒNG HỒ DEMO cho bản trình diễn có chứng từ mẫu ghi ngày cố định — ``today()`` trả ngày đó, ``shown()`` dời giờ hiển thị
theo cùng độ lệch. Chỉ đặt ở máy demo; bản cài thật không đặt.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, time, timedelta
from functools import cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from barista.config import settings

VN = ZoneInfo("Asia/Ho_Chi_Minh")   # múi giờ PHÁP LUẬT Việt Nam — xem đầu file
VN_NAME = "Asia/Ho_Chi_Minh"

_zone: ContextVar[ZoneInfo | None] = ContextVar("barista_company_zone", default=None)


@cache
def named(name: str) -> ZoneInfo:
    """Tên IANA ⇒ múi giờ. Chỉ nhận tên vùng thật (``Khu vực/Thành phố`` hoặc ``UTC``) — không nhận viết tắt ("ICT"), độ lệch ("+07", "UTC+7":
    chuỗi POSIX đảo dấu) hay đường dẫn tệp. Sai ⇒ ``ValueError``."""
    n = (name or "").strip()
    if (n != "UTC" and ("/" not in n or n.startswith(("Etc/", "posix/", "right/")))) or n not in _known():
        raise ValueError(f"Múi giờ “{name}” không hợp lệ — dùng tên IANA như Asia/Ho_Chi_Minh, Asia/Singapore, Europe/London")
    try:
        return ZoneInfo(n)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ValueError(f"Múi giờ “{name}” không hợp lệ") from e


@cache
def of(name: str) -> ZoneInfo:
    """Múi giờ từ tên ĐÃ LƯU (``core.tenants.timezone`` — đã kiểm lúc ghi bằng ``named`` + CHECK của DB)."""
    return ZoneInfo(name)


@cache
def _known() -> frozenset[str]:
    return frozenset(available_timezones())


def choices() -> list[str]:
    """Mọi tên nhận được (``named``), xếp theo tên — mặc định của bản cài đứng đầu."""
    first = settings.default_timezone
    return [first, *sorted(n for n in _known() if n != first and _ok(n))]


def _ok(n: str) -> bool:
    try:
        named(n)
        return True
    except ValueError:
        return False


def default_zone() -> ZoneInfo:
    return named(settings.default_timezone)


def zone() -> ZoneInfo:
    """Múi giờ công ty của giao dịch đang chạy (``db.tenant_tx`` đặt); ngoài giao dịch công ty ⇒ mặc định của bản cài."""
    return _zone.get() or default_zone()


def zone_label(z: ZoneInfo | None = None, *, head: bool = False) -> str:
    """Nhãn cho người đọc: "giờ Việt Nam" cho múi Việt Nam (giữ đúng chữ cũ), còn lại "giờ <tên IANA>". ``head``: viết hoa chữ đầu (đầu câu)."""
    k = (z or zone()).key
    s = "giờ Việt Nam" if k == VN_NAME else f"giờ {k}"
    return "G" + s[1:] if head else s


@contextmanager
def using(z: ZoneInfo | str) -> Iterator[ZoneInfo]:
    """Tính theo múi ``z`` trong khối này (lõi: ``db.tenant_tx``; vòng quét xuyên công ty: múi của từng công ty)."""
    zi = of(z) if isinstance(z, str) else z
    token = _zone.set(zi)
    try:
        yield zi
    finally:
        _zone.reset(token)


def _pinned() -> date | None:
    # chỉ 10 ký tự đầu: stack qua YAML biến "2026-11-30" thành "2026-11-30 00:00:00 +0000 UTC" (bản LC 09/11)
    return date.fromisoformat(settings.demo_today.strip()[:10]) if settings.demo_today.strip() else None


def now() -> datetime:
    """Bây giờ, mốc có múi (múi công ty) — so sánh với mốc khác như mọi mốc tuyệt đối; ``.hour`` / ``.date()`` là giờ / ngày của công ty."""
    return datetime.now(zone())


def today() -> date:
    return _pinned() or datetime.now(zone()).date()


def local(ts: datetime) -> datetime:
    """Mốc tuyệt đối ⇒ giờ của công ty (để lấy ngày / giờ / định dạng). Mốc không múi là lỗi lập trình."""
    if ts.tzinfo is None:
        raise ValueError("mốc giờ không có múi — mọi mốc trong Barista phải có múi (timestamptz)")
    return ts.astimezone(zone())


def day_of(ts: datetime) -> date:
    """Ngày (theo công ty) chứa mốc ``ts``."""
    return local(ts).date()


def day_start(d: date, at: time = time(0)) -> datetime:
    """Mốc bắt đầu ngày ``d`` (hoặc giờ ``at`` của ngày đó) theo giờ công ty."""
    return datetime.combine(d, at, zone())


def shown(ts: datetime) -> datetime:
    """Mốc giờ thật ⇒ giờ hiển thị: đang chạy đồng hồ demo thì dời cùng độ lệch để các ngày không mâu thuẫn nhau trên màn hình."""
    pin = _pinned()
    return ts + timedelta(days=(pin - datetime.now(zone()).date()).days) if pin else ts


def month_of(d: date) -> date:
    """Ngày đầu tháng của ``d`` — kỳ tháng lưu dạng ngày đầu tháng."""
    return d.replace(day=1)


def add_months(d: date, n: int) -> date:
    """Ngày đầu tháng của tháng cách tháng ``d`` ``n`` tháng."""
    y, mo = divmod(d.year * 12 + d.month - 1 + n, 12)
    return date(y, mo + 1, 1)
