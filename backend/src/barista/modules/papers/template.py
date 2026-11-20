"""MẪU VĂN BẢN (0118 lát 1) — phần thuần, không DB: đọc thân mẫu, kiểm ô, điền số liệu, định dạng kiểu Việt.

Thân mẫu là Markdown rút gọn — đọc được, so sánh được giữa hai phiên bản bằng diff chữ thường:

* ``# Tên văn bản`` (một dòng, in hoa khi in) · ``## Tiêu đề mục`` (vd "Điều 1. …") · ``- dòng gạch đầu`` · dòng khác = một đoạn.
* ``{{khoá}}`` = ô lấy từ hồ sơ (danh mục ô do gói khai qua điểm cắm ``papers.field_catalog``) hoặc ô chung của giấy tờ (pháp nhân, người ký…).
  Bộ lọc: ``{{khoá|chu}}`` số tiền bằng chữ · ``{{khoá|dai}}`` ngày viết dài ("ngày 08 tháng 11 năm 2026").
* Ô kiểu bảng (``rows``, vd lịch đóng tiền) đứng MỘT MÌNH trên một dòng ⇒ thành bảng.
* Mục có điều kiện: ``{{#if khoá}}`` … ``{{/if}}`` / ``{{#unless khoá}}`` … ``{{/unless}}``, mỗi dấu một dòng riêng; lồng được.

Giá trị điền vào SAU khi đã tách khối ⇒ chữ trong hồ sơ (kể cả "{{…}}") không bao giờ thành cú pháp mẫu. Ô thiếu giá trị hiện "……" và được
liệt kê — bản có ô thiếu không ban hành được.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from barista.kernel.text import money, vnd_words

KINDS = {"text": "chữ", "number": "số", "amount": "số tiền", "date": "ngày", "rows": "bảng"}
FILTERS = {"chu": "amount", "dai": "date"}   # bộ lọc → kiểu ô nhận được
GAP = "……"
KEY = r"[a-z][a-z0-9_]{0,59}"
TOKEN = re.compile(r"\{\{\s*(" + KEY + r")\s*(?:\|\s*([a-z]+)\s*)?\}\}")
OPEN = re.compile(r"^\{\{#(if|unless)\s+(" + KEY + r")\s*\}\}$")
CLOSE = re.compile(r"^\{\{/(if|unless)\s*\}\}$")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")   # ký tự điều khiển (trừ tab / xuống dòng): Word / XML không nhận
SOLO = re.compile(r"^\{\{\s*(" + KEY + r")\s*\}\}$")


class TemplateError(ValueError):
    """Thân mẫu sai cú pháp / dùng ô lạ — lời báo cho người soạn mẫu (kèm số dòng)."""


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str = "text"
    columns: tuple[Field, ...] = ()   # chỉ cho ``rows``: cột của bảng (kiểu như ô thường)
    help: str = ""
    person: bool = False   # người soạn được nhập khi hồ sơ trống (gói khai từng ô); mặc định KHÔNG — ô từ hồ sơ / công ty chỉ lấy từ nguồn

    @staticmethod
    def of(raw: dict[str, Any]) -> Field:
        kind = str(raw.get("kind") or "text")
        if kind not in KINDS:
            raise ValueError(f"ô {raw.get('key')}: kiểu lạ {kind}")
        cols = tuple(Field.of(c) for c in raw.get("columns") or ())
        if kind == "rows" and not cols:
            raise ValueError(f"ô bảng {raw.get('key')} cần cột")
        if not re.fullmatch(KEY, str(raw.get("key") or "")):
            raise ValueError(f"khoá ô lạ: {raw.get('key')!r}")
        if raw.get("person") and kind == "rows":
            raise ValueError(f"ô bảng {raw.get('key')} chỉ lấy từ hồ sơ")
        return Field(str(raw["key"]), str(raw.get("label") or raw["key"]), kind, cols, str(raw.get("help") or ""), bool(raw.get("person")))


@dataclass(frozen=True)
class Block:
    type: str                     # title · heading · para · bullet · rows
    text: str = ""
    line: int = 0
    when: tuple[tuple[bool, str], ...] = field(default=())   # (phủ định?, khoá) — mọi điều kiện bao quanh phải đúng


def parse(body: str) -> list[Block]:
    out: list[Block] = []
    stack: list[tuple[str, bool, str, int]] = []
    titles = 0
    for n, raw in enumerate(body.replace("\r\n", "\n").split("\n"), 1):
        if CONTROL.search(raw):
            raise TemplateError(f"Dòng {n}: có ký tự điều khiển — xoá đi rồi lưu lại")
        s = raw.strip()
        if not s:
            continue
        when = tuple((neg, k) for _, neg, k, _ in stack)
        if m := OPEN.match(s):
            stack.append((m[1], m[1] == "unless", m[2], n))
            continue
        if m := CLOSE.match(s):
            if not stack or stack[-1][0] != m[1]:
                raise TemplateError(f"Dòng {n}: {s} không khớp mục điều kiện nào đang mở")
            stack.pop()
            continue
        if "{{#" in s or "{{/" in s:
            raise TemplateError(f"Dòng {n}: dấu điều kiện phải đứng riêng một dòng")
        if s.startswith("# "):
            titles += 1
            if titles > 1 or stack:
                raise TemplateError(f"Dòng {n}: chỉ một dòng tên văn bản (# …), không đặt trong mục điều kiện")
            out.append(Block("title", s[2:].strip(), n))
        elif s.startswith("## "):
            out.append(Block("heading", s[3:].strip(), n, when))
        elif s.startswith("- "):
            out.append(Block("bullet", s[2:].strip(), n, when))
        elif m := SOLO.match(s):
            out.append(Block("solo", m[1], n, when))
        else:
            out.append(Block("para", s, n, when))
        _check_tokens(out[-1], n)
    if stack:
        raise TemplateError(f"Dòng {stack[-1][3]}: mục điều kiện {{{{#{stack[-1][0]} {stack[-1][2]}}}}} chưa đóng")
    if not titles:
        raise TemplateError("Thiếu dòng tên văn bản (# …) ở đầu mẫu")
    return out


def _check_tokens(b: Block, n: int) -> None:
    rest = TOKEN.sub("", b.text) if b.type != "solo" else ""
    if "{{" in rest or "}}" in rest:
        raise TemplateError(f"Dòng {n}: ô viết sai — dạng đúng {{{{khoá}}}} hoặc {{{{khoá|chu}}}}")


def used(blocks: list[Block]) -> dict[str, set[str]]:
    """khoá → bộ lọc dùng ("" = không lọc), kể cả khoá chỉ dùng làm điều kiện."""
    out: dict[str, set[str]] = {}
    for b in blocks:
        for _, k in b.when:
            out.setdefault(k, set())
        if b.type == "solo":
            out.setdefault(b.text, set()).add("")
        else:
            for k, f in TOKEN.findall(b.text):
                out.setdefault(k, set()).add(f)
    return out


def validate(blocks: list[Block], catalog: dict[str, Field]) -> list[str]:
    """Lỗi của mẫu so với danh mục ô (rỗng = hợp lệ): ô lạ, bộ lọc sai kiểu, ô bảng đặt giữa câu."""
    errs: list[str] = []
    for k, filters in sorted(used(blocks).items()):
        f = catalog.get(k)
        if f is None:
            errs.append(f"ô {{{{{k}}}}} không có trong danh mục của loại hồ sơ này")
            continue
        for flt in filters - {""}:
            if flt not in FILTERS:
                errs.append(f"bộ lọc lạ |{flt} ở ô {k} (có: |chu, |dai)")
            elif FILTERS[flt] != f.kind:
                errs.append(f"|{flt} chỉ dùng cho ô kiểu {KINDS[FILTERS[flt]]} — {k} là {KINDS[f.kind]}")
    for b in blocks:
        if b.type != "solo":
            for k, _ in TOKEN.findall(b.text):
                if (f := catalog.get(k)) and f.kind == "rows":
                    errs.append(f"dòng {b.line}: ô bảng {{{{{k}}}}} phải đứng riêng một dòng")
    return errs


# ── Định dạng kiểu Việt ──

def _num(v: Any) -> str:
    x = Decimal(str(v))
    sign, x = ("-" if x < 0 else ""), abs(x)
    whole = int(x)
    frac = f"{x - whole:.4f}"[2:].rstrip("0")
    return sign + money(whole) + (f",{frac}" if frac else "")


def as_date(v: Any) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if m := re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s):
        return date(int(m[3]), int(m[2]), int(m[1]))
    return date.fromisoformat(s[:10])


def empty(v: Any) -> bool:
    return v is None or v == "" or v == [] or v == {}


def fmt(v: Any, kind: str, flt: str = "") -> str:
    """Giá trị → chữ in trên giấy. ``amount``: 5.600.000.000 đồng · ``number``: 1.234,5 · ``date``: 08/11/2026."""
    if kind == "amount":
        n = int(v)
        return vnd_words(n) if flt == "chu" else f"{money(n)} đồng"
    if kind == "number":
        return _num(v)
    if kind == "date":
        d = as_date(v)
        return f"ngày {d.day:02d} tháng {d.month:02d} năm {d.year}" if flt == "dai" else f"{d:%d/%m/%Y}"
    if kind == "rows":
        return f"{len(v)} dòng"
    return CONTROL.sub("", str(v)).strip()


def jsonable(v: Any) -> Any:
    """Giá trị gói trả ⇒ dạng lưu được (jsonb) và băm ổn định: ngày ⇒ ISO, Decimal ⇒ số."""
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [jsonable(x) for x in v]
    return v


def mark(values: dict[str, Any]) -> str:
    """Dấu của số liệu hồ sơ — lệch giữa lúc soạn và lúc ban hành ⇒ hồ sơ đã đổi."""
    return hashlib.sha256(json.dumps(jsonable(values), sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:32]


# ── Điền ──

@dataclass
class Filled:
    title: str
    blocks: list[dict[str, Any]]   # {type: heading|para|bullet|table, text?, columns?, rows?, align?, total?}
    missing: list[str]             # khoá thiếu giá trị ở phần THỰC SỰ in (mục điều kiện tắt không tính)
    markdown: str                  # bản đã điền dạng chữ — lưu theo từng bản để so sánh


def _table(f: Field, rows: list[dict[str, Any]]) -> dict[str, Any]:
    cols = list(f.columns)
    body = [[GAP if empty(r.get(c.key)) else fmt(r.get(c.key), c.kind) for c in cols] for r in rows]
    align = ["r" if c.kind in ("amount", "number") else ("c" if c.kind == "date" else "l") for c in cols]
    out: dict[str, Any] = {"type": "table", "columns": [c.label for c in cols], "rows": body, "align": align}
    if cols and cols[-1].kind == "amount" and len(cols) > 1:
        out["total"] = ["Tổng cộng", fmt(sum(int(r.get(cols[-1].key) or 0) for r in rows), "amount")]
    return out


def fill(blocks: list[Block], catalog: dict[str, Field], values: dict[str, Any], marks: dict[str, int] | None = None) -> Filled:
    """``marks``: ô người nhập ⇒ số chú thích in ngay sau giá trị ("[1]"); danh sách chú thích do bên gọi in cuối văn bản."""
    marks = marks or {}
    missing: list[str] = []
    out: list[dict[str, Any]] = []
    title = ""

    def sub(text: str) -> str:
        def one(m: re.Match[str]) -> str:
            k, flt = m[1], m[2] or ""
            v = values.get(k)
            if empty(v):
                if k not in missing:
                    missing.append(k)
                return GAP
            out = fmt(v, catalog[k].kind if k in catalog else "text", flt)
            return f"{out} [{marks[k]}]" if k in marks else out
        return TOKEN.sub(one, text)

    for b in blocks:
        if any(empty(values.get(k)) != neg for neg, k in b.when):
            continue
        if b.type == "title":
            title = sub(b.text)
        elif b.type == "solo":
            f = catalog.get(b.text)
            v = values.get(b.text)
            if f is not None and f.kind == "rows":
                if empty(v):
                    if b.text not in missing:
                        missing.append(b.text)
                    out.append({"type": "para", "text": GAP})
                else:
                    out.append(_table(f, list(v or [])))
            else:
                out.append({"type": "para", "text": sub("{{" + b.text + "}}")})
        else:
            out.append({"type": b.type, "text": sub(b.text)})
    return Filled(title, out, missing, to_markdown(title, out))


def to_markdown(title: str, blocks: list[dict[str, Any]]) -> str:
    lines = [f"# {title}"]
    for b in blocks:
        if b["type"] == "heading":
            lines.append(f"## {b['text']}")
        elif b["type"] == "bullet":
            lines.append(f"- {b['text']}")
        elif b["type"] == "table":
            lines.append("| " + " | ".join(b["columns"]) + " |")
            lines.extend("| " + " | ".join(r) + " |" for r in b["rows"])
            if b.get("total"):
                lines.append("| " + " | ".join(b["total"]) + " |")
        else:
            lines.append(b["text"])
    return "\n".join(lines)


def parse_value(raw: str, kind: str) -> Any:
    """Chữ người nhập (dữ kiện còn thiếu) ⇒ giá trị theo kiểu ô. Sai ⇒ ValueError có lời báo."""
    s = raw.strip()
    if not s:
        raise ValueError("Chưa nhập giá trị")
    if CONTROL.search(s):
        raise ValueError("Có ký tự điều khiển (không in được) — gõ lại giá trị")
    if kind == "amount":
        digits = re.sub(r"[.\s]|đồng|đ$", "", s, flags=re.IGNORECASE)
        if not digits.isdigit():
            raise ValueError("Số tiền chỉ gồm chữ số (vd 50.000.000)")
        return int(digits)
    if kind == "number":
        t = s.replace(".", "").replace(",", ".")
        try:
            x = Decimal(t)
        except ArithmeticError:
            raise ValueError("Số viết kiểu Việt, vd 1.234,5") from None
        return jsonable(x)
    if kind == "date":
        try:
            return as_date(s).isoformat()
        except ValueError:
            raise ValueError("Ngày dạng 08/11/2026") from None
    if kind == "rows":
        raise ValueError("Ô bảng chỉ lấy từ hồ sơ")
    return s
