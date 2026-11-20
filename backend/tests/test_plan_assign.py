"""Tổ chức + trợ lý lập kế hoạch / giao việc (quyết định 0073).

- Vai đặt theo VIỆC, phòng là ĐỘI: sales admin nhập liệu nhưng không sửa quyền; trưởng phòng (is_lead) thấy việc cả phòng, nhân viên chỉ thấy của mình.
- Giao việc theo danh sách ứng viên CODE xếp hạng (khối lượng thật, kể cả việc người giao không thấy); lệnh lập kế hoạch từ chối người ngoài danh sách.
- Một kế hoạch = một thẻ đề xuất, bấm một lần ra kế hoạch + mọi việc; hạn rơi ngày nghỉ lùi về ngày làm việc liền trước; tiến độ / dự báo / cờ rủi ro do code tính.
"""

import asyncio
from datetime import date, timedelta
from typing import Any

from barista.agent import desk
from barista.db import db
from barista.kernel import business_calendar, executor, registry
from barista.kernel.clock import today
from barista.kernel.forms import field_specs
from barista.modules.flow import plans as flow_plans
from tests.conftest import Who, ok, owner_val, q


def _next_weekday(d: date, weekday: int) -> date:
    return d + timedelta(days=(weekday - d.weekday()) % 7 or 7)


async def test_department_lead_sees_the_team_members_see_their_own(who: Who) -> None:
    lead, a, b = await who("mkt"), await who("mkt2"), await who("mkt3")
    task = (await ok(lead, "flow.create_task", title="Viết bài mở bán (test phạm vi)", assignee_email=a.email))["task_id"]
    seen = "SELECT count(*) FROM flow.tasks WHERE id = %s"
    for p, visible in ((lead, 1), (a, 1), (b, 0)):
        from barista.db import db
        async with db.tenant_tx(p, read_only=True) as conn:
            cur = await conn.execute(seen, (task,))
            row = await cur.fetchone()
            assert row is not None and row["count"] == visible, p.email


async def test_candidates_are_ranked_by_real_workload_even_work_the_assigner_cannot_see(who: Who) -> None:
    gd, lead, a, b = await who("giamdoc"), await who("mkt"), await who("mkt2"), await who("mkt3")
    late = (today() - timedelta(days=3)).isoformat()
    for i in range(2):   # việc của mkt3 thuộc đội KD-A — trưởng MKT KHÔNG thấy, nhưng khối lượng vẫn phải tính
        t = await ok(gd, "flow.create_task", title=f"Hỗ trợ kinh doanh {i} (test xếp hạng)", assignee_email=b.email, team_code="KD-A")
        await owner_val("UPDATE flow.tasks SET due_on = %s WHERE id = %s RETURNING 1", (late, t["task_id"]))
    rows = (await q(lead, "flow.assignees", team_code="MKT"))["rows"]
    emails = [r["email"] for r in rows]
    assert {a.email, b.email, lead.email} <= set(emails) and "sale1@sanmau.demo" not in emails      # chỉ người trong phạm vi
    assert next(r for r in rows if r["email"] == b.email)["overdue"] >= 2                             # việc trưởng MKT không thấy vẫn tính
    # xếp theo khối lượng (quá hạn nặng gấp 3) — so theo SỐ trên chính bảng, không theo thứ tự cố định: file test khác chạy trước trên cùng DB
    # (--dist loadfile, chia file theo tiến trình không cố định) giao thêm việc cho mkt2 ⇒ "mkt2 luôn trước mkt3" từng đỏ tuỳ lần chạy
    load = [r["overdue"] * 3 + r["open"] + r["due_week"] * 0.5 for r in rows]
    assert load == sorted(load)
    staff = (await q(await who("nhansu"), "flow.assignees"))["rows"]
    assert [r["email"] for r in staff] == ["nhansu@sanmau.demo"]                                      # không làm trưởng ⇒ chỉ giao cho mình


