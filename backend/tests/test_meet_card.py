"""Thẻ cuộc gọi trong luồng trao đổi (0114, ghi chú 07/11 "chữ và nút theo người xem"): chữ và nút do SERVER quyết theo người xem và trạng thái thật của cuộc gọi;
chữ lưu trong DB / tin chỉ là sự thật trung tính (tên cả hai người), không phải chữ của riêng ai."""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from barista import seed
from barista.kernel.principal import Principal
from barista.modules.meet import Facts, Side, ring_view
from tests.conftest import fresh_phone, ok, owner_val, q

PEOPLE = [("admin", "Quản trị thử", "admin", "QT", True), ("truong", "Trưởng nhóm thử", "team_lead", "KD-A", True),
          ("sale1", "Tư vấn một", "sale", "KD-A", False), ("sale2", "Tư vấn hai", "sale", "KD-A", False)]


@pytest.fixture(scope="module")
async def co() -> tuple[Any, str]:
    d = f"d{uuid.uuid4().hex[:6]}.demo"
    return await seed.create_tenant(f"Công ty thẻ gọi {d}", d, people=PEOPLE, packs=("crm", "talk", "meet")), d


async def _people(co: tuple[Any, str]) -> dict[str, Principal]:
    tid, d = co
    return {k: await seed.who(tid, k, d) for k, *_ in PEOPLE}


async def _dm_call(a: Principal, b: Principal) -> tuple[dict[str, str], str]:
    d = await ok(a, "talk.start_direct", person=b.email, body="")
    subj = {"subject_kind": "talk.direct", "subject_id": str(d["subject_id"])}
    out = await ok(a, "meet.ring", **subj, callee="", join_url="")
    return subj, str(out["call_id"])


async def _card(p: Principal, cid: str) -> tuple[str, str, list[str]]:
    page = await q(p, "meet.call", call_id=cid)
    assert page["headline"] == page["title"]                          # thẻ sống trong luồng hiện đúng tên này
    return page["title"], page["subtitle"], [a["title"] for a in page["actions"]]


async def test_a_direct_message_call_reads_differently_to_the_callee_and_the_caller_until_he_joins(co: tuple[Any, str]) -> None:
    p = await _people(co)
    a, b = p["sale1"], p["sale2"]
    subj, cid = await _dm_call(a, b)
    # sự thật lưu: trung tính, nêu cả hai người; người nhận không đọc thấy tên mình làm tên cuộc gọi
    assert await owner_val("SELECT title FROM meet.calls WHERE id = %s", (cid,)) == f"{a.name} gọi {b.name}"
    assert await owner_val("SELECT body FROM talk.messages WHERE card->'input'->>'call_id' = %s", (cid,)) == f"{a.name} gọi {b.name}"
    assert await owner_val("SELECT card->>'title' FROM talk.messages WHERE card->'input'->>'call_id' = %s", (cid,)) == f"Cuộc gọi: {a.name} gọi {b.name}"
    # người nhận, chưa vào
    assert await _card(b, cid) == (f"{a.name} gọi cho bạn", "Đang chờ bạn vào", ["Vào cuộc gọi", "Từ chối"])
    page = await q(b, "meet.call", call_id=cid)
    assert [(x["command"], x["style"]) for x in page["actions"]] == [("meet.join", "instant"), ("meet.end", "destructive")]
    # người gọi, người nhận chưa vào
    assert await _card(a, cid) == (f"Bạn đang gọi {b.name}", f"Chờ {b.name} vào", ["Vào lại", "Huỷ cuộc gọi"])
    # cùng thẻ đó trong luồng: tên theo người xem nằm ở live.headline, tên lưu giữ nguyên
    thread = next(x for x in (await q(b, "talk.thread", **subj))["blocks"] if x["type"] == "thread")
    card = thread["messages"][-1]["card"]
    assert card["title"] == f"Cuộc gọi: {a.name} gọi {b.name}" and card["live"]["headline"] == f"{a.name} gọi cho bạn"
    assert card["live"]["subtitle"] == "Đang chờ bạn vào" and [x["title"] for x in card["live"]["actions"]] == ["Vào cuộc gọi", "Từ chối"]
    # người nhận vào ⇒ cả hai đã vào: đang diễn ra, mỗi người có Rời + Kết thúc
    await ok(b, "meet.join", call_id=cid, enter=True)
    for who, other in ((a, b), (b, a)):
        head, status, buttons = await _card(who, cid)
        assert head == f"Cuộc gọi với {other.name}" and status.startswith("Đang diễn ra · 2 người · từ ") and buttons == ["Rời", "Kết thúc"]
    await ok(b, "meet.leave", call_id=cid)
    head, status, buttons = await _card(b, cid)
    assert status.startswith("Đang diễn ra · 1 người · từ ") and buttons == ["Vào cuộc gọi", "Kết thúc"]
    await ok(a, "meet.end", call_id=cid)
    assert (await _card(a, cid))[1].startswith("Đã kết thúc")


