"""SỔ CHO GÓI HÀNG HOÁ (ADR 0217) trên Postgres thật, với một GÓI GIẢ khai ngay trong test (như ``test_ad_control``) — lõi không biết tên gói hàng nào.

* ``acct.post_entry`` (họ ``journal``): bút toán không tiền trên tài khoản CỐ ĐỊNH của chính gói; gọi lại cùng số ⇒ bút toán cũ, khác số ⇒ xung đột;
  tài khoản tiền / tài khoản nền của Tài chính / nguồn của module khác / module không khai họ ⇒ từ chối; đảo ⇒ về 0.
* ``acct.settle_line``: MỘT dòng sao kê = nhiều khoản phải thu − khấu trừ; lệch một đồng ⇒ không ghi gì; người không có quyền Tài chính ⇒ từ chối; quyền
  hệ thống ⇒ từ chối (không khai hợp đồng); đối soát lại ⇒ xung đột; sau đó sổ cân, bất biến tiền 0 / 0.
"""

import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest

from barista import seed
from barista.api import Ctx, Module, NoInput, Pack
from barista.db import db
from barista.kernel import executor, ledger, registry, screens
from barista.kernel.clock import today
from barista.kernel.executor import core_ctx
from barista.kernel.principal import Principal, system_principal
from tests.conftest import ok, owner_val

KEY = "goodsfake"
PEOPLE = [("admin", "Quản trị", "admin", "QT", True), ("ketoan", "Kế toán", "accountant", "KT", True), ("sale", "Sale", "sale", "KD-A", False)]
LIVE: tuple[Any, ...] = (registry.PACKS, registry.MODULES, registry.MODULE_PACK, registry.RESOURCES, registry.RESOURCE_MODULE, registry.OPS,
                         registry.SETUP_RESOURCES, registry.PACK_MODULE, screens.WORKSPACES, screens.CONTRIB, ledger.ACCOUNTS, ledger.OWNERS)
ACCOUNTS = {"inventory:goodsfake": ("Hàng thử", "asset"), "expense:goodsfake_cogs": ("Giá vốn thử", "expense"),
            "expense:goodsfake_fee": ("Phí bên thu hộ thử", "expense"), "revenue:goodsfake_sales": ("Doanh thu hàng thử", "revenue"),
            "receivable:goodsfake": ("Phải thu bên thu hộ thử", "asset"), "payable:goodsfake_refunds": ("Phải trả khách thử", "liability"),
            "cash:goodsfake": ("Tiền thử (không được)", "asset")}


