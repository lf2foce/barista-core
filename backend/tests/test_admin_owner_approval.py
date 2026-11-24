"""0029 bước 1 — quản trị không tự nâng quyền: đổi quyền / vai / luật / chuỗi duyệt / chủ tài khoản / trưởng đội đi qua chủ tài khoản.

Mỗi test dùng công ty RIÊNG (đổi quyền của vai quản trị ở công ty mẫu dùng chung sẽ làm lệch test khác)."""

import uuid
from typing import Any
from uuid import UUID

import pytest
from psycopg.types.json import Jsonb

from barista import seed
from barista.db import db
from barista.kernel import executor
from barista.kernel.approvals import SOLE_OWNER_NOTE
from barista.kernel.principal import Principal, system_principal
from tests.conftest import approved, edge, ok, owner_val

PEOPLE = [("giamdoc", "Giám đốc thử", "director", "HQ", True), ("admin", "Quản trị thử", "admin", "QT", True),
          ("ketoan", "Kế toán thử", "accountant", "KT", True), ("nv", "Nhân viên thử", "staff", "KD-A", False)]
ACCESS_RULES = {f"admin.{c}" for c in ("set_grant", "set_info_access", "set_rule", "set_chain", "upsert_role", "update_member", "invite_user", "add_team", "remove_team",
                                        "upsert_team", "set_agent_policy", "set_agent_trust", "set_agent_preset")} | {"kb.save_shelf"} \
    | {f"connect.{c}" for c in ("install", "set_access", "rotate_secret")}   # nới tự chủ trợ lý / hạ ngưỡng tin cậy = cấp quyền (0143)


async def _company(people: list[tuple[str, str, str, str, bool]] = PEOPLE) -> tuple[UUID, str]:
    d = f"o{uuid.uuid4().hex[:8]}.demo"
    return await seed.create_tenant(f"Công ty chủ tài khoản {d}", d, people=people, packs=("finance",), about="công ty thử (test)"), d


@pytest.fixture(scope="module")
async def co() -> dict[str, Any]:
    tid, d = await _company()
    return {"tid": tid, "d": d}


async def _who(co: dict[str, Any], local: str) -> Principal:
    return await seed.who(co["tid"], local, co["d"])


async def _owners(tid: UUID) -> list[str]:
    return await owner_val("""SELECT coalesce(array_agg(u.email ORDER BY u.email), '{}') FROM core.memberships m JOIN core.users u ON u.id = m.user_id
                              WHERE m.tenant_id = %s AND m.owner AND m.active""", (tid,))


async def _rules(tid: UUID) -> dict[str, str]:
    return await owner_val("SELECT coalesce(jsonb_object_agg(command, chain), '{}') FROM core.approval_rules WHERE tenant_id = %s", (tid,))


async def test_new_company_gets_owner_chain_and_rules(co: dict[str, Any]) -> None:
    """Tạo công ty không kẹt chờ duyệt (cài bằng quyền hệ thống); giám đốc thành chủ tài khoản; chuỗi + luật đổi quyền có sẵn."""
    assert await _owners(co["tid"]) == [f"giamdoc@{co['d']}"]
    rules = await _rules(co["tid"])
    assert {c for c, ch in rules.items() if ch == "access_change"} == ACCESS_RULES
    steps = await owner_val("SELECT steps FROM core.approval_chains WHERE tenant_id = %s AND key = 'access_change'", (co["tid"],))
    assert steps == [{"kind": "owner"}]


