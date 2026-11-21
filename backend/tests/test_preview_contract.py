"""BẢN CHẠY THỬ CHUẨN (ADR 0253): mọi thẻ chờ người duyệt — thẻ đề xuất ở chat, thẻ buổi sáng, phiếu duyệt — mang CÙNG MỘT hợp đồng:
hồ sơ nào · cũ → mới · vì sao · đảo được không. Postgres thật, lệnh thật."""

import asyncio  # noqa: F401
from datetime import timedelta
from typing import Any
from uuid import UUID

from barista.agent import morning
from barista.kernel import executor
from barista.kernel.clock import today
from tests.conftest import Who, edge, ok, owner_val

KEYS = {"title", "subject", "changes", "reason", "reversible"}


async def _task(gd: Any, sale: Any, title: str) -> str:
    out = await ok(gd, "flow.create_task", title=title, assignee_email=sale.email, due_on=(today() + timedelta(days=3)).isoformat())
    return str(out["task_id"])


async def test_a_proposal_says_which_record_what_changes_from_and_to_why_and_whether_it_can_be_undone(who: Who) -> None:
    gd, sale = await who("giamdoc"), await who("sale1")
    tid = await _task(gd, sale, "Việc để dời hạn (bản chạy thử)")
    new_due = today() + timedelta(days=9)
    r = await executor.propose(gd.as_agent(), "flow.adjust_task", {"task_id": tid, "due_on": new_due.isoformat()}, reason="Khách hẹn lại tuần sau")
    assert r.status == "proposed", (r.status, r.error)
    brief = r.output["brief"]
    assert set(brief) == KEYS
    assert "Việc để dời hạn (bản chạy thử)" in brief["subject"]["label"] and brief["subject"]["open"]       # hồ sơ nào + bấm mở
    assert {"label": "Hạn", "before": f"{(today() + timedelta(days=3)).day} tháng {(today() + timedelta(days=3)).month}, {(today() + timedelta(days=3)).year}",
            "after": f"{new_due.day} tháng {new_due.month}, {new_due.year}"} in brief["changes"]            # cũ → mới, lõi tự gom từ ctx.track
    assert brief["reason"] == "Khách hẹn lại tuần sau"                                                        # lý do của NGUỒN thắng lý do mặc định của lệnh
    assert brief["reversible"]["ok"] is True and brief["title"] != "Giao lại / dời hạn"                         # đảo được; tiêu đề nói đúng việc, không phải tên lệnh
    assert await owner_val("SELECT due_on FROM flow.tasks WHERE id = %s", (UUID(tid),)) == today() + timedelta(days=3)   # chỉ chạy thử: chưa đổi gì


async def test_the_reason_falls_back_to_the_purpose_the_command_declares(who: Who) -> None:
    gd, sale = await who("giamdoc"), await who("sale1")
    tid = await _task(gd, sale, "Việc lý do mặc định (bản chạy thử)")
    r = await executor.propose(gd.as_agent(), "flow.adjust_task", {"task_id": tid, "due_on": (today() + timedelta(days=8)).isoformat()})
    assert "dời hạn" in r.output["brief"]["reason"]


async def test_the_approval_slip_carries_the_same_brief_and_notifications_open_exactly_that_slip(who: Who) -> None:
    """Phiếu duyệt không còn "<người>: <tên lệnh>": tiêu đề nói đúng việc, thẻ phiếu có khối bản chạy thử, thông báo mở ĐÚNG phiếu."""
    from barista import seed
    from tests.test_admin_owner_approval import PEOPLE, _company

    tid, d = await _company(PEOPLE)
    admin, gd = await seed.who(tid, "admin", d), await seed.who(tid, "giamdoc", d)
    r = await executor.run(admin, "admin.set_grant", {"role": "admin", "resource": "acct", "scope": "all", "actions": ["read"]})
    assert r.status == "pending_approval" and r.approval_id
    row = await owner_val("SELECT jsonb_build_object('summary', summary, 'preview', preview) FROM core.approvals WHERE id = %s", (UUID(r.approval_id),))
    assert row["preview"] and set(row["preview"]) == KEYS
    assert not row["summary"].startswith(f"{admin.name}:")                                                    # không còn "<người>: <tên lệnh>"
    opened = await owner_val("SELECT open FROM core.notifications WHERE user_id = %s AND kind = 'approval' ORDER BY id DESC LIMIT 1", (gd.user_id,))
    assert opened == {"query": "core.approval", "input": {"approval_id": r.approval_id}}                      # mở đúng phiếu, không phải danh sách
    page = (await executor.query(gd, "core.approval", {"approval_id": r.approval_id})).output
    assert any(b["type"] == "brief" for b in page["blocks"])                                                  # thẻ phiếu vẽ đúng bản chạy thử
    d1 = await executor.run(edge(gd), "core.decide", {"approval_id": r.approval_id, "approve": True})
    assert d1.status == "ok"


async def test_the_morning_card_carries_the_brief_and_the_server_says_when_it_is_decided(who: Who) -> None:
    gd = await who("giamdoc")
    sale = await who("sale1")
    tid = await _task(gd, sale, "Việc thẻ sáng (bản chạy thử)")
    await owner_val("DELETE FROM core.proposals WHERE user_id = %s RETURNING 1", (gd.user_id,))
    r = await executor.propose(gd.as_agent(), "flow.adjust_task", {"task_id": tid, "due_on": (today() + timedelta(days=11)).isoformat()}, reason="Quá hạn 2 ngày")
    n = await morning.file_cards(gd, "test-brief", [{"kind": "proposal", "command": "flow.adjust_task", "title": r.output["brief"]["title"],
                                                     "input": r.output["input"], "basis": r.output.get("basis"), "brief": r.output["brief"]}], "Quá hạn 2 ngày")
    assert n == 1
    card = next(c for c in await morning.cards(gd) if c["input"].get("task_id") == tid)
    assert set(card["brief"]) == KEYS and card["state"] == "proposed" and card["title"] == card["brief"]["title"]
    done = await morning.decide(gd, UUID(card["id"]), "confirm", None, "brief-1")
    assert done["status"] == "ok"
    again = await owner_val("SELECT status FROM core.proposals WHERE id = %s", (UUID(card["id"]),))
    assert again == "done" and morning.previews.state_of(again) == {"state": "executed"}
