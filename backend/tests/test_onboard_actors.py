"""KHUNG CHUYỂN DỮ LIỆU — người chạy theo thực thể và sự kiện giữa các lệnh (ADR 0263, bổ sung sau diễn tập) trên dữ liệu GIẢ: mỗi thực thể chạy bằng quyền
của đúng người bộ chuyển khai (``Entity(actor=…)`` + ``load(actors=…)``); thiếu quyền ⇒ MỘT danh sách trước khi mở lượt, không cấp quyền tạm; khoá chống lặp
không phụ thuộc người chạy (đổi người giữa chừng không tạo trùng); khung giao sự kiện sau mỗi thực thể, và sau mỗi lệnh với thực thể khai ``settle``."""

from typing import Any
from uuid import UUID

import pytest

from barista import seed
from barista.api import onboard as ob
from barista.db import db
from barista.kernel import executor
from tests.conftest import owner_val
from tests.test_onboard_load import PEOPLE, _names, _seq, people_entity

ROWS = [{"source_id": "1", "name": "Người Giả Một"}, {"source_id": "2", "name": "Người Giả Hai"}]


async def company(packs: tuple[str, ...] = ("directory",)) -> tuple[UUID, str]:
    """Công ty thử có thêm MỘT người thứ hai giữ vai được nhập danh mục (``nhap``) — người chạy của một thực thể."""
    domain = f"oba{next(_seq)}.demo"
    tid = await seed.create_tenant(f"Công ty chuyển dữ liệu {domain}", domain, people=[*PEOPLE, ("nhap", "Người Nhập Danh Mục", "admin", "QT", False)],
                                   packs=packs)
    return tid, domain


def _people(actor: str = "") -> ob.Entity:
    return ob.Entity("person", lambda: list(ROWS), "parties.create", lambda r, ids: {"display_name": r["name"], "kind": "person"}, "party_id", actor=actor)


def _teams(actor: str = "") -> ob.Entity:
    rows = [{"source_id": "t1", "code": "KD-GIA", "name": "Đội Giả"}]
    return ob.Entity("team", lambda: rows, "admin.upsert_team", lambda r, ids: {"code": r["code"], "name": r["name"], "parent_code": "QT"}, "team_id",
                     actor=actor)


async def _ran_by(tid: UUID, command: str) -> set[UUID]:
    async with db.owner.connection() as c:
        rows = await (await c.execute("SELECT DISTINCT user_id FROM core.audit_log WHERE tenant_id = %s AND command = %s AND status = 'ok'",
                                      (tid, command))).fetchall()
    return {r["user_id"] for r in rows}


async def test_each_entity_runs_as_the_person_the_converter_names() -> None:
    tid, domain = await company()
    admin, nhap = await seed.who(tid, "admin", domain), await seed.who(tid, "nhap", domain)
    conv = ob.Converter("hecu", (_teams(), _people("danh_muc")))
    r = await ob.load(tid, conv, by_email=f"admin@{domain}", actors={"danh_muc": f"nhap@{domain}"})
    assert (r.created, r.exceptions) == (3, 0)
    assert await _ran_by(tid, "admin.upsert_team") == {admin.user_id} and await _ran_by(tid, "parties.create") == {nhap.user_id}
    assert await owner_val("SELECT by_user FROM onboard.runs WHERE id = %s", (r.run_id,)) == admin.user_id   # lượt vẫn đứng tên người chạy lượt
    with pytest.raises(ValueError, match="danh_muc"):   # bộ chuyển khai người chạy mà lượt không nêu ai
        await ob.load(tid, conv, by_email=f"admin@{domain}")
    with pytest.raises(PermissionError, match="thành viên"):
        await ob.load(tid, conv, by_email=f"admin@{domain}", actors={"danh_muc": "nguoi-ngoai@example.invalid"})


async def test_missing_rights_are_reported_as_one_list_before_anything_is_written() -> None:
    tid, domain = await company()
    conv = ob.Converter("hecu", (_teams("ban_hang"), _people("ban_hang"), ob.Entity("ghost", list, "khong.co_lenh", lambda r, ids: {}, keyless=True)))
    with pytest.raises(PermissionError) as err:
        await ob.load(tid, conv, by_email=f"admin@{domain}", actors={"ban_hang": f"a@{domain}"})
    text = str(err.value)
    assert "admin.upsert_team" in text and "parties.create" in text and "khong.co_lenh" in text and "ban_hang" in text and "chưa ghi gì" in text
    assert text.count("\n- ") == 3   # MỘT danh sách đủ ba chỗ thiếu — không phải ba lượt chạy mới biết
    assert await owner_val("SELECT count(*) FROM onboard.runs WHERE tenant_id = %s", (tid,)) == 0
    assert await _names(tid) == []
    assert await owner_val("SELECT count(*) FROM core.grants g JOIN core.memberships m ON m.tenant_id = g.tenant_id AND m.role = g.role JOIN core.users u ON u.id = m.user_id"
                           " WHERE g.tenant_id = %s AND u.email = %s AND g.resource = 'parties' AND 'manage' = ANY(g.actions)", (tid, f"a@{domain}")) == 0