def test_plan_form_is_an_editable_task_table() -> None:
    op = registry.get("flow.propose_plan")
    assert op is not None
    tasks = next(f for f in field_specs(op) if f["key"] == "tasks")
    assert tasks["kind"] == "rows"                                                                    # thẻ đề xuất vẽ bảng việc sửa được, không phải multiselect
    subs = {f["key"]: f for f in tasks["fields"]}
    assert subs["title"]["required"] and subs["due_on"]["kind"] == "date"
    assert subs["assignee_email"]["options_query"] == "flow.assignee_options"                         # "Giao cho" = danh sách xếp hạng


async def test_one_card_creates_the_whole_plan_and_only_for_people_in_scope(who: Who) -> None:
    lead, a, b = await who("mkt"), await who("mkt2"), await who("mkt3")
    t0 = today()
    sunday = _next_weekday(t0 + timedelta(days=7), 6)
    milestone = t0 + timedelta(days=40)
    plan: dict[str, Any] = {"title": "Chuẩn bị mở bán dự án (test)", "goal": "Đủ ấn phẩm + chạy quảng cáo trước mở bán", "due_on": milestone.isoformat(),
                            "team_code": "MKT", "tasks": [
                                {"title": "Chốt key visual", "assignee_email": a.email, "due_on": sunday.isoformat()},
                                {"title": "Landing page", "assignee_email": b.email, "due_on": (t0 + timedelta(days=20)).isoformat()},
                                {"title": "Lên lịch quảng cáo", "assignee_email": a.email, "due_on": (t0 + timedelta(days=25)).isoformat()}]}
    proposed = await executor.run(lead.as_agent(), "flow.propose_plan", plan)
    assert proposed.status == "proposed"                                                              # trợ lý: một thẻ đề xuất
    assert await owner_val("SELECT count(*) FROM flow.plans WHERE title = %s", (plan["title"],)) == 0

    outsider = {**plan, "tasks": [{**plan["tasks"][0], "assignee_email": "sale1@sanmau.demo"}]}
    r = await executor.run(lead, "flow.propose_plan", outsider)
    assert r.status == "invalid" and "sale1@sanmau.demo" in (r.error or "")                           # ngoài phạm vi giao việc
    late = {**plan, "tasks": [{**plan["tasks"][0], "due_on": (milestone + timedelta(days=5)).isoformat()}]}
    assert (await executor.run(lead, "flow.propose_plan", late)).status == "invalid"                  # trễ hơn mốc kế hoạch

    out = await ok(lead, "flow.propose_plan", **plan)                                                 # người bấm ⇒ tạo hết trong một lần
    assert (out["tasks"], out["people"]) == (3, 2) and "dời khỏi ngày nghỉ" in out["summary"]
    rows = await owner_val("""SELECT jsonb_object_agg(title, due_on) FROM flow.tasks WHERE plan_id = %s""", (out["plan_id"],))
    assert date.fromisoformat(rows["Chốt key visual"]) == business_calendar.previous_workday("vn-public", sunday).day   # xong TRƯỚC ngày nghỉ
    notes = await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND title LIKE %s", (a.user_id, f"%{plan['title']}%"))
    assert notes == 1                                                                                 # một thông báo gộp / người


async def test_progress_signals_are_computed_and_surface_to_the_manager(who: Who) -> None:
    lead, a = await who("mkt"), await who("mkt2")
    t0 = today()
    out = await ok(lead, "flow.propose_plan", title="Kế hoạch sắp tới mốc (test)", due_on=(t0 + timedelta(days=5)).isoformat(), team_code="MKT",
                   tasks=[{"title": f"Việc {i}", "assignee_email": a.email} for i in range(4)])
    card = await q(lead, "flow.plan", plan_id=out["plan_id"])
    callout = next(b for b in card["blocks"] if b.get("type") == "callout")
    assert callout["title"] == "Có nguy cơ trễ" and "Chưa xong việc nào" in callout["text"]           # 4 việc, chưa xong gì, 5 ngày tới mốc
    await owner_val("UPDATE flow.tasks SET due_on = %s WHERE plan_id = %s AND title = 'Việc 0' RETURNING 1", (t0 - timedelta(days=1), out["plan_id"]))
    card = await q(lead, "flow.plan", plan_id=out["plan_id"])
    assert next(b for b in card["blocks"] if b.get("type") == "callout")["title"] == "Trễ"
    listing = await q(lead, "flow.plans")
    assert any(r["id"] == out["plan_id"] and r["risk"] == "late" for r in listing["rows"])
    assert any(i["query"] == "flow.plans" for i in await desk.attention(lead))                   # màn đầu trợ lý nhắc
    assert not any(r["id"] == out["plan_id"] for r in (await q(await who("mkt3"), "flow.plans"))["rows"])   # người khác trong đội không thấy


