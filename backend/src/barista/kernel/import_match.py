"""NHẬP EXCEL TỰ KHỚP (quyết định 0226) — phần THUẦN: đọc mọi trang tính, tìm dòng tiêu đề, khớp cột của file vào trường của lệnh, chuẩn hoá ô lựa chọn.

Không DB, không model, không module — test được độc lập; ``kernel/imports.py`` làm phần chạy (đọc quyền, lưu ánh xạ, gọi model cho cột còn lại, chạy thử / nhập thật).

Nguyên tắc: máy ĐOÁN, người XÁC NHẬN. Mỗi cặp (trường ← cột) mang độ tin cậy + lý do ngắn; cặp dưới ngưỡng "chắc" hiện màu cảnh báo.
Khớp theo thứ tự rẻ → đắt: (1) ánh xạ đã nhớ của công ty · (2) tên cột (nhãn, khoá, tên khác, từ đồng nghĩa) · (3) nội dung cột (SĐT, email, ngày, tiền, mã có trong
danh sách chọn) · (4) model — CHỈ cho cột còn lại, chỉ gửi tiêu đề + vài ô mẫu (``ai_request``). Không gửi cả file.
"""

import hashlib
import itertools
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from difflib import SequenceMatcher
from io import BytesIO
from typing import Any

from openpyxl import load_workbook
from pydantic import BaseModel

from barista.kernel.fields import parse_phone
from barista.kernel.text import fold, name_key

MAX_ROWS = 50_000   # trần dòng đọc mỗi trang tính — file lớn hơn thì chia nhỏ
MAX_COLS = 80
HEADER_SCAN = 40    # tìm dòng tiêu đề trong chừng này dòng đầu
SAMPLE_N = 5        # ô mẫu mỗi cột gửi cho model / hiện trên màn khớp cột
SNIFF_N = 300       # số ô mỗi cột dùng để đoán kiểu nội dung

# Ngưỡng độ tin cậy: từ CHẮC trở lên màu xanh; từ GỢI Ý trở lên được chọn sẵn nhưng màu cảnh báo; dưới đó coi như chưa khớp.
SURE = 0.75
MAYBE = 0.5


# ── Đọc file ──

def blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def cell_text(v: Any) -> str:
    """Ô → chữ cho người đọc: số nguyên không đuôi .0, ngày dd/mm/yyyy."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "có" if v else "không"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, datetime):
        return v.strftime("%d/%m/%Y") if v.time() == datetime.min.time() else v.strftime("%d/%m/%Y %H:%M")
    if isinstance(v, date):
        return v.strftime("%d/%m/%Y")
    return str(v).strip()


@dataclass
class Sheet:
    name: str
    grid: list[list[Any]]


def read_sheets(data: bytes, read_csv: Any) -> list[Sheet]:
    """MỌI trang tính nhìn thấy được của file. Không phải .xlsx (không mở đầu bằng "PK") ⇒ một trang từ ``read_csv`` (UTF-8, ``,`` ``;`` hoặc tab)."""
    if not data.startswith(b"PK"):
        return [Sheet("Dữ liệu", _trim(read_csv(data)))]
    wb = load_workbook(BytesIO(data), read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        if getattr(ws, "sheet_state", "visible") != "visible":
            continue
        grid = _trim([list(r[:MAX_COLS]) for _, r in zip(range(MAX_ROWS), ws.iter_rows(values_only=True), strict=False)])
        if grid:
            out.append(Sheet(ws.title, grid))
    if not out:
        raise IndexError("file không có trang tính nào có dữ liệu")
    return out


def _trim(grid: list[list[Any]]) -> list[list[Any]]:
    """Bỏ các dòng trống ở cuối (file Excel hay khai báo vùng dữ liệu dài hơn thực tế)."""
    while grid and all(blank(v) for v in grid[-1]):
        grid.pop()
    return grid


# ── Dòng tiêu đề ──

@dataclass
class Header:
    row: int                      # chỉ số dòng (0 = dòng đầu của lưới); số dòng Excel = row + 1
    names: list[str]              # tiêu đề từng cột ("" = cột không tên)
    groups: list[str]             # tiêu đề nhóm (ô gộp ở dòng trên) — "" nếu không có
    first_data: int               # chỉ số dòng dữ liệu đầu tiên

    @property
    def line(self) -> int:
        return self.row + 1


_DATE_TXT = re.compile(r"^\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}$")
# "Mon 01/12/26", "Thứ Hai, 01/12/2026", "T2 01/12/2026" (MS Project, Excel) — thứ trong tuần ở đầu ô chỉ là chữ trang trí, bỏ đi trước khi đọc ngày
_WEEKDAY_HEAD = re.compile(r"^\s*(?:thứ\s*(?:hai|ba|tư|năm|sáu|bảy|[2-7])|chủ\s*nhật|cn|t[2-7]|mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?[,\s]+(?=\d)", re.I)


def unweekday(v: str) -> str:
    return _WEEKDAY_HEAD.sub("", v, count=1)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MONEY_UNIT = re.compile(r"^-?\d[\d.,]*\s*(tỷ|tỉ|ty|triệu|trieu|tr|k|đ|vnđ|vnd|d)$", re.I)
_PCT = re.compile(r"^-?\d+([.,]\d+)?\s*%$")
_NUMLIKE = re.compile(r"^-?\d+([.,]\d+)?$")
_THOUSANDS = re.compile(r"^-?\d{1,3}([.,]\d{3})+$")
_PHONELIKE = re.compile(r"^\+?[\d\s.()-]{9,18}$")
_BOOL_WORDS = {"co", "khong", "x", "yes", "no", "true", "false", "dung", "sai"}


def sniff(v: Any) -> set[str]:
    """Một ô trông giống những gì: ``phone`` ``email`` ``date`` ``money`` ``number`` ``percent`` ``bool`` ``text`` (một ô có thể thuộc nhiều — số 9 chữ số có thể
    là SĐT mất số 0 đầu hoặc là tiền)."""
    if blank(v):
        return set()
    if isinstance(v, bool):
        return {"bool"}
    if isinstance(v, datetime | date):
        return {"date"}
    if isinstance(v, int | float):
        out = {"number"}
        if float(v).is_integer():
            s = str(abs(int(v)))
            if abs(v) >= 1000:
                out.add("money")
            if SERIAL_RANGE[0] <= v <= SERIAL_RANGE[1]:
                out.add("serial")   # có thể là số ngày của Excel (ô ngày không gán định dạng ngày)
            if (len(s) == 9 and s[0] in "35789") or (len(s) in (11, 12) and s.startswith("84")):
                out.add("phone")   # Excel coi SĐT là số ⇒ mất số 0 đầu
        elif abs(v) <= 100:
            out.add("percent")
        return out
    s = str(v).strip()
    out = set()
    if _EMAIL.match(s):
        return {"email"}
    if _DATE_TXT.match(unweekday(s)):
        return {"date"}
    if _PCT.match(s):
        return {"percent"}
    if _MONEY_UNIT.match(s):
        return {"money"}
    if fold(s).strip() in _BOOL_WORDS:
        out.add("bool")
    if _PHONELIKE.match(s) and len(re.sub(r"\D", "", s)) >= 9:
        try:
            parse_phone(s)
            out.add("phone")
        except ValueError:
            pass
    if _THOUSANDS.match(s):
        out |= {"money", "number"}
    elif _NUMLIKE.match(s):
        out.add("number")
        if abs(float(s.replace(",", "."))) >= 1000:
            out.add("money")
    return out or {"text"}


def _is_texty(v: Any) -> bool:
    """Ô chữ thật (không phải số / ngày / SĐT / email) — thứ một dòng tiêu đề gồm toàn là."""
    if blank(v):
        return False
    return sniff(v) <= {"text", "bool"} and not isinstance(v, bool) and len(str(v).strip()) <= 60


_TOTAL_LABEL = re.compile(r"^(tong|tong cong|tong so|tong ket|tong gia tri|cong|cong chung|total|grand total|sub ?total)( .{0,24})?:?$")
IGNORE_HEADERS = {"stt", "tt", "no", "so tt", "so thu tu", "thu tu", "stt.", "#", "tt."}


def is_total_row(values: list[Any]) -> bool:
    """Dòng tổng cộng: một ô nhãn kiểu "Tổng cộng" + các ô còn lại là số hoặc trống (một khách tên "Tổng công ty ABC" có ô chữ khác ⇒ không bị nhầm)."""
    filled = [v for v in values if not blank(v)]
    if not filled or len(filled) > max(3, len(values) // 2 + 1):
        return False
    labels = [v for v in filled if isinstance(v, str) and _TOTAL_LABEL.match(fold(v).strip())]
    if not labels:
        return False
    others = [v for v in filled if v is not labels[0]]
    return all(isinstance(v, int | float) or (isinstance(v, str) and ("number" in sniff(v) or "money" in sniff(v))) for v in others)


def detect_header(grid: list[list[Any]], known: set[str] | None = None) -> Header | None:
    """Dòng tiêu đề của trang tính. Bỏ qua tên báo cáo / ngày lập ở đầu (một ô, ô gộp) và dòng trống; chọn dòng có nhiều ô CHỮ nhất, ưu tiên dòng chứa
    tên cột quen thuộc (``known`` = tên đã chuẩn hoá bằng ``name_key``: nhãn / khoá / từ đồng nghĩa của lệnh) và có dòng dữ liệu đúng bề rộng ngay dưới.
    Hai tầng tiêu đề (nhóm ô gộp ở dòng trên + tiêu đề con) ⇒ ghép "nhóm tiêu đề"."""
    known = known or set()
    best: tuple[float, int] | None = None
    ignore = {name_key(h) for h in IGNORE_HEADERS}
    multi = any(sum(1 for v in r if not blank(v)) >= 2 for r in grid[:HEADER_SCAN])   # có dòng nhiều ô ⇒ dòng một ô là tên báo cáo, không phải tiêu đề
    for i, row in enumerate(grid[:HEADER_SCAN]):
        filled = [v for v in row if not blank(v)]
        if len(filled) < (2 if multi else 1):
            continue
        texty = [v for v in filled if _is_texty(v)]
        if len(texty) < -(-4 * len(filled) // 5):   # ≥ 80% ô là chữ
            continue
        keys = [name_key(str(v)) for v in texty]
        if len(set(keys)) < len(keys) * 0.8:
            continue
        hits = sum(1 for k in keys if k in known or k in ignore)
        score = len(texty) + 2.5 * hits
        below = [r for r in grid[i + 1:i + 6] if any(not blank(v) for v in r)]
        if below:
            width = sum(1 for r in below if sum(1 for v in r if not blank(v)) >= 0.5 * len(filled)) / len(below)
            typed = sum(1 for r in below for v in r if not blank(v) and not _is_texty(v)) / max(1, sum(1 for r in below for v in r if not blank(v)))
            score += 2 * width + 1.5 * typed
        else:
            score -= 3
        if best is None or score > best[0] + 0.5:   # hoà ⇒ dòng đứng trước
            best = (score, i)
    return None if best is None else header_at(grid, best[1])


def header_at(grid: list[list[Any]], h: int) -> Header:
    """Tiêu đề lấy từ dòng ``h`` (chỉ số từ 0) — cả khi người chọn tay dòng tiêu đề. Dòng ngay trên có ≥ 2 ô chữ ít hơn tiêu đề (ô gộp nhóm) ⇒ ghép nhóm."""
    width = max((len(r) for r in grid[h:h + 6]), default=0)
    row = list(grid[h]) + [None] * (width - len(grid[h]))
    names = [cell_text(v) for v in row]
    groups = [""] * width
    prev = grid[h - 1] if h > 0 else []
    pf = [v for v in prev if not blank(v)]
    if len(pf) >= 2 and len(pf) < len([n for n in names if n]) and all(_is_texty(v) for v in pf):
        cur = ""
        for j in range(width):
            if j < len(prev) and not blank(prev[j]):
                cur = cell_text(prev[j])
            groups[j] = cur if (names[j] and cur != names[j]) else ""
    return Header(h, names, groups, h + 1)


@dataclass
class DataRows:
    rows: list[tuple[int, list[Any]]] = field(default_factory=list)   # (số dòng Excel, các ô)
    skipped: list[tuple[int, str]] = field(default_factory=list)      # (số dòng Excel, lý do) — dòng KHÔNG phải dữ liệu


def data_rows(grid: list[list[Any]], header: Header) -> DataRows:
    """Dòng dữ liệu dưới tiêu đề. Bỏ: dòng trống, dòng tổng cộng, tiêu đề lặp lại (ngắt trang), chân trang (dòng một ô sau dòng dữ liệu cuối)."""
    out = DataRows()
    heads = {name_key(n) for n in header.names if n}
    pending: list[tuple[int, list[Any]]] = []
    for i in range(header.first_data, len(grid)):
        row = grid[i]
        if all(blank(v) for v in row):
            continue
        line = i + 1
        if is_total_row(row):
            out.skipped.append((line, "dòng tổng cộng"))
            continue
        texts = [name_key(str(v)) for v in row if not blank(v) and isinstance(v, str)]
        if len(texts) >= 2 and sum(1 for t in texts if t in heads) >= max(2, len(texts) // 2):
            out.skipped.append((line, "tiêu đề lặp lại"))
            continue
        filled = sum(1 for v in row if not blank(v))
        if filled <= 1 and len(header.names) >= 4:
            pending.append((line, row))   # có thể là tên nhóm / chân trang — quyết khi biết sau nó còn dòng dữ liệu không
            continue
        for ln, r in pending:   # sau dòng một ô còn dữ liệu ⇒ nó là dòng dữ liệu thưa (chỉ có tên) — giữ lại
            out.rows.append((ln, r))
        pending = []
        out.rows.append((line, row))
    for ln, _ in pending:
        out.skipped.append((ln, "chân trang / dòng ghi chú"))
    return out


# ── Cột của file ──

@dataclass
class Column:
    index: int
    header: str                     # tiêu đề (hoặc "Cột C" khi trống mà có dữ liệu)
    group: str = ""
    samples: list[str] = field(default_factory=list)
    n: int = 0                      # số ô có dữ liệu
    frac: dict[str, float] = field(default_factory=dict)   # tỉ lệ ô theo kiểu nội dung
    distinct: int = 0
    ignore: str = ""                # lý do bỏ qua (số thứ tự…) — không khớp, không gửi model
    values: list[Any] = field(default_factory=list, repr=False)   # ô mẫu thô (≤ SNIFF_N) để đối chiếu danh sách chọn

    def variants(self) -> list[str]:
        return [self.header, f"{self.group} {self.header}"] if self.group else [self.header]


def col_letter(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def profile_columns(header: Header, rows: DataRows) -> list[Column]:
    cols: list[Column] = []
    for j, name in enumerate(header.names):
        vals = [r[j] for _, r in rows.rows if j < len(r) and not blank(r[j])]
        if not name and not vals:
            continue
        c = Column(j, name or f"Cột {col_letter(j)}", header.groups[j] if j < len(header.groups) else "")
        c.values = vals[:SNIFF_N]
        c.n = len(vals)
        c.samples = _samples(vals)
        if c.values:
            counts: dict[str, int] = {}
            for v in c.values:
                for k in sniff(v):
                    counts[k] = counts.get(k, 0) + 1
            c.frac = {k: n / len(c.values) for k, n in counts.items()}
            c.distinct = len({name_key(cell_text(v)) for v in vals})
        if name_key(name) in {name_key(h) for h in IGNORE_HEADERS}:
            c.ignore = "số thứ tự"
        elif not name and _sequential(vals):
            c.ignore = "số thứ tự"
        cols.append(c)
    return cols


def _samples(vals: list[Any]) -> list[str]:
    out: list[str] = []
    for v in vals:
        t = cell_text(v)
        if t and t not in out:
            out.append(t[:80])
        if len(out) >= SAMPLE_N:
            break
    return out


def _sequential(vals: list[Any]) -> bool:
    nums = [v for v in vals[:20] if isinstance(v, int | float) and float(v).is_integer()]
    return len(nums) >= 3 and len(nums) == len(vals[:20]) and all(int(b) - int(a) == 1 for a, b in itertools.pairwise(nums))


def signature(header: Header) -> str:
    """Chữ ký bố cục file: sha1 của TẬP tiêu đề đã chuẩn hoá (không phụ thuộc thứ tự cột, hoa / thường, dấu) — hai file cùng bố cục ⇒ cùng chữ ký."""
    keys = sorted({name_key(f"{g} {n}" if g else n) for n, g in zip(header.names, header.groups, strict=False) if n})
    return hashlib.sha1("\n".join(keys).encode()).hexdigest()


def header_key(c: Column) -> str:
    return name_key(c.header if not c.group else f"{c.group} {c.header}")


# ── Trường của lệnh ──

@dataclass
class Field:
    key: str
    label: str
    kind: str
    required: bool
    names: list[str]                                  # nhãn (trung tính + công ty), khoá, tên khác — khớp tuyệt đối
    synonyms: dict[str, float]                        # từ đồng nghĩa (đã bỏ dấu) → hệ số
    options: OptionIndex | None = None              # lựa chọn (cố định hoặc từ options_query) nếu có

    @property
    def distinctive(self) -> bool:
        """Kiểu có thể nhận ra chỉ bằng nội dung."""
        return self.kind in ("phone", "email", "date", "bool") or self.options is not None


_SYN: dict[str, tuple[str, ...]] = {
    "phone": ("sdt", "so dt", "so dien thoai", "dien thoai", "dt", "di dong", "mobile", "phone", "tel", "hotline", "so may", "sdt lien he", "so dien thoai lien he",
              "sdt khach", "so lien lac"),
    "email": ("email", "e-mail", "mail", "thu dien tu", "dia chi email", "gmail"),
    "name": ("ho ten", "ho va ten", "ten", "ten khach", "ten khach hang", "khach hang", "ten kh", "name", "full name", "nguoi lien he", "ten nguoi"),
    "code": ("ma", "ma so", "code", "ma hieu"),
    "note": ("ghi chu", "note", "notes", "nhan xet", "mo ta", "remark", "remarks", "dien giai", "luu y", "ghi chu them"),
    "birthday": ("ngay sinh", "sinh nhat", "ngay thang nam sinh", "dob", "birthday", "nam sinh", "ns"),
    "address": ("dia chi", "address", "dc", "noi o", "noi o hien tai"),
    "tax": ("mst", "ma so thue", "tax code", "tax id"),
    "budget": ("ngan sach", "budget", "tai chinh", "muc gia", "khoang gia", "tam gia", "gia mong muon"),
    "amount": ("so tien", "thanh tien", "gia tri", "tong tien", "amount", "tien"),
    "source": ("nguon", "nguon khach", "nguon lead", "kenh", "source"),
    "date": ("ngay", "date", "thoi gian"),
    "owner": ("nguoi phu trach", "phu trach", "nhan vien", "nv", "sales", "chuyen vien", "nguoi cham soc", "email nhan vien", "resource names", "resource",
              "nguoi thuc hien", "nguoi lam", "pic", "assigned to", "nhan su", "nguoi chiu trach nhiem", "giao cho", "doi tuong thuc hien", "bo phan phu trach"),
    # kế hoạch / bảng việc (0222 §15/11): tên cột hay gặp của file kế hoạch tự soạn và file xuất từ MS Project
    "task": ("ten cong viec", "cong viec", "task name", "task", "nhiem vu", "ten nhiem vu", "noi dung cong viec", "ten hang muc", "activity"),
    "taskcat": ("hang muc", "noi dung", "muc cong viec", "cong viec chinh"),
    "start": ("bat dau", "ngay bat dau", "start", "start date", "tu ngay", "ngay bd", "bat dau tu", "ngay khoi dong", "ngay khoi cong"),
    "finish": ("ket thuc", "ngay ket thuc", "finish", "end", "end date", "han", "han chot", "deadline", "han hoan thanh", "den ngay", "ngay hoan thanh", "ngay kt",
               "due", "due date", "han cuoi", "ngay hoan tat"),
    "duration": ("so ngay", "thoi gian thuc hien", "thoi luong", "duration", "days", "so ngay thuc hien", "tg thuc hien", "so ngay lam", "so ngay cong", "thoi gian"),
    "predecessor": ("viec truoc", "cong viec truoc", "predecessors", "predecessor", "phu thuoc", "phu thuoc vao", "phu thuoc cong viec", "tien quyet", "depends on",
                    "dependencies", "lien ket truoc", "sau viec"),
    "progress": ("hoan thanh", "% hoan thanh", "phan tram hoan thanh", "ty le hoan thanh", "tien do", "% complete", "percent complete", "progress", "complete",
                 "tinh trang hoan thanh"),
    "phase": ("giai doan", "phase", "stage", "nhom cong viec", "nhom viec", "work package", "hang muc lon"),
    "phasecat": ("hang muc", "nhom"),
    "wishes": ("nhu cau", "mong muon", "nhu cau khach", "yeu cau khach"),
}


def concepts(key: str, kind: str) -> dict[str, float]:
    """Từ đồng nghĩa của tên cột theo Ý của trường (suy từ khoá + kiểu): hệ số 0..1 = mức chắc khi tên cột trùng từ đó."""
    k = key.lower()
    cs: dict[str, float] = {}
    if kind == "phone":
        cs["phone"] = 0.92
    if kind == "email":
        cs["email"] = 0.92
    if k == "name":
        cs["name"] = 0.9
    if k == "code" or (k.endswith("_code") and k.split("_")[0] in ("code",)):
        cs["code"] = 0.7
    if k in ("note", "notes", "memo", "description"):
        cs["note"] = 0.9
    if "birth" in k:
        cs["birthday"] = 0.92
    if k == "address":
        cs["address"] = 0.9
    if "tax" in k:
        cs["tax"] = 0.9
    if "budget" in k:
        cs["budget"] = 0.9
    if k in ("amount", "amount_vnd", "target") or k.startswith("amount_"):
        cs["amount"] = 0.8
    if k == "source":
        cs["source"] = 0.85
    if kind == "date" and (k in ("day", "date") or k.endswith("_on")):
        cs["date"] = 0.6
    if "owner" in k or "assignee" in k:
        cs["owner"] = 0.8
    if k == "wishes":
        cs["wishes"] = 0.85
    if k == "title":
        cs["task"], cs["taskcat"] = 0.92, 0.75
    if k == "start_on":
        cs["start"] = 0.9
    if k == "due_on":
        cs["finish"] = 0.9
    if k == "duration":
        cs["duration"] = 0.9
    if k == "after":
        cs["predecessor"] = 0.9
    if k == "percent":
        cs["progress"] = 0.88
    if k == "phase":
        cs["phase"], cs["phasecat"] = 0.92, 0.7
    out: dict[str, float] = {}
    for c, w in cs.items():
        for phrase in _SYN[c]:
            out[phrase] = max(out.get(phrase, 0), w)
    return out


def make_field(spec: dict[str, Any], neutral_label: str | None = None, options: OptionIndex | None = None) -> Field:
    names = [spec["label"], spec["key"], *(spec.get("aliases") or ())]
    if neutral_label and neutral_label not in names:
        names.append(neutral_label)
    names = [n.replace(" *", "") for n in names]
    if options is None and spec.get("options"):
        options = OptionIndex([(o["value"], o["label"]) for o in spec["options"]])
    return Field(spec["key"], spec["label"], spec["kind"], bool(spec["required"]), names, concepts(spec["key"], spec["kind"]), options)


# ── Danh sách chọn ──

_HONORIFICS = {"anh", "chi", "em", "ban", "ong", "ba", "co", "chu", "bac", "thay", "sep", "mr", "ms", "mrs"}
_PERSON_SPLIT = re.compile(r"\s*[,;/&+\n]\s*|\s+(?:và|va|and)\s+", re.I)


class OptionIndex:
    """Tra giá trị lựa chọn từ chữ trong ô: khớp tuyệt đối (nhãn / mã, bỏ dấu, hoa thường) → nhãn chứa / nằm trong chữ → gần đúng (sai chính tả).
    Không chắc ⇒ trả None + gợi ý — KHÔNG đoán bừa một giá trị.

    Danh sách NGƯỜI (mọi giá trị là email — vd người phụ trách): thêm cách gọi quen của người Việt, CHỈ khi chỉ trúng đúng một người — chữ cuối ("Lan" ⇒ "Nguyễn Thị Lan"),
    hai / ba chữ cuối, chữ cái đầu ("NVA" ⇒ "Nguyễn Văn An"), phần trước @ của email; bỏ "anh / chị…" ở đầu và phần trong ngoặc; ô nhiều người ("An, Bình") lấy người ĐẦU."""

    def __init__(self, options: list[tuple[str, str]]) -> None:
        self.options = options
        self.exact: dict[str, str] = {}
        for value, label in options:
            self.exact.setdefault(name_key(value), value)
            self.exact.setdefault(name_key(label), value)
            head = label.split("—")[0].strip()   # "MÃ — tên" ⇒ khớp cả mã lẫn tên
            self.exact.setdefault(name_key(head), value)
            if "—" in label:
                self.exact.setdefault(name_key(label.split("—", 1)[1]), value)
        self.people = bool(options) and all("@" in v for v, _ in options)
        if self.people:
            self._add_people_aliases()

    def _add_people_aliases(self) -> None:
        soft: dict[str, set[str]] = {}
        for value, label in self.options:
            name = re.split(r"\s+·\s+|\s*\(|\s+—\s+", label)[0].strip()
            toks = [t for t in _toks(name) if t]
            self.exact.setdefault(name_key(name), value)
            keys = {name_key(value.split("@")[0])}
            if len(toks) >= 2:
                for n in (1, 2, 3):
                    if n < len(toks):
                        keys.add("".join(toks[-n:]))
                if len(toks) >= 3:
                    keys.add("".join(t[0] for t in toks))
            for k in keys:
                if len(k) >= 2:
                    soft.setdefault(k, set()).add(value)
        for k, values in soft.items():
            if len(values) == 1:
                self.exact.setdefault(k, next(iter(values)))

    def _person_forms(self, raw: str) -> list[str]:
        out = [raw]
        bare = re.sub(r"\([^)]*\)", " ", raw).strip()
        toks = bare.split()
        if len(toks) > 1 and fold(toks[0]).strip(".") in _HONORIFICS:
            toks = toks[1:]
        out.append(" ".join(toks))
        first = _PERSON_SPLIT.split(" ".join(toks))[0].strip()
        if first:
            out.append(first)
            ft = first.split()
            if len(ft) > 1 and fold(ft[0]).strip(".") in _HONORIFICS:
                out.append(" ".join(ft[1:]))
        return out

    def resolve(self, raw: str) -> tuple[str | None, str | None]:
        """(giá trị đã chắc | None, gợi ý | None)."""
        if self.people:
            for form in self._person_forms(raw):
                if (v := self.exact.get(name_key(form))) is not None:
                    return v, None
        k = name_key(raw)
        if not k:
            return None, None
        if k in self.exact:
            return self.exact[k], None
        # nhãn nằm trong chữ ("Căn hộ cao cấp ST" ⊃ "ST") hoặc chữ nằm trong nhãn — chỉ nhận khi DUY NHẤT một giá trị khớp
        hit = {v for key, v in self.exact.items() if len(key) >= 3 and (key in k or k in key) and min(len(key), len(k)) >= 0.5 * max(len(key), len(k))}
        if len(hit) == 1:
            return next(iter(hit)), None
        scored = sorted(((SequenceMatcher(None, k, key).ratio(), v) for key, v in self.exact.items()), reverse=True)
        if scored and scored[0][0] >= 0.86 and (len(scored) == 1 or scored[0][1] == scored[1][1] or scored[0][0] - scored[1][0] >= 0.06):
            return scored[0][1], None
        if scored and scored[0][0] >= 0.7:
            return None, scored[0][1]
        return None, None

    def label_of(self, value: str) -> str:
        return next((lb for v, lb in self.options if v == value), value)

    def share(self, values: list[Any]) -> float:
        """Tỉ lệ ô của một cột khớp được một lựa chọn (chắc)."""
        texts = [cell_text(v) for v in values if not blank(v)]
        return sum(1 for t in texts if self.resolve(t)[0] is not None) / len(texts) if texts else 0.0


# ── Khớp tên cột ──

def _toks(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", fold(s))


def name_score(variants: list[str], f: Field) -> tuple[float, str]:
    best, why = 0.0, ""
    for vi, h in enumerate(variants):   # biến thể sau (nhóm + tiêu đề) chỉ nhận khi TRÙNG HẲN — tên nhóm quá chung để suy rộng
        ht = _toks(h)
        hc = "".join(ht)
        if not hc:
            continue
        for cand in f.names:
            ct = _toks(cand)
            cc = "".join(ct)
            if not cc:
                continue
            if hc == cc:
                return 1.0, f"tên cột “{h}” trùng “{f.label}”"
            if vi:
                continue
            common = len(set(ht) & set(ct))
            dice = 2 * common / (len(ht) + len(ct))
            sub = set(ht) <= set(ct) or set(ct) <= set(ht)
            s = 0.5 + 0.35 * dice if sub and common else dice * 0.7 if dice >= 0.5 else 0.0
            if abs(len(hc) - len(cc)) <= max(3, len(cc) // 4):   # chỉ so chính tả khi độ dài gần nhau (rẻ hơn nhiều khi dò mọi lệnh × mọi trang)
                sm = SequenceMatcher(None, hc, cc)
                if sm.real_quick_ratio() >= 0.84 and sm.quick_ratio() >= 0.84 and (ratio := sm.ratio()) >= 0.84:
                    s = max(s, ratio * 0.9)
            if s > best:
                best, why = s, f"tên cột “{h}” gần “{cand}”"
        for phrase, w in f.synonyms.items():
            pt = _toks(phrase)
            pc = "".join(pt)
            if hc == pc:
                s = w
            elif not vi and ((len(pt) >= 2 and _contains(ht, pt)) or (len(pc) >= 5 and _contains(ht, pt))):
                s = w * 0.85
            else:
                continue
            if s > best:
                best, why = s, f"“{h}” là tên quen của “{f.label}”"
    return best, why


def _contains(hay: list[str], needle: list[str]) -> bool:
    n = len(needle)
    return n > 0 and any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


def value_score(f: Field, c: Column) -> float | None:
    """Nội dung cột hợp kiểu trường đến đâu (0..1); None = kiểu này không đoán được từ nội dung."""
    if not c.values:
        return None
    fr = c.frac
    if f.options is not None:
        return f.options.share(c.values)
    k = f.kind
    if k == "date":
        return max(fr.get("date", 0.0), 0.6 * fr.get("serial", 0.0))
    if k in ("phone", "email", "bool", "percent"):
        return fr.get(k, 0.0)
    if k == "money":
        return max(fr.get("money", 0.0), 0.8 * fr.get("number", 0.0))
    if k == "number":
        return max(fr.get("number", 0.0), 0.8 * fr.get("money", 0.0))
    if k in ("text", "textarea") and max(fr.get("phone", 0), fr.get("email", 0), fr.get("date", 0)) >= 0.85:
        return 0.1   # ô chữ thường mà cột toàn SĐT / email / ngày ⇒ cột đó thuộc trường khác
    return None


def combine(n: float, v: float | None, distinctive: bool) -> float:
    if n >= MAYBE:
        if v is None:
            return n
        if v >= 0.6:
            return min(1.0, n + 0.15 * v)
        return n * 0.55 if v < 0.3 else n * 0.85
    if v is not None and v >= 0.8 and distinctive:
        return 0.45 + 0.25 * v   # chỉ nội dung nhận ra: tối đa 0.70 ⇒ luôn "gợi ý", không "chắc"
    return 0.0


@dataclass
class Match:
    field: str
    col: int
    conf: float
    source: str       # saved · name · value · ai
    why: str


def match_columns(cols: list[Column], fields: list[Field], remembered: dict[str, str | None] | None = None) -> tuple[list[Match], set[int]]:
    """Khớp tham lam theo độ tin cậy giảm dần, mỗi trường một cột và mỗi cột một trường. ``remembered``: {tiêu đề đã chuẩn hoá: khoá trường | None} của công
    ty (None = người đã chọn bỏ cột này) — thắng mọi suy đoán. Trả (các cặp, tập cột bị người bỏ chủ ý)."""
    remembered = remembered or {}
    byk = {f.key: f for f in fields}
    dropped = {c.index for c in cols if header_key(c) in remembered and remembered[header_key(c)] is None}
    cand: list[Match] = []
    for c in cols:
        if c.ignore or c.index in dropped:
            continue
        mem = remembered.get(header_key(c))
        if mem and mem in byk:
            cand.append(Match(mem, c.index, 0.97, "saved", "bố cục đã nhập trước đây"))
            continue
        for f in fields:
            n, why = name_score(c.variants(), f)
            v = value_score(f, c)
            conf = combine(n, v, f.distinctive)
            if conf < MAYBE:
                continue
            if n >= MAYBE:
                if v is not None and v >= 0.6:
                    why += f"; nội dung hợp ({round(v * 100)}% ô)"
                elif v is not None and v < 0.3:
                    why += "; nhưng nội dung ít giống — kiểm lại"
            else:
                why = f"nội dung cột là {_KIND_WORD.get(f.kind, f.kind)} ({round((v or 0) * 100)}% ô)" if f.options is None else \
                    f"{round((v or 0) * 100)}% ô là giá trị trong danh sách chọn"
            cand.append(Match(f.key, c.index, conf, "name" if n >= MAYBE else "value", why))
    cand.sort(key=lambda m: (-m.conf, m.col))
    out: list[Match] = []
    used_f: set[str] = set()
    used_c: set[int] = set()
    for m in cand:
        if m.field in used_f or m.col in used_c:
            continue
        rival = next((o for o in cand if o is not m and ((o.field == m.field and o.col not in used_c) or (o.col == m.col and o.field not in used_f))
                      and m.conf - o.conf < 0.04 and m.source != "saved"), None)
        if rival is not None:   # hai ứng viên sát nhau ⇒ trừ điểm để màn khớp cột đánh dấu cho người nhìn
            m = Match(m.field, m.col, max(MAYBE, m.conf - 0.12), m.source, m.why + " — còn một cột/trường khác cũng hợp, kiểm lại")
        out.append(m)
        used_f.add(m.field)
        used_c.add(m.col)
    return out, dropped


_KIND_WORD = {"phone": "số điện thoại", "email": "email", "date": "ngày", "money": "số tiền", "number": "số", "bool": "có / không", "percent": "phần trăm"}


# ── Model: chỉ cho cột còn lại ──

class AIPair(BaseModel):
    column: int
    field: str | None = None
    confidence: float = 0.5
    why: str = ""


class AIMapping(BaseModel):
    pairs: list[AIPair]


AI_INSTRUCTIONS = (
    "Bạn khớp cột của một file Excel tiếng Việt vào trường của một form nhập liệu. Chỉ dùng thông tin được đưa: tiêu đề cột + vài ô mẫu. "
    "Với MỖI cột, chọn đúng một trường trong danh sách trường còn trống (khoá `key`) nếu nội dung rõ ràng thuộc trường đó; nếu không chắc hoặc không có trường "
    "nào hợp thì để field = null — sai còn tệ hơn bỏ trống. Mỗi trường chỉ được chọn tối đa một lần. Không bịa khoá. `confidence` từ 0 tới 1, "
    "`why` một câu ngắn tiếng Việt (không quá 15 từ).")

_NUM_RUN = re.compile(r"\d{6,}")


def _mask(t: str) -> str:
    """Che chuỗi số dài / email trước khi gửi model — model chỉ cần hình dạng ô mẫu, không cần số thật."""
    return _NUM_RUN.sub(lambda m: "9" * len(m.group()), re.sub(r"[^@\s]+@[^@\s]+", "<email>", t))[:60]


def ai_pool(cols: list[Column], taken: set[int], fields: list[Field], used: set[str], dropped: set[int]) -> tuple[list[Column], list[Field]]:
    """Cột chưa khớp (có tiêu đề hoặc dữ liệu, không phải số thứ tự, không bị người bỏ) × trường chưa có cột."""
    pc = [c for c in cols if c.index not in taken and c.index not in dropped and not c.ignore and c.n > 0][:30]
    pf = [f for f in fields if f.key not in used][:40]
    return pc, pf


def ai_request(cols: list[Column], fields: list[Field]) -> str:
    import json
    return json.dumps({
        "cot_trong_file": [{"cot": c.index, "tieu_de": c.header if not c.header.startswith("Cột ") else "", "nhom": c.group,
                            "o_mau": [_mask(s) for s in c.samples]} for c in cols],
        "truong_con_trong": [{"key": f.key, "nhan": f.label, "kieu": f.kind} for f in fields],
    }, ensure_ascii=False)


def ai_apply(out: AIMapping, cols: list[Column], fields: list[Field]) -> list[Match]:
    """Kiểm kết quả model: khoá phải nằm trong danh sách đã gửi, mỗi cột / trường một lần; độ tin cậy bị chặn dưới ngưỡng "chắc" (model đoán ⇒ người xem lại)."""
    keys = {f.key for f in fields}
    idx = {c.index for c in cols}
    seen_f: set[str] = set()
    seen_c: set[int] = set()
    res: list[Match] = []
    for p in sorted(out.pairs, key=lambda x: -x.confidence):
        if p.field is None or p.field not in keys or p.column not in idx or p.field in seen_f or p.column in seen_c or p.confidence < 0.5:
            continue
        seen_f.add(p.field)
        seen_c.add(p.column)
        res.append(Match(p.field, p.column, min(SURE - 0.05, max(MAYBE, p.confidence)), "ai", ("máy đoán: " + p.why).strip(": ")))
    return res


# ── Ngày (0226 — theo vùng của công ty, quyết định PER CỘT) ──

MONTH_FIRST = {"US", "PH"}   # nước viết ngày kiểu tháng/ngày/năm; còn lại ngày/tháng/năm (Việt Nam, châu Âu…)
_TXT_DATE = re.compile(r"^\s*(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2}|\d{4})(?:[ T,]+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(Z|[+-]\d{2}:?\d{2})?)?\s*$")
_ISO_STAMP = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?\s*(Z|[+-]\d{2}:?\d{2})?)?\s*$")
SERIAL_RANGE = (20_000, 80_000)   # số nguyên trong khoảng này ở ô ngày = số ngày của Excel (1954…2118)
_EXCEL_EPOCH = datetime(1899, 12, 30)


@dataclass
class DateInfo:
    order: str                # dmy | mdy — cách đọc ngày chữ của cột này
    decided: str              # data (có giá trị > 12 quyết định) · country (cả cột mơ hồ ⇒ mặc định của vùng) · mixed (cột có cả hai ⇒ chọn bên nhiều hơn) · saved · user
    text_dates: int           # số ô là ngày dạng CHỮ (ô ngày thật của Excel không đếm — không bao giờ mơ hồ)
    sample: str = ""          # một ô mẫu để minh hoạ cách đọc
    examples: dict[str, str | None] = field(default_factory=dict)   # {dmy|mdy: "03/04/2026 = 3 tháng 4 năm 2026" | None nếu cách đọc đó không hợp lệ}


def default_order(country: str) -> str:
    return "mdy" if country.upper() in MONTH_FIRST else "dmy"


def _parts(v: Any) -> tuple[int, int] | None:
    if not isinstance(v, str):
        return None
    m = _TXT_DATE.match(unweekday(v))
    return (int(m.group(1)), int(m.group(2))) if m else None


def decide_order(values: list[Any], country: str, saved: str | None = None, user: str | None = None) -> DateInfo:
    """Thứ tự ngày / tháng của MỘT cột ngày chữ — quyết một lần cho cả cột, không từng ô: có giá trị mà phần đầu > 12 ⇒ ngày trước; phần giữa > 12 ⇒ tháng trước;
    cả cột đều ≤ 12 (mơ hồ) ⇒ ánh xạ đã nhớ, rồi mặc định của quốc gia công ty. ``user``: người đã bấm đổi ở màn khớp cột — thắng tất cả."""
    pairs = [x for x in (_parts(v) for v in values) if x is not None]
    first_big = sum(1 for a, b in pairs if a > 12 and b <= 12)
    second_big = sum(1 for a, b in pairs if b > 12 and a <= 12)
    if user in ("dmy", "mdy"):
        order, how = user, "user"
    elif first_big and second_big:
        order, how = ("dmy" if first_big >= second_big else "mdy"), "mixed"
    elif first_big:
        order, how = "dmy", "data"
    elif second_big:
        order, how = "mdy", "data"
    elif saved in ("dmy", "mdy"):
        order, how = saved, "saved"
    else:
        order, how = default_order(country), "country"
    ambiguous = next((v for v in values if (p := _parts(v)) and max(p) <= 12), None)
    sample = ambiguous or next((v for v in values if _parts(v)), "")   # ưu tiên ô mơ hồ — chỗ người cần xem
    info = DateInfo(order, how, len(pairs), str(sample).strip())
    for o in ("dmy", "mdy"):
        d = _text_date(info.sample, o) if sample else None
        info.examples[o] = f"{info.sample} = {d.day} tháng {d.month} năm {d.year}" if d else None
    return info


def _text_date(v: str, order: str) -> date | None:
    m = _TXT_DATE.match(unweekday(v))
    if not m:
        return None
    a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 100:
        y += 2000 if y < 70 else 1900
    day, month = (a, b) if order == "dmy" else (b, a)
    try:
        return date(y, month, day)
    except ValueError:
        return None


def _offset(token: str | None) -> tzinfo | None:
    if not token:
        return None
    if token == "Z":
        return UTC
    sign = 1 if token[0] == "+" else -1
    digits = token[1:].replace(":", "")
    return timezone(sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:])))


def _stamp(v: Any, order: str, tz: tzinfo) -> str | None:
    """Ô → thời điểm ISO có độ lệch: ô ngày giờ thật / chữ KHÔNG nêu độ lệch ⇒ giờ của công ty (múi giờ công ty); có nêu (Z, +07:00) ⇒ giữ."""
    dt: datetime | None = None
    if isinstance(v, datetime):
        dt = v
    elif isinstance(v, date):
        dt = datetime(v.year, v.month, v.day)
    elif isinstance(v, int | float) and not isinstance(v, bool) and SERIAL_RANGE[0] <= v <= SERIAL_RANGE[1]:
        dt = _EXCEL_EPOCH + timedelta(days=float(v))
    elif isinstance(v, str):
        v = unweekday(v)
        if m := _TXT_DATE.match(v):
            d = _text_date(v, order)
            if d:
                dt = datetime(d.year, d.month, d.day, int(m.group(4) or 0), int(m.group(5) or 0), int(m.group(6) or 0), tzinfo=_offset(m.group(7)))
        elif m := _ISO_STAMP.match(v):
            try:
                dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4) or 0), int(m.group(5) or 0), int(m.group(6) or 0),
                              tzinfo=_offset(m.group(7)))
            except ValueError:
                return None
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=tz)).isoformat()


def _day(v: Any, order: str) -> str | None:
    """Ô → ngày ISO. Ô ngày thật của Excel (datetime / date / số ngày của Excel) dùng NGUYÊN, không đọc lại như chữ."""
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, int | float) and not isinstance(v, bool) and SERIAL_RANGE[0] <= v <= SERIAL_RANGE[1]:
        return (_EXCEL_EPOCH + timedelta(days=float(v))).date().isoformat()
    if isinstance(v, str):
        d = _text_date(v, order)
        return d.isoformat() if d else None
    return None


def is_stamp_field(key: str, kind: str) -> bool:
    """Ô chữ nhận THỜI ĐIỂM ISO có độ lệch (``at``, ``…_at``, ``until``, ``due`` — lệnh nạp lịch sử): giờ không nêu múi là giờ công ty."""
    return kind in ("text", "textarea") and (key in ("at", "until", "due") or key.endswith("_at"))


def convert_dates(fields: dict[str, Field], rows: list[dict[str, Any]], country: str, tz: tzinfo, orders: dict[str, str] | None = None,
                  saved: dict[str, str] | None = None) -> tuple[list[dict[str, Any]], dict[str, DateInfo]]:
    """Ô của trường NGÀY / THỜI ĐIỂM → ISO (mỗi cột đọc theo MỘT thứ tự ngày-tháng, xem ``decide_order``). Ô không đọc được giữ nguyên chữ ⇒ lệnh báo lỗi dòng đó."""
    orders, saved = orders or {}, saved or {}
    info: dict[str, DateInfo] = {}
    out = [dict(r) for r in rows]
    for key, f in fields.items():
        stamp = is_stamp_field(key, f.kind)
        if f.kind != "date" and not stamp:
            continue
        vals = [r[key] for r in out if key in r]
        info[key] = decide_order(vals, country, saved.get(key), orders.get(key))
        for r in out:
            if key not in r:
                continue
            got = _stamp(r[key], info[key].order, tz) if stamp else _day(r[key], info[key].order)
            if got is not None:
                r[key] = got
    return out, info


# ── Chuẩn hoá ô lựa chọn ──

@dataclass
class Unresolved:
    field: str
    raw: str
    count: int = 0
    suggest: str | None = None


def resolve_options(fields: dict[str, Field], rows: list[dict[str, Any]], resolutions: dict[str, dict[str, str]]) -> tuple[list[dict[str, Any]], list[Unresolved], set[int]]:
    """Ô của trường có danh sách chọn → giá trị chọn. ``resolutions``: {khoá trường: {chữ trong ô đã chuẩn hoá: giá trị | ""}} — người chọn tay một lần cho mỗi
    chữ khác nhau (không phải mỗi dòng); "" = bỏ trống ô này. Chữ không nhận ra và chưa được chọn ⇒ giữ nguyên chữ (lệnh sẽ báo lỗi dòng đó) + liệt kê để chọn."""
    pending: dict[tuple[str, str], Unresolved] = {}
    waiting: set[int] = set()
    out = []
    for row in rows:
        new = dict(row)
        for key, f in fields.items():
            if f.options is None or key not in row:
                continue
            v = row[key]
            parts = [p.strip() for p in re.split(r"[,;\n]", v) if p.strip()] if isinstance(v, str) and f.kind == "multiselect" else [v]
            got: list[Any] = []
            for part in parts:
                text = cell_text(part)
                nk = name_key(text)
                chosen = resolutions.get(key, {}).get(nk)
                if chosen is not None:
                    if chosen != "":
                        got.append(chosen)
                    continue
                value, suggest = f.options.resolve(text)
                if value is not None:
                    got.append(value)
                    continue
                u = pending.setdefault((key, nk), Unresolved(key, text, 0, suggest))
                u.count += 1
                waiting.add(int(row.get("_line") or 0))
                got.append(text)
            if f.kind == "multiselect":
                new[key] = got
            elif got:
                new[key] = got[0]
            else:
                new.pop(key, None)
        out.append(new)
    return out, sorted(pending.values(), key=lambda u: (u.field, -u.count)), waiting
