"""Trao đổi (quyết định 0046): quyền đọc = quyền thấy đối tượng (kiểm ở DB lúc truy cập), @nhắc không cấp quyền và chỉ nhắc được
người vốn thấy, đổi người phụ trách là người cũ mất quyền ngay, thẻ chia sẻ chỉ là tham chiếu (người mở chạy lại bằng quyền mình),
gửi lại không thành hai tin, trợ lý chỉ soạn, tắt gói thì thẻ hồ sơ vẫn mở bình thường."""

import asyncio
import uuid
from typing import Any

from barista.db import db
from barista.kernel import executor, live
from tests.conftest import OWNER_URL, Who, fresh_phone, ok, owner_val, q
from tests.test_journey import _blocks


async def _lead(sale: Any, name: str = "") -> str:
    out = await ok(sale, "crm.create_lead", name=name or f"Khách trao đổi {uuid.uuid4().hex[:4]}", phone=fresh_phone(), offering_code="HSR")
    return str(out["lead_id"])


def _subj(lead_id: str) -> dict[str, str]:
    return {"subject_kind": "crm.lead", "subject_id": lead_id}


def _thread(page: dict[str, Any]) -> dict[str, Any]:
    return _blocks(page, "thread")[0]


async def test_who_can_read_follows_who_can_see_the_lead(who: Who) -> None:
    sale1, sale2, truonga, ketoan = await who("sale1"), await who("sale2"), await who("truonga"), await who("ketoan")
    lead = await _lead(sale1)
    await ok(sale1, "talk.post", **_subj(lead), body="Khách hẹn xem nhà mẫu chiều thứ Bảy")
    assert [m["body"] for m in _thread(await q(truonga, "talk.thread", **_subj(lead)))["messages"]] == ["Khách hẹn xem nhà mẫu chiều thứ Bảy"]
    for outsider in (sale2, ketoan):                                                # cùng đội nhưng không phải khách của mình / không có quyền khách
        assert (await executor.query(outsider, "talk.thread", _subj(lead))).status == "not_found"
        r = await executor.run(outsider, "talk.post", {**_subj(lead), "body": "chen vào"})
        assert r.status == "not_found"
    page = await q(sale1, "crm.journey", lead_id=lead)
    assert _thread(page)["messages"][0]["mine"] is True                             # khối "Trao đổi" nằm ngay trên hành trình khách
    assert await owner_val("SELECT count(*) FROM talk.messages m JOIN talk.threads t ON t.id = m.thread_id WHERE t.subject_id = %s",
                           (lead,)) == 1


async def test_mentions_never_grant_access_and_notify_only_people_who_can_see(who: Who) -> None:
    sale1, sale2, truonga = await who("sale1"), await who("sale2"), await who("truonga")
    lead = await _lead(sale1, "Khách nhắc tên")
    r = await executor.run(sale1, "talk.post", {**_subj(lead), "body": "@Bích xem giúp", "mentions": [sale2.email]})
    assert r.status == "invalid" and "không xem được" in r.error                   # nhắc người không thấy khách ⇒ từ chối, không lộ
    assert (await executor.query(sale2, "talk.thread", _subj(lead))).status == "not_found"
    await ok(sale1, "talk.post", **_subj(lead), body="Anh Nam duyệt giúp em mức chiết khấu", mentions=[truonga.email])
    inbox = (await q(truonga, "core.notifications", limit=50))["items"]
    n = next(x for x in inbox if x["kind"] == "mention" and "Khách nhắc tên" in x["title"])
    assert n["open"] == {"query": "talk.thread", "input": _subj(lead)}
    assert "chiết khấu" not in await owner_val("SELECT push FROM core.notifications WHERE id = %s", (n["id"],))   # màn khoá không có nội dung
    mine = await q(truonga, "talk.mine")
    assert {k["label"]: k["value"] for k in _blocks(mine, "kpis")[0]["items"]}["Nhắc tên bạn"] >= 1


async def test_reassigning_the_lead_revokes_the_old_owner_immediately(who: Who) -> None:
    sale1, sale2, truonga = await who("sale1"), await who("sale2"), await who("truonga")
    lead = await _lead(sale1, "Khách chuyển người")
    await ok(sale1, "talk.post", **_subj(lead), body="Ghi chú nội bộ về khách")
    await ok(truonga, "crm.assign", lead_id=lead, owner_email=sale2.email)
    assert (await executor.query(sale1, "talk.thread", _subj(lead))).status == "not_found"   # không chờ đồng bộ gì
    async with db.tenant_tx(sale1, read_only=True) as conn:                         # cả khi đọc thẳng bảng: chính sách DB chặn
        cur = await conn.execute("SELECT count(*) AS n FROM talk.messages m JOIN talk.threads t ON t.id = m.thread_id WHERE t.subject_id = %s",
                                 (lead,))
        row = await cur.fetchone()
        assert row is not None and row["n"] == 0
    assert not any("Khách chuyển người" in i["title"] for b in _blocks(await q(sale1, "talk.mine"), "list") for i in b["items"])
    assert _thread(await q(sale2, "talk.thread", **_subj(lead)))["messages"][0]["body"] == "Ghi chú nội bộ về khách"


