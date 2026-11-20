"""SỔ CHO GÓI HÀNG HOÁ (ADR 0217) trên Postgres thật, với một GÓI GIẢ khai ngay trong test (như ``test_ad_control``) — lõi không biết tên gói hàng nào.

* ``acct.post_entry`` (họ ``journal``): bút toán không tiền trên tài khoản CỐ ĐỊNH của chính gói; gọi lại cùng số ⇒ bút toán cũ, khác số ⇒ xung đột;
  tài khoản tiền / tài khoản nền của Tài chính / nguồn của module khác / module không khai họ ⇒ từ chối; đảo ⇒ về 0.
* ``acct.settle_line``: MỘT dòng sao kê = nhiều khoản phải thu − khấu trừ; lệch một đồng ⇒ không ghi gì; người không có quyền Tài chính ⇒ từ chối; quyền
  hệ thống ⇒ từ chối (không khai hợp đồng); đối soát lại ⇒ xung đột; sau đó sổ cân, bất biến tiền 0 / 0.
"""

import asyncio
import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from psycopg import errors as pg_errors

from barista import seed
from barista.api import Ctx, Module, NoInput, Pack
from barista.db import db
from barista.kernel import executor, ledger, registry, screens
from barista.kernel.clock import today
from barista.kernel.errors import Conflict
from barista.kernel.executor import core_ctx
from barista.kernel.principal import Principal, system_principal
from tests.conftest import approved, ok, owner_val

KEY = "goodsfake"
PEOPLE = [("admin", "Quản trị", "admin", "QT", True), ("ketoan", "Kế toán", "accountant", "KT", True), ("sale", "Sale", "sale", "KD-A", False),
          ("giamdoc", "Giám đốc", "director", "QT", True)]   # giám đốc: người duyệt giấy báo có
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


async def _held(tid: Any, release: asyncio.Event, posted: asyncio.Event, **inp: Any) -> Any:
    """Ghi trong một giao dịch rồi GIỮ chưa commit tới khi ``release`` — để yêu cầu thứ hai chạy đúng lúc bên kia còn dở."""
    p = system_principal(tid)
    async with db.tenant_tx(p) as conn:
        got = await core_ctx(conn, p, module=KEY).call_system("acct.post_entry", **inp)
        posted.set()
        await release.wait()
        return got


async def _race(tid: Any, first: dict[str, Any], second: dict[str, Any]) -> tuple[Any, str | dict[str, Any]]:
    release, posted = asyncio.Event(), asyncio.Event()
    a = asyncio.create_task(_held(tid, release, posted, **first))
    await posted.wait()
    b = asyncio.create_task(_sys(tid, "acct.post_entry", **second))
    await asyncio.sleep(0.4)   # b đã qua bước kiểm "đã có chưa" và đang chờ chỉ mục duy nhất của a
    release.set()
    got_a = await a
    try:
        return got_a, await b
    except Exception as e:   # lỗi nghiệp vụ của lõi ⇒ tên lớp
        return got_a, type(e).__name__


async def test_same_key_different_amount_at_the_same_time_loser_gets_conflict(co: dict[str, Any]) -> None:
    """Rà Codex 20/11 lõi tiền, lỗi 1: hai yêu cầu đồng thời cùng (nguồn, mã) khác số — trước đây cả hai trả created=True, sổ chỉ có một số. Nay bên
    thua đọc lại bút toán bên thắng: khác số ⇒ Conflict; cùng số ⇒ đúng một lần tạo + một lần đọc lại."""
    tid = co["tid"]
    before = await _gl(tid, "expense:goodsfake_cogs")
    ref = "RACE-" + uuid.uuid4().hex[:8]
    body = {"source": f"{KEY}.issue", "source_ref": ref, "memo": "Giá vốn đua"}
    a, b = await _race(tid, {**body, "lines": [{"account": "expense:goodsfake_cogs", "amount_vnd": 100}, {"account": "inventory:goodsfake", "amount_vnd": -100}]},
                       {**body, "lines": [{"account": "expense:goodsfake_cogs", "amount_vnd": 200}, {"account": "inventory:goodsfake", "amount_vnd": -200}]})
    assert a["created"] is True and b == "Conflict"
    assert await _gl(tid, "expense:goodsfake_cogs") == before + 100

    ref2 = "RACE-" + uuid.uuid4().hex[:8]
    same = [{"account": "expense:goodsfake_cogs", "amount_vnd": 300}, {"account": "inventory:goodsfake", "amount_vnd": -300}]
    a, b = await _race(tid, {**body, "source_ref": ref2, "lines": same}, {**body, "source_ref": ref2, "lines": same})
    assert isinstance(b, dict) and a["created"] is True and b["created"] is False and a["journal_id"] == b["journal_id"]
    assert await _gl(tid, "expense:goodsfake_cogs") == before + 400


