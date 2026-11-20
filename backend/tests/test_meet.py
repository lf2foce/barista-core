"""Gọi video (0114): ai gọi / tham gia được = ai thấy đối tượng (kiểm ở DB), gói tắt thì phần còn lại không đổi, nhật ký tham dự chỉ thêm và ghi nguồn
"bấm nút", số phút không đoán, kết thúc phát sự kiện có số liệu, trợ lý không bấm thay và không tự xác nhận người tham dự."""

import asyncio
import uuid
from typing import Any

import psycopg
import pytest

from barista import seed
from barista.db import db
from barista.kernel import executor
from barista.kernel.principal import Principal, system_principal
from barista.modules.meet import tally
from barista.workspaces import manifest
from tests.conftest import fresh_phone, ok, owner_val, q

PEOPLE = [("admin", "Quản trị thử", "admin", "QT", True), ("truong", "Trưởng nhóm thử", "team_lead", "KD-A", True),
          ("sale1", "Tư vấn một", "sale", "KD-A", False), ("sale2", "Tư vấn hai", "sale", "KD-A", False),
          ("ketoan", "Kế toán thử", "accountant", "QT", False)]
LINK = "https://meet.google.com/abc-defg-hij"


@pytest.fixture(scope="module")
async def co() -> tuple[Any, str]:
    d = f"m{uuid.uuid4().hex[:6]}.demo"
    return await seed.create_tenant(f"Công ty gọi {d}", d, people=PEOPLE, packs=("crm", "talk", "meet")), d


async def _people(co: tuple[Any, str]) -> dict[str, Principal]:
    tid, d = co
    return {k: await seed.who(tid, k, d) for k, *_ in PEOPLE}


async def _lead(sale: Principal) -> dict[str, str]:
    out = await ok(sale, "crm.create_lead", name=f"Khách gọi {uuid.uuid4().hex[:4]}", phone=fresh_phone())
    return {"subject_kind": "crm.lead", "subject_id": str(out["lead_id"])}


async def _rows(p: Principal, sql: Any, params: Any = None) -> list[dict[str, Any]]:
    async with db.tenant_tx(p, read_only=True) as conn:
        cur = await conn.execute(sql, params)
        return await cur.fetchall()


def _thread(page: dict[str, Any]) -> dict[str, Any]:
    return next(b for b in page["blocks"] if b["type"] == "thread")


async def _ended_event(call_id: Any) -> dict[str, Any]:
    return await owner_val("SELECT payload FROM core.events WHERE type = 'meet.call_ended' AND payload->>'call_id' = %s", (str(call_id),))


async def test_who_can_start_join_or_see_a_call_follows_who_can_see_the_subject(co: tuple[Any, str]) -> None:
    p = await _people(co)
    subj = await _lead(p["sale1"])
    for outsider in (p["sale2"], p["ketoan"]):                                       # cùng đội nhưng không phải khách của mình / không có quyền khách
        assert (await executor.run(outsider, "meet.start", {**subj, "join_url": LINK})).status in ("not_found", "denied")
    started = await ok(p["sale1"], "meet.start", **subj, join_url=LINK)
    cid = str(started["call_id"])
    assert started["once_open_url"] == LINK and started["already"] is False
    for outsider in (p["sale2"], p["ketoan"]):
        assert (await executor.run(outsider, "meet.join", {"call_id": cid})).status in ("not_found", "denied")
        assert (await executor.query(outsider, "meet.call", {"call_id": cid})).status in ("not_found", "denied")
    assert (await _rows(p["sale2"], "SELECT count(*) AS n FROM meet.calls WHERE id = %s", (cid,)))[0]["n"] == 0   # đọc thẳng bảng: DB chặn
    assert (await ok(p["truong"], "meet.join", call_id=cid))["once_open_url"] == LINK                         # trưởng nhóm thấy khách của đội
    # đổi người phụ trách ⇒ người cũ mất cuộc gọi NGAY, không có bản sao quyền nào để đồng bộ
    await ok(p["truong"], "crm.assign", lead_id=subj["subject_id"], owner_email=p["sale2"].email)
    assert (await executor.query(p["sale1"], "meet.call", {"call_id": cid})).status == "not_found"
    assert (await _rows(p["sale1"], "SELECT count(*) AS n FROM meet.participants WHERE call_id = %s", (cid,)))[0]["n"] == 0
    assert (await q(p["sale2"], "meet.call", call_id=cid))["title"]
    assert await owner_val("SELECT count(*) FROM meet.calls WHERE id = %s", (cid,)) == 1


