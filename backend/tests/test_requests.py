"""Nhờ phòng khác / thuê ngoài (quyết định 0074 C): việc ngoài phạm vi giao của người lập thành YÊU CẦU cho PHÒNG; thuê ngoài qua duyệt chi."""

from datetime import timedelta
from uuid import UUID, uuid4

from barista.agent import morning
from barista.kernel import executor
from barista.kernel.clock import today
from barista.kernel.principal import system_principal
from tests.conftest import Who, edge, ok, owner_val, q


async def test_lead_asks_another_department_its_lead_assigns_and_the_plan_counts_it(who: Who) -> None:
    lead, a, truonga, sale1, sale2 = await who("mkt"), await who("mkt2"), await who("truonga"), await who("sale1"), await who("sale2")
    due = (today() + timedelta(days=12)).isoformat()
    r = await executor.run(lead, "flow.propose_plan", {"title": "Mời khách mở bán (test nhờ)", "due_on": (today() + timedelta(days=20)).isoformat(),
                                                       "tasks": [{"title": "Thiệp mời", "assignee_email": a.email},
                                                                 {"title": "Gọi mời khách cũ", "assignee_email": sale1.email}]})
    assert r.status == "invalid"                                                                           # sale ngoài phạm vi của trưởng MKT
    out = await ok(lead, "flow.propose_plan", title="Mời khách mở bán (test nhờ)", due_on=(today() + timedelta(days=20)).isoformat(),
                   tasks=[{"title": "Thiệp mời", "assignee_email": a.email}, {"title": "Gọi mời khách cũ", "request_team": "KD-A", "due_on": due}])
    assert "nhờ phòng khác 1 việc" in out["summary"]
    page = await q(truonga, "flow.requests")
    row = next(x for x in page["rows"] if x["title"] == "Gọi mời khách cũ")
    assert row["mine"] == "yes" and next(k for k in page["kpis"] if k["label"] == "Chờ phòng bạn nhận")["value"] >= 1
    card = await q(lead, "flow.plan", plan_id=out["plan_id"])
    assert any(b.get("title") == "Đang nhờ phòng khác" for b in card["blocks"])
    assert (await executor.run(sale1, "flow.accept_request", {"request_id": str(row["id"]), "assignee_email": sale2.email})).status in ("denied", "not_found")
    assert (await executor.run(lead, "flow.accept_request", {"request_id": str(row["id"]), "assignee_email": a.email})).status == "denied"   # người nhờ không tự nhận
    await owner_val("DELETE FROM core.proposals WHERE user_id = %s RETURNING 1", (truonga.user_id,))
    sug = [c for c in await morning.cards(truonga) if c["input"].get("request_id") == str(row["id"])]
    assert sug and sug[0]["command"] == "flow.accept_request"                                               # sáng ra trưởng phòng thấy sẵn
    done = await ok(truonga, "flow.accept_request", request_id=str(row["id"]), assignee_email=sale2.email)
    assert await owner_val("SELECT assignee_id FROM flow.tasks WHERE id = %s", (done["task_id"],)) == sale2.user_id
    assert str(await owner_val("SELECT plan_id FROM flow.tasks WHERE id = %s", (done["task_id"],))) == str(out["plan_id"])
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND title = 'Team Kinh doanh A đã nhận: Gọi mời khách cũ'",
                           (lead.user_id,)) == 1
    kpis = next(b for b in (await q(lead, "flow.plan", plan_id=out["plan_id"]))["blocks"] if b.get("type") == "kpis")["items"]
    assert any(k["label"] == "Tiến độ" and k["value"] == "0/2 việc" for k in kpis)                         # việc đã nhận tính vào kế hoạch


