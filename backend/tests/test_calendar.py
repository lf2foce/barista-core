"""0158: MỘT đối tượng lịch cho mọi thứ chiếm thời gian — riêng tư / khối "Bận", người tham dự trả lời mời, lặp lại (sửa chỉ lần này / các lần sau / cả
chuỗi, ngoại lệ), nguồn lực nhiều lượt (đầy ⇒ DB chặn, kể cả đồng thời), tìm giờ (người mềm, phòng cứng), mốc hạn không là lịch, hẹn khách chuyển sang
lịch chung (sao chép lịch cũ giữ đủ mọi hẹn), gọi video và nghỉ phép hiện trên lịch, trợ lý không tự đặt lịch lên lịch người khác."""

import asyncio
import uuid
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, LiteralString

import pytest

from barista import seed, seed_pay
from barista.db import db
from barista.kernel import executor
from barista.kernel.clock import VN, today
from barista.kernel.principal import Principal, system_principal
from barista.modules import schedule
from barista.modules.schedule import rrule
from tests.conftest import edge, fresh_phone, ok, owner_val, q

PEOPLE = [("admin", "Quản trị", "admin", "QT", True), ("dpa", "Điều phối A", "coordinator", "KD-A", True),
          ("dpc", "Điều phối C", "coordinator", "KD-C", True), ("nv1", "Người một", "staff", "KD-A", False),
          ("nv2", "Người hai", "staff", "KD-C", False), ("nv3", "Người ba", "staff", "KD-A", False), ("hc", "Hành chính", "office", "HCNS", True)]
MON = date.fromordinal(today().toordinal() - today().weekday() + 28)   # thứ Hai sau bốn tuần: luôn ở tương lai


@pytest.fixture(scope="module")
async def co() -> dict[str, Any]:
    domain = f"c{uuid.uuid4().hex[:6]}.demo"
    tid = await seed.create_tenant(f"Công ty lịch chung {domain}", domain, people=PEOPLE, packs=("schedule",), about="công ty thử lịch chung")
    await owner_val("INSERT INTO core.grants (tenant_id, role, resource, scope, actions) VALUES (%s, 'coordinator', 'schedule', 'team', '{read,write}') RETURNING 1",
                    (tid,))
    who = {k: await seed.who(tid, k, domain) for k, *_ in PEOPLE}
    res = {}
    for key, name, nature, kind, team, email, slots in (
            ("p1", "Người một", "person", "nhân sự", "KD-A", f"nv1@{domain}", 1), ("ra", "Phòng A1", "thing", "phòng", "KD-A", "", 1),
            ("rb", "Phòng A2", "thing", "phòng", "KD-A", "", 1), ("rc", "Phòng C1", "thing", "phòng", "KD-C", "", 1),
            ("desk", "Khu ngồi chung", "thing", "chỗ ngồi", "KD-A", "", 2)):
        res[key] = (await ok(who["admin"], "schedule.add_resource", name=name, nature=nature, kind=kind, team_code=team, user_email=email, slots=slots))["resource_id"]
    return {"tid": tid, "domain": domain, "who": who, "res": res}


def _email(co: dict[str, Any], k: str) -> str:
    return f"{k}@{co['domain']}"


def _ev(co: dict[str, Any], day: date, start: str, end: str, *keys: str, team: str = "KD-A", **kw: Any) -> dict[str, Any]:
    return {"title": kw.pop("title", f"Việc {uuid.uuid4().hex[:5]}"), "day": day.isoformat(), "start": start, "end": end, "team_code": team,
            "resource_ids": [co["res"][k] for k in keys], **kw}


async def _sql_count(p: Principal, sql: LiteralString, params: Any) -> int:
    async with db.tenant_tx(p, read_only=True) as conn:
        return (await (await conn.execute(sql, params)).fetchone() or {})["n"]


# ── RRULE: hàm thuần ──

def test_rrule_subset_round_trips_and_counts_from_the_first_occurrence() -> None:
    start = datetime.combine(MON, time(9), VN)
    r = rrule.parse("FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE;COUNT=5")
    assert rrule.parse(rrule.text(r)) == r and rrule.text(r) == "FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE;COUNT=5"
    got = rrule.starts(r, start, upto=start + timedelta(days=60))
    assert [t.date() for t in got] == [MON, MON + timedelta(days=2), MON + timedelta(days=14), MON + timedelta(days=16), MON + timedelta(days=28)]
    assert all(t.astimezone(VN).time() == time(9) for t in got)                                         # giờ đồng hồ giữ nguyên
    assert rrule.starts(r, start, upto=start + timedelta(days=60), after=got[1]) == got[2:]               # số lần đếm từ lần đầu chuỗi
    assert rrule.nth(r, start, got[3]) == 3
    daily = rrule.parse(f"FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR;UNTIL={(MON + timedelta(days=6)):%Y%m%d}")
    assert len(rrule.starts(daily, start, upto=start + timedelta(days=30))) == 5                         # hết thứ Sáu, bỏ cuối tuần
    utc = rrule.parse("FREQ=DAILY;UNTIL=" + f"{(start + timedelta(days=2)).astimezone(UTC):%Y%m%dT%H%M%SZ}")
    assert len(rrule.starts(utc, start, upto=start + timedelta(days=30))) == 3                           # UNTIL tính cả lần đúng mốc
    for bad in ("FREQ=MONTHLY", "FREQ=WEEKLY;BYSETPOS=1", "FREQ=DAILY;COUNT=2;UNTIL=20301231", "FREQ=WEEKLY;BYDAY=XX"):
        with pytest.raises(schedule.Invalid):
            rrule.parse(bad)


# ── Riêng tư: người không được đọc chi tiết chỉ thấy "Bận" ──

