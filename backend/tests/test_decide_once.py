"""MỖI ĐỀ XUẤT CHỈ CHẠY ĐÚNG MỘT LẦN (ADR 0253). Lỗi thật tái hiện trên PostgreSQL: hai lượt xác nhận ĐỒNG THỜI cho cùng một đề xuất tạo hai việc, vì
đọc trạng thái "đang mở", chạy lệnh rồi mới đóng thẻ. Giờ thẻ được chiếm (khoá dòng) TRƯỚC khi chạy lệnh — lượt thứ hai không chạy lại.
Chạy bằng ``asyncio.gather`` trên DB thật: không mock, không tuần tự hoá bằng tay."""

import asyncio
from datetime import timedelta
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from barista import seed
from barista.agent import chat, morning
from barista.db import db
from barista.kernel import executor
from barista.kernel.clock import today
from tests.conftest import Who, edge, fresh_phone, owner_val
from tests.test_admin_owner_approval import PEOPLE, _company
from tests.test_assistant import Scripted, call
from tests.test_resume import Log


async def _tasks(title: str) -> int:
    return await owner_val("SELECT count(*) FROM flow.tasks WHERE title = %s", (title,))


async def _file_card(p: Any, title: str) -> UUID:
    """Một thẻ đề xuất sáng của ``p`` — lệnh ghi thật (tạo việc) để đếm được số lần chạy."""
    due = (today() + timedelta(days=1)).isoformat()
    key = f"test-once:{title}"
    async with db.tenant_tx(p) as conn:
        cur = await conn.execute(
            """INSERT INTO core.proposals (user_id, day, key, command, title, reason, input)
               VALUES (core.me(), %s, %s, 'flow.create_task', %s, 'thử', %s) RETURNING id""",
            (today(), key, title, Jsonb({"title": title, "due_on": due})))
        row = await cur.fetchone()
    assert row is not None
    return row["id"]


async def test_two_simultaneous_confirms_of_one_morning_card_run_the_command_once(who: Who) -> None:
    sale = await who("sale1")
    title = "Việc đề xuất sáng — xác nhận đồng thời (test)"
    card = await _file_card(sale, title)
    a, b = await asyncio.gather(morning.decide(sale, card, "confirm", None, "once-a"), morning.decide(sale, card, "confirm", None, "once-b"))
    assert sorted([a["status"], b["status"]]) == ["not_found", "ok"]                      # một lượt chạy, lượt kia nhận "đã xử lý"
    assert "đã được xử lý" in (a["error"] or b["error"])
    assert await _tasks(title) == 1                                                       # MỘT việc sinh ra
    assert await owner_val("SELECT status FROM core.proposals WHERE id = %s", (card,)) == "done"


async def test_confirm_racing_a_dismiss_never_runs_a_card_that_ends_up_dismissed(who: Who) -> None:
    sale = await who("sale1")
    title = "Việc đề xuất sáng — xác nhận cùng lúc bỏ (test)"
    card = await _file_card(sale, title)
    a, b = await asyncio.gather(morning.decide(sale, card, "confirm", None, "once-c"), morning.decide(sale, card, "dismiss", None, "once-d"))
    state = await owner_val("SELECT status FROM core.proposals WHERE id = %s", (card,))
    ran = await _tasks(title)
    assert (state, ran) in {("done", 1), ("dismissed", 0)}                                # thẻ nói ĐÚNG điều đã xảy ra, không "bỏ" mà vẫn chạy
    assert {a["status"], b["status"]} <= {"ok", "dismissed", "not_found"} and "not_found" in {a["status"], b["status"]}


async def test_two_simultaneous_confirms_of_one_chat_card_run_the_command_once(who: Who) -> None:
    sale = await who("sale1")
    phone = fresh_phone()
    model = Scripted([[call("crm.create_lead", name="Khách xác nhận đồng thời", phone=phone, offering_code="HSR")], "Đã tạo."])
    first = Log()
    await chat.chat_turn(sale, "Thêm khách", emit=first, model=model)
    card = first.proposals()[0]
    one, two = Log(), Log()
    await asyncio.gather(chat.decide(sale, card["id"], "confirm", card["input"], "once-1", emit=one, model=model),
                         chat.decide(sale, card["id"], "confirm", card["input"], "once-2", emit=two, model=model))
    assert await owner_val("SELECT count(*) FROM crm.customers WHERE phone = %s", (phone,)) == 1
    replays = [e["replayed"] for log in (one, two) for e in log.of("decided")]
    assert sorted(replays) == [False, True]                                               # một lượt chạy thật, một lượt nhận kết quả đã có
    assert len([e for log in (one, two) for e in log.of("decided") if e.get("resuming")]) == 1   # trợ lý chạy tiếp đúng MỘT lần


