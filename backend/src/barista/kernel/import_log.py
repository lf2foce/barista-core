"""NHẬT KÝ NHẬP FILE (quyết định 0168 §2) — đọc lần nhập + dòng lỗi, và nạp nhật ký nhập của HỆ KHÁC (lô nhập Excel ở hệ cũ) vào cùng chỗ.

Lần nhập thật trong Barista tự ghi ở ``imports.record_run``. Lần nhập của hệ khác: MỘT dòng file = một dòng của một lô ở hệ đó (thông tin lô lặp lại
trên mỗi dòng; lô tạo ở dòng đầu, số đếm cộng dần theo dòng mới — nạp lại cùng file không nhân đôi). Dòng như đã gửi (``data``) có thể chứa dữ liệu
cá nhân: chỉ quản trị đọc được, màn chỉ hiện số dòng / kết quả / lỗi, không hiện nội dung; công ty đặt hạn giữ nội dung (mặc định 90 ngày, 0174 §4),
quá hạn thì vòng quét xoá nội dung dòng — số đếm / kết quả / lỗi giữ mãi."""

import json
import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import AwareDatetime

from barista.kernel import registry, ui
from barista.kernel.core_ops import m
from barista.kernel.errors import Denied, Invalid, NotFound
from barista.kernel.executor import Ctx
from barista.kernel.fields import Input, NoInput, field
from barista.kernel.forms import field_specs

ORIGIN = r"^[a-z][a-z0-9_]{1,39}$"
ROW_STATUS = {"ok": "Vào", "pending_approval": "Chờ duyệt", "proposed": "Đề xuất", "invalid": "Sai dữ liệu", "conflict": "Xung đột",
              "denied": "Không có quyền", "not_found": "Không thấy", "error": "Lỗi hệ thống", "failed": "Lỗi (hệ nguồn)"}


class RunsIn(Input):
    source: Literal["", "live", "import"] = field("Nguồn", "", options={"": "Tất cả", "live": "Nhập trong Barista", "import": "Nạp từ hệ khác"})
    limit: int = field("Số dòng", 200, ge=1, le=2000)


@m.query("import_runs", resource="core.audit", title="Nhật ký nhập file", category="core")
async def import_runs(ctx: Ctx, inp: RunsIn) -> dict[str, Any]:
    """Ai nhập file nào, lúc nào, bằng lệnh gì, bao nhiêu dòng vào / lỗi — gồm lô nhập của hệ cũ đã nạp."""
    rows = await ctx.all(
        """SELECT r.id, r.started_at, r.command, r.file_name, r.total, r.ok, r.failed, r.source, r.origin,
                  coalesce(u.name, nullif(r.by_ref, '')) AS who
           FROM core.import_runs r LEFT JOIN core.users u ON u.id = r.by_user
           WHERE (%(s)s = '' OR r.source = %(s)s) ORDER BY r.started_at DESC LIMIT %(n)s""", {"s": inp.source, "n": inp.limit})
    total = await ctx.one("SELECT count(*) AS runs, coalesce(sum(total), 0) AS lines, coalesce(sum(failed), 0) AS failed FROM core.import_runs"
                          " WHERE %(s)s = '' OR source = %(s)s", {"s": inp.source})
    return ui.table(rows, [
        ui.col("started_at", "Lúc", "datetime"), ui.col("who", "Người"), ui.col("command", "Lệnh / loại nhập"), ui.col("file_name", "File"),
        ui.col("total", "Dòng", "number"), ui.col("ok", "Vào", "number"), ui.col("failed", "Lỗi", "number"),
        ui.col("source", "Nguồn", "badge", labels={"live": "Barista", "import": "Hệ khác"}, tones={"live": "blue", "import": "gray"}),
    ], title="Nhật ký nhập file", limit=inp.limit, kpis=[ui.kpi("Lần nhập", total["runs"] if total else 0), ui.kpi("Dòng", total["lines"] if total else 0),
                                                          ui.kpi("Dòng lỗi", total["failed"] if total else 0)],
        empty="Chưa có lần nhập file nào")


