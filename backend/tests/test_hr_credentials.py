"""Chứng chỉ nhân sự (0202): loại là dữ liệu công ty; phạm vi của tôi / đội / cả công ty (RLS) và số chứng chỉ chỉ ở bảng riêng; lọc sắp hết hạn /
thiếu; nhắc một lần mỗi mốc, gộp theo người nhận, không nhắc mốc đã qua lúc ghi; nhập Excel; đổi đội ⇒ trưởng đội mới thấy."""

import base64
import itertools
from datetime import timedelta
from typing import Any
from uuid import UUID

from barista import seed
from barista.db import db
from barista.kernel import events, executor, imports
from barista.kernel.clock import today
from barista.kernel.principal import Principal, system_principal
from tests.conftest import ok, owner_val, q

_seq = itertools.count(1)
PEOPLE = [("admin", "Quản trị", "admin", "QT", True), ("nhansu", "Phụ trách nhân sự", "office", "HCNS", True),
          ("truong", "Trưởng nhóm A", "team_lead", "KD-A", True), ("a", "Sale A", "sale", "KD-A", False),
          ("truongb", "Trưởng nhóm B", "team_lead", "KD-B", True), ("b", "Sale B", "sale", "KD-B", False)]
NOTES = "SELECT count(*) FROM core.notifications WHERE tenant_id = %s AND user_id = %s AND kind = 'hr_credential'"
PDF = b"%PDF-1.4\n% chung chi mau\n"


async def _company() -> tuple[UUID, str, dict[str, Principal]]:
    domain = f"cc{next(_seq)}.demo"
    tid = await seed.create_tenant(f"Công ty chứng chỉ {domain}", domain, people=PEOPLE)
    return tid, domain, {k[0]: await seed.who(tid, k[0], domain) for k in PEOPLE}


async def _kind(hr: Principal, key: str = "the_an_toan", name: str = "Thẻ an toàn lao động", **kw: Any) -> None:
    await ok(hr, "hr.save_credential_kind", **{"key": key, "name": name, "remind_days": 30, **kw})


async def _record(hr: Principal, d: str, who: str, kind: str = "the_an_toan", *, days: int | None = 20, number: str = "AT-0001", **kw: Any) -> UUID:
    exp = (today() + timedelta(days=days)).isoformat() if days is not None else None
    out = await ok(hr, "hr.record_credential", person=f"{who}@{d}", kind_key=kind, number=number, expires_on=exp, **kw)
    return UUID(str(out["credential_id"]))


def _ids(page: dict[str, Any]) -> set[str]:
    return {str(r["id"]) for r in page["rows"] if r.get("id")}


async def test_kinds_are_company_data_not_code_and_pack_defaults_keep_company_edits() -> None:
    tid, d, w = await _company()
    assert await owner_val("SELECT count(*) FROM hr.credential_kinds WHERE tenant_id = %s", (tid,)) == 0   # lõi không mang loại nào
    await _kind(w["nhansu"], validity_months=24, required_roles=["sale"], required_teams=["KD-B"])
    # bộ mặc định của gói (only_new) không ghi đè loại công ty đã đặt
    out = await ok(w["nhansu"], "hr.save_credential_kind", key="the_an_toan", name="Tên của gói", only_new=True)
    assert out["added"] is False
    assert await owner_val("SELECT name FROM hr.credential_kinds WHERE tenant_id = %s AND key = 'the_an_toan'", (tid,)) == "Thẻ an toàn lao động"
    page = await q(w["nhansu"], "hr.credential_kinds")
    assert page["rows"][0]["required"] == "sale, KD-B"
    # công ty khác không thấy; người không phải nhân sự không sửa được
    _, _, w2 = await _company()
    assert (await q(w2["nhansu"], "hr.credential_kinds"))["rows"] == []
    assert (await executor.run(w["a"], "hr.save_credential_kind", {"key": "x", "name": "X"})).status == "denied"
    bad = await executor.run(w["nhansu"], "hr.save_credential_kind", {"key": "Thẻ", "name": "X"})
    assert bad.status == "invalid"
    # thời hạn mặc định: trống ngày hết hạn ⇒ ngày cấp + số tháng (ngày cuối còn hiệu lực)
    issued = today() - timedelta(days=10)
    cid = UUID(str((await ok(w["nhansu"], "hr.record_credential", person=f"a@{d}", kind_key="Thẻ an toàn lao động",
                             issued_on=issued.isoformat()))["credential_id"]))
    exp = await owner_val("SELECT expires_on FROM hr.credentials WHERE id = %s", (cid,))
    assert exp.month == ((issued.month - 1 + 24) % 12) + 1 and exp >= issued + timedelta(days=700)