async def test_admin_granting_himself_finance_waits_for_the_owner(co: dict[str, Any]) -> None:
    admin, gd = await _who(co, "admin"), await _who(co, "giamdoc")
    assert not admin.can("acct", "read")
    r = await executor.run(admin, "admin.set_grant", {"role": "admin", "resource": "acct", "scope": "all", "actions": ["read", "write"]})
    assert r.status == "pending_approval", (r.status, r.error)
    summary = await owner_val("SELECT summary FROM core.approvals WHERE id = %s", (UUID(r.approval_id),))
    # người duyệt thấy đổi gì: MÃ vai / tài nguyên / phạm vi / hành động (nhãn là chữ quản trị sửa được) + trạng thái đang có
    assert "[admin]" in summary and "[acct]" in summary and "[all]" in summary and "[read/write]" in summary and "đang: không có" in summary
    assert not (await _who(co, "admin")).can("acct", "read")                           # chưa duyệt ⇒ chưa đổi
    # người xin không tự duyệt (bất biến 6) — kể cả khi là quản trị
    own = await executor.run(edge(admin), "core.decide", {"approval_id": r.approval_id, "approve": True})
    assert own.status == "denied"
    # trợ lý không duyệt thay chủ tài khoản
    assert (await executor.run(edge(gd.as_agent()), "core.decide", {"approval_id": r.approval_id, "approve": True})).status != "ok"
    d = await executor.run(edge(gd), "core.decide", {"approval_id": r.approval_id, "approve": True})
    assert d.status == "ok" and d.output["status"] == "executed", d.output
    assert (await _who(co, "admin")).can("acct", "read")
    row = await owner_val("""SELECT jsonb_build_object('user', user_id, 'by', approved_by) FROM core.audit_log
                             WHERE tenant_id = %s AND command = 'admin.set_grant' AND ref = %s AND status = 'ok'""", (co["tid"], r.approval_id))
    assert row["user"] == str(admin.user_id) and row["by"] == [str(gd.user_id)]          # chạy bằng quyền người xin, ghi người duyệt
    await approved(admin, "admin.set_grant", role="admin", resource="acct", scope="all", actions=[])
    assert not (await _who(co, "admin")).can("acct", "read")


async def test_removing_the_protection_also_needs_the_owner(co: dict[str, Any]) -> None:
    admin, gd = await _who(co, "admin"), await _who(co, "giamdoc")
    r = await executor.run(admin, "admin.set_rule", {"command": "admin.set_grant", "chain": ""})
    assert r.status == "pending_approval"
    assert (await _rules(co["tid"]))["admin.set_grant"] == "access_change"
    await ok(edge(gd), "core.decide", approval_id=r.approval_id, approve=False)
    assert (await _rules(co["tid"]))["admin.set_grant"] == "access_change"
    r = await executor.run(admin, "admin.set_chain", {"key": "access_change", "label": "Đổi quyền", "steps": "team_lead"})
    assert r.status == "pending_approval"                                                  # đổi người duyệt cũng phải duyệt
    await ok(edge(gd), "core.decide", approval_id=r.approval_id, approve=False)


async def test_only_access_changes_of_a_member_need_approval(co: dict[str, Any]) -> None:
    """Đổi đội nhân viên thường, khoá / mở người nghỉ, mời nhân viên mới ⇒ chạy thẳng; đổi vai, làm trưởng đội ⇒ phiếu."""
    admin = await _who(co, "admin")
    nv = f"nv@{co['d']}"
    assert (await executor.run(admin, "admin.update_member", {"email": nv, "team_code": "KD-B"})).status == "ok"
    assert (await executor.run(admin, "admin.update_member", {"email": nv, "active": False})).status == "ok"
    assert (await executor.run(admin, "admin.update_member", {"email": nv, "active": True})).status == "ok"
    new = f"moi{uuid.uuid4().hex[:6]}@{co['d']}"
    # vai nhập môn (gói nền: staff) ⇒ mời người mới chạy thẳng
    assert await owner_val("SELECT array_agg(key) FROM core.roles WHERE tenant_id = %s AND entry", (co["tid"],)) == ["staff"]
    assert (await executor.run(admin, "admin.invite_user", {"email": new, "name": "Người mới", "role": "staff", "team_code": "KD-A"})).status == "ok"
    assert (await executor.run(admin, "admin.upsert_team", {"code": f"T{uuid.uuid4().hex[:4]}", "name": "Đội mới", "parent_code": "HQ"})).status == "ok"
    assert (await executor.run(admin, "admin.add_team", {"email": new, "team_code": "KD-B"})).status == "ok"
    assert (await executor.run(admin, "admin.upsert_role", {"key": "tro_ly", "label": "Trợ lý"})).status == "ok"
    for name, inp in (("admin.update_member", {"email": nv, "role": "accountant"}),
                      ("admin.update_member", {"email": nv, "is_lead": True}),
                      ("admin.add_team", {"email": new, "team_code": "KD-C", "is_lead": True}),
                      ("admin.invite_user", {"email": nv, "name": "Nhân viên thử", "role": "director"}),   # mời lại = đổi vai
                      ("admin.invite_user", {"email": f"qt{uuid.uuid4().hex[:6]}@{co['d']}", "name": "QT mới", "role": "admin"}),
                      ("admin.upsert_role", {"key": "tro_ly", "label": "Trợ lý", "copy_from": "director"}),
                      ("admin.upsert_role", {"key": "tro_ly", "label": "Trợ lý", "entry": True}),
                      # tài khoản MỚI với vai rộng / làm trưởng = thêm một người duyệt (review 08/11)
                      ("admin.invite_user", {"email": f"phu{uuid.uuid4().hex[:6]}@{co['d']}", "name": "Phụ", "role": "director"}),
                      ("admin.invite_user", {"email": f"phu{uuid.uuid4().hex[:6]}@{co['d']}", "name": "Phụ", "role": "staff", "team_code": "HQ",
                                             "is_lead": True}),
                      ("admin.upsert_team", {"code": "KD-C", "name": "Team Kinh doanh C", "parent_code": "QT"}),   # đổi đội cha
                      ("kb.save_shelf", {"key": "management", "name": "Ban giám đốc", "roles": ["admin", "director"], "people": [nv]}),
                      ("admin.update_member", {"email": f"admin@{co['d']}", "owner": True})):
        r = await executor.run(admin, name, inp)
        assert r.status == "pending_approval", (name, inp, r.status, r.error)
    assert await owner_val("SELECT role FROM core.memberships m JOIN core.users u ON u.id = m.user_id WHERE m.tenant_id = %s AND u.email = %s",
                           (co["tid"], nv)) == "staff"


