"""Nhập liệu hàng loạt (Excel / dán từ bảng tính) — chạy ĐÚNG lệnh như nhập tay, từng dòng một.

* Cột khớp theo nhãn trường hoặc tên khoá (bỏ dấu, không phân biệt hoa thường); ô "lựa chọn" nhận cả chữ hiển thị.
* Chạy thử (``dry_run``): chạy thật từng dòng trong điểm lưu rồi bỏ hết ⇒ báo lỗi y như lúc nhập thật (trùng SĐT,
  căn đã bán, sai quyền…) mà không ghi gì.
* Nhập thật: mỗi dòng một giao dịch riêng với khoá chống lặp = nội dung dòng ⇒ nhập lại cùng file không nhân đôi,
  sửa dòng lỗi rồi nhập lại cả file chỉ chạy những dòng chưa vào. Mỗi lần nhập thật ghi NHẬT KÝ NHẬP (``core.import_runs`` + từng dòng — 0168 §2):
  ai, lệnh, file (sha256), số dòng vào / lỗi, dòng nào lỗi vì sao.
* TỰ KHỚP (0226): file không cần đúng mẫu — ``analyze`` đọc mọi trang tính, tìm dòng tiêu đề, khớp cột vào trường (``import_match``), ``run_plan`` chạy theo
  ánh xạ người đã xác nhận: chuẩn hoá ô lựa chọn, nhận ra dòng đã có (``Op.import_key``) → thêm mới / cập nhật / trùng, rồi chạy thử / nhập thật bằng ĐÚNG
  đường ``_run_rows`` ở trên. Ánh xạ đã xác nhận nhớ theo (công ty, lệnh, bố cục tiêu đề) ở ``core.import_mappings``.
"""

import csv
import hashlib
import json
import logging
import re
import unicodedata
import zipfile
import zlib
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from io import BytesIO
from typing import Any

import psycopg
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.utils import get_column_letter
from openpyxl.utils.exceptions import InvalidFileException
from psycopg.types.json import Jsonb

from barista.db import db
from barista.kernel import ai, clock, executor, registry
from barista.kernel import import_match as im
from barista.kernel.errors import Denied
from barista.kernel.executor import Result, check_deferred, invoke, jsonable, map_error
from barista.kernel.fields import company_country, phone_display
from barista.kernel.forms import import_specs
from barista.kernel.principal import Principal
from barista.kernel.text import name_key

log = logging.getLogger("barista.imports")

KIND_HELP = {
    "money": "Số tiền — 4.500.000.000 hoặc 4,5 tỷ / 90tr",
    "date": "Ngày — dd/mm/yyyy",
    "phone": "Số điện thoại — 0912345678",
    "percent": "Phần trăm — 2 hoặc 2%",
    "number": "Số",
    "bool": "có / không",
    "text": "Chữ",
    "textarea": "Chữ (dài)",
    "email": "Email",
    "select": "Một trong các giá trị bên phải",
    "multiselect": "Nhiều giá trị, cách nhau dấu phẩy",
}
_TRUE = {"co", "x", "yes", "true", "1", "dung"}
_FALSE = {"khong", "no", "false", "0", "sai", ""}


def norm(s: Any) -> str:
    t = unicodedata.normalize("NFD", str(s)).replace("đ", "d").replace("Đ", "D")
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", t.lower())


# Lỗi ``read_grid`` ném khi file KHÔNG đọc được (đã thử với file rác): zip hỏng / cắt cụt (BadZipFile, zlib.error, EOFError), thiếu phần bắt buộc của .xlsx (KeyError,
# OSError "no valid workbook part", InvalidFileException), XML hỏng (ParseError ⊂ SyntaxError), không có trang tính (IndexError), CSV không phải UTF-8 (ValueError ⊃
# UnicodeDecodeError), csv.Error (ô quá 128 KB, dấu phân cách lạ — KHÔNG là ValueError), AttributeError / TypeError (openpyxl gặp XML hỏng nửa chừng). Chỉ bọc đúng lời gọi
# ``read_grid`` ở http_api — không bọc cả hàm xử lý. Lỗi KHÁC (vd MemoryError, lỗi lập trình) KHÔNG nằm đây ⇒ lộ ra thành 500 chứ không bị che thành "file hỏng".
UNREADABLE = (zipfile.BadZipFile, zlib.error, EOFError, KeyError, IndexError, OSError, SyntaxError, ValueError, InvalidFileException, csv.Error, AttributeError, TypeError)


def csv_grid(data: bytes) -> list[list[Any]]:
    text = data.decode("utf-8-sig")
    try:
        dialect: Any = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [[c if c != "" else None for c in r] for r in csv.reader(text.splitlines(), dialect)]


def read_grid(data: bytes) -> list[list[Any]]:
    """Trang tính đầu thành LƯỚI ô (mọi dòng, kể cả phần đầu file trước dòng tiêu đề) — chỉ số dòng = số dòng Excel − 1. File không phải .xlsx
    (không mở đầu bằng "PK" của zip) ⇒ đọc như CSV (UTF-8, có / không BOM; dấu phân cách ``,`` ``;`` hoặc tab — ngân hàng xuất kiểu nào cũng được)."""
    if not data.startswith(b"PK"):
        return csv_grid(data)
    wb = load_workbook(BytesIO(data), read_only=True, data_only=True)
    return [list(values) for values in wb.worksheets[0].iter_rows(values_only=True)]


def rows_of(grid: list[list[Any]]) -> list[dict[str, Any]]:
    """Lưới ô → dòng: dòng đầu có chữ = tiêu đề. Mỗi dòng → {tiêu đề: giá trị, "_line": số dòng Excel}."""
    headers: list[str] | None = None
    rows: list[dict[str, Any]] = []
    for i, values in enumerate(grid, 1):
        if headers is None:
            if any(v not in (None, "") for v in values):
                headers = [str(v).strip() if v is not None else "" for v in values]
            continue
        if all(v in (None, "") for v in values):
            continue
        row = {h: v for h, v in zip(headers, values, strict=False) if h}
        row["_line"] = i
        rows.append(row)
    return rows


def grid_of(rows: list[dict[str, Any]]) -> list[list[Any]]:
    """Dòng dán từ bảng tính ({tiêu đề: giá trị}) → lưới ô (dòng đầu = tiêu đề) cho lệnh đọc lưới (``import_rows``)."""
    headers = list(dict.fromkeys(k for r in rows for k in r if k != "_line"))
    return [headers, *([r.get(h) for h in headers] for r in rows)]


def read_xlsx(data: bytes) -> list[dict[str, Any]]:
    return rows_of(read_grid(data))


