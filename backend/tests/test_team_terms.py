"""Một nguồn cho cơ cấu đội (ADR 0256): ``core.team_members`` là hiện tại (quyền đọc), mọi thay đổi của nó được trigger DB chép thành dòng có ngày ở
``core.team_terms`` (hoa hồng đọc theo ngày ký qua ``hr.structure_on``). Một đội một trưởng; lùi ngày chỉ sửa lịch sử, cần lý do khi quá hạn mức."""

import uuid
from datetime import date, timedelta
from typing import Any

import psycopg
import pytest

from barista.config import settings
from barista.db import db
from barista.kernel import executor, registry
from barista.kernel.clock import today
from barista.kernel.executor import core_ctx
from tests.conftest import Who, approved, fresh_sale, ok, owner_val


async def _team(admin: Any, parent: str = "HQ") -> str:
    code = f"T{uuid.uuid4().hex[:6].upper()}"
    await ok(admin, "admin.upsert_team", code=code, name=f"Đội thử {code}", kind="sales", parent_code=parent)
    return code


async def _terms(user_id: Any, code: str) -> list[tuple[bool, date, date | None]]:
    async with await psycopg.AsyncConnection.connect(settings.owner_database_url) as c:
        cur = await c.execute("""SELECT tt.is_lead, tt.valid_from, tt.valid_to FROM core.team_terms tt JOIN core.teams t ON t.id = tt.team_id
                                 WHERE tt.user_id = %s AND t.code = %s ORDER BY tt.valid_from, tt.is_lead""", (user_id, code))
        return [tuple(r) for r in await cur.fetchall()]   # type: ignore[misc]


async def _lead_of(code: str) -> Any:
    return await owner_val("SELECT tm.user_id FROM core.team_members tm JOIN core.teams t ON t.id = tm.team_id WHERE t.code = %s AND tm.is_lead", (code,))


