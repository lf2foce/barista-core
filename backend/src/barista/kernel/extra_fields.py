"""TRƯỜNG TUỲ BIẾN THEO CÔNG TY (quyết định 0060, lát 1): công ty tự thêm trường cho một đối tượng mà KHÔNG sửa mã gói.

* Module khai đối tượng mở được (``Module.extensible("lead", "Khách / lead")``) và lệnh nhận giá trị (``Module.command(extra="lead")`` — input có ô
  ``extra: dict``). Giá trị nằm trong cột ``extra jsonb`` của CHÍNH bảng đối tượng, module sở hữu lưu (bất biến 3).
* Định nghĩa trường là DỮ LIỆU công ty (``core.field_defs``; quản trị sửa trong app), nạp vào ``Principal.fields`` như từ vựng (0124).
* MỘT nơi kiểm, mọi cửa như nhau: executor gộp các ô ``x_<khoá>`` (form, Excel, công cụ trợ lý, API gửi phẳng) vào ``extra`` rồi kiểm kiểu ở đây
  (``check``) — trường lạ / sai kiểu bị từ chối; KHÔNG ``extra="allow"``. Form / schema công cụ / mẫu Excel thêm ô từ cùng định nghĩa (``specs``).
* Ranh giới (0060): chỉ chữ · số · ngày · chọn một. Trường ảnh hưởng tiền, quyền, trạng thái nghiệp vụ không làm ở đây — đó là hợp đồng của module.
* Chưa làm ở lát này: bắt buộc, quyền theo trường, luật điều kiện, lọc / chỉ mục theo trường.
"""

import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from barista.db import Conn
from barista.kernel.errors import Invalid

PREFIX = "x_"            # ô trên form / cột Excel / tham số công cụ của trường tuỳ biến: ``x_<khoá>`` — không trùng ô nào module khai
KINDS = {"text": "Chữ", "number": "Số", "date": "Ngày", "choice": "Chọn một"}
KEY = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
GROUP = "Thông tin riêng của công ty"
MAX_PER_SUBJECT = 30
MAX_TEXT = 500
MAX_NUMBER_CHARS = 30
MAX_NUMBER = Decimal("1e15")
TTL_S = 30.0             # đổi định nghĩa hiệu lực trên mọi tiến trình trong ≤ 30 giây (trong tiến trình đổi: ngay — ``invalidate``)


@dataclass(frozen=True)
class Subject:
    key: str
    label: str
    module: str


@dataclass(frozen=True)
class FieldDef:
    subject: str
    key: str
    label: str
    kind: str
    options: tuple[str, ...] = ()
    help: str = ""


SUBJECTS: dict[str, Subject] = {}
_cache: dict[UUID, tuple[float, dict[str, tuple[FieldDef, ...]]]] = {}


def declare(key: str, label: str, module: str) -> Subject:
    if not KEY.fullmatch(key):
        raise ValueError(f"đối tượng mở trường tuỳ biến {key!r}: khoá phải dạng chữ thường_gạch_dưới")
    if key in SUBJECTS:
        raise ValueError(f"đối tượng {key} đã có module {SUBJECTS[key].module} khai")
    SUBJECTS[key] = Subject(key, label, module)
    return SUBJECTS[key]


async def load(conn: Conn, tenant_id: UUID) -> dict[str, tuple[FieldDef, ...]]:
    """Trường ĐANG DÙNG của công ty theo đối tượng (chỉ đối tượng còn được khai) — bộ nhớ đệm ngắn như từ vựng."""
    hit = _cache.get(tenant_id)
    if hit and time.monotonic() - hit[0] < TTL_S:
        return hit[1]
    cur = await conn.execute("""SELECT subject, key, label, kind, options, help FROM core.field_defs
                                WHERE tenant_id = %s AND active ORDER BY subject, position, created_at""", (tenant_id,))
    out: dict[str, list[FieldDef]] = {}
    for r in await cur.fetchall():
        if r["subject"] in SUBJECTS:
            out.setdefault(r["subject"], []).append(FieldDef(r["subject"], r["key"], r["label"], r["kind"],
                                                             tuple(str(o) for o in r["options"] or ()), r["help"]))
    done = {k: tuple(v) for k, v in out.items()}
    _cache[tenant_id] = (time.monotonic(), done)
    return done


def invalidate(tenant_id: UUID) -> None:
    _cache.pop(tenant_id, None)


def of(fields: Mapping[str, tuple[FieldDef, ...]] | None, subject: str) -> tuple[FieldDef, ...]:
    return (fields or {}).get(subject, ())


def fold(inp: Any) -> Any:
    """Ô phẳng ``x_<khoá>`` (form, Excel, công cụ trợ lý) ⇒ gộp vào ``extra`` TRƯỚC khi kiểm model. Không có ô ``x_`` ⇒ trả nguyên đầu vào."""
    if not isinstance(inp, dict) or not any(isinstance(k, str) and k.startswith(PREFIX) for k in inp):
        return inp
    flat = {k[len(PREFIX):]: v for k, v in inp.items() if isinstance(k, str) and k.startswith(PREFIX)}
    rest = {k: v for k, v in inp.items() if not (isinstance(k, str) and k.startswith(PREFIX))}
    given = rest.get("extra")
    filled = {k: v for k, v in flat.items() if v is not None and not (isinstance(v, str) and not v.strip())}   # ô phẳng trống không xoá giá trị đã có trong extra
    return {**rest, "extra": {**(given if isinstance(given, dict) else {}), **filled}}