async def test_last_owner_cannot_be_removed_or_deactivated(co: dict[str, Any]) -> None:
    admin = await _who(co, "admin")
    gd = f"giamdoc@{co['d']}"
    for inp in ({"email": gd, "owner": False}, {"email": gd, "active": False}):
        r = await executor.run(admin, "admin.update_member", inp)
        assert r.status == "pending_approval"
        d = await ok(edge(await _who(co, "giamdoc")), "core.decide", approval_id=r.approval_id, approve=True)
        assert d["status"] == "failed" and "chủ tài khoản" in d["error"], d
    assert await _owners(co["tid"]) == [gd]


async def test_single_owner_changes_run_at_once_logged_and_told_to_admins() -> None:
    """Chủ tài khoản DUY NHẤT tự đổi quyền: không ai để duyệt ⇒ chạy ngay, nhật ký ghi rõ, quản trị khác nhận thông báo. Có chủ tài khoản thứ hai
    ⇒ phiếu đi người kia như thường."""
    tid, d = await _company([("chu", "Chủ thử", "admin", "QT", True), ("nv", "Nhân viên thử", "staff", "KD-A", False)])
    assert await _owners(tid) == [f"chu@{d}"]                      # không có giám đốc ⇒ quản trị thành chủ tài khoản
    chu = await seed.who(tid, "chu", d)
    await ok(system_principal(tid), "admin.invite_user", email=f"qt2@{d}", name="Quản trị hai", role="admin", team_code="QT")
    r = await executor.run(chu, "admin.set_grant", {"role": "staff", "resource": "acct", "scope": "own", "actions": ["read"]})
    assert r.status == "ok", (r.status, r.error)
    note = await owner_val("""SELECT error FROM core.audit_log WHERE tenant_id = %s AND command = 'admin.set_grant' AND user_id = %s
                              ORDER BY at DESC LIMIT 1""", (tid, chu.user_id))
    assert note.startswith(SOLE_OWNER_NOTE)
    qt2 = await seed.who(tid, "qt2", d)
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE tenant_id = %s AND user_id = %s AND title LIKE %s",
                           (tid, qt2.user_id, f"{SOLE_OWNER_NOTE}%")) == 1
    users = await executor.query(chu, "admin.users", {})
    assert next(k for k in users.output["kpis"] if k["label"] == "Chủ tài khoản")["value"] == 1   # màn người dùng nhắc thêm người thứ hai
    assert (await executor.run(chu, "admin.update_member", {"email": qt2.email, "owner": True})).status == "ok"
    r = await executor.run(chu, "admin.set_grant", {"role": "staff", "resource": "acct", "scope": "own", "actions": []})
    assert r.status == "pending_approval"
    assert await owner_val("SELECT approver_ids FROM core.approvals WHERE id = %s", (UUID(r.approval_id),)) == [qt2.user_id]


