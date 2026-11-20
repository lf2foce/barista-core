"""Nút Gọi MỘT chạm (0114 ghi chú 07/11): server quyết gọi ai và nút nào hiện, nút điền sẵn hết ⇒ không còn form; ô cũ của ``meet.start`` vẫn dùng được."""

import uuid
from typing import Any

import pytest

from barista import seed
from barista.config import settings
from barista.kernel import executor, registry
from barista.kernel.forms import form_spec
from barista.kernel.principal import Principal
from tests.conftest import fresh_phone, ok, owner_val, q

PEOPLE = [("admin", "Quản trị thử", "admin", "QT", True), ("truong", "Trưởng nhóm thử", "team_lead", "KD-A", True),
          ("sale1", "Tư vấn một", "sale", "KD-A", False), ("sale2", "Tư vấn hai", "sale", "KD-A", False)]
LINK = "https://meet.google.com/abc-defg-hij"


@pytest.fixture(scope="module")
async def co() -> tuple[Any, str]:
    d = f"d{uuid.uuid4().hex[:6]}.demo"
    return await seed.create_tenant(f"Công ty gọi một chạm {d}", d, people=PEOPLE, packs=("crm", "talk", "meet")), d


async def _people(co: tuple[Any, str]) -> dict[str, Principal]:
    tid, d = co
    return {k: await seed.who(tid, k, d) for k, *_ in PEOPLE}


def _actions(page: dict[str, Any]) -> list[dict[str, Any]]:
    return next(b for b in page["blocks"] if b["type"] == "thread")["actions"]


def _form(command: str) -> dict[str, dict[str, Any]]:
    op = registry.get(command)
    assert op is not None
    return {f["key"]: f for f in form_spec(op)["fields"]}


def _visible(act: dict[str, Any]) -> list[str]:
    """Ô người còn phải thấy / điền khi bấm nút (như app: không ẩn, không điền sẵn) — rỗng ⇒ nút "instant" chạy luôn."""
    return [k for k, f in _form(act["command"]).items() if not f["hidden"] and k not in act["preset"]]


async def _direct_thread(a: Principal, b: Principal) -> dict[str, str]:
    d = await ok(a, "talk.start_direct", person=b.email, body="")
    return {"subject_kind": "talk.direct", "subject_id": str(d["subject_id"])}


async def test_a_direct_message_calls_the_other_person_in_one_tap_voice_or_video(co: tuple[Any, str]) -> None:
    p = await _people(co)
    subj = await _direct_thread(p["sale1"], p["sale2"])
    acts = _actions(await q(p["sale1"], "talk.thread", **subj))
    assert [(a["command"], a["title"], a["style"]) for a in acts] == [("meet.ring", "Gọi", "instant"), ("meet.ring", "Gọi video", "instant")]
    assert all(_visible(a) == [] for a in acts)                                          # không hỏi gì: bấm là chạy
    voice = await ok(p["sale1"], acts[0]["command"], **acts[0]["preset"])
    cid = str(voice["call_id"])
    assert voice["call_page"] == f"/goi/{cid}?auto=1" and voice["once_open_url"].endswith(f"/goi/{cid}?auto=1")   # giọng là mặc định, vào ngay
    assert await owner_val("SELECT title FROM meet.calls WHERE id = %s", (cid,)) == f"{p['sale1'].name} gọi {p['sale2'].name}"   # tên lưu trung tính: nêu cả hai người
    assert await owner_val("SELECT user_id FROM meet.seats WHERE call_id = %s AND seat = 'b'", (cid,)) == p["sale2"].user_id
    # người kia thấy "Tham gia" một chạm, vào thẳng trang gọi
    theirs = _actions(await q(p["sale2"], "talk.thread", **subj))
    assert [(a["command"], a["style"]) for a in theirs] == [("meet.join", "instant")] and _visible(theirs[0]) == []
    joined = await ok(p["sale2"], theirs[0]["command"], **theirs[0]["preset"])
    assert joined["seat"] == "b" and joined["call_page"] == f"/goi/{cid}?auto=1"
    await ok(p["sale1"], "meet.end", call_id=cid)
    video = await ok(p["sale1"], acts[1]["command"], **acts[1]["preset"])
    assert video["call_page"].endswith("?auto=1&video=1")