@pytest.fixture(autouse=True)
def _pack_reads_structure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Một module của lõi đóng vai gói ngành khai ``consumes("structure")`` (gói Hoa hồng thật ở repo gói) — cách làm của tests/test_system_calls.py."""
    monkeypatch.setattr(registry, "FAMILY_CALLERS", {**registry.FAMILY_CALLERS, "structure": {*registry.FAMILY_CALLERS.get("structure", ()), "hr"}})


async def _internal(p: Any, name: str, **data: Any) -> Any:
    async with db.tenant_tx(p) as conn:
        return await core_ctx(conn, p, module="hr").call_system(name, **data)


async def _party(p: Any) -> str:
    return str((await _internal(p, "parties.ensure_member", user_id=p.user_id))["party_id"])


async def _structure(p: Any, on: date) -> dict[str, Any]:
    return await _internal(p, "hr.structure_on", party_id=await _party(p), on=on)


async def test_every_change_of_membership_is_copied_with_dates_even_raw_sql(who: Who) -> None:
    admin = await who("admin")
    a, b = await _team(admin), await _team(admin)
    sale = await fresh_sale(who, a)
    assert await _terms(sale.user_id, a) == [(False, today(), None)]
    await ok(admin, "admin.update_member", email=sale.email, team_code=b)        # đổi trong cùng ngày: không để lại lịch sử dài 0 ngày
    assert await _terms(sale.user_id, a) == [] and await _terms(sale.user_id, b) == [(False, today(), None)]
    # ghi thẳng SQL (kết nối chủ, không qua lệnh nào) vẫn được trigger chép — không có đường ghi bỏ qua lịch sử
    await owner_val("""INSERT INTO core.team_members (tenant_id, user_id, team_id, is_lead, is_home)
                       SELECT %s, %s, id, false, false FROM core.teams WHERE code = %s AND tenant_id = %s RETURNING user_id""",
                    (sale.tenant_id, sale.user_id, a, sale.tenant_id))
    assert await _terms(sale.user_id, a) == [(False, today(), None)]


async def test_a_team_has_one_lead_and_the_new_lead_replaces_the_old_one(who: Who) -> None:
    admin = await who("admin")
    code = await _team(admin)
    old, new = await fresh_sale(who, code), await fresh_sale(who, code)
    await approved(admin, "admin.set_team_lead", team_code=code, email=old.email)
    assert await _lead_of(code) == old.user_id
    await approved(admin, "admin.set_team_lead", team_code=code, email=new.email)
    assert await _lead_of(code) == new.user_id                                     # trưởng cũ thành thành viên thường, không còn hai trưởng
    assert await owner_val("""SELECT count(*) FROM core.team_members tm JOIN core.teams t ON t.id = tm.team_id WHERE t.code = %s""", (code,)) == 2
    # hai trưởng cùng lúc bị DB chặn ở cuối giao dịch, kể cả ghi thẳng SQL
    with pytest.raises(psycopg.errors.ExclusionViolation):
        await owner_val("""UPDATE core.team_members tm SET is_lead = true FROM core.teams t
                           WHERE t.id = tm.team_id AND t.code = %s AND tm.user_id = %s RETURNING tm.user_id""", (code, old.user_id))


async def test_backdating_a_lead_rewrites_history_only_and_needs_a_reason_past_the_limit(who: Who) -> None:
    admin = await who("admin")
    code = await _team(admin)
    old, new = await fresh_sale(who, code), await fresh_sale(who, code)
    d_old, d_new = today() - timedelta(days=60), today() - timedelta(days=20)
    for p in (old, new):
        await ok(admin, "admin.set_team_since", email=p.email, team_code=code, valid_from=(d_old - timedelta(days=5)).isoformat(), reason="Dữ liệu thử")
    await approved(admin, "admin.set_team_lead", team_code=code, email=old.email, valid_from=d_old.isoformat(), reason="Quyết định bổ nhiệm số 3")
    no_reason = await executor.run(admin, "admin.set_team_lead", {"team_code": code, "email": new.email, "valid_from": d_new.isoformat()})
    assert no_reason.status == "invalid" and "lý do" in (no_reason.error or "")
    future = await executor.run(admin, "admin.set_team_lead", {"team_code": code, "email": new.email, "valid_from": (today() + timedelta(days=1)).isoformat()})
    assert future.status == "invalid" and "tương lai" in (future.error or "")
    await approved(admin, "admin.set_team_lead", team_code=code, email=new.email, valid_from=d_new.isoformat(), reason="Quyết định bổ nhiệm số 7")
    # trưởng cũ: thành viên → trưởng [d_old, d_new) → lại là thành viên từ d_new, không hở ngày nào
    assert await _terms(old.user_id, code) == [(False, d_old - timedelta(days=5), d_old), (True, d_old, d_new), (False, d_new, None)]
    assert await _terms(new.user_id, code) == [(False, d_old - timedelta(days=5), d_new), (True, d_new, None)]
    assert await owner_val("""SELECT tt.backdate_reason FROM core.team_terms tt JOIN core.teams t ON t.id = tt.team_id
                              WHERE t.code = %s AND tt.is_lead AND tt.valid_to IS NULL""", (code,)) == "Quyết định bổ nhiệm số 7"
    # cơ cấu tại ngày ký: giữa hai nhiệm kỳ trả đúng trưởng lúc đó, hôm nay trả trưởng mới — cùng nguồn với quyền
    then, now = await _structure(new, d_new - timedelta(days=1)), await _structure(new, today())
    assert then["teams"][0]["code"] == code and then["teams"][0]["head_party_id"] == await _party(old)
    assert now["teams"][0]["head_party_id"] == await _party(new)
    assert str(admin.user_id) in now["editors"]                                    # người sửa cơ cấu không tự xác nhận phần chia dựng từ nó (0160)


async def test_vacating_the_lead_from_a_past_date_leaves_the_team_without_a_head_from_then(who: Who) -> None:
    admin = await who("admin")
    code = await _team(admin)
    lead = await fresh_sale(who, code)
    since, gone = today() - timedelta(days=30), today() - timedelta(days=3)
    await approved(admin, "admin.set_team_lead", team_code=code, email=lead.email, valid_from=since.isoformat(), reason="Dữ liệu thử")
    await approved(admin, "admin.set_team_lead", team_code=code, email="", valid_from=gone.isoformat())
    assert await _lead_of(code) is None
    assert (await _structure(lead, gone - timedelta(days=1)))["teams"][0]["head_party_id"] == await _party(lead)
    assert (await _structure(lead, today()))["teams"][0]["head_party_id"] is None   # khuyết hiện rõ, không lấy người khác thay
    again = await executor.run(admin, "admin.set_team_lead", {"team_code": code, "email": ""})
    assert again.status == "invalid"


async def test_moving_home_team_answers_by_date_and_a_team_keeps_its_parent_history(who: Who) -> None:
    admin = await who("admin")
    a, b = await _team(admin), await _team(admin)
    sale = await fresh_sale(who, a)
    d0, d1 = today() - timedelta(days=40), today() - timedelta(days=5)
    await ok(admin, "admin.set_team_since", email=sale.email, team_code=a, valid_from=d0.isoformat(), reason="Dữ liệu thử")
    await ok(admin, "admin.update_member", email=sale.email, team_code=b)
    await ok(admin, "admin.set_team_since", email=sale.email, team_code=b, valid_from=d1.isoformat())
    assert (await _structure(sale, d1 - timedelta(days=1)))["teams"][0]["code"] == a
    assert (await _structure(sale, today()))["teams"][0]["code"] == b
    assert (await _structure(sale, d0 - timedelta(days=1)))["teams"] == []         # trước ngày có ghi nhận: không đoán
    # ngày vào đội mới không được nằm trước một thay đổi đã ghi của chính người đó
    early = await executor.run(admin, "admin.set_team_since", {"email": sale.email, "team_code": b, "valid_from": (d0 - timedelta(days=1)).isoformat(),
                                                               "reason": "Thử"})
    assert early.status in ("invalid", "error")
    # đổi cấp cha của đội: nhật ký giữ một dòng đang mở theo cấp cha mới
    await approved(admin, "admin.upsert_team", code=b, name=f"Đội thử {b}", kind="sales", parent_code=a)   # đổi cấp cha = đổi phạm vi quyền ⇒ qua duyệt
    assert await owner_val("""SELECT p.code FROM core.team_parent_log l JOIN core.teams t ON t.id = l.team_id JOIN core.teams p ON p.id = l.parent_id
                              WHERE t.code = %s AND l.valid_to IS NULL""", (b,)) == a
    assert [t["code"] for t in (await _structure(sale, today()))["teams"]][:2] == [b, a]
