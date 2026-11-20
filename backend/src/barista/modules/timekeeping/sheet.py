"""BẢNG CÔNG NGÀY = HÀM THUẦN (0150 §4.3): không DB, không ngữ cảnh lệnh, không đồng hồ — chỉ thư viện chuẩn (``tests/test_timekeeping.py`` đọc AST).

Đầu vào (đã đọc sẵn, giờ công ty — 0169): lịch làm việc (ngày trong tuần, giờ vào / ra, nghỉ giữa ca, phút tha), cách tính công (``punch`` theo giờ vào / ra,
``exception`` đủ công theo lịch chỉ trừ ngoại lệ), ngày lễ hưởng lương (bộ quy định ``vn-labour``), giờ chấm ĐƯỢC TÍNH (không cờ, hoặc quản lý đã nhận;
giờ bị bỏ không vào đây — giờ còn cờ chưa xét vẫn tính nhưng đánh dấu), đơn nghỉ đã duyệt theo ngày, giờ làm thêm đã duyệt theo ngày. Kết quả mỗi người
mỗi ngày + tổng tháng. Không lưu: lưu bảng công là hai nơi giữ một sự thật; chỉ TỔNG lúc chốt được ghi (``close_lines``) kèm mã băm đầu vào.
"""

from datetime import date, datetime, timedelta
from typing import Any

OT_CLASS = {"weekday": "ot.150", "rest": "ot.200", "holiday": "ot.300"}   # hệ số làm thêm theo loại ngày (mức tiền ở bộ quy định lương)