async def test_decline_needs_a_reason_and_unanswered_requests_go_up_to_the_common_boss(who: Who) -> None:
    lead, truonga, gd = await who("mkt"), await who("truonga"), await who("giamdoc")
    one = await ok(lead, "flow.request_team", title="Nhờ KD-A đứng booth (test)", team_code="KD-A")
    two = await ok(lead, "flow.request_team", title="Nhờ KD-A gọi khách (test)", team_code="KD-A")
    assert (await executor.run(truonga, "flow.decline_request", {"request_id": str(one["request_id"]), "note": ""})).status == "invalid"
    await ok(truonga, "flow.decline_request", request_id=str(one["request_id"]), note="Tuần này cả nhóm đi sự kiện")
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND title LIKE %s",
                           (lead.user_id, "Team Kinh doanh A từ chối: Nhờ KD-A đứng booth%")) == 1
    await owner_val("UPDATE flow.requests SET created_at = now() - interval '3 days' WHERE id = %s RETURNING 1", (two["request_id"],))
    await ok(system_principal(gd.tenant_id), "flow.escalate_requests")
    note = "SELECT count(*) FROM core.notifications WHERE user_id = %s AND title LIKE 'Việc nhờ Team Kinh doanh A 2 ngày chưa ai nhận: Nhờ KD-A gọi khách%%'"
    assert await owner_val(note, (gd.user_id,)) == 1                                                       # trưởng đội cha chung (HQ)
    await ok(system_principal(gd.tenant_id), "flow.escalate_requests")
    assert await owner_val(note, (gd.user_id,)) == 1                                                       # một lần


async def test_outsourcing_waits_for_spend_approval_then_lays_out_the_hiring_steps(who: Who) -> None:
    lead, gd = await who("mkt"), await who("giamdoc")
    r = await executor.run(lead, "flow.request_vendor", {"title": "Dựng phim 3D (test)", "brief": "Phim 60 giây giới thiệu tiện ích",
                                                         "budget_vnd": 30_000_000, "due_on": (today() + timedelta(days=20)).isoformat()})
    assert r.status == "pending_approval"                                                                  # cam kết chi ⇒ duyệt trước
    assert await owner_val("SELECT count(*) FROM flow.requests WHERE title = 'Dựng phim 3D (test)'") == 0
    await ok(edge(gd), "core.decide", approval_id=r.approval_id)
    steps = await owner_val("SELECT count(*) FROM flow.tasks WHERE assignee_id = %s AND title LIKE %s", (lead.user_id, "%— Dựng phim 3D (test)"))
    assert steps == 4
    assert await owner_val("SELECT budget_vnd FROM flow.requests WHERE title = 'Dựng phim 3D (test)'") == 30_000_000
    assert UUID(str(r.approval_id))


async def test_department_is_found_by_code_or_name_however_it_is_typed(who: Who) -> None:
    lead = await who("mkt")
    for spelled in ("KD-A", "kda", "Kinh doanh A", "phòng kinh doanh a", "Team Kinh doanh A", "team kinh doanh a"):
        r = await ok(lead, "flow.request_team", title=f"Nhờ theo cách gõ {spelled} (test)", team_code=spelled)
        assert await owner_val("SELECT t.code FROM flow.requests r JOIN core.teams t ON t.id = r.to_team WHERE r.id = %s", (r["request_id"],)) == "KD-A"
    assert {"KD-A", "MKT"} <= {x["code"] for x in (await q(lead, "flow.teams"))["rows"]}


