"""Dòng thời gian kế hoạch + ngày bắt đầu / phụ thuộc + giao việc từ một câu nói (0222).

- Việc có ``start_on`` (không sau hạn), ``phase``, ``depends_on`` (cùng kế hoạch, không vòng — kiểm trên cả đồ thị, kể cả việc người sửa không thấy).
- Khối ``timeline`` do server dựng: nhóm, mốc kế hoạch, hôm nay, ngày nghỉ, mũi tên phụ thuộc; người xem chỉ thấy tên / người làm của việc mình THẤY (RLS).
- Câu "giao chị Hạnh làm … trước thứ 6": CODE đổi tên → email (trong phạm vi giao việc), lời hạn → ngày công ty, tên kế hoạch → mã; trợ lý chỉ đề xuất MỘT thẻ.
"""

from datetime import date, timedelta
from typing import Any

import pytest

from barista.kernel import executor, registry
from barista.kernel.clock import today
from barista.modules.flow.assign import match_people
from barista.modules.flow.due_phrase import parse_due_phrase
from barista.modules.flow.schedule_rules import check_schedule, find_cycle
from tests.conftest import Who, ok, owner_val, q

# ── hàm thuần ──

def test_cycle_detection() -> None:
    assert find_cycle({"a": ["b"], "b": ["c"], "c": []}) is None
    assert find_cycle({"a": ["b"], "b": ["a"]}) is not None
    assert find_cycle({"a": ["b"], "b": ["c"], "c": ["a"]}) is not None
    assert find_cycle({"a": ["a"]}) is not None
    assert find_cycle({"a": ["b", "c"], "b": ["d"], "c": ["d"], "d": []}) is None   # kim cương không phải vòng


def test_start_must_not_be_after_due() -> None:
    check_schedule(date(2026, 11, 10), date(2026, 11, 10))
    check_schedule(None, date(2026, 11, 10))
    check_schedule(date(2026, 11, 10), None)
    with pytest.raises(Exception, match="sau hạn"):
        check_schedule(date(2026, 11, 11), date(2026, 11, 10))


def test_due_phrases_resolve_to_one_date_in_company_calendar() -> None:
    sat = date(2026, 11, 14)   # thứ Bảy
    expect = {"trước thứ 6": date(2026, 11, 20), "thứ sáu": date(2026, 11, 20), "mai": date(2026, 11, 15), "mốt": date(2026, 11, 16),
              "cuối tuần": date(2026, 11, 20), "20/11": date(2026, 11, 20), "ngày 20": date(2026, 11, 20), "3 ngày nữa": date(2026, 11, 17),
              "tuần sau thứ 3": date(2026, 11, 17), "chủ nhật": date(2026, 11, 15), "cuối tháng": date(2026, 11, 30),
              "10/11": date(2027, 11, 10), "đầu tuần sau": date(2026, 11, 16)}
    for text, want in expect.items():
        assert parse_due_phrase(text, sat) == want, text
    assert parse_due_phrase("khi nào rảnh", sat) is None and parse_due_phrase("", sat) is None
    fri = date(2026, 11, 20)
    assert parse_due_phrase("thứ 6", fri) == date(2026, 11, 27) and parse_due_phrase("hôm nay", fri) == fri   # thứ đang là hôm nay ⇒ tuần sau


def test_name_matching_prefers_the_given_name() -> None:
    people = [{"name": "Nguyễn Thị Lan", "email": "lan@x"}, {"name": "Trần Lan Anh", "email": "la@x"}, {"name": "Võ Thị Hạnh", "email": "h@x"}]
    assert [p["email"] for p in match_people("chị Lan", people)] == ["lan@x"]              # tên gọi = chữ cuối
    assert [p["email"] for p in match_people("Lan Anh", people)] == ["la@x"]
    assert [p["email"] for p in match_people("chi hanh", people)] == ["h@x"]               # không dấu, bỏ "chị"
    assert [p["email"] for p in match_people("LA@x", people)] == ["la@x"]
    assert match_people("Mai", people) == []
    assert len(match_people("Thị", people)) == 2                                            # mơ hồ ⇒ trả cả hai, bên gọi hỏi lại


# ── dữ liệu ──

def _plan(lead_email: str, a: str, b: str, **extra: Any) -> dict[str, Any]:
    t0 = today()
    return {"title": "Dòng thời gian (test)", "due_on": (t0 + timedelta(days=40)).isoformat(), "team_code": "MKT", "tasks": [
        {"title": "Chốt key visual", "assignee_email": a, "start_on": (t0 + timedelta(days=1)).isoformat(), "due_on": (t0 + timedelta(days=10)).isoformat(),
         "phase": "Chuẩn bị"},
        {"title": "Landing page", "assignee_email": b, "due_on": (t0 + timedelta(days=20)).isoformat(), "phase": "Chuẩn bị", "after": "Chốt key visual"},
        {"title": "Chạy quảng cáo", "assignee_email": a, "start_on": (t0 + timedelta(days=21)).isoformat(), "due_on": (t0 + timedelta(days=30)).isoformat(),
         "phase": "Chạy", "after": "Landing page"}], **extra}


