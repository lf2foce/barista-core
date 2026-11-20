"""Mức dùng theo gói (0163): ``admin.pack_usage`` — người hoạt động / lệnh / lượt xem theo (gói, tháng), tách người bấm và trợ lý, chỉ số đếm, theo công ty (RLS).
Lệnh lấy từ ``core.audit_log``, lượt xem từ ``core.usage_reads`` (executor gom trong bộ nhớ, ``usage.flush`` ghi); module không thuộc gói ⇒ "Lõi"."""

import json
import uuid
from datetime import timedelta
from typing import Any

import pytest

from barista import seed
from barista.db import db
from barista.kernel import clock, executor, registry, usage
from tests.conftest import owner_val, q

ADMIN = ("admin", "Quản trị", "admin", "QT", True)
STAFF = ("kt", "Kế toán", "accountant", "KT", False)
PACK = registry.MODULE_PACK["acct"]


async def _company(label: str = "mức dùng") -> tuple[Any, Any, Any]:
    d = f"{uuid.uuid4().hex[:6]}.mucdung.test"
    tid = await seed.create_tenant(f"Công ty {label} (test)", d, packs=("finance",), people=[ADMIN, STAFF])
    return tid, await seed.who(tid, "admin", d), await seed.who(tid, "kt", d)


async def _log(tid: Any, user: Any, command: str, *, status: str = "ok", via: str = "human", system: bool = False, days_ago: int = 0) -> None:
    await owner_val("""INSERT INTO core.audit_log (tenant_id, user_id, via, command, status, system, input, at)
                       VALUES (%s, %s, %s, %s, %s, %s, '{}', now() - make_interval(days => %s)) RETURNING 1""",
                    (tid, user, via, command, status, system, days_ago))


def _cell(out: dict[str, Any], pack: str, month: str | None = None) -> dict[str, Any]:
    row = next(p for p in out["packs"] if p["pack"] == pack)
    return next(c for c in row["months"] if c["month"] == (month or out["months"][-1]))


async def test_commands_are_counted_per_pack_and_month_from_the_audit_log() -> None:
    tid, admin, staff = await _company()
    for _ in range(3):
        await _log(tid, admin.user_id, "acct.anything")
    await _log(tid, staff.user_id, "acct.other")
    await _log(tid, staff.user_id, "acct.other", status="denied")            # không thành công ⇒ không tính
    await _log(tid, admin.user_id, "acct.sys", system=True)                  # hệ thống ⇒ không tính
    await _log(tid, admin.user_id, "ghostmod.x")                              # module không thuộc gói ⇒ Lõi
    await _log(tid, admin.user_id, "acct.old", days_ago=40)                   # tháng trước (hoặc trước nữa) — ngoài tháng hiện tại
    out = await q(admin, "admin.pack_usage", months=3)
    assert len(out["months"]) == 3 and out["months"][-1] == clock.today().isoformat()[:7]
    cur = _cell(out, PACK)
    assert (cur["humans"], cur["commands"], cur["reads"]) == (2, 4, 0)
    assert (cur["assistant_users"], cur["assistant_commands"]) == (0, 0)
    assert _cell(out, "core")["commands"] == 1
    older = [c for c in next(p for p in out["packs"] if p["pack"] == PACK)["months"] if c["month"] != out["months"][-1] and c["commands"]]
    assert sum(c["commands"] for c in older) == 1
    assert json.dumps(out).count("ghostmod") == 0                              # chỉ đếm, không lộ tên lệnh


async def test_months_are_capped_and_default_range_is_small() -> None:
    _, admin, _ = await _company()
    assert len((await q(admin, "admin.pack_usage"))["months"]) == 6
    assert len((await q(admin, "admin.pack_usage", months=12))["months"]) == 12
    r = await executor.query(admin, "admin.pack_usage", {"months": 60})
    assert r.status == "invalid"


