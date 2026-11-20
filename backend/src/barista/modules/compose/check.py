"""KIỂM SỐ trong chữ AI viết (code, không model): mỗi cụm chữ số trong khối đoạn văn AI viết phải khớp một giá trị của khối số liệu trong
CÙNG bản soạn (kết quả truy vấn chạy bằng quyền người soạn). Nới tay: năm (1900–2100), số thứ tự nhỏ sau chữ chỉ thứ tự ("quý 3", "bước 2",
"slide 4"), ngày khớp một ngày trong số liệu hoặc ngày soạn. Khớp có làm tròn: "4,5 tỷ" khớp 4.512.000.000 vì sai khác ≤ nửa đơn vị của chữ số
cuối người viết ghi (0,05 tỷ). Chỗ giữ "[cần số liệu: …]" không bị kiểm — đó là cách đúng khi không có truy vấn nào cho con số cần."""

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

PLACEHOLDER = re.compile(r"\[cần số liệu:[^\]]*\]", re.IGNORECASE)
_UNITS = {"tỷ": Decimal(10) ** 9, "tỉ": Decimal(10) ** 9, "triệu": Decimal(10) ** 6, "tr": Decimal(10) ** 6, "nghìn": Decimal(1000),
          "ngàn": Decimal(1000), "k": Decimal(1000)}
_TOKEN = re.compile(r"(?<![\w])(\d[\d.,]*\d|\d)(\s*(?:tỷ|tỉ|triệu|tr|nghìn|ngàn|k)\b)?(\s*%)?", re.IGNORECASE)
_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\b")
_ORDINAL = re.compile(r"(?:thứ|bước|phần|mục|giai đoạn|quý|tháng|kỳ|lần|slide|trang|chương|điều|khoản|số|top|q|t)\s*$", re.IGNORECASE)
ORDINAL_MAX = 12


def placeholders(text: str) -> list[str]:
    return [m.group(0) for m in PLACEHOLDER.finditer(text)]


def _readings(tok: str) -> list[tuple[Decimal, int]]:
    """Cách hiểu một cụm chữ số (chấm / phẩy có thể là nghìn hoặc thập phân) kèm số chữ số thập phân người viết ghi."""
    out: list[tuple[Decimal, int]] = []
    for s in (tok.replace(".", "").replace(",", "."), tok.replace(",", "")):
        try:
            d = Decimal(s)
        except InvalidOperation:
            continue
        decimals = len(s.split(".", 1)[1]) if "." in s else 0
        if (d, decimals) not in out:
            out.append((d, decimals))
    return out


def values_of(snapshot: dict[str, Any]) -> set[Decimal]:
    """Mọi giá trị số trong một khối số liệu: ô của bảng, ô số, số dòng, chữ số trong ô chữ (mã hàng "12A01"…)."""
    out: set[Decimal] = set()

    def add(v: Any) -> None:
        if isinstance(v, bool) or v is None:
            return
        if isinstance(v, int | float | Decimal):
            out.add(Decimal(str(v)))
        elif isinstance(v, str):
            try:
                out.add(Decimal(v))
                return
            except InvalidOperation:
                pass
            for m in re.findall(r"\d[\d.,]*\d|\d", v):
                for d, _ in _readings(m):
                    out.add(d)
    for r in snapshot.get("rows") or []:
        for v in r.values():
            add(v)
    for k in snapshot.get("kpis") or []:
        add(k.get("value"))
    for t in snapshot.get("texts") or []:
        add(t.get("text"))   # chữ nguyên văn của bản ghi (chính sách…): con số trong đó là số của dữ liệu — đoạn dẫn nhắc lại được
    add(len(snapshot.get("rows") or []))
    add(snapshot.get("total_rows"))
    return out


def dates_of(snapshot: dict[str, Any]) -> set[date]:
    out: set[date] = set()
    cells = [v for r in snapshot.get("rows") or [] for v in r.values()] + [k.get("value") for k in snapshot.get("kpis") or []]
    for v in cells:
        if isinstance(v, str) and re.match(r"^\d{4}-\d{2}-\d{2}", v):
            try:
                out.add(date.fromisoformat(v[:10]))
            except ValueError:
                pass
    return out


def unmatched(text: str, values: set[Decimal], days: set[date], today: date) -> list[str]:
    """Các cụm số trong ``text`` không khớp giá trị nào (danh sách để báo, theo thứ tự xuất hiện, không trùng)."""
    body = PLACEHOLDER.sub(" ", text)
    bad: list[str] = []
    spans: list[tuple[int, int]] = []
    for m in _DATE.finditer(body):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3) or today.year)
        spans.append(m.span())
        try:
            when = date(y, mo, d)
        except ValueError:
            bad.append(m.group(0))
            continue
        if when != today and when not in days and not (m.group(3) is None and any((x.day, x.month) == (d, mo) for x in days)):
            bad.append(m.group(0))
    for m in _TOKEN.finditer(body):
        if any(a <= m.start() < b for a, b in spans):
            continue
        tok, unit, pct = m.group(1), (m.group(2) or "").strip().lower(), bool(m.group(3))
        plain = tok.isdigit()
        if plain and not unit and not pct and 1900 <= int(tok) <= 2100:
            continue   # năm
        if plain and not unit and not pct and int(tok) <= ORDINAL_MAX and _ORDINAL.search(body[:m.start()]):
            continue   # số thứ tự: "quý 3", "bước 2"
        mult = _UNITS.get(unit, Decimal(1))
        hit = False
        for d, decimals in _readings(tok):
            v = d * mult
            tol = (Decimal(5) / Decimal(10) ** (decimals + 1)) * mult if unit or pct else Decimal("0.5") / Decimal(10) ** decimals
            if any(abs(v - x) <= tol for x in values):
                hit = True
                break
        if not hit:
            shown = m.group(0).strip()
            if shown not in bad:
                bad.append(shown)
    return bad
