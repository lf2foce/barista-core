"""Lịch hẹn (học từ bds): hẹn là bảng con của lead (ai thấy khách thì thấy hẹn, đổi người phụ trách ⇒ lịch đi theo), không chồng lịch,
chốt "đã gặp" thành chăm sóc thật, lịch gộp hẹn + hạn gọi lại + việc + đợt đóng tiền, nhắc trước 1 giờ đúng một lần."""

import uuid
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from barista.kernel import executor
from barista.kernel.clock import VN, today
from barista.kernel.principal import system_principal
from tests.conftest import Who, fresh_phone, ok, owner_val, q
from tests.test_journey import _blocks

NAMES: dict[str, str] = {}


async def _lead(sale: Any) -> str:
    name = f"Khách hẹn {uuid.uuid4().hex[:6]}"
    lead = str((await ok(sale, "crm.create_lead", name=name, phone=fresh_phone(), offering_code="HSR"))["lead_id"])
    NAMES[lead] = name
    return lead


def _later(days: int = 3, hhmm: str = "10:00") -> dict[str, str]:
    return {"day": (today() + timedelta(days=days)).isoformat(), "time": hhmm}


def _titles(page: dict[str, Any]) -> list[str]:
    return [i["title"] for b in _blocks(page, "list") for i in b["items"]]


async def test_appointment_follows_the_lead_and_shows_on_the_agenda(who: Who) -> None:
    sale1, sale2, truonga = await who("sale1"), await who("sale2"), await who("truonga")
    lead = await _lead(sale1)
    out = await ok(sale1, "crm.schedule", lead_id=lead, kind="visit", place="Nhà mẫu HSR", **_later())
    assert "tham quan" in out["summary"].lower()
    mine = await q(sale1, "crm.agenda", days=14)
    name = NAMES[lead]
    has = lambda page: any(name in t for t in _titles(page))  # noqa: E731
    assert has(mine)
    assert not has(await q(sale2, "crm.agenda", days=14))                                              # khách người khác: không thấy
    assert has(await q(truonga, "crm.agenda", days=14, who="team"))                                    # trưởng nhóm thấy lịch cả đội
    await ok(truonga, "crm.assign", lead_id=lead, owner_email=sale2.email)
    assert not has(await q(sale1, "crm.agenda", days=14))                                              # lịch đi theo khách
    assert has(await q(sale2, "crm.agenda", days=14))


async def test_overlaps_warn_on_the_sales_calendar_and_bad_times_are_refused(who: Who) -> None:
    """0158: hẹn nằm trên lịch chung, sale là NGƯỜI THAM DỰ (mềm) — chồng giờ với lịch khác của sale ⇒ vẫn đặt, kèm cảnh báo (thay cho chặn ±60 phút)."""
    sale1 = await who("sale1")
    a, b = await _lead(sale1), await _lead(sale1)
    first = await ok(sale1, "crm.schedule", lead_id=a, kind="meet", **_later(4, "09:00"))
    assert first["warnings"] == [] and first["event_id"]
    clash = await ok(sale1, "crm.schedule", lead_id=b, kind="meet", **_later(4, "09:30"))
    assert clash["warnings"] and "đã bận" in clash["warnings"][0] and "Lưu ý" in clash["summary"]
    assert NAMES[a] not in str(clash)                                                                    # cảnh báo không nêu lịch kia là gì
    later = await ok(sale1, "crm.schedule", lead_id=b, kind="meet", **_later(4, "11:00"))
    assert later["warnings"] == []
    times = await owner_val("""SELECT jsonb_agg(to_char(starts_at AT TIME ZONE 'Asia/Ho_Chi_Minh', 'HH24:MI') || '-' || to_char(ends_at AT TIME ZONE 'Asia/Ho_Chi_Minh', 'HH24:MI')
                                                ORDER BY starts_at) FROM schedule.events WHERE source = 'crm.appointment' AND source_ref = ANY(%s)""",
                            ([str(first["appointment_id"]), str(clash["appointment_id"]), str(later["appointment_id"])],))
    assert times == ["09:00-10:00", "09:30-10:30", "11:00-12:00"]                                         # giờ ở lịch chung, gặp mặt mặc định 60 phút
    assert (await executor.run(sale1, "crm.schedule", {"lead_id": b, "kind": "meet", "day": "2020-01-01", "time": "09:00"})).status == "invalid"
    assert (await executor.run(sale1, "crm.schedule", {"lead_id": b, "kind": "meet", "day": _later()["day"], "time": "25:99"})).status == "invalid"
    p = await executor.run(sale1.as_agent(), "crm.schedule", {"lead_id": a, "kind": "meet", **_later(9, "08:00")})
    assert p.status == "proposed"                                                                        # trợ lý chỉ soạn


