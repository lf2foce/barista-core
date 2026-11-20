"""0119: ngày hạch toán trên bút toán, khoá kỳ theo pháp nhân (chặn ở DB), số dư đầu kỳ theo lô (pháp nhân, miền).

Khoá kỳ đổi luật ghi của cả pháp nhân ⇒ mọi test ở đây chạy trên MỘT CÔNG TY RIÊNG (không khoá pháp nhân của công ty mẫu mà test khác dùng chung),
mỗi test một pháp nhân mới khi cần khoá."""

import ast
import asyncio
import dataclasses
from datetime import date, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
import pytest_asyncio

from barista import seed
from barista.db import db
from barista.kernel import executor, imports, ledger, registry
from barista.kernel.clock import today
from barista.kernel.errors import Invalid
from barista.kernel.executor import core_ctx
from barista.kernel.principal import Principal, elevate
from tests.conftest import approved, edge, ok, owner_val, q
from tests.module_src import module_files, rel

DOMAIN = "sodu.demo"
PEOPLE = [("giamdoc", "Giám đốc thử", "director", "HQ", True), ("admin", "Quản trị thử", "admin", "QT", True),
          ("ketoan", "Kế toán thử", "accountant", "KT", True)]
_seq = iter(range(1, 10_000))
# Hai MIỀN nguồn khoản phải thu ngoài Hoa hồng: ở bản cài là gói ngoài lõi khai consumes("receivable") (vd cho thuê, dịch vụ kế toán) — ở đây giả
# bằng hai module của lõi không có nguồn riêng (``training``, ``hr``) để test chạy được khi chỉ có lõi.
SRC_A, SRC_B = "training", "hr"


@pytest.fixture(autouse=True)
def _receivable_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "FAMILY_CALLERS", {**registry.FAMILY_CALLERS, "receivable": {*registry.FAMILY_CALLERS["receivable"], SRC_A, SRC_B}})


@pytest_asyncio.fixture(scope="module")
async def co(demo: dict[str, Any]) -> dict[str, Any]:
    tid = await seed.create_tenant("Công ty thử đầu kỳ", DOMAIN, people=PEOPLE)
    return {"tid": tid}


async def who(co: dict[str, Any], local: str) -> Principal:
    return await seed.who(co["tid"], local, DOMAIN)


async def entity(co: dict[str, Any]) -> tuple[str, UUID]:
    """Pháp nhân mới của công ty thử — khoá kỳ của test này không chạm test khác."""
    code = f"E{next(_seq):03d}"
    out = await ok(await who(co, "admin"), "admin.upsert_entity", code=code, name=f"Pháp nhân thử {code}")
    return code, out["entity_id"]


async def post(p: Principal, *, key: str | None = None, on: date, entity: UUID | None = None, amount: int = 1_000,
               lines: list[tuple[str, int]] | None = None) -> UUID:
    async with db.tenant_tx(p) as conn:
        return await ledger.post(core_ctx(conn, p), key=key or uuid4().hex, memo="thử", source="test", on=on, entity=entity,
                                 lines=lines or [("cash:bank", amount), ("revenue:services", -amount)])


async def posted_on(key: str) -> date:
    return await owner_val("SELECT posted_on FROM ledger.journals WHERE idempotency_key = %s", (key,))


def last_month_end() -> date:
    return today().replace(day=1) - timedelta(days=1)


# ── 1. Hai mốc thời gian ──

def test_every_ledger_call_in_modules_passes_a_posting_date() -> None:
    """Quét mã: chỗ ghi tiền mới không quên chọn ngày (không có mặc định ở post; reverse cũng khai rõ)."""
    missing, seen = [], 0
    for f in (f for files in module_files().values() for f in files):   # module của lõi + của gói ngoài đang cài (0191)
        for node in ast.walk(ast.parse(f.read_text())):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("post", "reverse")
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "ledger"):
                seen += 1
                if not any(k.arg == "on" for k in node.keywords):
                    missing.append(f"{rel(f)}:{node.lineno}")
    assert seen >= 15 and not missing, missing


async def test_post_needs_a_date_and_refuses_the_future(co: dict[str, Any]) -> None:
    kt = await who(co, "ketoan")
    async with db.tenant_tx(kt) as conn:
        with pytest.raises(TypeError):
            await ledger.post(core_ctx(conn, kt), key=uuid4().hex, memo="x", source="test", lines=[("cash:bank", 1), ("revenue:services", -1)])  # type: ignore[call-arg]
    with pytest.raises(Invalid, match="sau hôm nay"):
        await post(kt, on=today() + timedelta(days=1))