class RunIn(Input):
    run_id: UUID = field("Lần nhập", hidden=True)


@m.query("import_run", resource="core.audit", title="Dòng lỗi của một lần nhập", category="core")
async def import_run(ctx: Ctx, inp: RunIn) -> dict[str, Any]:
    """Dòng KHÔNG vào của một lần nhập: số dòng Excel, kết quả, lỗi — không hiện nội dung dòng (có thể có dữ liệu cá nhân)."""
    rows = await ctx.all("""SELECT line AS id, line, status, error FROM core.import_run_rows WHERE run_id = %s AND status NOT IN ('ok', 'pending_approval')
                            ORDER BY line LIMIT 2000""", (inp.run_id,))
    return ui.table(rows, [ui.col("line", "Dòng", "number"), ui.col("status", "Kết quả", "badge", labels=ROW_STATUS), ui.col("error", "Lỗi", width=420)],
                    title="Dòng lỗi của lần nhập", empty="Mọi dòng đã vào")


class PastRowIn(Input):
    origin: str = field("Hệ nguồn", max_length=40, example="he_cu")
    run_ref: str = field("Mã lô ở hệ nguồn", max_length=120, example="415")
    run_kind: str = field("Loại nhập", "import", max_length=80, help="Hệ nguồn nhập gì (vd lead) — hiện ở cột Lệnh / loại nhập")
    file_name: str = field("Tên file", "", max_length=300)
    file_sha256: str = field("SHA-256 của file", "", max_length=64)
    run_by_email: str = field("Người nhập (email)", "", max_length=200)
    run_by_ref: str = field("Mã người nhập ở hệ nguồn", "", max_length=120)
    started_at: AwareDatetime = field("Lô bắt đầu lúc", kind="text", help="ISO 8601 CÓ múi giờ")
    finished_at: AwareDatetime | None = field("Lô xong lúc", None, kind="text")
    declared: str = field("Số hệ nguồn tự khai (JSON)", "", kind="textarea", max_length=2000, help='vd {"total": 9, "success": 8, "failed": 1}')
    line: int = field("Số dòng trong lô", ge=0)
    status: Literal["ok", "failed"] = field("Kết quả ở hệ nguồn", options={"ok": "Vào", "failed": "Lỗi"})
    error: str = field("Lỗi ở hệ nguồn", "", max_length=2000)
    data: str = field("Dòng như đã nhập (JSON)", "", kind="textarea", max_length=20000, audit=False,
                      help="Nội dung dòng ở hệ nguồn — có thể có dữ liệu cá nhân; không chép vào nhật ký thao tác")
    ref: str = field("Mã bản ghi dòng đã sinh ra ở hệ nguồn", "", max_length=120)
    ext_ref: str = field("Mã dòng ở hệ nguồn", "", max_length=120)


def _json_object(v: str, what: str) -> dict[str, Any]:
    if not v.strip():
        return {}
    try:
        got = json.loads(v)
    except ValueError as e:
        raise Invalid(f"{what}: không phải JSON") from e
    if not isinstance(got, dict):
        raise Invalid(f"{what}: phải là một đối tượng JSON")
    return got


@m.command("import_past_run", resource="core.admin", action="manage", title="Nạp nhật ký nhập file của hệ khác", category="setup",
           importable=True, agent="deny", min_scope="all")