async def test_resending_with_the_same_key_posts_once_and_the_assistant_only_drafts(who: Who) -> None:
    sale1 = await who("sale1")
    lead = await _lead(sale1)
    for _ in range(2):
        r = await executor.run(sale1, "talk.post", {**_subj(lead), "body": "Gửi một lần thôi"}, key="talk-key-1")
        assert r.ok
    assert await owner_val("SELECT count(*) FROM talk.messages WHERE body = 'Gửi một lần thôi'") == 1
    p = await executor.run(sale1.as_agent(), "talk.post", {**_subj(lead), "body": "Trợ lý soạn"})
    assert p.status == "proposed" and await owner_val("SELECT count(*) FROM talk.messages WHERE body = 'Trợ lý soạn'") == 0
    assert (await executor.query(sale1.as_agent(), "talk.thread", _subj(lead))).ok     # trợ lý ĐỌC được trao đổi người đó thấy


async def test_unread_and_mark_read(who: Who) -> None:
    sale1, truonga = await who("sale1"), await who("truonga")
    lead = await _lead(sale1, "Khách đọc tin")
    await ok(sale1, "talk.post", **_subj(lead), body="Tin một", mentions=[truonga.email])
    await ok(sale1, "talk.post", **_subj(lead), body="Tin hai")
    t = _thread(await q(truonga, "talk.thread", **_subj(lead)))
    row = lambda page: next(i for i in _blocks(page, "list")[0]["items"] if "Khách đọc tin" in i["title"])  # noqa: E731
    assert "2 chưa đọc" in row(await q(truonga, "talk.mine"))["badge"]
    await ok(truonga, "talk.mark_read", thread_id=t["thread_id"], upto=t["messages"][-1]["id"])
    assert "chưa đọc" not in row(await q(truonga, "talk.mine"))["badge"]
    r = await executor.run(await who("sale3"), "talk.mark_read", {"thread_id": t["thread_id"], "upto": 1})
    assert r.status == "not_found"                                                  # không thấy khách ⇒ không thấy luồng


async def test_new_messages_signal_only_the_thread_id(who: Who) -> None:
    sale1 = await who("sale1")
    lead = await _lead(sale1)
    await ok(sale1, "talk.post", **_subj(lead), body="Mở luồng")
    tid = await owner_val("SELECT id FROM talk.threads WHERE subject_id = %s", (lead,))
    task = asyncio.create_task(live.listen_forever(OWNER_URL))
    try:
        await asyncio.sleep(0.5)
        with live.hub.subscribe_key(f"thread:{tid}") as sig:
            await ok(sale1, "talk.post", **_subj(lead), body="Tin mới")
            await asyncio.wait_for(sig.get(), 5)
    finally:
        task.cancel()


async def test_inbox_lists_my_threads_newest_first_with_unread_and_mentions(who: Who) -> None:
    """Cột "Tin nhắn" của app: dòng luồng tôi theo dõi / được nhắc, tin cuối + số chưa đọc; đọc xong là hết; người không thấy khách không có dòng."""
    sale1, sale3, truonga = await who("sale1"), await who("sale3"), await who("truonga")
    lead = await _lead(sale1, "Khách hộp tin")
    await ok(sale1, "talk.post", **_subj(lead), body="Anh xem giúp em hồ sơ vay", mentions=[truonga.email])
    await ok(sale1, "talk.post", **_subj(lead), body="Khách gửi thêm sao kê rồi ạ")
    box = await q(truonga, "talk.inbox")
    row = box["items"][0]                                                            # vừa nhắn ⇒ đứng đầu
    assert row["title"].startswith("Khách hộp tin") and row["kind_label"]
    assert (row["unread"], row["mentioned"], row["last_body"], row["last_mine"]) == (2, 1, "Khách gửi thêm sao kê rồi ạ", False)
    assert row["open"] == {"query": "talk.thread", "input": _subj(lead)} and box["unread"] >= 1 and box["mentioned"] >= 1
    t = _thread(await q(truonga, "talk.thread", **_subj(lead)))
    await ok(truonga, "talk.mark_read", thread_id=t["thread_id"], upto=t["messages"][-1]["id"])
    row = next(i for i in (await q(truonga, "talk.inbox"))["items"] if i["thread_id"] == t["thread_id"])
    assert (row["unread"], row["mentioned"]) == (0, 0)
    assert not any(i["thread_id"] == t["thread_id"] for i in (await q(sale3, "talk.inbox"))["items"])   # không thấy khách ⇒ không có dòng
    assert (await executor.query(truonga.as_agent(), "talk.inbox", {})).status == "denied"            # trợ lý dùng talk.mine