def map_rows(op: registry.Op, rows: list[dict[str, Any]], terms: Mapping[str, str] | None = None) -> tuple[list[tuple[int, dict[str, Any]]], list[str]]:
    """``terms``: nhãn riêng của công ty (0124) — cột nhận theo nhãn công ty, vẫn nhận nhãn trung tính (file mẫu cũ) và khoá."""
    specs = {s["key"]: s for s in import_specs(op, terms)}
    lookup: dict[str, str] = {}
    for key, s in specs.items():
        for alias in s.get("aliases") or ():
            lookup[norm(alias)] = key
        lookup[norm(key)] = key
    for s in import_specs(op):   # nhãn trung tính trước, nhãn công ty sau ⇒ trùng thì nhãn công ty thắng
        lookup.setdefault(norm(s["label"]), s["key"])
    for key, s in specs.items():
        lookup[norm(s["label"])] = key
    unknown: set[str] = set()
    out = []
    for idx, row in enumerate(rows, 1):
        line = int(row.get("_line") or idx)
        inp: dict[str, Any] = {}
        for header, value in row.items():
            if header == "_line":
                continue
            key = lookup.get(norm(header))
            if key is None:
                unknown.add(header)
                continue
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            s = specs[key]
            if s["kind"] == "select" and "options" in s:
                by_label = {norm(o["label"]): o["value"] for o in s["options"]} | {norm(o["value"]): o["value"] for o in s["options"]}
                value = by_label.get(norm(value), value)
            elif s["kind"] == "multiselect" and isinstance(value, str):
                value = [v.strip() for v in value.split(",") if v.strip()]
            elif s["kind"] == "bool" and isinstance(value, str):
                n = norm(value)
                value = True if n in _TRUE else False if n in _FALSE else value
            elif s["kind"] in ("text", "textarea", "phone") and isinstance(value, int | float):
                value = str(int(value)) if float(value).is_integer() else str(value)
            inp[key] = value
        out.append((line, inp))
    return out, sorted(unknown)


def _row_key(command: str, inp: dict[str, Any]) -> str:
    body = json.dumps(jsonable(inp), sort_keys=True, ensure_ascii=False)
    return f"import:{command}:{hashlib.sha256(body.encode()).hexdigest()[:32]}"


async def run_import(p: Principal, command: str, rows: list[dict[str, Any]], *, dry_run: bool,
                     grid: list[list[Any]] | None = None, file_name: str = "", file_sha256: str = "", historical: bool = False) -> Result:
    """``historical``: CHỈ mã trong tiến trình (lớp nạp dữ liệu hệ cũ — 0185; cửa HTTP không truyền): mọi dòng chạy ở chế độ nạp lịch sử — dữ liệu tham chiếu,
    không thông báo / nhắc việc / việc theo dõi."""
    op = registry.get(command)
    if op is None or op.kind != "command" or not op.importable:
        return Result("not_found", error=f"Lệnh {command} không nhận nhập hàng loạt")
    if not p.can(op.resource, op.action):
        return Result("denied", error=f"Không có quyền {op.title}")
    if op.import_table is not None:   # dòng tham chiếu nhau (kế hoạch…) — chỉ nhập được bằng màn nhập tự khớp, nơi nhìn cả file
        return Result("invalid", error=f"{op.title}: nhập bằng màn “Nhập từ Excel” (cần nhìn cả file để hiểu mã dòng / việc trước), không nhập theo mẫu từng dòng")
    if op.import_rows is not None:   # lệnh đọc LƯỚI ô theo cấu hình công ty (vd mẫu cột sao kê từng ngân hàng) — quyền người nhập, chỉ đọc
        try:
            async with db.tenant_tx(p, rollback=True) as conn:
                got = await op.import_rows(executor.core_ctx(conn, p, module=op.module), grid if grid is not None else grid_of(rows))
            rows = got or rows   # bộ đọc không nhận ra file ⇒ cách đọc chung (dòng đầu có chữ là tiêu đề)
        except Exception as e:   # lỗi bộ đọc / lệnh nhập ⇒ Result đã phân loại (map_error), không ném tiếp
            return map_error(e, op.input)
    mapped, unknown = map_rows(op, rows, p.vocab)
    results: list[dict[str, Any]] = []
    token = executor.FROM_FILE.set(grid is not None)   # dòng của TỆP tải lên (có lưới ô) — lệnh đọc ctx.from_file, không tin ô đầu vào nào
    try:
        with executor.historical(historical or executor.HISTORICAL.get()):
            out = await _run_rows(p, op, command, mapped, unknown, results, dry_run=dry_run)
    finally:
        executor.FROM_FILE.reset(token)
    if not dry_run and out.status == "ok":
        await record_run(p, command, mapped, results, file_name=file_name, file_sha256=file_sha256)
    return out


async def record_run(p: Principal, command: str, mapped: list[tuple[int, dict[str, Any]]], results: list[dict[str, Any]], *,
                     file_name: str = "", file_sha256: str = "") -> None:
    """Nhật ký nhập (0168 §2): một dòng lần nhập + từng dòng (số dòng Excel, kết quả, lỗi, dòng như đã gửi, khoá chống lặp). Giao dịch riêng SAU
    khi các dòng đã chạy — nhật ký hỏng không làm hỏng lần nhập (đã ghi), chỉ ghi log."""
    if not results:
        return
    ok = sum(1 for r in results if r["status"] in ("ok", "pending_approval"))
    try:
        async with db.tenant_tx(p) as conn:
            got = await (await conn.execute(
                """INSERT INTO core.import_runs (command, file_name, file_sha256, total, ok, failed, by_user, finished_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, now()) RETURNING id""",
                (command, file_name[:300], file_sha256 if re.fullmatch(r"[0-9a-f]{64}", file_sha256) else "", len(results), ok, len(results) - ok,
                 p.user_id))).fetchone()
            assert got is not None
            run_id = got["id"]
            data = dict(mapped)
            async with conn.cursor() as cur:
                await cur.executemany(
                    "INSERT INTO core.import_run_rows (run_id, line, status, error, data, ref) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    [(run_id, r["line"], r["status"], (r.get("error") or "")[:2000], Jsonb(executor._loggable(command, data.get(r["line"], {}))),
                      _row_key(command, data.get(r["line"], {}))) for r in results])
    except Exception:  # nhật ký hỏng không được che kết quả lần nhập
        log.exception("không ghi được nhật ký nhập cho %s", command)


async def _run_rows(p: Principal, op: registry.Op, command: str, mapped: list[tuple[int, dict[str, Any]]], unknown: list[str],
                    results: list[dict[str, Any]], *, dry_run: bool) -> Result:
    if dry_run:
        try:
            async with db.tenant_tx(p, rollback=True) as conn:
                ctx = executor.core_ctx(conn, p, module=op.module)
                # lệnh công ty đặt "phải duyệt": chạy thật mỗi dòng sẽ thành phiếu ⇒ xem trước vẫn chạy thử để bắt lỗi dữ liệu, báo "chờ duyệt"
                # lệnh khai ``sensitive`` (0029): chỉ dòng nhạy cảm (vd mời lại người đã có với vai khác) mới thành phiếu
                chain = await executor.approval_chain(conn, p, command)
                for line, inp in mapped:
                    out: Any = None
                    ok_status = "ok"
                    try:
                        async with conn.transaction():
                            if chain and (op.sensitive is None or await op.sensitive(ctx, op.input.model_validate(inp))):
                                ok_status = "pending_approval"
                            out = await invoke(ctx, command, inp, events=[], approval_handled=True)
                            await check_deferred(conn)
                            raise psycopg.Rollback()
                    except Exception as e:   # lỗi một dòng ⇒ ghi vào kết quả của DÒNG đó (map_error), các dòng khác vẫn chạy thử
                        r = map_error(e, op.input)
                        results.append({"line": line, "status": r.status, "error": r.error})
                    else:
                        results.append({"line": line, "status": ok_status, "output": jsonable(out)})
        except Exception as e:   # lỗi ngoài từng dòng ⇒ Result đã phân loại (map_error), giao dịch nhập quay lui
            return map_error(e, op.input)
    else:
        for line, inp in mapped:
            r = await executor.run(p, command, inp, key=_row_key(command, inp), keep_output=False)   # chỉ đọc trạng thái / lỗi từng dòng (0177)
            results.append({"line": line, "status": r.status, "error": r.error, "replayed": r.replayed})
    ok = sum(1 for r in results if r["status"] in ("ok", "pending_approval"))
    return Result("ok", {"command": command, "title": op.title, "dry_run": dry_run, "total": len(results), "ok": ok,
                         "failed": len(results) - ok, "unknown_columns": unknown, "rows": results})