def _one(d: FieldDef, v: Any) -> Any:
    if d.kind == "text":
        s = " ".join(str(v).split()) if not isinstance(v, (dict, list)) else None
        if s is None or len(s) > MAX_TEXT:
            raise Invalid(f"{d.label}: cần chữ, tối đa {MAX_TEXT} ký tự")
        return s
    if d.kind == "number":
        raw = str(v).strip().replace(",", ".")
        try:   # chặn TRƯỚC khi đổi: "1e999999" là số hữu hạn nhưng int() của nó dựng số triệu chữ số, treo cả tiến trình (review an ninh 15/11)
            n = Decimal(raw) if not isinstance(v, bool) and len(raw) <= MAX_NUMBER_CHARS else None
        except InvalidOperation:
            n = None
        if n is None or not n.is_finite() or abs(n) >= MAX_NUMBER or n.as_tuple().exponent < -6:   # type: ignore[operator]
            raise Invalid(f"{d.label}: cần một số (tối đa 15 chữ số, 6 số lẻ)")
        return int(n) if n == n.to_integral_value() else float(n)
    if d.kind == "date":
        s = str(v).strip()
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
        try:
            day = date(int(m[3]), int(m[2]), int(m[1])) if m else date.fromisoformat(s[:10])
        except ValueError:
            raise Invalid(f"{d.label}: cần ngày dạng 31/12/2026") from None
        return day.isoformat()
    if str(v).strip() not in d.options:
        raise Invalid(f"{d.label}: chọn một trong {', '.join(d.options)}")
    return str(v).strip()


def check(defs: tuple[FieldDef, ...], values: Mapping[str, Any] | None) -> dict[str, Any]:
    """Giá trị trường tuỳ biến đã kiểm kiểu, chỉ gồm ô CÓ giá trị (trống = không đổi). Khoá không có trong định nghĩa đang dùng ⇒ từ chối."""
    by = {d.key: d for d in defs}
    out: dict[str, Any] = {}
    for k, v in (values or {}).items():
        d = by.get(str(k))
        if d is None:
            raise Invalid(f"Không có trường “{k}” — quản trị thêm trường ở Quản trị › Trường riêng của công ty")
        if v is None or (isinstance(v, str) and not v.strip()):
            continue
        out[d.key] = _one(d, v)
    return out


def specs(defs: tuple[FieldDef, ...]) -> list[dict[str, Any]]:
    """Ô form của trường tuỳ biến (cùng hình ``forms.field_specs``) — gom một nhóm gấp lại, sau các ô module khai."""
    out = []
    for d in defs:
        s: dict[str, Any] = {"key": PREFIX + d.key, "label": d.label, "required": False, "help": d.help, "hidden": False, "group": GROUP,
                             "kind": {"text": "text", "number": "number", "date": "date", "choice": "select"}[d.kind]}
        if d.kind == "choice":
            s["options"] = [{"value": o, "label": o} for o in d.options]
        out.append(s)
    return out


def schema_props(defs: tuple[FieldDef, ...]) -> dict[str, dict[str, Any]]:
    """Thuộc tính JSON schema cho công cụ trợ lý / ứng dụng AI — cùng khoá ``x_<khoá>`` với form."""
    out: dict[str, dict[str, Any]] = {}
    for d in defs:
        p: dict[str, Any] = {"title": d.label, "description": (d.help + " " if d.help else "") + "(trường riêng của công ty; trống = bỏ qua)"}
        if d.kind == "number":
            p["type"] = "number"
        elif d.kind == "date":
            p.update(type="string", description=p["description"] + " Ngày dạng YYYY-MM-DD.")
        elif d.kind == "choice":
            p.update(type="string", enum=list(d.options))
        else:
            p["type"] = "string"
        out[PREFIX + d.key] = p
    return out


def shown(fields: Mapping[str, tuple[FieldDef, ...]] | None, subject: str, values: Mapping[str, Any] | None) -> list[tuple[str, str]]:
    """(nhãn, chữ hiện) của các trường ĐANG DÙNG có giá trị trên một bản ghi — cho thẻ chi tiết: ``shown(ctx.p.fields, "<đối tượng>", row["extra"])``.
    Trường đã ngừng không hiện (giá trị vẫn còn trong bảng)."""
    out = []
    for d in of(fields, subject):
        v = (values or {}).get(d.key)
        if v in (None, ""):
            continue
        if d.kind == "date":
            try:
                v = date.fromisoformat(str(v)).strftime("%d/%m/%Y")
            except ValueError:
                pass
        out.append((d.label, str(v)))
    return out
