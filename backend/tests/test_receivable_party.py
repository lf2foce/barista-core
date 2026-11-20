"""0177 §1 — bên nợ của khoản phải thu là MỘT định danh (``acct.receivables.party_id``), cột chữ ``party`` chỉ là tên hiện (một nơi ghi): ghi mới theo mã hoặc
theo TÊN (tìm / tạo qua ``parties.ensure_counterparty``), mọi đường ghi (phí CĐT, khoản thu khác, ký gửi, học phí, số dư đầu kỳ) gắn mã, chạy bù khoản cũ
(``acct.link_receivable_parties``): khớp tên chuẩn hoá, tạo bên thiếu qua hợp đồng, báo số khớp / tạo / chưa gắn, chạy lại không đổi, công ty khác tách."""

import itertools
import uuid
from typing import Any

from barista import seed
from barista.db import db
from barista.kernel import executor
from barista.kernel.clock import today
from barista.kernel.principal import Principal, elevate, system_principal
from tests.conftest import ok, owner_val

_seq = itertools.count(1)
PEOPLE = [("admin", "Quản trị", "admin", "QT", True), ("ketoan", "Kế toán", "accountant", "KT", True), ("sale", "Sale", "sale", "KD-A", False)]


async def _company() -> tuple[Any, str, dict[str, Principal]]:
    domain = f"bn{next(_seq)}.demo"
    tid = await seed.create_tenant(f"Công ty bên nợ {domain}", domain, people=PEOPLE)
    return tid, domain, {k: await seed.who(tid, k, domain) for k, *_ in PEOPLE}


async def _issue(p: Principal, **inp: Any) -> dict[str, Any]:
    r = await executor.run(elevate(p), "acct.issue_receivable", {"source": "test.thu", "source_ref": uuid.uuid4().hex[:10], "label": "Khoản thử",
                                                                     "nature": "revenue", "amount_vnd": 1_000_000, "revenue_account": "revenue:services",
                                                                     "due_on": today().isoformat(), **inp})
    assert r.status == "ok", (r.status, r.error)
    return r.output


async def _ar(rid: Any) -> dict[str, Any]:
    async with db.owner.connection() as conn:
        got = await (await conn.execute("SELECT party, party_id FROM acct.receivables WHERE id = %s", (rid,))).fetchone()
    assert got is not None
    return dict(got)


async def _party(pid: Any) -> dict[str, Any]:
    async with db.owner.connection() as conn:
        got = await (await conn.execute("SELECT kind, display_name, tenant_id FROM parties.parties WHERE id = %s", (pid,))).fetchone()
    assert got is not None
    return dict(got)


async def _legacy(tid: Any, party: str) -> None:
    """Khoản phải thu cũ: chỉ có tên chữ, chưa có mã bên nợ (như 3.512 dòng trước migration)."""
    async with db.owner_tx() as c:
        await c.execute("""INSERT INTO acct.receivables (tenant_id, code, source, source_ref, label, party, nature, amount_vnd, fee_vnd, revenue_account, due_on)
                           VALUES (%s, %s, 'test.cu', %s, 'Khoản cũ', %s, 'revenue', 500000, 500000, 'revenue:services', current_date)""",
                        (tid, f"CU{uuid.uuid4().hex[:8].upper()}", uuid.uuid4().hex[:10], party))


async def test_a_name_only_issue_finds_or_creates_one_organization_and_derives_the_display_text() -> None:
    tid, _, w = await _company()
    a = await _ar((await _issue(w["ketoan"], party="  Công ty TNHH Đầu Tư Mẫu "))["receivable_id"])
    assert a["party_id"] is not None and a["party"] == "Công ty TNHH Đầu Tư Mẫu"                   # tên hiện đã cắt, từ định danh
    p = await _party(a["party_id"])
    assert (p["kind"], p["display_name"], p["tenant_id"]) == ("organization", "Công ty TNHH Đầu Tư Mẫu", tid)
    # viết khác hoa / dấu / dấu câu ⇒ cùng bên, tên hiện là tên của định danh (một nơi ghi)
    b = await _ar((await _issue(w["ketoan"], party="CONG TY tnhh dau-tu mau"))["receivable_id"])
    assert b["party_id"] == a["party_id"] and b["party"] == "Công ty TNHH Đầu Tư Mẫu"
    # gọi lại cùng (nguồn, tham chiếu) ⇒ khoản cũ, không tạo thêm bên
    n = await owner_val("SELECT count(*) FROM parties.parties WHERE tenant_id = %s AND kind = 'organization'", (tid,))
    again = await _issue(w["ketoan"], source_ref="gioi-han-1", party="Bên khác hẳn")
    await _issue(w["ketoan"], source_ref="gioi-han-1", party="Bên khác hẳn")
    assert again["created"] is True and await owner_val("SELECT count(*) FROM parties.parties WHERE tenant_id = %s AND kind = 'organization'", (tid,)) == n + 1
    # không tên, không mã ⇒ khoản không có bên nợ (như trước), không tạo bên rỗng
    c = await _ar((await _issue(w["ketoan"]))["receivable_id"])
    assert (c["party"], c["party_id"]) == ("", None)


