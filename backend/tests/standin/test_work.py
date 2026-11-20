"""VIỆC THEO DÕI NHIỀU NGÀY (0021 / 0076) qua gói thế vai: bản LÕI (ADR 0190) của ``barista-packs:commission/tests/test_work.py`` — gói khai loại việc, bộ máy
theo dõi ở gói nền: mở khi ký (người phụ trách chọn theo ma trận quyền), điều kiện xong kiểm bằng code, ngoại lệ ⇒ trợ lý CỦA người phụ trách với bộ
công cụ hẹp, quyền nạp lại mỗi lần chạy, DBOS chỉ giữ nhịp, bảng đo kết quả."""

import asyncio
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from pydantic_ai import ToolCallPart

from barista import work_runtime
from barista.kernel import events, executor
from barista.kernel.clock import today
from barista.modules import work
from tests.conftest import Who, approved, ok, owner_val, q
from tests.standin import helpers
from tests.test_assistant import Scripted


async def _signed(who: Who, *, days_ago: int, sale: str = "sale3") -> tuple[str, Any, int]:
    """Đơn mới ký ``days_ago`` ngày trước (hạn khách trả phí = ký + 30 ngày). Trả (mã khoản phí, id việc, phí)."""
    o = await helpers.signed(who, owner=sale, amount=6_000_000_000, signed_on=today() - timedelta(days=days_ago))
    return o["inv"], o["work"], o["fee"]


async def _events(item_id: Any, kind: str) -> int:
    return await owner_val("SELECT count(*) FROM work.events WHERE item_id = %s AND kind = %s", (item_id, kind))


async def test_signing_opens_item_for_member_holding_accounting_write(who: Who) -> None:
    """Người phụ trách chọn theo ma trận quyền (ghi Kế toán, phạm vi toàn công ty) — không viết cứng vai trò."""
    ketoan = await who("ketoan")
    _, item, _ = await _signed(who, days_ago=1)
    assert await owner_val("SELECT owner_id FROM work.items WHERE id = %s", (item,)) == ketoan.user_id
    assert await _events(item, "created") == 1


