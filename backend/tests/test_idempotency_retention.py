"""0177 §2 — khoá chống lặp (``core.idempotency``) có CỬA SỔ phát lại là dữ liệu công ty: trong cửa sổ cùng khoá + cùng nội dung ⇒ kết quả cũ, khác nội dung ⇒ conflict;
quá cửa sổ vòng quét xoá khoá (theo lô, từng công ty), không chạm nhật ký thao tác và sổ cái. Đường nhập hàng loạt chỉ giữ TRẠNG THÁI trong bảng khoá (không giữ
kết quả) mà chống lặp vẫn đúng."""

import itertools
from typing import Any

from barista import seed
from barista.db import db
from barista.kernel import executor, idempotency_log, imports, registry
from barista.kernel.principal import Principal, system_principal
from tests.conftest import ok, owner_val, q

_seq = itertools.count(1)
PEOPLE = [("admin", "Quản trị", "admin", "QT", True), ("ketoan", "Kế toán", "accountant", "KT", True), ("sale", "Sale", "sale", "KD-A", False)]


async def _company() -> tuple[Any, dict[str, Principal]]:
    domain = f"kh{next(_seq)}.demo"
    tid = await seed.create_tenant(f"Công ty khoá {domain}", domain, people=PEOPLE)
    return tid, {k: await seed.who(tid, k, domain) for k, *_ in PEOPLE}


async def _keyed(p: Principal, key: str, name: str = "Bên thử") -> executor.Result:
    return await executor.run(p, "parties.create", {"display_name": name, "kind": "organization"}, key=key)


async def _age(tid: Any, key_like: str, days: int) -> int:
    async with db.owner_tx() as c:
        cur = await c.execute("UPDATE core.idempotency SET created_at = now() - make_interval(days => %s) WHERE tenant_id = %s AND key LIKE %s",
                              (days, tid, key_like))
        return cur.rowcount


async def _keys(tid: Any) -> int:
    return int(await owner_val("SELECT count(*) FROM core.idempotency WHERE tenant_id = %s", (tid,)))


async def test_purge_removes_only_keys_past_the_company_window_and_replay_semantics_hold_inside_it() -> None:
    tid, w = await _company()
    old, new = await _keyed(w["admin"], "k-old"), await _keyed(w["admin"], "k-new")
    assert old.status == new.status == "ok" and not old.replayed
    again = await _keyed(w["admin"], "k-new")                                                           # trong cửa sổ: cùng khoá + cùng nội dung ⇒ kết quả cũ
    assert again.replayed and again.output == new.output
    clash = await _keyed(w["admin"], "k-new", name="Nội dung khác")                                     # cùng khoá khác nội dung ⇒ xung đột
    assert clash.status == "conflict"
    journals = await owner_val("SELECT count(*) FROM ledger.journals WHERE tenant_id = %s", (tid,))
    audit = await owner_val("SELECT count(*) FROM core.audit_log WHERE tenant_id = %s", (tid,))
    assert await _age(tid, "%:k-old", 100) == 1
    page = await q(w["admin"], "core.idempotency_retention")
    assert page["rows"][0]["keep"] == "90 ngày" and page["rows"][0]["due"] == 1
    assert await ok(system_principal(tid), "core.purge_idempotency") == {"keys": 1}
    assert await ok(system_principal(tid), "core.purge_idempotency") == {"keys": 0}
    assert await _keys(tid) == 1                                                                         # khoá còn trong cửa sổ giữ nguyên
    assert (await _keyed(w["admin"], "k-new")).replayed                                                  # vẫn phát lại được
    re_old = await _keyed(w["admin"], "k-old")                                                           # quá cửa sổ: coi như khoá mới (chạy lại)
    assert re_old.status == "ok" and not re_old.replayed and re_old.output != old.output
    # nhật ký thao tác không bị xoá, sổ cái không đụng
    assert await owner_val("SELECT count(*) FROM core.audit_log WHERE tenant_id = %s", (tid,)) >= audit
    assert await owner_val("SELECT count(*) FROM ledger.journals WHERE tenant_id = %s", (tid,)) == journals