@pytest.fixture(scope="module", autouse=True)
def fake_pack() -> Iterator[None]:
    snap = [type(s)(s) for s in LIVE]
    families = {k: set(v) for k, v in registry.FAMILY_CALLERS.items()}
    Pack(KEY, "Hàng thử", "Gói hàng giả cho test lõi", kind="capability", requires=("finance",))
    m = Module(KEY, "Hàng thử", pack=KEY, resources={KEY: "Hàng thử"})
    m.consumes("journal", "receivable")
    ledger.declare(ACCOUNTS)   # nơi khai = file test ⇒ chủ = gốc mã của thao tác dưới (0188)

    @m.query("ping", resource=KEY, title="Thử", internal=True, agent="deny", callers=("acct",), why="test")
    async def _ping(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
        return {}

    registry.validate()
    yield
    for live, old in zip(LIVE, snap, strict=True):
        live.clear()
        live.extend(old) if isinstance(live, list) else live.update(old)
    registry.FAMILY_CALLERS.clear()
    registry.FAMILY_CALLERS.update(families)


@pytest.fixture(scope="module")
async def co() -> dict[str, Any]:
    d = f"g{uuid.uuid4().hex[:6]}.demo"
    tid = await seed.create_tenant(f"Công ty hàng thử {d}", d, people=PEOPLE, packs=("finance",))
    return {"tid": tid, **{k: await seed.who(tid, k, d) for k, *_ in PEOPLE}}


async def _sys(tid: Any, name: str, module: str = KEY, **inp: Any) -> Any:
    p = system_principal(tid)
    async with db.tenant_tx(p) as conn:
        return await core_ctx(conn, p, module=module).call_system(name, **inp)


async def _as(p: Principal, name: str, **inp: Any) -> Any:
    async with db.tenant_tx(p) as conn:
        ctx = core_ctx(conn, p, module=KEY)
        return await ctx.call_as(name, ctx.p, **inp)


async def _status(fn: Any, *a: Any, **kw: Any) -> str:
    try:
        await fn(*a, **kw)
    except Exception as e:   # lỗi nghiệp vụ của lõi ⇒ tên lớp
        return type(e).__name__
    return "ok"


async def _gl(tid: Any, code: str) -> int:
    return int(await owner_val("""SELECT coalesce(sum(p.amount_vnd), 0) FROM ledger.postings p JOIN ledger.accounts a ON a.id = p.account_id
                                  WHERE a.tenant_id = %s AND a.code = %s""", (tid, code)))


async def test_post_entry_only_on_the_packs_own_non_cash_accounts(co: dict[str, Any]) -> None:
    tid, ref = co["tid"], uuid.uuid4().hex[:8]
    lines = [{"account": "expense:goodsfake_cogs", "amount_vnd": 120_000}, {"account": "inventory:goodsfake", "amount_vnd": -120_000}]
    first = await _sys(tid, "acct.post_entry", source=f"{KEY}.issue", source_ref=ref, memo="Giá vốn thử", lines=lines)
    again = await _sys(tid, "acct.post_entry", source=f"{KEY}.issue", source_ref=ref, memo="Giá vốn thử", lines=lines)
    assert first["created"] and not again["created"] and again["journal_id"] == first["journal_id"]
    other = [{"account": "expense:goodsfake_cogs", "amount_vnd": 1}, {"account": "inventory:goodsfake", "amount_vnd": -1}]
    assert await _status(_sys, tid, "acct.post_entry", source=f"{KEY}.issue", source_ref=ref, memo="x", lines=other) == "Conflict"
    # cùng mã, cùng số nhưng NÊU pháp nhân / ngày khác bút toán đã ghi ⇒ xung đột, không trả "đã ghi" (rà đa ngành 17/11: bên gọi tưởng đã ghi cho pháp nhân kia)
    await ok(co["admin"], "admin.upsert_entity", code="PN2", name="Pháp nhân thứ hai (thử chống lặp)")
    home, second = (await owner_val("SELECT entity_id FROM ledger.journals WHERE id = %s", (first["journal_id"],)),
                    await owner_val("SELECT id FROM core.legal_entities WHERE tenant_id = %s AND code = 'PN2'", (tid,)))
    same = await _sys(tid, "acct.post_entry", source=f"{KEY}.issue", source_ref=ref, memo="Giá vốn thử", lines=lines, entity_id=str(home), on=today().isoformat())
    assert not same["created"] and same["journal_id"] == first["journal_id"]
    assert await _status(_sys, tid, "acct.post_entry", source=f"{KEY}.issue", source_ref=ref, memo="Giá vốn thử", lines=lines, entity_id=str(second)) == "Conflict"
    assert await _status(_sys, tid, "acct.post_entry", source=f"{KEY}.issue", source_ref=ref, memo="Giá vốn thử", lines=lines,
                         on=(today() - timedelta(days=1)).isoformat()) == "Conflict"
    assert await owner_val("SELECT entity_id FROM ledger.journals WHERE id = %s", (first["journal_id"],)) == home
    for bad in ("cash:goodsfake", "revenue:services", "receivable:goodsfake", "cash:bank"):   # tiền / tài khoản nền / tiền tố không cho phép
        got = await _status(_sys, tid, "acct.post_entry", source=f"{KEY}.issue", source_ref=uuid.uuid4().hex, memo="x",
                            lines=[{"account": "expense:goodsfake_cogs", "amount_vnd": 5}, {"account": bad, "amount_vnd": -5}])
        assert got == "Invalid", bad
    assert await _status(_sys, tid, "acct.post_entry", source="acct.issue", source_ref=uuid.uuid4().hex, memo="x", lines=lines) == "Denied"
    assert await _status(_sys, tid, "acct.post_entry", module="crm", source="crm.issue", source_ref=uuid.uuid4().hex, memo="x", lines=lines) == "Denied"
    assert await _status(_sys, tid, "acct.post_entry", source=f"{KEY}.issue", source_ref=uuid.uuid4().hex, memo="x",
                         lines=[{"account": "expense:goodsfake_cogs", "amount_vnd": 5}, {"account": "inventory:goodsfake", "amount_vnd": -4}]) == "Invalid"
    rev = await _sys(tid, "acct.post_entry", source=f"{KEY}.return", source_ref=f"{ref}-r", memo="Đảo", reverses_source=f"{KEY}.issue", reverses_ref=ref)
    assert rev["created"]
    assert await _status(_sys, tid, "acct.post_entry", source=f"{KEY}.return", source_ref=uuid.uuid4().hex, memo="Đảo", reverses_ref="khong-co") == "NotFound"
    assert await _gl(tid, "expense:goodsfake_cogs") == 0 and await _gl(tid, "inventory:goodsfake") == 0
    assert (await executor.run(co["ketoan"], "acct.post_entry", {"source": f"{KEY}.issue", "source_ref": "x", "memo": "x", "lines": lines})).status in ("denied", "not_found")   # thao tác nội bộ: người không gọi thẳng


async def _receivable(tid: Any, amount: int) -> str:
    ref = uuid.uuid4().hex[:10]
    await _sys(tid, "acct.issue_receivable", source=f"{KEY}.order", source_ref=ref, label=f"Đơn thử {ref}", party="Bên thu hộ thử", nature="revenue",
               amount_vnd=amount, revenue_account="revenue:goodsfake_sales", receivable_account="receivable:goodsfake", due_on=today().isoformat(),
               followed=True)
    return ref


async def test_one_net_bank_line_settles_many_receivables_with_deductions(co: dict[str, Any]) -> None:
    tid = co["tid"]
    r1, r2 = await _receivable(tid, 300_000), await _receivable(tid, 200_000)
    bank = f"NH-{uuid.uuid4().hex[:8]}"
    await ok(co["ketoan"], "acct.add_statement_line", received_on=today().isoformat(), amount_vnd=480_000, memo="BEN THU HO CHUYEN", bank_ref=bank)
    items = [{"source": f"{KEY}.order", "source_ref": r1, "amount_vnd": 300_000}, {"source": f"{KEY}.order", "source_ref": r2, "amount_vnd": 200_000}]
    fee = [{"account": "expense:goodsfake_fee", "amount_vnd": 20_000, "memo": "Phí"}]
    base = dict(bank_ref=bank, source=f"{KEY}.cod_batch", source_ref="BK-1", items=items)
    assert await _status(_as, co["ketoan"], "acct.settle_line", **base, deductions=[{**fee[0], "amount_vnd": 18_000}]) == "Invalid"   # lệch 2.000 đ
    assert await _status(_as, co["ketoan"], "acct.settle_line", **base, deductions=[{**fee[0], "account": "expense:marketing"}]) == "Invalid"
    assert await owner_val("SELECT status || ':' || allocated_vnd || ':' || deducted_vnd FROM acct.statement_lines WHERE tenant_id = %s AND bank_ref = %s",
                           (tid, bank)) == "open:0:0"
    assert await _status(_as, co["sale"], "acct.settle_line", **base, deductions=fee) == "Denied"
    assert await _status(_sys, tid, "acct.settle_line", **base, deductions=fee) == "Denied"   # không hợp đồng quyền hệ thống
    out = await _as(co["ketoan"], "acct.settle_line", **base, deductions=fee)
    assert (out["receivables"], out["gross_vnd"], out["deducted_vnd"]) == (2, 500_000, 20_000)
    assert await owner_val("SELECT status || ':' || allocated_vnd || ':' || deducted_vnd FROM acct.statement_lines WHERE tenant_id = %s AND bank_ref = %s",
                           (tid, bank)) == "applied:500000:20000"
    assert await owner_val("SELECT string_agg(status, ',') FROM acct.receivables WHERE tenant_id = %s AND source_ref = ANY(%s)", (tid, [r1, r2])) == "paid,paid"
    assert await _gl(tid, "expense:goodsfake_fee") == 20_000
    # api 1.54: đã thu TÍNH TỚI trước một ngày — phiếu thu hôm nay không tính vào mốc "trước hôm nay", tính vào mốc "trước ngày mai"; kèm ngày thu, mục
    now = {r["source_ref"]: r for r in await _sys(tid, "acct.receipt_facts", source=f"{KEY}.order", refs=[r1, r2])}
    assert (now[r1]["paid_vnd"], now[r1]["last_received_on"], now[r1]["category"]) == (300_000, today(), "")
    cut = {r["source_ref"]: r["paid_vnd"] for r in await _sys(tid, "acct.receipt_facts", source=f"{KEY}.order", refs=[r1, r2], before=today().isoformat())}
    assert cut == {r1: 0, r2: 0}
    nxt = {r["source_ref"]: r["paid_vnd"] for r in await _sys(tid, "acct.receipt_facts", source=f"{KEY}.order", refs=[r1, r2],
                                                             before=(today() + timedelta(days=1)).isoformat())}
    assert nxt == {r1: 300_000, r2: 200_000}
    assert "category" in (await _sys(tid, "acct.receivables_for", source_prefix=f"{KEY}.order", ref_prefixes=[r1]))[0]
    assert await _status(_as, co["ketoan"], "acct.settle_line", **base, deductions=fee) == "Conflict"
    assert await owner_val("SELECT sum(amount_vnd) FROM ledger.postings p JOIN ledger.accounts a ON a.id = p.account_id WHERE a.tenant_id = %s", (tid,)) == 0
    assert (await executor.run(system_principal(tid), "acct.check_money", {})).output == {"receivables": 0, "lines": 0}
    # tiền về chưa phân bổ của sổ cái không bị khấu trừ làm lệch
    assert await _gl(tid, "unapplied:bank") == 0