async def test_two_simultaneous_approvals_run_the_target_command_once() -> None:
    """Phiếu duyệt khoá dòng (``core.decide`` — ``FOR UPDATE`` rồi kiểm trạng thái): hai lượt duyệt cùng lúc ⇒ lệnh đích chạy một lần, lượt kia báo đã xử lý."""
    tid, d = await _company(PEOPLE)
    admin, gd = await seed.who(tid, "admin", d), await seed.who(tid, "giamdoc", d)
    r = await executor.run(admin, "admin.set_grant", {"role": "admin", "resource": "acct", "scope": "all", "actions": ["read"]})
    assert r.status == "pending_approval", (r.status, r.error)
    a, b = await asyncio.gather(executor.run(edge(gd), "core.decide", {"approval_id": r.approval_id, "approve": True}),
                                executor.run(edge(gd), "core.decide", {"approval_id": r.approval_id, "approve": True}))
    assert sorted([a.status, b.status]) == ["invalid", "ok"], (a.status, b.status)
    ran = await owner_val("SELECT count(*) FROM core.audit_log WHERE tenant_id = %s AND command = 'admin.set_grant' AND ref = %s AND status = 'ok'",
                          (tid, r.approval_id))
    assert ran == 1


async def test_command_written_but_card_not_saved_runs_once_when_pressed_again(who: Who) -> None:
    """Lệnh và thẻ commit ở HAI giao dịch: lệnh đã ghi xong, tiến trình chết trước khi đóng thẻ ⇒ thẻ còn mở. Bấm lại (khoá bấm khác) ⇒ nhận kết quả đã có
    nhờ khoá thực thi cố định theo mã đề xuất, thẻ đóng, vẫn đúng MỘT việc."""
    sale = await who("sale1")
    title = "Việc đề xuất sáng — lệnh đã ghi, thẻ chưa lưu (test)"
    card = await _file_card(sale, title)
    row = await owner_val("SELECT input FROM core.proposals WHERE id = %s", (card,))
    first = await executor.run(sale, "flow.create_task", row, key=f"proposal:{card}")   # đúng lần chạy mà thẻ không kịp ghi nhận
    assert first.status == "ok" and await _tasks(title) == 1
    assert await owner_val("SELECT status FROM core.proposals WHERE id = %s", (card,)) == "open"
    again = await morning.decide(sale, card, "confirm", None, "press-again")
    assert again["status"] == "ok" and await _tasks(title) == 1
    assert await owner_val("SELECT status FROM core.proposals WHERE id = %s", (card,)) == "done"


async def test_chat_command_written_but_card_not_saved_runs_once_when_pressed_again(who: Who) -> None:
    sale = await who("sale1")
    phone = fresh_phone()
    model = Scripted([[call("crm.create_lead", name="Khách lệnh đã ghi", phone=phone, offering_code="HSR")], "Đã tạo."])
    first = Log()
    await chat.chat_turn(sale, "Thêm khách", emit=first, model=model)
    card = first.proposals()[0]
    ran = await executor.run(sale, "crm.create_lead", card["input"], key=f"chat-card:{card['id']}")
    assert ran.status == "ok" and await owner_val("SELECT count(*) FROM crm.customers WHERE phone = %s", (phone,)) == 1
    again = Log()
    await chat.decide(sale, card["id"], "confirm", card["input"], "press-again", emit=again, model=model)
    assert await owner_val("SELECT count(*) FROM crm.customers WHERE phone = %s", (phone,)) == 1
    assert again.of("decided") and again.of("decided")[0]["status"] == "ok"


async def test_many_simultaneous_proposal_confirms_never_deadlock_the_pool(who: Who) -> None:
    """Rà Codex 21/11: 12 lượt xác nhận đồng thời vượt quá ``max_size=10`` của bể ``db.app`` — mỗi lượt chỉ dùng ĐÚNG MỘT kết nối
    (khoá thẻ + chạy lệnh + đóng thẻ trong cùng giao dịch) nên không bao giờ tự khoá chéo tới PoolTimeout."""
    sale = await who("sale1")
    cards = [await _file_card(sale, f"Việc song song pool #{i}") for i in range(12)]
    results = await asyncio.wait_for(
        asyncio.gather(*(morning.decide(sale, cid, "confirm", None, f"pool-{i}") for i, cid in enumerate(cards))),
        timeout=20,
    )
    assert all(r["status"] == "ok" for r in results), [r["status"] for r in results]
    done = await owner_val("SELECT count(*) FROM core.proposals WHERE id = ANY(%s) AND status = 'done'", (cards,))
    assert done == 12