async def test_closing_an_appointment_becomes_real_care_and_moving_respects_the_rules(who: Who) -> None:
    sale1 = await who("sale2")   # dữ liệu mẫu chỉ hẹn cho sale1 — chạy sau 19h lịch mẫu dời sang ngày mai, trùng khung 60 phút với hẹn ở đây
    lead = await _lead(sale1)
    aid = (await ok(sale1, "crm.schedule", lead_id=lead, kind="meet", **_later(2, "15:00")))["appointment_id"]
    moved = await ok(sale1, "crm.move_appointment", appointment_id=aid, **_later(2, "16:00"))
    assert "16:00" in moved["summary"]
    await ok(sale1, "crm.close_appointment", appointment_id=aid, status="done", note="Khách thích căn 2PN, hẹn xem thêm", next_follow_up=_later(5)["day"])
    assert await owner_val("SELECT last_activity_at IS NOT NULL FROM crm.leads WHERE id = %s", (lead,))      # đã liên hệ thật
    assert await owner_val("SELECT count(*) FROM crm.activities WHERE lead_id = %s AND kind = 'meet' AND note LIKE 'Khách thích%%'", (lead,)) == 1
    assert (await executor.run(sale1, "crm.close_appointment", {"appointment_id": str(aid), "status": "done"})).status == "not_found"   # chốt rồi


async def test_agenda_merges_callbacks_tasks_and_installments_and_feeds_the_brief(who: Who, tomorrow_is_today: Callable[[], date]) -> None:
    sale1 = await who("sale3")   # sale3 không có hẹn trong dữ liệu mẫu ⇒ không vướng khung 60 phút của hẹn hôm nay
    lead = await _lead(sale1)
    await ok(sale1, "crm.log_activity", lead_id=lead, kind="call", outcome="connected", next_follow_up=_later(2)["day"])
    task = await ok(sale1, "flow.create_task", title="Chuẩn bị bảng giá cho khách", due_on=_later(1)["day"])
    page = await q(sale1, "crm.agenda", days=10)
    titles = _titles(page)
    assert any(t.startswith("Gọi lại ") for t in titles) and "Chuẩn bị bảng giá cho khách" in titles
    kinds = {k["label"] for k in _blocks(page, "kpis")[0]["items"]}
    assert {"Hẹn gặp", "Gọi lại", "Việc đến hạn", "Khách đóng tiền"} <= kinds
    today_lead = await _lead(sale1)
    pinned = tomorrow_is_today()                                                                          # "hôm nay" = ngày mai thật ⇒ 10:00 luôn ở tương lai
    await ok(sale1, "crm.schedule", lead_id=today_lead, kind="visit", day=pinned.isoformat(), time="10:00")
    brief = await q(sale1, "crm.brief")
    assert {k["label"]: k["value"] for k in _blocks(brief, "kpis")[0]["items"]}["Hẹn hôm nay"] >= 1
    assert any(b["title"] == "Lịch hẹn hôm nay" for b in _blocks(brief, "list"))
    assert task["task_id"]


async def test_reminder_goes_out_once_an_hour_before(who: Who, demo: dict[str, Any]) -> None:
    sale1 = await who("sale2")   # người khác với test bản tin (hẹn +1 giờ của sale3 sẽ trùng khung 60 phút)
    lead = await _lead(sale1)
    at = datetime.now(VN) + timedelta(minutes=40)                                                         # gần nửa đêm: rơi sang ngày mai — vẫn đặt, vẫn nhắc
    await ok(sale1, "crm.schedule", lead_id=lead, kind="meet", day=at.date().isoformat(), time=at.strftime("%H:%M"), place="Quán cà phê")
    sys_p = system_principal(demo["tenant"])
    first = await executor.run(sys_p, "crm.remind_appointments", {})
    again = await executor.run(sys_p, "crm.remind_appointments", {})
    assert first.ok and first.output["sent"] >= 1 and again.output["sent"] == 0
    inbox = (await q(sale1, "core.notifications", limit=20))["items"]
    assert any(n["kind"] == "appointment" and "Sắp tới giờ hẹn" in n["title"] for n in inbox)