_THIN = Side(style="thin", color="999999")
_BOX = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)


def template_xlsx(op: registry.Op, terms: Mapping[str, str] | None = None) -> bytes:
    specs = [s for s in import_specs(op, terms) if not s["hidden"]]
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Nhập liệu"
    for j, s in enumerate(specs, 1):
        c = ws.cell(row=1, column=j, value=s["label"] + (" *" if s["required"] else ""))
        c.font = Font(bold=True)
        c.border = _BOX
        c.alignment = Alignment(wrap_text=True, vertical="center")
        ex = s.get("example")
        e = ws.cell(row=2, column=j, value=", ".join(ex) if isinstance(ex, list) else ex)
        e.border = _BOX
        ws.column_dimensions[get_column_letter(j)].width = max(14, min(36, len(s["label"]) + 6))
    ws.freeze_panes = "A2"
    guide = wb.create_sheet("Hướng dẫn")
    guide.append(["Cột", "Bắt buộc", "Kiểu", "Giá trị hợp lệ", "Ghi chú"])
    for cell in guide[1]:
        cell.font = Font(bold=True)
    for s in specs:
        opts = ", ".join(o["label"] for o in s.get("options", []))
        guide.append([s["label"], "có" if s["required"] else "", KIND_HELP.get(s["kind"], s["kind"]), opts, s["help"]])
    for j, w in enumerate((28, 10, 36, 40, 50), 1):
        guide.column_dimensions[get_column_letter(j)].width = w
    guide.append([])
    guide.append([f"Dòng 2 ở trang “Nhập liệu” là VÍ DỤ — xoá trước khi nhập. Lệnh: {op.title}."])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ═══ TỰ KHỚP (0226) ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════
# Đọc file → đoán ánh xạ (analyze) → người xác nhận → chạy thử / nhập thật theo ánh xạ (run_plan). Cùng đường ``_run_rows`` / ``record_run`` ở trên.

AI_MODEL: Any = None   # chỉ cho test: model giả thay model thật (``ai.run(model=…)``)
SHOW_ROWS = 8          # số dòng đầu hiện ở bản xem trước
KEEP_ROWS = 500        # trần số dòng chi tiết trả về (lỗi / cập nhật / trùng) — đếm vẫn đủ


def _specs(op: registry.Op, terms: Mapping[str, str] | None) -> list[dict[str, Any]]:
    return [s for s in import_specs(op, terms) if not s["hidden"] and s["kind"] != "rows"]


async def _load_options(p: Principal, op: registry.Op, terms: Mapping[str, str] | None) -> dict[str, list[tuple[str, str]]]:
    """Danh sách chọn từ ``options_query`` của các ô (mã dự án, người phụ trách…) — bằng quyền người nhập; ô có danh sách tuỳ giá trị ô khác (``options_with``) bỏ qua."""
    out: dict[str, list[tuple[str, str]]] = {}
    for s in _specs(op, terms):
        q = s.get("options_query")
        if not q or s.get("options") or s.get("options_with"):
            continue
        r = await executor.query(p, q, {})
        if r.ok and isinstance(r.output, list):
            out[s["key"]] = [(str(o["value"]), str(o["label"])) for o in r.output[:5000] if isinstance(o, dict) and "value" in o]
    return out


def _fields_of(op: registry.Op, terms: Mapping[str, str] | None, options: dict[str, list[tuple[str, str]]] | None = None) -> list[im.Field]:
    neutral = {s["key"]: s["label"] for s in import_specs(op)}
    out = []
    for s in _specs(op, terms):
        opts = (options or {}).get(s["key"])
        out.append(im.make_field(s, neutral.get(s["key"]), im.OptionIndex(opts) if opts else None))
    return out


def _known_headers(fields: list[im.Field]) -> set[str]:
    return {name_key(n) for f in fields for n in f.names} | {name_key(syn) for f in fields for syn in f.synonyms}


def _fit(cols: list[im.Column], matches: list[im.Match], fields: list[im.Field]) -> float:
    """Trang tính hợp lệnh này đến đâu (0..1): phần cột giải thích được × mức phủ trường bắt buộc."""
    live = [c for c in cols if not c.ignore and c.n]
    if not live:
        return 0.0
    explained = sum(m.conf for m in matches) / len(live)
    req = [f for f in fields if f.required]
    covered = {m.field for m in matches}
    cover = sum(1 for f in req if f.key in covered) / len(req) if req else 1.0
    return round(explained * (0.4 + 0.6 * cover), 3)


def _import_op(p: Principal, command: str) -> tuple[registry.Op | None, Result | None]:
    op = registry.get(command)
    if op is None or op.kind != "command" or not op.importable:
        return None, Result("not_found", error=f"Lệnh {command} không nhận nhập hàng loạt")
    if not p.can(op.resource, op.action):
        return None, Result("denied", error=f"Không có quyền {op.title}")
    return op, None


async def _saved(p: Principal, command: str) -> list[dict[str, Any]]:
    async with db.tenant_tx(p, rollback=True) as conn:
        cur = await conn.execute("""SELECT id, signature, name, columns, values, uses, last_used_at FROM core.import_mappings
                                    WHERE command = %s ORDER BY last_used_at DESC LIMIT 50""", (command,))
        return list(await cur.fetchall())


async def _locale(p: Principal) -> tuple[str, Any]:
    """(quốc gia, múi giờ) của công ty — dữ liệu sẵn có (``core.tenants.country`` / ``timezone``, 0169 / 0199), lõi nạp ở mỗi giao dịch công ty."""
    async with db.tenant_tx(p, rollback=True):
        return company_country(), clock.zone()


DATE_ORDER_KEY = "__date_order"   # trong ``core.import_mappings.values``: {khoá trường ngày: dmy | mdy} — cách đọc ngày người đã xác nhận


def _remembered(saved: list[dict[str, Any]], sig: str) -> tuple[dict[str, str | None], dict[str, dict[str, str]], dict[str, Any] | None]:
    """(tiêu đề đã chuẩn hoá → khoá trường | None, chữ ô → giá trị chọn, bản ghi trùng chữ ký nếu có). Chữ ký khớp hẳn ⇒ dùng đúng bố cục đó; không ⇒ gộp trí nhớ
    từng tiêu đề của các bố cục đã nhập (mới nhất thắng) — cột quen vẫn được nhận dù file có thêm / bớt cột khác."""
    exact = next((r for r in saved if r["signature"] == sig), None)
    rows = [exact] if exact else list(reversed(saved))
    cols: dict[str, str | None] = {}
    vals: dict[str, dict[str, str]] = {}
    for r in rows:
        for header, key in (r["columns"] or {}).items():
            cols[name_key(header)] = key
        for key, m in (r["values"] or {}).items():
            vals.setdefault(key, {}).update(m)
    return cols, vals, exact