async def test_requests_follow_the_plan_lifecycle(who: Who) -> None:
    """Review 04/11: kế hoạch còn việc nhờ thì không nghiệm thu; kế hoạch đã đóng thì không nhận việc nhờ vào nữa; huỷ kế hoạch ⇒ đóng việc nhờ."""
    lead, a, truonga, sale2 = await who("mkt"), await who("mkt2"), await who("truonga"), await who("sale2")
    out = await ok(lead, "flow.propose_plan", title="Vòng đời việc nhờ (test)", due_on=(today() + timedelta(days=10)).isoformat(),
                   tasks=[{"title": "Việc của MKT", "assignee_email": a.email}, {"title": "Việc nhờ KD", "request_team": "KD-A"}])
    tid = await owner_val("SELECT id FROM flow.tasks WHERE plan_id = %s", (out["plan_id"],))
    await ok(a, "flow.set_task", task_id=str(tid), status="done")
    r = await executor.run(lead, "flow.set_plan", {"plan_id": out["plan_id"], "status": "done"})
    assert r.status == "invalid" and "đang nhờ phòng khác" in (r.error or "")
    rid = await owner_val("SELECT id FROM flow.requests WHERE plan_id = %s", (out["plan_id"],))
    late = (today() + timedelta(days=20)).isoformat()
    r = await executor.run(truonga, "flow.accept_request", {"request_id": str(rid), "assignee_email": sale2.email, "due_on": late})
    assert r.status == "invalid"                                                                           # hạn vượt mốc kế hoạch
    await ok(lead, "flow.set_plan", plan_id=out["plan_id"], status="cancelled")
    assert await owner_val("SELECT status FROM flow.requests WHERE id = %s", (rid,)) == "declined"
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND title = 'Không cần làm nữa: Việc nhờ KD'",
                           (truonga.user_id,)) == 1
    await owner_val("UPDATE flow.requests SET status = 'open' WHERE id = %s RETURNING 1", (rid,))          # giả như còn treo
    r = await executor.run(truonga, "flow.accept_request", {"request_id": str(rid), "assignee_email": sale2.email})
    assert r.status == "invalid" and "đã đóng" in (r.error or "")                                         # kế hoạch đã huỷ ⇒ không nhận vào


async def test_outsourcing_brief_goes_out_by_link_without_the_budget_and_answers_come_back(who: Who) -> None:
    """0074 C + asks (0075): việc thuê ngoài đã duyệt ⇒ link đề bài một lần; bên làm gửi báo giá / bàn giao qua link ⇒ người xin được báo.
    Ngân sách tối đa là số nội bộ — bên ngoài không thấy."""
    from barista.kernel import events, links
    lead, gd = await who("mkt"), await who("giamdoc")
    r = await executor.run(lead, "flow.request_vendor", {"title": "Dựng phim 3D qua link (test)", "brief": "Phim 60 giây giới thiệu tiện ích",
                                                         "budget_vnd": 45_000_000, "due_on": (today() + timedelta(days=15)).isoformat(),
                                                         "vendor_name": "Studio Mẫu"})
    rid = await owner_val("SELECT id FROM flow.requests WHERE title = 'Dựng phim 3D qua link (test)'")
    assert rid is None and r.status == "pending_approval"
    assert (await executor.run(lead, "flow.send_brief", {"request_id": str(uuid4())})).status == "not_found"
    await ok(edge(gd), "core.decide", approval_id=r.approval_id)
    rid = await owner_val("SELECT id FROM flow.requests WHERE title = 'Dựng phim 3D qua link (test)'")
    row = next(x for x in (await q(lead, "flow.requests"))["rows"] if str(x["id"]) == str(rid))
    assert row["brief"] == "yes"                                                                           # nút "Gửi đề bài (link)" hiện cho người xin
    out = await ok(lead, "flow.send_brief", request_id=str(rid))
    p = await links.resolve(str(out["once_link"]).partition(":")[2])
    assert p is not None
    view = (await executor.query(p, "asks.link_view", {})).output
    assert view["recipient"] == "Studio Mẫu" and "Phim 60 giây" in str(view)
    assert not any(b in str(view) for b in ("45000000", "45.000.000", "45 triệu"))                       # ngân sách không lộ ra ngoài (mã ngẫu nhiên có thể chứa "45")
    await ok(p, "asks.link_answer", item_id=view["items"][0]["id"], answer="38 triệu, 10 ngày")
    await events.drain()
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND title = 'Báo giá thuê ngoài đã về' AND body = '38 triệu, 10 ngày'",
                           (lead.user_id,)) == 1
