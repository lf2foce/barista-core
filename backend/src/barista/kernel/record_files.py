"""TỆP CỦA HỒ SƠ (ADR 0249 lát 1, api 1.72) — hợp đồng của giao dịch, CCCD của khách, chứng từ của một bộ hồ sơ gắn vào CHÍNH đối tượng đó.

* KHAI: module sở hữu đối tượng khai ``m.record_files(loại, labels=…)`` (``registry.RECORD_FILES``) — lõi không biết module nào.
* GIỮ: ``core.record_files`` (migration base/105) — một dòng = một CHỖ DÙNG của một tệp trong sổ ``core.files`` (thân tệp ở kho đối tượng — 0122). Ai thấy
  đối tượng mới thấy tệp (RLS qua ``core.subject_visible`` ⇒ hàm của module sở hữu); đổi người phụ trách là người cũ mất tệp ngay — không chép quyền.
* MỘT CỬA GẮN: ``core.attach_file`` — tải lên (thân tệp đi kèm lệnh, route ``POST /v1/record_files``) HOẶC lấy theo tham chiếu một tệp module khác đang giữ
  (tin đính kèm của trao đổi…: module đó ``files.grant`` rồi gọi lệnh này với ``grant`` — cùng tệp, không chép, ``files.adopt``). Gỡ: ``core.remove_file``
  (thêm dòng gỡ, không xoá; dòng đã gỡ không còn hiện với ai và không còn mở được tệp qua hồ sơ). Mở: ai thấy hồ sơ (kernel/files.py kiểm lại chỗ dùng).
* HIỆN: ``block`` (khối "Tệp" cho thẻ của module — ``barista.api.record_files.block``, đặt cạnh khối trao đổi / lịch sử) và truy vấn ``core.files_of``
  (trợ lý hỏi "hồ sơ này có những tệp gì?" bằng quyền người hỏi; trợ lý KHÔNG đọc nội dung tệp — còn mở, ADR 0249).
"""

import base64
import hashlib
from typing import Any
from uuid import UUID

from barista.kernel import files, packs, registry, ui
from barista.kernel.clock import shown, zone
from barista.kernel.core_ops import m
from barista.kernel.errors import Denied, Invalid, NotFound
from barista.kernel.executor import Ctx
from barista.kernel.fields import Input, field

TITLE = "Tệp"
BLOCK_ROWS = 8
FILE_MAX = 20 * 1024 * 1024
PATH = "/v1/record_files"


async def _subject(ctx: Ctx, kind: str, sid: Any) -> dict[str, Any] | None:
    """Đối tượng như người gọi thấy — None nếu loại không nhận tệp / không thấy / gói của đối tượng đang tắt."""
    if kind not in registry.RECORD_FILES or kind not in registry.SUBJECTS:
        return None
    try:
        return await ctx.call_as(registry.SUBJECTS[kind][1], ctx.p, subject_id=sid)
    except (NotFound, Denied, packs.PackOff):
        return None


def _when(at: Any) -> str:
    t = shown(at).astimezone(zone())
    return f"{t.day} tháng {t.month}, {t.year} {t:%H:%M}"


def _open(rid: Any) -> dict[str, Any]:
    return {"query": "core.file_card", "input": {"record_file_id": str(rid)}}


async def _rows(ctx: Ctx, kind: str, sid: Any, limit: int) -> list[dict[str, Any]]:
    return await ctx.all("""SELECT r.id, r.label, r.added_at, r.added_by, r.via, f.filename, f.content_type, f.size_bytes, u.name AS who
                            FROM core.record_files r JOIN core.files f ON f.id = r.file_id LEFT JOIN core.users u ON u.id = r.added_by
                            WHERE r.subject_kind = %s AND r.subject_id = %s ORDER BY r.added_at DESC, r.id LIMIT %s""", (kind, sid, limit))


def _item(r: dict[str, Any]) -> dict[str, Any]:
    bits = (files.kind_label(r["content_type"]), files.size_text(r["size_bytes"]), r["who"] or "", _when(r["added_at"]))
    return {"title": r["filename"], "subtitle": " · ".join(x for x in bits if x), "tone": "accent", "badge": r["label"], "open": _open(r["id"])}