async def test_other_team_and_private_events_show_only_busy(co: dict[str, Any]) -> None:
    w = co["who"]
    day = MON + timedelta(days=1)
    secret = (await ok(w["dpa"], "schedule.create", **_ev(co, day, "09:00", "10:00", "ra", title="Họp kín dự án Sao Băng", note="ghi chú nội bộ",
                                                          attendee_emails=[_email(co, "nv3")])))["event_id"]
    mine = await q(w["nv3"], "schedule.my_agenda", days=60)                                              # người tham dự (phạm vi "của mình") thấy chi tiết
    assert any(r["id"] == secret and r["title"] == "Họp kín dự án Sao Băng" for r in mine["rows"])
    assert "Họp kín dự án Sao Băng" in str(await q(w["dpa"], "schedule.event", event_id=secret))
    # đội khác (phạm vi đội KD-C): xem lịch của người ba ⇒ chỉ khối "Bận", không tên việc / ghi chú; SQL thẳng dưới RLS cũng không thấy
    other = await q(w["dpc"], "schedule.week", week_of=day.isoformat(), member_email=_email(co, "nv3"))
    row = next(r for r in other["rows"] if r["id"] == secret)
    assert row["title"] == "Bận (chưa chắc)" and row["busy"] and row["status"] == "maybe"                # người ba chưa nhận lời ⇒ chưa chắc
    assert "Sao Băng" not in str(other) and "ghi chú" not in str(other)
    await ok(w["nv3"], "schedule.respond", event_id=secret, answer="accepted")
    other = await q(w["dpc"], "schedule.week", week_of=day.isoformat(), member_email=_email(co, "nv3"))
    assert next(r for r in other["rows"] if r["id"] == secret)["title"] == "Bận"                          # đã nhận ⇒ bận
    card = await q(w["dpc"], "schedule.event", event_id=secret)
    assert card["title"] == "Bận" and "Sao Băng" not in str(card) and "ghi chú" not in str(card) and not card.get("actions")
    assert await _sql_count(w["dpc"], "SELECT count(*) AS n FROM schedule.events WHERE id = %s", (secret,)) == 0
    assert await _sql_count(w["dpc"], "SELECT count(*) AS n FROM schedule.attendees WHERE event_id = %s", (secret,)) == 0
    # riêng tư: cả người phạm vi cả công ty (hành chính) chỉ thấy "Bận" (cùng phòng đang giữ), người trong lịch thấy đủ
    priv = (await ok(w["dpa"], "schedule.create", **_ev(co, day, "11:00", "12:00", "rb", title="Khám sức khoẻ riêng", visibility="private",
                                                        attendee_emails=[_email(co, "nv1")])))["event_id"]
    office = await q(w["hc"], "schedule.week", week_of=day.isoformat())
    prow = next(r for r in office["rows"] if r["id"] == priv)
    assert prow["title"] == "Bận" and prow["resources"] == "Phòng A2" and "Khám sức khoẻ" not in str(office)
    assert any(r["id"] == secret and r["title"] == "Họp kín dự án Sao Băng" for r in office["rows"])    # lịch thường: cả công ty thấy đủ
    assert await _sql_count(w["hc"], "SELECT count(*) AS n FROM schedule.events WHERE id = %s", (priv,)) == 0
    assert "Khám sức khoẻ riêng" in str(await q(w["nv1"], "schedule.event", event_id=priv))
    assert "Khám sức khoẻ riêng" in str(await q(w["dpa"], "schedule.event", event_id=priv))            # người tạo
    # tìm rảnh / tìm giờ không lộ tên việc của ai
    free = await q(w["hc"], "schedule.find_free", day=day.isoformat(), start="11:00", end="12:00", kind="phòng")
    assert "Phòng A2" not in {r["name"] for r in free["rows"]} and "Khám" not in str(free)
    found = await q(w["hc"], "schedule.find_time", attendee_emails=[_email(co, "nv1"), _email(co, "nv3")], from_day=day.isoformat(),
                    to_day=day.isoformat(), day_start="09:00", day_end="12:00", minutes=60)
    assert "Khám" not in str(found) and "Sao Băng" not in str(found)
    assert {r["start"]: r["who"] for r in found["rows"]}["11:00"] == "Chưa chắc: Người một"              # được mời, chưa nhận lời


# ── Người tham dự: mời, trả lời, cảnh báo trùng (không chặn) ──

async def test_attendees_rsvp_and_declining_frees_them(co: dict[str, Any]) -> None:
    w = co["who"]
    day = MON + timedelta(days=2)
    out = await ok(w["admin"], "schedule.create", **_ev(co, day, "10:00", "11:00", title="Họp kế hoạch quý",
                                                        attendee_emails=[_email(co, "nv1"), _email(co, "nv2"), _email(co, "admin")]))
    eid = out["event_id"]
    rsvp = await owner_val("SELECT jsonb_object_agg(u.name, a.rsvp) FROM schedule.attendees a JOIN core.users u ON u.id = a.user_id WHERE a.event_id = %s", (eid,))
    assert rsvp == {"Người một": "invited", "Người hai": "invited", "Quản trị": "accepted"}               # người tự đặt cho mình: nhận sẵn
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND title LIKE 'Lịch mới: Họp kế hoạch quý%%'",
                           (w["nv2"].user_id,)) == 1
    card = await q(w["nv1"], "schedule.event", event_id=eid)
    assert {a["command"] for a in card["actions"]} >= {"schedule.respond"}
    await ok(w["nv1"], "schedule.respond", event_id=eid, answer="accepted")
    await ok(w["nv2"], "schedule.respond", event_id=eid, answer="declined")
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND title LIKE 'Người hai: không tham dự — Họp kế hoạch quý%%'",
                           (w["admin"].user_id,)) == 1                                                    # người tạo được báo
    assert (await executor.run(w["nv3"], "schedule.respond", {"event_id": str(eid), "answer": "accepted"})).status == "not_found"
    found = await q(w["admin"], "schedule.find_time", attendee_emails=[_email(co, "nv1"), _email(co, "nv2")], from_day=day.isoformat(),
                    to_day=day.isoformat(), day_start="10:00", day_end="11:00", minutes=60)
    assert [r["who"] for r in found["rows"]] == ["Bận: Người một"]                                       # từ chối ⇒ không còn bận vì lịch này
    clash = await ok(w["admin"], "schedule.create", **_ev(co, day, "10:30", "11:30", title="Việc khác", attendee_emails=[_email(co, "nv1")]))
    assert clash["warnings"] and clash["warnings"][0].startswith("Người một đã bận") and "kế hoạch" not in str(clash)   # chỉ cảnh báo, không nêu lịch kia
    assert "Họp kế hoạch quý" not in str(await q(w["nv3"], "schedule.my_agenda", days=60))