async def _ids(plan_id: Any) -> dict[str, Any]:
    async with __import__("barista.db", fromlist=["db"]).db.owner.connection() as conn:
        cur = await conn.execute("SELECT id, title, start_on, phase, depends_on FROM flow.tasks WHERE plan_id = %s", (plan_id,))
        return {r["title"]: r for r in await cur.fetchall()}


async def test_plan_tasks_carry_start_phase_and_dependencies(who: Who) -> None:
    lead, a, b = await who("mkt"), await who("mkt2"), await who("mkt3")
    plan = _plan(lead.email, a.email, b.email)
    out = await ok(lead, "flow.propose_plan", **plan)
    rows = await _ids(out["plan_id"])
    assert rows["Chốt key visual"]["start_on"] == today() + timedelta(days=1) and rows["Chốt key visual"]["phase"] == "Chuẩn bị"
    assert rows["Landing page"]["depends_on"] == [rows["Chốt key visual"]["id"]] and rows["Chạy quảng cáo"]["depends_on"] == [rows["Landing page"]["id"]]
    assert rows["Landing page"]["start_on"] is None                                           # trống = từ ngày tạo

    async def bad(change: dict[str, Any], needle: str) -> None:
        tasks = [dict(t) for t in plan["tasks"]]
        tasks[change.pop("i")].update(change)
        r = await executor.run(lead, "flow.propose_plan", {**plan, "title": plan["title"] + needle, "tasks": tasks})
        assert r.status == "invalid" and needle in (r.error or ""), (r.status, r.error)
    await bad({"i": 0, "after": "Chạy quảng cáo"}, "vòng")                                   # A chờ C, C chờ B, B chờ A
    await bad({"i": 0, "after": "Không có việc này"}, "không có trong kế hoạch")
    await bad({"i": 1, "after": "Landing page"}, "chính nó")
    await bad({"i": 0, "start_on": (today() + timedelta(days=11)).isoformat()}, "sau hạn")


async def test_schedule_command_rejects_cycles_cross_plan_and_late_start(who: Who) -> None:
    lead, a, b = await who("mkt"), await who("mkt2"), await who("mkt3")
    out = await ok(lead, "flow.propose_plan", **_plan(lead.email, a.email, b.email))
    other = await ok(lead, "flow.propose_plan", **{**_plan(lead.email, a.email, b.email), "title": "Kế hoạch khác (test)"})
    rows, others = await _ids(out["plan_id"]), await _ids(other["plan_id"])
    key, land, ads = (rows[k]["id"] for k in ("Chốt key visual", "Landing page", "Chạy quảng cáo"))

    async def run(task: Any, **inp: Any) -> Any:
        return await executor.run(lead, "flow.set_task_timing", {"task_id": str(task), **inp})
    r = await run(key, after=[str(ads)])                                                       # key chờ ads, ads chờ landing, landing chờ key
    assert r.status == "invalid" and "vòng" in (r.error or "")
    r = await run(key, after=[str(others["Landing page"]["id"])])
    assert r.status == "invalid" and "CÙNG kế hoạch" in (r.error or "")
    r = await run(key, after=[str(key)])
    assert r.status == "invalid"
    r = await run(key, start_on=(today() + timedelta(days=11)).isoformat())                    # hạn của key là +10
    assert r.status == "invalid" and "sau hạn" in (r.error or "")
    good = await ok(lead, "flow.set_task_timing", task_id=str(ads), phase="Tăng tốc", start_on=(today() + timedelta(days=22)).isoformat(), after=[str(key), str(land)])
    assert "Tăng tốc" in good["summary"]
    row = (await _ids(out["plan_id"]))["Chạy quảng cáo"]
    assert (row["phase"], row["start_on"], set(row["depends_on"])) == ("Tăng tốc", today() + timedelta(days=22), {key, land})
    await ok(lead, "flow.set_task_timing", task_id=str(ads), after=[], clear_start=True)    # chọn rỗng = bỏ hết phụ thuộc
    row = (await _ids(out["plan_id"]))["Chạy quảng cáo"]
    assert row["depends_on"] == [] and row["start_on"] is None and row["phase"] == "Tăng tốc"  # phase không nêu = giữ
    await ok(lead, "flow.set_task_timing", task_id=str(key), start_on=(today() + timedelta(days=8)).isoformat())
    r = await executor.run(lead, "flow.adjust_task", {"task_id": str(key), "due_on": (today() + timedelta(days=3)).isoformat()})
    assert r.status == "invalid" and "trước ngày bắt đầu" in (r.error or "")                   # dời hạn về trước ngày bắt đầu bị chặn
    opts = await q(lead, "flow.plan_task_options", task_id=str(key))
    assert {o["value"] for o in opts} == {str(land), str(ads)}