def _minutes(t: str) -> int:
    h, m = t.split(":")[:2]
    return int(h) * 60 + int(m)


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def day_sheet(day: date, *, pattern: dict[str, Any], mode: str, holidays: dict[str, str], punches: list[dict[str, Any]],
              leave: dict[str, Any] | None, ot_minutes: int) -> dict[str, Any]:
    """Một ngày của một người. ``punches``: [{at: datetime giờ công ty, direction, flagged}] của ĐÚNG ngày này; ``leave``: {type, paid_by, fraction} hoặc None."""
    iso = day.isoformat()
    holiday = holidays.get(iso, "")
    scheduled = day.isoweekday() in pattern["weekdays"] and not holiday
    start, end = _minutes(pattern["starts"]), _minutes(pattern["ends"])
    span = end - start - int(pattern["break_minutes"])
    out: dict[str, Any] = {"day": iso, "weekday": day.isoweekday(), "scheduled": scheduled, "holiday": holiday, "status": "off", "paid": 0.0,
                           "unpaid": 0.0, "worked_minutes": 0, "late_minutes": 0, "early_minutes": 0, "first_in": "", "last_out": "",
                           "leave_type": "", "leave_paid_by": "", "ot_class": "", "ot_minutes": 0, "flags": []}
    ins = sorted(p["at"] for p in punches if p["direction"] == "in")
    outs = sorted(p["at"] for p in punches if p["direction"] == "out")
    if any(p.get("flagged") for p in punches):
        out["flags"].append("unreviewed")
    if ins:
        out["first_in"] = f"{ins[0]:%H:%M}"
    if outs:
        out["last_out"] = f"{outs[-1]:%H:%M}"
    worked = 0
    if ins and outs and outs[-1] > ins[0]:
        gross = int((outs[-1] - ins[0]).total_seconds() // 60)
        worked = gross - (int(pattern["break_minutes"]) if gross > span // 2 else 0)
    out["worked_minutes"] = max(worked, 0)
    if holiday and day.isoweekday() in pattern["weekdays"]:
        out.update(status="holiday", paid=1.0)
    elif scheduled:
        frac = float(leave["fraction"]) if leave else 0.0
        if leave:
            out.update(leave_type=leave["type"], leave_paid_by=leave["paid_by"])
            if leave["paid_by"] == "company":
                out["paid"] += frac
            else:
                out["unpaid"] += frac
        rest = 1.0 - frac
        if rest > 0:
            if mode == "exception":
                out["paid"] += rest
                out["status"] = "leave" if leave else "work"
            elif ins and outs and outs[-1] > ins[0]:
                out["paid"] += rest
                out["status"] = "work"
                late = _minutes(f"{ins[0]:%H:%M}") - start
                if late > int(pattern["grace_minutes"]) and not (leave and frac >= 0.5):
                    out["late_minutes"] = late
                early = end - _minutes(f"{outs[-1]:%H:%M}")
                if early > 0 and not (leave and frac >= 0.5):
                    out["early_minutes"] = early
            elif ins or outs:   # chấm vào mà không ra (hoặc ngược lại) ⇒ CHƯA ĐỦ: không tính công tới khi bổ sung giờ được duyệt (review 10/11)
                out["status"] = "incomplete"
                out["flags"].append("missing_out" if ins else "missing_in")
            else:
                out["unpaid"] += rest
                out["status"] = "absent"
        else:
            out["status"] = "leave"
    # làm thêm: chỉ phần ĐÃ DUYỆT; theo giờ chấm thì không quá giờ làm thực tế ngoài lịch (ngày nghỉ / lễ: cả giờ làm)
    if ot_minutes:
        kind = "holiday" if holiday else ("weekday" if day.isoweekday() in pattern["weekdays"] else "rest")
        beyond = out["worked_minutes"] - (span if kind == "weekday" else 0)
        got = ot_minutes if mode == "exception" else max(0, min(ot_minutes, beyond))
        if got:
            out.update(ot_class=OT_CLASS[kind], ot_minutes=got)
        if got < ot_minutes:
            out["flags"].append("ot_not_worked")
    return out


def segment_on(segments: list[dict[str, Any]], day: date) -> dict[str, Any] | None:
    """Dòng cách chấm hiệu lực ngày ``day`` (dòng có ``from`` mới nhất không sau ngày đó); None = chưa đăng ký."""
    hit = None
    for seg in segments:
        if seg["from"] <= day:
            hit = seg
    return hit


def month_sheet(month: date, *, until: date, segments: list[dict[str, Any]], holidays: dict[str, str], punches: list[dict[str, Any]],
                leave: dict[str, dict[str, Any]], overtime: dict[str, int]) -> dict[str, Any]:
    """Một tháng của một người tới ngày ``until`` (bảng công đang chạy) hoặc cuối tháng (chốt). ``segments``: các dòng cách chấm [{from, pattern, mode}]
    xếp theo ngày — mỗi ngày tính theo dòng hiệu lực ngày đó (đổi giữa tháng, nghỉ việc = ``off`` giữa tháng vẫn giữ những ngày đã làm). Ngày trước dòng
    đầu tiên / dòng ``off``: không tính công. ``std_days`` = ngày công CHUẨN của cả tháng theo lịch (ngày làm việc trong tuần, kể cả ngày lễ hưởng lương,
    kể cả ngày trước khi vào làm / sau khi nghỉ — theo lịch của dòng gần nhất): mẫu số của lương theo ngày công (review 10/11). Trả {days, totals}."""
    first = month.replace(day=1)
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    by_day: dict[str, list[dict[str, Any]]] = {}
    for p in punches:
        at: datetime = p["at"]
        by_day.setdefault(at.date().isoformat(), []).append(p)
    days = []
    std = 0
    for d in _days(first, last):
        seg = segment_on(segments, d) or (segments[0] if segments else None)
        std += bool(seg) and d.isoweekday() in seg["pattern"]["weekdays"]   # type: ignore[index]
        live = segment_on(segments, d)
        if d > until or live is None or live["mode"] == "off":
            continue
        iso = d.isoformat()
        days.append(day_sheet(d, pattern=live["pattern"], mode=live["mode"], holidays=holidays, punches=by_day.get(iso, []), leave=leave.get(iso),
                              ot_minutes=int(overtime.get(iso, 0))))
    t: dict[str, Any] = {"std_days": std, "paid_days": 0.0, "unpaid_days": 0.0, "worked_days": 0, "absent_days": 0, "holiday_days": 0, "late_count": 0,
                         "late_minutes": 0, "early_count": 0, "missing_out": 0, "incomplete": [], "unreviewed": 0, "leave": {}, "ot": {}, "ot_not_worked": 0}
    for x in days:
        t["paid_days"] += x["paid"]
        t["unpaid_days"] += x["unpaid"]
        t["worked_days"] += x["status"] == "work"
        t["absent_days"] += x["status"] == "absent"
        t["holiday_days"] += x["status"] == "holiday"
        t["late_count"] += x["late_minutes"] > 0
        t["late_minutes"] += x["late_minutes"]
        t["early_count"] += x["early_minutes"] > 0
        t["missing_out"] += x["status"] == "incomplete"
        if x["status"] == "incomplete":
            t["incomplete"].append(x["day"])
        t["unreviewed"] += "unreviewed" in x["flags"]
        t["ot_not_worked"] += "ot_not_worked" in x["flags"]
        if x["leave_type"]:
            t["leave"][x["leave_type"]] = t["leave"].get(x["leave_type"], 0.0) + (x["paid"] if x["leave_paid_by"] == "company" else x["unpaid"])
        if x["ot_class"]:
            t["ot"][x["ot_class"]] = t["ot"].get(x["ot_class"], 0) + x["ot_minutes"]
    return {"days": days, "totals": t}


def working_days(start: date, end: date, *, weekdays: list[int], holidays: dict[str, str]) -> list[date]:
    """Ngày làm việc theo lịch trong khoảng (bỏ ngày nghỉ hằng tuần và ngày lễ) — số ngày của một đơn nghỉ."""
    return [d for d in _days(start, end) if d.isoweekday() in weekdays and d.isoformat() not in holidays]