async def test_money_received_last_month_is_booked_in_last_month(co: dict[str, Any]) -> None:
    """Tiền về ngày cuối tháng trước, kế toán ghi hôm nay ⇒ xuất MISA và tập sổ cái xếp vào tháng trước (lúc ghi `at` vẫn là hôm nay)."""
    kt = await who(co, "ketoan")
    day = last_month_end()
    async with db.tenant_tx(kt) as conn:
        ar = await core_ctx(conn, kt, module=SRC_A).call_system(
            "acct.issue_receivable", source=f"{SRC_A}.fee", source_ref=uuid4().hex, label="Phí tháng thử", party="Khách thử", nature="revenue",
            amount_vnd=5_000_000, due_on=day, issued_on=day - timedelta(days=3))
    ref = f"MISA-{uuid4().hex[:8]}"
    await ok(kt, "acct.record_payment", code=ar["code"], amount_vnd=5_000_000, received_on=day.isoformat(), bank_ref=ref)
    days = await owner_val("""SELECT array_agg(DISTINCT j.posted_on) FROM ledger.journals j WHERE j.tenant_id = %s
                              AND (j.source_ref = %s OR j.source_ref = %s)""", (kt.tenant_id, ref, ar["code"]))
    assert set(days) == {day, day - timedelta(days=3)}
    misa = await q(kt, "acct.misa_export", date_from=day.replace(day=1).isoformat(), date_to=day.isoformat())
    mine = [r for r in misa["rows"] if r["ref"] in (ref, ar["code"])]
    assert len(mine) == 3 and all(str(r["day"]).startswith(f"{day:%Y-%m}") for r in mine)      # phải thu, tiền về, phân bổ
    assert not [r for r in (await q(kt, "acct.misa_export", month=f"{today():%m/%Y}"))["rows"] if r["ref"] == ref]   # tháng này không có
    led = await q(kt, "core.ds_ledger", measures=["debit"], by=["month"], date="day", date_from=day.replace(day=1).isoformat(),
                  date_before=(day + timedelta(days=1)).isoformat())
    assert {r["month"] for r in led["rows"]} == {f"{day:%Y-%m}"}


async def test_same_key_with_another_date_returns_the_old_journal_unchanged(co: dict[str, Any]) -> None:
    kt = await who(co, "ketoan")
    key = uuid4().hex
    a = await post(kt, key=key, on=today() - timedelta(days=5))
    b = await post(kt, key=key, on=today())
    assert a == b and await posted_on(key) == today() - timedelta(days=5)


# ── 2. Khoá kỳ ──

async def test_lock_is_enforced_by_the_database_even_for_a_raw_owner_insert(co: dict[str, Any]) -> None:
    kt = await who(co, "ketoan")
    code, eid = await entity(co)
    d = today() - timedelta(days=10)
    await ok(kt, "acct.lock_period", entity_code=code, locked_through=d.isoformat())
    with pytest.raises(Invalid, match="Kỳ đã khoá tới"):
        await post(kt, on=d, entity=eid)
    await post(kt, on=d + timedelta(days=1), entity=eid)
    with pytest.raises(psycopg.errors.RaiseException, match="Kỳ đã khoá tới"):
        async with db.owner_tx() as conn:
            await conn.execute("""INSERT INTO ledger.journals (tenant_id, memo, source, idempotency_key, entity_id, posted_on)
                                  VALUES (%s, 'thẳng', 'test', %s, %s, %s)""", (kt.tenant_id, uuid4().hex, eid, d - timedelta(days=3)))


async def test_lock_of_one_entity_does_not_block_another(co: dict[str, Any]) -> None:
    kt = await who(co, "ketoan")
    a, ea = await entity(co)
    _, eb = await entity(co)
    d = today() - timedelta(days=4)
    await ok(kt, "acct.lock_period", entity_code=a, locked_through=d.isoformat())
    await post(kt, on=d, entity=eb)
    with pytest.raises(Invalid):
        await post(kt, on=d, entity=ea)