async def test_a_room_call_uses_the_company_room_link_and_shows_as_a_live_card(co: tuple[Any, str]) -> None:
    p = await _people(co)
    assert (await executor.run(p["sale1"], "meet.configure", {"room_url": LINK})).status == "denied"        # cấu hình là việc quản trị
    await ok(p["admin"], "meet.configure", room_url="https://zoom.us/j/1234567890?pwd=khongthat")
    room = await ok(p["sale1"], "talk.create_room", name=f"Phòng gọi {uuid.uuid4().hex[:4]}", members=[p["truong"].email])
    subj = {"subject_kind": "talk.room", "subject_id": str(room["room_id"])}
    block = _thread(await q(p["truong"], "talk.thread", **subj))
    assert [(a["command"], a["title"]) for a in block["actions"]] == [                                    # phòng hai người ⇒ gọi thẳng; có link phòng ⇒ thêm nút link
        ("meet.ring", "Gọi"), ("meet.ring", "Gọi video"), ("meet.ring", "Gọi bằng link Meet/Zoom của công ty")]
    started = await ok(p["truong"], "meet.start", **subj)
    assert started["once_open_url"].startswith("https://zoom.us/j/")                                     # không dán ⇒ link phòng công ty
    cid = str(started["call_id"])
    msgs = _thread(await q(p["sale1"], "talk.thread", **subj))
    card = msgs["messages"][-1]["card"]
    assert card["query"] == "meet.call" and "pwd" not in str(card) and card["live"]["subtitle"].endswith("zoom.us")   # tham chiếu; thẻ chỉ ghi tên máy
    assert card["live"]["gone"] is False and card["live"]["subtitle"].startswith("Đang diễn ra")
    assert [a["command"] for a in card["live"]["actions"]] == ["meet.join", "meet.end"]                  # chưa bấm tham gia ⇒ không có "Rời"
    assert [a["command"] for a in msgs["actions"]] == ["meet.join"]                                       # đang có cuộc gọi ⇒ nút đổi thành Tham gia
    assert (await executor.query(p["sale2"], "meet.call", {"call_id": cid})).status == "not_found"         # không ở trong phòng
    assert (await executor.run(p["sale2"], "meet.start", subj)).status == "not_found"
    await ok(p["truong"], "meet.end", call_id=cid)
    await ok(p["admin"], "meet.configure", room_url="")


async def test_turning_the_pack_off_leaves_threads_and_cards_working(co: tuple[Any, str]) -> None:
    p = await _people(co)
    subj = await _lead(p["sale1"])
    cid = str((await ok(p["sale1"], "meet.start", **subj, join_url=LINK))["call_id"])
    await ok(p["admin"], "admin.set_pack", pack="meet", enabled=False)
    try:
        page = await q(p["sale1"], "talk.thread", **subj)
        block = _thread(page)
        assert block["actions"] == [] and block["messages"][-1]["card"]["live"]["gone"] is True          # thẻ vẫn mở, chỉ ẩn phần gọi
        assert (await q(p["sale1"], "crm.journey", lead_id=subj["subject_id"]))["title"]
        assert (await executor.run(p["sale1"], "meet.start", {**subj, "join_url": LINK})).status == "not_found"
        assert (await executor.run(p["sale1"], "meet.join", {"call_id": cid})).status == "not_found"
    finally:
        await ok(p["admin"], "admin.set_pack", pack="meet", enabled=True)
    await ok(p["sale1"], "meet.end", call_id=cid)