async def test_director_plan_belongs_to_the_working_team_and_its_lead(who: Who) -> None:
    gd, lead, a, b = await who("giamdoc"), await who("mkt"), await who("mkt2"), await who("mkt3")
    out = await ok(gd, "flow.propose_plan", title="Kế hoạch giám đốc giao (test)", due_on=(today() + timedelta(days=30)).isoformat(),
                   tasks=[{"title": "Ấn phẩm", "assignee_email": a.email}, {"title": "Quảng cáo", "assignee_email": b.email}])
    owner, team = await owner_val("""SELECT ARRAY[u.email, t.code] FROM flow.plans p JOIN core.users u ON u.id = p.owner_id
                                     JOIN core.teams t ON t.id = p.team_id WHERE p.id = %s""", (out["plan_id"],))
    assert (owner, team) == (lead.email, "MKT")                                                      # đội chung của người nhận + trưởng đội phụ trách
    assert any(r["id"] == out["plan_id"] for r in (await q(lead, "flow.plans"))["rows"])              # trưởng phòng thấy để theo dõi
    notes = await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND title LIKE %s", (lead.user_id, "%phụ trách kế hoạch “Kế hoạch giám đốc giao (test)”%"))
    assert notes == 1
    r = await executor.run(gd, "flow.propose_plan", {"title": "Phụ trách ngoài (test)", "owner_email": "khong-co@sanmau.demo",
                                                    "tasks": [{"title": "X", "assignee_email": a.email}]})
    assert r.status == "invalid"


async def test_cross_department_plan_is_visible_to_every_participant_with_progress_per_department(who: Who) -> None:
    gd, lead, a, hcns, sale = await who("giamdoc"), await who("mkt"), await who("mkt2"), await who("hcns"), await who("sale1")
    out = await ok(gd, "flow.propose_plan", title="Mở bán nhiều phòng (test)", team_code="marketing",          # tên đội cũng nhận due_on=(today() + timedelta(days=30)).isoformat(),
                   tasks=[{"title": "Quảng cáo", "assignee_email": a.email}, {"title": "Hậu cần sự kiện", "assignee_email": hcns.email},
                          {"title": "Mời khách cũ", "assignee_email": sale.email}])
    assert await owner_val("SELECT owner_id FROM flow.plans WHERE id = %s", (out["plan_id"],)) == lead.user_id
    for p in (a, hcns, sale, lead):                                                                  # người nhận + trưởng phòng chủ trì mở được thẻ
        card = await q(p, "flow.plan", plan_id=out["plan_id"])
        assert card["title"] == "Mở bán nhiều phòng (test)", p.email
    mine = await q(lead, "flow.plan", plan_id=out["plan_id"])                                        # trưởng phòng chủ trì: tiến độ CẢ kế hoạch
    assert next(b for b in mine["blocks"] if b.get("type") == "kpis")["items"][0]["value"] == "0/3 việc"
    assert next(b for b in mine["blocks"] if b.get("type") == "list")["title"].startswith("Các việc bạn được xem (1/3")
    await owner_val("UPDATE flow.tasks SET due_on = %s WHERE plan_id = %s AND title = 'Mời khách cũ' RETURNING 1", (today() - timedelta(days=1), out["plan_id"]))
    callout = next(b for b in (await q(lead, "flow.plan", plan_id=out["plan_id"]))["blocks"] if b.get("type") == "callout")
    assert callout["title"] == "Trễ" and "việc của Team Kinh doanh A" in callout["text"]                    # thấy phòng khác trễ, không lộ tên việc
    card = await q(gd, "flow.plan", plan_id=out["plan_id"])
    tiles = next(b for b in card["blocks"] if b.get("type") == "tiles")
    assert {t["title"] for t in tiles["items"]} == {"Marketing", "Hành chính – Nhân sự", "Team Kinh doanh A"}
    labels = [o["label"] for o in await q(gd, "flow.assignee_options")]
    assert any("Hành chính – Nhân sự" in x for x in labels)                                          # ô "Giao cho" ghi rõ phòng



