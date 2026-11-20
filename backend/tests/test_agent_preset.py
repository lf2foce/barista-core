"""Mức tự chủ đặt sẵn của trợ lý (0144 — "preset chỉ là GIAO DIỆN"): ba mức ghi vào dữ liệu đã có (công tắc + chính sách); mức đang áp SUY từ dữ
liệu (sửa một lệnh ⇒ "Tuỳ chỉnh"); nới = MỘT phiếu chủ tài khoản nói rõ nới những lệnh nào; siết áp ngay; công ty mới có luật duyệt sẵn."""

import uuid
from typing import Any

import pytest

from barista import seed
from barista.kernel import executor, registry
from barista.kernel.clock import today
from barista.modules import admin as admin_mod
from tests.conftest import approved, ok, owner_val, q

PEOPLE = [("giamdoc", "Giám đốc thử", "director", "HQ", True), ("admin", "Quản trị thử", "admin", "QT", True),
          ("nv", "Nhân viên thử", "staff", "HCNS", False)]


async def _co() -> dict[str, Any]:
    d = f"p{uuid.uuid4().hex[:8]}.demo"
    return {"tid": await seed.create_tenant(f"Công ty mức tự chủ {d}", d, people=PEOPLE, packs=(), about="công ty thử (test)"), "d": d}


async def _picker(p: Any) -> dict[str, Any]:
    page = await q(p, "admin.agent_policies")
    return page["header"][0]


async def test_fresh_company_has_the_rule_and_loosening_needs_the_owner_ticket() -> None:
    co = await _co()
    admin = await seed.who(co["tid"], "admin", co["d"])
    nv = await seed.who(co["tid"], "nv", co["d"])
    assert await owner_val("SELECT chain FROM core.approval_rules WHERE tenant_id = %s AND command = 'admin.set_agent_preset'",
                           (co["tid"],)) == "access_change"
    pick = await _picker(admin)
    assert pick["type"] == "picker" and pick["current"] == "" and pick["custom"] == "Chưa chọn"
    r = await executor.run(admin, "admin.set_agent_preset", {"preset": "full"})
    assert r.status == "pending_approval", r
    summary = await owner_val("SELECT summary FROM core.approvals WHERE id = %s", (uuid.UUID(r.approval_id or ""),))
    assert "Bật Toàn quyền: trợ lý tự làm 3 lệnh" in summary and "Thêm việc" in summary and "Đặt lịch hẹn" in summary
    # chưa duyệt ⇒ chưa đổi gì, màn nói đang chờ chủ tài khoản
    assert await owner_val("SELECT count(*) FROM core.agent_policies WHERE tenant_id = %s", (co["tid"],)) == 0
    assert (await _picker(admin))["pending"] == "Toàn quyền: chờ chủ tài khoản duyệt"
    inp = {"title": "Việc trợ lý tạo", "assignee_email": nv.email, "due_on": today().isoformat()}
    assert (await executor.run(nv.as_agent(), "flow.create_task", inp, attended=True)).status == "proposed"


