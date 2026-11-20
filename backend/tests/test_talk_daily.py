"""Trao đổi dùng hằng ngày (quyết định 0115): tin riêng chỉ hai người đọc, chữ tin không lọt ra nhật ký / thông báo, tệp theo quyền luồng,
sửa / thu hồi có vết, trả lời / cảm xúc / ghim, tìm trong tin nhắn bằng quyền người tìm, kênh báo tức thì không gửi gì cho người đã mất quyền."""

import asyncio
import base64
import contextlib
import dataclasses
import hashlib
import io
import json
import socket
import time
import uuid
import zipfile
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import psycopg
import uvicorn
from httpx import ASGITransport, AsyncClient

from barista.db import db
from barista.http_api import app
from barista.kernel import executor, live, registry
from barista.modules import talk
from tests.conftest import OWNER_URL, Who, approved, fresh_phone, fresh_sale, ok, owner_val, q
from tests.test_journey import _blocks

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


async def _lead(sale: Any, name: str = "") -> dict[str, str]:
    out = await ok(sale, "crm.create_lead", name=name or f"Khách tin {uuid.uuid4().hex[:4]}", phone=fresh_phone(), offering_code="HSR")
    return {"subject_kind": "crm.lead", "subject_id": str(out["lead_id"])}


async def _thread(p: Any, subj: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return _blocks(await q(p, "talk.thread", **subj, **extra), "thread")[0]


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


async def _send_file(p: Any, subj: dict[str, Any], filename: str, data: bytes, **extra: Any) -> Any:
    """Như route tải lên: nội dung tệp đi KÈM lệnh (``files``), đầu vào chỉ có cỡ + sha256."""
    inp = {**subj, "filename": filename, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), **extra}
    return await executor.run(p, "talk.post_file", inp, files={"file": data})


async def _post_file(p: Any, subj: dict[str, Any], filename: str, data: bytes, **extra: Any) -> Any:
    r = await _send_file(p, subj, filename, data, **extra)
    assert r.status == "ok", (r.status, r.error)
    return r.output


# ── tin riêng ──

async def test_direct_messages_are_read_by_the_two_people_only(who: Who) -> None:
    sale1, sale2, admin, giamdoc = await who("sale1"), await who("sale2"), await who("admin"), await who("giamdoc")
    out = await ok(sale1, "talk.start_direct", person=sale2.email, body="Chiều nay em đi xem nhà mẫu với khách giúp chị nhé")
    subj = {"subject_kind": out["subject_kind"], "subject_id": str(out["subject_id"])}
    again = await ok(sale1, "talk.start_direct", person=sale2.email)                      # mỗi cặp một cuộc — mở lại cuộc cũ
    assert str(again["subject_id"]) == subj["subject_id"]
    t = await _thread(sale2, subj)
    assert [m["body"] for m in t["messages"]] == ["Chiều nay em đi xem nhà mẫu với khách giúp chị nhé"] and t["people"] == []
    assert t["subject_title"] == sale1.name and t["kind_label"] == "Tin riêng"            # tên cuộc = người KIA
    for boss in (admin, giamdoc):                                                         # quản trị / giám đốc phạm vi cả công ty vẫn không đọc
        assert (await executor.query(boss, "talk.thread", subj)).status == "not_found"
        assert (await executor.run(boss, "talk.post", {**subj, "body": "chen vào"})).status == "not_found"
        async with db.tenant_tx(boss, read_only=True) as conn:                            # cả khi đọc thẳng bảng: chính sách DB chặn
            cur = await conn.execute("SELECT (SELECT count(*) FROM talk.directs WHERE id = %s) + (SELECT count(*) FROM talk.messages m "
                                     "JOIN talk.threads th ON th.id = m.thread_id WHERE th.subject_id = %s) AS n", (subj["subject_id"], subj["subject_id"]))
            row = await cur.fetchone()
            assert row is not None and row["n"] == 0
    row = next(i for i in (await q(sale2, "talk.inbox"))["items"] if i["subject_id"] == subj["subject_id"])
    assert (row["section"], row["title"], row["unread"]) == ("direct", sale1.name, 1)
    notice = next(n for n in (await q(sale2, "core.notifications", limit=30))["items"] if n["kind"] == "direct")
    assert notice["body"] == "" and "nhà mẫu" not in notice["title"]                      # thông báo không chép chữ tin
    assert (await executor.run(sale1, "talk.start_direct", {"person": sale1.email})).status == "invalid"
    r = await executor.run(sale1, "talk.post", {**subj, "body": "@Lan", "mentions": [admin.email]})
    assert r.status == "invalid"                                                          # tin riêng: không nhắc người thứ ba vào được
    p = await executor.run(sale1.as_agent(), "talk.start_direct", {"person": sale2.email, "body": "Trợ lý soạn hộ"})
    assert p.status == "proposed"                                                         # trợ lý chỉ soạn, người bấm mới gửi


async def test_message_text_never_reaches_the_audit_log_or_notifications(who: Who) -> None:
    sale1, truonga = await who("sale1"), await who("truonga")
    subj = await _lead(sale1)
    secret = f"BIMAT{uuid.uuid4().hex[:8]}"
    await ok(sale1, "talk.post", **subj, body=f"Khách ngại giá {secret}", mentions=[truonga.email])
    await ok(truonga, "talk.post", **subj, body=f"Anh duyệt chiết khấu {secret}")         # người phụ trách (sale1) nhận thông báo
    assert await owner_val("SELECT count(*) FROM core.audit_log WHERE command = 'talk.post' AND input::text LIKE %s", (f"%{secret}%",)) == 0
    assert await owner_val("SELECT count(*) FROM core.audit_log WHERE command = 'talk.post' AND input->'body'->>'omitted' = 'true'") >= 2
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE title || body || push LIKE %s", (f"%{secret}%",)) == 0
    assert await owner_val("SELECT count(*) FROM core.idempotency WHERE output::text LIKE %s", (f"%{secret}%",)) == 0


