"""KHUNG CHUYỂN DỮ LIỆU HỆ CŨ (ADR 0263 mục B) trên dữ liệu GIẢ: chạy lại cùng nguồn không tạo trùng; đổi một bản ghi ⇒ chỉ bản đó chạy ``update`` (hoặc
thành ngoại lệ "đổi sau khi nạp"); mọi lệnh chạy ``historical`` (không thông báo, lead nằm đỗ); dữ liệu nguồn sai / thiếu thành ngoại lệ, không tự điền."""

import itertools
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest

from barista import seed
from barista.api import onboard as ob
from barista.db import db
from barista.kernel import events, executor
from barista.kernel.clock import today
from tests.conftest import fresh_phone, ok, owner_val

_seq = itertools.count(1)
PEOPLE = [("admin", "Quản trị", "admin", "QT", True), ("gd", "Giám đốc", "director", "QT", False), ("a", "Sale A", "sale", "KD-A", False)]


async def company(packs: tuple[str, ...] = ("directory",)) -> tuple[UUID, str]:
    domain = f"ob{next(_seq)}.demo"
    tid = await seed.create_tenant(f"Công ty chuyển dữ liệu {domain}", domain, people=PEOPLE, packs=packs)
    return tid, domain


def people_entity(rows: list[dict[str, Any]], *, update: bool = False) -> ob.Entity:
    def to_input(r: dict[str, Any], ids: ob.Ids) -> dict[str, Any]:
        if not (r.get("name") or "").strip():
            raise ob.Problem("bad_source", "thiếu tên")
        return {"display_name": r["name"].strip(), "kind": "person"}

    return ob.Entity("person", lambda: list(rows), "parties.create", to_input, "party_id",
                     update="parties.update" if update else "",
                     update_input=(lambda r, ids, t: {"party_id": str(t), "display_name": r["name"].strip()}) if update else None, label="Người")


async def _names(tid: UUID) -> list[str]:
    async with db.owner.connection() as c:
        rows = await (await c.execute("SELECT display_name FROM parties.parties WHERE tenant_id = %s AND user_id IS NULL ORDER BY display_name",
                                      (tid,))).fetchall()
    return [r["display_name"] for r in rows]


async def test_rerun_of_the_same_source_creates_nothing_twice() -> None:
    tid, domain = await company()
    rows = [{"source_id": "1", "name": "Người Giả Một"}, {"source_id": "2", "name": "Người Giả Hai"}, {"source_id": "3", "name": "Người Giả Ba"}]
    conv = ob.Converter("hecu", (people_entity(rows),))
    first = await ob.load(tid, conv, by_email=f"admin@{domain}")
    assert (first.status, first.created, first.skipped, first.exceptions) == ("done", 3, 0, 0)
    again = await ob.load(tid, conv, by_email=f"admin@{domain}")
    assert (again.created, again.updated, again.skipped) == (0, 0, 3)
    assert await _names(tid) == ["Người Giả Ba", "Người Giả Hai", "Người Giả Một"]
    assert await owner_val("SELECT count(*) FROM onboard.source_ids WHERE tenant_id = %s", (tid,)) == 3
    assert [r["status"] for r in again.recon if r["metric"] == "count.person"] == ["khop"]
    assert await owner_val("SELECT count(*) FROM onboard.runs WHERE tenant_id = %s AND status = 'done'", (tid,)) == 2


async def test_a_changed_record_runs_update_only_for_that_record() -> None:
    tid, domain = await company()
    rows = [{"source_id": "1", "name": "Người Giả Một"}, {"source_id": "2", "name": "Người Giả Hai"}]
    await ob.load(tid, ob.Converter("hecu", (people_entity(rows, update=True),)), by_email=f"admin@{domain}")
    rows[1] = {"source_id": "2", "name": "Người Giả Hai (đổi tên)"}
    r = await ob.load(tid, ob.Converter("hecu", (people_entity(rows, update=True),)), by_email=f"admin@{domain}")
    assert (r.created, r.updated, r.skipped, r.exceptions) == (0, 1, 1, 0)
    assert await _names(tid) == ["Người Giả Hai (đổi tên)", "Người Giả Một"]
    # thực thể không khai lệnh cập nhật ⇒ nguồn đổi thành ngoại lệ, không ghi đè lặng lẽ, không tạo bản thứ hai (công ty khác: bộ chuyển giữ nguyên định nghĩa)
    tid, domain = await company()
    rows = [{"source_id": "1", "name": "Người Giả Một"}, {"source_id": "2", "name": "Người Giả Hai"}]
    await ob.load(tid, ob.Converter("hecu", (people_entity(rows),)), by_email=f"admin@{domain}")
    rows[0] = {"source_id": "1", "name": "Người Giả Một (đổi)"}
    r = await ob.load(tid, ob.Converter("hecu", (people_entity(rows),)), by_email=f"admin@{domain}")
    assert (r.created, r.updated, r.skipped, r.exceptions) == (0, 0, 1, 1)
    assert await owner_val("SELECT reason FROM onboard.exceptions WHERE run_id = %s", (r.run_id,)) == "changed_after_load"
    assert await _names(tid) == ["Người Giả Hai", "Người Giả Một"]