async def test_lock_trigger_runs_after_the_default_entity_is_filled(co: dict[str, Any]) -> None:
    """Thứ tự trigger: bút toán không chọn pháp nhân vẫn bị chặn theo mốc của pháp nhân mặc định (khoá không lấy theo NULL)."""
    names = await owner_val("""SELECT array_agg(tgname::text ORDER BY tgname) FROM pg_trigger WHERE tgrelid = 'ledger.journals'::regclass
                               AND NOT tgisinternal AND tgname IN ('t_journals_entity', 't_journals_period_lock')""")
    assert names == ["t_journals_entity", "t_journals_period_lock"]
    kt, admin = await who(co, "ketoan"), await who(co, "admin")
    code, eid = await entity(co)
    old = await owner_val("SELECT code FROM core.legal_entities WHERE tenant_id = %s AND is_default", (kt.tenant_id,))
    await ok(admin, "admin.upsert_entity", code=code, name=f"Pháp nhân thử {code}", is_default=True)
    try:
        await ok(kt, "acct.lock_period", entity_code=code, locked_through=(today() - timedelta(days=1)).isoformat())
        with pytest.raises(Invalid, match="Kỳ đã khoá"):
            await post(kt, on=today() - timedelta(days=1))
        assert await owner_val("SELECT entity_id FROM ledger.journals WHERE idempotency_key = %s", (await _key_of(await post(kt, on=today())),)) == UUID(str(eid))
    finally:
        await ok(admin, "admin.upsert_entity", code=old, name=await owner_val("SELECT name FROM core.legal_entities WHERE tenant_id = %s AND code = %s",
                                                                                (kt.tenant_id, old)), is_default=True)


async def _key_of(jid: UUID) -> str:
    return await owner_val("SELECT idempotency_key FROM ledger.journals WHERE id = %s", (jid,))


async def test_lock_only_moves_forward_and_not_past_today(co: dict[str, Any]) -> None:
    kt = await who(co, "ketoan")
    code, _ = await entity(co)
    await ok(kt, "acct.lock_period", entity_code=code, locked_through=(today() - timedelta(days=3)).isoformat())
    for d in (today() - timedelta(days=5), today() - timedelta(days=3), today() + timedelta(days=1)):
        r = await executor.run(kt, "acct.lock_period", {"entity_code": code, "locked_through": d.isoformat()})
        assert r.status == "invalid", d
    assert (await executor.run(kt.as_agent(), "acct.lock_period", {"entity_code": code, "locked_through": today().isoformat()})).status == "denied"


async def test_reopen_needs_another_persons_approval_and_the_assistant_cannot_ask(co: dict[str, Any]) -> None:
    kt, gd = await who(co, "ketoan"), await who(co, "giamdoc")
    code, eid = await entity(co)
    d = today() - timedelta(days=6)
    await ok(kt, "acct.lock_period", entity_code=code, locked_through=d.isoformat())
    agent = await executor.run(kt.as_agent(), "acct.reopen_period", {"entity_code": code, "reason": "thử", "locked_through": None})
    assert agent.status == "denied"
    assert (await executor.run(kt, "acct.reopen_period", {"entity_code": code, "reason": " "})).status == "invalid"
    r = await executor.run(kt, "acct.reopen_period", {"entity_code": code, "locked_through": (d - timedelta(days=3)).isoformat(),
                                                      "reason": "Sửa số tiền gửi nhập sai"})
    assert r.status == "pending_approval"
    aid = UUID(str(r.approval_id))
    approvers = await owner_val("SELECT approver_ids FROM core.approvals WHERE id = %s", (aid,))
    assert kt.user_id not in approvers and gd.user_id in approvers                     # người xin không tự duyệt
    assert (await executor.run(edge(kt), "core.decide", {"approval_id": str(aid), "approve": True})).status in ("not_found", "denied", "invalid")
    with pytest.raises(Invalid):
        await post(kt, on=d, entity=eid)                                                 # chưa duyệt: còn khoá
    done = await ok(edge(gd), "core.decide", approval_id=str(aid), approve=True)
    assert done["status"] == "executed", done
    await post(kt, on=d, entity=eid)
    row = await owner_val("SELECT approved_by FROM ledger.period_locks WHERE entity_id = %s ORDER BY id DESC LIMIT 1", (eid,))
    assert row == [gd.user_id]


async def test_a_running_post_holds_the_lock_back_and_nothing_slips_under_the_new_mark(co: dict[str, Any]) -> None:
    kt = await who(co, "ketoan")
    code, eid = await entity(co)
    d = today() - timedelta(days=2)
    async with db.tenant_tx(kt) as conn:
        await ledger.post(core_ctx(conn, kt), key=uuid4().hex, memo="đang ghi", source="test", on=d, entity=eid,
                          lines=[("cash:bank", 1), ("revenue:services", -1)])
        locking = asyncio.create_task(executor.run(kt, "acct.lock_period", {"entity_code": code, "locked_through": d.isoformat()}))
        await asyncio.sleep(0.5)
        assert not locking.done()                                                        # mốc chờ bút toán đang ghi dở
    assert (await locking).status == "ok"
    with pytest.raises(Invalid, match="Kỳ đã khoá"):
        await post(kt, on=d, entity=eid)


