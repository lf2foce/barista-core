"""Bước duyệt "Trưởng nhóm" không bao giờ kẹt: cả nhánh cây của người xin chưa có trưởng (đội mới, cơ cấu đang dựng lại — ADR 0256) ⇒ chủ tài khoản
duyệt thay, nhãn nói rõ vì sao. Không ai phải sửa chuỗi duyệt trước."""

import uuid

from barista.db import db
from barista.kernel import approvals
from tests.conftest import Who, fresh_sale, ok, owner_val


async def test_a_team_lead_step_with_no_lead_anywhere_up_the_tree_goes_to_the_account_owner(who: Who) -> None:
    admin = await who("admin")
    code = f"R{uuid.uuid4().hex[:6].upper()}"
    await ok(admin, "admin.upsert_team", code=code, name=f"Đội gốc chưa có trưởng {code}", kind="sales", parent_code="")
    sale = await fresh_sale(who, code)
    owners = set(await owner_val("SELECT array_agg(user_id) FROM core.memberships WHERE tenant_id = %s AND owner AND active", (sale.tenant_id,)) or [])
    async with db.tenant_tx(sale) as conn:
        label, ids = await approvals.resolve_step(conn, sale.tenant_id, {"kind": "team_lead"}, sale.user_id, set())   # type: ignore[arg-type]
    assert owners and set(ids) == owners and "chưa có trưởng nhóm" in label
    # có trưởng ⇒ như cũ: trưởng duyệt, nhãn thường
    lead = await fresh_sale(who, code)
    await owner_val("UPDATE core.team_members tm SET is_lead = true FROM core.teams t WHERE t.id = tm.team_id AND t.code = %s AND tm.user_id = %s "
                    "RETURNING tm.user_id", (code, lead.user_id))
    async with db.tenant_tx(sale) as conn:
        label, ids = await approvals.resolve_step(conn, sale.tenant_id, {"kind": "team_lead"}, sale.user_id, set())   # type: ignore[arg-type]
    assert ids == [lead.user_id] and label == "Trưởng nhóm"