async def test_many_simultaneous_chat_confirms_never_deadlock_the_pool(who: Who) -> None:
    """Cùng ca cạn bể kết nối cho ``chat.decide``: 12 luồng chat xác nhận đồng thời trên bể 10 kết nối."""
    sale = await who("sale1")
    items: list[tuple[dict[str, Any], Scripted]] = []
    for i in range(12):
        phone = fresh_phone()
        model = Scripted([[call("crm.create_lead", name=f"Khách pool {i}", phone=phone, offering_code="HSR")], "Xong."])
        first = Log()
        await chat.chat_turn(sale, f"Thêm khách {i}", emit=first, model=model)
        items.append((first.proposals()[0], model))
    logs = [Log() for _ in items]
    await asyncio.wait_for(
        asyncio.gather(*(chat.decide(sale, card["id"], "confirm", card["input"], f"chat-pool-{i}", emit=log, model=model)
                         for i, ((card, model), log) in enumerate(zip(items, logs, strict=True)))),
        timeout=30,
    )
    assert all(log.of("decided") and log.of("decided")[0]["status"] == "ok" for log in logs)


def _register_ext_op(monkeypatch: Any, prepare_fn: Any) -> str:
    """Đăng ký tạm một lệnh ghi có ``prepare=`` gọi hệ ngoài (mô phỏng ``mkt.set_ad_status`` / ``set_ad_budget``)."""
    from barista.kernel import registry
    from barista.kernel.fields import Input

    class ExtIn(Input):
        target: str

    async def handler(ctx: Any, inp: ExtIn) -> dict[str, Any]:
        if ctx.preview:
            return {"summary": f"Sẽ gửi lệnh ngoài tới {inp.target}"}
        return {"summary": f"Đã gửi tới {inp.target}", "ext": ctx.prepared}

    name = "flow._test_ext_prepare"
    op = registry.Op(
        name=name, module="flow", kind="command", resource="flow", action="write",
        title="Gọi hệ ngoài (test)", description="Lệnh thử có bước gọi ngoài", input=ExtIn, handler=handler,
        internal=False, importable=False, agent="propose", prepare=prepare_fn,
    )
    monkeypatch.setitem(registry.OPS, name, op)
    return name


async def _file_custom_card(p: Any, command: str, inp: dict[str, Any], title: str) -> UUID:
    async with db.tenant_tx(p) as conn:
        cur = await conn.execute(
            """INSERT INTO core.proposals (user_id, day, key, command, title, reason, input)
               VALUES (core.me(), %s, %s, %s, %s, 'thử gọi ngoài', %s) RETURNING id""",
            (today(), f"test-ext:{title}", command, title, Jsonb(inp)))
        row = await cur.fetchone()
    assert row is not None
    return row["id"]


async def test_morning_prepare_dismiss_wins_sends_zero_external_calls(who: Who, monkeypatch: Any) -> None:
    """Rà Codex 21/11: Bỏ thắng trước Xác nhận ⇒ hệ ngoài nhận ĐÚNG 0 lệnh."""
    ext_calls: list[str] = []

    async def ext_prepare(_prep: Any, inp: Any) -> dict[str, Any]:
        ext_calls.append(inp.target)
        return {"sent": True}

    cmd = _register_ext_op(monkeypatch, ext_prepare)
    sale = await who("sale1")
    card = await _file_custom_card(sale, cmd, {"target": "ad-0"}, "Bỏ thắng trước gọi ngoài")
    dismissed = await morning.decide(sale, card, "dismiss", None, "ext-d0")
    confirmed = await morning.decide(sale, card, "confirm", None, "ext-c0")
    assert dismissed["status"] == "dismissed" and confirmed["status"] == "not_found"
    assert ext_calls == []
    assert await owner_val("SELECT status FROM core.proposals WHERE id = %s", (card,)) == "dismissed"