async def test_a_party_id_wins_over_typed_text_and_two_owners_with_one_name_stay_apart() -> None:
    tid, _, w = await _company()
    one = (await ok(w["admin"], "parties.create", display_name="Trần Văn Bình", kind="person"))["party_id"]
    two = (await ok(w["admin"], "parties.create", display_name="Trần Văn Bình", kind="person"))["party_id"]
    a = await _ar((await _issue(w["ketoan"], party_id=one, party="gõ lung tung"))["receivable_id"])
    b = await _ar((await _issue(w["ketoan"], party_id=two))["receivable_id"])
    assert (a["party_id"], a["party"]) == (uuid.UUID(str(one)), "Trần Văn Bình") and b["party_id"] == uuid.UUID(str(two))   # tên hiện từ định danh
    assert a["party_id"] != b["party_id"]                                                              # cùng tên, khác người ⇒ không gộp
    r = await executor.run(elevate(w["ketoan"]), "acct.issue_receivable", {"source": "test.thu", "source_ref": "x1", "label": "x", "nature": "revenue",
                                                                              "amount_vnd": 1, "revenue_account": "revenue:services", "party_id": str(uuid.uuid4()),
                                                                              "due_on": today().isoformat()})
    assert r.status == "not_found"
    # mã của công ty khác không dùng được (RLS parties theo công ty)
    _, _, w2 = await _company()
    other = (await ok(w2["admin"], "parties.create", display_name="Bên công ty khác", kind="organization"))["party_id"]
    r2 = await executor.run(elevate(w["ketoan"]), "acct.issue_receivable", {"source": "test.thu", "source_ref": "x2", "label": "x", "nature": "revenue",
                                                                               "amount_vnd": 1, "revenue_account": "revenue:services", "party_id": str(other),
                                                                               "due_on": today().isoformat()})
    assert r2.status == "not_found"
    # cùng một tên ở hai công ty ⇒ hai bên riêng
    x = await _ar((await _issue(w["ketoan"], party="Chủ đầu tư dùng chung tên"))["receivable_id"])
    y = await _ar((await _issue(w2["ketoan"], party="Chủ đầu tư dùng chung tên"))["receivable_id"])
    assert x["party_id"] != y["party_id"] and (await _party(x["party_id"]))["tenant_id"] == tid


async def test_backfill_matches_names_creates_missing_parties_and_is_rerunnable() -> None:
    tid, _, w = await _company()
    existing = (await ok(w["admin"], "parties.create", display_name="Công ty CP Có Sẵn", kind="organization"))["party_id"]
    for name in ("Công ty CP Có Sẵn", "CONG TY CP CO SAN", "Công ty CP Chưa Có", "Công ty CP Chưa Có", "Công ty CP Chưa Có"):
        await _legacy(tid, name)
    await _legacy(tid, "")                                                                              # không tên ⇒ không có gì để gắn
    _, _, other = await _company()
    other_tid = other["admin"].tenant_id
    await _legacy(other_tid, "Công ty CP Chưa Có")                                                      # công ty khác, cùng tên
    out = await ok(system_principal(tid), "acct.link_receivable_parties")
    assert out == {"names": 3, "matched": 2, "created": 3, "parties_created": 1, "unmatched": 0, "left": 0}, out
    rows = {r["party"]: r for r in await _rows("SELECT party, party_id, count(*) OVER (PARTITION BY party) AS n FROM acct.receivables WHERE tenant_id = %s", tid)}
    assert rows["Công ty CP Có Sẵn"]["party_id"] == uuid.UUID(str(existing))                           # khớp tên chuẩn hoá vào bên đã có, không tạo trùng
    assert rows["Công ty CP Chưa Có"]["party_id"] is not None and rows[""]["party_id"] is None
    assert await owner_val("SELECT count(DISTINCT party_id) FROM acct.receivables WHERE tenant_id = %s AND party_id IS NOT NULL", (tid,)) == 2
    assert await owner_val("SELECT count(*) FROM acct.receivables WHERE tenant_id = %s AND party = 'CONG TY CP CO SAN'", (tid,)) == 0   # tên hiện = tên định danh
    assert (await _party(rows["Công ty CP Chưa Có"]["party_id"]))["kind"] == "organization"
    # chạy lại không đổi gì; công ty khác chưa bị đụng, rồi chạy riêng thì có bên RIÊNG của nó
    assert await ok(system_principal(tid), "acct.link_receivable_parties") == {"names": 0, "matched": 0, "created": 0, "parties_created": 0, "unmatched": 0, "left": 0}
    assert await owner_val("SELECT party_id FROM acct.receivables WHERE tenant_id = %s AND party <> ''", (other_tid,)) is None
    await ok(system_principal(other_tid), "acct.link_receivable_parties")
    theirs = await owner_val("SELECT party_id FROM acct.receivables WHERE tenant_id = %s AND party <> ''", (other_tid,))
    assert theirs != rows["Công ty CP Chưa Có"]["party_id"] and (await _party(theirs))["tenant_id"] == other_tid
    # vòng quét chọn đúng công ty còn dòng chưa gắn
    due = await _rows(executor_sweep_sql())
    assert tid not in {r["tenant_id"] for r in due}


def executor_sweep_sql() -> str:
    from barista.kernel import registry
    return registry.SWEEPS["acct.link_receivable_parties"]


async def _rows(sql: str, *params: Any) -> list[dict[str, Any]]:
    async with db.owner.connection() as conn:
        return [dict(r) for r in await (await conn.execute(sql, params)).fetchall()]   # type: ignore[arg-type]