async def block(ctx: Ctx, kind: str, subject_id: Any, *, limit: int = BLOCK_ROWS) -> dict[str, Any] | None:
    """Khối "Tệp" cho thẻ của module (đặt cạnh khối trao đổi / lịch sử) — bằng quyền người xem. Loại không khai nhận tệp / người xem không thấy hồ sơ ⇒
    None. Khối là danh sách thường (app nào cũng vẽ được); trường THÊM ``attach`` cho app biết gắn tệp vào đâu (thả tệp lên khối, nút "Thêm tệp")."""
    subj = await _subject(ctx, kind, subject_id)
    if subj is None:
        return None
    rows = await _rows(ctx, kind, subject_id, limit + 1)
    items = [_item(r) for r in rows[:limit]]
    if len(rows) > limit:
        items.append({"title": "Xem tất cả tệp của hồ sơ", "tone": "accent",
                      "open": {"query": "core.files_of", "input": {"subject_kind": kind, "subject_id": str(subject_id)}}})
    if not items:
        items = [{"title": "Chưa có tệp nào trên hồ sơ này", "subtitle": "Hợp đồng, giấy tờ, chứng từ của hồ sơ — ai thấy hồ sơ thì thấy tệp", "tone": "muted"}]
    out = ui.b_list(items, TITLE)
    out["attach"] = {"subject_kind": kind, "subject_id": str(subject_id), "subject_title": str(subj.get("title") or ""), "path": PATH,
                     "file_max": FILE_MAX, "file_kinds": files.ACCEPTED, "labels": list(registry.RECORD_FILES[kind].labels)}
    return out


# ── Gắn / gỡ ──

class AttachIn(Input):
    subject_kind: str = field("Loại hồ sơ", hidden=True, max_length=60)
    subject_id: UUID = field("Hồ sơ", hidden=True)
    label: str = field("Loại tệp", "", max_length=80, options_query="core.file_label_options", options_args={"subject_kind": "subject_kind"},
                       help="Tuỳ chọn — để trống nếu chưa rõ")
    # TẢI LÊN: thân tệp đi KÈM lệnh (``ctx.files["file"]``, route ``POST /v1/record_files``) — đầu vào chỉ mang tên + cỡ + sha256 (nhật ký / chống lặp không giữ nội dung)
    filename: str = field("Tên tệp", "", max_length=300, hidden=True)
    size: int = field("Cỡ (byte)", 0, ge=0, le=FILE_MAX, hidden=True)
    sha256: str = field("Mã băm SHA-256", "", max_length=64, hidden=True)
    # THEO THAM CHIẾU: module đang giữ tệp vừa cấp quyền lấy (``files.grant`` tới "core") trong cùng giao dịch — cùng tệp, không chép
    file_id: UUID | None = field("Tệp đã có", None, hidden=True, agent=False)
    grant: UUID | None = field("Quyền lấy tệp", None, hidden=True, agent=False)