def _date_orders(resolutions: dict[str, Any]) -> dict[str, str]:
    return {k: v for k, v in (resolutions.get(DATE_ORDER_KEY) or {}).items() if v in ("dmy", "mdy")}


def _date_report(infos: dict[str, im.DateInfo], fmap: dict[str, im.Field]) -> list[dict[str, Any]]:
    """Cách đọc ngày từng cột cho màn khớp cột: thứ tự đang dùng, vì sao, ví dụ cụ thể cho CẢ HAI cách (người bấm đổi ngay). Cột chỉ có ô ngày thật của Excel ⇒ không hiện."""
    return [{"field": k, "label": fmap[k].label, "order": i.order, "decided": i.decided, "text_dates": i.text_dates, "sample": i.sample, "examples": i.examples}
            for k, i in infos.items() if i.text_dates]


async def save_mapping(p: Principal, command: str, sig: str, name: str, columns: dict[str, str | None], values: dict[str, dict[str, str]]) -> None:
    """Nhớ ánh xạ đã xác nhận (0226): bảng của lõi, giao dịch riêng SAU lần nhập thật (như nhật ký nhập) — hỏng không làm hỏng lần nhập."""
    try:
        async with db.tenant_tx(p) as conn:
            got = await (await conn.execute("SELECT id, values FROM core.import_mappings WHERE command = %s AND signature = %s FOR UPDATE", (command, sig))).fetchone()
            if got is None:
                await conn.execute("""INSERT INTO core.import_mappings (command, signature, name, columns, values, created_by) VALUES (%s, %s, %s, %s, %s, %s)""",
                                   (command, sig, name[:300], Jsonb(columns), Jsonb(values), p.user_id))
            else:
                merged = {k: {**(got["values"] or {}).get(k, {}), **v} for k, v in values.items()} | {k: v for k, v in (got["values"] or {}).items() if k not in values}
                await conn.execute("""UPDATE core.import_mappings SET columns = %s, values = %s, uses = uses + 1, last_used_at = now() WHERE id = %s""",
                                   (Jsonb(columns), Jsonb(merged), got["id"]))
    except Exception:
        log.exception("không nhớ được ánh xạ nhập cho %s", command)


@dataclass
class _SheetInfo:
    sheet: im.Sheet
    header: im.Header | None = None
    rows: im.DataRows | None = None
    cols: list[im.Column] = field(default_factory=list)
    fit: float = 0.0


def read_all(data: bytes) -> list[im.Sheet]:
    """MỌI trang tính của file (.xlsx / .csv). Chỉ bọc lời gọi NÀY bằng ``UNREADABLE`` ở cửa HTTP — không bọc phần xử lý."""
    return im.read_sheets(data, csv_grid)


def _scan(sheets: list[im.Sheet], fields: list[im.Field]) -> list[_SheetInfo]:
    known = _known_headers(fields)
    infos = []
    for sh in sheets:
        info = _SheetInfo(sh, im.detect_header(sh.grid, known))
        if info.header is not None:
            info.rows = im.data_rows(sh.grid, info.header)
            info.cols = im.profile_columns(info.header, info.rows)
            for c in info.cols:   # cột "STT" thường là số thứ tự để bỏ — trừ khi lệnh này CÓ trường đặt tên đúng thế (kế hoạch: STT / WBS là mã dòng)
                if c.ignore and name_key(c.header) in known:
                    c.ignore = ""
            info.fit = _fit(info.cols, im.match_columns(info.cols, fields)[0], fields)
        infos.append(info)
    return infos


def _suggest(p: Principal, infos: list[_SheetInfo], exclude: str) -> dict[str, dict[str, Any]]:
    """Trang tính → lệnh nhập hợp nhất (theo độ giống tiêu đề), chỉ lệnh người nhập được chạy và nhận file tự khớp. Chỉ trả khi đủ giống."""
    cands = []
    for name, op in registry.OPS.items():
        if op.kind == "command" and op.importable and op.import_rows is None and not op.internal and p.can(op.resource, op.action):
            cands.append((name, op, _fields_of(op, p.vocab)))
    out: dict[str, dict[str, Any]] = {}
    for info in infos:
        if not info.cols:
            continue
        best = max(((_fit(info.cols, im.match_columns(info.cols, fs)[0], fs), name, op) for name, op, fs in cands), key=lambda t: t[0], default=None)
        if best and best[0] >= 0.35:
            out[info.sheet.name] = {"command": best[1], "title": best[2].title, "fit": best[0], "same": best[1] == exclude}
    return out


def _ai_state(state: str, **extra: Any) -> dict[str, Any]:
    return {"state": state, **extra}


async def analyze(p: Principal, command: str, sheets: list[im.Sheet], *, sheet: str | None = None, file_name: str = "") -> Result:
    """Bước 1 của nhập tự khớp: đọc MỌI trang tính, tìm dòng tiêu đề, đoán ánh xạ cột → trường. Chưa chạy gì, chưa ghi gì."""
    op, err = _import_op(p, command)
    if err is not None or op is None:
        return err or Result("not_found", error=command)
    if op.import_rows is not None:   # công ty có bộ đọc riêng cho lệnh này (mẫu sao kê từng ngân hàng) ⇒ không tự khớp, đi đường cũ
        return Result("ok", {"auto": False, "command": command, "title": op.title, "reason": "Lệnh này đọc file theo mẫu riêng của công ty"})
    options = await _load_options(p, op, p.vocab)
    fields = _fields_of(op, p.vocab, options)
    infos = _scan(sheets, fields)
    live = [i for i in infos if i.cols]
    if not live:
        return Result("invalid", error="Không tìm thấy bảng dữ liệu (dòng tiêu đề) trong file")
    chosen = next((i for i in live if i.sheet.name == sheet), None) or max(live, key=lambda i: i.fit)
    header, rows, cols = chosen.header, chosen.rows, chosen.cols
    assert header is not None and rows is not None
    sig = im.signature(header)
    remembered, resolutions, exact = _remembered(await _saved(p, command), sig)
    matches, dropped = im.match_columns(cols, fields, remembered)
    # model: CHỈ cho cột còn lại (đã qua tên + nội dung + trí nhớ), chỉ gửi tiêu đề + vài ô mẫu
    pool_cols, pool_fields = im.ai_pool(cols, {m.col for m in matches}, fields, {m.field for m in matches}, dropped)
    ai_info = _ai_state("none")
    if pool_cols and pool_fields:
        prompt = im.ai_request(pool_cols, pool_fields)
        from pydantic_ai.usage import RunUsage
        usage = RunUsage()
        try:
            got = await ai.run(executor.core_prep(p, op.module), prompt, instructions=im.AI_INSTRUCTIONS, output_type=im.AIMapping, category="core:import_map",
                               modules=[op.module], model=AI_MODEL, usage=usage)
            extra = im.ai_apply(got, pool_cols, pool_fields)
            matches += extra
            ai_info = _ai_state("used", columns=len(pool_cols), fields=len(pool_fields), matched=len(extra), input_chars=len(prompt) + len(im.AI_INSTRUCTIONS),
                                tokens={"input": usage.input_tokens, "output": usage.output_tokens, "requests": usage.requests})
        except Denied as e:   # công ty tắt AI / hết hạn mức ⇒ vẫn khớp được bằng tên + nội dung
            ai_info = _ai_state("off", reason=str(e))
        except Exception:   # nhà cung cấp lỗi không được chặn nhập file
            log.exception("model khớp cột lỗi (%s)", command)
            ai_info = _ai_state("error", columns=len(pool_cols))
    order = {f.key: i for i, f in enumerate(fields)}
    matches.sort(key=lambda m: order.get(m.field, 99))
    country, tz = await _locale(p)
    fmap = {f.key: f for f in fields}
    cells = {m.field: [r[m.col] for _, r in rows.rows if m.col < len(r) and not im.blank(r[m.col])] for m in matches}
    date_infos: dict[str, im.DateInfo] = {}
    for key, vals in cells.items():
        f = fmap[key]
        if f.kind == "date" or im.is_stamp_field(key, f.kind):
            date_infos[key] = im.decide_order(vals, country, _date_orders(resolutions).get(key))
    taken = {m.field for m in matches}
    suggestions = _suggest(p, infos, command)
    return Result("ok", {
        "auto": True, "command": command, "title": op.title, "file_name": file_name, "signature": sig,
        "sheets": [{"name": i.sheet.name, "rows": len(i.rows.rows) if i.rows else 0, "header_line": i.header.line if i.header else None,
                    "fit": i.fit, "suggest": suggestions.get(i.sheet.name)} for i in infos],
        "sheet": chosen.sheet.name, "header_line": header.line, "rows": len(rows.rows),
        "columns": [{"index": c.index, "letter": im.col_letter(c.index), "header": c.header, "group": c.group, "samples": c.samples, "filled": c.n,
                     "ignore": c.ignore, "dropped": c.index in dropped} for c in cols],
        "fields": [{"key": f.key, "label": f.label, "kind": f.kind, "required": f.required, "options": f.options is not None} for f in fields],
        "mapping": [{"field": m.field, "column": m.col, "confidence": round(m.conf, 2), "source": m.source, "why": m.why} for m in matches],
        "unmapped_required": [f.key for f in fields if f.required and f.key not in taken],
        "skipped": [{"line": ln, "reason": why} for ln, why in rows.skipped[:20]], "skipped_count": len(rows.skipped),
        "layout": ({"exact": True, "name": exact["name"], "uses": exact["uses"]} if exact else {"exact": False} if remembered else None),
        "resolutions": {k: v for k, v in resolutions.items() if k != DATE_ORDER_KEY},
        "dates": _date_report(date_infos, fmap), "locale": {"country": country, "timezone": str(tz), "date_order": im.default_order(country)},
        "key": _key_info(op), "ai": ai_info,
        "table": {"preset": list(op.import_table.preset)} if op.import_table else None,
        "heading": _heading(chosen.sheet.grid, header.row, file_name),
    })