async def test_assistant_only_proposes_access_changes(co: dict[str, Any]) -> None:
    admin = await _who(co, "admin")
    r = await executor.run(admin.as_agent(), "admin.set_grant", {"role": "admin", "resource": "acct", "scope": "all", "actions": ["read"]})
    assert r.status == "proposed"
    assert not (await _who(co, "admin")).can("acct", "read")


async def test_upgrade_installs_owner_chain_rules_once_for_an_existing_company() -> None:
    """Công ty có trước 0029 (chưa chuỗi / luật / chủ tài khoản): cài mặc định (đường seed --upgrade) thêm chuỗi + luật, gán chủ tài khoản theo
    quy tắc; chạy lại không đổi gì. Không có giám đốc ⇒ mọi quản trị đang hoạt động."""
    for people, want in ((PEOPLE, ["giamdoc"]),
                         ([("qt1", "Quản trị một", "admin", "QT", True), ("qt2", "Quản trị hai", "admin", "QT", False),
                           ("nv", "Nhân viên", "staff", "KD-A", False)], ["qt1", "qt2"])):
        tid, d = await _company(people)
        async with db.owner_tx() as c:   # đưa về trạng thái trước 0029
            await c.execute("DELETE FROM core.approval_chains WHERE tenant_id = %s AND key = 'access_change'", (tid,))
            await c.execute("DELETE FROM core.pack_installs WHERE tenant_id = %s AND (key = 'access_change' OR key LIKE 'admin.%%'"
                            " OR key = 'kb.save_shelf' OR key LIKE 'connect.%%')", (tid,))   # app từ xa (0140) cũng có sau
            await c.execute("UPDATE core.memberships SET owner = false WHERE tenant_id = %s", (tid,))
            await c.execute("UPDATE core.roles SET entry = false WHERE tenant_id = %s", (tid,))
            await c.execute("INSERT INTO core.approval_rules (tenant_id, command, chain) VALUES (%s, 'flow.request_vendor', 'outsource')"
                            " ON CONFLICT DO NOTHING", (tid,))
        assert await _rules(tid) == {"flow.request_vendor": "outsource", "acct.credit_receivable": "receivable_correction",
                                     "acct.reverse_receipt": "receivable_correction",   # + luật mặc định của gói Tài chính (0156)
                                     "acct.cancel_vat_invoice": "receivable_correction",
                                     "acct.adjust_vat_invoice": "receivable_correction",   # + huỷ / điều chỉnh hoá đơn GTGT (0165)
                                     "acct.cancel_expense": "receivable_correction",       # + huỷ phiếu chi phí (0170)
                                     "acct.transfer_expense_entity": "receivable_correction",   # + chuyển pháp nhân phiếu chi phí (0183)
                                     "acct.write_off_dossier": "receivable_correction"}   # + xoá phần thiếu của khoản lệch (0223)
        out = await ok(system_principal(tid), "admin.install_defaults", packs=["base", "finance"])
        assert out["chains"] == 1 and out["rules"] == len(ACCESS_RULES) and len(out["owners"]) == len(want)
        assert await _owners(tid) == [f"{w}@{d}" for w in want]
        assert await owner_val("SELECT array_agg(key) FROM core.roles WHERE tenant_id = %s AND entry", (tid,)) == ["staff"]
        again = await ok(system_principal(tid), "admin.install_defaults", packs=["base", "finance"])
        assert again["chains"] == again["rules"] == 0 and again["owners"] == []
        assert {c for c, ch in (await _rules(tid)).items() if ch == "access_change"} == ACCESS_RULES
        # công ty đã sửa chuỗi (vd thêm bước) ⇒ cài lại giữ của công ty
        async with db.owner_tx() as c:
            await c.execute("UPDATE core.approval_chains SET steps = %s WHERE tenant_id = %s AND key = 'access_change'",
                            (Jsonb([{"kind": "owner"}, {"kind": "role", "role": "admin"}]), tid))
        await ok(system_principal(tid), "admin.install_defaults", packs=["base", "finance"])
        assert len(await owner_val("SELECT steps FROM core.approval_chains WHERE tenant_id = %s AND key = 'access_change'", (tid,))) == 2