async def test_own_team_all_scopes_and_the_number_only_for_the_person_and_hr() -> None:
    tid, d, w = await _company()
    hr = w["nhansu"]
    await _kind(hr)
    ca = await _record(hr, d, "a", number="AT-A-111")
    cb = await _record(hr, d, "b", number="AT-B-222")
    await ok(hr, "hr.attach_credential_file", credential_id=str(ca), filename="the.pdf", content_type="application/pdf",
             data_b64=base64.b64encode(PDF).decode())
    # của tôi: chỉ của mình, có số
    mine = await q(w["a"], "hr.my_credentials")
    assert _ids(mine) == {str(ca)} and mine["rows"][0]["number"] == "AT-A-111" and mine["rows"][0]["file"] == "yes"
    assert (await executor.query(w["a"], "hr.credentials", {})).status == "denied"
    got = await q(w["a"], "hr.credential_file", credential_id=str(ca))
    assert base64.b64decode(got["data_b64"]) == PDF
    # đội: trưởng đội A thấy chứng chỉ của Sale A (trạng thái), không thấy của đội B, không có cột số, không mở được bản scan
    team = await q(w["truong"], "hr.credentials")
    assert _ids(team) == {str(ca)} and "number" not in {c["key"] for c in team["columns"]}
    assert (await executor.query(w["truong"], "hr.credential_file", {"credential_id": str(ca)})).status == "not_found"
    # cả công ty: nhân sự thấy hết, có số
    allp = await q(hr, "hr.credentials")
    assert _ids(allp) >= {str(ca), str(cb)} and {r["number"] for r in allp["rows"]} >= {"AT-A-111", "AT-B-222"}
    # RLS ở DB (không chỉ ở truy vấn): trưởng đội đọc thẳng bảng — thấy dòng chứng chỉ của đội, không thấy dòng số nào
    async with db.tenant_tx(w["truong"], rollback=True) as conn:
        rows = await (await conn.execute("SELECT id FROM hr.credentials")).fetchall()
        assert {r["id"] for r in rows} == {ca}
        assert (await (await conn.execute("SELECT count(*) AS n FROM hr.credential_private")).fetchone())["n"] == 0   # type: ignore[index]
    async with db.tenant_tx(w["a"], rollback=True) as conn:
        assert (await (await conn.execute("SELECT count(*) AS n FROM hr.credential_private")).fetchone())["n"] == 1   # type: ignore[index]
    # người khác không ghi được; công ty khác không thấy gì
    assert (await executor.run(w["truong"], "hr.record_credential", {"person": f"a@{d}", "kind_key": "the_an_toan"})).status == "denied"
    _, _, w2 = await _company()
    assert (await q(w2["nhansu"], "hr.credentials"))["rows"] == []
    # công cụ trợ lý: không có số
    watch = await q(hr, "hr.credential_watch", show="expiring")
    assert watch["rows"] and all("number" not in r for r in watch["rows"])
    # số chứng chỉ không vào nhật ký thao tác
    logged = await owner_val("SELECT count(*) FROM core.audit_log WHERE tenant_id = %s AND input::text LIKE '%%AT-A-111%%'", (tid,))
    assert logged == 0


async def test_expiring_window_expired_missing_and_renewal_replaces() -> None:
    _, d, w = await _company()
    hr = w["nhansu"]
    await _kind(hr, required_roles=["sale"])
    soon = await _record(hr, d, "a", days=20)
    await _kind(hr, "bang_lai", "Bằng lái xe")
    later = await _record(hr, d, "a", "bang_lai", days=80)
    assert _ids(await q(hr, "hr.credentials", show="expiring", within_days=30)) == {str(soon)}
    assert _ids(await q(hr, "hr.credentials", show="expiring", within_days=90)) == {str(soon), str(later)}
    # thiếu: vai "sale" phải có thẻ an toàn — Sale B chưa có
    missing = await q(hr, "hr.credentials", show="missing")
    assert [(r["name"], r["kind_key"]) for r in missing["rows"]] == [("Sale B", "the_an_toan")]
    assert [r["name"] for r in (await q(w["truongb"], "hr.credential_watch", show="missing"))["rows"]] == ["Sale B"]
    assert (await q(w["truong"], "hr.credential_watch", show="missing"))["rows"] == []   # Sale B không thuộc đội A
    # gia hạn: bản mới ⇒ bản cũ "đã có bản mới", không còn ở danh sách sắp hết hạn
    renewed = await _record(hr, d, "a", days=700, number="AT-0002")
    hist = {str(r["id"]): r["status"] for r in (await q(hr, "hr.credentials", show="history"))["rows"]}
    assert hist[str(soon)] == "replaced" and hist[str(renewed)] == "valid"
    assert str(soon) not in _ids(await q(hr, "hr.credentials", show="expiring", within_days=30))
    # thu hồi
    await ok(hr, "hr.revoke_credential", credential_id=str(later), reason="Ghi nhầm loại")
    assert {str(r["id"]): r["status"] for r in (await q(hr, "hr.credentials", show="history"))["rows"]}[str(later)] == "revoked"