async def test_late_plan_card_offers_remind_reassign_and_add_and_they_follow_the_plan_rules(who: Who) -> None:
    lead, a, b = await who("mkt"), await who("mkt2"), await who("mkt3")
    t0 = today()
    out = await ok(lead, "flow.propose_plan", title="Kế hoạch chậm (test xử lý)", team_code="MKT", due_on=(t0 + timedelta(days=20)).isoformat(),
                   tasks=[{"title": "Việc chậm A", "assignee_email": a.email, "due_on": (t0 + timedelta(days=10)).isoformat()},
                          {"title": "Việc xa B", "assignee_email": b.email, "due_on": (t0 + timedelta(days=15)).isoformat()}])
    pid = out["plan_id"]
    late_id = await owner_val("UPDATE flow.tasks SET due_on = %s WHERE plan_id = %s AND title = 'Việc chậm A' RETURNING id", (t0 - timedelta(days=2), pid))
    card = await q(lead, "flow.plan", plan_id=pid)
    acts = next(b for b in card["blocks"] if b.get("type") == "callout")["actions"]
    assert [x["command"] for x in acts] == ["flow.remind_plan", "flow.adjust_task"]                  # chỉ việc chậm mới có nút giao lại
    assert acts[1]["preset"] == {"task_id": str(late_id)}
    assert card["actions"][0]["command"] == "flow.create_task" and card["ask"]["prompt"].startswith("Kế hoạch")

    r = await ok(lead, "flow.remind_plan", plan_id=pid)
    assert (r["people"], r["tasks"]) == (1, 1)                                                      # chỉ người có việc chậm
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND title LIKE 'Nhắc:%%Kế hoạch chậm%%'", (a.user_id,)) == 1

    too_late = (t0 + timedelta(days=30)).isoformat()
    assert (await executor.run(lead, "flow.adjust_task", {"task_id": late_id, "due_on": too_late})).status == "invalid"   # quá mốc
    assert (await executor.run(lead, "flow.adjust_task", {"task_id": late_id, "assignee_email": "sale1@sanmau.demo"})).status == "invalid"
    await ok(lead, "flow.adjust_task", task_id=late_id, assignee_email=b.email, due_on=(t0 + timedelta(days=5)).isoformat(), note="Hạnh đang nghỉ")
    assert await owner_val("SELECT assignee_id FROM flow.tasks WHERE id = %s", (late_id,)) == b.user_id
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND title LIKE %s", (a.user_id, "%đã chuyển cho người khác")) == 1

    added = await ok(lead, "flow.create_task", title="Việc thêm C", assignee_email=a.email, plan_id=pid, due_on=(t0 + timedelta(days=7)).isoformat())
    assert str(await owner_val("SELECT plan_id FROM flow.tasks WHERE id = %s", (added["task_id"],))) == str(pid)
    r = await executor.run(lead, "flow.create_task", {"title": "Việc quá mốc", "plan_id": pid, "due_on": too_late})
    assert r.status == "invalid"
    board = await q(a, "flow.tasks")
    item = next(i for c in board["blocks"][1]["columns"] for i in c["items"] if i["title"] == "Việc thêm C")
    assert item["subtitle"].startswith("Kế hoạch Kế hoạch chậm") and item["open"]["query"] == "flow.task"   # bấm ⇒ thẻ của việc
    card = await q(a, "flow.task", **item["open"]["input"])
    assert card["open"][0]["query"] == "flow.plan"                                                      # thẻ việc dẫn về kế hoạch


