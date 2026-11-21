"""Tệp của hồ sơ (ADR 0249 lát 1): quyền theo hồ sơ (RLS qua core.subject_visible), gỡ thì biến mất, một tệp nhiều chỗ dùng không chép (tin đính kèm ⇒
tệp của hồ sơ; một tệp trên hai hồ sơ mở được với người thấy một trong hai), biết file_id / tự chèn khoá ngoại không đủ để mở; khối "Tệp" trên thẻ khách
và thẻ việc; đường tải lên / tải về."""

import base64
import hashlib
import uuid
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from barista.db import db
from barista.http_api import app
from barista.kernel import executor, files, registry
from barista.kernel.errors import Denied
from barista.kernel.principal import Principal
from tests.conftest import Who, ok, owner_val, q
from tests.test_files import as_module
from tests.test_talk_daily import PNG, _lead, _post_file

PDF = b"%PDF-1.4\n% hop dong mau\n" + b"0" * 200


async def _attach(p: Principal, subj: dict[str, str], data: bytes = PDF, name: str = "Hợp đồng.pdf", **extra: Any) -> executor.Result:
    """Như route tải lên: thân tệp đi KÈM lệnh, đầu vào chỉ có tên + cỡ + sha256."""
    inp = {**subj, "filename": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), **extra}
    return await executor.run(p, "core.attach_file", inp, files={"file": data})


async def _attached(p: Principal, subj: dict[str, str], data: bytes = PDF, **extra: Any) -> str:
    r = await _attach(p, subj, data, **extra)
    assert r.status == "ok", (r.status, r.error)
    return str(r.output["record_file_id"])


def _files_block(page: dict[str, Any]) -> dict[str, Any] | None:
    return next((b for b in page["blocks"] if b.get("type") == "list" and b.get("title") == "Tệp"), None)


async def _visible(p: Principal, subject_id: str) -> int:
    """Đếm bằng RLS của ``p`` — chứng minh chính Postgres lọc, không phải mã hiện thẻ."""
    async with db.tenant_tx(p) as conn:
        cur = await conn.execute("SELECT count(*) AS n FROM core.record_files WHERE subject_id = %s", (UUID(subject_id),))
        row = await cur.fetchone()
    return int(row["n"]) if row else 0


async def _data(p: Principal, rid: str) -> executor.Result:
    return await executor.query(p, "core.file_body", {"record_file_id": rid})


async def _file_id(rid: str) -> UUID:
    return await owner_val("SELECT file_id FROM core.record_files WHERE id = %s", (UUID(rid),))


def test_lead_and_task_accept_record_files() -> None:
    assert {"crm.lead", "flow.task"} <= set(registry.RECORD_FILES)
    assert all(k in registry.SUBJECTS for k in registry.RECORD_FILES)


def test_a_module_declares_record_files_only_for_its_own_subjects() -> None:
    from barista.modules.crm import m as crm

    with pytest.raises(ValueError, match="chính mình"):
        crm.record_files("flow.task")


async def test_record_files_follow_who_can_see_the_record(who: Who) -> None:
    sale1, sale2, sale3, truonga = await who("sale1"), await who("sale2"), await who("sale3"), await who("truonga")
    subj = await _lead(sale1)
    rid = await _attached(sale1, subj, label="Hợp đồng")
    block = _files_block(await q(sale1, "crm.journey", lead_id=subj["subject_id"]))   # khối trên thẻ khách, cạnh trao đổi / lịch sử
    assert block is not None and [(i["title"], i["badge"]) for i in block["items"]] == [("Hợp đồng.pdf", "Hợp đồng")]
    assert block["attach"]["subject_id"] == subj["subject_id"] and block["attach"]["path"] == "/v1/record_files"
    assert base64.b64decode((await _data(sale1, rid)).output["data_b64"]) == PDF
    assert await _visible(sale3, subj["subject_id"]) == 0                                   # đội khác không thấy khách ⇒ không thấy tệp
    assert (await _data(sale3, rid)).status == "not_found"
    async with as_module(sale3, "core") as ctx:                                             # kernel cũng chặn: chỗ dùng không thấy được
        with pytest.raises(Denied):
            await files.open(ctx, await _file_id(rid))
    assert (await _attach(sale3, subj)).status == "not_found"                               # không thấy hồ sơ ⇒ không gắn được
    await ok(truonga, "crm.assign", lead_id=subj["subject_id"], owner_email=sale2.email)
    assert await _visible(sale1, subj["subject_id"]) == 0                                   # người cũ mất khách ⇒ mất tệp ngay
    assert (await _data(sale1, rid)).status == "not_found"
    assert base64.b64decode((await _data(sale2, rid)).output["data_b64"]) == PDF


