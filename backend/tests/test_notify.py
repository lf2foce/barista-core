"""Thông báo (quyết định 0044): đúng người nhận, cùng giao dịch với việc (đề xuất chạy thử ⇒ không có), người gây ra không tự nhận,
hộp ai nấy thấy (RLS), dòng đẩy không mang tên khách, gửi đẩy chỉ tới người đăng ký máy mới nhất, NOTIFY chỉ mang mã."""

import asyncio
import uuid
from typing import Any

from barista.kernel import events, executor, live, push
from barista.kernel.principal import Principal, system_principal
from barista.modules.flow import tasks as flow_tasks
from tests.conftest import OWNER_URL, Who, ok, owner_val, q


async def _inbox(p: Principal) -> list[dict[str, Any]]:
    return (await q(p, "core.notifications", limit=200))["items"]


async def _has(p: Principal, text: str) -> bool:
    return any(text in n["title"] for n in await _inbox(p))


async def test_task_notifications_reach_the_right_people_and_only_them(who: Who) -> None:
    truonga, sale1, sale2, sale3 = await who("truonga"), await who("sale1"), await who("sale2"), await who("sale3")
    title = f"Gọi lại khách {uuid.uuid4().hex[:6]}"
    await ok(truonga, "flow.create_task", title=title, assignee_email=sale1.email)
    assert await _has(sale1, f"Việc mới: {title}")
    assert not await _has(truonga, title)                                           # người giao không tự nhận
    assert not await _has(sale3, title)                                             # hộp người khác: RLS
    assert title not in await owner_val("SELECT push FROM core.notifications WHERE title = %s", (f"Việc mới: {title}",))   # màn khoá
    pool = f"Việc chung {uuid.uuid4().hex[:6]}"
    await ok(truonga, "flow.create_task", title=pool, team_code="KD-A")
    assert await _has(sale1, pool) and await _has(sale2, pool) and not await _has(sale3, pool)   # đúng đội


async def test_assistant_proposals_and_failed_commands_notify_nobody(who: Who) -> None:
    truonga, sale1 = await who("truonga"), await who("sale1")
    title = f"Đề xuất {uuid.uuid4().hex[:6]}"
    r = await executor.run(truonga.as_agent(), "flow.create_task", {"title": title, "assignee_email": sale1.email})
    assert r.status == "proposed" and not await _has(sale1, title)                  # chạy thử hoàn lại ⇒ không thông báo ma
    sale3 = await who("sale3")
    r = await executor.run(sale3, "flow.create_task", {"title": title, "assignee_email": sale1.email})
    assert r.status == "denied" and not await _has(sale1, title)


async def test_reading_marks_only_my_own_notifications(who: Who) -> None:
    sale1, sale3, truonga = await who("sale1"), await who("sale3"), await who("truonga")
    await ok(truonga, "flow.create_task", title=f"Đọc {uuid.uuid4().hex[:4]}", assignee_email=sale1.email)
    mine = (await q(sale1, "core.notifications"))
    nid = mine["items"][0]["id"]
    assert (await ok(sale3, "core.read_notifications", ids=[nid]))["read"] == 0     # không đánh dấu hộ người khác được
    assert (await ok(sale1, "core.read_notifications", ids=[nid]))["read"] == 1
    assert (await q(sale1, "core.notifications"))["unread"] == mine["unread"] - 1
    r = await executor.run(sale1.as_agent(), "core.read_notifications", {"ids": []})
    assert r.status == "denied"                                                     # trợ lý không "đọc hộ" thông báo


async def test_due_reminder_is_one_per_person_per_day(who: Who, demo: dict[str, Any], monkeypatch: Any) -> None:
    monkeypatch.setattr(flow_tasks, "REMIND_FROM_HOUR", 0)   # nhắc hạn nằm ở phần việc (0088)
    sys_p = system_principal(demo["tenant"])
    first = await executor.run(sys_p, "flow.remind_due", {})   # vòng quét ở test khác có thể đã nhắc hôm nay ⇒ lần này có thể là 0
    again = await executor.run(sys_p, "flow.remind_due", {})
    assert first.ok and again.output["sent"] == 0                                   # chạy lại mỗi phút không báo lặp
    sale1 = await who("sale1")
    due = [n for n in await _inbox(sale1) if n["kind"] == "due"]
    assert len(due) == 1 and "quá hạn" in due[0]["title"]                           # gộp: một thông báo / người / ngày


