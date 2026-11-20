"""Quy tắc lặp — TẬP CON của RFC 5545 RRULE: ``FREQ=DAILY|WEEKLY``, ``INTERVAL``, ``BYDAY`` (MO…SU, không số thứ tự), ``UNTIL``, ``COUNT``.

Hàm thuần, theo múi giờ công ty (0169) — giờ ĐỒNG HỒ giữ nguyên qua các lần lặp, kể cả qua đổi giờ mùa hè ở múi có đổi. Tuần bắt đầu thứ Hai (``WKST=MO``). Lần đầu
(``dtstart``) chỉ tính khi khớp quy tắc. ``UNTIL`` tính cả lần đúng mốc (RFC 5545 §3.3.10). Không làm: MONTHLY / YEARLY, BYSETPOS, số thứ tự trong BYDAY.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from barista.api import Invalid, zone

DAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")   # vị trí + 1 = thứ ISO
MAX_COUNT = 730


@dataclass(frozen=True)
class Rule:
    freq: str                      # "DAILY" | "WEEKLY"
    interval: int = 1
    byday: tuple[int, ...] = ()    # thứ ISO 1–7; trống = mọi ngày (DAILY) / thứ của dtstart (WEEKLY)
    until: datetime | None = None  # có múi giờ
    count: int | None = None

    def __post_init__(self) -> None:
        if self.freq not in ("DAILY", "WEEKLY"):
            raise Invalid("Lặp lại chỉ theo ngày hoặc theo tuần")
        if not 1 <= self.interval <= 52:
            raise Invalid("Khoảng lặp từ 1 tới 52")
        if any(d < 1 or d > 7 for d in self.byday):
            raise Invalid("Thứ trong tuần từ thứ Hai tới Chủ nhật")
        if self.until is not None and self.count is not None:
            raise Invalid("Chọn “lặp đến ngày” HOẶC “số lần”, không cả hai")
        if self.count is not None and not 1 <= self.count <= MAX_COUNT:
            raise Invalid(f"Số lần lặp từ 1 tới {MAX_COUNT}")
        if self.until is not None and self.until.tzinfo is None:
            raise Invalid("Mốc kết thúc lặp phải kèm múi giờ")


def parse(text: str) -> Rule:
    parts: dict[str, str] = {}
    for bit in (text or "").strip().removeprefix("RRULE:").split(";"):
        if not bit:
            continue
        k, sep, v = bit.partition("=")
        if not sep or k.upper() in parts:
            raise Invalid(f"Quy tắc lặp không đọc được: {text}")
        parts[k.upper()] = v.strip()
    if extra := set(parts) - {"FREQ", "INTERVAL", "BYDAY", "UNTIL", "COUNT", "WKST"}:
        raise Invalid(f"Quy tắc lặp chưa hỗ trợ: {', '.join(sorted(extra))}")
    if parts.get("WKST", "MO").upper() != "MO":
        raise Invalid("Tuần bắt đầu từ thứ Hai (WKST=MO)")
    try:
        byday = tuple(sorted({DAYS.index(d.strip().upper()) + 1 for d in parts["BYDAY"].split(",") if d.strip()})) if parts.get("BYDAY") else ()
        until = None
        if u := parts.get("UNTIL"):
            until = (datetime.strptime(u, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC) if "T" in u
                     else datetime.combine(datetime.strptime(u, "%Y%m%d").date(), time(23, 59, 59), zone()))
        return Rule(parts.get("FREQ", "").upper(), int(parts.get("INTERVAL", "1")), byday, until, int(parts["COUNT"]) if "COUNT" in parts else None)
    except ValueError as err:
        raise Invalid(f"Quy tắc lặp không đọc được: {text}") from err


def text(rule: Rule) -> str:
    """Chuỗi chuẩn hoá (lưu vào ``schedule.series.rrule``)."""
    bits = [f"FREQ={rule.freq}", f"INTERVAL={rule.interval}"]
    if rule.byday:
        bits.append("BYDAY=" + ",".join(DAYS[d - 1] for d in rule.byday))
    if rule.until is not None:
        bits.append(f"UNTIL={rule.until.astimezone(UTC):%Y%m%dT%H%M%SZ}")
    if rule.count is not None:
        bits.append(f"COUNT={rule.count}")
    return ";".join(bits)


def _monday(d: date) -> date:
    return d - timedelta(days=d.isoweekday() - 1)


def starts(rule: Rule, dtstart: datetime, *, upto: datetime, after: datetime | None = None) -> list[datetime]:
    """Giờ bắt đầu các lần lặp ``after < t <= upto`` (``after`` None ⇒ từ lần đầu), theo đúng thứ tự — số lần (COUNT) đếm từ lần đầu chuỗi, kể cả
    các lần trước ``after``. Múi giờ công ty, giờ đồng hồ của ``dtstart``."""
    if dtstart.tzinfo is None:
        raise Invalid("Giờ bắt đầu chuỗi phải kèm múi giờ")
    local = dtstart.astimezone(zone())
    clock, d0 = local.timetz().replace(tzinfo=None), local.date()
    days = rule.byday or ((d0.isoweekday(),) if rule.freq == "WEEKLY" else tuple(range(1, 8)))
    out: list[datetime] = []
    n = 0
    d = d0
    week0 = _monday(d0)
    while True:
        t = datetime.combine(d, clock, zone())
        if t > upto or (rule.until is not None and t > rule.until):
            break
        if rule.freq == "DAILY":
            hit = (d - d0).days % rule.interval == 0 and d.isoweekday() in days
        else:
            hit = ((_monday(d) - week0).days // 7) % rule.interval == 0 and d.isoweekday() in days
        if hit:
            n += 1
            if after is None or t > after:
                out.append(t)
            if rule.count is not None and n >= rule.count:
                break
        d += timedelta(days=1)
        if (d - d0).days > 366 * 20:   # lưới an toàn: quy tắc không bao giờ khớp
            break
    return out


def nth(rule: Rule, dtstart: datetime, at: datetime) -> int:
    """Số thứ tự (từ 0) của lần lặp bắt đầu đúng ``at`` — để cắt chuỗi giữ đúng số lần còn lại."""
    return len(starts(rule, dtstart, upto=at - timedelta(seconds=1)))


def describe(rule: Rule) -> str:
    names = {1: "thứ Hai", 2: "thứ Ba", 3: "thứ Tư", 4: "thứ Năm", 5: "thứ Sáu", 6: "thứ Bảy", 7: "Chủ nhật"}
    every = ("mỗi ngày" if rule.interval == 1 else f"{rule.interval} ngày một lần") if rule.freq == "DAILY" else \
        ("mỗi tuần" if rule.interval == 1 else f"{rule.interval} tuần một lần")
    on = f" vào {', '.join(names[d] for d in rule.byday)}" if rule.byday else ""
    end = f", đến {rule.until.astimezone(zone()):%d/%m/%Y}" if rule.until else (f", {rule.count} lần" if rule.count else "")
    return f"Lặp {every}{on}{end}"