# ── Lặp lại: chỉ lần này / lần này và các lần sau / cả chuỗi; ngoại lệ không bị sinh lại ──

async def _occ(series_id: Any) -> list[dict[str, Any]]:
    return await owner_val("""SELECT jsonb_agg(jsonb_build_object('id', id, 'status', status, 'detached', detached, 'series', series_id,
                                                                  'at', to_char(starts_at AT TIME ZONE 'Asia/Ho_Chi_Minh', 'YYYY-MM-DD HH24:MI'))
                                               ORDER BY starts_at, created_at) FROM schedule.events WHERE series_id = ANY(%s)""",
                           (series_id if isinstance(series_id, list) else [series_id],)) or []


async def test_recurring_series_edits_this_following_and_all(co: dict[str, Any]) -> None:
    w = co["who"]
    first = MON + timedelta(days=7)                                                                       # một thứ Hai
    made = await ok(w["dpa"], "schedule.create", **_ev(co, first, "09:00", "10:00", "rb", title="Giao ban tuần", attendee_emails=[_email(co, "nv3")],
                                                       repeat="weekly", repeat_count=4))
    assert made["occurrences"] == 4 and "mỗi tuần" in made["summary"]
    sid = made["series_id"]
    occ = await _occ(sid)
    assert [o["at"] for o in occ] == [f"{first + timedelta(days=7 * i)} 09:00" for i in range(4)]
    # phòng đã giữ ở MỘT lần lặp ⇒ chuỗi khác cùng phòng cùng giờ không đặt được lần nào (DB chặn từng lần)
    r = await executor.run(w["dpa"], "schedule.create", _ev(co, first + timedelta(days=14), "09:30", "10:30", "rb", repeat="daily", repeat_count=3))
    assert r.status == "conflict" and "Lần 09:30" in r.error and "chưa đặt lần nào" in r.error
    # chỉ lần này: lần 2 sang 11:00
    second = occ[1]["id"]
    await ok(w["dpa"], "schedule.move", event_id=second, day=(first + timedelta(days=7)).isoformat(), start="11:00", end="12:00", scope="this")
    # cả chuỗi (từ lần 1): mọi lần sắp tới CHƯA sửa riêng sang 14:00; lần 2 giữ 11:00
    await ok(w["dpa"], "schedule.move", event_id=occ[0]["id"], day=first.isoformat(), start="14:00", end="15:00", scope="all", title="Giao ban chiều")
    occ = await _occ(sid)
    assert [(o["at"][-5:], o["detached"]) for o in occ] == [("14:00", False), ("11:00", True), ("14:00", False), ("14:00", False)]
    assert await owner_val("SELECT array_agg(DISTINCT title) FROM schedule.events WHERE series_id = %s AND NOT detached", (sid,)) == ["Giao ban chiều"]
    # lần này và các lần sau (từ lần 3): chuỗi cũ dừng, lần 3 + 4 huỷ, chuỗi mới 2 lần (số lần còn lại) lúc 16:00
    out = await ok(w["dpa"], "schedule.move", event_id=occ[2]["id"], day=(first + timedelta(days=14)).isoformat(), start="16:00", end="17:00", scope="following")
    assert "2 lần" in out["summary"]
    old = await _occ(sid)
    assert [o["status"] for o in old] == ["booked", "booked", "cancelled", "cancelled"]
    new_sid = await owner_val("SELECT id FROM schedule.series WHERE tenant_id = %s AND id <> %s AND title = 'Giao ban chiều'", (co["tid"], sid))
    new = await _occ(new_sid)
    assert [o["at"] for o in new] == [f"{first + timedelta(days=14)} 16:00", f"{first + timedelta(days=21)} 16:00"]
    assert await owner_val("SELECT status FROM schedule.series WHERE id = %s", (sid,)) == "ended"
    assert await owner_val("SELECT count(*) FROM schedule.attendees a JOIN schedule.events e ON e.id = a.event_id WHERE e.series_id = %s", (new_sid,)) == 2
    # huỷ chỉ lần này ⇒ ngoại lệ; vòng quét kéo dài chuỗi không sinh lại lần đã huỷ, không nhân đôi
    await ok(w["dpa"], "schedule.cancel", event_id=new[0]["id"], reason="Nghỉ lễ", scope="this")
    await owner_val("UPDATE schedule.series SET horizon_until = dtstart - interval '1 second', status = 'active' WHERE id = %s RETURNING 1", (new_sid,))
    sweep = await executor.run(system_principal(co["tid"]), "schedule.extend_series", {})
    assert sweep.ok and sweep.output["made"] == 0
    assert [o["status"] for o in await _occ(new_sid)] == ["cancelled", "booked"]
    card = await q(w["dpa"], "schedule.event", event_id=new[1]["id"])
    assert "Lặp mỗi tuần" in str(card)