async def test_journey_shows_the_appointment_and_has_buttons(who: Who) -> None:
    sale1 = await who("sale1")
    lead = await _lead(sale1)
    await ok(sale1, "crm.schedule", lead_id=lead, kind="visit", place="Nhà mẫu", **_later(3, "09:00"))
    page = await q(sale1, "crm.journey", lead_id=lead)
    assert any("Tham quan" in i["title"] for b in _blocks(page, "list") for i in b["items"])
    cmds = [a["command"] for a in page["actions"]]
    assert "crm.schedule" in cmds and "crm.close_appointment" in cmds


async def test_agenda_opens_for_roles_without_deal_rights(who: Who) -> None:
    """Điều phối lead có quyền khách nhưng không có quyền giao dịch: lịch vẫn mở (bỏ nhóm đóng tiền), không vỡ cả màn."""
    page = await q(await who("digital"), "crm.agenda", days=7)
    assert _blocks(page, "kpis")


async def test_calendar_screen_names_the_customer_and_moves_and_creates_through_crm(who: Who) -> None:
    """0240: trên màn Lịch, hẹn khách (lịch riêng tư) hiện tên khách + nút mở hồ sơ cho người thấy khách (sale, trưởng nhóm), dời bằng
    ``crm.move_appointment``; tạo nhanh bằng ``crm.schedule`` với khách chọn từ danh sách; câu tự nhiên đoán đúng khách của mình."""
    sale1, truonga, sale2 = await who("sale1"), await who("truonga"), await who("sale2")
    lead = await _lead(sale1)
    day = (today() + timedelta(days=5)).isoformat()
    out = await ok(sale1, "crm.schedule", lead_id=lead, kind="visit", day=day, time="10:00")

    def item(page: dict[str, Any]) -> dict[str, Any] | None:
        (b,) = [x for x in page["blocks"] if x["type"] == "calendar"]
        return next((i for i in b["items"] if i["id"] == str(out["event_id"])), None)

    mine = item(await q(sale1, "schedule.calendar", on=day))
    assert mine and NAMES[lead] in mine["title"] and mine["kind"] == "site_visit" and not mine["busy"]
    assert mine["links"][0]["input"] == {"lead_id": lead}
    assert mine["move"] == {"command": "crm.move_appointment", "preset": {"appointment_id": str(out["appointment_id"])}, "fields": {"day": "day", "start": "time"}}
    lead_view = item(await q(truonga, "schedule.calendar", on=day, who="team"))
    assert lead_view and NAMES[lead] in lead_view["title"] and not lead_view["busy"]   # trưởng nhóm thấy khách của đội ⇒ thấy hẹn (như Lịch hẹn khách)
    other = item(await q(sale2, "schedule.calendar", on=day, member_email=sale1.email))
    assert other is None or (other["busy"] and NAMES[lead] not in other["title"])     # sale khác: không tên khách
    await ok(sale1, mine["move"]["command"], **mine["move"]["preset"], day=day, time="15:00")
    assert item(await q(sale1, "schedule.calendar", on=day))["start"].startswith(f"{day}T15:00")   # type: ignore[index]
    (b,) = [x for x in (await q(sale1, "schedule.calendar", on=day))["blocks"] if x["type"] == "calendar"]
    visit = next(c for c in b["create"]["creators"] if c["kind"] == "site_visit")
    assert visit["command"] == "crm.schedule" and visit["subject"]["search"] == "crm.lead_pick"
    picked = await q(sale1, "crm.lead_pick", q=NAMES[lead][-6:])
    assert [o["value"] for o in picked] == [lead]
    guess = await q(sale1, "schedule.understand", text=f"thứ 6 2h dẫn {NAMES[lead]} đi xem nhà mẫu")
    assert guess["creator"] == "site_visit" and guess["subject"]["value"] == lead and guess["start"] == "14:00"
