"""0240: màn Lịch kiểu app lịch — khối ``calendar`` (Ngày: người cạnh nhau theo phạm vi; Tuần; Lịch biểu), màu theo loại là dữ liệu công ty, lịch không
được xem chi tiết chỉ là "Bận", mục kéo được chỉ khi người xem dời được, cách tạo theo quyền, câu tự nhiên ⇒ bản đoán (không ghi gì)."""

import uuid
from datetime import date, datetime, timedelta
from typing import Any

import pytest

from barista import seed
from barista.kernel import executor
from barista.kernel.clock import VN, today
from barista.modules.schedule.view import understand_text
from tests.conftest import ok, owner_val, q

PEOPLE = [("admin", "Quản trị", "admin", "QT", True), ("tn", "Trưởng nhóm", "team_lead", "KD-A", True), ("nv1", "Người một", "staff", "KD-A", False),
          ("nv2", "Người hai", "staff", "KD-A", False), ("nv3", "Người ba", "staff", "KD-C", False)]
DAY = today() + timedelta(days=21)


@pytest.fixture(scope="module")
async def co() -> dict[str, Any]:
    domain = f"v{uuid.uuid4().hex[:6]}.demo"
    tid = await seed.create_tenant(f"Công ty màn lịch {domain}", domain, people=PEOPLE, packs=("schedule",), about="công ty thử màn lịch")
    who = {k: await seed.who(tid, k, domain) for k, *_ in PEOPLE}
    a = who["admin"]
    await ok(a, "schedule.save_kind", key="meeting", label="Họp nội bộ", sort=10, tone="purple")
    await ok(a, "schedule.save_kind", key="visit", label="Đi gặp", sort=20)   # chưa chọn màu ⇒ màu theo thứ tự
    ev = {}
    ev["team"] = (await ok(a, "schedule.create", title="Họp đội A", day=DAY.isoformat(), start="08:00", end="08:30", kind="meeting", team_code="KD-A",
                           attendee_emails=[f"tn@{domain}", f"nv1@{domain}", f"nv2@{domain}"]))["event_id"]
    ev["nv1"] = (await ok(a, "schedule.create", title="Đi gặp đối tác", day=DAY.isoformat(), start="14:00", end="15:30", kind="visit", team_code="KD-A",
                          attendee_emails=[f"nv1@{domain}"]))["event_id"]
    ev["c"] = (await ok(a, "schedule.create", title="Việc đội C", day=DAY.isoformat(), start="10:00", end="11:00", team_code="KD-C",
                        attendee_emails=[f"nv3@{domain}"]))["event_id"]
    ev["late"] = (await ok(a, "schedule.create", title="Trực tối", day=DAY.isoformat(), start="21:00", end="22:00", team_code="KD-A",
                           attendee_emails=[f"nv2@{domain}"]))["event_id"]
    return {"tid": tid, "domain": domain, "who": who, "ev": ev}


def _block(page: dict[str, Any]) -> dict[str, Any]:
    assert page["kind"] == "card"
    (b,) = [x for x in page["blocks"] if x["type"] == "calendar"]
    return b


async def test_day_view_puts_team_members_side_by_side_with_kind_colours(co: dict[str, Any]) -> None:
    b = _block(await q(co["who"]["tn"], "schedule.calendar", on=DAY.isoformat()))
    assert b["view"] == "day" and b["start"] == b["end"] == DAY.isoformat()
    names = [ln["title"] for ln in b["lanes"]]
    assert names[0] == "Tôi" and set(names[1:]) == {"Người một", "Người hai"}   # đội KD-A có lịch hôm đó; người đội C không ở đây
    team = [i for i in b["items"] if i["id"] == str(co["ev"]["team"])]
    assert len(team) == 3 and {i["tone"] for i in team} == {"purple"}   # họp ba người: một mục trên mỗi làn, màu của loại
    visit = next(i for i in b["items"] if i["id"] == str(co["ev"]["nv1"]))
    assert visit["tone"] != "purple" and visit["start"].startswith(f"{DAY.isoformat()}T14:00")
    assert b["hours"][1] >= 22   # khung giờ nới để phủ lịch tối
    assert not any(i["id"] == str(co["ev"]["c"]) for i in b["items"])
    assert {k["key"]: k["tone"] for k in b["kinds"]}["meeting"] == "purple"


