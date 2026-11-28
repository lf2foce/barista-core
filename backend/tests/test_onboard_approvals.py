"""KHUNG CHUYỂN DỮ LIỆU — lệnh phải qua duyệt (ADR 0263, bổ sung sau diễn tập) trên dữ liệu GIẢ: lệnh thành phiếu ⇒ mã phiếu ghi vào sổ, lượt sau ghi nhận phiếu
ĐÃ thực hiện (không còn là ngoại lệ), phiếu bị từ chối thành ngoại lệ; ``approver_email`` = một NGƯỜI THỨ HAI có thật quyết phiếu của lượt qua đúng
``core.decide`` (ghi tên người duyệt) — người chạy không tự duyệt, trợ lý / quyền hệ thống / cửa HTTP không dùng được lối này; phiếu sinh trong lượt nạp không
gửi thông báo (bất biến 12)."""

import pathlib
from typing import Any
from uuid import UUID

import pytest

from barista import seed
from barista.api import onboard as ob
from barista.kernel import executor
from barista.kernel.principal import system_principal
from tests.conftest import approved, edge, owner_val
from tests.test_onboard_load import _names, company, people_entity

ROWS = [{"source_id": "1", "name": "Người Giả Một"}, {"source_id": "2", "name": "Người Giả Hai"}, {"source_id": "3", "name": "Người Giả Ba"}]
CHAIN = "onboard_signoff"   # chuỗi mặc định của lõi: một bước chủ tài khoản (ở công ty thử: giám đốc)


async def _ruled() -> tuple[UUID, str, Any, Any]:
    """Công ty đặt luật: thêm cá nhân / tổ chức phải qua chủ tài khoản duyệt."""
    tid, domain = await company()
    admin, gd = await seed.who(tid, "admin", domain), await seed.who(tid, "gd", domain)
    await approved(admin, "admin.set_rule", command="parties.create", chain=CHAIN)
    return tid, domain, admin, gd


async def _slips(tid: UUID) -> dict[str, str]:
    """mã gốc → mã phiếu đang theo của lượt."""
    from barista.db import db

    async with db.owner.connection() as c:
        rows = await (await c.execute("SELECT source_id, approval_id FROM onboard.pending WHERE tenant_id = %s AND state = 'waiting'", (tid,))).fetchall()
    return {r["source_id"]: str(r["approval_id"]) for r in rows}


async def _notifications(tid: UUID) -> int:
    return await owner_val("SELECT count(*) FROM core.notifications WHERE tenant_id = %s", (tid,))


async def test_a_slip_approved_later_is_recorded_by_the_next_run_and_a_rejected_one_is_an_exception() -> None:
    tid, domain, _, gd = await _ruled()
    conv = ob.Converter("hecu", (people_entity(ROWS),))
    before = await _notifications(tid)
    first = await ob.load(tid, conv, by_email=f"admin@{domain}")
    assert (first.created, first.exceptions) == (0, 3)
    assert first.reasons == [{"entity": "person", "reason": "needs_approval", "n": 3}]
    assert first.warnings and "parties.create" in first.warnings[0] and CHAIN in first.warnings[0]   # báo trước: lệnh này đang có luật duyệt
    slips = await _slips(tid)
    assert set(slips) == {"1", "2", "3"} and await _names(tid) == []
    assert await _notifications(tid) == before   # phiếu của lượt nạp lịch sử không gửi "xin duyệt" cho ai
    # người duyệt bấm trong app: duyệt 1 và 2, từ chối 3
    for sid, approve in (("1", True), ("2", True), ("3", False)):
        r = await executor.run(edge(gd), "core.decide", {"approval_id": slips[sid], "approve": approve})
        assert r.status == "ok", r.error
    second = await ob.load(tid, conv, by_email=f"admin@{domain}")
    assert (second.created, second.skipped, second.exceptions) == (2, 0, 1)   # phiếu đã thực hiện ⇒ ghi nhận, không chạy lại, không còn là ngoại lệ
    assert second.reasons == [{"entity": "person", "reason": "rejected", "n": 1}]
    assert await _names(tid) == ["Người Giả Hai", "Người Giả Một"]
    assert await owner_val("SELECT count(*) FROM onboard.source_ids s JOIN parties.parties p ON p.id = s.target_id WHERE s.tenant_id = %s", (tid,)) == 2
    assert [x["status"] for x in second.recon if x["metric"] == "count.person"] == ["lech"]   # 3 trích, 2 đã nạp
    third = await ob.load(tid, conv, by_email=f"admin@{domain}")
    assert (third.created, third.skipped, third.exceptions) == (0, 2, 1)
    assert await owner_val("SELECT count(*) FROM core.approvals WHERE tenant_id = %s AND command = 'parties.create'", (tid,)) == 3   # không tự gửi lại phiếu bị từ chối
    # nguồn của dòng bị từ chối được sửa ⇒ gửi phiếu MỚI (lần gửi 1), duyệt ⇒ lượt sau ghi nhận
    rows = [*ROWS[:2], {"source_id": "3", "name": "Người Giả Ba (đã sửa)"}]
    conv2 = ob.Converter("hecu", (people_entity(rows),))
    fourth = await ob.load(tid, conv2, by_email=f"admin@{domain}")
    assert fourth.reasons == [{"entity": "person", "reason": "needs_approval", "n": 1}]
    again = (await _slips(tid))["3"]
    assert again != slips["3"] and await owner_val("SELECT attempt FROM onboard.pending WHERE tenant_id = %s AND source_id = '3'", (tid,)) == 1
    assert (await executor.run(edge(gd), "core.decide", {"approval_id": again, "approve": True})).status == "ok"
    fifth = await ob.load(tid, conv2, by_email=f"admin@{domain}")
    assert (fifth.created, fifth.skipped, fifth.exceptions) == (1, 2, 0)
    assert await _names(tid) == ["Người Giả Ba (đã sửa)", "Người Giả Hai", "Người Giả Một"]
    assert await _slips(tid) == {}


