"""Tệp trong trao đổi + kệ / phòng của đội (quyết định 0128): tab Tệp theo quyền luồng, tin thu hồi không hiện, link rút bằng code, tìm tệp không dấu
chỉ trong luồng đọc được, kệ tài liệu theo đội (đúng một kệ, đổi tên theo đội, tắt được bằng cài đặt công ty), phòng của đội đồng bộ thành viên, phần
"Tài liệu của đội" qua điểm cắm bằng quyền người xem."""

import hashlib
import pathlib
import re
import uuid
from typing import Any

from barista.kernel import events, executor
from barista.modules import talk
from tests.conftest import Who, approved, fresh_sale, ok, owner_val, q
from tests.test_journey import _blocks

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.4\n" + b"0" * 64


async def _room(owner: Any, *people: Any) -> dict[str, str]:
    out = await ok(owner, "talk.create_room", name=f"Phòng tệp {uuid.uuid4().hex[:4]}", members=[p.email for p in people])
    return {"subject_kind": "talk.room", "subject_id": str(out["room_id"])}


async def _thread(p: Any, subj: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return _blocks(await q(p, "talk.thread", **subj, **extra), "thread")[0]


async def _file(p: Any, subj: dict[str, Any], filename: str, data: bytes, body: str = "") -> int:
    r = await executor.run(p, "talk.post_file", {**subj, "filename": filename, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "body": body},
                           files={"file": data})
    assert r.status == "ok", (r.status, r.error)
    return int(r.output["message_id"])


def _items(page: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for b in page["blocks"] if b["type"] == "list" for i in b["items"]]


async def _files(p: Any, thread_id: str, kind: str) -> dict[str, Any]:
    return await q(p, "talk.files", thread_id=thread_id, kind=kind)


# ── mục 1: tab Tệp ──

def test_links_are_read_from_the_text_only() -> None:
    assert talk.links("xem https://Mau.example.vn/a?b=1, và (http://x.test/hd.pdf). trùng https://Mau.example.vn/a?b=1") == [
        ("https://Mau.example.vn/a?b=1", "mau.example.vn"), ("http://x.test/hd.pdf", "x.test")]
    assert talk.links("ftp://a.test javascript:alert(1) http:// không") == []
    assert len(talk.links(" ".join(f"https://a{i}.test" for i in range(40)))) == talk.LINKS_MAX


async def test_files_tab_lists_media_documents_and_links_and_opens_the_message(who: Who) -> None:
    truonga, sale1 = await who("truonga"), await who("sale1")
    subj = await _room(truonga, sale1)
    img = await _file(truonga, subj, "mat-bang.png", PNG)
    doc = await _file(sale1, subj, "Báo giá tháng 11.pdf", PDF, body="Báo giá gửi khách")
    post = await ok(truonga, "talk.post", **subj, body="Hồ sơ ở https://tai-lieu.example.vn/hs?id=7, xem giúp.")
    t = await _thread(sale1, subj)
    assert t["files"] == {"title": "Tệp", "query": "talk.files", "input": {"thread_id": t["thread_id"]}}
    media = await _files(sale1, t["thread_id"], "media")
    assert media["tabs"]["current"] == "media" and [x["key"] for x in media["tabs"]["items"]] == ["media", "file", "link"]
    assert [x["title"] for x in media["tabs"]["items"]] == ["Ảnh (1)", "Tài liệu (1)", "Link (1)"]    # phòng không gắn đội ⇒ không có tab đội
    assert [(i["title"], i["badge"]) for i in _items(media)] == [("mat-bang.png", "Ảnh")]
    assert _items(media)[0]["file"]["is_image"] is True                                    # ảnh thu nhỏ: app tải qua đường tệp sẵn có
    assert (await executor.query(sale1, "talk.file", {"attachment_id": _items(media)[0]["file"]["id"]})).status == "ok"
    assert _items(media)[0]["open"] == {"query": "talk.thread", "input": {**subj, "around": img}}
    files = _items(await _files(sale1, t["thread_id"], "file"))
    assert [(i["title"], i["badge"]) for i in files] == [("Báo giá tháng 11.pdf", "PDF")] and "Báo giá gửi khách" in files[0]["subtitle"]
    assert files[0]["open"]["input"]["around"] == doc
    links = _items(await _files(sale1, t["thread_id"], "link"))
    assert [(i["title"], i["badge"]) for i in links] == [("https://tai-lieu.example.vn/hs?id=7", "tai-lieu.example.vn")]
    assert links[0]["open"]["input"]["around"] == post["message_id"]
    opened = await _thread(sale1, subj, around=doc)                                      # mở đúng tin
    assert opened["focus_id"] == doc and any(m["id"] == doc for m in opened["messages"])
    # sửa tin ⇒ bộ link của tin thay theo chữ mới
    await ok(truonga, "talk.edit_message", message_id=post["message_id"], body="Đổi sang https://moi.example.vn/hs")
    assert [i["badge"] for i in _items(await _files(sale1, t["thread_id"], "link"))] == ["moi.example.vn"]


async def test_files_tab_follows_the_thread_rights_recalls_and_removals(who: Who) -> None:
    truonga, sale1, sale2, sale3 = await who("truonga"), await who("sale1"), await who("sale2"), await who("sale3")
    subj = await _room(truonga, sale1, sale2)
    mid = await _file(truonga, subj, "hop-dong.pdf", PDF)
    post = await ok(truonga, "talk.post", **subj, body="Link nội bộ https://noi-bo.example.vn/x")
    tid = (await _thread(sale1, subj))["thread_id"]
    # người ngoài phòng biết mã luồng vẫn không thấy gì
    assert (await executor.query(sale3, "talk.files", {"thread_id": tid, "kind": "file"})).status == "not_found"
    # tin thu hồi: tệp + link biến khỏi tab, với cả người gửi (xem lại tin thu hồi đi qua lịch sử của tin)
    await ok(truonga, "talk.recall_message", message_id=mid)
    await ok(truonga, "talk.recall_message", message_id=post["message_id"])
    for p in (sale1, truonga):
        assert _items(await _files(p, tid, "file")) == [] and _items(await _files(p, tid, "link")) == []
    await _file(truonga, subj, "lich.png", PNG)
    assert len(_items(await _files(sale2, tid, "media"))) == 1
    # bị mời khỏi phòng ⇒ mất cả danh sách ngay
    await ok(truonga, "talk.remove_member", room_id=subj["subject_id"], person=sale2.email)
    assert (await executor.query(sale2, "talk.files", {"thread_id": tid, "kind": "media"})).status == "not_found"


async def test_files_tab_on_a_record_needs_the_record(who: Who) -> None:
    from tests.conftest import fresh_phone

    sale1, sale3 = await who("sale1"), await who("sale3")
    lead = await ok(sale1, "crm.create_lead", name=f"Khách tệp {uuid.uuid4().hex[:4]}", phone=fresh_phone(), offering_code="HSR")
    subj = {"subject_kind": "crm.lead", "subject_id": str(lead["lead_id"])}
    await _file(sale1, subj, "cmnd.png", PNG)
    tid = (await _thread(sale1, subj))["thread_id"]
    assert len(_items(await _files(sale1, tid, "media"))) == 1
    assert (await executor.query(sale3, "talk.files", {"thread_id": tid, "kind": "media"})).status == "not_found"   # khách của đội khác


# ── mục 2: tìm tệp ──

async def _found(p: Any, text: str) -> list[dict[str, Any]]:
    """Nhóm "Tệp trong trao đổi" của ⌘K (core.find gọi nguồn bằng quyền người tìm)."""
    return [i for b in (await q(p, "core.find", q=text))["blocks"] if b["type"] == "list" and b["title"] == "Tệp trong trao đổi" for i in b["items"]]


async def test_search_files_is_accent_insensitive_and_only_in_readable_threads(who: Who) -> None:
    truonga, sale1, sale3 = await who("truonga"), await who("sale1"), await who("sale3")
    tag = uuid.uuid4().hex[:5]
    subj = await _room(truonga, sale1)
    mid = await _file(truonga, subj, f"Báo giá Đà Lạt {tag}.pdf", PDF)
    await _file(truonga, subj, "anh.png", PNG, body=f"Mặt bằng tầng {tag} đã chốt")
    hits = await _found(sale1, f"bao gia da lat {tag}")
    assert [h["title"] for h in hits] == [f"Báo giá Đà Lạt {tag}.pdf"] and hits[0]["open"]["input"] == {**subj, "around": mid}
    assert [h["title"] for h in await _found(sale1, f"mat bang tang {tag}")] == ["anh.png"]   # khớp chú thích
    assert await _found(sale3, tag) == []                                                  # không ở trong phòng
    await ok(truonga, "talk.recall_message", message_id=mid)
    assert await _found(truonga, f"bao gia {tag}") == []                                   # thu hồi ⇒ không tìm thấy
    # trợ lý tìm bằng quyền người hỏi qua ⌘K: thẻ dẫn tới tin, không có nội dung tệp
    found = await executor.query(sale1.as_agent(), "core.find", {"q": f"mat bang tang {tag}"})
    assert found.status == "ok", found.error
    group = next(b for b in found.output["blocks"] if b.get("title") == "Tệp trong trao đổi")
    assert [i["title"] for i in group["items"]] == ["anh.png"] and set(group["items"][0]) <= {"title", "subtitle", "badge", "tone", "open"}


# ── mục 3: kệ của đội ──

async def _team(admin: Any, name: str) -> tuple[str, str]:
    code = f"T{uuid.uuid4().hex[:6].upper()}"
    out = await ok(admin, "admin.upsert_team", code=code, name=name, kind="sales", parent_code="HQ")
    await events.drain()
    return code, str(out["team_id"])


async def _shelves(team_id: str) -> list[tuple[str, str]]:
    rows = await owner_val("""SELECT coalesce(jsonb_agg(jsonb_build_array(key, name) ORDER BY key), '[]') FROM kb.shelves
                              WHERE %s::uuid = ANY(team_ids)""", (team_id,))
    return [tuple(r) for r in rows]


async def test_a_new_team_gets_exactly_one_shelf_that_follows_its_name(who: Who) -> None:
    admin = await who("admin")
    code, tid = await _team(admin, "Kinh doanh Đông")
    key = "team_" + re.sub(r"[^a-z0-9]+", "_", code.lower())
    assert await _shelves(tid) == [(key, "Tài liệu Kinh doanh Đông")]
    await ok(admin, "admin.upsert_team", code=code, name="Kinh doanh Đông", kind="sales", parent_code="HQ")   # lưu lại / sự kiện chạy lại
    await events.drain()
    assert await _shelves(tid) == [(key, "Tài liệu Kinh doanh Đông")]
    await ok(admin, "admin.upsert_team", code=code, name="Kinh doanh Đông Bắc", kind="sales", parent_code="HQ")
    await events.drain()
    assert await _shelves(tid) == [(key, "Tài liệu Kinh doanh Đông Bắc")]
    # quản trị đặt tên khác cho kệ ⇒ đổi tên đội không ghi đè
    await ok(admin, "kb.save_shelf", key=key, name="Hồ sơ đội ĐB", teams=[code])
    await ok(admin, "admin.upsert_team", code=code, name="Kinh doanh Miền Đông", kind="sales", parent_code="HQ")
    await events.drain()
    assert await _shelves(tid) == [(key, "Hồ sơ đội ĐB")]


async def test_team_shelf_reads_follow_team_membership_at_once(who: Who) -> None:
    admin = await who("admin")
    sale = await fresh_sale(who)
    code, tid = await _team(admin, "Đội đọc kệ")
    key = (await _shelves(tid))[0][0]
    await approved(admin, "admin.add_team", email=admin.email, team_code=code)
    doc = await ok(await who("admin"), "kb.add_document", title=f"Quy trình đội {code}", shelf=key, kind="process", body="Bước 1. Gọi khách.")
    ref = {"document_id": str(doc["document_id"])}
    assert (await executor.query(sale, "kb.document", ref)).status in ("not_found", "denied")
    await approved(admin, "admin.add_team", email=sale.email, team_code=code)
    sale = await who(sale.email.split("@")[0])
    assert (await executor.query(sale, "kb.document", ref)).status == "ok"
    await approved(admin, "admin.remove_team", email=sale.email, team_code=code)
    sale = await who(sale.email.split("@")[0])
    assert (await executor.query(sale, "kb.document", ref)).status in ("not_found", "denied")


async def test_team_shelves_off_means_no_shelf_and_the_setting_is_company_data(who: Who) -> None:
    admin, sale1 = await who("admin"), await who("sale1")
    page = await q(admin, "admin.settings")
    row = next(r for r in page["rows"] if r["id"] == "kb.team_shelves")
    assert row["enabled"] is True and row["set"] == "Công ty đặt"                         # cài theo gói khi tạo công ty
    assert next(r for r in page["rows"] if r["id"] == "talk.team_rooms")["enabled"] is False
    assert (await executor.run(sale1, "admin.set_setting", {"key": "kb.team_shelves", "enabled": False})).status == "denied"
    await ok(admin, "admin.set_setting", key="kb.team_shelves", enabled=False)
    try:
        _, tid = await _team(admin, "Đội không kệ")
        assert await _shelves(tid) == []
    finally:
        await ok(admin, "admin.set_setting", key="kb.team_shelves", enabled=True)
    assert (await executor.run(admin, "admin.set_setting", {"key": "kb.khong_co", "enabled": True})).status == "not_found"


# ── mục 5: phòng của đội + "Tài liệu của đội" ──

async def test_team_rooms_follow_the_team_and_show_the_team_shelf(who: Who) -> None:
    admin = await who("admin")
    a, b, guest = await fresh_sale(who), await fresh_sale(who), await fresh_sale(who)
    await ok(admin, "admin.set_setting", key="talk.team_rooms", enabled=True)
    try:
        code, tid = await _team(admin, "Đội có phòng")
        assert await owner_val("SELECT count(*) FROM talk.rooms WHERE team_id = %s", (tid,)) == 0   # đội chưa có ai ⇒ chưa có phòng
        for p in (a, b):
            await approved(admin, "admin.add_team", email=p.email, team_code=code)
        await events.drain()
        rid = str(await owner_val("SELECT id FROM talk.rooms WHERE team_id = %s", (tid,)))
        subj = {"subject_kind": "talk.room", "subject_id": rid}
        assert any(i["subject_id"] == rid for i in (await q(a, "talk.inbox"))["items"])     # phòng hiện trong hộp tin của thành viên
        await ok(a, "talk.add_members", room_id=rid, members=[guest.email])                   # khách mời thêm tay
        await approved(admin, "admin.remove_team", email=b.email, team_code=code)
        await ok(admin, "admin.upsert_team", code=code, name="Đội đổi tên", kind="sales", parent_code="HQ")
        await events.drain()
        assert (await executor.query(b, "talk.thread", subj)).status == "not_found"          # ra khỏi đội ⇒ ra khỏi phòng
        assert (await _thread(guest, subj))["subject_title"] == "Đội đổi tên"                # người thêm tay giữ nguyên; phòng đổi tên theo đội
        # "Tài liệu của đội": tài liệu trên kệ đội, bằng quyền người xem
        key = (await _shelves(tid))[0][0]
        await approved(admin, "admin.add_team", email=admin.email, team_code=code)
        await ok(admin, "kb.add_document", title=f"Bảng giá đội {code}", shelf=key, kind="policy", body="Giá niêm yết.")
        await events.drain()
        th = (await _thread(a, subj))["thread_id"]
        page = await _files(a, th, "team")
        assert page["tabs"]["current"] == "team" and page["tabs"]["items"][-1] == {"key": "team", "title": "Tài liệu của đội (1)"}
        team_block = next(bl for bl in page["blocks"] if bl["type"] == "list")
        assert team_block["title"].startswith("Tài liệu của đội")
        assert [i["title"] for i in team_block["items"]] == [f"Bảng giá đội {code}"] and team_block["items"][0]["open"]["query"] == "kb.document"
        guest_page = await _files(guest, th, "team")                                           # trong phòng nhưng không thuộc đội ⇒ không đọc được kệ
        assert not any(i["title"] == f"Bảng giá đội {code}" for i in _items(guest_page))
        assert [x["key"] for x in (await _files(a, th, "media"))["tabs"]["items"]] == ["media", "file", "link", "team"]
    finally:
        await ok(admin, "admin.set_setting", key="talk.team_rooms", enabled=False)


async def _room_of(tid: str) -> str:
    return str(await owner_val("SELECT id FROM talk.rooms WHERE team_id = %s", (tid,)))


async def _owner(rid: str, p: Any) -> bool | None:
    return await owner_val("SELECT is_owner FROM talk.room_members WHERE room_id = %s AND user_id = %s", (rid, p.user_id))


async def test_team_room_owners_follow_the_team_leads_and_reactivated_people_come_back(who: Who) -> None:
    admin = await who("admin")
    a, b = await fresh_sale(who), await fresh_sale(who)
    await ok(admin, "admin.set_setting", key="talk.team_rooms", enabled=True)
    try:
        code, tid = await _team(admin, "Đội đổi trưởng")
        await approved(admin, "admin.add_team", email=a.email, team_code=code, is_lead=True)
        await approved(admin, "admin.add_team", email=b.email, team_code=code)
        await events.drain()
        rid = await _room_of(tid)
        assert (await _owner(rid, a), await _owner(rid, b)) == (True, False)
        await approved(admin, "admin.add_team", email=a.email, team_code=code, is_lead=False)        # hạ trưởng
        await approved(admin, "admin.add_team", email=b.email, team_code=code, is_lead=True)         # trưởng mới
        await events.drain()
        assert (await _owner(rid, a), await _owner(rid, b)) == (False, True)
        subj = {"subject_kind": "talk.room", "subject_id": rid}
        await approved(admin, "admin.update_member", email=b.email, active=False)                    # nghỉ việc ⇒ ra khỏi phòng
        await events.drain()
        assert await _owner(rid, b) is None
        await approved(admin, "admin.update_member", email=b.email, active=True)                     # đi làm lại ⇒ vào lại phòng, không phải chờ đổi đội
        await events.drain()
        assert await _owner(rid, b) is True
        assert (await _thread(await who(b.email.split("@")[0]), subj))["subject_title"] == "Đội đổi trưởng"
    finally:
        await ok(admin, "admin.set_setting", key="talk.team_rooms", enabled=False)


async def test_turning_team_rooms_off_still_removes_people_who_leave_the_team(who: Who) -> None:
    admin = await who("admin")
    a, b, c = await fresh_sale(who), await fresh_sale(who), await fresh_sale(who)
    await ok(admin, "admin.set_setting", key="talk.team_rooms", enabled=True)
    code, tid = await _team(admin, "Đội tắt phòng")
    for p in (a, b):
        await approved(admin, "admin.add_team", email=p.email, team_code=code)
    await events.drain()
    rid = await _room_of(tid)
    subj = {"subject_kind": "talk.room", "subject_id": rid}
    await ok(admin, "admin.set_setting", key="talk.team_rooms", enabled=False)
    await approved(admin, "admin.remove_team", email=b.email, team_code=code)
    await approved(admin, "admin.add_team", email=c.email, team_code=code)
    await ok(admin, "admin.upsert_team", code=code, name="Đội tắt phòng mới", kind="sales", parent_code="HQ")
    await events.drain()
    assert (await executor.query(b, "talk.thread", subj)).status == "not_found"              # rời đội ⇒ mất phòng, kể cả khi công tắc đã tắt
    assert (await executor.query(c, "talk.thread", subj)).status == "not_found"              # tắt ⇒ không thêm người mới
    assert (await _thread(a, subj))["subject_title"] == "Đội tắt phòng"                       # tắt ⇒ không đổi tên; người còn trong đội giữ phòng


async def test_team_rooms_off_by_default(who: Who) -> None:
    admin = await who("admin")
    sale = await fresh_sale(who)
    code, tid = await _team(admin, "Đội không phòng")
    await approved(admin, "admin.add_team", email=sale.email, team_code=code)
    await events.drain()
    assert await owner_val("SELECT count(*) FROM talk.rooms WHERE team_id = %s", (tid,)) == 0


def test_talk_never_names_the_document_store() -> None:
    """Trao đổi chỉ hỏi điểm cắm talk.thread_files — không biết gói nào góp (0110)."""
    src = pathlib.Path(talk.__file__).read_text()
    assert not re.search(r"\bkb\b", src)