async def test_reversing_a_journal_of_a_locked_period_lands_today(co: dict[str, Any]) -> None:
    kt = await who(co, "ketoan")
    code, eid = await entity(co)
    key = uuid4().hex
    await post(kt, key=key, on=today() - timedelta(days=8), entity=eid)
    await ok(kt, "acct.lock_period", entity_code=code, locked_through=(today() - timedelta(days=1)).isoformat())
    async with db.tenant_tx(kt) as conn:
        await ledger.reverse(core_ctx(conn, kt), original_key=key, key=key + "-rev", memo="đảo", source="test")
        with pytest.raises(Invalid):
            await ledger.reverse(core_ctx(conn, kt), original_key=key, key=key + "-rev2", memo="đảo", source="test", on=today() - timedelta(days=9))
    assert await posted_on(key + "-rev") == today()


# ── 3. Số dư đầu kỳ ──

async def stage(p: Principal, batch: str, **line: Any) -> dict[str, Any]:
    return await ok(p, "acct.add_opening_line", batch=batch, **line)


async def open_(p: Principal, batch: str, code: str, cut: date, **kw: Any) -> executor.Result:
    return await executor.run(p, "acct.open_balances", {"batch": batch, "entity_code": code, "cut_on": cut.isoformat(),
                                                         "completeness": kw.pop("completeness", "partial"), **kw})


async def party(co: dict[str, Any]) -> UUID:
    return (await ok(await who(co, "admin"), "parties.create", display_name=f"Bên thử {uuid4().hex[:6]}", kind="organization"))["party_id"]


async def test_opening_batch_from_excel_with_dry_run_control_totals_and_lock(co: dict[str, Any]) -> None:
    kt = await who(co, "ketoan")
    code, eid = await entity(co)
    cut, batch = today() - timedelta(days=20), f"DK{next(_seq)}"
    rows = [{"Mã lô": batch, "Loại dòng": "Số dư tài khoản", "Tài khoản sổ cái": "cash:bank", "Diễn giải": "Tiền gửi", "Dư Nợ": "300tr"},
            {"Mã lô": batch, "Loại dòng": "Phải thu còn mở", "Miền sở hữu": SRC_A, "Bên trả": "Khách cũ", "Diễn giải": "Phí tháng 9",
             "Số chứng từ gốc": f"HDCU{next(_seq)}", "Dư Nợ": "20tr", "Ngày phát sinh": (cut - timedelta(days=40)).strftime("%d/%m/%Y"),
             "Hạn": (cut - timedelta(days=10)).strftime("%d/%m/%Y")}]
    first = await imports.run_import(kt, "acct.add_opening_line", rows, dry_run=False)
    again = await imports.run_import(kt, "acct.add_opening_line", rows, dry_run=False)   # nhập lại cùng tệp
    assert first.output["ok"] == 2 and again.output["ok"] == 2
    assert await owner_val("SELECT count(*) FROM acct.opening_lines WHERE batch = %s", (batch,)) == 2
    # lô đủ: lệch tổng kiểm ⇒ không ghi gì
    bad = await open_(kt, batch, code, cut, completeness="full", control_debit_vnd=320_000_000, control_credit_vnd=1)
    assert bad.status == "invalid" and "tổng kiểm" in (bad.error or "")
    assert (await open_(kt, batch, code, cut, completeness="full")).status == "invalid"          # đủ mà không khai tổng kiểm
    dry = await open_(kt, batch, code, cut, completeness="full", control_equity_vnd=320_000_000, dry_run=True)
    assert dry.status == "ok" and dry.output["dry_run"] and dry.output["equity_vnd"] == 320_000_000
    assert await owner_val("SELECT count(*) FROM acct.opening_batches WHERE code = %s", (batch,)) == 0
    assert await ledger_lock(kt, eid) is None
    out = await open_(kt, batch, code, cut, completeness="full", control_debit_vnd=320_000_000, control_credit_vnd=0)
    assert out.status == "ok", out.error
    assert out.output["locked_through"] == cut.isoformat()                                           # mặc định khoá tới D
    assert (await open_(kt, batch, code, cut, completeness="full", control_equity_vnd=320_000_000)).output["replayed"]
    j = await owner_val("""SELECT array_agg(DISTINCT j.source || '@' || j.posted_on) FROM ledger.journals j WHERE j.entity_id = %s""", (eid,))
    assert j == [f"acct.opening_batch@{cut}"]
    ar = await owner_val("SELECT source FROM acct.receivables WHERE entity_id = %s", (eid,))
    assert ar == f"{SRC_A}.opening"
    assert await owner_val("SELECT party_id IS NOT NULL AND party = 'Khách cũ' FROM acct.receivables WHERE entity_id = %s", (eid,))   # 0177: bên nợ là định danh
    with pytest.raises(Invalid, match="Kỳ đã khoá"):
        await post(kt, on=cut, entity=eid)                                               # ghi lùi ≤ D bị chặn