async def test_reminders_once_per_milestone_aggregated_per_recipient_and_silent_for_past_milestones() -> None:
    tid, d, w = await _company()
    hr = w["nhansu"]
    for key, name in (("the_an_toan", "Thẻ an toàn lao động"), ("bang_lai", "Bằng lái xe"), ("so_cuu", "Chứng nhận sơ cứu")):
        await _kind(hr, key, name)
        await _record(hr, d, "a", key, days=10)
    await _record(hr, d, "b", "the_an_toan", days=5)
    await _record(hr, d, "b", "bang_lai", days=-3)   # đã hết hạn LÚC GHI ⇒ không báo "đã hết hạn"
    await _record(hr, d, "b", "so_cuu", days=200)    # xa ⇒ chưa tới mốc
    got = await ok(system_principal(tid), "hr.remind_credentials")
    assert got["claimed"] == 4
    assert await owner_val(NOTES, (tid, w["a"].user_id)) == 1                           # MỘT tin cho 3 chứng chỉ của mình
    assert await owner_val("SELECT title FROM core.notifications WHERE tenant_id = %s AND user_id = %s AND kind = 'hr_credential'",
                           (tid, w["a"].user_id)) == "Chứng chỉ sắp hết hạn: 3 chứng chỉ"
    assert await owner_val(NOTES, (tid, w["truong"].user_id)) == 1                      # trưởng đội A: một tin gộp
    assert await owner_val(NOTES, (tid, w["b"].user_id)) == 1 and await owner_val(NOTES, (tid, w["truongb"].user_id)) == 1
    body = await owner_val("SELECT title || ' ' || body || ' ' || push FROM core.notifications WHERE tenant_id = %s AND user_id = %s AND kind = 'hr_credential'",
                           (tid, w["truong"].user_id))
    assert "Sale A" in body and "AT-0001" not in body                                     # tên người, không số chứng chỉ
    # chạy lại: không gì mới
    assert (await ok(system_principal(tid), "hr.remind_credentials"))["claimed"] == 0
    assert await owner_val(NOTES, (tid, w["a"].user_id)) == 1
    # bản mới hơn của bằng lái cho Sale A (dựng thẳng: ghi từ trước, hết hạn sau bản cũ) ⇒ bản cũ đã báo bị thay; bản mới chưa báo mốc của nó ⇒ báo
    async with db.owner_tx() as conn:
        pid = (await (await conn.execute("SELECT party_id FROM hr.credentials WHERE tenant_id = %s AND user_id = %s LIMIT 1", (tid, w["a"].user_id))).fetchone())
        assert pid is not None
        await conn.execute("""INSERT INTO hr.credentials (tenant_id, party_id, user_id, kind_id, expires_on, recorded_on)
                              VALUES (%s, %s, %s, (SELECT id FROM hr.credential_kinds WHERE tenant_id = %s AND key = 'bang_lai'), %s, %s)""",
                           (tid, pid["party_id"], w["a"].user_id, tid, today() + timedelta(days=11), today() - timedelta(days=400)))
    got = await ok(system_principal(tid), "hr.remind_credentials")
    assert got["claimed"] == 1 and await owner_val(NOTES, (tid, w["a"].user_id)) == 2
    assert await owner_val("SELECT count(*) FROM hr.credential_notices WHERE tenant_id = %s AND milestone = 'expired'", (tid,)) == 0