async def test_staff_sees_own_lane_creates_own_events_and_cannot_move_others(co: dict[str, Any]) -> None:
    b = _block(await q(co["who"]["nv1"], "schedule.calendar", on=DAY.isoformat()))
    assert [ln["me"] for ln in b["lanes"]] == [True] and "who" not in b["nav"]
    assert {i["title"] for i in b["items"]} == {"Họp đội A", "Đi gặp đối tác"}
    assert {c["command"] for c in b["create"]["creators"]} == {"schedule.plan"}   # 0240: ai xem được Lịch cũng tạo được lịch CỦA MÌNH
    assert all("move" not in i for i in b["items"])                                # lịch quản trị đặt: không dời được
    assert next(i for i in b["items"] if i["title"] == "Họp đội A")["organizer"] == "Quản trị"


async def test_anyone_organises_a_meeting_invitees_see_it_and_answer(co: dict[str, Any]) -> None:
    """Người phạm vi "của mình" tổ chức họp: MỘT lịch, hiện ở làn mọi người được mời (kể cả người đội khác không có quyền xem lịch người tổ chức),
    người được mời thấy ai tổ chức + Đồng ý / Từ chối; người tổ chức dời / huỷ được lịch của mình, không dời được lịch người khác; được báo trong app."""
    nv1, nv2, nv3, d = co["who"]["nv1"], co["who"]["nv2"], co["who"]["nv3"], co["domain"]
    day = DAY + timedelta(days=1)
    out = await ok(nv1, "schedule.plan", title="Họp nhóm nhỏ", day=day.isoformat(), start="10:00", end="11:00", kind="meeting",
                   attendee_emails=[f"nv2@{d}", f"nv3@{d}"])
    eid = str(out["event_id"])
    assert await owner_val("SELECT count(*) FROM schedule.events WHERE id = %s", (eid,)) == 1
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND title LIKE '%%mời: Họp nhóm nhỏ%%'", (nv3.user_id,)) == 1
    for p, mine in ((nv2, False), (nv3, False), (nv1, True)):
        (it,) = [i for i in _block(await q(p, "schedule.calendar", on=day.isoformat()))["items"] if i["id"] == eid]
        assert it["organizer"] == "Người một" and not it["busy"] and {x["name"] for x in it["people"]} == {"Người một", "Người hai", "Người ba"}
        assert ("respond" in it) is not mine and ("move" in it) is mine and ("cancel" in it) is mine
        if not mine:
            assert it["status"] == "tentative" and it["respond"]["command"] == "schedule.respond"
    await ok(nv3, "schedule.respond", event_id=eid, answer="accepted")
    (it,) = [i for i in _block(await q(nv3, "schedule.calendar", on=day.isoformat()))["items"] if i["id"] == eid]
    assert it["status"] == "booked"
    await ok(nv1, "schedule.move", event_id=eid, day=day.isoformat(), start="14:00", end="15:00")   # người tổ chức dời lịch của mình
    assert (await executor.run(nv1, "schedule.move", {"event_id": str(co["ev"]["team"]), "day": DAY.isoformat(), "start": "09:00", "end": "09:30"})).status == "denied"
    card = await q(nv1, "schedule.event", event_id=eid)   # nhấn đúp mục ⇒ thẻ chi tiết: người tổ chức sửa được, ô điền sẵn
    acts = {x["command"]: x for x in card["actions"]}
    assert acts["schedule.edit"]["values"]["title"] == "Họp nhóm nhỏ" and set(acts["schedule.set_attendees"]["values"]["attendee_emails"]) == {
        f"nv1@{d}", f"nv2@{d}", f"nv3@{d}"} and acts["schedule.move"]["values"]["start"] == "14:00"
    assert not {"schedule.edit", "schedule.move", "schedule.cancel"} & {x["command"] for x in (await q(nv2, "schedule.event", event_id=eid))["actions"]}
    await ok(nv1, "schedule.edit", event_id=eid, title="Họp nhóm nhỏ (đổi tên)", kind="meeting", note="mang số liệu tuần")
    assert (await executor.run(nv2, "schedule.edit", {"event_id": eid, "title": "x"})).status == "denied"
    await ok(nv1, "schedule.cancel", event_id=eid)