# ── khối timeline ──

def _block(page: dict[str, Any]) -> dict[str, Any]:
    return next(b for b in page["blocks"] if b["type"] == "timeline")


async def test_plan_card_has_a_timeline_block_with_groups_milestone_today_off_days_and_arrows(who: Who) -> None:
    lead, a, b = await who("mkt"), await who("mkt2"), await who("mkt3")
    plan = _plan(lead.email, a.email, b.email)
    out = await ok(lead, "flow.propose_plan", **plan)
    tl = _block(await q(lead, "flow.plan", plan_id=out["plan_id"]))
    assert tl["today"] == today().isoformat() and tl["start"] <= tl["today"] <= tl["end"]
    assert [g["title"] for g in tl["groups"]] == ["Chuẩn bị", "Chạy"]                         # có giai đoạn ⇒ nhóm theo giai đoạn, sớm trước
    assert tl["milestones"] == [{"on": plan["due_on"], "title": "Mốc kế hoạch", "tone": "accent"}]
    items = {i["title"]: i for g in tl["groups"] for i in g["items"]}
    key, land, ads = items["Chốt key visual"], items["Landing page"], items["Chạy quảng cáo"]
    assert key["start"] == (today() + timedelta(days=1)).isoformat() and key["end"] <= plan["tasks"][0]["due_on"]   # hạn có thể lùi khỏi ngày nghỉ
    assert land["start"] == today().isoformat()                                                # không start_on ⇒ ngày tạo (hôm nay)
    assert land["after"] == [key["id"]] and ads["after"] == [land["id"]] and key["after"] == []
    assert key["who"] and key["open"]["query"] == "flow.task" and key["dates"] and key["status"]
    assert all(s["kind"] in ("weekend", "holiday") and s["from"] <= s["to"] for s in tl["off"]) and tl["off"]   # cuối tuần được tô bóng
    assert all(s["kind"] != "weekend" or date.fromisoformat(s["from"]).weekday() in (5, 6) for s in tl["off"])
    # cảnh báo: bắt đầu khi việc trước chưa xong
    await ok(lead, "flow.set_task_timing", task_id=str(land["id"]), start_on=(today() + timedelta(days=2)).isoformat())
    tl2 = _block(await q(lead, "flow.plan", plan_id=out["plan_id"]))
    land2 = next(i for g in tl2["groups"] for i in g["items"] if i["title"] == "Landing page")
    assert "Chốt key visual" in land2["flag"]
    # không giai đoạn ⇒ nhóm theo người làm
    await owner_val("UPDATE flow.tasks SET phase = '' WHERE plan_id = %s RETURNING 1", (out["plan_id"],))
    tl3 = _block(await q(lead, "flow.plan", plan_id=out["plan_id"]))
    assert {g["title"] for g in tl3["groups"]} == {a.name, b.name}


async def test_timeline_respects_who_may_see_what(who: Who) -> None:
    lead, a, b, outsider = await who("mkt"), await who("mkt2"), await who("mkt3"), await who("sale1")
    out = await ok(lead, "flow.propose_plan", **{**_plan(lead.email, a.email, b.email), "title": "Kế hoạch riêng tư (test)"})
    tl = _block(await q(b, "flow.plan", plan_id=out["plan_id"]))                               # mkt3 chỉ thấy việc của mình
    items = [i for g in tl["groups"] for i in g["items"]]
    assert len(items) == 3
    mine = next(i for i in items if i["title"] == "Landing page")
    assert mine["who"] and mine["open"]
    hidden = [i for i in items if i["title"] != "Landing page"]
    assert all(i["who"] == "" and "open" not in i and i["title"].startswith("việc của") for i in hidden)   # tên / người làm / mở: không lộ
    assert all(x in {i["id"] for i in items} for x in mine["after"])
    r = await executor.query(outsider, "flow.plan", {"plan_id": str(out["plan_id"])})
    assert r.status == "not_found"                                                             # ngoài kế hoạch: không có trang
    page = await q(outsider, "flow.plans")
    titles = {g["title"] for g in ((page["views"].get("timeline") or {}).get("groups") or [])}
    assert "Kế hoạch riêng tư (test)" not in titles and "Kế hoạch riêng tư (test)" not in {r["title"] for r in page["rows"]}