async def test_an_entry_is_reversed_once_even_with_two_keys_or_at_the_same_time(co: dict[str, Any]) -> None:
    """Rà Codex 20/11 lõi tiền, lỗi 2: bút toán gốc đảo bằng hai mã khác nhau — trước đây cả hai được nhận (gốc 100 ⇒ −100 ròng). Nay chỉ mục duy nhất
    base/104 giữ: lần đảo thứ hai (tuần tự hay đồng thời) ⇒ Conflict; gọi lại đúng mã đảo cũ ⇒ bút toán cũ."""
    tid = co["tid"]
    before = await _gl(tid, "expense:goodsfake_cogs")
    lines = [{"account": "expense:goodsfake_cogs", "amount_vnd": 100}, {"account": "inventory:goodsfake", "amount_vnd": -100}]
    ref = "REV-" + uuid.uuid4().hex[:8]
    await _sys(tid, "acct.post_entry", source=f"{KEY}.issue", source_ref=ref, memo="Giá vốn gốc", lines=lines)
    one = await _sys(tid, "acct.post_entry", source=f"{KEY}.issue", source_ref=ref + "-r1", memo="Đảo", reverses_ref=ref)
    assert one["created"] is True
    again = await _sys(tid, "acct.post_entry", source=f"{KEY}.issue", source_ref=ref + "-r1", memo="Đảo", reverses_ref=ref)
    assert again == {"journal_id": one["journal_id"], "created": False}
    assert await _status(_sys, tid, "acct.post_entry", source=f"{KEY}.issue", source_ref=ref + "-r2", memo="Đảo lần hai", reverses_ref=ref) == "Conflict"
    assert await _gl(tid, "expense:goodsfake_cogs") == before

    ref2 = "REV-" + uuid.uuid4().hex[:8]
    await _sys(tid, "acct.post_entry", source=f"{KEY}.issue", source_ref=ref2, memo="Giá vốn gốc", lines=lines)
    body = {"source": f"{KEY}.issue", "memo": "Đảo đua", "reverses_ref": ref2}
    a, b = await _race(tid, {**body, "source_ref": ref2 + "-a"}, {**body, "source_ref": ref2 + "-b"})
    assert a["created"] is True and b == "Conflict"
    assert await _gl(tid, "expense:goodsfake_cogs") == before


async def test_replay_compares_debit_and_credit_lines_not_the_net(co: dict[str, Any]) -> None:
    """Rà Codex 20/11 (greenfield): 100 Nợ rồi gửi lại cùng khoá với 150 Nợ + 50 Có cùng tài khoản — ròng bằng nhau nhưng phát sinh Nợ / Có khác ⇒
    Conflict, không phải "đã ghi"."""
    tid = co["tid"]
    p = system_principal(tid)
    key = "test.gross:" + uuid.uuid4().hex[:8]
    async with db.tenant_tx(p) as conn:
        cx = core_ctx(conn, p, module="acct")
        await ledger.post(cx, key=key, memo="Gốc", source="test.gross", lines=[("inventory:goodsfake", 100), ("expense:goodsfake_cogs", -100)], on=today())
    async with db.tenant_tx(p) as conn:
        cx = core_ctx(conn, p, module="acct")
        assert (await ledger.post(cx, key=key, memo="Gốc", source="test.gross", on=today(),
                                       lines=[("expense:goodsfake_cogs", -100), ("inventory:goodsfake", 100)])).created is False   # cùng dòng, khác thứ tự
        with pytest.raises(Conflict):
            await ledger.post(cx, key=key, memo="Gốc", source="test.gross", on=today(),
                              lines=[("inventory:goodsfake", 150), ("inventory:goodsfake", -50), ("expense:goodsfake_cogs", -100)])