async def test_finishing_my_part_does_not_make_the_plan_look_done(who: Who) -> None:
    """Review 04/11: tiến độ phần được xem không được trình bày như cả kế hoạch."""
    gd, a, sale = await who("giamdoc"), await who("mkt2"), await who("sale1")
    out = await ok(gd, "flow.propose_plan", title="Kế hoạch hai phòng (test phần mình)", team_code="MKT",
                   tasks=[{"title": "Bài đăng", "assignee_email": a.email}, {"title": "Gọi khách cũ", "assignee_email": sale.email}])
    mine = await owner_val("SELECT id FROM flow.tasks WHERE plan_id = %s AND title = 'Bài đăng'", (out["plan_id"],))
    await ok(a, "flow.set_task", task_id=str(mine), status="done")
    card = await q(a, "flow.plan", plan_id=out["plan_id"])
    assert next(b for b in card["blocks"] if b.get("type") == "kpis")["items"][0]["value"] == "1/2 việc"
    assert next(b for b in card["blocks"] if b.get("type") == "callout")["title"] not in ("Chờ nghiệm thu", "Đã nghiệm thu")


async def test_plan_lifecycle_review_close_cancel_reopen(who: Who) -> None:
    gd, lead, a, sale = await who("giamdoc"), await who("mkt"), await who("mkt2"), await who("sale1")
    out = await ok(gd, "flow.propose_plan", title="Kế hoạch vòng đời (test)", team_code="MKT",
                   tasks=[{"title": "Việc MKT", "assignee_email": a.email}, {"title": "Việc sale", "assignee_email": sale.email}])
    pid = out["plan_id"]
    assert (await executor.run(lead, "flow.set_plan", {"plan_id": pid, "status": "done"})).status == "invalid"     # còn việc ⇒ chưa nghiệm thu được
    for title, p in (("Việc MKT", a), ("Việc sale", sale)):
        tid = await owner_val("SELECT id FROM flow.tasks WHERE plan_id = %s AND title = %s", (pid, title))
        await ok(p, "flow.set_task", task_id=str(tid), status="done")
    card = await q(lead, "flow.plan", plan_id=pid)
    callout = next(b for b in card["blocks"] if b.get("type") == "callout")
    assert callout["title"] == "Chờ nghiệm thu" and any(x["command"] == "flow.set_plan" for x in callout["actions"])   # hết việc ≠ tự đóng
    assert any(r["id"] == pid and r["risk"] == "review" for r in (await q(lead, "flow.plans"))["rows"])
    assert (await executor.run(a, "flow.set_plan", {"plan_id": pid, "status": "done"})).status == "denied"          # người nhận việc không đóng thay
    await ok(lead, "flow.set_plan", plan_id=pid, status="done", note="Đủ ấn phẩm")
    assert not any(r["id"] == pid for r in (await q(lead, "flow.plans"))["rows"])                                    # rời "Đang chạy"
    assert (await q(lead, "flow.plan", plan_id=pid))["actions"][0]["preset"]["status"] == "open"
    await ok(lead, "flow.set_plan", plan_id=pid, status="open")
    await ok(gd, "flow.create_task", title="Việc thêm sau", assignee_email=sale.email, plan_id=pid)
    r = await ok(lead, "flow.set_plan", plan_id=pid, status="cancelled")
    assert "huỷ 1 việc" in r["summary"]                                                                                # việc phòng khác (lead không thấy) cũng huỷ theo
    assert await owner_val("SELECT status FROM flow.tasks WHERE plan_id = %s AND title = 'Việc thêm sau'", (pid,)) == "cancelled"
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND title = %s", (sale.user_id, "Kế hoạch “Kế hoạch vòng đời (test)” đã huỷ")) == 1


