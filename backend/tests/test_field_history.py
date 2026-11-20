"""Lịch sử thay đổi theo trường (ADR 0246): ghi trước → sau cùng giao dịch với thay đổi, ai thấy đối tượng mới thấy lịch sử, giá trị nhạy cảm che theo
quyền (0239), nạp lịch sử im, phân biệt người / trợ lý / luật tự động; khối "Lịch sử thay đổi" trên thẻ khách và thẻ việc."""

import dataclasses
import uuid
from datetime import date, timedelta
from typing import Any

import pytest

from barista.db import db
from barista.kernel import executor, registry
from barista.kernel.clock import today
from barista.kernel.principal import Principal
from tests.conftest import Who, fresh_phone, ok, owner_val, q


async def _lead(sale: Principal) -> str:
    return str((await ok(sale, "crm.create_lead", name=f"Khách lịch sử {uuid.uuid4().hex[:6]}", phone=fresh_phone(), offering_code="HSR"))["lead_id"])


def _items(page: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for b in page["blocks"] if b.get("type") == "list" for i in b["items"]]


def _history_block(page: dict[str, Any]) -> dict[str, Any] | None:
    return next((b for b in page["blocks"] if b.get("type") == "list" and b.get("title") == "Lịch sử thay đổi"), None)


def _day(d: date) -> str:
    return f"{d.day} tháng {d.month}, {d.year}"


async def _rows(lead: str) -> int:
    return int(await owner_val("SELECT count(*) FROM core.field_changes WHERE subject_id = %s", (uuid.UUID(lead),)))


async def _visible_rows(p: Principal, lead: str) -> int:
    """Đếm bằng RLS của ``p`` (không qua truy vấn nào) — chứng minh chính Postgres lọc, không phải mã hiện thẻ."""
    async with db.tenant_tx(p) as conn:
        cur = await conn.execute("SELECT count(*) AS n FROM core.field_changes WHERE subject_id = %s", (uuid.UUID(lead),))
        row = await cur.fetchone()
    return int(row["n"]) if row else 0


async def test_owner_follow_up_and_budget_are_recorded_before_after_and_shown_on_the_lead(who: Who) -> None:
    sale1, sale2, truonga = await who("sale1"), await who("sale2"), await who("truonga")
    lead = await _lead(sale1)
    when = today() + timedelta(days=9)
    await ok(sale1, "crm.log_activity", lead_id=lead, kind="call", outcome="connected", next_follow_up=when.isoformat())
    await ok(sale1, "crm.log_activity", lead_id=lead, kind="note", note="không đổi hẹn", next_follow_up=when.isoformat())   # cũ = mới ⇒ không ghi
    await ok(sale1, "crm.update_interest", lead_id=lead, budget_vnd=5_000_000_000)
    await ok(truonga, "crm.assign", lead_id=lead, owner_email=sale2.email)
    assert await _rows(lead) == 3
    row = await owner_val("""SELECT jsonb_build_object('cmd', command, 'via', via, 'old', old_value, 'new', new_value) FROM core.field_changes
                             WHERE subject_id = %s AND field = 'owner'""", (uuid.UUID(lead),))
    assert row == {"cmd": "crm.assign", "via": "human", "old": str(sale1.user_id), "new": str(sale2.user_id)}

    titles = [i["title"] for i in _items(await q(sale2, "core.history", subject_kind="crm.lead", subject_id=lead))]
    assert titles == [f"Người phụ trách: {sale1.name} → {sale2.name}",              # mới nhất trước
                      "Ngân sách: chưa ghi → 5.000.000.000 đ",
                      f"Hẹn chăm sóc lại: {_day(today())} → {_day(when)}"]   # lead mới hẹn gọi hôm nay
    only = _items(await q(sale2, "core.history", subject_kind="crm.lead", subject_id=lead, which="hẹn"))
    assert [i["title"] for i in only] == [titles[2]]
    first = _items(await q(sale2, "core.history", subject_kind="crm.lead", subject_id=lead))[0]
    assert first["subtitle"].startswith(truonga.name)                                   # ai đổi
    block = _history_block(await q(sale2, "crm.journey", lead_id=lead))                 # khối trên thẻ khách, cạnh trao đổi
    assert block is not None and [i["title"] for i in block["items"]] == titles


async def test_history_follows_who_can_see_the_record(who: Who) -> None:
    sale1, sale2, sale3, truonga = await who("sale1"), await who("sale2"), await who("sale3"), await who("truonga")
    lead = await _lead(sale1)
    await ok(sale1, "crm.update_interest", lead_id=lead, budget_vnd=900_000_000)
    assert await _visible_rows(sale1, lead) == 1 and await _visible_rows(sale3, lead) == 0          # sale đội khác không thấy khách ⇒ không thấy lịch sử
    await ok(truonga, "crm.assign", lead_id=lead, owner_email=sale2.email)
    assert await _visible_rows(sale1, lead) == 0                                                   # người cũ mất khách ⇒ mất lịch sử ngay
    assert await _visible_rows(sale2, lead) == 2 and await _visible_rows(truonga, lead) == 2
    r = await executor.query(sale1, "core.history", {"subject_kind": "crm.lead", "subject_id": lead})
    assert r.status == "not_found"
    assert _history_block(await q(sale2, "crm.journey", lead_id=lead)) is not None


