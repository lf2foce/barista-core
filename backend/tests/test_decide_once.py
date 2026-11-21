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