async def test_one_due_date_rule_everywhere_with_calendars_per_team(who: Who) -> None:
    gd, lead, a, sale = await who("giamdoc"), await who("mkt"), await who("mkt2"), await who("sale1")
    t0 = today()
    sunday = _next_weekday(t0 + timedelta(days=7), 6)
    past = (t0 - timedelta(days=1)).isoformat()
    assert (await executor.run(gd, "flow.propose_plan", {"title": "Mốc đã qua (test)", "due_on": past,
                                                          "tasks": [{"title": "X", "assignee_email": a.email}]})).status == "invalid"
    assert (await executor.run(lead, "flow.create_task", {"title": "Hạn đã qua", "due_on": past})).status == "invalid"
    out = await ok(gd, "flow.propose_plan", title="Kế hoạch lịch đội (test)", due_on=(t0 + timedelta(days=30)).isoformat(),
                   tasks=[{"title": "Văn phòng", "assignee_email": a.email}])
    added = await ok(gd, "flow.create_task", title="Thêm vào kế hoạch Chủ nhật", assignee_email=a.email, plan_id=out["plan_id"], due_on=sunday.isoformat())
    assert await owner_val("SELECT due_on FROM flow.tasks WHERE id = %s", (added["task_id"],)) == business_calendar.previous_workday("vn-public", sunday).day   # cùng luật với lập kế hoạch
    await ok(gd, "flow.set_work_calendar", team_code="KD-A", calendar="vn-ca-tuan")
    sale_task = await ok(gd, "flow.create_task", title="Dẫn khách xem nhà Chủ nhật", assignee_email=sale.email, due_on=sunday.isoformat())
    assert await owner_val("SELECT due_on FROM flow.tasks WHERE id = %s", (sale_task["task_id"],)) == sunday               # đội sale làm cả tuần
    await ok(gd, "flow.adjust_task", task_id=str(sale_task["task_id"]), assignee_email=a.email)
    await ok(gd, "flow.adjust_task", task_id=str(sale_task["task_id"]), due_on=sunday.isoformat())
    assert await owner_val("SELECT due_on FROM flow.tasks WHERE id = %s", (sale_task["task_id"],)) == business_calendar.previous_workday("vn-public", sunday).day   # theo lịch đội người MỚI


async def test_closed_plan_tasks_require_the_plan_to_be_reopened(who: Who) -> None:
    gd, a = await who("giamdoc"), await who("mkt2")
    out = await ok(gd, "flow.propose_plan", title="Khoá việc sau nghiệm thu (test)",
                   tasks=[{"title": "Việc cần nghiệm thu", "assignee_email": a.email}])
    pid = out["plan_id"]
    tid = await owner_val("SELECT id FROM flow.tasks WHERE plan_id = %s", (pid,))
    await ok(a, "flow.set_task", task_id=tid, status="done")
    await ok(gd, "flow.set_plan", plan_id=pid, status="done")
    assert (await executor.run(a, "flow.set_task", {"task_id": tid, "status": "doing"})).status == "invalid"
    assert (await executor.run(gd, "flow.adjust_task", {"task_id": tid, "due_on": today().isoformat()})).status == "invalid"
    assert (await executor.run(gd, "flow.create_task", {"title": "Thêm sau nghiệm thu", "plan_id": pid})).status == "not_found"
    await ok(gd, "flow.set_plan", plan_id=pid, status="open")
    await ok(a, "flow.set_task", task_id=tid, status="doing")
    await ok(gd, "flow.set_plan", plan_id=pid, status="cancelled")
    assert (await executor.run(a, "flow.set_task", {"task_id": tid, "status": "doing"})).status == "invalid"


async def test_cancelling_a_plan_serializes_with_task_changes(who: Who, monkeypatch: Any) -> None:
    gd, a = await who("giamdoc"), await who("mkt2")
    out = await ok(gd, "flow.propose_plan", title="Huỷ đồng thời sửa việc (test)",
                   tasks=[{"title": "Việc đang mở", "assignee_email": a.email}])
    pid = out["plan_id"]
    tid = await owner_val("SELECT id FROM flow.tasks WHERE plan_id = %s", (pid,))
    locked, release = asyncio.Event(), asyncio.Event()
    original = flow_plans._lock_plan   # set_plan (phần kế hoạch) giữ khoá — vá đúng chỗ nó tra tên (0088)

    async def hold(ctx: Any, plan_id: Any) -> None:
        await original(ctx, plan_id)
        if ctx.p.user_id == gd.user_id:
            locked.set()
            await release.wait()

    monkeypatch.setattr(flow_plans, "_lock_plan", hold)
    close = asyncio.create_task(executor.run(gd, "flow.set_plan", {"plan_id": pid, "status": "cancelled"}))
    change = None
    try:
        await asyncio.wait_for(locked.wait(), 30)   # trần rộng: chỉ chạm khi hỏng thật — máy tải nặng (hai bộ test cùng lúc) chậm hơn 5 giây
        change = asyncio.create_task(executor.run(a, "flow.set_task", {"task_id": tid, "status": "doing"}))
        async with asyncio.timeout(30):
            while True:
                async with db.app.connection() as conn:
                    row = await (await conn.execute("SELECT count(*) AS n FROM pg_stat_activity WHERE datname = current_database() AND wait_event = 'advisory'")).fetchone()
                if row and row["n"]:
                    break
                assert not change.done(), "Sửa việc phải chờ khoá kế hoạch"
                await asyncio.sleep(0.01)
    finally:
        release.set()
        results = await asyncio.gather(close, *([change] if change else []))
    assert [r.status for r in results] == ["ok", "invalid"]
    assert await owner_val("SELECT status FROM flow.tasks WHERE id = %s", (tid,)) == "cancelled"