async def test_morning_prepare_confirm_wins_blocks_dismiss_and_duplicate_confirm_with_one_external_call(who: Who, monkeypatch: Any) -> None:
    """Rà Codex 21/11: Xác nhận giành quyền chạy (``status = 'running'``) rồi gọi hệ ngoài ⇒ Bỏ chen vào bị từ chối (thẻ KHÔNG thành 'dismissed'),
    Xác nhận thứ hai chen vào bị từ chối, bấm lại sau khi xong cũng không gửi thêm — hệ ngoài nhận ĐÚNG 1 lệnh."""
    ext_calls: list[str] = []
    entered, release = asyncio.Event(), asyncio.Event()

    async def ext_prepare(_prep: Any, inp: Any) -> dict[str, Any]:
        ext_calls.append(inp.target)
        entered.set()
        await release.wait()
        return {"sent": inp.target}

    cmd = _register_ext_op(monkeypatch, ext_prepare)
    sale = await who("sale1")
    card = await _file_custom_card(sale, cmd, {"target": "ad-1"}, "Xác nhận thắng đua Bỏ")

    confirm_task = asyncio.create_task(morning.decide(sale, card, "confirm", None, "ext-c1"))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        assert await owner_val("SELECT status FROM core.proposals WHERE id = %s", (card,)) == "running"
        dismiss_mid = await morning.decide(sale, card, "dismiss", None, "ext-d1")
        confirm_mid = await morning.decide(sale, card, "confirm", None, "ext-c2")
        assert dismiss_mid["status"] == "conflict" and confirm_mid["status"] == "conflict"
        assert ext_calls == ["ad-1"]
        assert await owner_val("SELECT status FROM core.proposals WHERE id = %s", (card,)) == "running"
        release.set()
        res = await asyncio.wait_for(confirm_task, timeout=10)
    finally:
        release.set()

    assert res["status"] == "ok" and ext_calls == ["ad-1"]
    assert await owner_val("SELECT status FROM core.proposals WHERE id = %s", (card,)) == "done"
    after = await morning.decide(sale, card, "confirm", None, "ext-c3")
    assert after["status"] == "not_found" and ext_calls == ["ad-1"]


async def test_morning_prepare_failure_marks_failed_and_retry_never_sends_second_external_call(who: Who, monkeypatch: Any) -> None:
    """Bước gọi ngoài lỗi sau khi Xác nhận giành quyền ⇒ thẻ chuyển ``failed`` (không treo ``running``, không mở lại ``open``), bấm lại không gọi thêm."""
    from barista.kernel.errors import Conflict

    ext_calls: list[str] = []

    async def failing_prepare(_prep: Any, inp: Any) -> dict[str, Any]:
        ext_calls.append(inp.target)
        raise Conflict("Kênh quảng cáo quá hạn phản hồi")

    cmd = _register_ext_op(monkeypatch, failing_prepare)
    sale = await who("sale1")
    card = await _file_custom_card(sale, cmd, {"target": "ad-err"}, "Gọi ngoài lỗi")

    res = await morning.decide(sale, card, "confirm", None, "ext-fail-1")
    assert res["status"] == "conflict" and ext_calls == ["ad-err"]
    assert await owner_val("SELECT status FROM core.proposals WHERE id = %s", (card,)) == "failed"
    retry = await morning.decide(sale, card, "confirm", None, "ext-fail-2")
    assert retry["status"] == "not_found" and ext_calls == ["ad-err"]


