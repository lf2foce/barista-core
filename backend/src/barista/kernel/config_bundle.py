"""GÓI CẤU HÌNH CÔNG TY (ADR 0263 mục C) — xuất / so / nhập cấu hình (vai, quyền, đội, chuỗi + luật duyệt, cài đặt, từ vựng, mẫu quy trình, luật của gói…)
từ công ty này sang công ty khác (bản thử → bản thật) mà không làm tay lại.

* Không biết module nào: mọi tài nguyên do module tự khai (``Module.config`` → ``registry.CONFIGS``) — truy vấn ``read`` trả mục ở DẠNG ĐẦU VÀO của lệnh
  ``write``; xuất = gọi ``read``; nhập = gọi đúng ``write`` qua executor (``ctx.call`` — quyền + luật duyệt + kiểm hợp lệ của chính lệnh đó).
* ``config_version`` = băm nội dung các khối (không gồm lúc xuất / tên công ty) ⇒ hai công ty cùng cấu hình ra cùng số; nhập xong xuất lại ra đúng số của
  tệp (khi công ty đích không có thêm gì riêng).
* So khác theo KHOÁ TỰ NHIÊN của từng tài nguyên: thêm / đổi / giữ / không ghi đè (``mutable=False``) / chờ mời người. Mục chỉ có ở công ty đích được
  ĐẾM, không xoá. Mục nhắc email chưa là thành viên đang hoạt động của công ty đích ⇒ "cần mời trước": bỏ qua, không tự tạo tài khoản.
* Áp: từng mục một điểm lưu — một mục lỗi (vd đội cha không có) ghi lý do, mục khác vẫn vào; chạy lại cùng tệp chỉ còn "giữ".
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import psycopg

from barista.kernel import packs, registry
from barista.kernel.errors import Conflict, Denied, Invalid, NotFound
from barista.kernel.executor import Ctx, jsonable

FORMAT = "barista.config"
FORMAT_VERSION = 1
MAX_BYTES = 5 * 1024 * 1024
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
STATUS = {"add": "Thêm", "change": "Đổi", "keep": "Giữ nguyên", "locked": "Khác — không ghi đè", "invite": "Cần mời người trước", "failed": "Không ghi được"}
APPLY = ("add", "change")
WRITE_ERRORS = (Invalid, Denied, NotFound, Conflict, psycopg.errors.IntegrityError, psycopg.errors.DataError)


def canon(v: Any) -> str:
    return json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def version(blocks: dict[str, list[dict[str, Any]]]) -> str:
    """Băm NỘI DUNG (khối × mục) — không gồm lúc xuất / tên công ty nguồn."""
    return hashlib.sha256(canon(blocks).encode()).hexdigest()


def resources(on: frozenset[str] | set[str]) -> list[registry.ConfigResource]:
    """Tài nguyên của các gói ĐANG BẬT, theo thứ tự nhập."""
    return sorted((c for c in registry.CONFIGS.values() if packs.module_on(frozenset(on), c.module)), key=lambda c: (c.order, c.key))


def _norm(item: Any) -> dict[str, Any]:
    out = json.loads(canon(jsonable(item)))
    if not isinstance(out, dict):
        raise TypeError("mục cấu hình phải là dict (đầu vào của lệnh ghi)")
    return out


async def collect(ctx: Ctx, on: frozenset[str] | set[str]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    """(khối theo tài nguyên, tài nguyên KHÔNG đọc được kèm lý do) — đọc bằng quyền người gọi; thiếu quyền một khối ⇒ báo, không im lặng bỏ."""
    blocks: dict[str, list[dict[str, Any]]] = {}
    skipped: dict[str, str] = {}
    for c in resources(on):
        try:
            items = await ctx.call(c.read)
        except (Denied, NotFound) as e:
            skipped[c.key] = str(e)
            continue
        blocks[c.key] = [_norm(i) for i in items]
    return blocks, skipped


async def export(ctx: Ctx, on: frozenset[str] | set[str], company: str) -> dict[str, Any]:
    blocks, skipped = await collect(ctx, on)
    labels = {c.key: c.label for c in resources(on)}
    return {"format": FORMAT, "format_version": FORMAT_VERSION, "config_version": version(blocks), "exported_at": datetime.now(UTC).isoformat(),
            "company": company, "packs": sorted(on), "skipped": skipped,
            "resources": {k: {"label": labels[k], "items": v} for k, v in blocks.items()}}


def parse(data: bytes | bytearray) -> dict[str, Any]:
    """Đọc + kiểm tệp gói cấu hình: đúng dạng, ``config_version`` khớp nội dung (tệp sửa tay sau khi xuất ⇒ từ chối — tệp "đã duyệt" phải là đúng tệp đã
    xuất). Trả bản đã chuẩn hoá ``{config_version, company, packs, blocks}``."""
    if len(data) > MAX_BYTES:
        raise Invalid(f"Tệp cấu hình quá {MAX_BYTES // (1024 * 1024)} MB")
    try:
        raw = json.loads(bytes(data).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise Invalid("Không đọc được tệp — cần tệp .json xuất từ Quản trị › Cấu hình công ty") from e
    if not isinstance(raw, dict) or raw.get("format") != FORMAT or not isinstance(raw.get("resources"), dict):
        raise Invalid("Không phải tệp cấu hình công ty của Barista (thiếu format / resources)")
    if raw.get("format_version") != FORMAT_VERSION:
        raise Invalid(f"Tệp cấu hình phiên bản định dạng {raw.get('format_version')} — bản này đọc phiên bản {FORMAT_VERSION}")
    blocks: dict[str, list[dict[str, Any]]] = {}
    for key, block in raw["resources"].items():
        items = block.get("items") if isinstance(block, dict) else None
        if not isinstance(key, str) or not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
            raise Invalid(f"Khối {key!r} của tệp không đúng dạng (cần {{label, items: [...]}})")
        blocks[key] = [_norm(i) for i in items]
    if version(blocks) != raw.get("config_version"):
        raise Invalid("config_version không khớp nội dung tệp — tệp đã bị sửa sau khi xuất; xuất lại từ công ty nguồn")
    return {"config_version": raw["config_version"], "company": str(raw.get("company") or "")[:200], "packs": [str(p) for p in raw.get("packs") or []],
            "blocks": blocks}


def people(item: Any) -> set[str]:
    """Email người trong một mục (người duyệt ``user:…``, thành viên…) — khoá ánh xạ người giữa hai công ty."""
    if isinstance(item, str):
        return {m.lower() for m in EMAIL.findall(item)}
    if isinstance(item, dict):
        return set().union(*(people(v) for v in item.values())) if item else set()
    if isinstance(item, list):
        return set().union(*(people(v) for v in item)) if item else set()
    return set()


def _key(c: registry.ConfigResource, item: dict[str, Any]) -> str:
    return canon([item.get(f) for f in c.match])


def _name(c: registry.ConfigResource, item: dict[str, Any]) -> str:
    return " · ".join(str(item.get(f)) for f in c.match if item.get(f) not in (None, "")) or "(mặc định)"


@dataclass
class Line:
    resource: str
    label: str      # nhãn tài nguyên
    name: str       # khoá tự nhiên đọc được
    status: str     # add | change | keep | locked | invite | failed
    detail: str = ""
    item: dict[str, Any] = field(default_factory=dict)


@dataclass
class Plan:
    version: str                     # config_version của tệp
    current: str                     # config_version của công ty đích lúc so
    lines: list[Line]
    blocks: list[dict[str, Any]]     # mỗi tài nguyên: {key, label, add, change, keep, locked, invite, only_here, note}
    invite: list[str]                # email cần mời trước

    def count(self, *status: str) -> int:
        return sum(1 for x in self.lines if x.status in status)


async def plan(ctx: Ctx, bundle: dict[str, Any], on: frozenset[str] | set[str], members: set[str]) -> Plan:
    """So tệp với cấu hình HIỆN TẠI của công ty (chỉ đọc). ``members``: email thành viên đang hoạt động của công ty đích."""
    current, unreadable = await collect(ctx, on)
    have_res = {c.key: c for c in resources(on)}
    lines: list[Line] = []
    blocks: list[dict[str, Any]] = []
    invite: set[str] = set()
    for key in sorted(bundle["blocks"], key=lambda k: (have_res[k].order, k) if k in have_res else (10**9, k)):
        items = bundle["blocks"][key]
        c = have_res.get(key)
        if c is None:
            known = registry.CONFIGS.get(key)
            note = ("gói của phần này chưa bật ở công ty này — bật gói rồi nhập lại" if known is not None
                    else "bản Barista này không có phần cấu hình này")
            blocks.append({"key": key, "label": known.label if known else key, "items": len(items), "note": note})
            continue
        if key in unreadable:
            blocks.append({"key": key, "label": c.label, "items": len(items), "note": f"không đọc được ở công ty này: {unreadable[key]}"})
            continue
        have = {_key(c, i): i for i in current.get(key, [])}
        mine: list[Line] = []
        for item in items:
            k = _key(c, item)
            missing = sorted(people(item) - members)
            if k in have and canon(have[k]) == canon(item):
                mine.append(Line(key, c.label, _name(c, item), "keep", item=item))
            elif missing:
                invite.update(missing)
                mine.append(Line(key, c.label, _name(c, item), "invite", "cần mời trước: " + ", ".join(missing), item))
            elif k not in have:
                mine.append(Line(key, c.label, _name(c, item), "add", item=item))
            elif not c.mutable:
                mine.append(Line(key, c.label, _name(c, item), "locked", "đã có ở công ty này với nội dung khác — phần này không ghi đè, sửa tay nếu cần",
                                 item))
            else:
                mine.append(Line(key, c.label, _name(c, item), "change", _diff(have[k], item), item))
        lines += mine
        only = len(set(have) - {_key(c, i) for i in items})
        blocks.append({"key": key, "label": c.label, "items": len(items), **{s: sum(1 for x in mine if x.status == s) for s in STATUS if s != "failed"},
                       "only_here": only, "note": f"{only} mục chỉ có ở công ty này — giữ nguyên, không xoá" if only else ""})
    for key, c in have_res.items():
        if key not in bundle["blocks"] and current.get(key):
            blocks.append({"key": key, "label": c.label, "items": 0, "only_here": len(current[key]), "note": "tệp không có phần này — giữ nguyên"})
    return Plan(bundle["config_version"], version(current), lines, blocks, sorted(invite))


def _diff(was: dict[str, Any], now: dict[str, Any]) -> str:
    parts = [f"{k}: {_short(was.get(k))} → {_short(now.get(k))}" for k in sorted(set(was) | set(now)) if canon(was.get(k)) != canon(now.get(k))]
    return "; ".join(parts)[:600]


def _short(v: Any) -> str:
    s = v if isinstance(v, str) else canon(v)
    s = s.replace("\n", " ⏎ ")
    return s if len(s) <= 80 else s[:77] + "…"


async def apply(ctx: Ctx, p: Plan) -> list[Line]:
    """Ghi các mục thêm / đổi bằng ĐÚNG lệnh ``write`` của tài nguyên, theo thứ tự đã so (``order`` của tài nguyên). Mỗi mục một điểm lưu: lỗi ⇒ mục đó
    thành ``failed`` kèm lý do, phần đã ghi của mục khác giữ nguyên. Trả các dòng đã ghi / lỗi."""
    done: list[Line] = []
    for line in p.lines:
        if line.status not in APPLY:
            continue
        c = registry.CONFIGS[line.resource]
        try:
            async with ctx.conn.transaction():
                await ctx.call(c.write, **line.item)
            done.append(line)
        except WRITE_ERRORS as e:
            line.status, line.detail = "failed", str(e)[:600]
            done.append(line)
    return done