async def test_future_task_calendar_warnings_are_visible_on_every_entrypoint(who: Who) -> None:
    gd, a = await who("giamdoc"), await who("mkt2")
    sunday = date(2027, 1, 10)
    await ok(gd, "flow.set_work_calendar", team_code="MKT", calendar="vn-ca-tuan")
    p = await ok(gd, "flow.propose_plan", title="Lịch tuần năm chưa có dữ liệu (test)",
                 tasks=[{"title": "Chủ nhật vẫn làm", "assignee_email": a.email, "due_on": sunday.isoformat()}])
    tid = await owner_val("SELECT id FROM flow.tasks WHERE plan_id = %s", (p["plan_id"],))
    assert await owner_val("SELECT due_on FROM flow.tasks WHERE id = %s", (tid,)) == sunday
    added = await ok(gd, "flow.create_task", title="Việc thêm cuối tuần", assignee_email=a.email,
                     plan_id=p["plan_id"], due_on=sunday.isoformat())
    adjusted = await ok(gd, "flow.adjust_task", task_id=added["task_id"], due_on=sunday.isoformat())
    for result in (p, added, adjusted):
        assert result["calendar_warnings"] and "2027" in result["summary"] and "chưa xét ngày lễ" in result["summary"]


async def test_plan_owner_can_remind_people_of_other_departments(who: Who) -> None:
    """Trưởng phòng chủ trì không xem được việc phòng khác nhưng vẫn phải nhắc được người đang chậm trong kế hoạch mình phụ trách."""
    gd, lead, sale, a = await who("giamdoc"), await who("mkt"), await who("sale1"), await who("mkt2")
    out = await ok(gd, "flow.propose_plan", title="Nhắc xuyên phòng (test)", team_code="MKT",
                   tasks=[{"title": "Việc sale chậm", "assignee_email": sale.email}, {"title": "Việc MKT", "assignee_email": a.email}])
    await owner_val("UPDATE flow.tasks SET due_on = %s WHERE plan_id = %s AND title = 'Việc sale chậm' RETURNING 1",
                    (today() - timedelta(days=1), out["plan_id"]))
    card = await q(lead, "flow.plan", plan_id=out["plan_id"])
    acts = next(b for b in card["blocks"] if b.get("type") == "callout")["actions"]
    assert [x["command"] for x in acts] == ["flow.remind_plan"]                                       # nhắc được; giao lại việc phòng khác thì không
    r = await ok(lead, "flow.remind_plan", plan_id=out["plan_id"])
    assert (r["people"], r["tasks"]) == (1, 1)
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND title LIKE %s", (sale.user_id, "Nhắc:%Nhắc xuyên phòng%")) == 1
    assert (await executor.run(a, "flow.remind_plan", {"plan_id": out["plan_id"]})).status == "invalid"   # người nhận việc chỉ nhắc việc mình thấy