async def test_the_app_role_writes_whole_entries_only(co: dict[str, Any]) -> None:
    """Rà Codex 20/11 (greenfield): vai ứng dụng từng thêm được một cặp Nợ / Có cân vào bút toán đã commit (2 → 4 dòng) và commit được đầu bút toán
    0 dòng. Nay (base/104) DB giữ bằng trigger, vẫn dưới RLS của vai ứng dụng (không hàm quyền đặc biệt): dòng chỉ vào bút toán của CÙNG giao dịch,
    mỗi bút toán ≥ 2 dòng lúc commit."""
    tid = co["tid"]
    p = system_principal(tid)
    key = "test.whole:" + uuid.uuid4().hex[:8]
    async with db.tenant_tx(p) as conn:
        cx = core_ctx(conn, p, module="acct")
        jid = (await ledger.post(cx, key=key, memo="Gốc", source="test.whole", lines=[("inventory:goodsfake", 100), ("expense:goodsfake_cogs", -100)],
                                on=today())).journal_id
        acc = await cx.val("SELECT id FROM ledger.accounts WHERE code = 'inventory:goodsfake'")
    with pytest.raises(pg_errors.RaiseException, match="không nhận thêm dòng"):   # bút toán đã commit
        async with db.tenant_tx(p) as conn:
            await conn.execute("INSERT INTO ledger.postings (journal_id, account_id, amount_vnd, memo) VALUES (%s, %s, 50, 'x'), (%s, %s, -50, 'x')",
                               (jid, acc, jid, acc))
    with pytest.raises(pg_errors.RaiseException, match="hai dòng"):   # đầu bút toán không dòng — chặn lúc commit
        async with db.tenant_tx(p) as conn:
            await conn.execute("INSERT INTO ledger.journals (memo, source, source_ref, idempotency_key, posted_on) VALUES ('x', 'test.whole', '', %s, core.today())",
                               ("test.whole:" + uuid.uuid4().hex[:8],))
    with pytest.raises(pg_errors.RaiseException, match="hai dòng"):   # cửa ghi kiểm ngay
        async with db.tenant_tx(p) as conn:
            await conn.execute("SELECT ledger.write_journal(%s, 'x', 'test.whole', '', NULL, NULL, core.today(), %s::jsonb)",
                               ("test.whole:" + uuid.uuid4().hex[:8], f'[{{"account": "{acc}", "amount": 100}}]'))
    assert int(await owner_val("SELECT count(*) FROM ledger.postings WHERE journal_id = %s", (jid,))) == 2


async def test_writing_the_ledger_still_needs_the_ledger_grant(co: dict[str, Any]) -> None:
    """Rà Codex 20/11: bản đầu của base/104 ghi qua hàm chạy bằng role có BYPASSRLS ⇒ DB không còn kiểm ai được ghi sổ. Nay cửa ghi là hàm thường:
    người không có quyền sổ cái cả công ty (vd sale) gọi thẳng ⇒ RLS từ chối."""
    with pytest.raises(pg_errors.InsufficientPrivilege):
        async with db.tenant_tx(co["sale"]) as conn:
            await conn.execute("SELECT ledger.write_journal(%s, 'x', 'test.whole', '', NULL, NULL, core.today(), %s::jsonb)",
                               ("test.rls:" + uuid.uuid4().hex[:8],
                                '[{"account": "00000000-0000-0000-0000-000000000001", "amount": 1}, {"account": "00000000-0000-0000-0000-000000000002", "amount": -1}]'))


async def test_receivable_replay_with_another_amount_is_a_conflict(co: dict[str, Any]) -> None:
    """Rà Codex 20/11 (greenfield): lập khoản 100 rồi gọi lại cùng (nguồn, mã) với 200 từng báo thành công mà khoản vẫn 100. Nay một phép so cho cả
    gọi lại tuần tự lẫn khi thua yêu cầu song song: khác số ⇒ Conflict; cùng số ⇒ khoản cũ, created=False."""
    tid = co["tid"]
    ref = uuid.uuid4().hex[:10]
    body = {"source": f"{KEY}.order", "source_ref": ref, "label": f"Đơn thử {ref}", "party": "Bên thu hộ thử", "nature": "revenue",
            "revenue_account": "revenue:goodsfake_sales", "receivable_account": "receivable:goodsfake", "due_on": today().isoformat(), "followed": True}
    first = await _sys(tid, "acct.issue_receivable", amount_vnd=100, **body)
    assert first["created"] is True
    again = await _sys(tid, "acct.issue_receivable", amount_vnd=100, **body)
    assert again == {"receivable_id": first["receivable_id"], "code": first["code"], "created": False}
    assert await _status(_sys, tid, "acct.issue_receivable", amount_vnd=200, **body) == "Conflict"