# ── phòng nhắn tin nhóm (0048) ──

async def test_rooms_read_by_members_only_and_leaving_revokes_at_once(who: Who) -> None:
    truonga, sale1, sale2, sale3 = await who("truonga"), await who("sale1"), await who("sale2"), await who("sale3")
    out = await ok(truonga, "talk.create_room", name=f"Phòng thử {uuid.uuid4().hex[:4]}", purpose="Chia việc", members=[sale1.email, sale2.email])
    room = {"subject_kind": "talk.room", "subject_id": str(out["room_id"])}
    await ok(sale1, "talk.post", **room, body="Em nhận khách Lan ạ", mentions=[truonga.email])
    assert [m["body"] for m in _thread(await q(sale2, "talk.thread", **room))["messages"]] == ["Em nhận khách Lan ạ"]
    assert (await executor.query(sale3, "talk.thread", room)).status == "not_found"            # ngoài phòng ⇒ không đọc được
    r = await executor.run(sale1, "talk.post", {**room, "body": "@Khánh xem giúp", "mentions": [sale3.email]})
    assert r.status == "invalid"                                                               # nhắc người ngoài phòng ⇒ từ chối
    people = {p["value"] for p in _thread(await q(sale1, "talk.thread", **room))["people"]}
    assert people == {truonga.email, sale2.email}                                              # chỉ gợi ý người trong phòng
    row = next(i for i in (await q(truonga, "talk.inbox"))["items"] if i["subject_id"] == room["subject_id"])
    assert (row["kind_label"], row["unread"], row["mentioned"]) == ("Phòng", 1, 1)
    info = await q(sale2, "talk.room", room_id=room["subject_id"])
    assert {i["title"] for i in _blocks(info, "list")[0]["items"]} >= {"Trần Bảo Nam", "Hoàng Thị Bích (bạn)"}
    await ok(sale2, "talk.leave_room", room_id=room["subject_id"])
    assert (await executor.query(sale2, "talk.thread", room)).status == "not_found"            # rời là hết đọc ngay
    assert not any(i["subject_id"] == room["subject_id"] for i in (await q(sale2, "talk.inbox"))["items"])


async def test_new_room_shows_up_for_added_people_before_any_message(who: Who) -> None:
    truonga, sale1, sale3 = await who("truonga"), await who("sale1"), await who("sale3")
    out = await ok(truonga, "talk.create_room", name="Phòng mới tinh", members=[sale1.email])
    rid = str(out["room_id"])
    assert any(i["subject_id"] == rid for i in (await q(sale1, "talk.inbox"))["items"])        # vừa được thêm ⇒ thấy phòng dù chưa có tin
    n = next(x for x in (await q(sale1, "core.notifications", limit=20))["items"] if x["kind"] == "room")
    assert n["open"] == {"query": "talk.thread", "input": {"subject_kind": "talk.room", "subject_id": rid}}
    assert (await executor.run(sale3, "talk.add_members", {"room_id": rid, "members": [sale3.email]})).status == "not_found"   # người ngoài không tự chen vào
    await ok(sale1, "talk.add_members", room_id=rid, members=[sale3.email])                    # người trong phòng thêm được
    assert (await executor.query(sale3, "talk.thread", {"subject_kind": "talk.room", "subject_id": rid})).ok


async def test_the_same_card_shared_many_times_is_computed_once_per_thread_open(who: Who, monkeypatch: Any) -> None:
    """Thẻ trùng (cùng truy vấn + tham số) chỉ chạy lại MỘT lần mỗi lần mở luồng — chia sẻ một thẻ nặng nhiều lần không nhân tải."""
    from barista.modules import talk

    sale3, truongb = await who("sale3"), await who("truongb")
    room = await ok(sale3, "talk.create_room", name=f"Trùng thẻ {uuid.uuid4().hex[:4]}", members=[truongb.email])
    subj = {"subject_kind": "talk.room", "subject_id": str(room["room_id"])}
    card = {"query": "crm.leads", "input": {}, "title": "Khách"}
    for i in range(4):
        await ok(sale3, "talk.post", **subj, body=f"lần {i}", card=card)
    calls = 0
    real = talk._live

    async def counting(ctx: Any, c: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return await real(ctx, c)

    monkeypatch.setattr(talk, "_live", counting)
    thread = _blocks(await q(truongb, "talk.thread", **subj), "thread")[0]
    assert calls == 1 and len([m for m in thread["messages"] if m["card"] and "live" in m["card"]]) == 4