async def test_a_value_that_changes_and_comes_back_is_applied_each_time() -> None:
    """A → B → A (→ B): mỗi lần đổi là một lần cập nhật MỚI (khoá chống lặp mang số lần) — không bị nhận nhầm là lần chạy lại của lần trước."""
    tid, domain = await company()
    rows = [{"source_id": "1", "name": "Người Giả A"}]
    conv = ob.Converter("hecu", (people_entity(rows, update=True),))
    await ob.load(tid, conv, by_email=f"admin@{domain}")
    for name in ("Người Giả B", "Người Giả A", "Người Giả B"):
        rows[0] = {"source_id": "1", "name": name}
        r = await ob.load(tid, conv, by_email=f"admin@{domain}")
        assert (r.created, r.updated, r.exceptions) == (0, 1, 0), name
        assert await _names(tid) == [name]
    assert await owner_val("SELECT version FROM onboard.source_ids WHERE tenant_id = %s", (tid,)) == 3


async def test_changing_the_mapping_reloads_unchanged_source_rows() -> None:
    """Nguồn giữ nguyên nhưng ánh xạ đổi (``to_input`` / ``mapping_version``) ⇒ dòng được cập nhật lại, không bị bỏ qua."""
    tid, domain = await company()
    rows = [{"source_id": "1", "name": "người giả một"}, {"source_id": "2", "name": "người giả hai"}]
    await ob.load(tid, ob.Converter("hecu", (people_entity(rows, update=True),), mapping_version="1"), by_email=f"admin@{domain}")
    same = await ob.load(tid, ob.Converter("hecu", (people_entity(rows, update=True),), mapping_version="1"), by_email=f"admin@{domain}")
    assert (same.updated, same.skipped) == (0, 2)
    bumped = await ob.load(tid, ob.Converter("hecu", (people_entity(rows, update=True),), mapping_version="2"), by_email=f"admin@{domain}")
    assert (bumped.created, bumped.updated, bumped.skipped) == (0, 2, 0)


async def test_a_field_used_only_by_the_update_mapping_is_noticed() -> None:
    """Lệnh tạo chỉ nhận tên; lệnh cập nhật dùng thêm biệt danh ⇒ chỉ biệt danh đổi vẫn là "đổi" (dấu vân tay băm cả đầu vào cập nhật)."""
    tid, domain = await company()
    rows = [{"source_id": "1", "name": "Người Giả Một", "nick": "Một"}]

    def to_input(r: dict[str, Any], ids: ob.Ids) -> dict[str, Any]:
        return {"display_name": r["name"], "kind": "person"}

    def update_input(r: dict[str, Any], ids: ob.Ids, target: UUID) -> dict[str, Any]:
        return {"party_id": str(target), "display_name": f"{r['name']} ({r['nick']})"}

    conv = ob.Converter("hecu", (ob.Entity("person", lambda: list(rows), "parties.create", to_input, "party_id", update="parties.update",
                                           update_input=update_input, label="Người"),))
    await ob.load(tid, conv, by_email=f"admin@{domain}")
    same = await ob.load(tid, conv, by_email=f"admin@{domain}")
    assert (same.updated, same.skipped) == (0, 1)
    rows[0] = {**rows[0], "nick": "Số Một"}
    r = await ob.load(tid, conv, by_email=f"admin@{domain}")
    assert (r.updated, r.skipped, r.exceptions) == (1, 0, 0)
    assert await _names(tid) == ["Người Giả Một (Số Một)"]


async def test_a_record_gone_from_the_source_is_an_exception_not_a_delete() -> None:
    tid, domain = await company()
    rows = [{"source_id": "1", "name": "Người Giả Một"}, {"source_id": "2", "name": "Người Giả Hai"}]
    await ob.load(tid, ob.Converter("hecu", (people_entity(rows),)), by_email=f"admin@{domain}")
    r = await ob.load(tid, ob.Converter("hecu", (people_entity(rows[:1]),)), by_email=f"admin@{domain}")
    assert (r.created, r.skipped, r.exceptions) == (0, 1, 1)
    assert await owner_val("SELECT reason || ':' || source_id FROM onboard.exceptions WHERE run_id = %s", (r.run_id,)) == "missing_at_source:2"
    assert await _names(tid) == ["Người Giả Hai", "Người Giả Một"]   # không tự xoá ở Barista