def _heading(grid: list[list[Any]], header_row: int, file_name: str) -> str:
    """Tên của bảng đoán được: ô chữ đầu tiên phía TRÊN dòng tiêu đề (tên báo cáo — "KẾ HOẠCH TRIỂN KHAI …"), không có ⇒ tên file bỏ đuôi."""
    for row in grid[:header_row]:
        for v in row:
            if isinstance(v, str) and 3 <= len(v.strip()) <= 120 and not im.blank(v):
                return re.sub(r"\s+", " ", v).strip()
    base = re.sub(r"\.(xlsx|xlsm|csv)$", "", file_name.strip(), flags=re.I)
    return re.sub(r"[_]+", " ", base).strip()


def _key_info(op: registry.Op) -> dict[str, Any] | None:
    k = op.import_key
    if k is None:
        return None
    specs = {s["key"]: s["label"] for s in import_specs(op)}
    return {"fields": [specs.get(f, f) for f in k.fields], "update": k.update is not None}


# ── nhận ra dòng đã có ──

def _kv(v: Any) -> str:
    return "" if v is None else str(v).strip().lower()


def _same(old: Any, new: Any) -> bool:
    if _blank(old) and _blank(new):
        return True
    if (isinstance(old, int | float) and not isinstance(old, bool)) or (isinstance(new, int | float) and not isinstance(new, bool)):
        try:
            return float(old) == float(new)
        except (TypeError, ValueError):
            return False
    return _kv(old) == _kv(new)


def _blank(v: Any) -> bool:
    return v is None or v == "" or v == [] or v == {}


@dataclass
class _Classified:
    create: list[tuple[int, dict[str, Any]]] = field(default_factory=list)
    update: list[tuple[int, dict[str, Any], str, list[dict[str, Any]]]] = field(default_factory=list)
    skip: list[tuple[int, str, list[dict[str, Any]], str, str]] = field(default_factory=list)   # (dòng, lý do chung để gom, khác biệt, ai, ghi chú riêng của dòng)


def _who(vals: Mapping[str, Any], ik: Any, fields: Mapping[str, im.Field], country: str) -> str:
    """Dòng này là ai, cho người đọc: tên (trường ``name`` hoặc ô chữ đầu tiên không phải khoá) + khoá có giá trị (SĐT theo cách đọc của vùng) — không bao giờ in khoá rỗng."""
    name = vals.get("name") or next((v for k, v in vals.items() if k not in ik.fields and isinstance(v, str) and v.strip() and fields.get(k) is not None
                                     and fields[k].kind in ("text", "textarea")), "")
    parts = [str(name).strip()] if name else []
    for kf in ik.fields:
        v = vals.get(kf)
        if v in (None, "", [], {}):
            continue
        parts.append(phone_display(str(v), country) if fields.get(kf) is not None and fields[kf].kind == "phone" else str(v))
    return " · ".join(parts)