async def test_expired_milestone_fires_once_for_a_credential_recorded_while_valid() -> None:
    tid, d, w = await _company()
    hr = w["nhansu"]
    await _kind(hr)
    cid = await _record(hr, d, "a", days=200)
    async with db.owner_tx() as conn:   # dòng ghi khi còn hạn, nay đã quá hạn (đồng hồ DB không lùi được: dựng bằng một dòng y hệt, ghi 400 ngày trước)
        await conn.execute("""INSERT INTO hr.credentials (tenant_id, party_id, user_id, team_id, kind_id, expires_on, recorded_on)
                              SELECT tenant_id, party_id, user_id, team_id, kind_id, %s, %s FROM hr.credentials WHERE id = %s""",
                           (today() - timedelta(days=2), today() - timedelta(days=400), cid))
        # bản đang còn hạn bị thu hồi ⇒ bản đã hết hạn là bản hiện hành
        await conn.execute("UPDATE hr.credentials SET revoked_at = now(), revoked_by = user_id, revoke_reason = 'thử' WHERE id = %s", (cid,))
    got = await ok(system_principal(tid), "hr.remind_credentials")
    assert got["claimed"] == 1
    assert await owner_val("SELECT title FROM core.notifications WHERE tenant_id = %s AND user_id = %s AND kind = 'hr_credential'",
                           (tid, w["a"].user_id)) == f"Chứng chỉ đã hết hạn: Thẻ an toàn lao động (hết hạn {today() - timedelta(days=2):%d/%m/%Y})"
    assert (await ok(system_principal(tid), "hr.remind_credentials"))["claimed"] == 0


async def test_import_from_excel_rows_including_a_historical_load_is_idempotent() -> None:
    tid, d, w = await _company()
    hr = w["nhansu"]
    await _kind(hr)
    await ok(w["admin"], "hr.set_employment", party_id=await owner_val("SELECT id FROM parties.parties WHERE tenant_id = %s AND user_id = %s", (tid, w["b"].user_id)),
             employee_code="NV0002", started_on=today().isoformat())
    rows = [{"Người": f"a@{d}", "Loại chứng chỉ": "Thẻ an toàn lao động", "Số chứng chỉ": "AT-9", "Ngày cấp": "01/02/2024",
             "Ngày hết hạn": (today() + timedelta(days=15)).strftime("%d/%m/%Y")},
            {"Người": "NV0002", "Loại chứng chỉ": "the_an_toan", "Số chứng chỉ": "AT-10", "Ngày hết hạn": "31/12/2020"}]
    for _ in range(2):   # nhập lại cùng file: không ghi hai lần
        r = await imports.run_import(hr, "hr.record_credential", rows, dry_run=False, historical=True)
        assert r.status == "ok", r.error
    assert await owner_val("SELECT count(*) FROM hr.credentials WHERE tenant_id = %s", (tid,)) == 2
    assert await owner_val("SELECT count(*) FROM hr.credentials WHERE tenant_id = %s AND loaded_at IS NOT NULL", (tid,)) == 2
    page = await q(hr, "hr.credentials", show="unverified")
    assert len(page["rows"]) == 2 and {r["status"] for r in page["rows"]} == {"expiring", "expired"}
    bad = await imports.run_import(hr, "hr.record_credential", [{"Người": "không có ai", "Loại chứng chỉ": "the_an_toan"}], dry_run=True)
    assert "không thấy trong danh mục" in str(bad.output)   # dòng lỗi báo rõ, không ghi


async def test_moving_team_moves_who_sees_the_credential() -> None:
    _, d, w = await _company()
    hr = w["nhansu"]
    await _kind(hr)
    ca = await _record(hr, d, "a")
    assert _ids(await q(w["truong"], "hr.credentials")) == {str(ca)}
    await ok(w["admin"], "admin.update_member", email=f"a@{d}", team_code="KD-B")
    await events.drain()
    assert _ids(await q(w["truongb"], "hr.credentials")) == {str(ca)}
    assert _ids(await q(w["truong"], "hr.credentials")) == set()


async def test_history_is_append_only_even_for_owner() -> None:
    import psycopg
    import pytest

    tid, d, w = await _company()
    await _kind(w["nhansu"])
    cid = await _record(w["nhansu"], d, "a")
    for sql in ("UPDATE hr.credentials SET expires_on = expires_on + 1 WHERE id = %s", "DELETE FROM hr.credentials WHERE id = %s",
                "UPDATE hr.credential_private SET number = 'X' WHERE credential_id = %s"):
        with pytest.raises(psycopg.errors.RaiseException):
            async with db.owner_tx() as conn:
                await conn.execute(sql, (cid,))
    assert tid