async def test_lifecycle_overdue_draft_then_paid(who: Who, company: UUID) -> None:
    tid, ketoan, sale3 = company, await who("ketoan"), await who("sale3")
    inv, item, fee = await _signed(who, days_ago=40)  # hạn trả phí đã qua 10 ngày

    r = await work.step(tid, item, use_model=False)
    assert r["status"] == "waiting" and not r["final"]
    data = await owner_val("SELECT data FROM work.events WHERE item_id = %s AND kind = 'exception'", (item,))
    paper_id = data["cards"][0]["input"]["paper_id"]
    # dự thảo do TRỢ LÝ của kế toán soạn (đúng quyền kế toán, ghi nhật ký là agent), chưa ban hành
    assert await owner_val("SELECT status FROM papers.documents WHERE id = %s", (paper_id,)) == "draft"
    assert await owner_val("SELECT via FROM core.audit_log WHERE command = 'orderdesk.draft_fee_request' AND user_id = %s"
                           " ORDER BY id DESC LIMIT 1", (ketoan.user_id,)) == "agent"

    await work.step(tid, item, use_model=False)  # cùng ngày: không báo / soạn lại
    assert await _events(item, "exception") == 1

    await ok(ketoan, "acct.record_payment", code=inv, amount_vnd=fee // 2, bank_ref=f"W1-{inv}")
    await events.drain()
    assert await _events(item, "woke") == 1
    r = await work.step(tid, item, use_model=False)
    assert r["status"] == "waiting" and await _events(item, "exception") == 1  # còn nợ nhưng đã báo trong tuần
    # nút trên trang theo quyền người xem: kế toán phụ trách thấy "Tôi đã xử lý"; giám đốc chỉ có quyền XEM việc theo dõi ⇒ thấy trang, không thấy nút
    assert {a["command"] for a in (await q(ketoan, "work.view", item_id=item))["actions"]} == {"work.ack", "work.cancel"}
    assert (await q(await who("giamdoc"), "work.view", item_id=item))["actions"] == []

    await ok(ketoan, "work.ack", item_id=item, note="Đã gọi kế toán của khách, hẹn trả nốt thứ Sáu")
    await ok(ketoan, "acct.record_payment", code=inv, amount_vnd=fee - fee // 2, bank_ref=f"W2-{inv}")
    await events.drain()
    r = await work.step(tid, item, use_model=False)
    assert r == {"final": True, "status": "done", "sleep_s": 0}

    page = await q(ketoan, "work.view", item_id=item)
    timeline = page["sections"][2]["text"]
    assert "Cần người" in timeline and "Người xử lý" in timeline and "Xong" in timeline
    assert page["links"][0]["path"] == f"/v1/papers/{paper_id}/docx"
    # việc của kế toán: sale của giao dịch không thấy (work phạm vi own)
    assert all(row["id"] != item for row in (await q(sale3, "work.inbox", status="all"))["rows"])


async def test_agent_exception_runs_as_owner_with_narrow_tools(who: Who, company: UUID) -> None:
    inv, item, _ = await _signed(who, days_ago=45)
    await ok(await who("giamdoc"), "kb.add_document", title="Quy trình xử lý công nợ phí quá hạn (thử)", shelf="common", kind="policy",
             body="# Công nợ phí quá hạn\nQuá hạn trên 8 ngày: soạn công văn đôn đốc trình giám đốc ký. Quá 30 ngày: lập biên bản đối chiếu.")
    model = Scripted([
        [ToolCallPart("kb__search", {"q": "công nợ phí quá hạn"})],
        [ToolCallPart("orderdesk__draft_fee_request", {"code": inv})],
        "Khách quá hạn 15 ngày. Em đã soạn dự thảo đề nghị thanh toán; theo quy trình, quá 8 ngày cần công văn đôn đốc trình giám đốc ký.",
    ])
    r = await work.step(company, item, model=model)
    assert r["status"] == "waiting"
    assert model.tools_seen[0] == {"kb__search", "orderdesk__draft_fee_request"}  # đúng bộ hẹp, không cả ~50 công cụ
    assert "quy trình" in str(model.returns[0]).lower() or "đôn đốc" in str(model.returns[0])
    row = await owner_val("SELECT jsonb_build_object('note', last_note, 'runs', agent_runs) FROM work.items WHERE id = %s", (item,))
    assert row["runs"] == 1 and row["note"].startswith("Khách quá hạn")
    data = await owner_val("SELECT data FROM work.events WHERE item_id = %s AND kind = 'exception'", (item,))
    assert data["agent"] is True and data["cards"][-1]["query"] == "papers.view"


async def test_permissions_reloaded_each_run(who: Who, company: UUID) -> None:
    """Việc chạy bằng quyền người phụ trách NẠP LẠI mỗi lần: đổi vai mất quyền xem ⇒ việc dừng, không chạy bằng quyền hệ thống.
    Và chỉ giao được việc về thứ mình đang xem được."""
    admin, sale1 = await who("admin"), await who("sale1")
    sale9 = await helpers.fresh_member(who, team="KD-B")
    email = sale9.email
    deal = await helpers.order(who, owner=sale9, customer="Khách của người mới")

    r = await executor.run(sale1.as_agent(), "work.start", {"kind": "order_stage", "subject": f"{deal['code']}#2"})
    assert r.status == "not_found"  # trợ lý của sale1 không giao được việc về khách người khác
    out = await ok(sale9.as_agent(), "work.start", kind="order_stage", subject=f"{deal['code']}#2")
    item = out["item_id"]
    assert (await work.step(company, item, use_model=False))["status"] == "active"  # chưa tới hạn

    await approved(admin, "admin.update_member", email=email, role="marketer")
    r2 = await work.step(company, item, use_model=False)
    assert r2["status"] == "failed"
    assert "quyền" in await owner_val("SELECT last_note FROM work.items WHERE id = %s", (item,))


async def test_dbos_sleeps_then_wakes_on_receipt(who: Who, company: UUID, monkeypatch: pytest.MonkeyPatch) -> None:
    """Chạy thật trên DBOS: workflow kiểm → báo ngoại lệ → ngủ (hẹn 1 ngày) → phiếu thu đánh thức ngay → xong."""
    monkeypatch.setattr(work, "_has_model", lambda: False)
    ketoan = await who("ketoan")
    work_runtime.init()
    await work_runtime.launch(reconcile=False)
    try:
        inv, item, fee = await _signed(who, days_ago=40)  # ký ⇒ bên nghe mở việc ⇒ sau commit khởi động workflow
        for _ in range(100):
            if await owner_val("SELECT status FROM work.items WHERE id = %s", (item,)) == "waiting":
                break
            await asyncio.sleep(0.1)
        assert await owner_val("SELECT status FROM work.items WHERE id = %s", (item,)) == "waiting"

        await ok(ketoan, "acct.record_payment", code=inv, amount_vnd=fee, bank_ref=f"DB-{inv}")
        await events.drain()  # bên nghe fee_received ⇒ sau commit gửi "wake"
        handle = await work_runtime.DBOS.retrieve_workflow_async(work_runtime.workflow_id(item))
        assert await asyncio.wait_for(handle.get_result(), timeout=15) == "done"
        assert await owner_val("SELECT status FROM work.items WHERE id = %s", (item,)) == "done"
    finally:
        work_runtime.shutdown()


async def test_outcomes_count_what_code_closed_and_where_people_stepped_in(who: Who, company: UUID) -> None:
    """Bảng đo "bán theo kết quả": khép = điều kiện xong do code kiểm; tự khép = không ai phải vào (ack / dừng); người phạm vi hẹp không xem được
    (số toàn công ty — xem phần mình sẽ ra số thiếu mà tưởng đủ)."""
    tid, ketoan, gd = company, await who("ketoan"), await who("giamdoc")

    def row(page: dict[str, Any]) -> dict[str, Any]:
        return next((r for r in page["rows"] if r["id"] == "order_fee"), {"done": 0, "touched": 0, "opened": 0})

    before = row(await q(gd, "work.outcomes", days="30"))
    for touched in (False, True):
        inv, item, fee = await _signed(who, days_ago=40 if touched else 1)   # quá hạn ⇒ trợ lý báo cần người ⇒ kế toán vào xử lý
        if touched:
            assert (await work.step(tid, item, use_model=False))["status"] == "waiting"
            await ok(ketoan, "work.ack", item_id=item, note="Đã gọi kế toán của khách")
        await ok(ketoan, "acct.record_payment", code=inv, amount_vnd=fee, bank_ref=f"OUT-{inv}")
        await events.drain()
        assert (await work.step(tid, item, use_model=False))["status"] == "done"
    after = row(await q(gd, "work.outcomes", days="30"))
    assert (after["opened"] - before["opened"], after["done"] - before["done"], after["touched"] - before["touched"]) == (2, 2, 1)
    assert (await executor.query(await who("sale1"), "work.outcomes", {"days": "30"})).status == "denied"