async def import_past_run(ctx: Ctx, inp: PastRowIn) -> dict[str, Any]:
    """Một dòng của một lô nhập ở hệ khác ⇒ ``core.import_runs`` (``source = 'import'``) + ``core.import_run_rows``. Lô tạo ở dòng đầu; số đếm
    cộng theo dòng MỚI (nạp lại cùng dòng không đếm lại)."""
    if not ctx.p.has_scope("core.admin", "all"):
        raise Denied("Nạp nhật ký nhập của hệ khác cần quyền quản trị cả công ty")
    if not re.fullmatch(ORIGIN, inp.origin):
        raise Invalid("Hệ nguồn: chữ thường không dấu, số, _ (2–40 ký tự), bắt đầu bằng chữ")
    sha = inp.file_sha256.lower()
    if sha and not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise Invalid("SHA-256 phải là 64 ký tự hex")
    now = datetime.now(inp.started_at.tzinfo)
    if inp.started_at > now or (inp.finished_at is not None and inp.finished_at > now):
        raise Invalid("Lô ở tương lai — chỉ nạp lô đã chạy")
    declared = _json_object(inp.declared, "Số hệ nguồn tự khai")
    row = _json_object(inp.data, "Dòng như đã nhập")
    who = await ctx.val("SELECT u.id FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id AND mb.tenant_id = core.tenant() WHERE u.email = %s",
                        (inp.run_by_email.strip().lower(),)) if inp.run_by_email else None
    run_id = await ctx.val(
        """INSERT INTO core.import_runs (command, file_name, file_sha256, by_user, by_ref, started_at, finished_at, source, origin, ext_ref, data)
           VALUES (%s, %s, %s, %s, %s, %s, %s, 'import', %s, %s, %s)
           ON CONFLICT (tenant_id, origin, ext_ref) WHERE source = 'import' DO UPDATE SET data = core.import_runs.data RETURNING id""",
        (inp.run_kind, inp.file_name, sha, who, "" if who else inp.run_by_ref, inp.started_at, inp.finished_at, inp.origin, inp.run_ref,
         Jsonb({"declared": declared} if declared else {})))
    new = await ctx.val(
        """INSERT INTO core.import_run_rows (run_id, line, status, error, data, ref, ext_ref) VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (run_id, line) DO NOTHING RETURNING line""",
        (run_id, inp.line, inp.status, inp.error, Jsonb(row), inp.ref, inp.ext_ref))
    if new is not None:
        await ctx.exec("UPDATE core.import_runs SET total = total + 1, ok = ok + %s, failed = failed + %s WHERE id = %s",
                       (int(inp.status == "ok"), int(inp.status == "failed"), run_id))
    return {"run_id": run_id, "inserted": new is not None, "matched_user": who is not None}


# ── Giữ nội dung dòng bao lâu (0174 §4): DỮ LIỆU của công ty, mặc định 90 ngày; quá hạn ⇒ vòng quét xoá NỘI DUNG dòng, giữ mãi số đếm / kết quả / lỗi ──

PURGE_RUNS = 50   # mỗi lượt quét xoá nội dung dòng của tối đa chừng ấy lần nhập (lượt sau làm tiếp) — không khoá bảng lâu


