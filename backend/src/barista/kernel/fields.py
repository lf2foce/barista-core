"""Kiểu nhập liệu dùng chung + mô tả trường cho form / mẫu Excel.

Một model Pydantic là nguồn DUY NHẤT cho: kiểm dữ liệu lệnh, form trên app (``/v1/forms``), cột mẫu Excel, và schema
cho agent sau này. Nhãn tiếng Việt + loại hiển thị khai một lần bằng ``field(...)``.

Kiểu chấp nhận cách người Việt gõ / dán từ Excel: tiền "4.500.000.000", "4,5 tỷ", "90tr"; ngày "05/11/2026";
SĐT "+84 912…"; phần trăm "2%" / "2,5".
"""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


class Input(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class NoInput(Input):
    pass


class OptionsIn(Input):
    """Đầu vào của truy vấn chọn (``options_query``) có TÌM theo từ khoá (1.61): ``q`` = chữ người gõ vào ô chọn (trống = danh sách đầu / gần dùng),
    ``limit`` = tối đa bao nhiêu dòng. Truy vấn có danh sách DÀI (khách, căn, người) kế thừa lớp này và lọc ở SQL (``SearchTerms``); truy vấn chọn
    không khai ``q`` / ``limit`` vẫn chạy như cũ — cửa ``/v1/options`` lọc / cắt thay (``ui.narrow_options``). Trả ``[{value, label, hint?}]``."""
    q: str = ""
    limit: int = 50


_UNITS = sorted(
    [("triệu", 10**6), ("trieu", 10**6), ("tỷ", 10**9), ("tỉ", 10**9), ("ty", 10**9), ("tr", 10**6), ("k", 10**3),
     ("vnđ", 1), ("vnd", 1), ("đ", 1)],
    key=lambda u: -len(u[0]),
)


def parse_money(v: Any) -> Any:
    if v is None or isinstance(v, bool | int):
        return v
    if isinstance(v, float | Decimal):
        return round(v)
    if not isinstance(v, str):
        return v
    s = v.strip().lower().replace(" ", "")
    if not s:
        return None
    neg = s.startswith("-")
    s = s.lstrip("-")
    mult = 1
    for suffix, m in _UNITS:
        if s.endswith(suffix):
            s, mult = s[: -len(suffix)], m
            break
    if mult >= 1000:  # "4,5 tỷ" — dấu phẩy / chấm là phần thập phân
        s = s.replace(",", ".")
        if not re.fullmatch(r"\d+(\.\d+)?", s):
            raise ValueError(f"số tiền không đọc được: {v!r}")
        n = round(float(s) * mult)
    else:  # "4.500.000.000" / "4,500,000,000" / "4500000000"
        if not re.fullmatch(r"\d{1,3}([.,]\d{3})+|\d+", s):
            raise ValueError(f"số tiền không đọc được: {v!r}")
        n = int(re.sub(r"[.,]", "", s))
    return -n if neg else n


def parse_date(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date) or not isinstance(v, str):
        return v
    s = v.strip()
    if not s:
        return None
    if m := re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", s):
        d, mo, y = map(int, m.groups())
        return date(y, mo, d)
    return date.fromisoformat(s[:10])


# Đầu số di động 11 chữ số cũ → đầu số 10 chữ số (Bộ TT&TT đổi 15/09–15/11/2018). Một khách đổi đầu số vẫn là MỘT khách: không đổi thì số cũ và số mới
# thành hai khoá gộp khác nhau (0197).
OLD_MOBILE_PREFIX = {"0162": "032", "0163": "033", "0164": "034", "0165": "035", "0166": "036", "0167": "037", "0168": "038", "0169": "039",
                     "0120": "070", "0121": "079", "0122": "077", "0126": "076", "0128": "078",
                     "0123": "083", "0124": "084", "0125": "085", "0127": "081", "0129": "082",
                     "0186": "056", "0188": "058", "0199": "059"}


# Mã nước của các nước lõi biết luật số NỘI ĐỊA (số không có ``+``): mã gọi quốc tế + số trung kế đầu số nội địa. Nước khác: người nhập ghi ``+<mã nước>``.
NATIONAL = {"VN": ("84", "0")}
DEFAULT_COUNTRY = "VN"
_country: ContextVar[str | None] = ContextVar("company_country", default=None)


def company_country() -> str:
    """Quốc gia công ty của giao dịch đang chạy (``db.tenant_tx`` đặt, 0199); ngoài giao dịch công ty ⇒ VN."""
    return _country.get() or DEFAULT_COUNTRY


@contextmanager
def using_country(c: str | None) -> Iterator[None]:
    token = _country.set(c)
    try:
        yield
    finally:
        _country.reset(token)


def parse_phone(v: Any) -> Any:
    """MỘT cách chuẩn hoá SĐT — cũng là khoá gộp khách nên đổi luật ở đây phải kèm migration chuẩn hoá lại số đã lưu (0197, 0199).

    Lưu MỌI số dạng E.164 ``+<mã nước><số>`` (0199) — hiển thị theo quốc gia công ty là việc của lớp hiện (``phone_display``), không của khoá.
    Số không có ``+`` / ``00`` đọc theo luật nội địa Việt Nam: ``0…`` → ``+84…``; ``84…`` 11–12 chữ số → ``+84…``; đầu số di động cũ 11 chữ số → đầu số
    mới; 9 chữ số di động mất số 0 đầu (Excel / Sheets coi là số) → thêm lại. Số nước ngoài CHỈ nhận khi người gửi nói rõ là quốc tế (``+`` hoặc ``00``
    đầu) — không đoán mã nước từ một dãy số trần (dãy 11 chữ số đầu 1 có thể là số Mỹ hoặc số Việt Nam gõ sai). Chỉ kiểm HÌNH DẠNG (độ dài, chữ số),
    không kiểm số có thật theo bảng đầu số của nhà mạng: số mẫu đầu 010 (bất biến 9) phải qua được."""
    if v is None or (isinstance(v, str) and not v.strip()):   # ô trống = không có số; ô bắt buộc vẫn báo thiếu (kiểu str không nhận None)
        return None
    raw = str(v).strip()
    digits = re.sub(r"\D", "", raw)
    international = raw.startswith("+") or digits.startswith("00")
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("84") and (international or len(digits) in (11, 12)):
        digits = "0" + digits[2:]
    elif international:
        if not re.fullmatch(r"[1-9]\d{7,14}", digits):
            raise ValueError(f"SĐT không hợp lệ: {v!r}")
        return "+" + digits
    if re.fullmatch(r"[35789]\d{8}", digits):
        digits = "0" + digits
    if len(digits) == 11 and digits[:4] in OLD_MOBILE_PREFIX:
        digits = OLD_MOBILE_PREFIX[digits[:4]] + digits[4:]
    if not re.fullmatch(r"0\d{9,10}", digits):
        raise ValueError(f"SĐT không hợp lệ: {v!r}")
    return "+84" + digits[1:]


def phone_display(v: str | None, country: str | None = None) -> str:
    """Số đã lưu (E.164) → chữ cho người đọc theo quốc gia công ty (0199, bổ sung 15/11/2026): số CÙNG nước ⇒ số nội địa liền, không dấu cách
    (``+84898654936`` → ``0898654936``); số nước khác ⇒ giữ nguyên ``+<mã nước><số>`` đủ mã; nước công ty chưa có luật nội địa ⇒ mọi số giữ ``+…``.
    Giá trị không phải E.164 (số đã che, dữ liệu cũ, chữ tự do) trả nguyên — hiện sai còn hơn giấu. MỘT hàm cho mọi chỗ đưa SĐT ra chữ người đọc
    (``web/src/lib/format.ts::phone`` giữ cùng luật); link ``tel:`` / Zalo vẫn dùng E.164 đã lưu."""
    s = v or ""
    code, trunk = NATIONAL.get(country or company_country(), ("", ""))
    if not (code and s.startswith("+" + code) and s[1:].isdigit()):
        return s
    return trunk + s[1 + len(code):]


def phone_search(q: str) -> str:
    """Chữ số để tìm trong SĐT đã lưu (``LIKE '%…%'``): người gõ ``0912…`` (nội địa) tìm được ``+84912…`` ⇒ bỏ số 0 trung kế đầu; ``+84`` / ``84`` gõ đủ vẫn
    khớp. Ít hơn 3 chữ số ⇒ chuỗi rỗng (không tìm theo số)."""
    d = re.sub(r"\D", "", q or "")
    if d.startswith("0") and not d.startswith("00"):
        d = d[1:]
    elif d.startswith("00"):
        d = d[2:]
    return d if len(d) >= 3 else ""


def parse_pct(v: Any) -> Any:
    if isinstance(v, str):
        s = v.strip().replace("%", "").replace(",", ".")
        return float(s) if s else None
    return v


Money = Annotated[int, BeforeValidator(parse_money)]
Day = Annotated[date, BeforeValidator(parse_date)]
Phone = Annotated[str, BeforeValidator(parse_phone)]
OptionalPhone = Annotated[str | None, BeforeValidator(parse_phone)]   # ô trống = không có số, không phải sai định dạng
Percent = Annotated[float, BeforeValidator(parse_pct)]

# "time" = giờ trong ngày "HH:MM" (24 giờ) · "month" = tháng "YYYY-MM": ô chữ ở phía lõi (giá trị vẫn là chuỗi, ô tự kiểm), nhưng app vẽ ô chọn giờ / tháng thay vì gõ chữ
# (1.49). App cũ chưa biết kiểu mới ⇒ vẽ ô chữ như trước.
KINDS = ("text", "textarea", "money", "number", "percent", "date", "phone", "email", "select", "multiselect", "bool", "id", "time", "month")
_VALIDATOR_KIND = {parse_money: "money", parse_date: "date", parse_phone: "phone", parse_pct: "percent"}


def field(
    label: str,
    default: Any = ...,
    *,
    kind: str | None = None,
    options: dict[str, str] | None = None,
    options_query: str | None = None,
    options_with: tuple[str, ...] = (),
    options_args: dict[str, str] | None = None,
    help: str = "",
    example: Any = None,
    hidden: bool = False,
    ge: float | None = None,
    gt: float | None = None,
    le: float | None = None,
    max_length: int | None = None,
    audit: bool = True,
    aliases: tuple[str, ...] = (),
    agent: bool = True,
    group: str = "",
    placeholder: str = "",
    primary: bool = False,
) -> Any:
    """Khai một trường: nhãn (cũng là tiêu đề cột Excel), loại hiển thị, lựa chọn, ví dụ cho mẫu Excel.
    ``audit=False``: nội dung không chép vào nhật ký thao tác (chỉ ghi cỡ + sha256) — file / ảnh mà quyền đọc thuộc nơi khác.
    ``aliases``: tên cột khác khi nhập Excel (sao kê ngân hàng ghi "Số tiền ghi có", "Diễn giải"…) — khớp như nhãn.
    ``options_with``: danh sách chọn (``options_query``) phụ thuộc vào các trường này của chính form (thường là trường ẩn điền sẵn, vd mã tin
    nhắn) — app gửi giá trị của chúng kèm lần tải danh sách (``GET /v1/options/{tên}?trường=giá trị``); server vẫn kiểm lại lúc chạy lệnh.
    ``options_args``: như ``options_with`` nhưng ĐỔI TÊN — ``{"tham_số_truy_vấn": "trường_của_form"}`` (vd ``{"project_code": "project"}``). Trường đó ĐANG HIỆN
    trên form (người chọn) ⇒ danh sách tải lại mỗi khi nó đổi và ô này bị xoá (giá trị cũ có thể không còn thuộc danh sách mới); chưa chọn trường đó ⇒ ô
    này chưa có danh sách (chờ). Truy vấn chọn nhận thêm ``q`` / ``limit`` (``OptionsIn``) khi danh sách dài (1.61).
    ``agent=False``: trường không có trong schema công cụ trợ lý thấy (người vẫn điền trên form / thẻ đề xuất) — trường hiếm dùng làm schema to ra,
    đẩy công cụ khỏi trần dung lượng (0095). Phải có giá trị mặc định.
    ``group``: nhóm GẤP LẠI của form / bộ lọc (vd "Thêm điều kiện") — ô cùng nhóm nằm sau một nút bấm mở ra, ô không khai nhóm luôn hiện; chỉ thay
    cách bày, không đổi cách kiểm dữ liệu. ``placeholder``: chữ mờ trong ô trống nói trống nghĩa là gì (vd "Hôm nay") — thay chữ chung "Tất cả".
    ``primary``: ô lọc CHÍNH của màn (kỳ / dự án / trạng thái / tìm — rà bố cục 18/11/2026): luôn nằm ngoài, cạnh tên màn; ô còn lại gom vào nút
    "Bộ lọc". Màn không khai ô chính nào ⇒ ``workspaces`` coi 2 ô đầu không thuộc nhóm gấp là chính. Chỉ thay cách bày."""
    assert agent or default is not ..., "trường agent=False cần giá trị mặc định"
    assert kind is None or kind in KINDS, kind
    ui = {"kind": kind, "options": options, "options_query": options_query, "options_with": list(options_with) or None, "options_args": dict(options_args) if options_args else None, "example": example,
          "hidden": hidden or None, "aliases": list(aliases) or None, "group": group or None, "placeholder": placeholder or None,
          "primary": primary or None}
    return Field(
        default,
        title=label,
        description=help or None,
        ge=ge,
        gt=gt,
        le=le,
        max_length=max_length,
        json_schema_extra={"ui": {k: v for k, v in ui.items() if v is not None}, **({} if audit else {"audit": False}),
                           **({} if agent else {"agent": False})},
    )


def validator_kind(metadata: list[Any]) -> str | None:
    for m in metadata:
        if isinstance(m, BeforeValidator) and (k := _VALIDATOR_KIND.get(m.func)):  # type: ignore[arg-type]
            return k
    return None


class MetricIn(Input):
    """Đầu vào chung của CHỈ SỐ (0074 B mục tiêu kế hoạch · 0101 KPI): đếm trong [since, until) — ``until`` trống = tới nay; lọc theo PHẠM VI
    nếu chỉ số hỗ trợ — mã do gói hiểu (dự án, gian hàng…; như phạm vi tài liệu — 0080), trống = cả công ty. ``group_by``: "" ⇒ ``{"value"}``;
    "person" / "team" ⇒ thêm ``{"rows": [{"key": id người / đội, "value"}]}`` (KPI, xếp hạng) — MỘT định nghĩa cho mỗi con số."""
    since: Day = field("Từ ngày")
    until: Day | None = field("Đến trước ngày", None)
    context_code: str = field("Phạm vi", "")
    group_by: Literal["", "person", "team"] = field("Nhóm theo", "")
