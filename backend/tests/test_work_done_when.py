"""Quản lý việc nhẹ (0117): kế hoạch / việc là ĐỐI TƯỢNG (trao đổi, cuộc gọi gắn vào, quyền kiểm ở DB); việc trỏ tới đối tượng nghiệp vụ của gói
khác (hiện qua module chủ bằng quyền người xem); ``done_when`` — việc có bộ kiểm chỉ đóng khi bộ kiểm báo đạt, người không bấm "Xong" được, ngoại lệ
qua chuỗi duyệt; tiến độ tách "có bằng chứng" khỏi "tự báo"; xếp người nhận không thưởng tự báo nhanh."""

import json
import uuid
from datetime import timedelta
from typing import Any

import pytest

from barista import seed
from barista.agent import chat, toolset
from barista.db import db
from barista.kernel import executor, registry
from barista.kernel.clock import today
from barista.kernel.forms import field_specs
from barista.kernel.principal import Principal
from tests.conftest import Who, fresh_phone, ok, owner_val, q

PEOPLE = [("admin", "Quản trị thử", "admin", "QT", True), ("lead", "Trưởng HCNS thử", "office", "HCNS", True),
          ("nv1", "Nhân viên một", "staff", "HCNS", False), ("nv2", "Nhân viên hai", "staff", "HCNS", False)]
LINK = "https://meet.google.com/abc-defg-hij"


@pytest.fixture(scope="module")
async def co() -> tuple[Any, str]:
    d = f"w{uuid.uuid4().hex[:6]}.demo"
    return await seed.create_tenant(f"Công ty việc {d}", d, people=PEOPLE, packs=("talk", "meet")), d


async def _people(co: tuple[Any, str]) -> dict[str, Principal]:
    tid, d = co
    return {k: await seed.who(tid, k, d) for k, *_ in PEOPLE}


async def _count(p: Principal, sql: Any, params: Any = None) -> int:
    async with db.tenant_tx(p, read_only=True) as conn:
        cur = await conn.execute(sql, params)
        row = await cur.fetchone()
        return int(next(iter(row.values()))) if row else 0