async def test_bad_source_rows_become_exceptions_and_are_not_invented() -> None:
    tid, domain = await company()
    rows = [{"source_id": "1", "name": "Người Giả Một"}, {"source_id": "2", "name": "  "}, {"name": "Không mã"}, {"source_id": "1", "name": "Trùng"}]
    contacts = [{"source_id": "c1", "person": "1", "name": "Liên hệ Giả"}, {"source_id": "c2", "person": "99", "name": "Liên hệ lạc"}]

    def contact_input(r: dict[str, Any], ids: ob.Ids) -> dict[str, Any]:
        ids.need("person", r["person"])
        return {"display_name": r["name"], "kind": "person"}

    conv = ob.Converter("hecu", (people_entity(rows), ob.Entity("contact", lambda: contacts, "parties.create", contact_input, "party_id")))
    r = await ob.load(tid, conv, by_email=f"admin@{domain}")
    assert (r.created, r.exceptions) == (2, 4)
    got = await owner_val("""SELECT string_agg(entity || ':' || source_id || ':' || reason, ' ' ORDER BY entity, source_id, reason) FROM onboard.exceptions
                             WHERE run_id = %s""", (r.run_id,))
    assert got == "contact:c2:missing_ref person::bad_source person:1:duplicate person:2:bad_source"
    assert await _names(tid) == ["Liên hệ Giả", "Người Giả Một"]   # không có "Trùng", không có tên trống bị điền
    # đếm đối chiếu thấy phần thiếu: nguồn 2 người có mã (1, 2), Barista 1 ⇒ lệch
    assert {x["metric"]: x["status"] for x in r.recon} == {"count.person": "lech", "count.contact": "lech"}


async def test_every_command_runs_historical_no_notification_and_leads_stay_parked() -> None:
    tid, domain = await company(("crm",))
    admin = await seed.who(tid, "admin", domain)
    await ok(admin, "leadflow.set_rule", name="Chia lead", effective_from=(today() - timedelta(days=1)).isoformat(), cap_uncalled=0, roles=["sale"],
             first_call_min=15, max_recalls=2, days="all", open_at="00:00", close_at="24:00", remind_before_min=0, escalate=True, team_code="KD-A")
    leads = [{"source_id": f"L{i}", "name": f"Khách cũ {i}", "phone": fresh_phone()} for i in range(3)]
    seen: list[bool] = []
    real = executor.run

    async def spy(p: Any, name: str, inp: Any, **kw: Any) -> Any:
        if not p.system:
            seen.append(bool(kw.get("historical")))
        return await real(p, name, inp, **kw)

    mp = pytest.MonkeyPatch()
    mp.setattr(executor, "run", spy)
    try:
        conv = ob.Converter("hecu", (ob.Entity("lead", lambda: leads, "leadflow.intake", lambda r, ids: {"name": r["name"], "phone": r["phone"]},
                                               "lead_id"),))
        r = await ob.load(tid, conv, by_email=f"admin@{domain}", kind="final")
    finally:
        mp.undo()
    assert r.created == 3 and seen == [True, True, True]
    await events.drain()
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE tenant_id = %s", (tid,)) == 0
    assert await owner_val("SELECT count(*) FROM work.items WHERE tenant_id = %s", (tid,)) == 0
    assert await owner_val("""SELECT count(*) FROM leadflow.lead_state s JOIN onboard.source_ids o ON o.target_id = s.lead_id
                              WHERE o.tenant_id = %s AND s.imported_at IS NOT NULL""", (tid,)) == 3


async def test_the_runner_needs_a_member_who_may_run_onboarding() -> None:
    tid, domain = await company()
    conv = ob.Converter("hecu", (people_entity([{"source_id": "1", "name": "Người Giả"}]),))
    with pytest.raises(PermissionError):
        await ob.load(tid, conv, by_email=f"a@{domain}")   # sale: không có quyền Đưa vào dùng
    with pytest.raises(PermissionError):
        await ob.load(tid, conv, by_email="nguoi-ngoai@example.invalid")
    with pytest.raises(ValueError):
        await ob.load(tid, ob.Converter("Hệ Cũ", conv.entities), by_email=f"admin@{domain}")
