"""0113: năng lực Lịch & nguồn lực — không trùng lịch do DB chặn (kể cả đồng thời), dời vào chỗ trùng, giờ rảnh theo tuần (biên, qua đêm), giờ Việt
Nam khi hiện / timestamptz khi lưu, quyền (người được xếp lịch thấy lịch của mình dù phạm vi hẹp), tắt gói, hợp đồng đặt / nhả cho module khác."""

import ast
import asyncio
import dataclasses
import re
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest

from barista import seed, workspaces
from barista.agent import desk
from barista.db import db
from barista.kernel import executor, registry
from barista.kernel.clock import VN, today
from barista.kernel.errors import Conflict, Denied, Invalid
from barista.kernel.executor import core_ctx
from barista.kernel.principal import Principal
from barista.modules import schedule
from barista.modules.schedule import fits, minutes
from barista.workspaces import manifest
from tests.conftest import ok, owner_val, q

PEOPLE = [("admin", "Quản trị", "admin", "QT", True), ("dpa", "Điều phối A", "coordinator", "KD-A", True),
          ("dpc", "Điều phối C", "coordinator", "KD-C", True), ("nv1", "Người một", "staff", "KD-A", False),
          ("nv2", "Người hai", "staff", "KD-C", False), ("nv3", "Người ba", "staff", "KD-A", False), ("hc", "Hành chính", "office", "HCNS", True)]
MON = date.fromordinal(today().toordinal() - today().weekday() + 21)   # thứ Hai sau ba tuần: luôn ở tương lai, không đụng hôm nay


@pytest.fixture(scope="module")
async def co() -> dict[str, Any]:
    domain = f"l{uuid.uuid4().hex[:6]}.demo"
    tid = await seed.create_tenant(f"Công ty lịch {domain}", domain, people=PEOPLE, packs=("schedule",), about="công ty thử lịch")
    await owner_val("INSERT INTO core.grants (tenant_id, role, resource, scope, actions) VALUES (%s, 'coordinator', 'schedule', 'team', '{read,write}') RETURNING 1",
                    (tid,))
    who = {k: await seed.who(tid, k, domain) for k, *_ in PEOPLE}
    admin = who["admin"]
    res = {}
    for key, name, nature, kind, tags, team, email in (
            ("p1", "Người một", "person", "nhân sự", "tiếng nhật, sơ cứu", "KD-A", f"nv1@{domain}"),
            ("p2", "Người hai", "person", "nhân sự", "tiếng nhật", "KD-C", f"nv2@{domain}"),
            ("p3", "Người ba", "person", "nhân sự", "tiếng nhật", "KD-A", f"nv3@{domain}"),
            ("ra", "Phòng A1", "thing", "phòng", "máy chiếu", "KD-A", ""), ("rb", "Phòng A2", "thing", "phòng", "", "KD-A", ""),
            ("rc", "Phòng C1", "thing", "phòng", "", "KD-C", "")):
        res[key] = (await ok(admin, "schedule.add_resource", name=name, nature=nature, kind=kind, tags=tags, capacity=12, team_code=team,
                             user_email=email))["resource_id"]
    return {"tid": tid, "domain": domain, "who": who, "res": res}


def _ev(co: dict[str, Any], day: date, start: str, end: str, *keys: str, team: str = "KD-A", **kw: Any) -> dict[str, Any]:
    return {"title": f"Việc {uuid.uuid4().hex[:5]}", "day": day.isoformat(), "start": start, "end": end, "team_code": team,
            "resource_ids": [co["res"][k] for k in keys], **kw}


async def _make(p: Principal, co: dict[str, Any], day: date, start: str, end: str, *keys: str, **kw: Any) -> str:
    return (await ok(p, "schedule.create", **_ev(co, day, start, end, *keys, **kw)))["event_id"]