def test_pass_through_fee_is_exact_integer_math() -> None:
    """Rà Codex 20/11 (greenfield): phí qua số thực ra 71.428.572 ở ranh giới nửa đồng; số nguyên chính xác (làm tròn nửa lên) ra 71.428.571. Một
    công thức cho ghi nhận và dự phóng; thu nhiều đợt cộng đúng bằng phí."""
    from barista.modules.acct.cash_in import fee_earned
    assert fee_earned(100_000_000, 714_285_720, 1_000_000_007) == 71_428_571
    assert fee_earned(1, 1, 2) == 1 and fee_earned(1, 1, 3) == 0 and fee_earned(3, 1, 2) == 2   # nửa lên; dưới nửa xuống
    amount, fee = 1_000_000_007, 100_000_000
    paid, taken = 0, 0
    for part in (333_333_333, 333_333_333, 333_333_341):
        paid += part
        taken += fee_earned(fee, paid, amount) - taken
    assert paid == amount and taken == fee


async def test_receivable_replay_compares_the_issued_facts_not_the_adjusted_amount(co: dict[str, Any]) -> None:
    """Rà Codex 20/11 (kiểm lại): lập 100, giấy báo có giảm 20, rồi gửi lại. Trước: gửi lại 100 bị Conflict, gửi 80 lại được nhận (so với số đã điều
    chỉnh). Nay so với số LÚC LẬP (finance/077, không sửa được): gửi lại 100 ⇒ khoản cũ; gửi 80 ⇒ Conflict."""
    tid = co["tid"]
    ref = uuid.uuid4().hex[:10]
    body = {"source": f"{KEY}.order", "source_ref": ref, "label": f"Đơn thử {ref}", "party": "Bên thu hộ thử", "nature": "revenue",
            "revenue_account": "revenue:goodsfake_sales", "receivable_account": "receivable:goodsfake", "due_on": today().isoformat(), "followed": True}
    first = await _sys(tid, "acct.issue_receivable", amount_vnd=100, **body)
    await approved(co["ketoan"], "acct.credit_receivable", code=first["code"], amount_vnd=20, reason="Giảm thử theo biên bản")   # giấy báo có thật
    assert await owner_val("SELECT amount_vnd FROM acct.receivables WHERE id = %s", (first["receivable_id"],)) == 80
    assert (await _sys(tid, "acct.issue_receivable", amount_vnd=100, **body))["created"] is False
    assert await _status(_sys, tid, "acct.issue_receivable", amount_vnd=80, **body) == "Conflict"
    with pytest.raises(pg_errors.RaiseException, match="không sửa được"):
        await owner_val("UPDATE acct.receivables SET issued_amount_vnd = 80 WHERE id = %s RETURNING id", (first["receivable_id"],))


async def test_a_pack_writing_for_a_user_who_cannot_see_the_ledger_commits(co: dict[str, Any]) -> None:
    """Gói ghi tiền bằng quyền hệ thống (``call_system``) trong phiên của người dùng không thấy sổ cái (sale), rồi commit dưới phiên đó: trigger kiểm
    "bút toán nguyên khối" (base/104) phải đếm đủ dòng — chạy bằng quyền định nghĩa như các trigger kiểm khác, không theo RLS của người gọi."""
    sale = co["sale"]
    ref = "SALE-" + uuid.uuid4().hex[:8]
    async with db.tenant_tx(sale) as conn:
        got = await core_ctx(conn, sale, module=KEY).call_system(
            "acct.post_entry", source=f"{KEY}.issue", source_ref=ref, memo="Giá vốn do sale bán",
            lines=[{"account": "expense:goodsfake_cogs", "amount_vnd": 42}, {"account": "inventory:goodsfake", "amount_vnd": -42}])
    assert got["created"] is True
    assert int(await owner_val("SELECT count(*) FROM ledger.postings WHERE journal_id = %s", (got["journal_id"],))) == 2