# ── tệp ──

async def test_attachments_follow_the_thread_and_are_checked_on_the_server(who: Who) -> None:
    sale1, sale2, truonga = await who("sale1"), await who("sale2"), await who("truonga")
    subj = await _lead(sale1, "Khách gửi ảnh")
    out = await _post_file(sale1, subj, "../../sổ đỏ.png", PNG, body="Ảnh sổ đỏ khách gửi")
    t = await _thread(truonga, subj)
    f = t["messages"][-1]["attachments"][0]
    assert (f["filename"], f["content_type"], f["is_image"], f["size_bytes"]) == ("sổ đỏ.png", "image/png", True, len(PNG))
    got = await q(truonga, "talk.file", attachment_id=f["id"])
    assert base64.b64decode(got["data_b64"]) == PNG and got["inline"]
    assert (await executor.query(sale2, "talk.file", {"attachment_id": f["id"]})).status == "not_found"   # không thấy khách ⇒ không thấy tệp
    logged = await owner_val("SELECT input FROM core.audit_log WHERE command = 'talk.post_file' ORDER BY id DESC LIMIT 1")
    assert logged["sha256"] == hashlib.sha256(PNG).hexdigest() and "data" not in json.dumps(logged)   # nhật ký: cỡ + băm, không nội dung
    assert out["message_id"]
    for name, data in (("a.svg", b"<svg onload=alert(1)>"), ("a.html", b"<html><script>"), ("a.pdf", b"MZ\x90\x00"), ("a.exe", b"MZ"),
                       ("a.docx", b"PK\x03\x04 khong phai zip")):
        r = await _send_file(sale1, subj, name, data)
        assert r.status == "invalid", name                                                 # loại kiểm theo NỘI DUNG, không theo tên / máy khách
    big = await _send_file(sale1, subj, "to.png", PNG + b"\0" * talk.FILE_MAX)
    assert big.status == "invalid"
    forged = await executor.run(sale1, "talk.post_file", {**subj, "filename": "a.png", "size": len(PNG), "sha256": "0" * 64}, files={"file": PNG})
    assert forged.status == "invalid"                                                   # khai sha256 khác nội dung gửi kèm ⇒ từ chối
    assert (await executor.run(sale1, "talk.post_file", {**subj, "filename": "a.png", "size": 1, "sha256": "0" * 64})).status == "invalid"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<w/>")
    await _post_file(sale1, subj, "hop-dong.docx", buf.getvalue())
    assert (await _send_file(sale1.as_agent(), subj, "x.png", PNG)).status == "denied"
    await ok(truonga, "crm.assign", lead_id=subj["subject_id"], owner_email=sale2.email)
    assert (await executor.query(sale1, "talk.file", {"attachment_id": f["id"]})).status == "not_found"   # mất quyền xem khách ⇒ mất tệp ngay


async def test_file_routes_check_size_and_rights_over_http(who: Who) -> None:
    """Tải lên = multipart (chú thích trong THÂN, không lên URL / nhật ký truy cập); cỡ chặn khi đọc; Content-Length hỏng ⇒ 400."""
    sale1, sale2 = await who("sale1"), await who("sale2")
    subj = await _lead(sale1)
    secret = f"BIMAT{uuid.uuid4().hex[:6]}"

    def h(p: Any) -> dict[str, str]:
        return {"x-dev-user": p.email, "x-tenant-id": str(p.tenant_id)}

    form = {**subj, "body": f"Chú thích {secret}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/v1/talk/files", data=form, files={"file": ("ảnh.png", PNG, "application/octet-stream")},
                         headers={**h(sale1), "idempotency-key": "f1"})
        assert r.status_code == 200, r.text
        assert secret not in str(r.request.url) and r.request.url.query == b""          # chú thích không nằm trên URL
        again = await c.post("/v1/talk/files", data=form, files={"file": ("ảnh.png", PNG, "application/octet-stream")},
                             headers={**h(sale1), "idempotency-key": "f1"})
        assert again.json()["output"]["message_id"] == r.json()["output"]["message_id"]   # bấm lại cùng khoá ⇒ một tin
        m = (await _thread(sale1, subj))["messages"][-1]
        assert m["body"] == f"Chú thích {secret}"
        fid = m["attachments"][0]["id"]
        got = await c.get(f"/v1/talk/files/{fid}", headers=h(sale1))
        assert got.status_code == 200 and got.content == PNG and got.headers["content-type"] == "image/png"
        assert got.headers["x-content-type-options"] == "nosniff" and "no-store" in got.headers["cache-control"]
        assert got.headers["content-disposition"] == "inline; filename=\"anh.png\"; filename*=UTF-8''%E1%BA%A3nh.png"
        assert (await c.get(f"/v1/talk/files/{fid}", headers=h(sale2))).status_code == 404
        big = await c.post("/v1/talk/files", data=subj, files={"file": ("x.png", b"\0" * (talk.FILE_MAX + 1), "application/octet-stream")},
                           headers=h(sale1))
        assert big.status_code == 413
        bad = await c.post("/v1/talk/files", content=b"--x--", headers={**h(sale1), "content-type": "multipart/form-data; boundary=x",
                                                                         "content-length": "abc"})
        assert bad.status_code == 400
        assert (await c.post("/v1/talk/files", content=PNG, headers={**h(sale1), "content-type": "image/png"})).status_code == 415