async def test_other_viewers_of_a_record_call_see_who_calls_whom_and_get_no_end_button(co: tuple[Any, str]) -> None:
    p = await _people(co)
    lead = await ok(p["sale1"], "crm.create_lead", name=f"Khách thẻ {uuid.uuid4().hex[:4]}", phone=fresh_phone())
    subj = {"subject_kind": "crm.lead", "subject_id": str(lead["lead_id"])}
    out = await ok(p["sale1"], "meet.ring", **subj, callee=p["truong"].email, join_url="")
    cid = str(out["call_id"])
    who = f"{p['sale1'].name} đang gọi {p['truong'].name}"
    assert await _card(p["admin"], cid) == (who, f"Chờ {p['truong'].name} vào", [])                    # người khác xem được hồ sơ: không có nút nào
    assert await _card(p["truong"], cid) == (f"{p['sale1'].name} gọi cho bạn", "Đang chờ bạn vào", ["Vào cuộc gọi", "Từ chối"])
    await ok(p["truong"], "meet.join", call_id=cid, enter=True)
    head, status, buttons = await _card(p["admin"], cid)
    assert head == who and status.startswith("Đang diễn ra · 2 người") and buttons == []             # đang diễn ra: vẫn không có "Kết thúc" cho người ngoài cuộc
    await ok(p["sale1"], "meet.end", call_id=cid)


async def test_declining_ends_the_call_says_so_on_the_card_and_tells_the_caller(co: tuple[Any, str]) -> None:
    p = await _people(co)
    a, b = p["sale1"], p["sale2"]
    _, cid = await _dm_call(a, b)
    await ok(b, "meet.end", call_id=cid)                                                              # nút "Từ chối" = đường kết thúc có sẵn
    assert await owner_val("SELECT end_reason FROM meet.calls WHERE id = %s", (cid,)) == "person"
    assert await _card(b, cid) == (f"{a.name} đã gọi cho bạn", "Bạn đã từ chối", [])
    assert await _card(a, cid) == (f"Bạn đã gọi {b.name}", f"{b.name} đã từ chối", [])
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND title = %s AND open->'input'->>'call_id' = %s",
                           (a.user_id, f"{b.name} đã từ chối cuộc gọi", cid)) == 1


async def test_a_cancelled_call_is_a_missed_call_for_the_callee_not_a_decline(co: tuple[Any, str]) -> None:
    p = await _people(co)
    a, b = p["sale1"], p["sale2"]
    _, cid = await _dm_call(a, b)
    await ok(a, "meet.end", call_id=cid)                                                              # "Huỷ cuộc gọi"
    assert await _card(a, cid) == (f"Bạn đã gọi {b.name}", "Bạn đã huỷ cuộc gọi", [])
    assert await _card(b, cid) == (f"{a.name} đã gọi cho bạn", "Cuộc gọi nhỡ", [])
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE title LIKE %s AND open->'input'->>'call_id' = %s", ("%đã từ chối cuộc gọi", cid)) == 0


async def test_a_full_one_to_one_call_gives_a_third_person_no_join_button(co: tuple[Any, str]) -> None:
    p = await _people(co)
    lead = await ok(p["sale1"], "crm.create_lead", name=f"Khách đủ người {uuid.uuid4().hex[:4]}", phone=fresh_phone())
    subj = {"subject_kind": "crm.lead", "subject_id": str(lead["lead_id"])}
    out = await ok(p["sale1"], "meet.ring", **subj, callee=p["truong"].email, join_url="")
    block = next(x for x in (await q(p["admin"], "talk.thread", **subj))["blocks"] if x["type"] == "thread")
    assert block["actions"] == []                                                                     # đã đủ hai người, admin không ngồi ghế nào
    await ok(p["sale1"], "meet.end", call_id=out["call_id"])


def _sides(*, callee_joined: bool) -> Facts:
    now = datetime(2026, 11, 7, 16, 17, tzinfo=UTC)
    return Facts(Side(uuid.uuid4(), "Hồ Đức Phát", True, True, now, "join"),
                 Side(uuid.uuid4(), "Phạm Minh Đức", callee_joined, False, now if callee_joined else None, ""))


def test_card_wording_for_each_viewer_and_state() -> None:
    call: dict[str, Any] = {"status": "active", "end_reason": None, "ended_by": None, "started_at": datetime(2026, 11, 7, 16, 17, tzinfo=UTC), "ended_at": None}
    assert ring_view(call, _sides(callee_joined=False), "callee")[:2] == ("Hồ Đức Phát gọi cho bạn", "Đang chờ bạn vào")
    assert ring_view(call, _sides(callee_joined=False), "caller")[:2] == ("Bạn đang gọi Phạm Minh Đức", "Chờ Phạm Minh Đức vào")
    assert ring_view(call, _sides(callee_joined=False), "other")[:2] == ("Hồ Đức Phát đang gọi Phạm Minh Đức", "Chờ Phạm Minh Đức vào")
    live = ring_view(call, _sides(callee_joined=True), "other")
    assert live[0] == "Hồ Đức Phát đang gọi Phạm Minh Đức" and live[1] == "Đang diễn ra · 1 người · từ 23:17" and live[2] == "live"   # 16:17 UTC = 23:17 giờ Việt Nam
    stale = {**call, "status": "ended", "end_reason": "stale", "ended_at": call["started_at"]}
    assert ring_view(stale, _sides(callee_joined=False), "caller")[2] == "missed"