async def ledger_lock(p: Principal, eid: UUID) -> date | None:
    async with db.tenant_tx(p) as conn:
        return await ledger.locked_through(core_ctx(conn, p), eid)


async def test_opening_line_rules(co: dict[str, Any]) -> None:
    kt = await who(co, "ketoan")
    b = f"DK{next(_seq)}"
    issued = (today() - timedelta(days=30)).isoformat()
    cases = [
        {"kind": "receivable", "label": "thiếu miền", "debit_vnd": 1000, "issued_on": issued, "due_on": issued, "party": "A"},
        {"kind": "receivable", "domain": "acct", "label": "miền acct", "debit_vnd": 1000, "issued_on": issued, "due_on": issued, "party": "A"},
        {"kind": "receivable", "domain": "kpi", "label": "miền ngoài danh sách", "debit_vnd": 1000, "issued_on": issued, "due_on": issued},
        {"kind": "account", "account": "cash:bank", "label": "USD", "debit_vnd": 1000, "currency": "USD"},
        {"kind": "account", "account": "receivable:customers", "label": "tài khoản có sổ phụ", "debit_vnd": 1000},
        {"kind": "account", "account": "cash:bank", "label": "hai bên", "debit_vnd": 1000, "credit_vnd": 1000},
        {"kind": "payable", "domain": "pay", "label": "thiếu bên", "credit_vnd": 1000, "issued_on": issued},
    ]
    for c in cases:
        r = await executor.run(kt, "acct.add_opening_line", {"batch": b, **c})
        assert r.status == "invalid", (c["label"], r.status, r.error)


async def test_account_fence_and_domain_fence(co: dict[str, Any]) -> None:
    kt = await who(co, "ketoan")
    code, eid = await entity(co)
    cut = today() - timedelta(days=15)
    await post(kt, on=cut - timedelta(days=2), entity=eid)                              # cash:bank đã có bút toán trước D
    b1 = f"DK{next(_seq)}"
    await stage(kt, b1, kind="account", account="cash:bank", label="Tiền gửi", debit_vnd=5_000)
    r = await open_(kt, b1, code, cut)
    assert r.status == "invalid" and "cash:bank" in (r.error or "")
    # miền đã mang lịch sử (khoản phải thu của miền ngày ≤ D) ⇒ không ghi đầu kỳ cho miền đó
    async with db.tenant_tx(kt) as conn:
        await core_ctx(conn, kt, module=SRC_A).call_system(
            "acct.issue_receivable", source=f"{SRC_A}.fee", source_ref=uuid4().hex, label="Có lịch sử", party="X", nature="revenue",
            amount_vnd=1_000, due_on=cut, issued_on=cut - timedelta(days=1), entity_id=eid)
    b2 = f"DK{next(_seq)}"
    await stage(kt, b2, kind="receivable", domain=SRC_A, label="Khoản cũ", party="Y", debit_vnd=7_000,
                issued_on=(cut - timedelta(days=9)).isoformat(), due_on=cut.isoformat())
    r = await open_(kt, b2, code, cut)
    assert r.status == "invalid" and "lịch sử" in (r.error or "")
    assert await owner_val("SELECT count(*) FROM acct.opening_batches WHERE entity_id = %s", (eid,)) == 0