async def test_plans_screen_has_a_timeline_view_next_to_board_and_calendar(who: Who) -> None:
    lead, a, b = await who("mkt"), await who("mkt2"), await who("mkt3")
    out = await ok(lead, "flow.propose_plan", **{**_plan(lead.email, a.email, b.email), "title": "Kế hoạch trên màn (test)"})
    page = await q(lead, "flow.plans")
    assert {"board", "calendar", "timeline"} <= set(page["views"])
    tl = page["views"]["timeline"]
    g = next(x for x in tl["groups"] if x["key"] == str(out["plan_id"]))
    assert g["collapsed"] and g["open"]["query"] == "flow.plan" and g["start"] <= g["end"] and len(g["items"]) == 3 and g["milestones"]


# ── giao việc từ một câu ──

async def test_one_sentence_assigns_one_task_card_with_resolved_person_date_and_plan(who: Who) -> None:
    lead, a, b, outsider = await who("mkt"), await who("mkt2"), await who("mkt3"), await who("sale1")
    out = await ok(lead, "flow.propose_plan", **{**_plan(lead.email, a.email, b.email), "title": "Ra mắt dự án Xuân (test)"})
    r = await q(lead, "flow.resolve_assignment", who="chị Hạnh", due="trước thứ 6", plan="ra mắt xuân")
    assert r["status"] == "ok" and r["assignee"]["email"] == a.email and r["plan"] == {"id": str(out["plan_id"]), "title": "Ra mắt dự án Xuân (test)"}
    due = date.fromisoformat(r["due_on"])
    assert due > today() and due.weekday() < 5 and due == parse_due_phrase("thứ 6", today())    # ngày làm việc (T6 đúng); trùng lễ thì lùi — không trong test này
    assert r["due_label"].startswith("thứ")
    # trợ lý soạn MỘT thẻ đề xuất với giá trị đã giải; người bấm ⇒ tạo việc đúng người, đúng hạn, nằm trong kế hoạch
    inp = {"title": "Làm báo giá", "assignee_email": r["assignee"]["email"], "due_on": r["due_on"], "plan_id": r["plan"]["id"]}
    proposed = await executor.run(lead.as_agent(), "flow.create_task", inp)
    assert proposed.status == "proposed" and a.name in proposed.output["preview"]["summary"] and due.strftime("%d/%m") in proposed.output["preview"]["summary"]
    assert await owner_val("SELECT count(*) FROM flow.tasks WHERE title = 'Làm báo giá'") == 0   # chưa tạo gì
    made = await ok(lead, "flow.create_task", **inp)
    row = await owner_val("SELECT row_to_json(t) FROM flow.tasks t WHERE id = %s", (str(made["task_id"]),))
    assert (row["assignee_id"], row["plan_id"]) == (str(a.user_id), str(out["plan_id"])) and row["due_on"] == r["due_on"]
    tl = _block(await q(lead, "flow.plan", plan_id=out["plan_id"]))
    assert any(i["title"] == "Làm báo giá" for g in tl["groups"] for i in g["items"])            # xuất hiện trên dòng thời gian
    # ngoài phạm vi giao việc: không giải được tên, và lệnh không chạy được với email đó
    miss = await q(lead, "flow.resolve_assignment", who="Nguyễn Văn An", due="mai")
    assert miss["status"] == "not_found" and miss["assignee"] is None and "phạm vi" in miss["issues"][0]
    r2 = await executor.run(lead, "flow.create_task", {"title": "Việc ngoài phạm vi", "assignee_email": outsider.email})
    assert r2.status != "ok"
    # tên trùng ⇒ hỏi lại, không tự chọn; lời hạn không hiểu ⇒ invalid; kế hoạch không thấy ⇒ not_found
    amb = await q(await who("giamdoc"), "flow.resolve_assignment", who="Thị")
    assert amb["status"] == "ambiguous" and len(amb["candidates"]) >= 2
    assert (await q(lead, "flow.resolve_assignment", who="Hạnh", due="khi nào rảnh"))["status"] == "invalid"
    assert (await q(lead, "flow.resolve_assignment", plan="kế hoạch không tồn tại"))["status"] == "not_found"


async def test_resolve_assignment_is_a_read_tool_for_the_assistant() -> None:
    op = registry.OPS["flow.resolve_assignment"]
    assert op.kind == "query" and op.agent == "allow"


def test_team_lead_and_director_assistants_carry_the_plan_tools_by_default() -> None:
    roles = {k: r for p in registry.PACKS.values() for k, r in p.roles.items() if k in ("team_lead", "director")}
    for role in ("team_lead", "director"):
        tools = set(roles[role].tools)
        assert {"flow.plans", "flow.plan", "flow.propose_plan", "flow.assignees", "flow.resolve_assignment", "flow.create_task",
                "flow.set_task_timing"} <= tools, role
        assert all(registry.get(t) is not None or t in registry.MODULES for t in tools), role   # mọi tên có thật (lỗi gõ ⇒ đỏ)