async def test_a_20mb_upload_stays_close_to_the_file_size_in_memory(who: Who) -> None:
    """Review 07/11: tải lên từng chiếm ~150 MB cho tệp 20 MB (chép thân, base64, băm JSON đầu vào…). Nay: đọc theo khúc vào MỘT bộ đệm, tệp đi
    kèm lệnh không base64, bytea gửi nhị phân."""
    import tracemalloc

    sale1 = await who("sale1")
    subj = await _lead(sale1)
    data = PNG + b"\1" * (talk.FILE_MAX - len(PNG) - 1024)
    boundary = "ranhgioi" + uuid.uuid4().hex
    head = "".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n' for k, v in subj.items())
    body = (head + f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="lon.png"\r\n\r\n').encode() + data \
        + f"\r\n--{boundary}--\r\n".encode()

    async def chunks() -> AsyncIterator[bytes]:   # như mạng thật: thân tới theo khúc 64 KB, không nằm sẵn trong bộ nhớ của route
        for i in range(0, len(body), 65536):
            yield body[i:i + 65536]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        tracemalloc.start()
        try:
            r = await c.post("/v1/talk/files", content=chunks(), headers={"x-dev-user": sale1.email, "x-tenant-id": str(sale1.tenant_id),
                                                                         "content-type": f"multipart/form-data; boundary={boundary}"})
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
    assert r.status_code == 200, r.text
    print(f"\n[bộ nhớ] tệp {len(data) / 1e6:.1f} MB ⇒ đỉnh {peak / 1e6:.1f} MB ({peak / len(data):.2f}× cỡ tệp)")
    assert peak < 3 * len(data)


# ── sửa / thu hồi / trả lời / cảm xúc / ghim ──

async def test_edit_and_recall_keep_a_trail_and_hide_the_original(who: Who) -> None:
    sale1, truonga, admin = await who("sale1"), await who("truonga"), await who("admin")
    subj = await _lead(sale1, "Khách thu hồi")
    first = await ok(sale1, "talk.post", **subj, body="Giá chốt 3,1 tỷ")
    await ok(sale1, "talk.edit_message", message_id=first["message_id"], body="Giá chốt 3,2 tỷ")
    m = (await _thread(truonga, subj))["messages"][-1]
    assert (m["body"], m["edited"]) == ("Giá chốt 3,2 tỷ", True) and m["history"]
    hist = await q(truonga, "talk.message_history", message_id=first["message_id"])
    assert [i["title"] for i in _blocks(hist, "list")[0]["items"]] == ["Giá chốt 3,2 tỷ", "Giá chốt 3,1 tỷ"]   # ai đọc được luồng thấy bản trước khi sửa
    r = await executor.run(truonga, "talk.edit_message", {"message_id": first["message_id"], "body": "sửa hộ"})
    assert r.status == "denied"                                                           # chỉ người gửi sửa được

    secret = f"BIMAT{uuid.uuid4().hex[:6]}"
    sent = await _post_file(sale1, subj, "bang-gia.png", PNG, body=f"Bảng giá nội bộ {secret}")
    await ok(sale1, "talk.pin", message_id=sent["message_id"])
    fid = (await _thread(truonga, subj))["messages"][-1]["attachments"][0]["id"]
    await ok(sale1, "talk.recall_message", message_id=sent["message_id"])
    seen = (await _thread(truonga, subj))
    m = seen["messages"][-1]
    assert (m["body"], m["recalled"], m["attachments"], m["history"]) == (talk.RECALLED, True, [], None)
    assert not any(p["id"] == sent["message_id"] for p in seen["pinned"])                  # thu hồi ⇒ bỏ ghim
    assert (await executor.query(truonga, "talk.file", {"attachment_id": fid})).status == "not_found"
    assert (await executor.query(truonga, "talk.message_history", {"message_id": sent["message_id"]})).status == "not_found"
    async with db.tenant_tx(truonga, read_only=True) as conn:                              # DB chặn cả khi đọc thẳng bảng
        cur = await conn.execute("SELECT (SELECT count(*) FROM talk.message_versions WHERE message_id = %(m)s) "
                                 "+ (SELECT count(*) FROM talk.attachments WHERE message_id = %(m)s) AS n", {"m": sent["message_id"]})
        row = await cur.fetchone()
        assert row is not None and row["n"] == 0
    for viewer in (sale1, admin):                                                         # người gửi + quyền "Xem lại tin đã thu hồi" vẫn xem được
        items = _blocks(await q(viewer, "talk.message_history", message_id=sent["message_id"]), "list")[0]["items"]
        assert any(secret in i["title"] for i in items)
    assert (await q(sale1, "talk.file", attachment_id=fid))["data_b64"]                    # tệp gốc vẫn còn (không xoá)
    assert await owner_val("SELECT count(*) FROM talk.messages WHERE id = %s", (sent["message_id"],)) == 1
    hits = await q(truonga, "core.find", q=secret)
    assert not any(secret in i["title"] for b in _blocks(hits, "list") for i in b["items"])   # không tìm ra tin đã thu hồi
    digest = await q(truonga, "talk.digest", **subj)
    assert all(secret not in x["body"] for x in digest["messages"])                         # trợ lý không đọc tin đã thu hồi
    row = next(i for i in (await q(sale1, "talk.inbox"))["items"] if i["subject_id"] == subj["subject_id"])
    assert row["last_body"] == talk.RECALLED                                               # dòng cuối ở hộp tin cũng chỉ còn "đã thu hồi"
    assert (await executor.run(truonga, "talk.recall_message", {"message_id": first["message_id"]})).status == "denied"
    assert (await executor.run(sale1.as_agent(), "talk.recall_message", {"message_id": first["message_id"]})).status == "denied"


async def test_replies_reactions_and_pins(who: Who) -> None:
    truonga, sale1, sale3 = await who("truonga"), await who("sale1"), await who("sale3")
    room = await ok(truonga, "talk.create_room", name=f"Phòng ghim {uuid.uuid4().hex[:4]}", members=[sale1.email])
    subj = {"subject_kind": "talk.room", "subject_id": str(room["room_id"])}
    q1 = await ok(truonga, "talk.post", **subj, body="Lịch trực cuối tuần: An thứ Bảy, Nam Chủ nhật")
    await ok(sale1, "talk.post", **subj, body="Em nhận ạ", reply_to=q1["message_id"])
    await ok(truonga, "talk.pin", message_id=q1["message_id"])
    await ok(sale1, "talk.react", message_id=q1["message_id"], emoji="👍")
    await ok(sale1, "talk.react", message_id=q1["message_id"], emoji="👍")                 # bấm lại không thành hai
    t = await _thread(truonga, subj)
    first, reply = t["messages"][-2], t["messages"][-1]
    assert reply["reply"] == {"id": q1["message_id"], "author": truonga.name, "body": "Lịch trực cuối tuần: An thứ Bảy, Nam Chủ nhật", "recalled": False}
    assert first["reactions"] == [{"emoji": "👍", "count": 1, "mine": False, "names": [sale1.name]}] and first["pinned"]
    assert [p["id"] for p in t["pinned"]] == [q1["message_id"]] and t["pinned"][0]["pinned_by"] == truonga.name
    before = next(i for i in (await q(truonga, "talk.inbox"))["items"] if i["subject_id"] == subj["subject_id"])["unread"]
    await ok(sale1, "talk.react", message_id=q1["message_id"], emoji="✅")
    after = next(i for i in (await q(truonga, "talk.inbox"))["items"] if i["subject_id"] == subj["subject_id"])["unread"]
    assert after == before                                                                # cảm xúc không thành tin chưa đọc
    await ok(sale1, "talk.react", message_id=q1["message_id"], emoji="👍", on=False)
    assert [r["emoji"] for r in (await _thread(truonga, subj))["messages"][-2]["reactions"]] == ["✅"]
    for bad in ({"message_id": q1["message_id"], "emoji": "💩"},):
        assert (await executor.run(sale1, "talk.react", bad)).status == "invalid"
    assert (await executor.run(sale3, "talk.react", {"message_id": q1["message_id"], "emoji": "👍"})).status == "not_found"   # ngoài phòng
    assert (await executor.run(sale3, "talk.pin", {"message_id": q1["message_id"]})).status == "not_found"
    other = await _lead(sale1)
    elsewhere = await ok(sale1, "talk.post", **other, body="tin ở luồng khác")
    assert (await executor.run(sale1, "talk.post", {**subj, "body": "trả lời chéo", "reply_to": elsewhere["message_id"]})).status == "invalid"


async def test_older_messages_page_in_and_search_opens_around_the_hit(who: Who) -> None:
    truonga, sale1, sale2 = await who("truonga"), await who("sale1"), await who("sale2")
    room = await ok(truonga, "talk.create_room", name=f"Phòng dài {uuid.uuid4().hex[:4]}", members=[sale1.email])
    subj = {"subject_kind": "talk.room", "subject_id": str(room["room_id"])}
    ids = [(await ok(truonga, "talk.post", **subj, body=f"tin số {i}" + (" Khách hẹn ký hợp đồng Đà Lạt" if i == 5 else "")))["message_id"]
           for i in range(talk.PAGE + 20)]
    latest = await _thread(sale1, subj)
    assert len(latest["messages"]) == talk.PAGE and latest["has_more"] and latest["messages"][0]["id"] == ids[20]
    older = await _thread(sale1, subj, before=latest["messages"][0]["id"])
    assert [m["id"] for m in older["messages"]] == ids[:20] and not older["has_more"]
    hits = [i for b in _blocks(await q(sale1, "core.find", q="hen ky hop dong da lat"), "list") for i in b["items"]]   # gõ không dấu vẫn khớp
    hit = next(i for i in hits if "Đà Lạt" in i["title"])
    assert hit["open"]["input"]["around"] == ids[5]
    around = await _thread(sale1, hit["open"]["input"])
    assert around["focus_id"] == ids[5] and ids[5] in [m["id"] for m in around["messages"]] and around["has_newer"]
    assert not any("Đà Lạt" in i["title"] for b in _blocks(await q(sale2, "core.find", q="Đà Lạt"), "list") for i in b["items"])   # ngoài phòng ⇒ không


async def test_mute_keeps_the_thread_but_drops_it_from_the_menu_count(who: Who) -> None:
    truonga, sale1 = await who("truonga"), await who("sale1")
    room = await ok(truonga, "talk.create_room", name=f"Phòng ồn {uuid.uuid4().hex[:4]}", members=[sale1.email])
    subj = {"subject_kind": "talk.room", "subject_id": str(room["room_id"])}
    await ok(truonga, "talk.post", **subj, body="Tin thường")
    box = await q(sale1, "talk.inbox")
    row = next(i for i in box["items"] if i["subject_id"] == subj["subject_id"])
    await ok(sale1, "talk.mute", thread_id=row["thread_id"])
    muted = await q(sale1, "talk.inbox")
    row2 = next(i for i in muted["items"] if i["subject_id"] == subj["subject_id"])
    assert row2["muted"] and row2["unread"] == 1 and muted["unread"] == box["unread"] - 1   # vẫn thấy số của luồng, không tính lên menu
    await ok(truonga, "talk.post", **subj, body="@An xem gấp", mentions=[sale1.email])
    assert (await q(sale1, "talk.inbox"))["unread"] == box["unread"]                       # bị nhắc tên thì vẫn tính


async def test_people_who_leave_or_are_removed_from_a_room_lose_it_at_once(who: Who) -> None:
    truonga, sale1, sale2 = await who("truonga"), await who("sale1"), await who("sale2")
    room = await ok(truonga, "talk.create_room", name=f"Phòng mời ra {uuid.uuid4().hex[:4]}", members=[sale1.email, sale2.email])
    subj = {"subject_kind": "talk.room", "subject_id": str(room["room_id"])}
    await _post_file(truonga, subj, "lich.png", PNG)
    fid = (await _thread(sale2, subj))["messages"][-1]["attachments"][0]["id"]
    assert (await executor.run(sale1, "talk.remove_member", {"room_id": subj["subject_id"], "person": sale2.email})).status == "denied"
    await ok(truonga, "talk.remove_member", room_id=subj["subject_id"], person=sale2.email)
    assert (await executor.query(sale2, "talk.thread", subj)).status == "not_found"
    assert (await executor.query(sale2, "talk.file", {"attachment_id": fid})).status == "not_found"
    await ok(truonga, "talk.leave_room", room_id=subj["subject_id"])                       # người quản phòng rời ⇒ người còn lại nhận quản
    assert any(a["command"] == "talk.remove_member" for a in (await q(sale1, "talk.room", room_id=subj["subject_id"]))["actions"])


async def test_the_assistant_reads_a_thread_with_the_askers_rights_and_only_drafts(who: Who) -> None:
    sale1, truonga, sale2 = await who("sale1"), await who("truonga"), await who("sale2")
    subj = await _lead(sale1, "Khách tóm tắt")
    await ok(truonga, "talk.post", **subj, body="Chốt: giữ căn tới thứ Sáu", mentions=[sale1.email])
    await ok(sale1, "talk.post", **subj, body="Em gọi khách chiều nay")
    t = await _thread(truonga, subj)
    assert t["assist"] and "Khách tóm tắt" in t["assist"][-1]["prompt"]                     # nút "Tóm tắt" do server khai — app chỉ vẽ
    d = await q(truonga.as_agent(), "talk.digest", **subj, only_unread=True)
    assert [x["body"] for x in d["messages"]] == ["Em gọi khách chiều nay"]                 # chưa đọc của CHÍNH người hỏi, không gồm tin của họ
    assert (await executor.query(sale2.as_agent(), "talk.digest", subj)).status == "not_found"   # trợ lý không hơn người: không thấy khách ⇒ không đọc
    n = await owner_val("SELECT count(*) FROM talk.messages m JOIN talk.threads th ON th.id = m.thread_id WHERE th.subject_id = %s", (subj["subject_id"],))
    p = await executor.run(truonga.as_agent(), "talk.post", {**subj, "body": "Tóm tắt: giữ căn tới thứ Sáu"})
    assert p.status == "proposed"                                                          # tóm tắt không tự vào luồng chung
    assert await owner_val("SELECT count(*) FROM talk.messages m JOIN talk.threads th ON th.id = m.thread_id WHERE th.subject_id = %s",
                           (subj["subject_id"],)) == n
    for cmd in ("react", "pin", "edit_message", "recall_message", "mute", "remove_member", "post_file"):
        op = registry.get(f"talk.{cmd}")
        assert op is not None and op.agent == "deny", cmd                                   # việc của chính người dùng: trợ lý không làm thay


# ── báo tức thì qua HTTP thật: tín hiệu chỉ mang mã, người mất quyền không nhận gì ──

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.asynccontextmanager
async def _server() -> AsyncIterator[str]:
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off", log_level="warning"))
    task = asyncio.create_task(server.serve())
    listening = asyncio.Event()
    listener = asyncio.create_task(live.listen_forever(OWNER_URL, listening))
    try:
        while not server.started:   # chờ cổng mở (không có tín hiệu sẵn của uvicorn); máy chủ chết sớm ⇒ lỗi ngay thay vì treo
            assert not task.done(), "máy chủ thử không khởi động được"
            await asyncio.sleep(0.05)
        await asyncio.wait_for(listening.wait(), 60)   # LISTEN đã vào — tin NOTIFY trước lúc này không tới ai (chờ tín hiệu, không đoán bằng giờ)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        listener.cancel()
        await task


class _Lines(list[str]):
    """Các dòng một kênh tức thì đã nhận — kèm ``until``: chờ ĐẾN KHI điều kiện đúng (mỗi dòng mới đánh thức), không đoán bằng ``sleep``.
    Điều kiện không bao giờ đúng ⇒ ``TimeoutError`` sau ``timeout`` giây (đủ rộng cho máy tải nặng; vẫn ngắn hơn hạn giữ quyền 60 giây)."""

    def __init__(self) -> None:
        super().__init__()
        self._changed = asyncio.Event()

    def append(self, line: str) -> None:
        super().append(line)
        self._changed.set()

    async def until(self, pred: Callable[[list[str]], bool], timeout: float = 30.0) -> None:  # noqa: ASYNC109 — hạn chờ là chính chức năng của hàm
        async with asyncio.timeout(timeout):
            while not pred(self):
                self._changed.clear()
                await self._changed.wait()


async def _tail(base: str, path: str, p: Any, into: list[str], token: str = "") -> None:
    headers = {"authorization": f"Bearer {token}"} if token else {"x-dev-user": p.email}
    async with httpx.AsyncClient(base_url=base, timeout=None) as c, \
            c.stream("GET", path, headers={**headers, "x-tenant-id": str(p.tenant_id)}) as r:
        into.append(f"status {r.status_code}")
        async for line in r.aiter_lines():
            if line.startswith(("event:", "data:")):
                into.append(line)


async def _connected(*streams: list[str], within: float = 10.0) -> None:
    """Đợi mọi luồng đã nhận dòng trạng thái (đã nối) — ngủ cố định chập chờn khi máy bận (lần phát hành 09/11: 401 vì thu hồi phiên trước khi nối)."""
    for _ in range(int(within / 0.05)):
        if all(streams):
            break
        await asyncio.sleep(0.05)
    assert all(s and s[0] == "status 200" for s in streams), streams
    await asyncio.sleep(0.2)   # sau tiêu đề: máy chủ đã đăng ký nghe


async def test_live_signals_carry_no_text_and_stop_when_access_is_lost(who: Who) -> None:
    sale1, sale2, truonga = await who("sale1"), await who("sale2"), await who("truonga")
    subj = await _lead(sale1, "Khách tức thì")
    secret = f"BIMAT{uuid.uuid4().hex[:6]}"
    first = await ok(sale1, "talk.post", **subj, body="mở luồng", mentions=[truonga.email])
    tid = str(first["thread_id"])
    async with _server() as base:
        inbox_sale1, inbox_lead, thread_sale1 = [], [], []
        tasks = [asyncio.create_task(_tail(base, "/v1/talk/stream", sale1, inbox_sale1)),
                 asyncio.create_task(_tail(base, "/v1/talk/stream", truonga, inbox_lead)),
                 asyncio.create_task(_tail(base, f"/v1/talk/{tid}/stream", sale1, thread_sale1))]
        try:
            await asyncio.sleep(0.6)
            await ok(truonga, "talk.post", **subj, body=f"tin tức thì {secret}")
            await asyncio.sleep(1.0)
            assert f'data: {json.dumps({"threads": [tid]})}' in inbox_sale1 and f'data: {json.dumps({"threads": [tid]})}' in inbox_lead
            assert thread_sale1.count("event: changed") == 1
            await ok(truonga, "crm.assign", lead_id=subj["subject_id"], owner_email=sale2.email)   # sale1 mất quyền xem khách
            n_inbox = len(inbox_sale1)
            await ok(truonga, "talk.post", **subj, body=f"sau khi đổi người {secret}")
            await ok(truonga, "talk.react", message_id=first["message_id"], emoji="👍")
            await asyncio.sleep(1.2)
            assert inbox_sale1[n_inbox:] == []                                             # người mất quyền không nhận gì, kể cả mã luồng
            assert thread_sale1[-2:] == ["event: gone", "data: {}"]                         # luồng đang mở: báo "hết quyền" rồi đóng
            assert inbox_lead[-1] == f'data: {json.dumps({"threads": [tid]})}'
            assert all(secret not in x for x in inbox_sale1 + inbox_lead + thread_sale1)    # không dòng nào mang chữ tin
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


async def test_expired_session_and_transient_errors_are_not_reported_as_lost_access(who: Who, monkeypatch: Any) -> None:
    """0115 (review 07/11): chỉ mất quyền thật mới là ``gone``. Phiên bị thu hồi ⇒ ``auth`` (đăng nhập lại); DB lỗi tạm thời ⇒ ``retry`` (nối lại),
    không ghi lỗi hệ thống mỗi người nghe, không báo nhà vận hành."""
    from barista.kernel import alerts, sessions

    sale1, truonga = await who("sale1"), await who("truonga")
    subj = await _lead(sale1, "Khách phiên hết hạn")
    first = await ok(sale1, "talk.post", **subj, body="mở luồng", mentions=[truonga.email])
    tid = str(first["thread_id"])
    assert sale1.user_id is not None
    async with db.owner.connection() as conn:
        token, _ = await sessions.open_session(conn, sale1.user_id, provider="test")
    paged: list[str] = []
    monkeypatch.setattr(alerts, "operator", lambda *a, **k: paged.append(str(a)))
    async with _server() as base:
        by_token, by_dev, inbox = [], [], []
        tasks = [asyncio.create_task(_tail(base, f"/v1/talk/{tid}/stream", sale1, by_token, token=token)),
                 asyncio.create_task(_tail(base, f"/v1/talk/{tid}/stream", sale1, by_dev)),
                 asyncio.create_task(_tail(base, "/v1/talk/stream", truonga, inbox))]
        try:
            await _connected(by_token, by_dev, inbox)                                          # máy bận: đợi luồng nối thật, không đoán 0,6 giây
            async with db.owner.connection() as conn:
                await sessions.revoke(conn, token)                                            # đăng xuất / bị thu hồi phiên
            await ok(truonga, "talk.post", **subj, body="tin sau khi thu hồi phiên")
            await asyncio.sleep(1.0)
            assert by_token[-2:] == ["event: auth", "data: {}"]                               # đăng nhập lại — KHÔNG phải "gone"
            assert by_dev[-2:] == ["event: changed", "data: {}"]                              # người kia vẫn bình thường

            errors_before = await owner_val("SELECT count(*) FROM core.audit_log WHERE command = 'talk.can_read' AND status = 'error'")

            async def flaky(ctx: Any, inp: Any) -> Any:
                raise psycopg.OperationalError("mất kết nối DB (giả lập)")

            op = registry.get("talk.can_read")
            assert op is not None
            monkeypatch.setitem(registry.OPS, "talk.can_read", dataclasses.replace(op, handler=flaky))
            await ok(truonga, "talk.post", **subj, body="tin lúc DB chập chờn")
            await asyncio.sleep(1.0)
            assert by_dev[-2:] == ["event: retry", "data: {}"]                                # nối lại sau — không xoá luồng
            assert inbox[-2:] == ["event: retry", "data: {}"]
            assert await owner_val("SELECT count(*) FROM core.audit_log WHERE command = 'talk.can_read' AND status = 'error'") == errors_before
            assert paged == []
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


async def test_many_listeners_and_rapid_toggles_do_not_starve_logins(who: Who, monkeypatch: Any) -> None:
    """0115 (review 07/11): nạp quyền chạy trên pool CHỦ (3 kết nối, chung với mọi lượt đăng nhập). 60 kênh đang mở + 40 lần thả / bỏ cảm xúc liên
    tục: kênh KHÔNG nạp lại quyền mỗi tín hiệu (giữ quyền theo kênh, nạp lại khi công ty đổi quyền), kiểm thấy-luồng gom theo luồng, lượt đăng
    nhập chen giữa vẫn nhanh. Khoá một người giữa chừng ⇒ kênh của người đó nhận ``gone`` ngay, không chờ hết hạn giữ."""
    from barista.kernel import auth

    truonga, sale1, sale2, admin = await who("truonga"), await who("sale1"), await who("sale2"), await who("admin")
    leaver = await fresh_sale(who)
    room = await ok(truonga, "talk.create_room", name=f"Phòng tải {uuid.uuid4().hex[:4]}", members=[sale1.email, sale2.email, leaver.email])
    subj = {"subject_kind": "talk.room", "subject_id": str(room["room_id"])}
    first = await ok(truonga, "talk.post", **subj, body="Lịch trực tuần này")
    tid = str(first["thread_id"])

    loads = 0
    real_load = auth.load

    async def counting_load(*a: Any, **k: Any) -> Any:
        nonlocal loads
        loads += 1
        return await real_load(*a, **k)

    checks = 0
    op = registry.get("talk.can_read")
    assert op is not None
    real_check = op.handler

    async def counting_check(ctx: Any, inp: Any) -> Any:
        nonlocal checks
        checks += 1
        return await real_check(ctx, inp)

    monkeypatch.setattr(auth, "load", counting_load)
    monkeypatch.setitem(registry.OPS, "talk.can_read", dataclasses.replace(op, handler=counting_check))
    people = [truonga, sale1, sale2]
    async with _server() as base:
        streams = [_Lines() for _ in range(60)]
        tasks = [asyncio.create_task(_tail(base, f"/v1/talk/{tid}/stream" if i % 2 else "/v1/talk/stream", people[i % 3], streams[i]))
                 for i in range(60)]
        leaver_stream = _Lines()
        tasks.append(asyncio.create_task(_tail(base, f"/v1/talk/{tid}/stream", leaver, leaver_stream)))
        try:
            # chờ cả 61 kênh MỞ XONG (đã nạp quyền một lần) rồi mới đo — không đoán bằng sleep: máy tải nặng thì mở chậm, nạp quyền muộn bị tính nhầm là "mỗi tín hiệu".
            # Kênh luồng đăng ký nghe ngay sau dòng trạng thái (cùng vòng lặp sự kiện, trước khi phía khách kịp chạy); kênh hộp tin phát một "changed" khi đã đăng ký.
            await asyncio.gather(*(s.until(lambda x: bool(x) and x[0] == "status 200") for s in (*streams, leaver_stream)))
            await asyncio.gather(*(s.until(lambda x: "event: changed" in x) for i, s in enumerate(streams) if i % 2 == 0))
            loads_before, checks_before = loads, checks

            async def storm() -> None:
                for i in range(40):
                    await ok(sale1, "talk.react", message_id=first["message_id"], emoji="👍", on=i % 2 == 0)

            latencies: list[float] = []

            async def logins() -> None:   # lượt đăng nhập / tải manifest chen giữa: cũng nạp quyền trên pool chủ
                async with httpx.AsyncClient(base_url=base, timeout=30) as c:
                    for _ in range(15):
                        t0 = time.monotonic()
                        r = await c.get("/v1/manifest", headers={"x-dev-user": admin.email, "x-tenant-id": str(admin.tenant_id)})
                        latencies.append(time.monotonic() - t0)
                        assert r.status_code == 200
                        await asyncio.sleep(0.05)

            t0 = time.monotonic()
            await asyncio.gather(storm(), logins())
            storm_s = time.monotonic() - t0
            # cơn bấm đã lan tới MỌI kênh luồng (mỗi kênh nhận ≥1 "changed" sau khi gom) — chờ sự kiện, không đoán bằng sleep. Kênh hộp tin không chờ: hộp của người
            # không phải người bấm có thể không nhận gì, mà bất kỳ tín hiệu nào tới muộn chỉ làm số đếm TĂNG, không làm các khẳng định bên dưới đỏ oan.
            await asyncio.gather(*(s.until(lambda x: "event: changed" in x) for i, s in enumerate(streams) if i % 2))
            listener_loads = loads - loads_before - len(latencies)   # trừ lượt nạp của chính các lượt đăng nhập thử
            per_thread_listener = (checks - checks_before) / 30
            print(f"\n[tải] 60 kênh + 40 lần bấm trong {storm_s:.2f}s: nạp quyền thêm bởi kênh = {listener_loads}, "
                  f"kiểm thấy-luồng = {checks - checks_before} (≈{per_thread_listener:.1f}/kênh luồng), "
                  f"manifest chậm nhất {max(latencies) * 1000:.0f} ms, trung vị {sorted(latencies)[len(latencies) // 2] * 1000:.0f} ms")
            assert listener_loads == 0                                                       # không nạp quyền lại cho mỗi tín hiệu
            assert checks - checks_before < 40 * 30                                          # gom theo luồng: ít hơn hẳn 1 lần kiểm / tín hiệu / kênh
            # "đăng nhập không bị đói pool chủ": 15 lượt manifest chen giữa đều xong 200 (khách httpx hạn 30 giây), và kênh không nạp quyền lần nào (dòng trên) —
            # đó là nguyên nhân đói pool. KHÔNG khẳng định mốc mili-giây: chạy cùng máy với tiến trình khác thì mốc đó dao động, test chập chờn vô nghĩa.

            await approved(admin, "admin.update_member", email=leaver.email, active=False)       # khoá tài khoản ⇒ báo nạp lại quyền ngay
            await ok(truonga, "talk.post", **subj, body="tin sau khi khoá một người")
            await leaver_stream.until(lambda x: x[-2:] == ["event: gone", "data: {}"], timeout=20)   # không chờ hết hạn giữ quyền (60 giây)
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


async def test_file_names_cannot_hide_their_real_type(who: Who) -> None:
    """Review 07/11: PNG gửi với tên có ký tự đảo chiều chữ (U+202E) — "anh‮gnp.html" hiện như "anh lmth.png" nhưng lưu thành ".html". Server bỏ ký
    tự định dạng vô hình + ký tự điều khiển và ÉP đuôi theo loại nhận diện; tải về có tên dự phòng ASCII, không có dấu nháy / xuống dòng chen vào."""
    sale1 = await who("sale1")
    subj = await _lead(sale1)
    for raw, want in (("anh\u202egnp.html", "anhgnp.png"), ("bao-gia.png.exe", "bao-gia.png.png"), ("\u2066x\u2069\r\n.png", "x.png"),
                      ('a"; filename=evil.html', 'a"; filename=evil.png')):
        await _post_file(sale1, subj, raw, PNG)
        f = (await _thread(sale1, subj))["messages"][-1]["attachments"][0]
        assert f["filename"] == want and "\u202e" not in f["filename"] and f["content_type"] == "image/png", raw
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        got = await c.get(f"/v1/talk/files/{f['id']}", headers={"x-dev-user": sale1.email, "x-tenant-id": str(sale1.tenant_id)})
    cd = got.headers["content-disposition"]
    assert cd.startswith('inline; filename="a_filename_evil.png"; filename*=UTF-8\'\'') and "\n" not in cd and cd.count('"') == 2


async def test_recall_audit_scope_team_only_reaches_my_teams_authors(who: Who) -> None:
    """Review 07/11: quyền "Xem lại tin đã thu hồi" (talk.audit) theo PHẠM VI — team = chỉ tin của người cùng đội; all = mọi tin trong luồng mình đọc
    được. Trước đây phạm vi nào cũng thành "xem hết"."""
    admin, truonga, truongb, sale1, sale3 = await who("admin"), await who("truonga"), await who("truongb"), await who("sale1"), await who("sale3")
    room = await ok(truonga, "talk.create_room", name=f"Phòng kiểm {uuid.uuid4().hex[:4]}", members=[truongb.email, sale1.email, sale3.email])
    subj = {"subject_kind": "talk.room", "subject_id": str(room["room_id"])}
    a = await ok(sale1, "talk.post", **subj, body="Tin đội A sẽ thu hồi")      # sale1 ở KD-A (đội của truonga)
    b = await ok(sale3, "talk.post", **subj, body="Tin đội B sẽ thu hồi")      # sale3 ở KD-B (đội của truongb)
    await ok(sale1, "talk.recall_message", message_id=a["message_id"])
    await ok(sale3, "talk.recall_message", message_id=b["message_id"])
    await approved(admin, "admin.set_grant", role="team_lead", resource="talk.audit", scope="team", actions=["read"])
    try:
        lead_a, lead_b = await who("truonga"), await who("truongb")

        async def sees(p: Any, mid: int) -> bool:
            return (await executor.query(p, "talk.message_history", {"message_id": mid})).ok

        assert (await sees(lead_a, a["message_id"]), await sees(lead_a, b["message_id"])) == (True, False)
        assert (await sees(lead_b, a["message_id"]), await sees(lead_b, b["message_id"])) == (False, True)
        msgs = {m["id"]: m for m in (await _thread(lead_a, subj))["messages"]}
        assert msgs[a["message_id"]]["history"] and msgs[b["message_id"]]["history"] is None   # nút "lịch sử" theo đúng hàm DB
        await approved(admin, "admin.set_grant", role="team_lead", resource="talk.audit", scope="all", actions=["read"])
        assert await sees(await who("truonga"), b["message_id"])                                # all ⇒ mọi tin trong luồng đọc được
    finally:
        await approved(admin, "admin.set_grant", role="team_lead", resource="talk.audit", scope="team", actions=[])
