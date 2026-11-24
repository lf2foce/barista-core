"""Đội của một người (0062, bước dọn): đội chính / đội tham gia / đội quản lý — một định nghĩa ở ``kernel/teams.py``. Ranh giới giữ
nguyên hành vi: hồ sơ gắn đội chính của người phụ trách; tuyến duyệt "trưởng nhóm" đi ngược cây từ đội chính; chia lead mỗi người
một suất; người chưa thuộc đội vẫn làm việc được (không đội, không lỗi); mỗi công ty chỉ thấy đội của mình."""

import uuid
from typing import Any
from uuid import UUID

from barista import seed
from barista.db import db
from barista.kernel import executor
from barista.kernel.approvals import resolve_step
from barista.kernel.executor import core_ctx
from barista.modules import leadflow
from tests.conftest import Who, approved, ok, owner_val, q


async def _code(tenant: UUID, team: UUID | None) -> str:
    return await owner_val("SELECT code FROM core.teams WHERE tenant_id = %s AND id = %s", (tenant, team))


async def _team(tenant: UUID, code: str) -> UUID:
    return await owner_val("SELECT id FROM core.teams WHERE tenant_id = %s AND code = %s", (tenant, code))


async def test_three_answers_have_separate_meanings(who: Who, demo: dict[str, Any]) -> None:
    sale1, truonga, gd = await who("sale1"), await who("truonga"), await who("giamdoc")
    kda = await _team(demo["tenant"], "KD-A")
    assert (sale1.home_team, sale1.member_teams, sale1.managed_teams) == (kda, (kda,), ())   # thuộc đội ≠ quản lý đội
    assert truonga.home_team == kda and truonga.managed_teams == (kda,)
    every = await owner_val("""WITH RECURSIVE tree AS (SELECT id FROM core.teams WHERE tenant_id = %s AND code = 'HQ'
                                                       UNION SELECT c.id FROM core.teams c JOIN tree ON c.parent_id = tree.id)
                               SELECT array_agg(id ORDER BY id) FROM tree""", (demo["tenant"],))
    assert list(gd.managed_teams) == every                                          # trưởng đội gốc: cả cây CỦA ĐỘI ĐÓ (đội gốc khác của công ty không tính)
    outsider = await who("sale1", other=True)
    assert outsider.home_team == await _team(demo["other"], "KD-A") != kda            # cùng tên người, công ty khác: đội của công ty đó


async def test_records_take_the_home_team_and_teamless_people_still_work(who: Who, demo: dict[str, Any]) -> None:
    sale1, admin = await who("sale1"), await who("admin")
    t = await ok(sale1, "flow.create_task", title="Gọi lại khách (test đội)")
    assert await owner_val("SELECT team_id FROM flow.tasks WHERE id = %s", (t["task_id"],)) == sale1.home_team

    await ok(admin, "admin.invite_user", email=f"khongdoi@{seed.DOMAIN}", name="Người chưa có đội", role="sale")
    loner = await who("khongdoi")
    assert (loner.home_team, loner.member_teams, loner.managed_teams) == (None, (), ())
    t = await ok(loner, "flow.create_task", title="Việc của người chưa có đội")
    assert await owner_val("SELECT team_id FROM flow.tasks WHERE id = %s", (t["task_id"],)) is None
    board = await q(loner, "flow.tasks")
    assert "Việc của người chưa có đội" in str(board)                                  # "việc của tôi" chạy với danh sách đội rỗng
    async with db.tenant_tx(loner, read_only=True) as conn:
        label, ids = await resolve_step(conn, loner.tenant_id, {"kind": "team_lead"}, loner.user_id, set())  # type: ignore[arg-type]
    owners = set(await owner_val("SELECT array_agg(user_id) FROM core.memberships WHERE tenant_id = %s AND owner AND active", (loner.tenant_id,)) or [])
    assert label == "Chủ tài khoản (chưa có trưởng nhóm)" and set(ids) == owners - {loner.user_id} and ids   # không đội ⇒ phiếu vẫn có người duyệt


async def test_team_lead_approval_walks_up_from_the_home_team(who: Who) -> None:
    people = {k: await who(k) for k in ("sale1", "sale3", "truonga", "truongb", "gdkhoi1", "gdmb", "giamdoc", "digital", "mkt")}
    # trưởng tự xin ⇒ trưởng đội cha, đi ngược cây khu vực › khối › team: trưởng team ⇒ GĐ khối ⇒ GĐ khu vực ⇒ GĐ công ty
    expect = {"sale1": "truonga", "sale3": "truongb", "truonga": "gdkhoi1", "gdkhoi1": "gdmb", "gdmb": "giamdoc", "digital": "mkt"}
    async with db.tenant_tx(people["sale1"], read_only=True) as conn:
        for asker, approver in expect.items():
            p = people[asker]
            _, ids = await resolve_step(conn, p.tenant_id, {"kind": "team_lead"}, p.user_id, set())  # type: ignore[arg-type]
            assert ids == [people[approver].user_id], asker