async def test_sensitive_values_are_hidden_without_the_resource(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    """Trường khai ``needs=`` (0239): người thấy khách nhưng thiếu nhóm thông tin chỉ thấy "đã đổi" — giá trị không rời máy chủ."""
    sale1, giamdoc = await who("sale1"), await who("giamdoc")
    key = ("crm.lead", "budget")
    monkeypatch.setitem(registry.TRACKED, key, dataclasses.replace(registry.TRACKED[key], needs=("core.audit:all",)))
    lead = await _lead(sale1)
    await ok(sale1, "crm.update_interest", lead_id=lead, budget_vnd=1_250_000_000)
    hidden = await q(sale1, "core.history", subject_kind="crm.lead", subject_id=lead)
    assert [i["title"] for i in _items(hidden)] == ["Ngân sách: đã đổi (bạn không được xem giá trị)"]
    assert "1.250.000.000" not in str(hidden)
    shown = await q(giamdoc, "core.history", subject_kind="crm.lead", subject_id=lead)
    assert [i["title"] for i in _items(shown)] == ["Ngân sách: chưa ghi → 1.250.000.000 đ"]


async def test_a_rolled_back_run_and_a_historical_load_leave_no_history(who: Who) -> None:
    sale1 = await who("sale1")
    lead = await _lead(sale1)
    r = await executor.run(sale1.as_agent(), "crm.update_interest", {"lead_id": lead, "budget_vnd": 7_000_000_000})
    assert r.status == "proposed"                                         # trợ lý soạn đề xuất = chạy thật trong điểm lưu rồi hoàn
    assert await _rows(lead) == 0
    r = await executor.run(sale1, "crm.update_interest", {"lead_id": lead, "budget_vnd": 7_000_000_000}, historical=True)
    assert r.status == "ok", r.error
    assert await _rows(lead) == 0                                         # nạp từ hệ cũ (0185): dữ liệu tham chiếu, không thành "vừa đổi"
    assert _history_block(await q(sale1, "crm.journey", lead_id=lead)) is None


async def test_assistant_and_automation_are_labelled(who: Who) -> None:
    sale1 = await who("sale1")
    lead = await _lead(sale1)
    with executor.tracing("chat:t-1"):
        r = await executor.run(sale1.as_agent(), "crm.log_activity", {"lead_id": lead, "kind": "call", "outcome": "connected",
                                                                      "next_follow_up": (today() + timedelta(days=3)).isoformat()})
    assert r.status == "ok", r.error
    with executor.tracing("auto:rule-1"):
        await ok(sale1, "crm.log_activity", lead_id=lead, kind="call", outcome="connected", next_follow_up=(today() + timedelta(days=5)).isoformat())
    subs = [i["subtitle"] for i in _items(await q(sale1, "core.history", subject_kind="crm.lead", subject_id=lead))]
    assert subs[0].endswith("luật tự động") and subs[1].endswith("trợ lý") and subs[0].startswith(sale1.name)
    assert await owner_val("SELECT array_agg(trace ORDER BY id) FROM core.field_changes WHERE subject_id = %s", (uuid.UUID(lead),)) == ["chat:t-1", "auto:rule-1"]


async def test_task_assignee_and_due_date_history_on_the_task_card(who: Who) -> None:
    sale1, sale2, truonga = await who("sale1"), await who("sale2"), await who("truonga")
    due, later = today() + timedelta(days=4), today() + timedelta(days=11)
    task = (await ok(truonga, "flow.create_task", title="Gửi bảng giá", assignee_email=sale1.email, due_on=due.isoformat()))["task_id"]
    await ok(truonga, "flow.adjust_task", task_id=task, assignee_email=sale2.email, due_on=later.isoformat())
    page = await q(sale2, "flow.task", task_id=task)
    block = _history_block(page)
    assert block is not None
    titles = {i["title"] for i in block["items"]}
    assert f"Người làm: {sale1.name} → {sale2.name}" in titles
    assert any(t.startswith(f"Hạn: {_day(due)} → ") for t in titles)        # hạn mới có thể dời qua ngày nghỉ — chỉ chốt hạn cũ


def test_declarations_are_owned_by_the_subjects_module() -> None:
    from barista.modules.crm import m as crm

    with pytest.raises(ValueError, match="chính mình"):
        crm.tracks("flow.task", "x", "X")
    with pytest.raises(ValueError, match="kiểu"):
        crm.tracks("crm.lead", "x", "X", fmt="choice")                    # choice phải có nhãn
    for kind, _ in registry.TRACKED:
        assert kind in registry.SUBJECTS, f"{kind}: lịch sử chỉ cho đối tượng đã khai m.subject (hàm thấy-được ở DB)"


async def test_tracking_an_undeclared_field_or_another_modules_field_is_refused(who: Who) -> None:
    sale1 = await who("sale1")
    async with db.tenant_tx(sale1, rollback=True) as conn:
        ctx = executor.core_ctx(conn, sale1, module="flow")
        with pytest.raises(TypeError, match="thuộc module crm"):
            await ctx.track("crm.lead", uuid.uuid4(), "owner", None, sale1.user_id)
        with pytest.raises(TypeError, match="chưa khai"):
            await ctx.track("crm.lead", uuid.uuid4(), "nope", 1, 2)


async def test_every_tracked_kind_has_a_db_visibility_function() -> None:
    kinds = sorted({k for k, _ in registry.TRACKED})
    known = await owner_val("SELECT array_agg(kind) FROM core.subject_kinds WHERE kind = ANY(%s)", (kinds,))
    assert sorted(known or []) == kinds