def _blocks(page: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [b for b in page["blocks"] if b["type"] == kind]


async def _plan(lead: Principal, assignee: Principal, title: str = "") -> tuple[str, str]:
    title = title or f"Chuẩn bị họp quý {uuid.uuid4().hex[:4]}"
    out = await ok(lead, "flow.propose_plan", title=title, due_on=(today() + timedelta(days=20)).isoformat(),
                   tasks=[{"title": "Đặt phòng họp", "assignee_email": assignee.email, "due_on": (today() + timedelta(days=5)).isoformat()}])
    tid = await _task_of(out["plan_id"])
    return str(out["plan_id"]), tid


async def _task_of(plan_id: Any) -> str:
    async with db.owner.connection() as conn:
        cur = await conn.execute("SELECT id FROM flow.tasks WHERE plan_id = %s ORDER BY created_at LIMIT 1", (plan_id,))
        row = await cur.fetchone()
        assert row is not None
        return str(row["id"])


# ── 1. Kế hoạch và việc là đối tượng ──

async def test_plan_and_task_threads_follow_who_can_see_them_checked_at_the_database(co: tuple[Any, str]) -> None:
    p = await _people(co)
    plan_id, task_id = await _plan(p["lead"], p["nv1"])
    for kind, sid in (("flow.plan", plan_id), ("flow.task", task_id)):
        await ok(p["nv1"], "talk.post", subject_kind=kind, subject_id=sid, body="Phòng họp tầng 3 còn trống")   # người nhận việc thấy cả kế hoạch
        assert (await executor.run(p["nv2"], "talk.post", {"subject_kind": kind, "subject_id": sid, "body": "chen ngang"})).status in ("not_found", "denied")
        assert (await executor.query(p["nv2"], "talk.thread", {"subject_kind": kind, "subject_id": sid})).status == "not_found"
        assert await _count(p["nv2"], "SELECT count(*) FROM talk.threads WHERE subject_id = %s", (sid,)) == 0        # đọc thẳng bảng: DB chặn
        assert await _count(p["lead"], "SELECT count(*) FROM talk.threads WHERE subject_id = %s", (sid,)) == 1       # trưởng phòng thấy
    assert _blocks(await q(p["nv1"], "flow.plan", plan_id=plan_id), "thread")                                       # khối trao đổi trên thẻ
    assert _blocks(await q(p["nv1"], "flow.task", task_id=task_id), "thread")
    # giao lại việc ⇒ người cũ mất luồng của việc NGAY (không có bản sao quyền), người mới thấy
    await ok(p["lead"], "flow.adjust_task", task_id=task_id, assignee_email=p["nv2"].email)
    assert await _count(p["nv1"], "SELECT count(*) FROM talk.threads WHERE subject_id = %s", (task_id,)) == 0
    assert (await q(p["nv2"], "talk.thread", subject_kind="flow.task", subject_id=task_id))["title"]


async def test_a_plan_call_uses_the_same_visibility_and_talk_off_keeps_the_cards_open(co: tuple[Any, str]) -> None:
    p = await _people(co)
    plan_id, _ = await _plan(p["lead"], p["nv1"])
    started = await ok(p["nv1"], "meet.start", subject_kind="flow.plan", subject_id=plan_id, join_url=LINK)   # "họp kế hoạch" = cuộc gọi trên kế hoạch
    assert (await executor.run(p["nv2"], "meet.join", {"call_id": str(started["call_id"])})).status in ("not_found", "denied")
    assert await _count(p["nv2"], "SELECT count(*) FROM meet.calls WHERE id = %s", (started["call_id"],)) == 0
    await ok(p["admin"], "admin.set_pack", pack="meet", enabled=False)
    await ok(p["admin"], "admin.set_pack", pack="talk", enabled=False)
    try:
        lead = (await _people(co))["lead"]
        page = await q(lead, "flow.plan", plan_id=plan_id)                                                      # gói tắt: thẻ vẫn mở, chỉ mất khối
        assert page["title"] and not _blocks(page, "thread")
    finally:
        await ok(p["admin"], "admin.set_pack", pack="talk", enabled=True)
        await ok(p["admin"], "admin.set_pack", pack="meet", enabled=True)


# ── 2. Việc trỏ tới đối tượng nghiệp vụ ──

async def _lead(sale: Principal) -> tuple[str, str]:
    name = f"Khách việc {uuid.uuid4().hex[:4]}"
    return str((await ok(sale, "crm.create_lead", name=name, phone=fresh_phone()))["lead_id"]), name


def _opens(page: dict[str, Any]) -> list[str]:
    return [o["title"] for o in page.get("open") or []]


async def test_a_task_about_a_lead_shows_it_through_crm_with_the_viewers_rights(who: Who) -> None:
    truonga, sale1, sale2 = await who("truonga"), await who("sale1"), await who("sale2")
    lead, name = await _lead(sale1)
    # người giao phải THẤY đối tượng: sale2 không thấy khách của sale1 ⇒ không gắn được
    r = await executor.run(sale2, "flow.create_task", {"title": "Gọi lại", "subject_kind": "crm.lead", "subject_id": lead})
    assert r.status == "not_found"
    for bad in ({"subject_kind": "crm.lead"}, {"subject_kind": "khong.co", "subject_id": lead}, {"subject_kind": "flow.task", "subject_id": lead}):
        assert (await executor.run(truonga, "flow.create_task", {"title": "Sai", **bad})).status == "invalid", bad
    out = await ok(truonga, "flow.create_task", title="Gửi bảng giá", assignee_email=sale2.email, subject_kind="crm.lead", subject_id=lead)
    assert name in out["summary"]                                                                              # người giao thấy tên khách
    page = await q(sale2, "flow.task", task_id=out["task_id"])                                                  # người làm KHÔNG thấy khách
    assert not any(name in t for t in _opens(page)) and _blocks(page, "callout")[0]["title"] == "Gắn với một khách / lead"
    assert name not in str(await owner_val("SELECT body || title FROM core.notifications WHERE user_id = %s ORDER BY created_at DESC LIMIT 1",
                                           (sale2.user_id,)))                                                    # thông báo không mang tên khách
    await ok(truonga, "flow.adjust_task", task_id=out["task_id"], assignee_email=sale1.email)
    assert any(name in t for t in _opens(await q(sale1, "flow.task", task_id=out["task_id"])))                # người thấy khách: mở được khách


async def test_a_task_needs_only_a_title_and_an_object_and_plans_carry_subjects(who: Who) -> None:
    sale1, truonga = await who("sale1"), await who("truonga")
    lead, name = await _lead(sale1)
    # lối vào cho module khác (vd "tạo việc từ tin nhắn" sau nhánh talk): chỉ tiêu đề + đối tượng; trống người nhận ⇒ chính người gọi
    out = await ok(sale1, "flow.create_task", title="Gửi lại bảng giá", subject_kind="crm.lead", subject_id=lead)
    assert await owner_val("SELECT assignee_id FROM flow.tasks WHERE id = %s", (out["task_id"],)) == sale1.user_id and name in out["summary"]
    plan = await ok(truonga, "flow.propose_plan", title=f"Chăm khách cũ {uuid.uuid4().hex[:4]}", due_on=(today() + timedelta(days=10)).isoformat(),
                    tasks=[{"title": "Hẹn xem nhà", "assignee_email": sale1.email, "subject_kind": "crm.lead", "subject_id": lead}])
    assert await owner_val("SELECT subject_id::text FROM flow.tasks WHERE plan_id = %s", (plan["plan_id"],)) == lead


def test_create_task_is_always_in_the_assistants_fixed_tools_and_hides_checker_fields() -> None:
    """0095: "Thêm việc" là thao tác lõi — có mặt ở tầng 1 của mọi vai, dù câu hỏi là gì và schema các màn khác to đến đâu; trường bộ kiểm
    không vào schema trợ lý thấy (người gắn trên form)."""
    ops = [op for op in registry.OPS.values() if not op.internal]
    describe = {op.name: chat._describe(op, "allow") for op in ops}
    schema = {op.name: chat.tool_schema(op) for op in ops}
    for role in ("sale", "team_lead", "accountant", "marketer", "director", "office", "staff", "sales_admin"):
        plan = toolset.plan(role, [o.name for o in ops], describe=describe, schema=schema, question="Chào em", retrieve=0)
        assert "flow.create_task" in plan.visible, role
    for name in ("flow.create_task", "flow.propose_plan"):
        dumped = json.dumps(chat.tool_schema(registry.OPS[name]))
        assert "done_check" not in dumped and "done_args" not in dumped, name
    assert {"done_check", "done_args"} <= {f["key"] for f in field_specs(registry.OPS["flow.create_task"])}   # người vẫn điền trên form


# ── 4. Xếp người nhận không thưởng tự báo nhanh ──

async def test_self_reported_punctuality_no_longer_moves_anyone_up_the_assignee_list(co: tuple[Any, str]) -> None:
    p = await _people(co)
    _, d = co
    tag = uuid.uuid4().hex[:4]
    people = {}
    for local, name in ((f"an{tag}", f"An chậm {tag}"), (f"zu{tag}", f"Zung nhanh tay {tag}")):   # tên: An xếp trước Zung khi khối lượng bằng nhau
        await ok(p["admin"], "admin.invite_user", email=f"{local}@{d}", name=name, role="staff", team_code="HCNS", is_lead=False)
        people[local] = await seed.who(co[0], local, d)
    fast = people[f"zu{tag}"]
    for i in range(3):   # 3 việc có hạn, tự bấm "Xong" ngay — trước 0117 thành "đúng hạn 100%" và được trừ điểm
        tid = (await ok(p["lead"], "flow.create_task", title=f"Việc nhỏ {i} {tag}", assignee_email=fast.email,
                        due_on=(today() + timedelta(days=5)).isoformat()))["task_id"]
        await ok(fast, "flow.set_task", task_id=tid, status="done")
    rows = [r for r in (await q(p["lead"], "flow.assignees", q=tag))["rows"]]
    assert [r["name"] for r in rows] == [f"An chậm {tag}", f"Zung nhanh tay {tag}"]      # cùng khối lượng ⇒ không ai được ưu tiên vì bấm nhanh
    assert all(r["ontime"] is None for r in rows)                                         # tự báo không phải dữ kiện đúng hạn