async def test_admin_can_drag_others_events_and_create(co: dict[str, Any]) -> None:
    b = _block(await q(co["who"]["admin"], "schedule.calendar", on=DAY.isoformat(), member_email=f"nv1@{co['domain']}"))
    visit = next(i for i in b["items"] if i["id"] == str(co["ev"]["nv1"]))
    assert visit["move"] == {"command": "schedule.move", "preset": {"event_id": str(co["ev"]["nv1"])}, "fields": {"day": "day", "start": "start", "end": "end"}}
    kinds = {c["kind"]: c for c in b["create"]["creators"]}
    assert kinds["meeting"]["command"] == "schedule.plan" and kinds["meeting"]["fields"]["attendee"] == "attendee_emails"
    # thả mục sang giờ mới = đúng lệnh server khai
    await ok(co["who"]["admin"], visit["move"]["command"], **visit["move"]["preset"], day=DAY.isoformat(), start="15:00", end="16:30")
    b2 = _block(await q(co["who"]["admin"], "schedule.calendar", on=DAY.isoformat(), member_email=f"nv1@{co['domain']}"))
    assert next(i for i in b2["items"] if i["id"] == str(co["ev"]["nv1"]))["start"].startswith(f"{DAY.isoformat()}T15:00")


async def test_director_sees_team_leads_by_default_and_picks_a_unit(co: dict[str, Any]) -> None:
    gd = await seed.who(co["tid"], "admin", co["domain"])   # quản trị: phạm vi cả công ty như giám đốc
    b = _block(await q(gd, "schedule.calendar", on=DAY.isoformat(), who="team"))
    assert [ln["title"] for ln in b["lanes"]] == ["Tôi", "Trưởng nhóm"]                # tôi + các trưởng nhóm có lịch hôm đó
    assert b["nav"]["unit"]["options"][0]["title"] == "Các trưởng nhóm" and {"KD-A", "KD-C"} <= {o["key"] for o in b["nav"]["unit"]["options"]}
    u = _block(await q(gd, "schedule.calendar", on=DAY.isoformat(), who="team", team_code="KD-C"))
    assert [ln["title"] for ln in u["lanes"]] == ["Tôi", "Người ba"]
    assert any(g["title"].startswith("Các trưởng nhóm") for g in b["create"]["invite"]["groups"])


async def test_week_and_agenda_ranges_and_busy_blocks(co: dict[str, Any]) -> None:
    w = _block(await q(co["who"]["tn"], "schedule.calendar", view="week", on=DAY.isoformat(), who="team"))
    mon = DAY - timedelta(days=DAY.isoweekday() - 1)
    assert (w["start"], w["end"], len(w["days"])) == (mon.isoformat(), (mon + timedelta(days=6)).isoformat(), 7)
    assert w["lanes"] == [] and len([i for i in w["items"] if i["id"] == str(co["ev"]["team"])]) == 1   # tuần: họp nhiều người hiện một lần
    ag = _block(await q(co["who"]["tn"], "schedule.calendar", view="agenda", on=DAY.isoformat()))
    assert (date.fromisoformat(ag["end"]) - date.fromisoformat(ag["start"])).days == 13
    # người đội C xem "Một người" của đội A mà không có quyền chi tiết ⇒ chỉ khối "Bận", không tên việc
    other = _block(await q(co["who"]["nv3"], "schedule.calendar", on=DAY.isoformat(), member_email=f"nv1@{co['domain']}"))
    assert other["items"] and all(i["busy"] and i["title"] in ("Bận", "Bận (chưa chắc)") for i in other["items"])


def test_understand_reads_vietnamese_day_time_and_length() -> None:
    now = datetime(2026, 11, 18, 19, 0, tzinfo=VN)   # thứ Tư
    base = date(2026, 11, 18)
    got = understand_text("Thứ 5 2h dẫn khách A xem Sun Feliza", base, now)
    assert (got["day"], got["start"], got["title"]) == ("2026-11-19", "14:00", "Dẫn khách A xem Sun Feliza")
    assert understand_text("chiều mai 3h gặp chị Thư", base, now)["start"] == "15:00"
    assert understand_text("9h sáng thứ 7 tuần sau họp", base, now)["day"] == "2026-11-28"
    r = understand_text("25/11 10h-11h30 ký HĐ", base, now)
    assert (r["day"], r["start"], r["minutes"], r["title"]) == ("2026-11-25", "10:00", 90, "Ký HĐ")
    r = understand_text("gọi anh Quyền 30 phút lúc 8h", base, now)
    assert (r["day_said"], r["start"], r["minutes"], r["title"]) == (False, "08:00", 30, "Gọi anh Quyền")
    assert understand_text("CN 10 giờ đi xem nhà mẫu", base, now)["day"] == "2026-11-22"


async def test_understand_query_guesses_without_writing(co: dict[str, Any]) -> None:
    got = await q(co["who"]["admin"], "schedule.understand", text="mai 9h họp giao ban", on=DAY.isoformat())
    assert got["start"] == "09:00" and got["creator"] == "meeting" and got["creator_said"] is True