async def test_two_domains_share_a_control_account_at_two_dates_then_history_before_the_mark_needs_reopen(co: dict[str, Any]) -> None:
    kt, gd = await who(co, "ketoan"), await who(co, "giamdoc")
    code, eid = await entity(co)
    d1, d2 = today() - timedelta(days=40), today() - timedelta(days=25)
    b1, b2 = f"DK{next(_seq)}", f"DK{next(_seq)}"
    await stage(kt, b1, kind="receivable", domain=SRC_A, label="Phí cũ", party="P", debit_vnd=10_000,
                issued_on=(d1 - timedelta(days=5)).isoformat(), due_on=d1.isoformat())
    r = await open_(kt, b1, code, d1, more_history_later=True)
    assert r.status == "ok" and r.output["locked_through"] is None                       # còn miền nạp sau ⇒ mặc định không khoá
    await stage(kt, b2, kind="receivable", domain=SRC_B, label="Phí quản lý cũ", party="H", debit_vnd=20_000,
                issued_on=(d2 - timedelta(days=5)).isoformat(), due_on=d2.isoformat())
    r = await open_(kt, b2, code, d2)
    assert r.status == "ok" and r.output["locked_through"] == d2.isoformat()                         # cùng receivable:customers, cùng pháp nhân, hai D
    # cùng (pháp nhân, miền) lần hai bị từ chối; lô có D ≤ mốc bị từ chối
    b3 = f"DK{next(_seq)}"
    await stage(kt, b3, kind="receivable", domain=SRC_A, label="Lần hai", party="P", debit_vnd=1, issued_on=d1.isoformat(),
                due_on=d1.isoformat())
    assert (await open_(kt, b3, code, today())).status == "invalid"
    assert (await open_(kt, b3, code, d2 - timedelta(days=1))).status == "invalid"
    # nạp lịch sử miền thứ ba có ngày gốc trước mốc: trigger chặn ⇒ mở lại kỳ có duyệt rồi mới ghi được
    with pytest.raises(Invalid, match="Kỳ đã khoá"):
        await post(kt, on=d1 + timedelta(days=1), entity=eid)
    await approved(kt, "acct.reopen_period", entity_code=code, locked_through=d1.isoformat(), reason="Nạp lịch sử miền thứ ba")
    await post(kt, on=d1 + timedelta(days=1), entity=eid)
    assert gd.user_id in await owner_val("SELECT approved_by FROM ledger.period_locks WHERE entity_id = %s ORDER BY id DESC LIMIT 1", (eid,))


async def test_correction_is_reopen_reverse_repost_lock(co: dict[str, Any]) -> None:
    kt = await who(co, "ketoan")
    code, eid = await entity(co)
    cut = today() - timedelta(days=12)
    b = f"DK{next(_seq)}"
    await stage(kt, b, kind="account", account="cash:bank", label="Tiền gửi (sai)", debit_vnd=9_000)
    assert (await open_(kt, b, code, cut)).status == "ok"
    r = await executor.run(kt, "acct.reverse_opening_batch", {"batch": b, "reason": "nhập sai"})
    assert r.status == "invalid" and "mở lại kỳ" in (r.error or "")                    # kỳ đã khoá tới D
    await approved(kt, "acct.reopen_period", entity_code=code, locked_through=None, reason="Sửa đầu kỳ")
    await ok(kt, "acct.reverse_opening_batch", batch=b, reason="nhập sai")
    await stage(kt, b, kind="account", account="cash:bank", label="Tiền gửi (đúng)", debit_vnd=8_000)
    assert (await open_(kt, b, code, cut)).status == "ok"
    bal = await owner_val("""SELECT sum(p.amount_vnd) FROM ledger.postings p JOIN ledger.journals j ON j.id = p.journal_id
                             JOIN ledger.accounts a ON a.id = p.account_id WHERE j.entity_id = %s AND a.code = 'cash:bank'""", (eid,))
    assert bal == 8_000 and await ledger_lock(kt, eid) == cut
    assert await owner_val("SELECT array_agg(status ORDER BY created_at) FROM acct.opening_batches WHERE entity_id = %s", (eid,)) == [
        "reversed", "posted"]