async def test_a_removed_file_disappears_for_everyone_and_cannot_be_opened(who: Who) -> None:
    sale1, truonga = await who("sale1"), await who("truonga")
    subj = await _lead(sale1)
    rid = await _attached(truonga, subj)                                                     # trưởng nhóm gắn vào hồ sơ của sale1
    other = await _attached(truonga, subj, PDF + b"khac")
    assert (await _attached(truonga, subj)) == rid                                          # cùng nội dung ⇒ không thêm bản thứ hai
    stranger = await who("sale2")
    await ok(truonga, "crm.assign", lead_id=subj["subject_id"], owner_email=sale1.email)   # vẫn sale1 phụ trách
    r = await executor.run(stranger, "core.remove_file", {"record_file_id": other})
    assert r.status == "not_found"                                                          # không thấy hồ sơ
    await ok(sale1, "core.remove_file", record_file_id=rid)                                 # người phụ trách gỡ được tệp người khác gắn
    assert await _visible(sale1, subj["subject_id"]) == 1 and await _visible(truonga, subj["subject_id"]) == 1
    assert (await _data(truonga, rid)).status == "not_found"
    async with as_module(truonga, "core") as ctx:
        with pytest.raises(Denied):                                                         # dòng đã gỡ không còn là chỗ dùng
            await files.open(ctx, await _file_id(rid))
    items = _files_block(await q(sale1, "crm.journey", lead_id=subj["subject_id"]))["items"]  # type: ignore[index]
    assert [i["title"] for i in items] == ["Hợp đồng.pdf"] and items[0]["open"]["input"]["record_file_id"] == other
    assert await owner_val("SELECT removed_by FROM core.record_file_removals WHERE record_file_id = %s", (UUID(rid),)) == sale1.user_id   # không xoá
    async with db.owner_tx() as conn:
        assert await files.release_orphan_rows(conn) >= 0
    assert await owner_val("SELECT released_at FROM core.files WHERE id = %s", (await _file_id(rid),)) is None          # còn dòng ⇒ không dọn
    async with db.tenant_tx(sale1) as conn:                                                 # barista_app không xoá được dòng
        with pytest.raises(Exception, match="permission denied"):
            await conn.execute("DELETE FROM core.record_files WHERE id = %s", (UUID(other),))


async def test_only_the_person_who_attached_or_the_owner_removes(who: Who) -> None:
    sale1, truonga, giamdoc = await who("sale1"), await who("truonga"), await who("giamdoc")
    subj = await _lead(sale1)
    rid = await _attached(truonga, subj)
    r = await executor.run(giamdoc, "core.remove_file", {"record_file_id": rid})           # thấy hồ sơ nhưng không gắn, không phụ trách
    assert r.status == "denied", r
    page = await q(giamdoc, "core.file_card", record_file_id=rid)
    assert page["actions"] == [] and page["links"][0]["path"] == f"/v1/record_files/{rid}"
    assert [a["command"] for a in (await q(truonga, "core.file_card", record_file_id=rid))["actions"]] == ["core.remove_file"]


async def test_a_talk_file_kept_on_the_record_is_the_same_file_not_a_copy(who: Who) -> None:
    sale1, truonga = await who("sale1"), await who("truonga")
    subj = await _lead(sale1)
    sent = await _post_file(sale1, subj, "CCCD.png", PNG)
    att = str(await owner_val("SELECT id FROM talk.attachments WHERE message_id = %s", (sent["message_id"],)))
    fid = await owner_val("SELECT file_id FROM talk.attachments WHERE id = %s", (UUID(att),))
    before = await owner_val("SELECT count(*) FROM core.files")
    thread = next(b for b in (await q(sale1, "talk.thread", **subj))["blocks"] if b.get("type") == "thread")
    keep = thread["messages"][-1]["attachments"][0]["keep"]                                 # nút "Lưu vào hồ sơ" trên tệp của tin
    assert keep["command"] == "talk.keep_on_record"
    out = await ok(sale1, keep["command"], **keep["preset"])
    rid = str(out["record_file_id"])
    assert await _file_id(rid) == fid and await owner_val("SELECT count(*) FROM core.files") == before   # cùng tệp, không chép
    assert await owner_val("SELECT via FROM core.record_files WHERE id = %s", (UUID(rid),)) == "reference"
    assert await owner_val("SELECT by_reference FROM core.file_copy_grants WHERE file_id = %s AND to_module = 'core'", (fid,)) is True
    assert str((await ok(sale1, keep["command"], **keep["preset"]))["record_file_id"]) == rid   # lưu lại lần nữa ⇒ vẫn một dòng
    await ok(sale1, "talk.recall_message", message_id=sent["message_id"])
    assert (await executor.query(truonga, "talk.file", {"attachment_id": att})).status == "not_found"   # tin thu hồi: trao đổi không mở nữa…
    assert base64.b64decode((await _data(truonga, rid)).output["data_b64"]) == PNG           # …hồ sơ vẫn mở (chỗ dùng của hồ sơ)