async def _already(ctx: Ctx, inp: AttachIn, *, sha: str = "", file_id: UUID | None = None) -> UUID | None:
    """Cùng tệp (``file_id``) / cùng nội dung (``sha``) đang có trên hồ sơ (dòng chưa gỡ). Khoá theo hồ sơ trong giao dịch ⇒ hai lần thả cùng lúc không
    thành hai dòng."""
    await ctx.exec("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"record_files:{ctx.p.tenant_id}:{inp.subject_kind}:{inp.subject_id}",))
    return await ctx.val("""SELECT r.id FROM core.record_files r JOIN core.files f ON f.id = r.file_id
                            WHERE r.subject_kind = %s AND r.subject_id = %s AND (r.file_id = %s OR f.sha256 = %s) LIMIT 1""",
                         (inp.subject_kind, inp.subject_id, file_id, sha))


@m.command("attach_file", resource=None, title="Gắn tệp vào hồ sơ", agent="deny", category="core")   # tệp do NGƯỜI thả / chọn — trợ lý không gắn thay
async def attach_file(ctx: Ctx, inp: AttachIn) -> dict[str, Any]:
    """MỘT cửa gắn tệp vào một hồ sơ / khách / việc: tải lên, hoặc lấy theo tham chiếu tệp module khác đang giữ (tin đính kèm…) — không chép thân tệp.
    Ai thấy hồ sơ thì gắn được; cùng nội dung đã có trên hồ sơ ⇒ không thêm bản thứ hai."""
    subj = await _subject(ctx, inp.subject_kind, inp.subject_id)
    if subj is None:
        if inp.subject_kind in registry.SUBJECTS and inp.subject_kind not in registry.RECORD_FILES:
            raise Invalid(f"{registry.SUBJECTS[inp.subject_kind][0]} chưa nhận tệp gắn vào")
        raise NotFound("Không thấy hồ sơ này (hoặc bạn không có quyền xem)")
    label = inp.label.strip()
    if inp.grant is not None or inp.file_id is not None:
        if inp.grant is None or inp.file_id is None:
            raise Invalid("Lấy tệp theo tham chiếu cần cả tệp lẫn quyền lấy tệp")
        src = await ctx.one("SELECT filename, sha256 FROM core.files WHERE id = %s", (inp.file_id,))
        if src is None:
            raise NotFound("Không thấy tệp")
        name = src["filename"]
        if (same := await _already(ctx, inp, file_id=inp.file_id, sha=src["sha256"])) is not None:   # đã có trên hồ sơ — một lần là đủ
            return {"record_file_id": same, "summary": f"{name} đã có trên “{subj['title']}”"}
        stored = await files.adopt(ctx, inp.file_id, inp.grant)
        via = "reference"
    else:
        data = ctx.files.get("file")
        if not data:
            raise Invalid("Thiếu nội dung tệp")
        if len(data) > FILE_MAX:
            raise Invalid(f"Tệp quá {FILE_MAX // (1024 * 1024)} MB")
        sha = hashlib.sha256(data).hexdigest()
        if len(data) != inp.size or sha != inp.sha256.lower():
            raise Invalid("Nội dung tệp không khớp cỡ / mã băm đã khai")
        ctype = files.sniff(inp.filename, data)
        name = files.clean_name(inp.filename, ctype)
        same = await _already(ctx, inp, sha=sha)
        if same is not None:
            return {"record_file_id": same, "summary": f"{name} đã có trên “{subj['title']}”"}
        stored = await files.put(ctx, data, name, ctype, max_bytes=FILE_MAX)   # kho trước, dòng sau — cùng giao dịch (0122 §3)
        via = "upload"
    rid = await ctx.val("""INSERT INTO core.record_files (subject_kind, subject_id, file_id, label, added_by, via)
                           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""", (inp.subject_kind, inp.subject_id, stored.id, label, ctx.p.user_id, via))
    return {"record_file_id": rid, "file_id": stored.id, "summary": f"Đã gắn {name} vào “{subj['title']}”"}


class RecordFileRef(Input):
    record_file_id: UUID = field("Tệp của hồ sơ", hidden=True)


async def _one(ctx: Ctx, rid: UUID) -> tuple[dict[str, Any], dict[str, Any]]:
    r = await ctx.one("""SELECT r.*, f.filename, f.content_type, f.size_bytes, u.name AS who FROM core.record_files r JOIN core.files f ON f.id = r.file_id
                         LEFT JOIN core.users u ON u.id = r.added_by WHERE r.id = %s""", (rid,))
    subj = None if r is None else await _subject(ctx, r["subject_kind"], r["subject_id"])
    if r is None or subj is None:
        raise NotFound("Không thấy tệp (hoặc bạn không còn xem được hồ sơ này)")
    return r, subj


def _may_remove(ctx: Ctx, r: dict[str, Any], subj: dict[str, Any]) -> bool:
    """Người đã gắn tệp, hoặc người đang phụ trách hồ sơ."""
    return ctx.p.user_id in (r["added_by"], subj.get("owner_id"))


@m.command("remove_file", resource=None, title="Gỡ tệp khỏi hồ sơ", agent="deny", category="core")
async def remove_file(ctx: Ctx, inp: RecordFileRef) -> dict[str, Any]:
    """Gỡ = đánh dấu (không xoá): tệp biến khỏi hồ sơ với mọi người; nhật ký thao tác giữ ai gỡ, lúc nào. Chỉ người đã gắn tệp hoặc người phụ trách hồ sơ."""
    r, subj = await _one(ctx, inp.record_file_id)
    if not _may_remove(ctx, r, subj):
        raise Denied("Chỉ người đã gắn tệp hoặc người phụ trách hồ sơ gỡ được tệp")
    await ctx.exec("INSERT INTO core.record_file_removals (record_file_id, removed_by) VALUES (%s, %s) ON CONFLICT DO NOTHING", (r["id"], ctx.p.user_id))
    return {"record_file_id": r["id"], "summary": f"Đã gỡ {r['filename']} khỏi “{subj['title']}”"}


# ── Xem ──

class SubjectFilesIn(Input):
    subject_kind: str = field("Loại hồ sơ", hidden=True, max_length=60)
    subject_id: UUID = field("Hồ sơ", hidden=True)


@m.query("files_of", resource=None, title="Tệp của hồ sơ", category="core")
async def files_of(ctx: Ctx, inp: SubjectFilesIn) -> dict[str, Any]:
    """Các tệp gắn vào một hồ sơ / khách / việc (tên, loại tệp, cỡ, ai gắn, lúc nào) — mới nhất trước; chỉ khi người hỏi THẤY hồ sơ. Không đọc nội dung tệp."""
    subj = await _subject(ctx, inp.subject_kind, inp.subject_id)
    if subj is None:
        raise NotFound("Không thấy hồ sơ này (hoặc bạn không có quyền xem)")
    rows = await _rows(ctx, inp.subject_kind, inp.subject_id, 500)
    blocks = [ui.b_list([_item(r) for r in rows], f"{len(rows)} tệp") if rows
              else ui.b_callout("muted", "Chưa có tệp nào", "Thả tệp lên khối “Tệp” của hồ sơ để gắn vào.")]
    label = registry.SUBJECTS[inp.subject_kind][0]
    return ui.card(f"{TITLE} · {subj['title']}", blocks, subtitle=label, icon="paperclip",
                   open=[{"title": f"Mở {label.lower()}", **subj["open"]}] if subj.get("open") else [])


@m.query("file_card", resource=None, title="Một tệp của hồ sơ", category="core")
async def file_card(ctx: Ctx, inp: RecordFileRef) -> dict[str, Any]:
    r, subj = await _one(ctx, inp.record_file_id)
    label = registry.SUBJECTS[r["subject_kind"]][0]
    page = ui.detail(r["filename"], [ui.section("Thông tin", [
        ui.field(label, subj["title"]), ui.field("Loại tệp", r["label"] or "chưa đặt"), ui.field("Định dạng", files.kind_label(r["content_type"])),
        ui.field("Cỡ", files.size_text(r["size_bytes"])), ui.field("Người gắn", r["who"] or "Người đã rời công ty"),
        ui.field("Gắn lúc", _when(r["added_at"])), ui.field("Nguồn", "Lấy từ trao đổi / chỗ khác, không chép" if r["via"] == "reference" else "Tải lên"),
    ])], subtitle=f"Tệp của {label.lower()}")
    path = f"{PATH}/{r['id']}"
    if r["content_type"] == "application/pdf":
        page["original"] = {"title": "Xem", "path": path, "filename": r["filename"], "page": None, "kind": "pdf"}
    page["links"] = [{"title": "Tải về", "path": path, "filename": r["filename"]}]
    page["actions"] = [ui.act("core.remove_file", "Gỡ khỏi hồ sơ", {"record_file_id": str(r["id"])}, "destructive")] if _may_remove(ctx, r, subj) else []
    return page


@m.query("file_body", resource=None, title="Tải tệp của hồ sơ", agent="deny")
async def file_body(ctx: Ctx, inp: RecordFileRef) -> dict[str, Any]:
    """Thân tệp cho route tải — mở qua chỗ dùng ở hồ sơ (người xem thấy hồ sơ; kernel/files.py kiểm lại)."""
    r, _ = await _one(ctx, inp.record_file_id)
    data = await files.read(ctx, r["file_id"])
    return {"filename": r["filename"], "content_type": r["content_type"], "inline": r["content_type"] in (*files.MEDIA, "application/pdf"),
            "data_b64": base64.b64encode(data).decode()}


class LabelOptionsIn(Input):
    subject_kind: str = field("Loại hồ sơ", "", hidden=True, max_length=60)


@m.query("file_label_options", resource=None, title="Loại tệp gợi ý cho hồ sơ", agent="deny")
async def file_label_options(ctx: Ctx, inp: LabelOptionsIn) -> list[dict[str, str]]:
    decl = registry.RECORD_FILES.get(inp.subject_kind)
    return ui.options([{"value": x, "label": x} for x in (decl.labels if decl else ())])


# ── HTTP: thân tệp lên / xuống (không đi được qua /v1/commands) ──

def _routes() -> None:
    from barista.kernel import web

    @m.route("POST", PATH)
    async def http_attach(request: web.Request) -> web.JSONResponse:
        """Gắn MỘT tệp vào hồ sơ — ``multipart/form-data``: ``subject_kind``, ``subject_id``, ``label``, ``file``. Đọc theo khúc, chặn cỡ ngay khi vượt;
        thân tệp đi KÈM lệnh ``core.attach_file`` (một cửa: quyền, chống lặp, nhật ký), đầu vào chỉ có tên + cỡ + sha256."""
        p = await web.caller(request)
        up = await web.multipart(request, FILE_MAX)
        if up.filename is None or not up.data:
            raise web.HTTPException(422, "Thiếu tệp")
        inp: dict[str, Any] = {k: v for k, v in up.fields.items() if k in ("subject_kind", "subject_id", "label") and v != ""}
        inp |= {"filename": up.filename, "size": len(up.data), "sha256": up.sha.hexdigest()}
        return web.result(await web.run(p, "core.attach_file", inp, key=request.headers.get("idempotency-key"), files={"file": up.data}))

    @m.route("GET", PATH + "/{record_file_id}")
    async def http_file(record_file_id: UUID, request: web.Request) -> web.Response:
        """Tệp của hồ sơ — quyền = thấy hồ sơ, kiểm MỖI lần tải; không cho trình duyệt / proxy giữ bản sao (mất quyền là hết tải được)."""
        p = await web.caller(request)
        r = await web.query(p, "core.file_body", {"record_file_id": str(record_file_id)})
        if not r.ok:
            return web.result(r)
        return web.Response(base64.b64decode(r.output["data_b64"]), media_type=r.output["content_type"],
                            headers={"Content-Disposition": files.disposition("inline" if r.output["inline"] else "attachment", r.output["filename"]),
                                     "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})


_routes()