async def test_clicks_are_an_append_only_log_and_the_end_event_never_guesses_minutes(co: tuple[Any, str]) -> None:
    p = await _people(co)
    subj = await _lead(p["sale1"])
    cid = str((await ok(p["sale1"], "meet.start", **subj, join_url=LINK))["call_id"])
    assert (await executor.run(p["truong"], "meet.leave", {"call_id": cid})).status == "conflict"         # chưa bấm tham gia thì không có lần rời
    await ok(p["truong"], "meet.join", call_id=cid)
    await ok(p["truong"], "meet.leave", call_id=cid)
    page = await q(p["sale1"], "meet.call", call_id=cid)
    assert [a["command"] for a in page["actions"]] == ["meet.join", "meet.leave", "meet.end"]
    # chỉ ghi được lần bấm của CHÍNH mình; không sửa / xoá được dòng nào (kể cả viết SQL thẳng)
    async with db.tenant_tx(p["sale1"]) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute("INSERT INTO meet.participants (call_id, user_id, event, source) VALUES (%s, %s, 'join', 'click')",
                               (cid, p["truong"].user_id))
    for sql in ("UPDATE meet.participants SET at = now() WHERE call_id = %s", "DELETE FROM meet.participants WHERE call_id = %s",
                "UPDATE meet.calls SET join_url = 'https://khac.example' WHERE id = %s"):
        async with db.tenant_tx(p["sale1"]) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await conn.execute(sql, (cid,))
    await ok(p["sale1"], "meet.end", call_id=cid)
    assert (await executor.run(p["sale1"], "meet.end", {"call_id": cid})).status == "conflict"
    assert (await executor.run(p["truong"], "meet.join", {"call_id": cid})).status == "conflict"
    ev = await _ended_event(cid)
    att = ev["attendance"]
    assert att["source"] == "click" and att["covers_outsiders"] is False and ev["end_reason"] == "person"
    by = {x["user_id"]: x for x in att["people"]}
    assert by[str(p["sale1"].user_id)]["seconds"] is None                                               # bấm Gọi mà không bấm Rời ⇒ không đo được
    assert isinstance(by[str(p["truong"].user_id)]["seconds"], int)
    assert "name" not in str(att) and "Trưởng nhóm thử" not in str(ev)                                    # sự kiện chỉ mang mã và số
    assert await owner_val("SELECT count(*) FROM meet.participants WHERE call_id = %s AND source = 'click'", (cid,)) == 3
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND kind = 'meet'", (p["sale1"].user_id,)) == 0  # người tự kết thúc không tự nhận


def test_tally_only_counts_complete_join_leave_pairs() -> None:
    from datetime import UTC, datetime, timedelta
    t0, a, b = datetime(2026, 11, 7, 7, tzinfo=UTC), uuid.uuid4(), uuid.uuid4()
    rows = [{"user_id": a, "event": "join", "at": t0}, {"user_id": a, "event": "leave", "at": t0 + timedelta(minutes=30)},
            {"user_id": a, "event": "join", "at": t0 + timedelta(minutes=40)}, {"user_id": a, "event": "leave", "at": t0 + timedelta(minutes=50)},
            {"user_id": b, "event": "join", "at": t0}, {"user_id": b, "event": "join", "at": t0 + timedelta(minutes=5)},
            {"user_id": b, "event": "leave", "at": t0 + timedelta(minutes=9)}]
    got = {x.user_id: x for x in tally(rows)}
    assert got[a].seconds == 40 * 60 and got[a].first_join_at == t0
    assert got[b].seconds is None                                                                       # hai lần vào liền nhau ⇒ thiếu một lần rời


async def test_two_people_pressing_call_together_get_one_call(co: tuple[Any, str]) -> None:
    p = await _people(co)
    subj = await _lead(p["sale1"])
    a, b = await asyncio.gather(executor.run(p["sale1"], "meet.start", {**subj, "join_url": LINK}),
                                executor.run(p["truong"], "meet.start", {**subj, "join_url": "https://zoom.us/j/999"}))
    assert a.ok and b.ok and a.output["call_id"] == b.output["call_id"]
    assert {a.output["already"], b.output["already"]} == {True, False}
    assert await owner_val("SELECT count(*) FROM meet.calls WHERE subject_id = %s", (subj["subject_id"],)) == 1
    await ok(p["sale1"], "meet.end", call_id=str(a.output["call_id"]))


async def test_links_must_be_full_https_and_a_call_needs_one(co: tuple[Any, str]) -> None:
    p = await _people(co)
    await ok(p["admin"], "meet.configure", room_url="")
    subj = await _lead(p["sale1"])
    for bad in ("http://meet.google.com/x", "javascript:alert(1)", "https://user:mk@zoom.us/j/1", "https:///x", "https://meet.google.com/a b"):
        r = await executor.run(p["sale1"], "meet.start", {**subj, "join_url": bad})
        assert r.status == "invalid", bad
    r = await executor.run(p["sale1"], "meet.start", {**subj, "provider": "external"})
    assert r.status == "invalid" and "dán link" in r.error
    assert await owner_val("SELECT count(*) FROM meet.calls WHERE subject_id = %s", (subj["subject_id"],)) == 0
    # không chọn cách gọi, không có link nào ⇒ gọi trực tiếp (0114 bổ sung 07/11), không còn báo lỗi thiếu link
    direct = await ok(p["sale1"], "meet.start", **subj)
    assert direct["call_page"] == f"/goi/{direct['call_id']}" and direct["seat"] == "a"
    await ok(p["sale1"], "meet.end", call_id=str(direct["call_id"]))
    # link có mật khẩu phòng không vào nhật ký thao tác
    await ok(p["sale1"], "meet.start", **subj, join_url="https://zoom.us/j/1?pwd=bimat")
    logged = await owner_val("SELECT input::text FROM core.audit_log WHERE command = 'meet.start' AND input->>'subject_id' = %s AND status = 'ok'",
                             (subj["subject_id"],))
    assert "bimat" not in logged and "sha256" in logged