async def test_a_second_person_approves_the_slips_of_the_run_and_is_named_on_them() -> None:
    tid, domain, admin, gd = await _ruled()
    conv = ob.Converter("hecu", (people_entity(ROWS),))
    before = await _notifications(tid)
    r = await ob.load(tid, conv, by_email=f"admin@{domain}", approver_email=f"gd@{domain}")
    assert (r.created, r.exceptions, r.approvals_decided, r.warnings) == (3, 0, 3, [])
    assert await _names(tid) == ["Người Giả Ba", "Người Giả Hai", "Người Giả Một"]
    from barista.db import db

    async with db.owner.connection() as c:
        slips = await (await c.execute("SELECT status, requested_by, decisions FROM core.approvals WHERE tenant_id = %s AND command = 'parties.create'",
                                       (tid,))).fetchall()
        audit = await (await c.execute("""SELECT user_id, approved_by FROM core.audit_log WHERE tenant_id = %s AND command = 'parties.create' AND status = 'ok'""",
                                       (tid,))).fetchall()
    assert len(slips) == 3 and all(s["status"] == "executed" and s["requested_by"] == admin.user_id for s in slips)
    assert all([d["user_id"] for d in s["decisions"]] == [str(gd.user_id)] and "lượt chuyển dữ liệu" in s["decisions"][0]["note"] for s in slips)
    # lệnh đích chạy bằng quyền NGƯỜI XIN, nhật ký ghi người duyệt (bất biến 6) — không quyền hệ thống
    assert len(audit) == 3 and all(a["user_id"] == admin.user_id and a["approved_by"] == [gd.user_id] for a in audit)
    assert await _notifications(tid) == before   # không "xin duyệt", không "phiếu đã được duyệt"
    again = await ob.load(tid, conv, by_email=f"admin@{domain}", approver_email=f"gd@{domain}")
    assert (again.created, again.skipped, again.approvals_decided) == (0, 3, 0)
    assert await owner_val("SELECT count(*) FROM core.approvals WHERE tenant_id = %s AND command = 'parties.create'", (tid,)) == 3


async def test_a_slip_left_waiting_is_settled_by_the_approver_of_a_later_run() -> None:
    tid, domain, _, _ = await _ruled()
    conv = ob.Converter("hecu", (people_entity(ROWS),))
    assert (await ob.load(tid, conv, by_email=f"admin@{domain}")).exceptions == 3
    r = await ob.load(tid, conv, by_email=f"admin@{domain}", approver_email=[f"gd@{domain}"])
    assert (r.created, r.exceptions, r.approvals_decided) == (3, 0, 3)
    assert await owner_val("SELECT count(*) FROM core.approvals WHERE tenant_id = %s AND command = 'parties.create'", (tid,)) == 3   # đúng phiếu cũ, không gửi lại


