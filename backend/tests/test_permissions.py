"""Ma trận quyền — thứ dễ sai nhất và đắt nhất. Mỗi khẳng định chạy qua executor như request thật (RLS ở DB)."""

from barista.kernel import executor
from tests.conftest import Who, approved, edge, fresh_phone, ok, owner_val, q


async def _lead_owners(p) -> set[str]:
    page = await q(p, "crm.leads", stage="")
    return {r["owner"] for r in page["rows"]}


async def test_lead_visibility_follows_scope(who: Who) -> None:
    sale1, truonga, truongb, giamdoc = await who("sale1"), await who("truonga"), await who("truongb"), await who("giamdoc")
    assert await _lead_owners(sale1) == {"Nguyễn Văn An"}
    # test khác có thể thêm người vào đội: kiểm người mẫu của đội mình có mặt, người đội kia không
    team_a, team_b = await _lead_owners(truonga), await _lead_owners(truongb)
    assert {"Nguyễn Văn An", "Hoàng Thị Bích"} <= team_a and "Đặng Gia Khánh" not in team_a  # cả đội A, không thấy đội B
    assert "Đặng Gia Khánh" in team_b and not {"Nguyễn Văn An", "Hoàng Thị Bích"} & team_b
    total = await owner_val("SELECT count(*) FROM crm.leads WHERE tenant_id = %s", (giamdoc.tenant_id,))
    assert len((await q(giamdoc, "crm.leads", stage=""))["rows"]) == total


async def test_roles_without_crm_cannot_read_customers(who: Who) -> None:
    for local in ("mkt", "ketoan"):
        r = await executor.query(await who(local), "crm.leads", {})
        assert r.status == "denied", local


async def test_marketing_sees_counts_not_customers(who: Who) -> None:
    page = await q(await who("mkt"), "mkt.campaigns")
    assert sum(r["leads"] for r in page["rows"]) > 0
    assert all("phone" not in r and "customer" not in r for r in page["rows"])


async def test_sale_cannot_reassign_and_lead_cannot_move_outside_team(who: Who) -> None:
    sale1, truonga = await who("sale1"), await who("truonga")
    lead_id = (await q(sale1, "crm.leads"))["rows"][0]["id"]
    r = await executor.run(sale1, "crm.assign", {"lead_id": lead_id, "owner_email": "sale2@sanmau.demo"})
    assert r.status == "denied"
    r = await executor.run(truonga, "crm.assign", {"lead_id": lead_id, "owner_email": "sale3@sanmau.demo"})
    assert r.status == "denied", "trưởng nhóm A chuyển khách sang đội B phải bị RLS chặn"
    await ok(truonga, "crm.assign", lead_id=lead_id, owner_email="sale2@sanmau.demo")
    await ok(truonga, "crm.assign", lead_id=lead_id, owner_email="sale1@sanmau.demo")


async def test_duplicate_phone_does_not_reveal_owner(who: Who) -> None:
    sale1, sale3 = await who("sale1"), await who("sale3")
    phone = fresh_phone()
    await ok(sale1, "crm.create_lead", name="Khách A", phone=phone)
    r = await executor.run(sale3, "crm.create_lead", {"name": "Khách A", "phone": phone})
    assert r.status == "conflict"
    assert "Nguyễn" not in r.error and "sale1" not in r.error


async def test_grant_change_takes_effect_and_last_admin_is_kept(who: Who) -> None:
    admin = await who("admin")
    await approved(admin, "admin.set_grant", role="sale", resource="crm", scope="team", actions=["read", "write"])
    try:
        # sale1 không làm trưởng đội nào ⇒ phạm vi team = của mình + (không đội nào) — vẫn chỉ thấy của mình
        assert await _lead_owners(await who("sale1")) == {"Nguyễn Văn An"}
        await approved(admin, "admin.update_member", email="sale1@sanmau.demo", is_lead=True)
        assert await _lead_owners(await who("sale1")) == {"Nguyễn Văn An", "Hoàng Thị Bích"}
    finally:
        await approved(admin, "admin.update_member", email="sale1@sanmau.demo", is_lead=False)
        await approved(admin, "admin.set_grant", role="sale", resource="crm", scope="own", actions=["read", "write"])
    # bỏ quyền quản trị của mọi quản trị: thành phiếu chờ chủ tài khoản (0029); duyệt xong vẫn bị chặn vì phải còn một quản trị
    r = await executor.run(admin, "admin.set_grant", {"role": "admin", "resource": "core.admin", "scope": "all", "actions": []})
    assert r.status == "pending_approval"
    gd = await who("giamdoc")
    d = await executor.run(edge(gd), "core.decide", {"approval_id": r.approval_id, "approve": True})
    assert d.output["status"] == "failed" and "quản trị" in d.output["error"]


async def test_denied_attempts_are_audited(who: Who) -> None:
    sale1 = await who("sale1")
    r = await executor.run(sale1, "admin.invite_user", {"email": "x@y.vn", "name": "X", "role": "sale"})
    assert r.status == "denied"
    n = await owner_val("SELECT count(*) FROM core.audit_log WHERE user_id = %s AND command = 'admin.invite_user' AND status = 'denied'",
                        (sale1.user_id,))
    assert n >= 1


async def test_internal_ops_are_not_callable_directly(who: Who) -> None:
    admin = await who("admin")
    r = await executor.run(admin, "commission.disburse", {"request_id": "00000000-0000-0000-0000-000000000000"})
    assert r.status == "not_found"
    r = await executor.query(admin, "crm.lead_stats", {"campaign_ids": []})
    assert r.status == "not_found"