async def _classify(p: Principal, op: registry.Op, mapped: list[tuple[int, dict[str, Any]]], labels: Mapping[str, str], overwrite: bool,
                    fields: Mapping[str, im.Field], country: str) -> _Classified:
    """Dòng nào THÊM MỚI, CẬP NHẬT hay TRÙNG theo khoá tự nhiên của lệnh (``Op.import_key``). Mặc định an toàn: ô đang có dữ liệu không bị ghi đè trừ khi
    ``overwrite``; dòng trùng khoá với dòng trước trong CÙNG file bị bỏ (dòng đầu thắng)."""
    ik = op.import_key
    assert ik is not None
    out = _Classified()
    keyed: list[tuple[int, dict[str, Any], tuple[str, ...], dict[str, Any]]] = []
    seen: dict[tuple[str, ...], int] = {}
    for line, inp in mapped:
        try:
            m = op.input.model_validate(inp)
        except Exception:   # dòng sai dữ liệu: để lệnh báo lỗi như thường
            out.create.append((line, inp))
            continue
        vals = m.model_dump(mode="json")
        key = tuple(_kv(vals.get(f)) for f in ik.fields)
        if not key[0]:   # trường khoá đầu là định danh — thiếu thì không nhận ra được; các trường sau được phép trống
            out.create.append((line, inp))
            continue
        if key in seen:
            same = " + ".join(labels.get(f, f) for f, k in zip(ik.fields, key, strict=False) if k)
            out.skip.append((line, f"lặp lại dòng đứng trước trong file (trùng {same}) — giữ dòng đầu", [], _who(vals, ik, fields, country), f"giống dòng {seen[key]}"))
            continue
        seen[key] = line
        keyed.append((line, inp, key, vals))
    if not keyed:
        return out
    uop = registry.get(ik.update) if ik.update and ik.update != "self" else None
    async with db.tenant_tx(p, rollback=True) as conn:
        found = await ik.existing(executor.core_ctx(conn, p, module=op.module), sorted({k for _, _, k, _ in keyed}))
    existing = {tuple(_kv(x) for x in k): rec for k, rec in found.items()}
    for line, inp, key, vals in keyed:
        rec = existing.get(key)
        if rec is None:
            out.create.append((line, inp))
            continue
        who = _who(vals, ik, fields, country)
        same = " + ".join(labels.get(f, f) for f, k in zip(ik.fields, key, strict=False) if k)   # chỉ phần khoá CÓ giá trị — không in phần rỗng
        if ik.update is None:
            out.skip.append((line, f"đã có trong hệ thống (trùng {same})", [], who, "đã có"))
            continue
        if uop is not None and (uop.kind != "command" or not p.can(uop.resource, uop.action)):
            out.skip.append((line, "đã có trong hệ thống nhưng bạn không có quyền cập nhật", [], who, "đã có"))
            continue
        diff: list[dict[str, Any]] = []
        apply: dict[str, Any] = {}
        for f in inp:
            if f in ik.fields or f not in vals:
                continue
            old, new = rec.get(f), vals[f]
            if _same(old, new):
                continue
            if _blank(old):
                diff.append({"field": labels.get(f, f), "old": None, "new": new, "applied": True})
                apply[f] = new
            else:
                diff.append({"field": labels.get(f, f), "old": old, "new": new, "applied": overwrite})
                if overwrite:
                    apply[f] = new
        if not apply:
            held = [d["field"] for d in diff]
            if held:
                out.skip.append((line, "đã có trong hệ thống, có ô khác dữ liệu hiện tại — giữ nguyên (chưa bật “ghi đè”)", diff, who, f"khác: {', '.join(held[:4])}"))
            else:
                out.skip.append((line, f"đã có trong hệ thống, không có gì mới (trùng {same})", [], who, "đã có"))
            continue
        base = {k: v for k, v in rec.items() if k in op.input.model_fields} if ik.update == "self" else {}
        payload = base | {f: rec.get(f, vals[f]) for f in ik.fields} | apply   # khoá lấy đúng chữ của bản ghi đang có (hoa / thường)
        out.update.append((line, payload, op.name if ik.update == "self" else ik.update, diff))
    return out


# ── chạy theo ánh xạ ──

def _show(f: im.Field | None, v: Any) -> str:
    if isinstance(v, list):
        return ", ".join(_show(f, x) for x in v)
    if f is not None and f.options is not None and isinstance(v, str):
        return f.options.label_of(v)
    if f is not None and f.kind == "date":
        try:
            d = date.fromisoformat(v[:10]) if isinstance(v, str) else v.date() if isinstance(v, datetime) else v
            return f"{d.day} tháng {d.month} năm {d.year}"   # chữ, không phải dd/mm — người xem thấy ngay máy đã hiểu ngày nào
        except (ValueError, AttributeError):
            pass
    return im.cell_text(v)


_QUOTED = re.compile(r"[“\"'‘][^”\"'’]{0,80}[”\"'’]")


def _reason(err: str | None) -> str:
    """Lý do lỗi bỏ phần chữ riêng của từng dòng (giá trị trong dấu nháy) để gom: "SĐT không hợp lệ: '12'" và "…: '34'" cùng một lý do."""
    return _QUOTED.sub("…", (err or "lỗi không rõ").strip())[:200]


def _group(items: list[tuple[int, str]], detail: Mapping[int, dict[str, str]] | None = None) -> list[dict[str, Any]]:
    """Gom theo lý do. ``detail``: {dòng: {who, note}} ⇒ danh sách dòng bấm mở ra xem được (tối đa 100 dòng mỗi nhóm)."""
    by: dict[str, list[int]] = {}
    for line, reason in items:
        by.setdefault(reason, []).append(line)
    return [{"reason": r, "count": len(ls), "lines": ls[:6],
             "rows": [{"line": ln, **(detail or {}).get(ln, {})} for ln in ls[:100]]} for r, ls in sorted(by.items(), key=lambda kv: -len(kv[1]))]


async def _remember_layout(p: Principal, command: str, header: im.Header, drows: im.DataRows, mapping: dict[str, int], resolutions: dict[str, dict[str, str]],
                           date_infos: dict[str, im.DateInfo], file_name: str) -> None:
    cols = im.profile_columns(header, drows)
    by_col = {j: k for k, j in mapping.items()}
    await save_mapping(p, command, im.signature(header), file_name,
                       {(f"{c.group} {c.header}" if c.group else c.header): by_col.get(c.index) for c in cols if not c.header.startswith("Cột ")},
                       {k: m for k, m in resolutions.items() if m} | ({DATE_ORDER_KEY: {k: i.order for k, i in date_infos.items() if i.text_dates}}
                                                                      if any(i.text_dates for i in date_infos.values()) else {}))