async def test_the_runner_never_approves_and_an_outsider_to_the_chain_decides_nothing() -> None:
    tid, domain, _, _ = await _ruled()
    conv = ob.Converter("hecu", (people_entity(ROWS),))
    with pytest.raises(PermissionError, match="không tự duyệt"):
        await ob.load(tid, conv, by_email=f"admin@{domain}", approver_email=f"admin@{domain}")
    actor = ob.Converter("hecu", (ob.Entity("person", lambda: list(ROWS), "parties.create", lambda r, ids: {"display_name": r["name"], "kind": "person"},
                                            "party_id", actor="nhap"),))
    with pytest.raises(PermissionError, match="không tự duyệt"):   # người chạy của MỘT thực thể cũng không làm người duyệt của lượt
        await ob.load(tid, actor, by_email=f"admin@{domain}", actors={"nhap": f"gd@{domain}"}, approver_email=f"gd@{domain}")
    with pytest.raises(PermissionError, match="thành viên"):
        await ob.load(tid, conv, by_email=f"admin@{domain}", approver_email="nguoi-ngoai@example.invalid")
    assert await owner_val("SELECT count(*) FROM onboard.runs WHERE tenant_id = %s", (tid,)) == 0
    # người thứ hai có thật nhưng KHÔNG có tên trong chuỗi duyệt ⇒ không quyết được gì: dòng vẫn chờ, không ai được nâng quyền
    r = await ob.load(tid, conv, by_email=f"admin@{domain}", approver_email=f"a@{domain}")
    assert (r.created, r.exceptions, r.approvals_decided) == (0, 3, 0)
    assert await owner_val("SELECT count(*) FROM core.approvals WHERE tenant_id = %s AND command = 'parties.create' AND status = 'pending'", (tid,)) == 3
    assert "không người duyệt nào" in await owner_val("SELECT detail FROM onboard.exceptions WHERE run_id = %s LIMIT 1", (r.run_id,))


async def test_the_load_decision_door_is_closed_to_assistants_system_http_and_the_requester() -> None:
    tid, domain, admin, gd = await _ruled()
    await ob.load(tid, ob.Converter("hecu", (people_entity(ROWS),)), by_email=f"admin@{domain}")
    aid = (await _slips(tid))["1"]
    import dataclasses

    denied = [await executor.decide_in_load(gd.as_agent(), aid), await executor.decide_in_load(system_principal(tid, gd.user_id), aid),
              await executor.decide_in_load(system_principal(tid), aid),
              await executor.decide_in_load(dataclasses.replace(gd), aid),   # tay cầm không do lõi cấp (chép / sửa)
              await executor.decide_in_load(edge(gd), aid)]                  # tay cầm của một request HTTP: lối này không dành cho cửa HTTP
    assert [r.status for r in denied] == ["denied"] * 5
    own = await executor.decide_in_load(admin, aid)   # người xin quyết phiếu của chính mình
    assert own.status == "denied" and "không phải người duyệt" in (own.error or "")
    # đường thường (kể cả người duyệt đúng) vẫn chỉ nhận người bấm ở cửa HTTP; ô đầu vào không bật được lối của lớp nạp
    plain = await executor.run(gd, "core.decide", {"approval_id": aid, "approve": True, "in_load": True, "historical": True})
    assert plain.status == "denied" and "0144" in (plain.error or "")
    assert await owner_val("SELECT status FROM core.approvals WHERE id = %s", (UUID(aid),)) == "pending"
    ok_ = await executor.decide_in_load(gd, aid, note="Duyệt thử")
    assert ok_.status == "ok" and ok_.output["status"] == "executed"
    after = await executor.run(gd, "core.decide", {"approval_id": (await _slips(tid))["2"], "approve": True})   # lối không "dính" lại sau một lần dùng
    assert after.status == "denied"


def test_only_the_core_load_layer_reaches_the_load_decision_door() -> None:
    """CẤU TRÚC: chỉ lớp nạp của lõi gọi lối quyết phiếu trong tiến trình — route HTTP, trợ lý, module không nhắc tới."""
    src = pathlib.Path(__file__).resolve().parents[1] / "src" / "barista"
    users = sorted(str(f.relative_to(src)) for f in src.rglob("*.py") if "decide_in_load" in f.read_text() or "_LOAD_DECIDER" in f.read_text())
    assert users == ["kernel/executor.py", "kernel/onboard.py"]
    assert (src / "kernel" / "executor.py").read_text().count("_LOAD_DECIDER.set(") == 1