async def test_an_endless_daily_series_is_materialised_ninety_days_ahead_and_extended_without_duplicates(co: dict[str, Any]) -> None:
    w = co["who"]
    start = today() + timedelta(days=3)
    made = await ok(w["admin"], "schedule.create", **_ev(co, start, "06:00", "06:30", team="QT", title="Điểm danh sáng", attendee_emails=[_email(co, "nv2")],
                                                         repeat="daily"))
    sid = made["series_id"]
    n = await owner_val("SELECT count(*) FROM schedule.events WHERE series_id = %s", (sid,))
    assert 85 <= n <= 90
    await owner_val("UPDATE schedule.series SET horizon_until = now() + interval '30 days' WHERE id = %s RETURNING 1", (sid,))
    assert (await executor.run(system_principal(co["tid"]), "schedule.extend_series", {})).ok
    assert await owner_val("SELECT count(*) FROM schedule.events WHERE series_id = %s", (sid,)) == n                # đã có thì không sinh thêm
    await ok(w["admin"], "schedule.cancel", event_id=made["event_id"], scope="all")
    assert await owner_val("SELECT count(*) FROM schedule.events WHERE series_id = %s AND status = 'booked'", (sid,)) == 0


# ── Nguồn lực nhiều lượt: đầy ⇒ chặn, kể cả hai lần đặt cùng lúc khi bỏ qua mọi bước kiểm trong mã ──