async def test_renaming_a_role_is_gated_and_requests_show_role_keys(co: dict[str, Any]) -> None:
    """Review 08/11: đổi nhãn "Giám đốc" thành "Nhân viên" rồi xin chuyển vai ⇒ phiếu giả. Đổi tên vai đang có phải duyệt; phiếu luôn kèm MÃ vai."""
    admin = await _who(co, "admin")
    r = await executor.run(admin, "admin.upsert_role", {"key": "director", "label": "Nhân viên (hạn chế)"})
    assert r.status == "pending_approval"
    assert await owner_val("SELECT label FROM core.roles WHERE tenant_id = %s AND key = 'director'", (co["tid"],)) != "Nhân viên (hạn chế)"
    r = await executor.run(admin, "admin.update_member", {"email": admin.email, "role": "director"})
    assert r.status == "pending_approval"
    summary = await owner_val("SELECT summary FROM core.approvals WHERE id = %s", (UUID(r.approval_id),))
    assert "[admin]" in summary and "[director]" in summary


async def test_reinviting_a_lead_elsewhere_is_gated(co: dict[str, Any]) -> None:
    admin = await _who(co, "admin")
    r = await executor.run(admin, "admin.invite_user", {"email": f"ketoan@{co['d']}", "name": "Kế toán thử", "role": "accountant", "team_code": "KD-A"})
    assert r.status == "pending_approval"                                                   # mời lại vào đội khác làm mất cờ trưởng KT
    assert await owner_val("""SELECT tm.is_lead FROM core.team_members tm JOIN core.users u ON u.id = tm.user_id JOIN core.teams t ON t.id = tm.team_id
                              WHERE tm.tenant_id = %s AND u.email = %s AND t.code = 'KT'""", (co["tid"], f"ketoan@{co['d']}")) is True



async def test_a_sole_owner_is_never_refused_when_no_step_finds_anyone_else_and_nothing_wider_runs_unapproved() -> None:
    """Rà 24/11/2026 (Codex): chủ tài khoản DUY NHẤT xin qua chuỗi có bước "Trưởng nhóm" mà cả nhánh chưa có trưởng ⇒ bước lùi về chủ tài khoản, loại chính
    người xin ⇒ trước đây bị từ chối "không có người duyệt". MỘT luật: không bước nào tìm được người KHÁC duyệt ⇒ ngoại lệ một chủ tài khoản (chạy, ghi nhật
    ký, báo quản trị); còn một bước có người khác ⇒ phiếu đi người đó như thường."""
    import json

    from barista.kernel import approvals

    tid, d = await _company([("chu", "Chủ thử", "admin", "QT", True), ("kt", "Kế toán thử", "accountant", "KT", False)])
    chu = await seed.who(tid, "chu", d)

    async def chain(steps: list[dict[str, Any]]) -> str:
        key = "thu_" + "".join(chr(97 + int(c, 16) % 26) for c in uuid.uuid4().hex[:10])   # khoá chuỗi chỉ nhận chữ thường và gạch dưới
        await owner_val("INSERT INTO core.approval_chains (tenant_id, key, label, steps) VALUES (%s, %s, 'Chuỗi thử', %s) RETURNING key",
                        (tid, key, json.dumps(steps)))
        return key

    async def sole(steps: list[dict[str, Any]]) -> bool:
        key = await chain(steps)
        async with db.tenant_tx(chu, read_only=True) as conn:
            return await approvals.sole_owner(conn, chu, key)

    assert await sole([{"kind": "team_lead"}])                                       # không ai làm trưởng: chỉ còn chính người xin
    assert await sole([{"kind": "owner"}])                                           # như trước
    assert await sole([{"kind": "role", "role": "director"}])                        # không ai giữ vai giám đốc
    assert not await sole([{"kind": "role", "role": "accountant"}])                  # kế toán duyệt được ⇒ phiếu đi kế toán
    assert not await sole([{"kind": "team_lead"}, {"kind": "role", "role": "accountant"}])   # còn một bước có người khác ⇒ không tự duyệt