async def test_opening_payable_of_one_domain_is_not_settled_by_another_domain(co: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Khoản phải trả đầu kỳ của miền A không bị lệnh chi của miền B (cùng một người) cấn trừ — nguồn `<miền>.opening`, không phải `acct.opening`."""
    for name in ("acct.recognize_payable", "acct.prepare_payout"):
        op = registry.OPS[name]
        monkeypatch.setitem(registry.OPS, name, dataclasses.replace(op, callers=(*op.callers, SRC_A, SRC_B)))
    kt, gd = await who(co, "ketoan"), await who(co, "giamdoc")
    code, eid = await entity(co)
    pid = await party(co)
    b = f"DK{next(_seq)}"
    cut = today() - timedelta(days=3)
    await stage(kt, b, kind="payable", domain=SRC_B, party_id=str(pid), label="Nợ nhà cung cấp cũ", credit_vnd=4_000_000,
                issued_on=(cut - timedelta(days=20)).isoformat())
    assert (await open_(kt, b, code, cut, lock_after=False)).status == "ok"
    src = await owner_val("SELECT source FROM acct.payables WHERE party_id = %s", (pid,))
    assert src == f"{SRC_B}.opening"
    assert gd.user_id is not None
    payer = elevate((await who(co, "admin")), approved_by=(gd.user_id,))
    with pytest.raises(Invalid, match="không đủ"):
        async with db.tenant_tx(payer) as conn:
            await core_ctx(conn, payer, module=SRC_A).call_system(
                "acct.prepare_payout", source=f"{SRC_A}.payment", source_ref=uuid4().hex, party_id=pid, entity_id=eid, amount_vnd=1_000_000,
                label="Chi của miền khác")
    async with db.tenant_tx(payer) as conn:
        out = await core_ctx(conn, payer, module=SRC_B).call_system(
            "acct.prepare_payout", source=f"{SRC_B}.payment", source_ref=uuid4().hex, party_id=pid, entity_id=eid, amount_vnd=1_000_000,
            label="Trả nhà cung cấp")
    assert out["status"] == "pending_transfer"


# ── 4. Sửa sau review độc lập (08/11/2026) ──

async def test_balanced_postings_added_to_an_old_journal_of_a_locked_period_are_refused(co: dict[str, Any]) -> None:
    kt = await who(co, "ketoan")
    code, eid = await entity(co)
    d = today() - timedelta(days=9)
    jid = await post(kt, on=d, entity=eid)
    await ok(kt, "acct.lock_period", entity_code=code, locked_through=d.isoformat())
    with pytest.raises(psycopg.errors.RaiseException, match="Kỳ đã khoá"):
        async with db.owner_tx() as conn:
            for amount, acc in ((777, "cash:bank"), (-777, "revenue:services")):
                await conn.execute("""INSERT INTO ledger.postings (tenant_id, journal_id, account_id, amount_vnd)
                                      SELECT %s, %s, id, %s FROM ledger.accounts WHERE tenant_id = %s AND code = %s""",
                                   (kt.tenant_id, jid, amount, kt.tenant_id, acc))


async def test_the_app_role_cannot_move_a_lock_backwards_without_an_approved_reopen(co: dict[str, Any]) -> None:
    kt = await who(co, "ketoan")
    code, eid = await entity(co)
    d = today() - timedelta(days=5)
    await ok(kt, "acct.lock_period", entity_code=code, locked_through=d.isoformat())
    for back in (None, d - timedelta(days=1)):
        with pytest.raises(psycopg.errors.RaiseException, match="mở lại kỳ"):
            async with db.tenant_tx(kt) as conn:
                await conn.execute("INSERT INTO ledger.period_locks (entity_id, locked_through, reason, approved_by) VALUES (%s, %s, 'lách', %s)",
                                   (eid, back, [kt.user_id]))
        with pytest.raises(psycopg.errors.RaiseException):
            async with db.tenant_tx(kt) as conn:
                await conn.execute("SELECT ledger.reopen_period_lock(%s, %s, 'lách', %s, %s, 'acct.apply_reopen_period', 'period_reopen')",
                                   (eid, back, kt.user_id, [(await who(co, "giamdoc")).user_id]))
    assert await ledger_lock(kt, eid) == d


async def test_activity_before_the_cut_still_fences_an_account_when_reversed_after_the_cut(co: dict[str, Any]) -> None:
    kt = await who(co, "ketoan")
    code, eid = await entity(co)
    cut = today() - timedelta(days=7)
    key = uuid4().hex
    await post(kt, key=key, on=cut - timedelta(days=2), entity=eid)
    async with db.tenant_tx(kt) as conn:
        await ledger.reverse(core_ctx(conn, kt), original_key=key, key=key + "-rev", memo="đảo sau D", source="test", on=today())
    b = f"DK{next(_seq)}"
    await stage(kt, b, kind="account", account="cash:bank", label="Tiền gửi", debit_vnd=1_000)
    r = await open_(kt, b, code, cut)
    assert r.status == "invalid" and "cash:bank" in (r.error or "")


async def test_voiding_an_opening_receivable_adjusts_today_and_keeps_the_opening_at_the_cut(co: dict[str, Any]) -> None:
    kt = await who(co, "ketoan")
    code, eid = await entity(co)
    cut = today() - timedelta(days=6)
    b = f"DK{next(_seq)}"
    await stage(kt, b, kind="receivable", domain=SRC_B, label="Phí cũ", party="H", debit_vnd=3_000,
                issued_on=(cut - timedelta(days=30)).isoformat(), due_on=(cut + timedelta(days=30)).isoformat())
    out = await open_(kt, b, code, cut, lock_after=False)
    assert out.status == "ok", out.error
    async with db.tenant_tx(kt) as conn:
        got = await core_ctx(conn, kt, module=SRC_B).call_system("acct.void_receivables", source=f"{SRC_B}.opening", ref_prefix=str(out.output["batch_id"]),
                                                               due_after=cut.isoformat())
    assert len(got["voided"]) == 1
    rows = await owner_val("""SELECT array_agg(a.code || '@' || j.posted_on || ':' || p.amount_vnd ORDER BY j.posted_on, a.code) FROM ledger.postings p
                              JOIN ledger.journals j ON j.id = p.journal_id JOIN ledger.accounts a ON a.id = p.account_id WHERE j.entity_id = %s""", (eid,))
    assert rows == [f"equity:opening@{cut}:-3000", f"receivable:customers@{cut}:3000",
                    f"receivable:customers@{today()}:-3000", f"revenue:opening_adjustment@{today()}:3000"]


async def test_voiding_with_a_stated_date_books_on_that_date_not_today(co: dict[str, Any]) -> None:
    """api 1.56 (nạp lịch sử — 0185): bên gọi nêu ngày huỷ thật ⇒ bút toán huỷ ghi đúng ngày đó, không dồn vào ngày nạp; ngày sau hôm nay bị từ chối."""
    kt = await who(co, "ketoan")
    code, eid = await entity(co)
    cut = today() - timedelta(days=20)
    b = f"DK{next(_seq)}"
    await stage(kt, b, kind="receivable", domain=SRC_B, label="Phí cũ", party="H", debit_vnd=2_000,
                issued_on=(cut - timedelta(days=30)).isoformat(), due_on=(cut + timedelta(days=30)).isoformat())
    out = await open_(kt, b, code, cut, lock_after=False)
    assert out.status == "ok", out.error
    on = cut + timedelta(days=5)
    async with db.tenant_tx(kt) as conn:
        c = core_ctx(conn, kt, module=SRC_B)
        with pytest.raises(Invalid):
            await c.call_system("acct.void_receivables", source=f"{SRC_B}.opening", ref_prefix=str(out.output["batch_id"]), due_after=cut.isoformat(),
                                on=(today() + timedelta(days=1)).isoformat())
    async with db.tenant_tx(kt) as conn:
        got = await core_ctx(conn, kt, module=SRC_B).call_system("acct.void_receivables", source=f"{SRC_B}.opening", ref_prefix=str(out.output["batch_id"]),
                                                               due_after=cut.isoformat(), on=on.isoformat())
    assert len(got["voided"]) == 1
    rows = await owner_val("""SELECT array_agg(a.code || '@' || j.posted_on || ':' || p.amount_vnd ORDER BY j.posted_on, a.code) FROM ledger.postings p
                              JOIN ledger.journals j ON j.id = p.journal_id JOIN ledger.accounts a ON a.id = p.account_id WHERE j.entity_id = %s""", (eid,))
    assert rows == [f"equity:opening@{cut}:-2000", f"receivable:customers@{cut}:2000",
                    f"receivable:customers@{on}:-2000", f"revenue:opening_adjustment@{on}:2000"]


async def test_replaying_a_posted_batch_with_new_draft_lines_is_refused(co: dict[str, Any]) -> None:
    kt = await who(co, "ketoan")
    code, _ = await entity(co)
    cut = today() - timedelta(days=4)
    b = f"DK{next(_seq)}"
    await stage(kt, b, kind="account", account="cash:bank", label="Tiền gửi", debit_vnd=1_000)
    assert (await open_(kt, b, code, cut)).status == "ok"
    await stage(kt, b, kind="account", account="tax:withheld", label="Thuế", credit_vnd=500)
    r = await open_(kt, b, code, cut)
    assert r.status == "conflict" and "dòng nháp" in (r.error or "")


async def test_statement_line_in_a_locked_period_is_refused_at_import(co: dict[str, Any]) -> None:
    kt = await who(co, "ketoan")
    code, _ = await entity(co)
    d = today() - timedelta(days=3)
    await ok(kt, "acct.lock_period", entity_code=code, locked_through=d.isoformat())
    out = await imports.run_import(kt, "acct.add_statement_line", [
        {"Ngày": d.strftime("%d/%m/%Y"), "Số tiền": "1tr", "Nội dung": "tiền cũ", "Mã giao dịch ngân hàng": uuid4().hex[:12], "Pháp nhân nhận tiền": code},
        {"Ngày": today().strftime("%d/%m/%Y"), "Số tiền": "1tr", "Nội dung": "tiền mới", "Mã giao dịch ngân hàng": uuid4().hex[:12], "Pháp nhân nhận tiền": code},
    ], dry_run=False)
    st = [r["status"] for r in out.output["rows"]]
    assert st == ["invalid", "ok"] and "kỳ đã khoá" in out.output["rows"][0]["error"]