async def test_one_file_on_two_records_opens_for_a_viewer_of_either(who: Who) -> None:
    sale1, sale2, sale3 = await who("sale1"), await who("sale2"), await who("sale3")
    a, b = await _lead(sale1), await _lead(sale2)
    rid_a = await _attached(sale1, a)
    fid = await _file_id(rid_a)
    gd = await who("giamdoc")
    async with as_module(gd, "core") as ctx:                                                 # người thấy cả hai: lõi cấp tệp đang giữ cho chính nó
        grant = await files.grant(ctx, fid, "core")
    rid_b = str((await ok(gd, "core.attach_file", **b, file_id=str(fid), grant=str(grant)))["record_file_id"])
    assert await _file_id(rid_b) == fid
    assert base64.b64decode((await _data(sale1, rid_a)).output["data_b64"]) == PDF          # thấy hồ sơ A
    assert base64.b64decode((await _data(sale2, rid_b)).output["data_b64"]) == PDF          # chỉ thấy hồ sơ B — vẫn mở cùng tệp
    for p in (sale1, sale2):
        async with as_module(p, "core") as ctx:
            assert await files.read(ctx, fid) == PDF
    if not await _visible(sale3, a["subject_id"]) and not await _visible(sale3, b["subject_id"]):
        async with as_module(sale3, "core") as ctx:
            with pytest.raises(Denied):
                await files.open(ctx, fid)
    c = await _lead(sale1)
    assert (await executor.run(gd, "core.attach_file", {**c, "file_id": str(fid), "grant": str(grant)})).status == "denied"   # quyền lấy một lần
    assert (await executor.run(gd, "core.attach_file", {**c, "file_id": str(fid)})).status == "invalid"                       # thiếu quyền lấy tệp


async def test_writing_a_file_id_into_a_module_row_does_not_let_that_module_open_it(who: Who) -> None:
    """Sửa luật 0122 giữ chốt cũ: module chỉ mở tệp nó GIỮ (đã tạo / đã lấy qua quyền của kernel) — tự chèn khoá ngoại không đủ."""
    sale1, gd = await who("sale1"), await who("giamdoc")
    subj = await _lead(sale1)
    fid = await _file_id(await _attached(sale1, subj))
    doc = str((await ok(gd, "kb.add_document", title=f"Mượn tệp {uuid.uuid4().hex[:6]}", kind="other", body="x"))["document_id"])
    async with as_module(gd, "kb", rollback=True) as ctx:
        await ctx.exec("INSERT INTO kb.files (document_id, filename, content_type, size_bytes, file_id) VALUES (%s, 'x.pdf', 'application/pdf', 1, %s)",
                       (doc, fid))
        with pytest.raises(Denied):
            await files.open(ctx, fid)


async def test_the_files_block_sits_on_the_task_card(who: Who) -> None:
    truonga, sale1, sale3 = await who("truonga"), await who("sale1"), await who("sale3")
    task = str((await ok(truonga, "flow.create_task", title="Gửi bảng giá", assignee_email=sale1.email))["task_id"])
    page = await q(sale1, "flow.task", task_id=task)
    block = _files_block(page)
    assert block is not None and block["items"][0]["tone"] == "muted"                       # chưa có tệp: dòng gợi ý, vẫn nhận thả tệp
    titles = [b.get("title") for b in page["blocks"]]
    assert titles.index("Tệp") > max((i for i, b in enumerate(page["blocks"]) if b.get("type") == "thread"), default=-1)
    await _attached(sale1, {"subject_kind": "flow.task", "subject_id": task}, PNG, name="ket-qua.png")
    assert [i["title"] for i in _files_block(await q(truonga, "flow.task", task_id=task))["items"]] == ["ket-qua.png"]   # type: ignore[index]
    r = await executor.query(sale3, "core.files_of", {"subject_kind": "flow.task", "subject_id": task})
    assert r.status == "not_found"


async def test_a_subject_that_does_not_accept_files_is_refused(who: Who) -> None:
    sale1 = await who("sale1")
    room = (await ok(sale1, "talk.create_room", name=f"Phòng {uuid.uuid4().hex[:4]}"))
    r = await _attach(sale1, {"subject_kind": "talk.room", "subject_id": str(room["room_id"])})
    assert r.status == "invalid" and "chưa nhận tệp" in r.error


async def test_upload_and_download_over_http(who: Who) -> None:
    sale1, sale3 = await who("sale1"), await who("sale3")
    subj = await _lead(sale1)

    def h(p: Principal) -> dict[str, str]:
        return {"x-dev-user": p.email, "x-tenant-id": str(p.tenant_id)}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/v1/record_files", data={**subj, "label": "Hợp đồng"}, files={"file": ("hd.pdf", PDF, "application/octet-stream")},
                         headers={**h(sale1), "idempotency-key": "rf1"})
        assert r.status_code == 200, r.text
        rid = r.json()["output"]["record_file_id"]
        got = await c.get(f"/v1/record_files/{rid}", headers=h(sale1))
        assert got.status_code == 200 and got.content == PDF and got.headers["content-type"] == "application/pdf"
        assert got.headers["cache-control"] == "private, no-store" and got.headers["content-disposition"].startswith("inline;")
        assert (await c.get(f"/v1/record_files/{rid}", headers=h(sale3))).status_code == 404
        bad = await c.post("/v1/record_files", data=subj, files={"file": ("x.html", b"<html><script>", "text/html")}, headers=h(sale1))
        assert bad.json()["status"] == "invalid"