async def test_the_replay_key_belongs_to_the_load_not_to_whoever_runs_it() -> None:
    """Lượt bị ngắt TRƯỚC khi sổ mã gốc kịp ghi, lượt sau do NGƯỜI KHÁC chạy thực thể đó ⇒ nhận lại kết quả cũ, không tạo bản thứ hai."""
    tid, domain = await company()
    first = await ob.load(tid, ob.Converter("hecu", (_people(),)), by_email=f"admin@{domain}")
    assert first.created == 2
    async with db.owner_tx() as c:   # như thể tiến trình chết ngay sau khi lệnh ghi: sổ mã gốc mất các dòng vừa nạp
        await c.execute("DELETE FROM onboard.source_ids WHERE tenant_id = %s", (tid,))
    again = await ob.load(tid, ob.Converter("hecu", (_people("danh_muc"),)), by_email=f"admin@{domain}", actors={"danh_muc": f"nhap@{domain}"})
    assert (again.created, again.exceptions) == (2, 0)   # ghi lại sổ từ kết quả cũ
    assert await _names(tid) == ["Người Giả Hai", "Người Giả Một"]
    assert await owner_val("SELECT count(*) FROM core.idempotency WHERE tenant_id = %s AND key LIKE 'load:onboard:hecu:person:%%'", (tid,)) == 2
    # khoá của lượt nạp không trùng được với khoá người dùng gửi qua cửa thường (khoá của người luôn mang mã người đó)
    admin = await seed.who(tid, "admin", domain)
    key = await owner_val("SELECT substr(key, 6) FROM core.idempotency WHERE tenant_id = %s AND key LIKE 'load:%%' LIMIT 1", (tid,))
    r = await executor.run(admin, "parties.create", {"display_name": "Người Giả Khác", "kind": "person"}, key=key)
    assert r.status == "ok" and not r.replayed
    with pytest.raises(TypeError):
        await executor.run(admin, "parties.create", {"display_name": "Người Giả", "kind": "person"}, key=key, load_key=True)   # chỉ đi cùng nạp lịch sử


async def _pending_deliveries(tid: UUID) -> int:
    return await owner_val("SELECT count(*) FROM core.deliveries WHERE tenant_id = %s AND status = 'pending'", (tid,))


async def _spy_load(tid: UUID, domain: str, *, settle: bool) -> tuple[ob.Report, list[tuple[str, int]]]:
    """Chạy bộ chuyển (người → đổi tên [phát sự kiện] → liên hệ) và ghi số sự kiện CHƯA giao ngay trước từng lệnh nghiệp vụ."""
    await ob.load(tid, ob.Converter("hecu", (people_entity(ROWS),)), by_email=f"admin@{domain}")
    rename = ob.Entity("rename", lambda: list(ROWS), "parties.update",
                       lambda r, ids: {"party_id": str(ids.need("person", r["source_id"])), "display_name": f"{r['name']} (tên mới)"}, keyless=True, settle=settle)
    contact = ob.Entity("contact", lambda: list(ROWS[:1]), "parties.create", lambda r, ids: {"display_name": "Liên hệ Giả", "kind": "person"}, "party_id")
    seen: list[tuple[str, int]] = []
    real = executor.run

    async def spy(p: Any, name: str, inp: Any, **kw: Any) -> Any:
        if not p.system:
            seen.append((name, await _pending_deliveries(tid)))
        return await real(p, name, inp, **kw)

    mp = pytest.MonkeyPatch()
    mp.setattr(executor, "run", spy)
    try:
        r = await ob.load(tid, ob.Converter("hecu", (people_entity(ROWS), rename, contact)), by_email=f"admin@{domain}")
    finally:
        mp.undo()
    return r, seen


async def test_events_are_delivered_after_each_entity_and_after_each_command_when_settle() -> None:
    tid, domain = await company(("crm",))
    r, seen = await _spy_load(tid, domain, settle=False)
    assert [name for name, _ in seen] == ["parties.update", "parties.update", "parties.create"]
    assert seen[0][1] == 0 and seen[1][1] > 0   # không khai settle: sự kiện của lệnh 1 còn chờ khi lệnh 2 chạy…
    assert seen[2][1] == 0                      # …nhưng đã giao hết trước thực thể kế
    assert r.events_delivered > 0 and await _pending_deliveries(tid) == 0   # xong lượt không còn sự kiện treo — không cần máy chủ / vòng riêng
    assert await owner_val("SELECT bool_and(historical) FROM core.events WHERE tenant_id = %s AND type = 'parties.party_renamed'", (tid,)) is True
    tid, domain = await company(("crm",))
    r, seen = await _spy_load(tid, domain, settle=True)
    assert [n for _, n in seen] == [0, 0, 0] and r.events_delivered > 0   # settle: giao hết sau MỖI lệnh