async def test_the_assistant_drafts_but_never_joins_ends_or_confirms_by_itself(co: tuple[Any, str]) -> None:
    p = await _people(co)
    sale, bot = p["sale1"], p["sale1"].as_agent()
    subj = await _lead(sale)
    r = await executor.run(bot, "meet.start", {**subj, "join_url": LINK})
    assert r.status == "proposed" and await owner_val("SELECT count(*) FROM meet.calls WHERE subject_id = %s", (subj["subject_id"],)) == 0
    cid = str((await ok(sale, "meet.start", **subj, join_url=LINK))["call_id"])
    assert (await executor.run(bot, "meet.join", {"call_id": cid})).status == "denied"
    assert LINK not in str(await q(bot, "meet.call", call_id=cid))                                        # link (có thể kèm mật khẩu) không tới model
    assert LINK in str(await q(sale, "meet.call", call_id=cid))
    assert (await executor.run(bot, "meet.end", {"call_id": cid})).status == "proposed"
    assert (await executor.run(sale, "meet.confirm_attendance", {"call_id": cid})).status == "conflict"   # chưa kết thúc
    await ok(sale, "meet.end", call_id=cid)
    page = await q(sale, "meet.call", call_id=cid)
    confirm = next(a for a in page["actions"] if a["command"] == "meet.confirm_attendance")
    assert confirm["preset"] == {"call_id": cid}                                                       # người tự chọn ai đã dự (trường điền sẵn là cố định)
    draft = await executor.run(bot, "meet.confirm_attendance", {**confirm["preset"], "members": [sale.email], "others": "Một người ngoài"})
    assert draft.status == "proposed" and await owner_val("SELECT confirmed_at FROM meet.calls WHERE id = %s", (cid,)) is None
    await ok(sale, "meet.confirm_attendance", **confirm["preset"], members=[sale.email], others="Một người ngoài")
    assert (await executor.run(sale, "meet.confirm_attendance", {"call_id": cid})).status == "conflict"   # một lần
    ev = await owner_val("SELECT payload FROM core.events WHERE type = 'meet.attendance_confirmed' AND payload->>'call_id' = %s", (cid,))
    assert ev["user_ids"] == [str(sale.user_id)] and ev["has_others"] is True and "Một người ngoài" not in str(ev)


async def test_forgotten_calls_are_closed_by_the_sweep_and_still_report(co: tuple[Any, str]) -> None:
    tid, _ = co
    p = await _people(co)
    room = await ok(p["sale1"], "talk.create_room", name=f"Phòng quên {uuid.uuid4().hex[:4]}")
    cid = str((await ok(p["sale1"], "meet.start", subject_kind="talk.room", subject_id=str(room["room_id"]), join_url=LINK))["call_id"])
    async with db.owner.connection() as conn:
        await conn.execute("UPDATE meet.calls SET started_at = now() - interval '13 hours' WHERE id = %s", (cid,))
    out = await ok(system_principal(tid), "meet.end_stale")                                            # phòng: hàm thấy-được cần người gọi ⇒ chính sách riêng cho vòng quét
    assert out["ended"] >= 1
    assert (await _ended_event(cid))["end_reason"] == "stale"
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND kind = 'meet'", (p["sale1"].user_id,)) >= 1


async def test_every_screen_and_card_opens_for_every_role(co: tuple[Any, str]) -> None:
    """Màn danh sách / cấu hình của gói mở được cho mọi vai thấy nó (lỗi 07/11: cột kiểu "bool" không tồn tại, chỉ lộ ra khi chạy --check-contract)."""
    p = await _people(co)
    subj = await _lead(p["sale1"])
    cid = str((await ok(p["sale1"], "meet.start", **subj, join_url=LINK))["call_id"])
    await ok(p["sale1"], "meet.end", call_id=cid)
    seen = set()
    for person in p.values():
        for w in manifest(person, "x")["workspaces"]:
            for sc in w["screens"]:
                if sc["query"].startswith("meet."):
                    seen.add(sc["query"])
                    page = await q(person, sc["query"])
                    assert page["title"]
    assert seen == {"meet.calls", "meet.settings"}
    rows = (await q(p["sale1"], "meet.calls"))["rows"]
    assert next(r for r in rows if str(r["id"]) == cid)["confirmed"] == "no"