def test_availability_edges_are_half_open_and_cross_midnight() -> None:
    mon = datetime.combine(MON, time(0), VN)
    week = {1: [(8 * 60, 12 * 60), (13 * 60, 17 * 60), (22 * 60, 1440)], 2: [(0, 2 * 60)]}
    assert fits(week, mon + timedelta(hours=8), mon + timedelta(hours=12))                              # đúng biên
    assert not fits(week, mon + timedelta(hours=7, minutes=59), mon + timedelta(hours=9))
    assert not fits(week, mon + timedelta(hours=11, minutes=30), mon + timedelta(hours=12, minutes=30))  # rơi vào giờ nghỉ trưa
    assert fits(week, mon + timedelta(hours=23), mon + timedelta(hours=25))                             # qua đêm, nối hai ngày
    assert not fits(week, mon + timedelta(hours=23), mon + timedelta(hours=26, minutes=1))
    assert not fits(week, mon + timedelta(days=2, hours=9), mon + timedelta(days=2, hours=10))          # thứ Tư không khai giờ rảnh
    assert fits({}, mon, mon + timedelta(days=3))                                                         # không khai = không giới hạn
    assert fits({1: [(8 * 60, 10 * 60), (10 * 60, 12 * 60)]}, mon + timedelta(hours=9), mon + timedelta(hours=11))   # hai khung nối nhau
    utc = (mon + timedelta(hours=9)).astimezone(UTC)
    assert fits(week, utc, utc + timedelta(hours=1))                                                     # giờ UTC vẫn xét theo giờ Việt Nam
    assert minutes("24:00", end=True) == 1440
    with pytest.raises(Invalid):
        minutes("24:00")


async def test_no_double_booking_and_back_to_back_is_fine(co: dict[str, Any]) -> None:
    dpa = co["who"]["dpa"]
    await _make(dpa, co, MON, "08:00", "09:00", "p1", "ra")
    r = await executor.run(dpa, "schedule.create", _ev(co, MON, "08:30", "09:30", "p1", "rb"))
    assert r.status == "conflict" and "Người một" in r.error and "Phòng A2" not in r.error, r.error
    await _make(dpa, co, MON, "09:00", "10:00", "p1", "ra")                                             # nối tiếp đúng giờ: không trùng
    assert (await executor.run(dpa.as_agent(), "schedule.create", _ev(co, MON, "10:00", "11:00", "p1"))).status == "proposed"