async def test_plan_owner_tracks_other_departments_without_reading_their_tasks(who: Who) -> None:
    """Hợp đồng quyền hệ thống của kế hoạch chỉ trả trạng thái / hạn / phòng: người phụ trách thấy phòng khác trễ, nhắc được, nghiệm thu đúng —
    nhưng tên việc phòng khác không tới thẻ kế hoạch, danh sách kế hoạch, thẻ gợi ý buổi sáng, kết quả nhắc. Người làm vẫn đọc việc của mình."""
    gd, lead, sale = await who("giamdoc"), await who("mkt"), await who("sale1")
    secret = "Gọi lại khách VIP bí mật (test)"
    out = await ok(gd, "flow.propose_plan", title="Kế hoạch nhiều phòng không lộ tên việc (test)", team_code="MKT", owner_email=lead.email,
                   tasks=[{"title": secret, "assignee_email": sale.email}])
    await owner_val("UPDATE flow.tasks SET due_on = %s WHERE plan_id = %s RETURNING 1", (today() - timedelta(days=1), out["plan_id"]))
    card = await q(lead, "flow.plan", plan_id=out["plan_id"])
    assert "việc của Team Kinh doanh A" in str(card) and secret not in str(card)                          # thấy phòng khác trễ, không thấy việc gì
    assert secret not in str(await q(lead, "flow.plans"))
    assert secret not in str(await q(lead, "flow.suggestions"))                                       # lý do trên thẻ sáng cũng không lộ
    r = await ok(lead, "flow.remind_plan", plan_id=out["plan_id"])
    assert (r["people"], r["tasks"]) == (1, 1) and secret not in str(r)
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND body LIKE %s", (sale.user_id, f"%{secret}%")) == 1
    assert (await executor.run(lead, "flow.set_plan", {"plan_id": out["plan_id"], "status": "done"})).status == "invalid"   # còn việc mở
    await ok(lead, "flow.set_plan", plan_id=out["plan_id"], status="cancelled")
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND title LIKE %s",
                           (sale.user_id, "Kế hoạch “Kế hoạch nhiều phòng%đã huỷ")) == 1                # người làm phòng khác vẫn được báo



async def test_plan_accepts_person_ids_from_the_candidates_table(who: Who) -> None:
    """Bảng "Ai nên nhận việc" có cả mã người — trợ lý điền mã thay email vẫn phải giao đúng người (không bắt thử lại)."""
    gd, a = await who("giamdoc"), await who("mkt2")
    out = await ok(gd, "flow.propose_plan", title="Giao bằng mã người (test)", tasks=[{"title": "Một việc", "assignee_email": str(a.user_id)}])
    assert await owner_val("SELECT assignee_id FROM flow.tasks WHERE plan_id = %s", (out["plan_id"],)) == a.user_id


async def test_clicking_a_task_opens_the_task_with_the_actions_the_viewer_may_take(who: Who) -> None:
    """Bấm một việc trong hồ sơ / kế hoạch / bảng việc ⇒ thẻ CỦA VIỆC ĐÓ (trước đây chỉ mở lại hồ sơ đang xem)."""
    truonga, sale2 = await who("truonga"), await who("sale2")
    mine = await ok(truonga, "flow.create_task", title="Việc mở được (test)", assignee_email=sale2.email, notes="Gọi trước 10h")
    card = await q(sale2, "flow.task", task_id=str(mine["task_id"]))
    assert card["title"] == "Việc mở được (test)" and any(b.get("title") == "Ghi chú" for b in card["blocks"])
    assert [x["title"] for x in card["actions"]] == ["Bắt đầu làm", "Xong", "Báo kẹt", "Giao lại / dời hạn"]
    team = await ok(truonga, "flow.create_task", title="Việc chung mở được (test)", team_code="KD-A")
    assert [x["title"] for x in (await q(sale2, "flow.task", task_id=str(team["task_id"])))["actions"]] == ["Nhận việc"]
    case_card = await q(truonga, "flow.case", case_id=str(await owner_val("SELECT id FROM flow.cases WHERE status = 'open' LIMIT 1")))
    items = [i for b in case_card["blocks"] if b.get("type") == "list" for i in b["items"] if "open" in i]
    assert items and all(i["open"]["query"] == "flow.task" for i in items)                                # dòng việc trong hồ sơ ⇒ thẻ việc