async def test_a_user_who_only_runs_queries_is_counted_and_assistant_traffic_is_split() -> None:
    tid, admin, staff = await _company()
    usage._pending.clear()
    for _ in range(3):
        assert (await executor.query(staff, "core.notifications", {})).status == "ok"       # chỉ xem — không để lại dòng nhật ký
    assert (await executor.query(staff.as_agent(), "core.notifications", {})).status == "ok"  # trợ lý làm thay người này
    assert await owner_val("SELECT count(*) FROM core.audit_log WHERE tenant_id = %s AND user_id = %s AND command = 'core.notifications'",
                           (tid, staff.user_id)) == 0
    assert await usage.flush() >= 2
    out = await q(admin, "admin.pack_usage")
    base = registry.MODULE_PACK["core"]
    cell = _cell(out, base)
    assert cell["humans"] >= 1 and cell["reads"] == 3 and cell["commands"] == 0
    assert cell["assistant_users"] == 1 and cell["assistant_reads"] == 1 and cell["assistant_commands"] == 0
    # chỉ trợ lý (người không tự bấm gì) ⇒ KHÔNG làm phồng số người
    await _log(tid, admin.user_id, "acct.run", via="agent")
    cell2 = _cell(await q(admin, "admin.pack_usage"), PACK)
    assert (cell2["humans"], cell2["assistant_users"], cell2["assistant_commands"]) == (0, 1, 1)


async def test_usage_is_per_company_and_non_admins_are_denied() -> None:
    tid, admin, staff = await _company("A")
    other_tid, other_admin, _ = await _company("B")
    await _log(tid, admin.user_id, "acct.x")
    await _log(other_tid, other_admin.user_id, "acct.x")
    await _log(other_tid, other_admin.user_id, "acct.y")
    assert _cell(await q(admin, "admin.pack_usage"), PACK)["commands"] == 1
    assert _cell(await q(other_admin, "admin.pack_usage"), PACK)["commands"] == 2
    r = await executor.query(staff, "admin.pack_usage", {})
    assert r.status == "denied"
    store = json.dumps(await q(admin, "admin.pack_store"), ensure_ascii=False)
    assert "Mức dùng" in store                                                 # quản trị thấy mục Mức dùng trong Store
    r = await executor.query(staff, "admin.pack_store", {})
    assert r.status == "denied"


async def test_the_store_shows_usage_to_admins() -> None:
    tid, admin, _ = await _company()
    await _log(tid, admin.user_id, "acct.x")
    store = await q(admin, "admin.pack_store")
    block = next(b for b in store["blocks"] if b.get("type") == "list" and b["title"].startswith("Mức dùng"))
    assert any("1 người" == i["badge"] and "1 lệnh" in i["subtitle"] for i in block["items"])


async def test_a_failing_counter_write_never_fails_the_query(monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, staff = await _company()
    usage._pending.clear()

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("DB chết")
    monkeypatch.setattr(db, "owner_tx", boom)
    assert (await executor.query(staff, "core.notifications", {})).status == "ok"
    assert usage._pending                                                       # đã đếm trong bộ nhớ
    assert await usage.flush() == 0                                             # ghi hỏng ⇒ bỏ lô, không ném
    assert not usage._pending
    monkeypatch.setattr(usage, "note_query", boom)                              # bộ đếm hỏng hẳn
    assert (await executor.query(staff, "core.notifications", {})).status == "ok"


async def test_one_company_failing_to_flush_does_not_drop_the_others() -> None:
    tid, _, staff = await _company()
    usage._pending.clear()
    usage._pending[(uuid.uuid4(), clock.today(), "", staff.user_id, "human")] = 5        # công ty không tồn tại ⇒ khoá ngoại lỗi
    usage._pending[(tid, clock.today() - timedelta(days=0), "", staff.user_id, "human")] = 2
    assert await usage.flush() == 1
    assert await owner_val("SELECT sum(queries) FROM core.usage_reads WHERE tenant_id = %s", (tid,)) == 2


async def test_system_link_and_app_callers_are_not_counted() -> None:
    tid, admin, _ = await _company()
    usage._pending.clear()
    from barista.kernel.principal import system_principal
    await executor.query(system_principal(tid), "core.notifications", {})
    await executor.query(admin, "core.notifications", {}, quiet=True)           # kiểm tra lặp của kênh tức thì không tính
    assert not usage._pending
