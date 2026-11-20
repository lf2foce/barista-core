"""SỐ KIỂU VIỆT cho trang gửi khách (0224 §8) — MỘT bộ định dạng dùng chung cho bố cục (``layout.sections``), trang web (``html``), Word (``render``) và màn xem trước.

Chỉ là HIỂN THỊ: giá trị gốc nằm nguyên trong khối số liệu (ảnh chụp truy vấn) và bộ kiểm số (``check``) vẫn so với giá trị gốc đó — "2,85 tỷ" in ra là cách đọc của
2.850.000.000, không phải một con số khác. Quy tắc:

* tiền: ≥ 1 tỷ ⇒ "2,85 tỷ" (tối đa 2 số lẻ — cùng cách làm tròn ``vnd_text`` của lõi); ≥ 1 triệu ⇒ "850 triệu" (tối đa 1 số lẻ); nhỏ hơn ⇒ "125.000 đ".
  Khoảng: "2,85 – 3,01 tỷ" (đơn vị nêu một lần khi cùng đơn vị), "850 triệu – 1,2 tỷ" (khác đơn vị), hai đầu bằng nhau ⇒ một giá.
* diện tích: "48 m²", "48,5 m²"; khoảng "45 – 62 m²"; hai đầu bằng nhau ⇒ "48 m²".
* tỷ lệ: "5%", "12,5%".

Khối số liệu nêu cặp "từ – đến" bằng gợi ý TRÊN cột / ô (``range_to``: khoá cột / nhãn ô đầu kia; ``unit``: đơn vị đi sau số) — gói ngành khai, lõi không đoán theo tên cột.
Kiểu hiển thị chọn theo trang mẫu (``layout.TEMPLATES[...]["style"]``): ``vn`` ⇒ quy tắc trên; trống ⇒ in đủ từng đồng như trước (báo cáo nội bộ cần số đủ)."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from barista.api import phone_display

BILLION = Decimal(10) ** 9
MILLION = Decimal(10) ** 6
STYLE_VN = "vn"


def _dec(v: Any) -> Decimal | None:
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def num(d: Decimal, places: int = 2) -> str:
    """Số kiểu Việt: dấu chấm ngăn nghìn, dấu phẩy thập phân, bỏ số 0 thừa ("2,85", "1.250", "48")."""
    q = d.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    whole, _, frac = f"{abs(q):f}".partition(".")
    out = f"{int(whole):,}".replace(",", ".")
    frac = frac.rstrip("0")
    return ("-" if q < 0 else "") + out + ("," + frac if frac else "")


def _money_parts(d: Decimal) -> tuple[str, str]:
    """(số, đơn vị) của một số tiền: tỷ / triệu / đồng."""
    a = abs(d)
    if a >= BILLION or (a >= 999_500_000):   # 999,6 triệu làm tròn thành "1.000 triệu" ⇒ đọc là tỷ
        return num(d / BILLION, 2), "tỷ"
    if a >= MILLION:
        return num(d / MILLION, 1), "triệu"
    return num(d, 0), "đ"


def money(v: Any) -> str:
    """Tiền gọn kiểu Việt: 2850000000 ⇒ "2,85 tỷ" · 850000000 ⇒ "850 triệu" · 125000 ⇒ "125.000 đ". Không phải số ⇒ chữ nguyên."""
    d = _dec(v)
    if d is None:
        return str(v) if v not in (None, "") else "—"
    n, unit = _money_parts(d)
    return f"{n} {unit}"


def money_range(lo: Any, hi: Any) -> str:
    """Khoảng tiền: "2,85 – 3,01 tỷ" (cùng đơn vị ⇒ nêu một lần), "850 triệu – 1,2 tỷ", hai đầu bằng nhau ⇒ một giá. Thiếu một đầu ⇒ in đầu còn lại."""
    a, b = _dec(lo), _dec(hi)
    if a is None or b is None:
        return money(lo if b is None else hi)
    na, ua = _money_parts(a)
    nb, ub = _money_parts(b)
    if (na, ua) == (nb, ub):
        return f"{na} {ua}"
    return f"{na} – {nb} {ub}" if ua == ub else f"{na} {ua} – {nb} {ub}"


def number(v: Any, unit: str = "") -> str:
    """Số thường kiểu Việt (tối đa 2 số lẻ) kèm đơn vị: 48 ⇒ "48 m²"."""
    d = _dec(v)
    if d is None:
        return str(v) if v not in (None, "") else "—"
    return f"{num(d)} {unit}".strip()


def number_range(lo: Any, hi: Any, unit: str = "") -> str:
    """"45 – 62 m²"; hai đầu bằng nhau ⇒ "48 m²"."""
    a, b = _dec(lo), _dec(hi)
    if a is None or b is None:
        return number(lo if b is None else hi, unit)
    if num(a) == num(b):
        return number(a, unit)
    return f"{num(a)} – {num(b)} {unit}".strip()


def percent(v: Any) -> str:
    d = _dec(v)
    if d is None:
        return str(v) if v not in (None, "") else "—"
    return num(d, 1) + "%"


def cell(v: Any, fmt: str, *, unit: str = "") -> str:
    """Một giá trị theo kiểu ``vn`` (``fmt``: money · number · percent; kiểu khác ⇒ chữ)."""
    if fmt == "money":
        return money(v)
    if fmt == "number":
        return number(v, unit)
    if fmt == "percent":
        return percent(v)
    if fmt == "phone" and v not in (None, ""):   # SĐT lưu E.164 ⇒ chữ theo quốc gia công ty (thẻ liên hệ của tờ giới thiệu)
        return phone_display(str(v))
    return str(v) if v not in (None, "") else "—"


def pair(fmt: str, lo: Any, hi: Any, unit: str = "") -> str:
    return money_range(lo, hi) if fmt == "money" else number_range(lo, hi, unit)


# ── Khối số liệu ⇒ bảng / ô nổi bật ĐÃ định dạng (cùng một nơi cho HTML, Word, web) ──

def tiles(kpis: list[dict[str, Any]], fmt_plain: Any, style: str, *, skip_values: tuple[str, ...] = ()) -> list[dict[str, str]]:
    """Các ô số của khối ⇒ ``[{label, value}]``. Kiểu ``vn``: ô có ``range_to`` (nhãn ô đầu kia) hợp với ô đó thành một khoảng ("Giá" 2,85 – 3,01 tỷ), ô thành
    viên thứ hai không in riêng. ``skip_values``: giá trị bị bỏ (vd tên dự án đã là tiêu đề bìa). ``fmt_plain(value, format)``: cách in cũ."""
    by_label = {str(k.get("label", "")): k for k in kpis}
    partner = {str(k["range_to"]) for k in kpis if k.get("range_to") in by_label} if style == STYLE_VN else set()
    out: list[dict[str, str]] = []
    for k in kpis:
        label, f = str(k.get("label", "")), k.get("format", "number")
        if label in partner:
            continue
        if style == STYLE_VN and k.get("range_to") in by_label:
            other = by_label[str(k["range_to"])]
            value = pair(f, k.get("value"), other.get("value"), str(k.get("unit") or ""))
            label = str(k.get("range_label") or label.removesuffix(" từ").strip())
        else:
            value = cell(k.get("value"), f, unit=str(k.get("unit") or "")) if style == STYLE_VN else fmt_plain(k.get("value"), f)
        if value in skip_values:
            continue
        out.append({"label": label, "value": value})
    return out


def table(data: dict[str, Any], fmt_plain: Any, style: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Bảng của khối ⇒ (cột hiển thị, dòng đã in chữ). Kiểu ``vn``: cột có ``range_to`` hợp với cột kia thành một cột khoảng; ``unit`` đi sau số.
    Cột hiển thị: ``{key, label, num}``; dòng: ``{key: chữ}``."""
    cols = data.get("columns") or []
    keys = {c["key"] for c in cols}
    partner = {c["range_to"] for c in cols if c.get("range_to") in keys} if style == STYLE_VN else set()
    show = [c for c in cols if c["key"] not in partner]
    view = [{"key": c["key"], "label": (c.get("range_label") or c["label"]) if c.get("range_to") in partner else c["label"],
             "num": c.get("format") in ("money", "number", "percent")} for c in show]
    rows = []
    for r in data.get("rows") or []:
        out = {}
        for c in show:
            f, unit = c.get("format", "text"), str(c.get("unit") or "")
            if style == STYLE_VN and c.get("range_to") in keys:
                out[c["key"]] = pair(f, r.get(c["key"]), r.get(c["range_to"]), unit)
            elif style == STYLE_VN:
                out[c["key"]] = cell(r.get(c["key"]), f, unit=unit)
            else:
                out[c["key"]] = fmt_plain(r.get(c["key"]), f)
        rows.append(out)
    return view, rows