async def test_window_is_company_data_bounded_forever_allowed_people_only() -> None:
    tid, w = await _company()
    await _keyed(w["admin"], "k-1")
    await _age(tid, "%:k-1", 30)
    assert await ok(system_principal(tid), "core.purge_idempotency") == {"keys": 0}                      # mặc định 90 ngày
    await ok(w["admin"], "core.set_idempotency_retention", keep_days=7)
    assert await ok(system_principal(tid), "core.purge_idempotency") == {"keys": 1}
    assert (await executor.run(w["admin"], "core.set_idempotency_retention", {"keep_days": 6})).status == "invalid"   # không ngắn hơn một tuần
    await ok(w["admin"], "core.set_idempotency_retention", keep_forever=True)
    await _keyed(w["admin"], "k-2")
    await _age(tid, "%:k-2", 4000)
    assert await ok(system_principal(tid), "core.purge_idempotency") == {"keys": 0}
    assert (await q(w["admin"], "core.idempotency_retention"))["rows"][0]["keep"] == "Giữ mãi"
    assert not await _due(tid)                                                                           # giữ mãi ⇒ vòng quét không chọn công ty
    assert (await executor.run(w["sale"], "core.set_idempotency_retention", {"keep_days": 30})).status == "denied"
    assert (await executor.run(w["admin"].as_agent(), "core.set_idempotency_retention", {"keep_days": 30})).status != "ok"
    assert (await executor.run(w["sale"], "core.idempotency_retention", {})).status in ("denied", "not_found")


async def _due(tid: Any) -> bool:
    async with db.owner.connection() as conn:
        rows = await (await conn.execute(registry.SWEEPS["core.purge_idempotency"])).fetchall()
    return tid in {r["tenant_id"] for r in rows}


async def test_purge_is_batched_and_never_crosses_companies(monkeypatch: Any) -> None:
    tid, w = await _company()
    other, w2 = await _company()
    for i in range(5):
        await _keyed(w["admin"], f"b-{i}")
    await _keyed(w2["admin"], "b-x")
    await _age(tid, "%:b-%", 120)
    await _age(other, "%:b-%", 120)
    monkeypatch.setattr(idempotency_log, "PURGE_ROWS", 2)
    assert await _due(tid) and await _due(other)
    assert [(await ok(system_principal(tid), "core.purge_idempotency"))["keys"] for _ in range(4)] == [2, 2, 1, 0]
    assert await _keys(other) == 1 and await _due(other) and not await _due(tid)                         # công ty khác chưa bị đụng


async def test_import_rows_keep_only_the_status_yet_replay_and_conflict_still_work() -> None:
    tid, w = await _company()
    rows = [{"ledger_prefix": "wallet", "misa_account": "334", "label": "Ví thử một"}, {"ledger_prefix": "deposit", "misa_account": "338", "label": "Cọc thử"}]
    first = await imports.run_import(w["ketoan"], "acct.set_account_map", rows, dry_run=False)
    again = await imports.run_import(w["ketoan"], "acct.set_account_map", rows, dry_run=False)           # nhập lại cùng tệp
    assert first.output["ok"] == again.output["ok"] == 2
    assert [r["replayed"] for r in first.output["rows"]] == [False, False] and [r["replayed"] for r in again.output["rows"]] == [True, True]
    outs = [r["output"] for r in await _idem(tid, "%:import:%")]
    assert len(outs) == 2 and outs == [{}, {}]                                                           # trạng thái thôi, không giữ kết quả từng dòng
    # đường thường vẫn giữ kết quả; cùng khoá khác nội dung vẫn xung đột ở đường nhập
    await _keyed(w["admin"], "k-full")
    assert (await _idem(tid, "%:k-full"))[0]["output"].get("party_id")
    key = imports._row_key("acct.set_account_map", {"ledger_prefix": "wallet", "misa_account": "334", "label": "Ví thử một"})
    r = await executor.run(w["ketoan"], "acct.set_account_map", {"ledger_prefix": "wallet", "misa_account": "999", "label": "Khác"}, key=key, keep_output=False)
    assert r.status == "conflict"


async def _idem(tid: Any, like: str) -> list[dict[str, Any]]:
    async with db.owner.connection() as conn:
        return [dict(r) for r in await (await conn.execute("SELECT key, output FROM core.idempotency WHERE tenant_id = %s AND key LIKE %s ORDER BY key",
                                                           (tid, like))).fetchall()]