async def test_due_reminder_skips_people_who_left_instead_of_failing_for_everyone(who: Who, demo: dict[str, Any], monkeypatch: Any) -> None:
    """Việc quá hạn còn giao cho người đã nghỉ (khoá tài khoản): vòng nhắc bỏ qua người đó, người còn làm vẫn nhận nhắc — trước đây ghi thông báo cho
    người nghỉ bị chính sách của bảng từ chối và cả lượt của công ty hỏng."""
    from tests.conftest import fresh_sale
    monkeypatch.setattr(flow_tasks, "REMIND_FROM_HOUR", 0)
    admin = await who("admin")
    left, stays = await fresh_sale(who), await fresh_sale(who)
    for p in (left, stays):
        await ok(admin, "flow.create_task", title=f"Việc cũ {uuid.uuid4().hex[:6]}", assignee_email=p.email)
    await owner_val("UPDATE flow.tasks SET due_on = current_date - 3 WHERE assignee_id = ANY(%s) RETURNING 1", ([left.user_id, stays.user_id],))
    await ok(admin, "admin.update_member", email=left.email, active=False)
    r = await executor.run(system_principal(demo["tenant"]), "flow.remind_due", {})
    assert r.ok, r.error
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND kind = 'due'", (stays.user_id,)) == 1
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND kind = 'due'", (left.user_id,)) == 0


class FakeAPNs:
    def __init__(self, outcome: push.Outcome = "ok") -> None:
        self.outcome: push.Outcome = outcome
        self.sent: list[tuple[str, str, dict[str, Any]]] = []

    async def send(self, token: str, env: str, payload: dict[str, Any]) -> push.Outcome:
        self.sent.append((token, env, payload))
        return self.outcome


async def _flush(sender: Any) -> None:
    while sum((await push.deliver_pending(sender, limit=500)).values()):
        pass


async def test_push_goes_to_the_latest_registrant_of_a_device_and_drops_dead_tokens(who: Who) -> None:
    truonga, sale1, sale2 = await who("truonga"), await who("sale1"), await who("sale2")
    await _flush(None)                                                              # thông báo cũ của dữ liệu mẫu: không có máy ⇒ bỏ qua
    tok = uuid.uuid4().hex * 2
    r = await executor.run(sale1, "core.register_device", {"token": "khong-hop-le"})
    assert r.status == "invalid"
    assert (await executor.run(sale1.as_agent(), "core.register_device", {"token": tok})).status == "denied"
    await ok(sale1, "core.register_device", token=tok, env="sandbox")
    await ok(truonga, "flow.create_task", title="Việc đẩy 1", assignee_email=sale1.email)
    fake = FakeAPNs()
    await _flush(fake)
    assert [(t, e) for t, e, _ in fake.sent] == [(tok, "sandbox")]
    body = fake.sent[0][2]
    assert body["aps"]["alert"]["body"] == "Anh/chị có việc mới" and "Việc đẩy 1" not in str(body)   # màn khoá không có nội dung việc
    assert body["aps"]["badge"] >= 1 and body["open"]["query"] == "flow.tasks"

    await ok(sale2, "core.register_device", token=tok, env="sandbox")              # máy chuyển sang người khác
    await ok(truonga, "flow.create_task", title="Việc đẩy 2", assignee_email=sale1.email)
    fake = FakeAPNs()
    await _flush(fake)
    assert fake.sent == []                                                          # người cũ không nhận trên máy người mới

    await ok(truonga, "flow.create_task", title="Việc đẩy 3", assignee_email=sale2.email)
    gone = FakeAPNs("gone")
    await _flush(gone)
    assert len(gone.sent) == 1
    assert await owner_val("SELECT disabled_at IS NOT NULL FROM core.devices WHERE token = %s AND user_id = %s", (tok, sale2.user_id))


async def test_notify_carries_only_ids_and_wakes_the_right_subscriber(who: Who) -> None:
    truonga, sale1 = await who("truonga"), await who("sale1")
    task = asyncio.create_task(live.listen_forever(OWNER_URL))
    try:
        await asyncio.sleep(0.5)
        with live.hub.subscribe(str(sale1.tenant_id), str(sale1.user_id)) as mine, \
             live.hub.subscribe(str(truonga.tenant_id), str(truonga.user_id)) as other:
            await ok(truonga, "flow.create_task", title="Việc báo ngay", assignee_email=sale1.email)
            await asyncio.wait_for(mine.get(), 5)
            assert other.empty()                                                    # người giao không nhận tín hiệu
    finally:
        task.cancel()
    await events.drain()


async def test_lead_screen_counts_are_over_the_whole_set_not_the_1000_rows_shown(who: Who) -> None:
    """Ô "Đang theo" / "Đến hạn gọi" là số THẬT của tập đã lọc: bảng chỉ hiện 1.000 dòng, ô đếm không được dừng ở 1.000."""
    admin = await who("admin")
    shown = (await q(admin, "crm.leads"))
    kpis = {k["label"]: k["value"] for k in shown["kpis"]}
    real = await owner_val("SELECT count(*) FROM crm.leads WHERE tenant_id = %s AND stage IN ('lead', 'prospect')", (admin.tenant_id,))
    assert kpis["Đang theo"] == real
    assert len(shown["rows"]) == min(real, 1000)
