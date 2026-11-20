"""Hồ sơ làm việc mỏng (0107): lịch sử có hiệu lực theo ngày, mã không trùng người khác, quyền theo tài nguyên, không đụng tiền."""

from datetime import timedelta
from typing import Any

import psycopg
import pytest

from barista.db import db
from barista.kernel import executor, forms, registry
from barista.kernel.clock import today
from barista.workspaces import manifest
from tests.conftest import Who, fresh_sale, ok, owner_val, q


async def _party(who: Who, sale: Any) -> Any:
    return await owner_val("SELECT id FROM parties.parties WHERE user_id=%s AND tenant_id=%s", (sale.user_id, sale.tenant_id))


async def test_every_new_member_has_a_party_at_once(who: Who) -> None:
    sale = await fresh_sale(who)
    assert await _party(who, sale) is not None
    assert str(await _party(who, sale)) in {o["value"] for o in await q(await who("admin"), "parties.options")}


async def test_title_change_closes_the_old_row_and_past_dates_answer_as_of(who: Who) -> None:
    admin, sale = await who("admin"), await fresh_sale(who)
    me = await _party(who, sale)
    d0 = today() - timedelta(days=100)
    d1 = today() - timedelta(days=10)
    code = f"M{str(me)[:8]}"
    await ok(admin, "hr.set_employment", party_id=me, employee_code=code, title="Nhân viên", started_on=d0.isoformat())
    await ok(admin, "hr.set_employment", party_id=me, employee_code=code, title="Trưởng nhóm", started_on=d1.isoformat())
    await ok(admin, "hr.set_assignment", party_id=me, team_code="KD-A", valid_from=d0.isoformat(), reason="Dữ liệu thử")
    await ok(admin, "hr.set_assignment", party_id=me, team_code="KD-B", valid_from=d1.isoformat(), reason="Dữ liệu thử")

    async def on(day: Any) -> dict[str, Any]:
        return next(r for r in (await q(admin, "hr.staff", on=day.isoformat()))["rows"] if str(r["party_id"]) == str(me))

    before, now = await on(d1 - timedelta(days=1)), await on(today())
    assert (before["title"], before["team"]) == ("Nhân viên", "Team Kinh doanh A")
    assert (now["title"], now["team"]) == ("Trưởng nhóm", "Team Kinh doanh B")
    assert before["ended_on"] == (d1 - timedelta(days=1)).isoformat() or str(before["ended_on"]) == str(d1 - timedelta(days=1))
    backwards = await executor.run(admin, "hr.set_employment", {"party_id": str(me), "employee_code": code, "started_on": d0.isoformat()})
    assert backwards.status == "invalid"
    card = await q(admin, "hr.history", party_id=me)
    assert len(card["blocks"][0]["page"]["rows"]) == 2 and len(card["blocks"][1]["page"]["rows"]) == 2


async def test_a_code_belongs_to_one_person_inside_a_legal_entity(who: Who) -> None:
    admin, a, b = await who("admin"), await fresh_sale(who), await fresh_sale(who)
    pa, pb = await _party(who, a), await _party(who, b)
    code = f"D{str(pa)[:8]}"
    await ok(admin, "hr.set_employment", party_id=pa, employee_code=code, started_on=today().isoformat())
    dup = await executor.run(admin, "hr.set_employment", {"party_id": str(pb), "employee_code": code, "started_on": today().isoformat()})
    assert dup.status == "invalid" and code in (dup.error or "")
    # Pháp nhân khác có thể có cùng mã (mỗi pháp nhân một dãy số).
    await ok(admin, "hr.set_employment", party_id=pb, employee_code=code, entity_code="MG", started_on=today().isoformat())


async def test_history_is_append_only_even_for_owner(who: Who) -> None:
    admin, sale = await who("admin"), await fresh_sale(who)
    me = await _party(who, sale)
    got = await ok(admin, "hr.set_employment", party_id=me, employee_code=f"A{str(me)[:8]}", started_on=today().isoformat())
    with pytest.raises(psycopg.errors.RaiseException, match="chỉ thêm"):
        async with db.owner_tx() as conn:
            await conn.execute("UPDATE hr.employments SET employee_code='X' WHERE id=%s", (got["employment_id"],))
    with pytest.raises(psycopg.errors.RaiseException, match="chỉ thêm"):
        async with db.owner_tx() as conn:
            await conn.execute("DELETE FROM hr.employments WHERE id=%s", (got["employment_id"],))


async def test_only_granted_roles_see_and_write_and_other_tenants_see_nothing(who: Who) -> None:
    admin, sale, lead, ketoan = await who("admin"), await fresh_sale(who), await who("truonga"), await who("ketoan")
    me = await _party(who, sale)
    await ok(admin, "hr.set_employment", party_id=me, employee_code=f"R{str(me)[:8]}", started_on=today().isoformat())
    assert (await executor.query(sale, "hr.staff", {})).status == "denied"
    assert (await executor.query(lead, "hr.staff", {})).status == "denied"
    assert (await executor.query(ketoan, "hr.staff", {})).status == "ok"
    denied = await executor.run(ketoan, "hr.set_employment", {"party_id": str(me), "employee_code": "Z", "started_on": today().isoformat()})
    assert denied.status == "denied"
    other = await who("admin", other=True)
    missing = await executor.run(other, "hr.set_employment", {"party_id": str(me), "employee_code": "Z", "started_on": today().isoformat()})
    assert missing.status == "not_found"
    assert str(me) not in {str(r["party_id"]) for r in (await q(other, "hr.staff"))["rows"]}


def test_forms_have_labels_and_hide_ids() -> None:
    for name in ("hr.set_employment", "hr.end_employment", "hr.set_assignment"):
        for f in forms.field_specs(registry.OPS[name]):
            assert f["label"] != f["key"], f"{name}.{f['key']}"
            assert not (f["key"].endswith("_id") and not f["hidden"] and f["kind"] != "select"), f"{name}.{f['key']}"


async def test_screen_appears_for_office_and_not_for_sale(who: Who) -> None:
    office, sale = await who("hcns"), await who("sale1")
    assert "hr_staff" in {s["key"] for ws in manifest(office, "Mẫu")["workspaces"] for s in ws["screens"]}
    assert "hr_staff" not in {s["key"] for ws in manifest(sale, "Mẫu")["workspaces"] for s in ws["screens"]}


async def test_pay_terms_live_only_in_tables_of_the_separate_terms_resource() -> None:
    """Thay test "không cột lương" của lát 1 bằng test quyền theo bảng có chủ đích (0150 §5, §8): cột tiền / bảo hiểm / người phụ thuộc của ``hr`` chỉ
    nằm ở bảng thuộc tài nguyên RIÊNG ``hr.terms`` — người được xem hồ sơ làm việc (``hr``) không mặc nhiên thấy lương."""
    rows = await owner_val("""SELECT coalesce(jsonb_agg(jsonb_build_array(c.table_name, obj_description(('hr.' || c.table_name)::regclass, 'pg_class'))), '[]')
                              FROM information_schema.columns c WHERE c.table_schema = 'hr'
                              AND (c.column_name LIKE '%%vnd' OR c.column_name IN ('insured_vnd', 'work_region', 'union_member', 'relation',
                                                                                    'contract_kind'))""")
    assert {t for t, _ in rows} == {"terms", "dependants"}
    assert all(comment == "resource=hr.terms" for _, comment in rows), rows