def plan(data: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Khối là LỊCH THANH TOÁN theo đợt (có cột tỷ lệ % + một cột nhãn chữ, 2–8 dòng, mỗi tỷ lệ 0–100) ⇒ ``[{label, pct, text, after}]`` để vẽ thanh mốc; không phải ⇒ ``None``
    (vẽ thành bảng). ``after``: số ngày kể từ mốc đầu (cột số có chữ "sau" trong nhãn) — chỉ để ghi chú dưới mốc."""
    cols, rows = data.get("columns") or [], data.get("rows") or []
    pct = next((c for c in cols if c.get("format") == "percent"), None)
    label = next((c for c in cols if c.get("format", "text") == "text"), None)
    if pct is None or label is None or not 2 <= len(rows) <= 8:
        return None
    after = next((c for c in cols if c.get("format") == "number" and "sau" in str(c.get("label", "")).lower()), None)
    out = []
    for r in rows:
        d = _dec(r.get(pct["key"]))
        if d is None or not 0 < d <= 100:
            return None
        days = _dec(r.get(after["key"])) if after else None
        out.append({"label": str(r.get(label["key"]) or ""), "pct": float(d), "text": percent(d),
                    "after": f"Sau {num(days, 0)} ngày" if days is not None and days > 0 else ""})
    return out