async def test_a_record_thread_calls_its_owner_and_rings_them_by_name(co: tuple[Any, str]) -> None:
    p = await _people(co)
    lead = await ok(p["sale1"], "crm.create_lead", name=f"Khách gọi {uuid.uuid4().hex[:4]}", phone=fresh_phone())
    subj = {"subject_kind": "crm.lead", "subject_id": str(lead["lead_id"])}
    acts = _actions(await q(p["truong"], "talk.thread", **subj))                            # người xem khác chủ hồ sơ ⇒ gọi chủ hồ sơ
    assert [a["title"] for a in acts] == ["Gọi", "Gọi video"] and all(_visible(a) == [] for a in acts)
    out = await ok(p["truong"], acts[0]["command"], **acts[0]["preset"])
    assert await owner_val("SELECT user_id FROM meet.seats WHERE call_id = %s AND seat = 'b'", (out["call_id"],)) == p["sale1"].user_id
    assert await owner_val("SELECT %s = ANY(mentions) FROM talk.messages WHERE card->'input'->>'call_id' = %s", (p["sale1"].user_id, str(out["call_id"])))
    await ok(p["truong"], "meet.end", call_id=out["call_id"])
    mine = _actions(await q(p["sale1"], "talk.thread", **subj))                               # chủ hồ sơ tự xem: không có "người kia" ⇒ chọn gọi ai
    assert [(a["title"], a["style"]) for a in mine] == [("Gọi", "primary")] and _visible(mine[0]) == ["callee", "video", "join_url"]
    assert (await executor.run(p["sale1"], "meet.ring", {**subj})).status == "invalid"        # chưa chọn ai ⇒ báo, không tạo cuộc gọi nửa chừng
    opts = await q(p["sale1"], "meet.callee_options", **subj)
    values = [o["value"] for o in opts]                                                       # danh sách thành viên công ty: không lộ ai xem được hồ sơ, không cắt bớt
    assert p["truong"].email in values and p["sale2"].email in values and p["sale1"].email not in values
    assert await q(p["sale2"], "meet.callee_options", **subj) == []                           # không thấy hồ sơ ⇒ không có danh sách
    assert (await executor.run(p["sale1"], "meet.ring", {**subj, "callee": p["sale2"].email})).status == "invalid"
    me = await executor.run(p["sale1"], "meet.ring", {**subj, "callee": p["sale1"].email.upper()})        # tự gọi mình ⇒ báo, không để lại cuộc gọi treo
    assert me.status == "invalid" and "chính mình" in me.error
    assert await owner_val("SELECT count(*) FROM meet.calls WHERE subject_id = %s AND status = 'active'", (subj["subject_id"],)) == 0
    done = await ok(p["sale1"], "meet.ring", **subj, callee=p["truong"].email, video=True)
    assert done["call_page"].endswith("?auto=1&video=1")


async def test_a_room_asks_who_to_call_and_offers_the_company_link_only_when_one_is_set(co: tuple[Any, str]) -> None:
    p = await _people(co)
    room = await ok(p["sale1"], "talk.create_room", name=f"Phòng nhóm {uuid.uuid4().hex[:4]}", members=[p["truong"].email, p["sale2"].email])
    subj = {"subject_kind": "talk.room", "subject_id": str(room["room_id"])}
    acts = _actions(await q(p["sale1"], "talk.thread", **subj))
    assert [(a["title"], a["style"]) for a in acts] == [("Gọi", "primary")]                  # không link phòng ⇒ chỉ ô chọn
    assert _visible(acts[0]) == ["callee", "video", "join_url"] and _form("meet.ring")["join_url"]["group"] == "Tuỳ chọn khác"
    assert not any(f.get("group") for k, f in _form("meet.ring").items() if k != "join_url")
    assert sorted(o["value"] for o in await q(p["sale1"], "meet.callee_options", **subj)) == sorted([p["truong"].email, p["sale2"].email])
    outsider = await executor.query(p["admin"], "meet.callee_options", subj)
    assert outsider.status == "ok" and outsider.output == []                                  # không ở trong phòng ⇒ không thấy ai
    await ok(p["admin"], "meet.configure", room_url=LINK)
    try:
        acts = _actions(await q(p["sale1"], "talk.thread", **subj))
        assert [a["title"] for a in acts] == ["Gọi", "Gọi bằng link Meet/Zoom của công ty"]
        assert acts[1]["style"] == "instant" and _visible(acts[1]) == []
        started = await ok(p["sale1"], acts[1]["command"], **acts[1]["preset"])
        assert started["once_open_url"] == LINK and "call_page" not in started
        await ok(p["sale1"], "meet.end", call_id=started["call_id"])
    finally:
        await ok(p["admin"], "meet.configure", room_url="")
    pasted = await ok(p["sale1"], "meet.ring", **subj, join_url=LINK)                          # "Tuỳ chọn khác": dán link ngoài
    assert pasted["once_open_url"] == LINK
    await ok(p["sale1"], "meet.end", call_id=pasted["call_id"])
    pick = await ok(p["sale1"], "meet.ring", **subj, callee=p["sale2"].email)
    assert await owner_val("SELECT user_id FROM meet.seats WHERE call_id = %s AND seat = 'b'", (pick["call_id"],)) == p["sale2"].user_id


async def test_a_two_person_room_is_called_like_a_direct_message(co: tuple[Any, str]) -> None:
    p = await _people(co)
    room = await ok(p["sale1"], "talk.create_room", name=f"Phòng đôi {uuid.uuid4().hex[:4]}", members=[p["truong"].email])
    acts = _actions(await q(p["sale1"], "talk.thread", subject_kind="talk.room", subject_id=str(room["room_id"])))
    assert [a["title"] for a in acts] == ["Gọi", "Gọi video"] and all(_visible(a) == [] for a in acts)


async def test_the_old_form_still_works_and_the_assistant_only_drafts_a_call(co: tuple[Any, str]) -> None:
    p = await _people(co)
    lead = await ok(p["sale1"], "crm.create_lead", name=f"Khách cũ {uuid.uuid4().hex[:4]}", phone=fresh_phone())
    subj = {"subject_kind": "crm.lead", "subject_id": str(lead["lead_id"])}
    old = await ok(p["sale1"], "meet.start", **subj, provider="direct", with_member=p["truong"].email, title="Họp nhanh")
    assert old["call_page"] == f"/goi/{old['call_id']}" and old["once_open_url"] == f"{settings.web_url.rstrip('/')}/goi/{old['call_id']}"   # không đổi
    assert {"title", "provider", "join_url", "with_member"} <= set(_form("meet.start")) and not any(f.get("group") for f in _form("meet.start").values())
    await ok(p["sale1"], "meet.end", call_id=old["call_id"])
    bot = p["sale1"].as_agent()
    r = await executor.run(bot, "meet.ring", {**subj, "callee": p["truong"].email})
    assert r.status == "proposed" and await owner_val("SELECT count(*) FROM meet.calls WHERE subject_id = %s AND status = 'active'", (subj["subject_id"],)) == 0