async def _run_table(p: Principal, op: registry.Op, command: str, plan: dict[str, Any], mapped: list[tuple[int, dict[str, Any]]], mapped_all: list[tuple[int, dict[str, Any]]],
                     waiting: set[int], pending: list[dict[str, Any]], skipped: list[tuple[int, str]], fmap: dict[str, im.Field], labels: Mapping[str, str],
                     date_infos: dict[str, im.DateInfo], header: im.Header, drows: im.DataRows, mapping: dict[str, int], resolutions: dict[str, dict[str, str]], *,
                     dry_run: bool, file_name: str, file_sha256: str) -> Result:
    """Nhập CẢ BẢNG làm MỘT lần gọi lệnh (``Op.import_table``, 1.48): dòng hợp lệ (kiểm bằng mô hình dòng) đổ vào trường danh sách của đầu vào lệnh cùng các trường
    ``preset`` của màn; lệnh nhìn cả file (mã dòng, việc trước, ngày tính…) và trả ``import.rows`` (kết quả từng dòng) + ``import.view`` (bản xem trước dữ liệu kết quả).
    Lỗi cả file (vòng phụ thuộc, không có quyền…) ⇒ trả thẳng, không nhập gì. Chạy thử: điểm lưu rồi bỏ; nhập thật: ``executor.run`` — quyền / nhật ký thao tác như mọi lệnh."""
    tbl = op.import_table
    assert tbl is not None
    row_model = registry.table_row_model(op.input, tbl)
    assert row_model is not None
    preset = {k: v for k, v in (plan.get("preset") or {}).items() if k in tbl.preset and v not in (None, "")} if isinstance(plan.get("preset"), dict) else {}
    results: list[dict[str, Any]] = []
    good_rows: list[dict[str, Any]] = []
    for line, inp in mapped:
        try:
            good_rows.append(row_model.model_validate({**inp, "line": line}).model_dump(mode="json", exclude_defaults=False))
        except Exception as e:   # dòng sai kiểu (ngày không đọc được, chữ quá dài…): báo từng dòng, các dòng khác vẫn vào
            r = map_error(e, row_model)
            results.append({"line": line, "status": r.status, "error": r.error, "action": "create"})
    whole = {**preset, tbl.field: good_rows}
    out: Any = None
    status = "ok"
    token = executor.FROM_FILE.set(True)
    try:
        if not good_rows:
            report: dict[str, Any] = {"rows": [], "notes": [], "view": None}
        elif dry_run:
            try:
                async with db.tenant_tx(p, rollback=True) as conn:
                    ctx = executor.core_ctx(conn, p, module=op.module)
                    chain = await executor.approval_chain(conn, p, command)
                    status = "pending_approval" if chain and (op.sensitive is None or await op.sensitive(ctx, op.input.model_validate(whole))) else "ok"
                    async with conn.transaction():
                        out = await invoke(ctx, command, whole, events=[], approval_handled=True)
                        await check_deferred(conn)
                        raise psycopg.Rollback()
            except Exception as e:   # lỗi cả file ⇒ Result đã phân loại, người đọc thấy lý do ngay ở màn xem trước
                return map_error(e, op.input)
            report = (jsonable(out) or {}).get("import") or {"rows": [], "notes": [], "view": None}
        else:
            r = await executor.run(p, command, whole, keep_output=True)   # không khoá chống lặp theo nội dung: nhập lại phải chạy lại (lệnh tự nhận ra việc đã có)
            if not r.ok:
                return Result(r.status, error=r.error)
            status = r.status
            report = (jsonable(r.output) or {}).get("import") or {"rows": [], "notes": [], "view": None} if r.status == "ok" else {"rows": [], "notes": [], "view": None}
            if r.status == "pending_approval":   # chờ người duyệt: chưa có gì ghi, báo đúng trạng thái
                report = {"rows": [{"line": ln, "status": "pending_approval", "action": "create"} for ln, _ in mapped], "notes": ["Đã gửi duyệt — việc sẽ vào kế hoạch sau khi được duyệt"],
                          "view": None}
    finally:
        executor.FROM_FILE.reset(token)

    by_line = {r["line"]: r for r in report.get("rows") or []}
    for line, _ in mapped:
        if line in by_line and not any(x["line"] == line for x in results):
            r = by_line[line]
            results.append({"line": line, "status": r.get("status", "ok"), "error": r.get("error"), "action": r.get("action", "create"), "diff": r.get("diff") or [],
                            "note": r.get("note") or "", "who": r.get("who") or ""})
    for line in sorted(waiting):
        results.append({"line": line, "status": "waiting", "action": "create", "error": None})
    results.sort(key=lambda r: r["line"])
    good = {"ok", "pending_approval"}
    summary = {"create": sum(1 for r in results if r["action"] == "create" and r["status"] in good),
               "update": sum(1 for r in results if r["action"] == "update" and r["status"] in good),
               "skip": sum(1 for r in results if r["action"] == "skip"),
               "failed": sum(1 for r in results if r["status"] not in good and r["status"] != "waiting" and r["action"] != "skip"),
               "waiting": len(waiting), "ignored": len(skipped)}
    ran = [r for r in results if r["action"] != "skip" and r["status"] != "waiting"]
    data_by_line = dict(mapped_all)
    if not dry_run and ran and status != "pending_approval":
        await record_run(p, command, [(r["line"], data_by_line.get(r["line"], {})) for r in ran], ran, file_name=file_name, file_sha256=file_sha256)
        if plan.get("remember", True) and summary["create"] + summary["update"] > 0:
            await _remember_layout(p, command, header, drows, mapping, resolutions, date_infos, file_name)
    head = []
    for ln, vals in sorted(mapped_all, key=lambda t: t[0])[:SHOW_ROWS]:
        res = next((r for r in results if r["line"] == ln), None)
        head.append({"line": ln, "action": res["action"] if res else "create", "status": res["status"] if res else "ok", "error": res.get("error") if res else None,
                     "values": [{"label": labels.get(k, k), "text": _show(fmap.get(k), v)} for k, v in vals.items()]})
    reasons = _group([(r["line"], _reason(r.get("error"))) for r in results if r["status"] not in good and r["status"] != "waiting" and r["action"] != "skip"])
    skips = _group([(r["line"], r.get("error") or "không đổi") for r in results if r["action"] == "skip"], {r["line"]: {"who": r.get("who", ""), "note": r.get("note", "")}
                                                                                                          for r in results if r["action"] == "skip"})
    nothing = None
    if summary["create"] + summary["update"] == 0:
        nothing = ("Còn giá trị trong cột lựa chọn chưa chọn — chọn xong mới biết dòng nào nhập được." if waiting else
                   "Mọi dòng đều lỗi — sửa theo lý do bên dưới rồi chọn lại file." if summary["failed"] else
                   "Kế hoạch trong file không có gì mới so với hệ thống." if summary["skip"] else "Không có dòng dữ liệu nào để nhập.")
    return Result("ok", {
        "command": command, "title": op.title, "dry_run": dry_run, "total": len(ran) + summary["skip"] + len(waiting), "ok": summary["create"] + summary["update"],
        "failed": summary["failed"], "unknown_columns": [], "summary": summary, "rows": [r for r in results if r["status"] not in good or r["action"] != "create"][:KEEP_ROWS],
        "preview": head, "reasons": reasons, "skips": skips, "ignored": [{"line": ln, "reason": why} for ln, why in skipped[:20]], "unresolved": pending, "nothing": nothing,
        "overwrite": False, "dates": _date_report(date_infos, fmap), "key": None, "counts": dict(Counter(r["action"] for r in results)),
        "notes": report.get("notes") or [], "plan_view": report.get("view"), "pending_approval": status == "pending_approval",
    })