async def test_lead_receivers_are_one_per_person_within_the_team_tree(who: Who, demo: dict[str, Any]) -> None:
    admin = await who("admin")
    names = {k: (await who(k)).user_id for k in ("sale1", "sale2", "sale3", "truonga", "truongb")}
    async with db.tenant_tx(admin, read_only=True) as conn:
        ctx = core_ctx(conn, admin, module="leadflow")   # ctx của chính module (hỏi điểm cắm nghỉ phép — 0152)
        everyone = await leadflow._eligible(ctx, {"team_id": await _team(demo["tenant"], "HQ"), "roles": ["sale", "team_lead"]})
        kdb = await leadflow._eligible(ctx, {"team_id": await _team(demo["tenant"], "KD-B"), "roles": ["sale", "team_lead"]})
    assert len(everyone) == len(set(everyone)) and set(everyone) >= set(names.values())   # đội gốc: gồm đội con, mỗi người một lần
    assert set(kdb) == {names["sale3"], names["truongb"]}


async def test_region_and_block_heads_see_their_whole_subtree_only(who: Who) -> None:
    """Cây KHU VỰC › KHỐI › TEAM (dữ liệu mẫu): trưởng mỗi tầng thấy cả cây con của mình, không thấy khu vực khác (managed_teams + RLS)."""
    gdmb, gdkhoi1, gdmn, truongc = [await who(k) for k in ("gdmb", "gdkhoi1", "gdmn", "truongc")]
    codes = {r["code"]: r["id"] for r in await owner_val("SELECT json_agg(json_build_object('code', code, 'id', id)) FROM core.teams WHERE tenant_id = %s",
                                                          (gdmb.tenant_id,))}
    assert {codes[c] for c in ("KV-MB", "KHOI-1", "KD-A", "KD-B")} <= set(map(str, gdmb.managed_teams))
    assert codes["KD-C"] not in set(map(str, gdmb.managed_teams)) and codes["KV-MB"] not in set(map(str, gdkhoi1.managed_teams))
    owners = lambda page: {r["owner"] for r in page["rows"]}   # noqa: E731
    north, south = owners(await q(gdmb, "crm.leads", stage="")), owners(await q(gdmn, "crm.leads", stage=""))
    assert {"Nguyễn Văn An", "Đặng Gia Khánh"} <= north and "Hồ Đức Phát" not in north
    assert south == {"Hồ Đức Phát"} and owners(await q(truongc, "crm.leads", stage="")) == {"Hồ Đức Phát"}


async def test_leads_filter_by_region_covers_the_whole_subtree(who: Who) -> None:
    """Giám đốc hỏi "khu vực Miền Nam có bao nhiêu khách" ⇒ một lần gọi (lọc cả cây đội bên dưới), không phải gọi từng đội rồi tự cộng (0095).
    Mã hoặc tên đều được; đội không có ⇒ báo không thấy, không trả 0 khách như thể hết khách."""
    gd = await who("giamdoc")
    south = await q(gd, "crm.leads", team_code="KV-MN")
    by_name = await q(gd, "crm.leads", team_code="Miền Nam")
    every = await q(gd, "crm.leads")
    assert south["rows"] and {r["team"] for r in south["rows"]} <= {"Team Kinh doanh C", "Khối kinh doanh 2", "Khu vực Miền Nam"}
    assert [r["id"] for r in by_name["rows"]] == [r["id"] for r in south["rows"]]
    assert len(south["rows"]) < len(every["rows"])
    r = await executor.query(gd, "crm.leads", {"team_code": "KHONG-CO"})
    assert r.status == "not_found"


