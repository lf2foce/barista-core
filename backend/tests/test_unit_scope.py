"""PHẠM VI "unit" (0125 §A) — "các đội tôi THAM GIA", giữa own và team. Chứng minh bằng một vai thử (không vai nào của gói mang phạm vi này):
người chỉ là thành viên đội thấy dòng của đội đó mà KHÔNG làm trưởng, không thành người duyệt; own / team / all giữ nguyên nghĩa; Principal,
has_scope / can và RLS (core.can_see) cùng một câu trả lời."""

import uuid
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest

from barista import seed
from barista.db import db
from barista.kernel.approvals import resolve_step
from barista.kernel.principal import SCOPES, Grant, Principal
from tests.conftest import Who, approved, ok, owner_val, q

ROLE = "unit_viewer_test"


async def _titles(p: Principal, marks: list[str]) -> set[str]:
    """Việc thấy được dưới RLS (câu SQL thẳng — không qua lệnh nào khác): chỉ những việc do test này tạo."""
    async with db.tenant_tx(p, read_only=True) as conn:
        cur = await conn.execute("SELECT title FROM flow.tasks WHERE title = ANY(%s)", (marks,))
        return {r["title"] for r in await cur.fetchall()}


@pytest.fixture
async def viewer(who: Who, demo: dict[str, Any]) -> AsyncIterator[tuple[Principal, str]]:
    """Một vai thử: quyền như sale nhưng ``flow`` phạm vi unit; người dùng thuộc KD-A (không làm trưởng). Gỡ sạch sau test."""
    admin = await who("admin")
    tag = uuid.uuid4().hex[:8]
    email = f"unit{tag}@{seed.DOMAIN}"
    await approved(admin, "admin.upsert_role", key=ROLE, label="Vai thử phạm vi unit", copy_from="sale")
    await approved(admin, "admin.set_grant", role=ROLE, resource="flow", scope="unit", actions=["read", "write"])
    await approved(admin, "admin.invite_user", email=email, name="Người thử unit", role=ROLE, team_code="KD-A")
    p = await seed.who(demo["tenant"], f"unit{tag}", seed.DOMAIN)
    yield p, tag
    async with db.owner_tx() as conn:   # gỡ sạch: đội, thành viên, quyền, vai thử
        await conn.execute("DELETE FROM core.team_members WHERE user_id = %s", (p.user_id,))
        await conn.execute("DELETE FROM core.memberships WHERE user_id = %s", (p.user_id,))
        await conn.execute("DELETE FROM core.grants WHERE tenant_id = %s AND role = %s", (demo["tenant"], ROLE))
        await conn.execute("DELETE FROM core.roles WHERE tenant_id = %s AND key = %s", (demo["tenant"], ROLE))


def test_scope_order_is_own_unit_team_all() -> None:
    assert SCOPES == ("own", "unit", "team", "all")
    def mk(scope: str) -> Principal:
        return Principal(tenant_id=UUID(int=1), user_id=UUID(int=2), grants={"x": Grant(scope, frozenset({"read"}))})

    for have, need, expect in [("own", "unit", False), ("unit", "own", True), ("unit", "unit", True), ("unit", "team", False), ("unit", "all", False),
                               ("team", "unit", True), ("team", "all", False), ("all", "unit", True)]:
        assert mk(have).has_scope("x", need) is expect, (have, need)


async def test_member_sees_the_team_without_leading_it(who: Who, viewer: tuple[Principal, str]) -> None:
    p, tag = viewer
    sale1, sale2, sale3 = await who("sale1"), await who("sale2"), await who("sale3")
    marks = {k: f"unit-{tag}-{k}" for k in ("a1", "a2", "b3", "own")}
    await ok(sale1, "flow.create_task", title=marks["a1"])            # KD-A: đội của người thử
    await ok(sale2, "flow.create_task", title=marks["a2"])            # KD-A
    await ok(sale3, "flow.create_task", title=marks["b3"])            # KD-B: người thử không thuộc
    await ok(p, "flow.create_task", title=marks["own"])
    every = list(marks.values())

    # Principal, nhập khẩu từ DB: thành viên chứ không phải trưởng
    assert p.scope("flow") == "unit" and p.can("flow", "read") and not p.can("flow", "manage")
    assert p.has_scope("flow", "own") and p.has_scope("flow", "unit") and not p.has_scope("flow", "team") and not p.has_scope("flow", "all")
    assert p.member_teams == (p.home_team,) and p.managed_teams == ()

    # RLS đọc: đội mình thuộc + việc của mình; đội khác thì không
    assert await _titles(p, every) == {marks["a1"], marks["a2"], marks["own"]}
    # người cùng đội nhưng vai sale (own) vẫn chỉ thấy việc của mình — không vai nào khác bị mở rộng
    assert await _titles(sale1, every) == {marks["a1"]}
    # RLS ghi (WITH CHECK dùng chính hàm đó): sửa được việc đội mình thuộc, không sửa được việc đội khác
    async with db.tenant_tx(p, rollback=True) as conn:
        cur = await conn.execute("UPDATE flow.tasks SET notes = 'x' WHERE title = %s", (marks["a2"],))
        assert cur.rowcount == 1
        cur = await conn.execute("UPDATE flow.tasks SET notes = 'x' WHERE title = %s", (marks["b3"],))
        assert cur.rowcount == 0


async def test_joining_a_second_team_widens_unit_only_by_that_team(who: Who, viewer: tuple[Principal, str]) -> None:
    p, tag = viewer
    admin, sale3 = await who("admin"), await who("sale3")
    mark = f"unit-{tag}-b3"
    await ok(sale3, "flow.create_task", title=mark)
    assert await _titles(p, [mark]) == set()
    await approved(admin, "admin.add_team", email=p.email, team_code="KD-B")
    try:
        both = await seed.who(p.tenant_id, p.email.split("@")[0], seed.DOMAIN)
        assert set(both.member_teams) == {p.home_team, await owner_val("SELECT id FROM core.teams WHERE tenant_id = %s AND code = 'KD-B'", (p.tenant_id,))}
        assert both.managed_teams == ()                                  # tham gia thêm đội vẫn không mở phạm vi team
        assert await _titles(both, [mark]) == {mark}
    finally:
        await approved(admin, "admin.remove_team", email=p.email, team_code="KD-B")
    assert await _titles(await seed.who(p.tenant_id, p.email.split("@")[0], seed.DOMAIN), [mark]) == set()


async def test_being_a_member_never_makes_an_approver(who: Who, viewer: tuple[Principal, str]) -> None:
    p, _ = viewer
    sale1, truonga = await who("sale1"), await who("truonga")
    async with db.tenant_tx(sale1, read_only=True) as conn:
        _, ids = await resolve_step(conn, sale1.tenant_id, {"kind": "team_lead"}, sale1.user_id, set())  # type: ignore[arg-type]
    assert list(ids) == [truonga.user_id] and p.user_id not in ids   # cùng đội KD-A, phạm vi unit, vẫn không là người duyệt
    async with db.tenant_tx(p, read_only=True) as conn:
        _, mine = await resolve_step(conn, p.tenant_id, {"kind": "team_lead"}, p.user_id, set())  # type: ignore[arg-type]
    assert p.user_id not in mine


async def test_grant_matrix_and_admin_accept_the_new_scope(who: Who, viewer: tuple[Principal, str]) -> None:
    admin = await who("admin")
    assert "Đội mình thuộc" in str(await q(admin, "admin.permissions"))
    assert await owner_val("SELECT scope FROM core.grants WHERE tenant_id = %s AND role = %s AND resource = 'flow'", (admin.tenant_id, ROLE)) == "unit"