async def test_multi_slot_resource_blocks_when_full_even_under_concurrency(co: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    w = co["who"]
    day = MON + timedelta(days=3)
    await ok(w["dpa"], "schedule.create", **_ev(co, day, "09:00", "11:00", "desk"))
    await ok(w["dpa"], "schedule.create", **_ev(co, day, "10:00", "12:00", "desk"))                     # lượt thứ hai: còn chỗ
    r = await executor.run(w["dpa"], "schedule.create", _ev(co, day, "10:30", "11:00", "desk"))
    assert r.status == "conflict" and "Khu ngồi chung" in r.error                                       # 10:30 đã đủ hai lượt
    await ok(w["dpa"], "schedule.create", **_ev(co, day, "11:00", "11:30", "desk"))                     # một lượt đã trả lúc 11:00
    assert (await executor.run(w["admin"], "schedule.update_resource", {"resource_id": co["res"]["desk"], "name": "Khu ngồi chung", "nature": "thing",
                                                                        "kind": "chỗ ngồi", "team_code": "KD-A", "slots": 1})).status == "conflict"
    assert (await executor.run(w["admin"], "schedule.add_resource", {"name": "Hai người một", "nature": "person", "kind": "nhân sự", "team_code": "KD-A",
                                                                     "slots": 2})).status == "invalid"

    async def blind(*_: Any, **__: Any) -> set[Any]:
        await asyncio.sleep(0.3)
        return set()

    async def no_lock(*_: Any, **__: Any) -> None:
        return None

    monkeypatch.setattr(schedule, "_busy", blind)   # bỏ cả bước kiểm lẫn bước giữ lượt trong mã: chỉ còn DB
    monkeypatch.setattr(schedule, "_lock", no_lock)
    later = day + timedelta(days=1)
    await ok(w["dpa"], "schedule.create", **_ev(co, later, "14:00", "15:00", "desk"))
    a, b = await asyncio.gather(executor.run(w["dpa"], "schedule.create", _ev(co, later, "14:00", "15:00", "desk")),
                                executor.run(w["admin"], "schedule.create", _ev(co, later, "14:30", "15:30", "desk")))
    assert sorted([a.status, b.status]) == ["conflict", "ok"], (a, b)
    assert await owner_val("SELECT schedule.load_of(%s, %s, tstzrange(%s, %s), NULL, NULL)",
                           (co["tid"], co["res"]["desk"], datetime.combine(later, time(14), VN), datetime.combine(later, time(16), VN))) == 2


async def test_single_slot_room_still_refuses_two_simultaneous_bookings(co: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    async def blind(*_: Any, **__: Any) -> set[Any]:
        await asyncio.sleep(0.3)
        return set()

    monkeypatch.setattr(schedule, "_busy", blind)
    w = co["who"]
    day = MON + timedelta(days=5)
    a, b = await asyncio.gather(executor.run(w["dpa"], "schedule.create", _ev(co, day, "08:00", "09:00", "ra")),
                                executor.run(w["admin"], "schedule.create", _ev(co, day, "08:30", "09:30", "ra")))
    assert sorted([a.status, b.status]) == ["conflict", "ok"], (a, b)


# ── Tìm giờ: phòng cứng, người mềm ──

async def test_find_time_skips_busy_rooms_and_ranks_busy_people_last(co: dict[str, Any]) -> None:
    w = co["who"]
    day = MON + timedelta(days=8)
    await ok(w["dpa"], "schedule.create", **_ev(co, day, "09:00", "10:00", "ra"))
    tbd = (await ok(w["dpa"], "schedule.create", **_ev(co, day, "10:00", "11:00", attendee_emails=[_email(co, "nv1")])))["event_id"]
    await ok(w["nv1"], "schedule.respond", event_id=tbd, answer="accepted")
    got = await q(w["dpa"], "schedule.find_time", attendee_emails=[_email(co, "nv1"), _email(co, "nv3")], resource_ids=[co["res"]["ra"]],
                  from_day=day.isoformat(), to_day=day.isoformat(), day_start="08:00", day_end="12:00", minutes=60)
    starts = {r["start"]: r for r in got["rows"]}
    assert "09:00" not in starts and "08:30" not in starts and "09:30" not in starts                     # phòng bận: không gợi ý
    assert starts["08:00"]["who"] == "Mọi người rảnh" and starts["11:00"]["free"]
    assert starts["10:00"]["who"] == "Bận: Người một"                                                    # người bận: vẫn gợi ý nhưng xếp sau
    assert [r["free"] for r in got["rows"]] == sorted([r["free"] for r in got["rows"]], reverse=True)
    assert starts["08:00"]["attendee_emails"] == [_email(co, "nv1"), _email(co, "nv3")]                  # bấm "Đặt giờ này" điền sẵn
    proposed = await executor.run(w["dpa"].as_agent(), "schedule.create", _ev(co, day, "08:00", "09:00", "ra", attendee_emails=starts["08:00"]["attendee_emails"]))
    assert proposed.status == "proposed"                                                                  # trợ lý: chỉ thẻ đề xuất
    assert await owner_val("SELECT count(*) FROM schedule.bookings WHERE resource_id = %s AND active AND lower(during) = %s",
                           (co["res"]["ra"], datetime.combine(day, time(8), VN))) == 0


async def test_kinds_are_company_data_with_defaults_from_packs(co: dict[str, Any]) -> None:
    w = co["who"]
    kinds = {r["key"]: r["label"] for r in (await q(w["admin"], "schedule.kinds"))["rows"]}
    assert kinds.get("meeting") == "Họp nội bộ"                                                           # bộ mặc định của gói Lịch
    await ok(w["admin"], "schedule.save_kind", key="meeting", label="Họp", sort=5)
    await ok(w["admin"], "schedule.save_kind", key="meeting", label="Không đè", only_new=True)
    assert await owner_val("SELECT label FROM schedule.kinds WHERE tenant_id = %s AND key = 'meeting'", (co["tid"],)) == "Họp"
    eid = (await ok(w["dpa"], "schedule.create", **_ev(co, MON + timedelta(days=9), "08:00", "08:30", "rb", kind="meeting")))["event_id"]
    assert next(r for r in (await q(w["dpa"], "schedule.week", week_of=(MON + timedelta(days=9)).isoformat()))["rows"] if r["id"] == eid)["kind_label"] == "Họp"
    assert (await executor.run(w["dpa"], "schedule.create", _ev(co, MON + timedelta(days=9), "09:00", "09:30", "rb", kind="khong_co"))).status == "not_found"
    assert (await executor.run(w["dpa"], "schedule.save_kind", {"key": "x", "label": "x"})).status == "denied"


# ── Khách hàng: hẹn trên lịch chung, sao chép hẹn cũ, trợ lý không tự đặt lên lịch người khác ──

SALES = [("admin", "Quản trị", "admin", "QT", True), ("tl", "Trưởng nhóm", "team_lead", "KD-A", True), ("s1", "Sale một", "sale", "KD-A", False),
         ("s2", "Sale hai", "sale", "KD-A", False)]


@pytest.fixture(scope="module")
async def sales() -> dict[str, Any]:
    domain = f"k{uuid.uuid4().hex[:6]}.demo"
    tid = await seed.create_tenant(f"Công ty khách {domain}", domain, people=SALES, packs=("crm", "meet"), about="công ty thử hẹn khách")
    return {"tid": tid, "domain": domain, "who": {k: await seed.who(tid, k, domain) for k, *_ in SALES}}


async def _lead(sale: Principal, name: str = "") -> str:
    return str((await ok(sale, "crm.create_lead", name=name or f"Khách {uuid.uuid4().hex[:6]}", phone=fresh_phone()))["lead_id"])


async def test_backfill_puts_every_existing_appointment_on_the_calendar_once(sales: dict[str, Any]) -> None:
    w, tid = sales["who"], sales["tid"]
    lead = await _lead(w["s1"], "Khách hẹn cũ")
    base = datetime.combine(today() + timedelta(days=2), time(9), VN)
    old = []
    async with db.owner_tx() as conn:   # hẹn kiểu cũ (trước 0158): chỉ có dòng crm.appointments, chưa có lịch
        for i, (kind, status) in enumerate((("meet", "scheduled"), ("visit", "done"), ("call", "cancelled"), ("meet", "missed"))):
            cur = await conn.execute("""INSERT INTO crm.appointments (tenant_id, lead_id, kind, at, status, closed_at, created_by)
                                        VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                                     (tid, lead, kind, base + timedelta(hours=i), status, None if status == "scheduled" else base, w["s1"].user_id))
            old.append((await cur.fetchone() or {})["id"])
    sql = (Path(schedule.__file__).parents[2] / "migrations" / "crm" / "069_appointments_on_calendar.sql").read_text()
    for _ in range(2):                                                                                    # chạy lại không tạo thêm
        async with db.owner_tx() as conn:
            await conn.execute(sql.encode())
    rows = await owner_val("""SELECT jsonb_object_agg(e.source_ref, jsonb_build_object('n', (SELECT count(*) FROM schedule.events x WHERE x.tenant_id = e.tenant_id
                                                                                                  AND x.source = e.source AND x.source_ref = e.source_ref),
                                                       'starts', e.starts_at = a.at, 'status', e.status, 'kind', e.kind, 'vis', e.visibility,
                                                       'who', (SELECT array_agg(rsvp) FROM schedule.attendees t WHERE t.event_id = e.id AND t.user_id = %s),
                                                       'party', (SELECT count(*) FROM schedule.event_parties p WHERE p.event_id = e.id)))
                              FROM schedule.events e JOIN crm.appointments a ON a.id::text = e.source_ref
                              WHERE e.tenant_id = %s AND e.source = 'crm.appointment' AND a.id = ANY(%s)""", (w["s1"].user_id, tid, old))
    assert set(rows) == {str(x) for x in old}                                                            # MỌI hẹn cũ có lịch
    assert all(r["n"] == 1 and r["starts"] and r["vis"] == "private" and r["who"] == ["accepted"] and r["party"] == 1 for r in rows.values()), rows
    assert [rows[str(x)]["status"] for x in old] == ["booked", "done", "cancelled", "done"]
    assert [rows[str(x)]["kind"] for x in old] == ["customer_meeting", "site_visit", "callback", "customer_meeting"]
    page = await q(w["s1"], "crm.agenda", days=7)                                                         # giờ hẹn đọc từ lịch chung
    assert any("Khách hẹn cũ" in i["title"] and i["badge"] == "09:00" for b in page["blocks"] if b.get("type") == "list" for i in b["items"])


async def test_appointments_live_on_the_calendar_and_follow_the_lead(sales: dict[str, Any]) -> None:
    w = sales["who"]
    lead = await _lead(w["s1"])
    day = today() + timedelta(days=5)
    out = await ok(w["s1"], "crm.schedule", lead_id=lead, kind="visit", day=day.isoformat(), time="15:00", place="Nhà mẫu")
    ev = await owner_val("""SELECT jsonb_build_object('kind', kind, 'vis', visibility, 'mins', extract(epoch FROM ends_at - starts_at) / 60, 'src', source_ref)
                            FROM schedule.events WHERE id = %s""", (out["event_id"],))
    assert ev == {"kind": "site_visit", "vis": "private", "mins": 90, "src": str(out["appointment_id"])}
    moved = await ok(w["s1"], "crm.move_appointment", appointment_id=out["appointment_id"], day=day.isoformat(), time="16:00")
    assert "16:00" in moved["summary"]
    assert await owner_val("SELECT to_char(starts_at AT TIME ZONE 'Asia/Ho_Chi_Minh', 'HH24:MI') || '/' || (extract(epoch FROM ends_at - starts_at) / 60)::int "
                           "FROM schedule.events WHERE id = %s", (out["event_id"],)) == "16:00/90"           # dời giữ độ dài
    await ok(w["tl"], "crm.assign", lead_id=lead, owner_email=w["s2"].email)
    assert await owner_val("SELECT array_agg(user_id) FROM schedule.attendees WHERE event_id = %s", (out["event_id"],)) == [w["s2"].user_id]
    await ok(w["s2"], "crm.close_appointment", appointment_id=out["appointment_id"], status="cancelled")
    assert await owner_val("SELECT status FROM schedule.events WHERE id = %s", (out["event_id"],)) == "cancelled"


async def test_the_assistant_never_books_onto_someone_elses_calendar_without_a_person(sales: dict[str, Any]) -> None:
    w, tid = sales["who"], sales["tid"]
    await owner_val("INSERT INTO core.agent_policies (tenant_id, command, mode, daily_cap) VALUES (%s, 'crm.schedule', 'allow', 50) RETURNING 1", (tid,))
    day = (today() + timedelta(days=6)).isoformat()
    own = await _lead(w["s1"])
    direct = await executor.run(w["s1"].as_agent(), "crm.schedule", {"lead_id": own, "kind": "meet", "day": day, "time": "09:00"}, attended=True)
    assert direct.status == "ok", direct                                                                  # công ty nới: việc của chính mình chạy thẳng
    other = await _lead(w["s1"])
    by_lead = await executor.run(w["tl"].as_agent(), "crm.schedule", {"lead_id": other, "kind": "meet", "day": day, "time": "10:00"}, attended=True)
    assert by_lead.status == "proposed", by_lead                                                          # lên lịch của sale khác ⇒ thẻ đề xuất
    assert await owner_val("SELECT count(*) FROM crm.appointments WHERE lead_id = %s", (other,)) == 0
    confirmed = await executor.run(w["tl"], "crm.schedule", by_lead.output["input"], attended=True)       # người bấm xác nhận
    assert confirmed.status == "ok" and await owner_val("SELECT count(*) FROM crm.appointments WHERE lead_id = %s", (other,)) == 1


async def test_a_video_call_is_a_meeting_on_the_calendar(sales: dict[str, Any]) -> None:
    w = sales["who"]
    lead = await _lead(w["s1"])
    started = await ok(w["s1"], "meet.start", subject_kind="crm.lead", subject_id=lead, join_url="https://meet.google.com/abc-defg-hij")
    ev = await owner_val("""SELECT jsonb_build_object('kind', e.kind, 'vis', e.visibility, 'status', e.status,
                                                      'who', (SELECT array_agg(a.rsvp) FROM schedule.attendees a WHERE a.event_id = e.id AND a.user_id = %s))
                            FROM schedule.events e WHERE e.source = 'meet.call' AND e.source_ref = %s""", (w["s1"].user_id, str(started["call_id"])))
    assert ev == {"kind": "meeting", "vis": "private", "status": "booked", "who": ["accepted"]}
    await ok(w["s1"], "meet.end", call_id=started["call_id"])
    assert await owner_val("SELECT ends_at - starts_at < interval '5 minutes' FROM schedule.events WHERE source = 'meet.call' AND source_ref = %s",
                           (str(started["call_id"]),))                                                    # kết thúc ⇒ đúng giờ đã gọi


async def test_deadlines_are_markers_not_events(sales: dict[str, Any]) -> None:
    w, tid = sales["who"], sales["tid"]
    lead = await _lead(w["s1"], "Khách cần gọi lại")
    soon = today() + timedelta(days=2)
    await ok(w["s1"], "crm.log_activity", lead_id=lead, kind="call", outcome="connected", next_follow_up=soon.isoformat())
    await ok(w["s1"], "flow.create_task", title="Gửi báo giá cho khách", due_on=(soon + timedelta(days=1)).isoformat())
    before = await owner_val("SELECT count(*) FROM schedule.events WHERE tenant_id = %s", (tid,))
    got = await q(w["admin"], "schedule.markers", from_day=today().isoformat(), to_day=(soon + timedelta(days=3)).isoformat(), mine=False)
    titles = {(r["label"], r["title"]) for r in got["rows"]}
    assert ("Gọi lại", "Gọi lại Khách cần gọi lại") in titles and ("Hạn việc", "Gửi báo giá cho khách") in titles
    assert await owner_val("SELECT count(*) FROM schedule.events WHERE tenant_id = %s", (tid,)) == before   # mốc hạn không thành lịch


# ── Nghỉ phép đã duyệt: khối "nghỉ" trên lịch người đó, chi tiết riêng tư ──

async def test_approved_leave_is_a_private_block_on_that_persons_calendar() -> None:
    co = await seed_pay.office(f"lv{uuid.uuid4().hex[:6]}.demo", "Văn phòng nghỉ phép (test)", runs=False, close=False)
    w = co["who"]
    w = {k: await seed.who(co["tenant_id"], k, co["domain"]) for k in ("chu", "nvbinh")}
    rid, aid = (await owner_val("""SELECT jsonb_build_array(id, approval_id) FROM timekeeping.leave_requests WHERE tenant_id = %s AND user_id = %s
                                   AND status = 'requested'""", (co["tenant_id"], w["nvbinh"].user_id)))
    approver = await owner_val("SELECT approver_ids[1] FROM core.approvals WHERE id = %s", (uuid.UUID(aid),))
    boss = await seed.who(co["tenant_id"], await owner_val("SELECT split_part(email, '@', 1) FROM core.users WHERE id = %s", (approver,)), co["domain"])
    assert (await executor.run(edge(boss), "core.decide", {"approval_id": aid, "approve": True})).status == "ok"
    ev = await owner_val("""SELECT jsonb_build_object('id', e.id, 'kind', e.kind, 'vis', e.visibility, 'title', e.title,
                                                      'who', (SELECT array_agg(a.rsvp) FROM schedule.attendees a WHERE a.event_id = e.id AND a.user_id = %s))
                            FROM schedule.events e WHERE e.source = 'timekeeping.leave' AND e.source_ref = %s""", (w["nvbinh"].user_id, rid))
    assert ev["kind"] == "leave" and ev["vis"] == "private" and ev["who"] == ["accepted"]
    week = await q(w["chu"], "schedule.week", week_of=co["friday"].isoformat(), member_email=w["nvbinh"].email)
    row = next(r for r in week["rows"] if r["id"] == ev["id"])
    assert row["title"] == "Bận" and ev["title"] not in str(week)                                        # người khác: chỉ "Bận"
    assert await owner_val("SELECT label FROM schedule.kinds WHERE tenant_id = %s AND key = 'leave'", (co["tenant_id"],)) == "Nghỉ"


# ── Bận / rảnh kiểu Teams: mặc định ai cũng thấy GIỜ bận của nhau; chi tiết chỉ người trong lịch + quyền schedule.details (chính sách) ──

async def test_invitations_are_tentative_until_accepted_and_declining_tells_the_organiser(co: dict[str, Any]) -> None:
    w = co["who"]
    day = MON + timedelta(days=11)
    eid = (await ok(w["dpa"], "schedule.create", **_ev(co, day, "15:00", "16:00", title="Họp chọn mẫu", attendee_emails=[_email(co, "nv1"), _email(co, "nv3")])))["event_id"]
    mine = next(r for r in (await q(w["nv1"], "schedule.week", week_of=day.isoformat()))["rows"] if r["id"] == eid)
    assert mine["status"] == "tentative" and mine["my_rsvp"] == "invited"                                # trên lịch của người được mời: "Chờ xác nhận"
    assert "Chờ xác nhận" in str((await q(w["dpa"], "schedule.event", event_id=eid))["blocks"])          # người tổ chức thấy từng người
    free = await q(w["hc"], "schedule.find_free", day=day.isoformat(), start="15:00", end="16:00", kind="nhân sự")
    assert "Người một" in {r["name"] for r in free["rows"]}                                              # chưa nhận ⇒ không tính là bận
    await ok(w["nv1"], "schedule.respond", event_id=eid, answer="accepted")
    free = await q(w["hc"], "schedule.find_free", day=day.isoformat(), start="15:00", end="16:00", kind="nhân sự")
    assert "Người một" not in {r["name"] for r in free["rows"]}                                          # đã nhận ⇒ bận
    assert next(r for r in (await q(w["nv1"], "schedule.my_agenda", days=60))["rows"] if r["id"] == eid)["my_rsvp"] == "accepted"
    await ok(w["nv3"], "schedule.respond", event_id=eid, answer="declined")
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND title LIKE 'Người ba: không tham dự — Họp chọn mẫu%%'",
                           (w["dpa"].user_id,)) == 1
    other = await q(w["dpc"], "schedule.week", week_of=day.isoformat(), member_email=_email(co, "nv3"))
    assert eid not in {r["id"] for r in other["rows"]}                                                   # từ chối ⇒ không còn trong bận của người ba


async def test_sales_see_when_colleagues_are_busy_not_what_and_team_leads_see_their_team(sales: dict[str, Any]) -> None:
    w = sales["who"]
    lead = await _lead(w["s1"], "Khách bí mật")
    day = today() + timedelta(days=8)
    while day.isoweekday() > 5:   # find_time bỏ thứ Bảy / Chủ nhật — "+8 ngày" rơi cuối tuần (chạy thứ Năm / thứ Sáu) làm test hỏng theo ngày
        day += timedelta(days=1)
    appt = await ok(w["s1"], "crm.schedule", lead_id=lead, kind="meet", day=day.isoformat(), time="10:00")
    meeting = (await ok(w["admin"], "schedule.create", title="Họp đội tuần", day=day.isoformat(), start="14:00", end="15:00", team_code="KD-A",
                        attendee_emails=[w["s1"].email]))["event_id"]
    peer = await q(w["s2"], "schedule.week", week_of=day.isoformat(), member_email=w["s1"].email)   # sale khác: chỉ giờ bận
    rows = {r["id"]: r for r in peer["rows"]}
    assert rows[appt["event_id"]]["title"] == "Bận" and rows[meeting]["busy"] and "Khách bí mật" not in str(peer) and "Họp đội" not in str(peer)
    assert (await q(w["s2"], "schedule.event", event_id=appt["event_id"]))["title"] == "Bận"
    found = await q(w["s2"], "schedule.find_time", attendee_emails=[w["s1"].email], from_day=day.isoformat(), to_day=day.isoformat(),
                    day_start="10:00", day_end="11:00", minutes=60)
    assert [r["who"] for r in found["rows"]] == ["Bận: Sale một"]
    lead_view = await q(w["tl"], "schedule.week", week_of=day.isoformat(), member_email=w["s1"].email)   # trưởng nhóm: chi tiết lịch thường của đội
    rows = {r["id"]: r for r in lead_view["rows"]}
    assert rows[meeting]["title"] == "Họp đội tuần" and rows[appt["event_id"]]["title"] == "Bận"        # hẹn khách riêng tư: vẫn chỉ "Bận"
    assert await _sql_count(w["s2"], "SELECT count(*) AS n FROM schedule.events WHERE id = %s", (meeting,)) == 0
    assert await owner_val("""SELECT array_agg(role || ':' || resource || ':' || scope ORDER BY role, resource) FROM core.grants
                              WHERE tenant_id = %s AND resource IN ('schedule', 'schedule.details') AND role IN ('sale', 'team_lead')""", (sales["tid"],)) == \
        ["sale:schedule:own", "team_lead:schedule:team", "team_lead:schedule.details:team"]                 # chính sách là dữ liệu: quản trị sửa được


async def test_new_appointments_do_not_write_the_deprecated_at_copy_and_the_sweep_leaves_them_alone(sales: dict[str, Any]) -> None:
    """crm/074: ``crm.appointments.at`` ngừng dùng — đặt / dời hẹn để rỗng, giờ chỉ ở lịch; vòng quét không động tới hẹn không có ``at``."""
    w, tid = sales["who"], sales["tid"]
    lead = await _lead(w["s1"], "Khách không bản chép giờ")
    day = today() + timedelta(days=6)
    out = await ok(w["s1"], "crm.schedule", lead_id=lead, kind="meet", day=day.isoformat(), time="10:00")
    await ok(w["s1"], "crm.move_appointment", appointment_id=out["appointment_id"], day=day.isoformat(), time="11:30")
    assert await owner_val("SELECT at IS NULL FROM crm.appointments WHERE id = %s", (out["appointment_id"],)) is True
    assert await owner_val("SELECT to_char(starts_at AT TIME ZONE 'Asia/Ho_Chi_Minh', 'HH24:MI') FROM schedule.events WHERE id = %s", (out["event_id"],)) == "11:30"
    # dòng không có ``at`` lẫn không có lịch (không thể xảy ra từ code mới) ⇒ quét bỏ qua, không đặt lịch theo giờ đoán
    async with db.owner_tx() as conn:
        orphan = (await (await conn.execute("INSERT INTO crm.appointments (tenant_id, lead_id, kind, created_by) VALUES (%s, %s, 'call', %s) RETURNING id",
                                            (tid, lead, w["s1"].user_id))).fetchone() or {})["id"]
    r = await executor.run(system_principal(tid), "crm.heal_appointments", {})
    assert r.ok, r
    assert await owner_val("SELECT count(*) FROM schedule.events WHERE tenant_id = %s AND source = 'crm.appointment' AND source_ref = %s", (tid, str(orphan))) == 0
    assert await owner_val("SELECT count(*) FROM schedule.events WHERE id = %s AND status = 'booked'", (out["event_id"],)) == 1


async def test_appointments_written_by_the_old_version_get_their_event_on_the_next_sweep(sales: dict[str, Any]) -> None:
    w, tid = sales["who"], sales["tid"]
    lead = await _lead(w["s2"], "Khách lúc chuyển")
    at = datetime.combine(today() + timedelta(days=4), time(11), VN)
    async with db.owner_tx() as conn:   # bản cũ còn chạy sau migration: chỉ ghi crm.appointments
        new = (await (await conn.execute("""INSERT INTO crm.appointments (tenant_id, lead_id, kind, at, created_by) VALUES (%s, %s, 'visit', %s, %s)
                                            RETURNING id""", (tid, lead, at, w["s2"].user_id))).fetchone() or {})["id"]
        gone = (await (await conn.execute("""INSERT INTO crm.appointments (tenant_id, lead_id, kind, at, status, closed_at, created_by)
                                             VALUES (%s, %s, 'meet', %s, 'cancelled', now(), %s) RETURNING id""",
                                          (tid, lead, at + timedelta(hours=3), w["s2"].user_id))).fetchone() or {})["id"]
    for _ in range(2):
        r = await executor.run(system_principal(tid), "crm.heal_appointments", {})
        assert r.ok, r
    assert await owner_val("""SELECT jsonb_object_agg(source_ref, status || '|' || (starts_at = %s)::text) FROM schedule.events
                              WHERE tenant_id = %s AND source = 'crm.appointment' AND source_ref = ANY(%s)""", (at, tid, [str(new), str(gone)])) == \
        {str(new): "booked|true", str(gone): "cancelled|false"}                                         # đúng một lịch mỗi hẹn, chạy lại không thêm
    assert any("Khách lúc chuyển" in i["title"] for b in (await q(w["s2"], "crm.agenda", days=7))["blocks"] if b.get("type") == "list" for i in b["items"])


async def test_lowering_slots_lists_the_times_that_would_overflow(co: dict[str, Any]) -> None:
    w = co["who"]
    rid = (await ok(w["admin"], "schedule.add_resource", name="Bàn chung B", nature="thing", kind="chỗ ngồi", team_code="KD-A", slots=3))["resource_id"]
    day = MON + timedelta(days=12)
    for start in ("09:00", "09:15", "09:30"):
        await ok(w["dpa"], "schedule.create", title="Ngồi", day=day.isoformat(), start=start, end="10:00", team_code="KD-A", resource_ids=[rid])
    args = {"resource_id": rid, "name": "Bàn chung B", "nature": "thing", "kind": "chỗ ngồi", "team_code": "KD-A"}
    r = await executor.run(w["admin"], "schedule.update_resource", {**args, "slots": 2})
    assert r.status == "conflict" and "xuống 2" in r.error and "tới 3 lịch" in r.error and f"09:30–10:00 {day:%d/%m}" in r.error, r.error
    await ok(w["admin"], "schedule.update_resource", **args, slots=3)                                    # không giảm: được
    assert (await executor.run(w["admin"], "schedule.update_resource", {**args, "slots": 1})).status == "conflict"