async def test_chat_prepare_confirm_wins_blocks_dismiss_and_duplicate_confirm_with_one_external_call(who: Who, monkeypatch: Any) -> None:
    """Cùng ca đua Xác nhận / Bỏ khi gọi hệ ngoài trên thẻ chat (``chat.decide``): Bỏ và Xác nhận thứ hai chen vào lúc ``prepare`` đang chạy
    không đổi trạng thái thẻ và không gửi thêm lệnh ngoài; hoàn tất ra ``ok`` với ĐÚNG 1 lần gọi ngoài."""
    import dataclasses

    from barista.kernel import registry

    ext_calls: list[str] = []
    entered, release = asyncio.Event(), asyncio.Event()

    async def ext_prepare(_prep: Any, inp: Any) -> dict[str, Any]:
        ext_calls.append(inp.phone)
        entered.set()
        await release.wait()
        return {"sent": inp.phone}

    orig = registry.OPS["crm.create_lead"]
    monkeypatch.setitem(registry.OPS, "crm.create_lead", dataclasses.replace(orig, prepare=ext_prepare))
    sale = await who("sale1")
    phone = fresh_phone()
    model = Scripted([[call("crm.create_lead", name="Khách gọi ngoài", phone=phone, offering_code="HSR")], "Đã xong."])
    first = Log()
    await chat.chat_turn(sale, "Thêm khách gọi ngoài", emit=first, model=model)
    card = first.proposals()[0]

    log_confirm, log_dismiss, log_dup = Log(), Log(), Log()
    confirm_task = asyncio.create_task(chat.decide(sale, card["id"], "confirm", card["input"], "chat-ext-1", emit=log_confirm, model=model))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        await chat.decide(sale, card["id"], "dismiss", card["input"], "chat-ext-d", emit=log_dismiss, model=model)
        await chat.decide(sale, card["id"], "confirm", card["input"], "chat-ext-2", emit=log_dup, model=model)
        assert log_dismiss.of("decided")[0]["status"] == "running" and log_dismiss.of("decided")[0]["replayed"] is True
        assert log_dup.of("decided")[0]["status"] == "running" and log_dup.of("decided")[0]["replayed"] is True
        assert ext_calls == [phone]
        release.set()
        await asyncio.wait_for(confirm_task, timeout=10)
    finally:
        release.set()

    assert log_confirm.of("decided")[0]["status"] == "ok" and ext_calls == [phone]
    assert await owner_val("SELECT count(*) FROM crm.customers WHERE phone = %s", (phone,)) == 1
    log_after = Log()
    await chat.decide(sale, card["id"], "confirm", card["input"], "chat-ext-3", emit=log_after, model=model)
    assert log_after.of("decided")[0]["status"] == "ok" and log_after.of("decided")[0]["replayed"] is True
    assert ext_calls == [phone]


async def test_chat_prepare_dismiss_wins_sends_zero_external_calls_and_failure_marks_failed(who: Who, monkeypatch: Any) -> None:
    """Thẻ chat có ``prepare=``: Bỏ thắng ⇒ 0 lần gọi ngoài; ``prepare`` lỗi ⇒ thẻ chuyển ``failed``, bấm lại không gọi thêm."""
    import dataclasses

    from barista.kernel import registry
    from barista.kernel.errors import Conflict

    ext_calls: list[str] = []

    async def failing_prepare(_prep: Any, inp: Any) -> dict[str, Any]:
        ext_calls.append(inp.phone)
        raise Conflict("Kênh ngoài tạm lỗi")

    orig = registry.OPS["crm.create_lead"]
    monkeypatch.setitem(registry.OPS, "crm.create_lead", dataclasses.replace(orig, prepare=failing_prepare))
    sale = await who("sale1")

    # Ca 1: Bỏ thắng trước Xác nhận ⇒ 0 gọi ngoài
    p1 = fresh_phone()
    m1 = Scripted([[call("crm.create_lead", name="Bỏ chat", phone=p1, offering_code="HSR")], "Đã bỏ."])
    f1 = Log()
    await chat.chat_turn(sale, "Thêm khách bỏ", emit=f1, model=m1)
    c1 = f1.proposals()[0]
    d1, cf1 = Log(), Log()
    await chat.decide(sale, c1["id"], "dismiss", c1["input"], "chat-d1", emit=d1, model=m1)
    await chat.decide(sale, c1["id"], "confirm", c1["input"], "chat-c1", emit=cf1, model=m1)
    assert d1.of("decided")[0]["status"] == "dismissed" and cf1.of("decided")[0]["status"] == "dismissed" and cf1.of("decided")[0]["replayed"] is True
    assert ext_calls == []

    # Ca 2: prepare lỗi ⇒ thẻ thành failed, bấm lại không gọi thêm
    p2 = fresh_phone()
    m2 = Scripted([[call("crm.create_lead", name="Lỗi chat", phone=p2, offering_code="HSR")], "Lỗi."])
    f2 = Log()
    await chat.chat_turn(sale, "Thêm khách lỗi", emit=f2, model=m2)
    c2 = f2.proposals()[0]
    l2, l2_retry = Log(), Log()
    await chat.decide(sale, c2["id"], "confirm", c2["input"], "chat-f1", emit=l2, model=m2)
    await chat.decide(sale, c2["id"], "confirm", c2["input"], "chat-f2", emit=l2_retry, model=m2)
    assert l2.of("rejected")[0]["status"] == "conflict"
    assert l2_retry.of("decided")[0]["status"] == "failed" and l2_retry.of("decided")[0]["replayed"] is True
    assert ext_calls == [p2]