async def test_lead_filters_one_person_within_own_scope_only(who: Who) -> None:
    """Rà vai trưởng nhóm (18/11/2026): trưởng nhóm cần xem khách của TỪNG người trong đội (ai đang để khách quá hạn) — lọc theo người phụ trách
    trong phạm vi đã có (RLS), không mở rộng phạm vi: chọn người đội khác ⇒ không dòng nào."""
    truonga, gdmn = await who("truonga"), await who("gdmn")
    every = await q(truonga, "crm.leads", stage="")
    owners = {r["owner"] for r in every["rows"]}
    assert len(owners) >= 2
    sale1 = await who("sale1")
    one = await q(truonga, "crm.leads", stage="", owner=sale1.email)
    assert one["rows"] and {r["owner"] for r in one["rows"]} == {sale1.name}
    assert next(k["value"] for k in one["kpis"] if k["label"] == "Số lead") == len(one["rows"]) < len(every["rows"])
    south = await owner_val("SELECT u.email FROM crm.leads l JOIN core.users u ON u.id = l.owner_id JOIN core.teams t ON t.id = l.team_id "
                            "WHERE l.tenant_id = %s AND t.code = 'KD-C' LIMIT 1", (truonga.tenant_id,))
    assert (await q(gdmn, "crm.leads", stage="", owner=south))["rows"]                        # người đội khác có khách thật…
    assert not (await q(truonga, "crm.leads", stage="", owner=south))["rows"]                 # …nhưng lọc theo người không mở thêm quyền


async def test_one_person_in_two_teams_keeps_one_home_and_lists_do_not_duplicate(who: Who, demo: dict[str, Any]) -> None:
    """0109: giáo viên dạy hai cơ sở. Đội chính là một; người hiện một dòng ở danh sách; nhận thông báo và thấy việc chung của cả hai; là trưởng ở đội thêm thì có phạm vi đội đó."""
    admin, sale = await who("admin"), await who("sale1")
    tenant = sale.tenant_id
    home_before = sale.home_team
    assert home_before is not None
    target = await _team(tenant, "KD-C")
    assert target != home_before
    await approved(admin, "admin.add_team", email=sale.email, team_code="KD-C")
    lead_c = await owner_val("SELECT u.email FROM core.team_members tm JOIN core.users u ON u.id = tm.user_id JOIN core.teams t ON t.id = tm.team_id WHERE t.tenant_id = %s AND t.code = %s AND tm.is_lead", (tenant, "KD-C"))
    try:
        again = await who("sale1")
        assert again.home_team == home_before and set(again.member_teams) == {home_before, target}
        assert again.managed_teams == sale.managed_teams                                                # tham gia không mở phạm vi xem
        users = (await q(admin, "admin.users"))["rows"]
        mine = [r for r in users if r["email"] == sale.email]
        assert len(mine) == 1 and mine[0]["other_teams"]
        lead = await ok(sale, "crm.create_lead", name="Khách đội chính", phone=f"0108{uuid.uuid4().int % 10**6:06d}", offering_code="HSR")
        assert await owner_val("SELECT team_id FROM crm.leads WHERE id = %s", (lead["lead_id"],)) == home_before   # hồ sơ theo đội chính
        assert await owner_val("SELECT count(*) FROM core.team_members WHERE tenant_id = %s AND user_id = %s AND is_home", (tenant, sale.user_id)) == 1
        await approved(admin, "admin.add_team", email=sale.email, team_code="KD-C", is_lead=True)   # một đội một trưởng (0256): thay trưởng đang giữ
        assert target in (await who("sale1")).managed_teams                                             # trưởng ở đội thêm: có phạm vi đội đó
        r = await executor.run(admin, "admin.remove_team", {"email": sale.email, "team_code": (await _code(tenant, home_before))})
        assert r.status == "invalid" and "đội chính" in (r.error or "")                                 # phải đổi đội chính trước
    finally:
        await approved(admin, "admin.remove_team", email=sale.email, team_code="KD-C")
        if lead_c:   # trả trưởng gốc của đội mẫu dùng chung
            await approved(admin, "admin.set_team_lead", team_code="KD-C", email=lead_c)
    assert (await who("sale1")).member_teams == (home_before,)


async def test_changing_home_team_keeps_other_teams(who: Who, demo: dict[str, Any]) -> None:
    admin, sale = await who("admin"), await who("sale2")
    tenant = sale.tenant_id
    home_code = await _code(tenant, sale.home_team)
    await approved(admin, "admin.add_team", email=sale.email, team_code="KD-C")
    try:
        await approved(admin, "admin.update_member", email=sale.email, team_code="KV-MN")
        now = await who("sale2")
        assert now.home_team == await _team(tenant, "KV-MN") and await _team(tenant, "KD-C") in now.member_teams
        assert sale.home_team not in now.member_teams
    finally:
        await approved(admin, "admin.update_member", email=sale.email, team_code=home_code)
        await approved(admin, "admin.remove_team", email=sale.email, team_code="KD-C")
