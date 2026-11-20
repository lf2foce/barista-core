"""HẠN NÓI BẰNG LỜI → NGÀY (0222). Hàm THUẦN: nhận câu người dùng nói ("trước thứ 6", "mai", "cuối tuần", "20/11") + hôm nay (theo múi giờ công ty —
bên gọi truyền ``today()``), trả ĐÚNG MỘT ngày hoặc ``None`` (không hiểu / mơ hồ ⇒ bên gọi hỏi lại, không đoán).

Luật (ghi rõ để người dùng đoán trước được):
* thứ trong tuần ("thứ 6", "thứ sáu", "t6", "chủ nhật") = lần xuất hiện KẾ TIẾP SAU hôm nay (nói vào thứ Sáu "thứ 6" nghĩa là thứ Sáu tuần sau — hôm nay thì nói "hôm nay");
  "thứ 6 tuần sau" / "tuần sau thứ 6" = thứ đó của tuần Thứ Hai–Chủ Nhật kế tiếp. Chữ "trước / đến / chậm nhất / muộn nhất / hạn" ở đầu bị bỏ — hạn chính là ngày đó.
* "hôm nay / nay" · "mai / ngày mai" · "mốt / ngày kia" · "N ngày nữa / sau N ngày / trong N ngày" · "N tuần nữa".
* "cuối tuần" = thứ Sáu của tuần này (việc nội bộ xong trước cuối tuần làm việc; qua thứ Sáu ⇒ thứ Sáu tuần sau) · "đầu tuần sau" = thứ Hai tuần sau · "tuần sau" = cùng thứ tuần sau ·
  "cuối tháng" = ngày cuối tháng (``_due`` lùi về ngày làm việc nếu rơi ngày nghỉ) · "cuối tháng sau".
* "20/11", "20-11", "20/11/2026", "ngày 20" (ngày 20 của tháng này, đã qua ⇒ tháng sau), "ngày 20 tháng 11" — ngày đã qua trong năm ⇒ năm sau (chỉ khi gõ dd/mm không năm).
"""

import calendar
import re
from datetime import date, timedelta

from barista.api import fold

_LEAD = re.compile(r"^(?:truoc|den|chay nhat|cham nhat|muon nhat|han|xong|vao|trong vong|truoc ngay|truoc khi het|den het)\s+")
_WEEKDAY_WORDS = {"hai": 0, "2": 0, "ba": 1, "3": 1, "tu": 2, "4": 2, "nam": 3, "5": 3, "sau": 4, "6": 4, "bay": 5, "7": 5}


def _weekday(text: str) -> int | None:
    """"thu 6" / "thu sau" / "t6" / "chu nhat" / "cn" ⇒ 0–6 (Thứ Hai = 0) hoặc None."""
    if re.search(r"\b(?:chu nhat|cn)\b", text):
        return 6
    m = re.search(r"\b(?:thu|t)\s*(hai|ba|tu|nam|sau|bay|[2-7])\b", text)
    return _WEEKDAY_WORDS[m.group(1)] if m else None


def _next_weekday(today: date, wd: int, *, next_week: bool) -> date:
    if next_week:   # tuần Thứ Hai–Chủ Nhật kế tiếp
        return today - timedelta(days=today.weekday()) + timedelta(days=7 + wd)
    return today + timedelta(days=(wd - today.weekday()) % 7 or 7)


def _add_months(d: date, n: int) -> tuple[int, int]:
    idx = d.year * 12 + d.month - 1 + n
    return idx // 12, idx % 12 + 1


def _safe(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_due_phrase(text: str, today: date) -> date | None:
    t = fold(text).strip().strip(".,;!?")
    if not t:
        return None
    t = re.sub(r"\s+", " ", t)
    while (m := _LEAD.match(t)):
        t = t[m.end():]
    # ngày cụ thể: dd/mm(/yyyy)
    if m := re.fullmatch(r"(?:ngay )?(\d{1,2})\s*[/.-]\s*(\d{1,2})(?:\s*[/.-]\s*(\d{2,4}))?", t):
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        if y:
            year = int(y) + (2000 if len(y) == 2 else 0)
            return _safe(year, mo, d)
        got = _safe(today.year, mo, d)
        if got is None:
            return None
        return got if got >= today else _safe(today.year + 1, mo, d)
    if m := re.fullmatch(r"ngay (\d{1,2}) thang (\d{1,2})(?: nam (\d{4}))?", t):
        d, mo = int(m.group(1)), int(m.group(2))
        if m.group(3):
            return _safe(int(m.group(3)), mo, d)
        got = _safe(today.year, mo, d)
        return None if got is None else (got if got >= today else _safe(today.year + 1, mo, d))
    if m := re.fullmatch(r"ngay (\d{1,2})", t):   # ngày d của tháng này; đã qua ⇒ tháng sau
        d = int(m.group(1))
        got = _safe(today.year, today.month, d)
        if got is not None and got >= today:
            return got
        y, mo = _add_months(today, 1)
        return _safe(y, mo, d)
    if t in ("hom nay", "nay", "trong ngay", "ngay hom nay"):
        return today
    if t in ("mai", "ngay mai", "sang mai", "chieu mai"):
        return today + timedelta(days=1)
    if t in ("mot", "ngay mot", "ngay kia"):
        return today + timedelta(days=2)
    if m := re.fullmatch(r"(?:sau |trong )?(\d{1,3}) (ngay|tuan)(?: nua| toi)?", t):
        n = int(m.group(1))
        return today + timedelta(days=n * (7 if m.group(2) == "tuan" else 1))
    if t in ("cuoi tuan", "cuoi tuan nay"):
        fri = today - timedelta(days=today.weekday()) + timedelta(days=4)
        return fri if fri >= today else fri + timedelta(days=7)
    if t in ("dau tuan sau", "dau tuan toi"):
        return today - timedelta(days=today.weekday()) + timedelta(days=7)
    if t in ("cuoi thang", "cuoi thang nay"):
        last = calendar.monthrange(today.year, today.month)[1]
        end = date(today.year, today.month, last)
        if end >= today:
            return end
        y, mo = _add_months(today, 1)
        return date(y, mo, calendar.monthrange(y, mo)[1])
    if t in ("cuoi thang sau", "cuoi thang toi"):
        y, mo = _add_months(today, 1)
        return date(y, mo, calendar.monthrange(y, mo)[1])
    next_week = bool(re.search(r"\btuan (?:sau|toi|ke tiep)\b", t))
    wd = _weekday(re.sub(r"\btuan (?:sau|toi|ke tiep)\b", "", t))
    if wd is not None:
        return _next_weekday(today, wd, next_week=next_week)
    if t in ("tuan sau", "tuan toi"):
        return today + timedelta(days=7)
    return None