async def run_plan(p: Principal, command: str, sheets: list[im.Sheet], plan: dict[str, Any], *, dry_run: bool, file_name: str = "", file_sha256: str = "") -> Result:
    """Bước 2-3 của nhập tự khớp: chạy theo ánh xạ người đã xác nhận (``plan``: sheet, header_line, mapping {trường: chỉ số cột}, resolutions, overwrite, remember).
    ``dry_run``: chạy thật từng dòng trong điểm lưu rồi bỏ (như nhập thường) + báo THÊM / CẬP NHẬT / TRÙNG + lỗi theo từng lý do. Nhập thật: đúng đường lệnh
    (quyền, nhật ký thao tác, nhật ký nhập), không ghi bảng nào trực tiếp."""
    op, err = _import_op(p, command)
    if err is not None or op is None:
        return err or Result("not_found", error=command)
    if op.import_rows is not None:
        return Result("invalid", error="Lệnh này đọc file theo mẫu riêng của công ty — dùng nhập theo mẫu")
    sh = next((s for s in sheets if s.name == plan.get("sheet")), sheets[0])
    options = await _load_options(p, op, p.vocab)
    fields = _fields_of(op, p.vocab, options)
    fmap = {f.key: f for f in fields}
    try:
        header = im.header_at(sh.grid, int(plan["header_line"]) - 1) if plan.get("header_line") else im.detect_header(sh.grid, _known_headers(fields))
    except (ValueError, TypeError, IndexError):
        header = None
    if header is None or header.row >= len(sh.grid):
        return Result("invalid", error="Không tìm thấy dòng tiêu đề — chọn dòng tiêu đề của bảng")
    drows = im.data_rows(sh.grid, header)
    width = len(header.names)
    try:
        mapping = {k: int(v) for k, v in (plan.get("mapping") or {}).items() if k in fmap and v is not None}
    except (ValueError, TypeError):
        return Result("invalid", error="Ánh xạ cột không hợp lệ")
    if any(j < 0 or j >= width for j in mapping.values()) or len(set(mapping.values())) != len(mapping):
        return Result("invalid", error="Ánh xạ cột không hợp lệ (cột không có thật hoặc một cột khớp hai trường)")
    skip_lines = {int(x) for x in plan.get("skip_lines") or [] if str(x).isdigit()}
    raw: list[dict[str, Any]] = []
    skipped = list(drows.skipped)
    for line, cells in drows.rows:
        if line in skip_lines:
            continue
        row: dict[str, Any] = {"_line": line}
        for key, j in mapping.items():
            if j < len(cells) and not im.blank(cells[j]):
                row[key] = cells[j]
        if len(row) > 1:
            raw.append(row)
        else:
            skipped.append((line, "không có dữ liệu ở các cột đã khớp"))
    resolutions = {k: {str(a): str(b) for a, b in m.items()} for k, m in (plan.get("resolutions") or {}).items() if isinstance(m, dict) and k != DATE_ORDER_KEY}
    country, tz = await _locale(p)
    user_orders = {k: v for k, v in (plan.get("date_orders") or {}).items() if v in ("dmy", "mdy") and k in fmap}
    raw, date_infos = im.convert_dates(fmap, raw, country, tz, user_orders, _date_orders(plan.get("resolutions") or {}))
    raw, unresolved, waiting = im.resolve_options(fmap, raw, resolutions)
    pending = []
    for k in dict.fromkeys(u.field for u in unresolved):
        idx = fmap[k].options
        if idx is None:
            continue
        pending.append({"field": k, "label": fmap[k].label, "options": [{"value": v, "label": lb} for v, lb in idx.options[:300]],
                        "values": [{"raw": u.raw, "key": name_key(u.raw), "count": u.count, "suggest": u.suggest,
                                    "suggest_label": idx.label_of(u.suggest) if u.suggest else None} for u in unresolved if u.field == k]})
    if pending and not dry_run:
        return Result("invalid", error=f"Còn {sum(len(g['values']) for g in pending)} giá trị trong cột lựa chọn chưa chọn — chọn xong mới nhập")
    mapped_all, _ = map_rows(op, raw, p.vocab)
    mapped = [(ln, inp) for ln, inp in mapped_all if ln not in waiting]   # dòng còn giá trị chờ chọn: CHƯA chạy, không tính là lỗi
    labels = {f.key: f.label for f in fields}
    if op.import_table is not None:
        return await _run_table(p, op, command, plan, mapped, mapped_all, waiting, pending, skipped, fmap, labels, date_infos, header, drows, mapping, resolutions,
                                dry_run=dry_run, file_name=file_name, file_sha256=file_sha256)
    cls = _Classified(create=mapped)
    if op.import_key is not None:
        cls = await _classify(p, op, mapped, labels, bool(plan.get("overwrite")), fmap, country)

    results: list[dict[str, Any]] = []
    token = executor.FROM_FILE.set(True)
    try:
        with executor.historical(executor.HISTORICAL.get()):
            if cls.create:
                out = await _run_rows(p, op, command, cls.create, [], results, dry_run=dry_run)
                if out.status != "ok":
                    return out
            by_cmd: dict[str, list[tuple[int, dict[str, Any]]]] = {}
            for line, payload, cmd, _ in cls.update:
                by_cmd.setdefault(cmd, []).append((line, payload))
            for cmd, rows_ in by_cmd.items():
                uop = registry.get(cmd)
                if uop is None:
                    return Result("invalid", error=f"Lệnh cập nhật {cmd} không có")
                out = await _run_rows(p, uop, cmd, rows_, [], results, dry_run=dry_run)
                if out.status != "ok":
                    return out
    finally:
        executor.FROM_FILE.reset(token)

    action = {ln: "create" for ln, _ in cls.create} | {ln: "update" for ln, _, _, _ in cls.update}
    diffs = {ln: d for ln, _, _, d in cls.update} | {ln: d for ln, _, d, _, _ in cls.skip}
    for r in results:
        r["action"] = action.get(r["line"], "create")
        if r["line"] in diffs:
            r["diff"] = diffs[r["line"]]
        r.pop("output", None)
    detail_of = {line: {"who": who, "note": note} for line, _, _, who, note in cls.skip}
    for line, reason, d, who, note in cls.skip:
        results.append({"line": line, "status": "skipped", "action": "skip", "error": reason, "diff": d, "who": who, "note": note})
    for line in sorted(waiting):
        results.append({"line": line, "status": "waiting", "action": "create", "error": None})
    results.sort(key=lambda r: r["line"])
    good = {"ok", "pending_approval"}
    summary = {"create": sum(1 for r in results if r["action"] == "create" and r["status"] in good),
               "update": sum(1 for r in results if r["action"] == "update" and r["status"] in good),
               "skip": sum(1 for r in results if r["action"] == "skip"),
               "failed": sum(1 for r in results if r["status"] not in good and r["status"] != "waiting" and r["action"] != "skip"),
               "waiting": len(waiting), "ignored": len(skipped)}
    data_by_line = dict(mapped)
    ran = [r for r in results if r["action"] != "skip" and r["status"] != "waiting"]
    if not dry_run and ran:
        await record_run(p, command, [(r["line"], data_by_line.get(r["line"], {})) for r in ran], ran, file_name=file_name, file_sha256=file_sha256)
        if plan.get("remember", True) and summary["create"] + summary["update"] > 0:
            await _remember_layout(p, command, header, drows, mapping, resolutions, date_infos, file_name)
    head = []
    for ln, vals in sorted(mapped_all, key=lambda t: t[0])[:SHOW_ROWS]:
        res = next((r for r in results if r["line"] == ln), None)
        head.append({"line": ln, "action": res["action"] if res else "create", "status": res["status"] if res else "ok", "error": res.get("error") if res else None,
                     "values": [{"label": labels.get(k, k), "text": _show(fmap.get(k), v)} for k, v in vals.items()]})
    detail = [r for r in results if (r["status"] not in good and r["status"] != "waiting") or r["action"] != "create"][:KEEP_ROWS]
    reasons = _group([(r["line"], _reason(r.get("error"))) for r in results if r["status"] not in good and r["status"] != "waiting" and r["action"] != "skip"])
    skips = _group([(r["line"], r["error"]) for r in results if r["action"] == "skip"], detail_of)
    nothing = None
    if summary["create"] + summary["update"] == 0:
        nothing = ("Còn giá trị trong cột lựa chọn chưa chọn — chọn xong mới biết dòng nào nhập được." if waiting else
                   f"Cả {summary['skip']} dòng đã có trong hệ thống — không có gì mới để nhập." if summary["skip"] and not summary["failed"] else
                   "Mọi dòng đều lỗi — sửa theo lý do bên dưới rồi chọn lại file." if summary["failed"] else "Không có dòng dữ liệu nào để nhập.")
    return Result("ok", {
        "command": command, "title": op.title, "dry_run": dry_run, "total": len(ran) + len(cls.skip) + len(waiting), "ok": summary["create"] + summary["update"],
        "failed": summary["failed"], "unknown_columns": [], "summary": summary, "rows": detail, "preview": head, "reasons": reasons, "skips": skips,
        "ignored": [{"line": ln, "reason": why} for ln, why in skipped[:20]], "unresolved": pending, "nothing": nothing, "overwrite": bool(plan.get("overwrite")),
        "dates": _date_report(date_infos, fmap), "key": _key_info(op), "counts": dict(Counter(r["action"] for r in results)),
    })
