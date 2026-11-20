"""TRÍCH TRƯỜNG CÓ NGUỒN (quyết định 0070) — phần THUẦN của lõi: khuôn chứng từ do gói khai + đọc lại giá trị từ câu gốc bằng code.

Gói khai loại chứng từ (``Module.doc_type``): tên, nhãn, các Ô ``F(key, nhãn, kiểu)``. Model điền mỗi ô bằng {giá trị, trang, câu nguyên văn};
``kb.extract`` kiểm hai lớp: (a) câu có nguyên văn trên đúng trang (kb), (b) ``read_back`` — CODE đọc lại câu ra đúng giá trị đó. Trượt một lớp ⇒
ô "chưa kiểm": vẫn hiện, không dùng được cho bộ kiểm bằng code tới khi người xác nhận. Lỗi (b) bắt là lỗi hay gặp nhất của model đọc chứng từ:
trích đúng câu nhưng chép sai số ("USD 459,000.00" ⇒ 495000).

Không DB, không model, không module — test được độc lập; module kb làm phần đọc tài liệu / gọi model / lưu.
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from barista.kernel.text import fold

Kind = Literal["text", "number", "amount", "date", "list"]
KINDS: dict[str, str] = {"text": "chữ", "number": "số", "amount": "số tiền + đồng tiền", "date": "ngày", "list": "danh sách"}


@dataclass(frozen=True)
class F:
    """Một ô của khuôn chứng từ. ``help``: dặn model ô này lấy ở đâu (vd "ngày ghi chú SHIPPED ON BOARD, không phải ngày phát hành")."""
    key: str
    label: str
    kind: Kind = "text"
    help: str = ""


@dataclass(frozen=True)
class DocType:
    key: str            # "<module>.<tên>" — vd "invoicing.invoice"
    label: str
    module: str
    fields: tuple[F, ...]
    help: str = ""

    def field(self, key: str) -> F | None:
        return next((f for f in self.fields if f.key == key), None)


# ── Chuẩn hoá chữ để so nguyên văn (như kb.quote: bỏ dấu, chữ thường, bỏ ký hiệu markdown, gộp khoảng trắng) ──

def norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*_#|`>]", " ", fold(s))).strip()


# ── Số: một cụm chữ số trong câu có thể hiểu hai kiểu (1,700 · 102.000 · 450000,00) ⇒ tập các cách hiểu ──

_NUM = re.compile(r"\d[\d.,]*\d|\d")


def _number_readings(tok: str) -> set[Decimal]:
    out: set[Decimal] = set()
    for s in (tok.replace(",", ""), tok.replace(".", "").replace(",", ".")):   # phẩy = nghìn · chấm = nghìn, phẩy = thập phân
        try:
            out.add(Decimal(s))
        except InvalidOperation:
            continue
    return out


def numbers_in(text: str) -> set[Decimal]:
    out: set[Decimal] = set()
    for tok in _NUM.findall(text):
        out |= _number_readings(tok)
    return out


def parse_number(value: str) -> Decimal | None:
    """Giá trị model / người đưa: "459000.00" (chuẩn) — chấp nhận cả "459,000.00"."""
    v = value.strip().replace(" ", "")
    for s in (v, v.replace(",", "")):
        try:
            return Decimal(s)
        except InvalidOperation:
            continue
    return None


# ── Tiền: mã đồng tiền + số ──

CURRENCY_WORDS = {"USD": ("usd", "us dollars", "us dollar", "us$"), "EUR": ("eur", "euro"), "VND": ("vnd", "dong", "₫"),
                  "JPY": ("jpy", "yen"), "CNY": ("cny", "yuan", "rmb")}
_AMOUNT = re.compile(r"^\s*([A-Za-z]{3})?\s*([\d.,]+)\s*([A-Za-z]{3})?\s*$")


def parse_amount(value: str) -> tuple[str, Decimal] | None:
    m = _AMOUNT.match(value)
    if not m or not (cur := (m.group(1) or m.group(3) or "").upper()):
        return None
    n = parse_number(m.group(2))
    return None if n is None else (cur, n)


def _has_currency(quote: str, cur: str) -> bool:
    q = norm(quote)
    words = CURRENCY_WORDS.get(cur, (cur.lower(),))
    return any(re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", q) for w in words)


# ── Ngày: các cách viết hay gặp trên chứng từ (21 NOV 2026 · November 21, 2026 · 21/11/2026 · 2026-11-21 · 261121 (điện SWIFT) ·
#    ngày 21 tháng 11 năm 2026) ⇒ tập ngày có trong câu ──

_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"


def _ok(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def dates_in(text: str) -> set[date]:
    t = fold(text)
    found: list[date | None] = []
    for d, mon, y in re.findall(rf"(?<!\d)(\d{{1,2}})(?:st|nd|rd|th)?[\s\-]*{_MON}[\s,\-]*(\d{{4}})", t):
        found.append(_ok(int(y), _MONTHS[mon], int(d)))
    for mon, d, y in re.findall(rf"{_MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})", t):
        found.append(_ok(int(y), _MONTHS[mon], int(d)))
    for y, m, d in re.findall(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)", t):
        found.append(_ok(int(y), int(m), int(d)))
    for a, b, y in re.findall(r"(?<!\d)(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})(?!\d)", t):
        found += [_ok(int(y), int(b), int(a)), _ok(int(y), int(a), int(b))]   # dd/mm (VN) và mm/dd (Mỹ) — giá trị phải trùng một trong hai
    for d, m, y in re.findall(r"ngay\s+(\d{1,2})\s+thang\s+(\d{1,2})\s+nam\s+(\d{4})", t):
        found.append(_ok(int(y), int(m), int(d)))
    for yy, m, d in re.findall(r"(?<![\d,.])(\d{2})(\d{2})(\d{2})(?![\d,.])", t):   # YYMMDD của điện SWIFT (:31C: 260915)
        found.append(_ok(2000 + int(yy), int(m), int(d)))
    return {x for x in found if x is not None}


def parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


# ── Đọc lại: giá trị chuẩn hoá + đọc lại được từ câu không ──

def canonical(kind: str, value: str) -> str | None:
    """Giá trị chuẩn để lưu / so: ngày ISO, số "102", tiền "USD 459000.00", chữ giữ nguyên. Không hiểu được ⇒ None."""
    v = value.strip()
    if not v:
        return None
    if kind == "date":
        d = parse_date(v)
        return d.isoformat() if d else None
    if kind == "number":
        n = parse_number(v)
        return None if n is None else _plain(n)
    if kind == "amount":
        a = parse_amount(v)
        return None if a is None else f"{a[0]} {_plain(a[1])}"
    return v


def _plain(n: Decimal) -> str:
    return format(n.normalize(), "f")


def read_back(kind: str, value: str, quote: str) -> str:
    """"" nếu CODE đọc lại câu ra đúng giá trị; không thì lý do (người đọc hiểu)."""
    if kind == "date":
        d = parse_date(value)
        if d is None:
            return "giá trị không phải ngày"
        return "" if d in dates_in(quote) else "câu gốc không có ngày này"
    if kind == "number":
        n = parse_number(value)
        if n is None:
            return "giá trị không phải số"
        return "" if n in numbers_in(quote) else "câu gốc không có số này"
    if kind == "amount":
        a = parse_amount(value)
        if a is None:
            return "giá trị phải là đồng tiền + số (vd USD 459000.00)"
        if not _has_currency(quote, a[0]):
            return f"câu gốc không có đồng tiền {a[0]}"
        return "" if a[1] in numbers_in(quote) else "câu gốc không có số tiền này"
    want = norm(value)   # chữ / mục danh sách: giá trị phải nằm nguyên văn trong câu
    return "" if want and want in norm(quote) else "giá trị không nằm nguyên văn trong câu gốc"