async def test_presets_write_existing_data_and_the_screen_reads_them_back() -> None:
    co = await _co()
    admin = await seed.who(co["tid"], "admin", co["d"])
    nv = await seed.who(co["tid"], "nv", co["d"])
    inp = {"title": "Việc trợ lý tạo", "assignee_email": nv.email, "due_on": today().isoformat()}
    # Cân bằng: chỉ công tắc
    await approved(admin, "admin.set_agent_preset", preset="balanced")
    assert (await _picker(admin))["current"] == "balanced"
    assert await owner_val("SELECT autorun_low_risk FROM core.agent_trust WHERE tenant_id = %s", (co["tid"],)) is True
    assert (await executor.run(nv.as_agent(), "flow.create_task", inp, attended=True)).status == "ok"
    # Toàn quyền: nới từng lệnh promotable, không cần thành tích (chủ tài khoản đã duyệt cả mức), có căn cứ "preset"
    await approved(admin, "admin.set_agent_preset", preset="full")
    assert (await _picker(admin))["current"] == "full"
    rows = await owner_val("""SELECT jsonb_object_agg(command, basis->>'preset') FROM core.agent_policies WHERE tenant_id = %s AND mode = 'allow'""",
                           (co["tid"],))
    assert rows == {o.name: "full" for o in registry.OPS.values() if o.promotable}
    # một lệnh sửa tay ⇒ Tuỳ chỉnh
    await ok(admin, "admin.set_agent_policy", command="flow.adjust_task", mode="propose")
    pick = await _picker(admin)
    assert pick["current"] == "" and pick["custom"] == "Tuỳ chỉnh"
    # từ Tuỳ chỉnh có lệnh "Không được" sang Thận trọng (cả nhóm "chỉ đề xuất") = NỚI lệnh đó ⇒ phiếu, nêu đúng lệnh
    await ok(admin, "admin.set_agent_policy", command="flow.set_task", mode="deny")
    r = await executor.run(admin, "admin.set_agent_preset", {"preset": "careful"})
    assert r.status == "pending_approval"
    assert "Đổi trạng thái việc" in await owner_val("SELECT summary FROM core.approvals WHERE id = %s", (uuid.UUID(r.approval_id or ""),))
    await ok(admin, "admin.set_agent_policy", command="flow.set_task", mode="")
    # Thận trọng = siết ⇒ áp NGAY, không phiếu; mọi nhóm ghi chỉ đề xuất, tắt công tắc
    before = await owner_val("SELECT count(*) FROM core.approvals WHERE tenant_id = %s", (co["tid"],))
    out = await ok(admin, "admin.set_agent_preset", preset="careful")
    assert out["loosened"] == [] and (await _picker(admin))["current"] == "careful"
    assert await owner_val("SELECT count(*) FROM core.approvals WHERE tenant_id = %s", (co["tid"],)) == before
    assert await owner_val("SELECT mode FROM core.agent_policies WHERE tenant_id = %s AND command = 'flow.*'", (co["tid"],)) == "propose"
    assert (await executor.run(nv.as_agent(), "flow.create_task", inp, attended=True)).status == "proposed"


async def test_going_down_from_full_applies_immediately_and_group_off_is_kept() -> None:
    co = await _co()
    admin = await seed.who(co["tid"], "admin", co["d"])
    await approved(admin, "admin.set_agent_preset", preset="full")
    await ok(admin, "admin.set_agent_group", group="crm.*", mode="deny")   # nhóm "Tắt" là chuyện khác — mức giữ nguyên
    before = await owner_val("SELECT count(*) FROM core.approvals WHERE tenant_id = %s", (co["tid"],))
    r = await executor.run(admin, "admin.set_agent_preset", {"preset": "balanced"})
    assert r.status == "ok", r
    assert await owner_val("SELECT count(*) FROM core.approvals WHERE tenant_id = %s", (co["tid"],)) == before
    assert await owner_val("SELECT mode FROM core.agent_policies WHERE tenant_id = %s AND command = 'crm.*'", (co["tid"],)) == "deny"
    assert (await _picker(admin))["current"] == "balanced"


def test_preset_of_reads_data_not_a_stored_name() -> None:
    groups = {g: "propose" for g in admin_mod._write_groups()}
    assert admin_mod.preset_of({}, False) == "default"
    assert admin_mod.preset_of(groups, False) == "careful"
    assert admin_mod.preset_of({"crm.*": "deny"}, True) == "balanced"
    full = {o.name: "allow" for o in registry.OPS.values() if o.promotable}
    assert admin_mod.preset_of(full, True) == "full"
    assert admin_mod.preset_of({**full, "flow.set_task": "deny"}, True) == ""
    assert admin_mod.preset_of({"flow.*": "propose"}, True) == ""


@pytest.mark.parametrize("preset", ["careful", "balanced", "full"])
def test_presets_never_allow_money_or_authority(preset: str) -> None:
    new, on = admin_mod._preset_target(preset, {})
    for op in registry.OPS.values():
        if op.kind == "command" and not op.internal and executor.effective_mode(op, new, on) == "allow" and op.agent != "allow":
            assert op.promotable, op.name


async def test_demo_profiles_seed_full_autonomy_once_and_real_installs_stay_off() -> None:
    co = await _co()
    admin = await seed.who(co["tid"], "admin", co["d"])
    assert (await _picker(admin))["custom"] == "Chưa chọn"   # công ty thật: install_defaults không đặt mức nào
    assert await seed.demo_autonomy(co["tid"]) == "full"
    assert (await _picker(admin))["current"] == "full"
    await ok(admin, "admin.set_agent_preset", preset="careful")   # người xem demo đổi ⇒ chạy lại / --upgrade không đè
    assert await seed.demo_autonomy(co["tid"]) is None
    assert (await _picker(admin))["current"] == "careful"