async def test_the_database_refuses_two_simultaneous_bookings(co: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Bỏ bước kiểm trước (giả như hai yêu cầu cùng lọt qua) và làm chậm để hai giao dịch chồng nhau: ràng buộc loại trừ vẫn chỉ cho một bên."""
    async def blind(*_: Any, **__: Any) -> set[Any]:
        await asyncio.sleep(0.3)
        return set()

    monkeypatch.setattr(schedule, "_busy", blind)
    dpa, admin = co["who"]["dpa"], co["who"]["admin"]
    day = MON + timedelta(days=1)
    a, b = await asyncio.gather(executor.run(dpa, "schedule.create", _ev(co, day, "14:00", "15:00", "p3", "ra")),
                                executor.run(admin, "schedule.create", _ev(co, day, "14:30", "15:30", "p3", "rb")))
    assert sorted([a.status, b.status]) == ["conflict", "ok"], (a, b)
    assert "đặt trùng" in (a.error or b.error)
    # hai bên giữ hai nguồn lực theo thứ tự ngược nhau: vẫn một bên thắng, không lỗi hệ thống (khoá chéo)
    c, d = await asyncio.gather(executor.run(dpa, "schedule.create", _ev(co, day, "16:00", "17:00", "ra", "rb")),
                                executor.run(admin, "schedule.create", _ev(co, day, "16:00", "17:00", "rb", "ra")))
    assert sorted([c.status, d.status]) == ["conflict", "ok"], (c, d)
    async def held(key: str, h1: int, h2: int) -> int:
        return await owner_val("""SELECT count(*) FROM schedule.bookings WHERE tenant_id = %s AND active AND resource_id = %s AND during && tstzrange(%s, %s)""",
                               (co["tid"], co["res"][key], datetime.combine(day, time(h1), VN), datetime.combine(day, time(h2), VN)))
    assert await held("p3", 14, 16) == 1 and await held("ra", 16, 17) == 1 and await held("rb", 16, 17) == 1   # đúng một bên giữ chỗ


async def test_moving_onto_a_conflict_is_refused_and_bookings_follow_the_event(co: dict[str, Any]) -> None:
    dpa = co["who"]["dpa"]
    day = MON + timedelta(days=2)
    first = await _make(dpa, co, day, "08:00", "09:00", "p1", "ra")
    second = await _make(dpa, co, day, "10:00", "11:00", "p1", "rb")
    r = await executor.run(dpa, "schedule.move", {"event_id": second, "day": day.isoformat(), "start": "08:30", "end": "09:30"})
    assert r.status == "conflict" and "Người một" in r.error
    await ok(dpa, "schedule.move", event_id=second, day=day.isoformat(), start="13:00", end="14:00")
    assert await owner_val("SELECT array_agg(DISTINCT lower(during)) FROM schedule.bookings WHERE event_id = %s", (second,)) == \
        [datetime.combine(day, time(13), VN)]
    await ok(dpa, "schedule.move", event_id=first, day=day.isoformat(), start="10:00", end="11:00")   # chỗ cũ của lịch kia đã trống
    await ok(dpa, "schedule.cancel", event_id=first, reason="Đổi kế hoạch")
    assert await owner_val("SELECT bool_or(active) FROM schedule.bookings WHERE event_id = %s", (first,)) is False
    await _make(dpa, co, day, "10:00", "11:00", "p1", "ra")                                           # huỷ ⇒ nhả chỗ
    assert (await executor.run(dpa, "schedule.move", {"event_id": first, "day": day.isoformat(), "start": "15:00", "end": "16:00"})).status == "invalid"


async def test_weekly_availability_is_respected_with_an_explicit_override(co: dict[str, Any]) -> None:
    admin, dpa = co["who"]["admin"], co["who"]["dpa"]
    await ok(admin, "schedule.set_availability", resource_id=co["res"]["p3"],
             windows=[{"weekday": 4, "start": "08:00", "end": "12:00"}, {"weekday": 4, "start": "22:00", "end": "24:00"},
                      {"weekday": 5, "start": "00:00", "end": "02:00"}])
    thu = MON + timedelta(days=3)
    await _make(dpa, co, thu, "08:00", "12:00", "p3")
    r = await executor.run(dpa, "schedule.create", _ev(co, thu, "12:00", "12:30", "p3"))
    assert r.status == "invalid" and "Ngoài giờ rảnh" in r.error
    await _make(dpa, co, thu, "12:00", "12:30", "p3", allow_outside=True)
    await _make(dpa, co, thu, "23:00", "01:00", "p3", end_day=(thu + timedelta(days=1)).isoformat())   # qua đêm trong hai khung nối nhau
    free = await q(dpa, "schedule.find_free", day=(thu + timedelta(days=1)).isoformat(), start="09:00", end="10:00", kind="nhân sự")
    assert "Người ba" not in str(free["rows"]) and "Ngoài giờ rảnh đã khai: Người ba" in free["note"]
    bad = await executor.run(admin, "schedule.set_availability", {"resource_id": co["res"]["p3"], "windows": [{"weekday": 1, "start": "10:00", "end": "09:00"}]})
    assert bad.status == "invalid"


async def test_vietnam_time_for_display_tz_aware_storage(co: dict[str, Any]) -> None:
    dpa = co["who"]["dpa"]
    eid = await _make(dpa, co, MON, "00:30", "01:30", "rb")   # 00:30 thứ Hai giờ VN = 17:30 Chủ nhật UTC
    assert await owner_val("SELECT to_char(starts_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI') FROM schedule.events WHERE id = %s", (eid,)) == \
        f"{MON - timedelta(days=1):%Y-%m-%d} 17:30"
    this = await q(dpa, "schedule.week", week_of=MON.isoformat())
    row = next(r for r in this["rows"] if r["id"] == eid)
    assert row["day"] == MON.isoformat() and row["when"] == "00:30–01:30" and row["starts_at"].endswith("+07:00")
    assert this["views"]["calendar"] == {"date": "day", "title": "title", "subtitle": "line", "overdue": False}
    before = await q(dpa, "schedule.week", week_of=(MON - timedelta(days=1)).isoformat())
    assert all(r["id"] != eid for r in before["rows"])


async def test_who_can_cover_in_three_hours_then_replace_with_a_trail(co: dict[str, Any]) -> None:
    """Người phụ trách báo nghỉ: tìm người cùng thẻ, cùng loại, rảnh đúng giờ đó; đổi; người cũ được báo; dòng cũ còn làm vết."""
    dpa, admin = co["who"]["dpa"], co["who"]["admin"]
    day = MON + timedelta(days=10)                                                                      # thứ Năm: trong giờ rảnh của người ba
    eid = await _make(dpa, co, day, "09:00", "10:30", "p1", "ra")
    await _make(admin, co, day, "09:30", "10:00", "p2", team="KD-C")                                  # người hai bận đúng lúc đó
    free = await q(admin, "schedule.find_free", day=day.isoformat(), start="09:00", end="10:30", kind="nhân sự", tags="tiếng nhật", except_event_id=eid)
    names = {r["name"] for r in free["rows"]}
    assert names == {"Người ba"}, names                                                                 # không có người một (đang ở lịch), không người hai (bận)
    out = await ok(dpa, "schedule.replace_resource", event_id=eid, resource_id=co["res"]["p1"], new_resource_id=co["res"]["p3"], reason="Nghỉ ốm")
    assert "Người ba thay Người một" in out["summary"]
    trail = await owner_val("SELECT release_reason || '|' || (released_by IS NOT NULL)::text FROM schedule.bookings WHERE event_id = %s AND released_at IS NOT NULL",
                            (eid,))
    assert trail == "Nghỉ ốm|true"
    nv1 = co["who"]["nv1"]
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND title LIKE 'Không còn trong lịch%%'", (nv1.user_id,)) == 1
    assert eid not in {r["id"] for r in (await q(nv1, "schedule.my_agenda", days=60))["rows"]}
    assert eid in {r["id"] for r in (await q(co["who"]["nv3"], "schedule.my_agenda", days=60))["rows"]}
    assert "Nghỉ ốm" in str(await q(dpa, "schedule.event", event_id=eid))                              # người quản lý lịch thấy vết
    nv3 = co["who"]["nv3"]                                                                              # phạm vi "của mình", đang ở lịch này
    assert "Nghỉ ốm" not in str(await q(nv3, "schedule.event", event_id=eid))                           # không đọc được vì sao đồng nghiệp bị đổi ra
    async with db.tenant_tx(nv3, read_only=True) as conn:
        cur = await conn.execute("SELECT count(*) AS n FROM schedule.bookings WHERE event_id = %s AND released_at IS NOT NULL", (eid,))
        assert (await cur.fetchone() or {})["n"] == 0
    gone = await q(nv1, "schedule.event", event_id=eid)                                                 # đã bị đổi ra: lịch không còn là của mình
    assert gone["title"] == "Bận" and "Nghỉ ốm" not in str(gone) and "Phòng A1" not in str(gone)       # 0158: chỉ còn khối "Bận", không chi tiết
    assert await owner_val("""SELECT payload->>'change' FROM core.events WHERE type = 'schedule.booking_changed' AND payload->>'event_id' = %s""",
                           (eid,)) == "resource_replaced"                                               # MỘT sự kiện cho module nguồn nghe


async def test_narrow_scope_sees_own_bookings_and_room_names_but_nothing_else(co: dict[str, Any]) -> None:
    dpa, dpc, nv1 = co["who"]["dpa"], co["who"]["dpc"], co["who"]["nv1"]
    day = MON + timedelta(days=6)
    mine = await _make(dpa, co, day, "09:00", "10:00", "p1", "ra")
    other = await _make(dpc, co, day, "09:00", "10:00", "p2", "rc", team="KD-C")
    agenda = await q(nv1, "schedule.my_agenda", days=60)
    row = next(r for r in agenda["rows"] if r["id"] == mine)
    assert "Phòng A1" in row["resources"]                                                               # thấy phòng của lịch mình dù không xem được danh mục phòng
    week = await q(nv1, "schedule.week", week_of=day.isoformat())
    assert mine in {r["id"] for r in week["rows"]} and other not in {r["id"] for r in week["rows"]}
    assert all(r["name"] == "Người một" for r in (await q(nv1, "schedule.resources"))["rows"])
    assert (await executor.run(nv1, "schedule.create", _ev(co, day, "11:00", "12:00", "p1"))).status == "denied"
    assert (await executor.run(nv1, "schedule.cancel", {"event_id": mine})).status in ("denied", "not_found")
    # phạm vi đội: thấy / ghi đội mình, không đội khác
    assert other not in {r["id"] for r in (await q(dpa, "schedule.week", week_of=day.isoformat()))["rows"]}
    r = await executor.run(dpa, "schedule.create", _ev(co, day, "13:00", "14:00", "rc", team="KD-C"))
    assert r.status == "denied" and "đội mình quản lý" in r.error
    r = await executor.run(dpa, "schedule.create", _ev(co, day, "13:00", "14:00", "ra", "rc"))
    assert r.status == "not_found"                                                                      # phòng của đội khác: ngoài phạm vi
    assert (await executor.run(dpa, "schedule.cancel", {"event_id": other})).status == "not_found"
    async with db.tenant_tx(nv1, read_only=True) as conn:                                              # RLS là lưới cuối, kể cả SQL thẳng
        assert (await (await conn.execute("SELECT count(*) AS n FROM schedule.events WHERE id = %s", (other,))).fetchone() or {})["n"] == 0
    assert (await executor.query(co["who"]["hc"], "schedule.week", {})).ok                             # hành chính: cả công ty


async def test_screens_open_and_the_desk_line_comes_from_the_agenda(co: dict[str, Any], tomorrow_is_today: Callable[[], date]) -> None:
    for k in ("admin", "dpa", "nv1", "hc"):
        p = co["who"][k]
        spaces = {w["key"]: w for w in manifest(p, "x")["workspaces"]}
        assert "schedule" in spaces, k
        assert spaces["schedule"]["title"] == "Lịch làm việc"
        for sc in spaces["schedule"]["screens"]:
            r = await executor.query(p, sc["query"], {})
            assert r.ok, (k, sc["query"], r.error)
        await desk.attention(p)
    assert any(s.key == "schedule_agenda" for _, s in workspaces.desk_screens())
    start = datetime.combine(tomorrow_is_today(), time(10), VN)   # "hôm nay" = ngày mai thật ⇒ lịch 10:00 luôn trọn trong hôm nay, không phụ thuộc giờ chạy test
    nv3 = co["who"]["nv3"]
    await _make(co["who"]["dpa"], co, today(), f"{start:%H:%M}", f"{start + timedelta(minutes=30):%H:%M}", "p3", allow_outside=True)
    line = schedule._say_today(await q(nv3, "schedule.my_agenda"))
    assert line and line["title"].startswith("Hôm nay có") and f"{start:%H:%M}" in line["detail"]


async def test_contract_books_and_releases_only_the_callers_own_subject(co: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Hình của hợp đồng (bên gọi thật ngoài lõi là gói khai consumes("booking") — vd gói Đào tạo ở repo gói, test của gói): test cấp hợp đồng tạm cho một module giả để thử riêng phía lịch."""
    admin = co["who"]["admin"]
    s = datetime.combine(MON + timedelta(days=4), time(18), VN)
    args = {"source": "demo.session", "source_ref": f"S-{uuid.uuid4().hex[:6]}", "title": "Buổi thử", "starts_at": s, "ends_at": s + timedelta(hours=1),
            "resource_ids": [co["res"]["p1"], co["res"]["ra"]]}
    async with db.tenant_tx(admin) as conn:
        with pytest.raises(Denied, match="hợp đồng"):
            await core_ctx(conn, admin, module="demo").call_system("schedule.book", **args)                 # module ngoài hợp đồng ⇒ không gọi được
    for name in ("schedule.book", "schedule.release"):
        monkeypatch.setitem(registry.OPS, name, dataclasses.replace(registry.OPS[name], callers=("demo",), why="thử hình hợp đồng"))
    async with db.tenant_tx(admin) as conn:
        ctx = core_ctx(conn, admin, module="demo")
        first = await ctx.call_system("schedule.book", **args)
        again = await ctx.call_system("schedule.book", **args)
        assert first["created"] and not again["created"] and again["event_id"] == first["event_id"]   # gọi lại = cùng lịch
        moved = await ctx.call_system("schedule.book", **{**args, "starts_at": s + timedelta(hours=1), "ends_at": s + timedelta(hours=2),
                                                          "resource_ids": [co["res"]["p1"]]})
        assert moved["event_id"] == first["event_id"]
        with pytest.raises(Denied, match="thuộc module other"):
            await ctx.call_system("schedule.book", **{**args, "source": "other.session"})
        assert await ctx.call_system("schedule.release", source="demo.session", source_ref=args["source_ref"]) == {"released": 1}
        assert await ctx.call_system("schedule.release", source="demo.session", source_ref=args["source_ref"]) == {"released": 0}
        assert ctx.events == []                                                                          # việc của chính module nguồn: không báo ngược lại
    rows = await owner_val("""SELECT jsonb_agg(jsonb_build_object('r', resource_name, 'active', active, 'released', released_at IS NOT NULL) ORDER BY resource_name)
                              FROM schedule.bookings WHERE event_id = %s""", (first["event_id"],))
    assert rows == [{"r": "Người một", "active": False, "released": False}, {"r": "Phòng A1", "active": False, "released": True}]


def _grant_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("schedule.book", "schedule.release"):
        monkeypatch.setitem(registry.OPS, name, dataclasses.replace(registry.OPS[name], callers=("demo",), why="thử hình hợp đồng"))


async def test_a_conflicting_book_leaves_nothing_half_changed_when_the_caller_carries_on(co: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Review 07/11: module gọi bắt Conflict rồi làm tiếp trong cùng giao dịch ⇒ lịch phải y như trước (không nhả / dời một nửa)."""
    _grant_demo(monkeypatch)
    admin, dpa = co["who"]["admin"], co["who"]["dpa"]
    day = MON + timedelta(days=15)
    t1, t2 = datetime.combine(day, time(8), VN), datetime.combine(day, time(13), VN)
    await _make(dpa, co, day, "13:00", "14:00", "ra")                                                  # phòng A1 đã bận lúc 13:00
    ref, fresh = f"S-{uuid.uuid4().hex[:6]}", f"S-{uuid.uuid4().hex[:6]}"
    base = {"source": "demo.session", "title": "Buổi thử"}
    async with db.tenant_tx(admin) as conn:
        ctx = core_ctx(conn, admin, module="demo")
        eid = (await ctx.call_system("schedule.book", **{**base, "source_ref": ref, "starts_at": t1, "ends_at": t1 + timedelta(hours=1),
                                                          "resource_ids": [co["res"]["p1"], co["res"]["rb"]]}))["event_id"]
        with pytest.raises(Conflict):   # dời sang 13:00, bỏ phòng A2, thêm phòng A1 đang bận: nhả + dời chạy trước rồi mới vấp
            await ctx.call_system("schedule.book", **{**base, "source_ref": ref, "starts_at": t2, "ends_at": t2 + timedelta(hours=1),
                                                       "resource_ids": [co["res"]["p1"], co["res"]["ra"]]})
        with pytest.raises(Conflict):   # lịch mới: người một rảnh, phòng A1 bận ⇒ không để lại sự kiện mồ côi
            await ctx.call_system("schedule.book", **{**base, "source_ref": fresh, "starts_at": t2, "ends_at": t2 + timedelta(hours=1),
                                                       "resource_ids": [co["res"]["p1"], co["res"]["ra"]]})
        await conn.execute("SELECT 1")                                                                   # giao dịch của bên gọi vẫn dùng được
    assert await owner_val("SELECT starts_at FROM schedule.events WHERE id = %s", (eid,)) == t1
    assert await owner_val("SELECT array_agg(resource_name ORDER BY resource_name) FROM schedule.bookings WHERE event_id = %s AND active", (eid,)) == \
        ["Người một", "Phòng A2"]
    assert await owner_val("SELECT count(*) FROM schedule.events WHERE source_ref = %s", (fresh,)) == 0


async def test_two_simultaneous_books_of_one_subject_make_one_event(co: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    _grant_demo(monkeypatch)
    admin = co["who"]["admin"]
    s = datetime.combine(MON + timedelta(days=16), time(9), VN)
    args = {"source": "demo.session", "source_ref": f"S-{uuid.uuid4().hex[:6]}", "title": "Buổi đồng thời", "starts_at": s, "ends_at": s + timedelta(hours=1),
            "resource_ids": [co["res"]["p3"]], "allow_outside": True}

    async def one() -> dict[str, Any]:
        async with db.tenant_tx(admin) as conn:
            out = await core_ctx(conn, admin, module="demo").call_system("schedule.book", **args)
            await asyncio.sleep(0.2)   # giữ giao dịch mở để lần kia phải chờ thật
            return out

    a, b = await asyncio.gather(one(), one())
    assert a["event_id"] == b["event_id"] and sorted([a["created"], b["created"]]) == [False, True]
    assert await owner_val("SELECT count(*) FROM schedule.events WHERE source_ref = %s", (args["source_ref"],)) == 1


async def test_the_pack_is_always_on_so_it_cannot_be_turned_off_even_with_no_bookings() -> None:
    """0157: Lịch & nguồn lực luôn bật ở mọi công ty (như gói nền) — tắt bị từ chối, lịch và dữ liệu giữ nguyên."""
    domain = f"o{uuid.uuid4().hex[:6]}.demo"
    tid = await seed.create_tenant(f"Công ty tắt lịch {domain}", domain, people=PEOPLE[:1], packs=("schedule",), about="thử tắt")
    admin = await seed.who(tid, "admin", domain)
    rid = (await ok(admin, "schedule.add_resource", name="Phòng X", nature="thing", kind="phòng", team_code="QT"))["resource_id"]
    await ok(admin, "schedule.create", title="Họp", day=MON.isoformat(), start="09:00", end="10:00", resource_ids=[rid], team_code="QT")
    r = await executor.run(admin, "admin.set_pack", {"pack": "schedule", "enabled": False})
    assert r.status == "invalid" and "luôn bật cho mọi công ty" in r.error
    admin = await seed.who(tid, "admin", domain)
    assert (await executor.query(admin, "schedule.week", {})).status == "ok"
    assert "schedule" in {w["key"] for w in manifest(admin, "x")["workspaces"]}
    assert await owner_val("SELECT count(*) FROM schedule.events WHERE tenant_id = %s", (tid,)) == 1


def test_schedule_speaks_no_industry_words_and_hub_files_stay_untouched() -> None:
    words = re.compile(r"giáo viên|học sinh|học viên|lớp học|buổi học|bàn ăn|ca làm|bệnh nhân|khám|khách hàng|hẹn khách|nghỉ phép", re.I)
    for src in sorted(Path(schedule.__file__).parent.glob("*.py")):   # cả package (0158: __init__, calendar, rrule)
        tree = ast.parse(src.read_text())
        doc = id(tree.body[0].value) if isinstance(tree.body[0], ast.Expr) else None
        bad = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) != doc and words.search(n.value)]
        assert not bad, (src.name, bad)
    assert registry.pack_kind("schedule") == "capability" and registry.MODULE_PACK["schedule"] == "schedule"
    assert "parties.end_guardians_of_age" in registry.SWEEPS and "schedule" not in registry.pack_requires("schedule")


def _screens(p: Principal) -> dict[str, dict[str, Any]]:
    return {sc["key"]: sc for w in manifest(p, "x")["workspaces"] if w["key"] == "schedule" for sc in w["screens"]}


async def test_menu_follows_the_role_and_nothing_is_pinned_by_default(co: dict[str, Any]) -> None:
    who = co["who"]
    assert set(_screens(who["nv1"])) == {"schedule_week"}                                                # chỉ xem: thấy lịch của mình, không có công cụ tìm / thiết lập
    assert set(_screens(who["dpa"])) == {"schedule_week", "schedule_free", "schedule_time"}              # đặt được lịch (phạm vi đội) nhưng không quản lý
    for k in ("admin", "hc"):
        sc = _screens(who[k])
        assert set(sc) == {"schedule_week", "schedule_free", "schedule_time", "schedule_resources", "schedule_kinds"}, k
        assert sc["schedule_resources"]["group"] == "Thiết lập" and sc["schedule_kinds"]["group"] == "Thiết lập"
        assert sc["schedule_week"]["group"] == "" and sc["schedule_free"]["group"] == "" and sc["schedule_time"]["group"] == ""
        pinned = {x["key"] for x in manifest(who[k], "x")["pins"]}
        assert not pinned & {"schedule_free", "schedule_time", "schedule_resources", "schedule_kinds"}, pinned   # tìm và thiết lập không tự ghim


async def test_the_default_pins_skip_the_finder_and_the_setup_screens(co: dict[str, Any]) -> None:
    for k in ("admin", "dpa"):                                                                           # vai công ty tự tạo (không có ghim riêng) ⇒ rơi về "hai màn đầu"
        pins = {(x["workspace"], x["key"]) for x in manifest(dataclasses.replace(co["who"][k], role="vai_tu_tao"), "x")["pins"]}
        assert ("schedule", "schedule_week") in pins and not {x for _, x in pins} & {"schedule_free", "schedule_time", "schedule_resources", "schedule_kinds"}, \
            (k, pins)


async def test_the_finder_asks_for_three_things_and_keeps_the_rest_folded(co: dict[str, Any]) -> None:
    filters = _screens(co["who"]["admin"])["schedule_free"]["filters"]
    assert [f["key"] for f in filters if not f.get("group")] == ["day", "start", "end"]
    assert {f["group"] for f in filters if f.get("group")} == {"Thêm điều kiện"} and len([f for f in filters if f.get("group")]) == 6
    assert not any(f["required"] for f in filters)                                                       # trống = mặc định ở server
    assert all(f.get("placeholder") for f in filters if f["key"] in ("day", "start", "end"))
    labels = {f["key"]: f["label"] for f in filters}
    assert labels["nature"] == "Người hay phòng" and labels["team_code"] == "Cơ sở / đội" and labels["start"] == "Từ giờ"
    nature = next(f for f in filters if f["key"] == "nature")
    assert [o["label"] for o in nature["options"]] == ["Cả hai", "Người", "Phòng / thiết bị"]
    week = _screens(co["who"]["admin"])["schedule_week"]["filters"]
    assert [f["key"] for f in week if not f.get("group")] == []   # 0240: màn Lịch tự điều hướng (Hôm nay / ‹ › / chế độ) trong khối; ô lọc phụ gấp lại


async def test_the_finder_says_how_many_are_free_and_why_the_others_are_not(co: dict[str, Any]) -> None:
    admin = co["who"]["admin"]
    await ok(admin, "schedule.set_availability", resource_id=co["res"]["p3"], windows=[{"weekday": 4, "start": "08:00", "end": "12:00"}])
    day = MON + timedelta(days=14)                                                                       # thứ Hai: người ba chỉ làm thứ Năm
    free = await q(admin, "schedule.find_free", day=day.isoformat(), start="09:00", end="10:00", kind="nhân sự")
    assert [r["name"] for r in free["rows"]] == ["Người hai", "Người một"] and "Có 2 người / phòng rảnh trong khung giờ này" in free["note"]
    assert free["kpis"] == [] and "Không rảnh: Ngoài giờ rảnh đã khai: Người ba." in free["note"]
    await _make(admin, co, day, "09:00", "10:00", "p1", "p2", team="KD-A")
    free = await q(admin, "schedule.find_free", day=day.isoformat(), start="09:00", end="10:00", kind="nhân sự")
    assert free["rows"] == [] and "Có 0 người / phòng rảnh trong khung giờ này" in free["note"]
    assert free["empty"] == "Không ai rảnh trong khung giờ này. Đã có lịch: Người hai, Người một. Ngoài giờ rảnh đã khai: Người ba."
    free = await q(admin, "schedule.find_free", day=day.isoformat(), start="09:00", end="10:00", kind="phòng")
    assert [r["name"] for r in free["rows"]] == ["Phòng A1", "Phòng A2", "Phòng C1"] and "Có 3 người / phòng rảnh trong khung giờ này" in free["note"]
    assert free["rows"] and "empty_actions" not in free
    none = await q(admin, "schedule.find_free", day=day.isoformat(), start="09:00", end="10:00", kind="không có loại này")
    assert "khớp điều kiện" in none["empty"] and "Thêm điều kiện" in none["empty"]                       # có người / phòng, nhưng bộ lọc loại hết
    default = await q(admin, "schedule.find_free")                                                      # không điền gì: hôm nay, giờ tròn kế tiếp, một giờ sau
    assert default["title"].startswith("Rảnh ") and "Có " in default["note"] + default["empty"]


async def test_empty_company_explains_itself_and_offers_the_next_step() -> None:
    domain = f"l{uuid.uuid4().hex[:6]}.demo"
    tid = await seed.create_tenant(f"Công ty trống {domain}", domain, people=PEOPLE, packs=("schedule",), about="chưa có người hay phòng")
    admin, nv1 = await seed.who(tid, "admin", domain), await seed.who(tid, "nv1", domain)
    free = await q(admin, "schedule.find_free")
    assert free["empty"] == "Chưa có người hay phòng nào. Thêm ở Người & phòng." and free["kpis"] == []
    assert [a["command"] for a in free["empty_actions"]] == ["schedule.add_resource"]
    week = await q(admin, "schedule.week")
    assert "chưa có người hay phòng nào" in week["empty"] and [a["command"] for a in week["empty_actions"]] == ["schedule.add_resource"]
    assert (await q(admin, "schedule.resources"))["empty"].startswith("Chưa có người hay phòng nào.")
    plain = await q(nv1, "schedule.week")                                                                # chỉ xem: không có nút thêm, nói rõ lịch chỉ của mình
    assert "empty_actions" not in plain and "tên bạn" in plain["empty"] and "Đặt lịch" not in plain["empty"]
    assert "Chỉ hiện lịch có tên bạn" in plain["note"]


async def test_an_empty_week_gives_a_short_hint(co: dict[str, Any]) -> None:
    far = (MON + timedelta(days=700)).isoformat()
    week = await q(co["who"]["admin"], "schedule.week", week_of=far)
    assert week["rows"] == [] and week["empty"].startswith("Tuần này chưa có lịch nào.") and "Đặt lịch" in week["empty"] and "tên bạn" not in week["empty"]
    assert "tên bạn" in (await q(co["who"]["nv1"], "schedule.week", week_of=far))["empty"]