@m.query("import_retention", resource="core.admin", title="Giữ nội dung dòng nhập file", category="core")
async def import_retention(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Một dòng: số ngày giữ nội dung dòng của nhật ký nhập + bao nhiêu lần nhập / dòng còn nội dung, bao nhiêu sẽ xoá ở lượt quét tới."""
    keep = await ctx.val("SELECT import_rows_keep_days FROM core.tenants")
    got = await ctx.one(
        """SELECT count(*) FILTER (WHERE r.rows_purged_at IS NULL) AS kept_runs, count(*) FILTER (WHERE r.rows_purged_at IS NOT NULL) AS purged_runs,
                  count(*) FILTER (WHERE r.rows_purged_at IS NULL AND %(k)s::int IS NOT NULL
                                   AND r.started_at < now() - make_interval(days => %(k)s::int)) AS due_runs
           FROM core.import_runs r""", {"k": keep})
    g = got or {}
    return ui.table([{"id": "retention", "keep": f"{keep} ngày" if keep else "Giữ mãi", "kept_runs": g.get("kept_runs", 0),
                      "purged_runs": g.get("purged_runs", 0), "due_runs": g.get("due_runs", 0)}],
                    [ui.col("keep", "Giữ nội dung dòng"), ui.col("kept_runs", "Lần nhập còn nội dung", "number"),
                     ui.col("due_runs", "Sẽ xoá ở lượt quét tới", "number"), ui.col("purged_runs", "Đã xoá nội dung", "number")],
                    title="Giữ nội dung dòng nhập file",
                    note="Nội dung từng dòng (có thể có dữ liệu cá nhân) chỉ giữ chừng ấy ngày kể từ lúc nhập. Lần nhập (file, người, lúc, tổng / vào / lỗi) "
                         "và số dòng / kết quả / lỗi của từng dòng giữ mãi. Nhật ký thao tác không bị xoá.")


class SetRetention(Input):
    keep_days: int = field("Giữ nội dung dòng (ngày)", 90, ge=1, le=3650)
    keep_forever: bool = field("Giữ mãi", False, help="Bật: không xoá nội dung dòng (bỏ qua số ngày)")


@m.command("set_import_retention", resource="core.admin", action="manage", title="Đặt hạn giữ nội dung dòng nhập file", agent="deny", category="setup")
async def set_import_retention(ctx: Ctx, inp: SetRetention) -> dict[str, Any]:
    """Đổi hạn giữ của CHÍNH công ty (RLS: một dòng core.tenants của phiên). Rút ngắn ⇒ lượt quét kế tiếp xoá nội dung dòng của lần nhập đã quá hạn
    mới — không lấy lại được. Trợ lý không đổi thay (xoá dữ liệu là quyết định của quản trị)."""
    keep = None if inp.keep_forever else inp.keep_days
    old = await ctx.val("SELECT import_rows_keep_days FROM core.tenants")
    await ctx.exec("UPDATE core.tenants SET import_rows_keep_days = %s WHERE id = core.tenant()", (keep,))
    return {"keep_days": keep, "previous": old, "summary": f"Giữ nội dung dòng nhập file: {f'{keep} ngày' if keep else 'mãi'}"}


@m.command("purge_import_rows", resource="core.admin", title="Xoá nội dung dòng nhập file quá hạn giữ", internal=True,
           sweep="""SELECT DISTINCT r.tenant_id FROM core.import_runs r JOIN core.tenants t ON t.id = r.tenant_id
                    WHERE r.rows_purged_at IS NULL AND t.import_rows_keep_days IS NOT NULL
                      AND r.started_at < now() - make_interval(days => t.import_rows_keep_days)""")
async def purge_import_rows(ctx: Ctx, inp: NoInput) -> dict[str, int]:
    """Vòng quét: lần nhập bắt đầu trước hạn giữ của công ty ⇒ nội dung dòng thành rỗng, đánh dấu ``rows_purged_at``. Số dòng / kết quả / lỗi / khoá chống
    lặp / mã nguồn giữ nguyên; nhật ký thao tác không đụng."""
    runs = [r["id"] for r in await ctx.all(
        """SELECT r.id FROM core.import_runs r JOIN core.tenants t ON t.id = r.tenant_id
           WHERE r.rows_purged_at IS NULL AND t.import_rows_keep_days IS NOT NULL AND r.started_at < now() - make_interval(days => t.import_rows_keep_days)
           ORDER BY r.started_at LIMIT %s FOR UPDATE OF r SKIP LOCKED""", (PURGE_RUNS,))]
    if not runs:
        return {"runs": 0, "rows": 0}
    rows = await ctx.val("""WITH x AS (UPDATE core.import_run_rows SET data = '{}'::jsonb WHERE run_id = ANY(%s) AND data <> '{}'::jsonb RETURNING 1)
                            SELECT count(*) FROM x""", (runs,))
    await ctx.exec("UPDATE core.import_runs SET rows_purged_at = now() WHERE id = ANY(%s)", (runs,))
    return {"runs": len(runs), "rows": int(rows or 0)}


# ── Cách khớp cột nhập Excel đã nhớ (0226) ──

class MappingId(Input):
    id: UUID = field("Cách khớp", hidden=True)


@m.query("import_mappings", resource="core.admin", title="Cách khớp cột nhập Excel", category="core")
async def import_mappings(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Bố cục file đã xác nhận ở màn nhập tự khớp: lệnh, file đầu tiên dùng bố cục, cột → trường, số lần dùng. Lần sau file CÙNG bố cục nhập không phải khớp lại."""
    got = await ctx.all("""SELECT m.id, m.command, m.name, m.columns, m.values, m.uses, m.last_used_at, u.name AS who
                           FROM core.import_mappings m LEFT JOIN core.users u ON u.id = m.created_by ORDER BY m.last_used_at DESC LIMIT 500""")
    rows = []
    for r in got:
        op = registry.get(r["command"])
        labels = {s["key"]: s["label"] for s in field_specs(op, ctx.p.vocab)} if op is not None else {}
        pairs = [f"{h} → {labels.get(k, k)}" for h, k in (r["columns"] or {}).items() if k]
        dropped = sum(1 for k in (r["columns"] or {}).values() if not k)
        picks = sum(len(v) for v in (r["values"] or {}).values())
        rows.append({"id": r["id"], "command": op.title if op is not None else r["command"], "name": r["name"], "uses": r["uses"], "last_used_at": r["last_used_at"],
                     "who": r["who"] or "", "columns": "; ".join(pairs) + (f" (bỏ qua {dropped} cột)" if dropped else ""),
                     "picks": f"{picks} lựa chọn đã chọn tay" if picks else ""})
    return ui.table(rows, [ui.col("command", "Nhập vào"), ui.col("name", "File đầu tiên", width=200), ui.col("columns", "Cột → trường", width=460),
                           ui.col("picks", "Lựa chọn nhớ"), ui.col("uses", "Số lần", "number"), ui.col("last_used_at", "Dùng gần nhất", "datetime"), ui.col("who", "Người xác nhận")],
                    title="Cách khớp cột nhập Excel", empty="Chưa nhớ bố cục file nào — xác nhận một lần ở màn nhập Excel là hệ thống nhớ.",
                    note="Lần sau file có CÙNG tiêu đề cột nhập không phải khớp lại. Muốn đổi cách khớp: xoá dòng này rồi nhập lại file, hoặc sửa ngay ở màn khớp cột "
                         "(bố cục mới ghi đè). Hệ thống chỉ nhớ tiêu đề cột và chữ ô lựa chọn, không nhớ dữ liệu dòng.")


@m.command("delete_import_mapping", resource="core.admin", action="manage", title="Quên cách khớp cột đã nhớ", agent="deny", category="setup",
           reversible="Lần nhập sau bấm khớp cột lại một lần là máy nhớ lại")
async def delete_import_mapping(ctx: Ctx, inp: MappingId) -> dict[str, Any]:
    """Xoá MỘT bố cục đã nhớ — file cùng bố cục lần sau được khớp lại từ đầu (tên cột + nội dung + model). Không đụng dữ liệu đã nhập."""
    if not await ctx.exec("DELETE FROM core.import_mappings WHERE id = %s", (inp.id,)):
        raise NotFound("Không có cách khớp này")
    return {"deleted": str(inp.id)}


class MappingEdit(Input):
    id: UUID = field("Cách khớp", hidden=True)
    name: str = field("Tên gọi", "", max_length=300, help="Nhãn cho người đọc (mặc định là tên file đầu tiên dùng bố cục này)")
    forget_values: bool = field("Quên các lựa chọn đã chọn tay", False, help="Bật: lần sau chữ trong ô lựa chọn (vd “Facebook” → Quảng cáo) hỏi lại")


@m.command("edit_import_mapping", resource="core.admin", action="manage", title="Sửa cách khớp cột đã nhớ", agent="deny", category="setup")
async def edit_import_mapping(ctx: Ctx, inp: MappingEdit) -> dict[str, Any]:
    if not await ctx.exec("UPDATE core.import_mappings SET name = %s, values = CASE WHEN %s THEN '{}'::jsonb ELSE values END WHERE id = %s",
                          (inp.name, inp.forget_values, inp.id)):
        raise NotFound("Không có cách khớp này")
    return {"id": str(inp.id)}
