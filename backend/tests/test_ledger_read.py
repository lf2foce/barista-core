"""0245 — hợp đồng ĐỌC sổ cái cho gói (họ ``ledger_read``): ``acct.ledger_lines`` trả đúng mọi dòng sổ cái của kỳ theo trang ĐỦ BÚT TOÁN (mỗi trang
cân, nối các trang = sổ cái), ``acct.ledger_balances`` = tổng phát sinh tới hết một ngày; số hiệu theo bảng Ánh xạ tài khoản, chưa ánh xạ ⇒ ``""``;
đối tượng chỉ ở tài khoản sổ phụ. Dữ liệu giả, Postgres thật, công ty thử riêng."""

import uuid
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

import pytest
import pytest_asyncio

from barista import seed
from barista.db import db
from barista.kernel import executor, registry
from barista.kernel.clock import today
from barista.kernel.errors import Invalid
from barista.kernel.principal import system_principal
from barista.modules.acct import ledger_read
from barista.modules.acct.ledger_read import party_of
from tests.conftest import ok, owner_val

DOMAIN = "docso.demo"


@pytest_asyncio.fixture(scope="module")
async def co() -> dict[str, Any]:
    tid = await seed.create_tenant("Công ty thử đọc sổ cái", DOMAIN, people=[("giamdoc", "Giám đốc thử", "director", "HQ", True),
                                                                         ("ketoan", "Kế toán thử", "accountant", "KT", True)], packs=seed.ALL_PACKS)
    kt = await seed.who(tid, "ketoan", DOMAIN)
    await ok(kt, "acct.set_category", code="van_phong", direction="out", label="Thuê văn phòng", account="expense:thue_van_phong")
    await ok(kt, "acct.set_account_map", ledger_prefix="expense:thue_van_phong", misa_account="6422", label="Chi phí quản lý")
    ref = "R" + uuid.uuid4().hex[:10].upper()
    for i, amount in enumerate((10_000_000, 4_000_000, 2_500_000)):
        await ok(kt, "acct.record_expense", category="van_phong", amount_vnd=amount, ref=f"{ref}{i}")
    return {"tid": tid, "kt": kt}


async def _sys(tid: Any, name: str, **inp: Any) -> Any:
    """Gọi đúng như gói gọi: ``ctx.call_system`` từ một module khai tiêu thụ họ (mượn mgmt trong lần gọi — gói thật khai ``m.consumes``)."""
    fam = registry.OPS[name].family
    assert fam == "ledger_read"
    had = "mgmt" in registry.FAMILY_CALLERS[fam]
    registry.FAMILY_CALLERS[fam].add("mgmt")
    try:
        async with db.tenant_tx(system_principal(tid)) as conn:
            cx = executor.core_ctx(conn, system_principal(tid), module="mgmt")
            return await cx.call_system(name, **inp)
    finally:
        if not had:
            registry.FAMILY_CALLERS[fam].discard("mgmt")


async def _books(tid: Any, a: date, b: date) -> dict[tuple[str, str], int]:
    rows = await owner_val(
        """SELECT coalesce(json_agg(x), '[]') FROM (SELECT e.code AS entity, a.code AS account, sum(p.amount_vnd)::bigint AS v
                  FROM ledger.journals j JOIN ledger.postings p ON p.journal_id = j.id JOIN ledger.accounts a ON a.id = p.account_id
                  JOIN core.legal_entities e ON e.id = j.entity_id
                  WHERE j.tenant_id = %s AND j.posted_on BETWEEN %s AND %s GROUP BY 1, 2) x""", (tid, a, b))
    return {(r["entity"], r["account"]): int(r["v"]) for r in rows if int(r["v"])}


def test_party_only_on_subledger_accounts() -> None:
    u = str(uuid.uuid4())
    assert party_of(f"payable:{u}", "Người hưởng mẫu", u) == {"kind": "payable", "id": u, "name": "Người hưởng mẫu", "user_id": u}
    assert party_of(f"wallet:{u}", "Ví mẫu", None) == {"kind": "wallet", "id": u, "name": "Ví mẫu", "user_id": None}
    assert party_of("expense:marketing", "Quảng cáo", None) is None
    assert party_of("cash:bank", "Tiền gửi", None) is None


async def test_lines_page_by_whole_journals_and_rebuild_the_ledger(co: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    tid = co["tid"]
    a, b = today() - timedelta(days=400), today()
    monkeypatch.setattr(ledger_read, "PAGE", 1)   # mỗi trang một bút toán ⇒ thử nối trang
    got: dict[tuple[str, str], int] = defaultdict(int)
    journals: list[str] = []
    after, pages = "", 0
    while True:
        d = await _sys(tid, "acct.ledger_lines", since=a, until=b, after=after)
        pages += 1
        assert d["basis"] == "ledger" and d["entity"] == ""
        ids = {r["journal"] for r in d["rows"]}
        assert len(ids) <= 1 and sum(r["amount_vnd"] for r in d["rows"]) == 0   # trang đủ bút toán ⇒ tự cân
        journals += sorted(ids)
        for r in d["rows"]:
            got[(r["entity"], r["account"])] += r["amount_vnd"]
            assert r["kind"] == d["accounts"][r["account"]]["kind"]
        if d["next"] is None:
            break
        after = d["next"]
    assert pages >= 3 and len(journals) == len(set(journals))   # không bút toán nào lặp giữa hai trang
    assert {k: v for k, v in got.items() if v} == await _books(tid, a, b)


async def test_mapped_number_and_unmapped_is_empty(co: dict[str, Any]) -> None:
    d = await _sys(co["tid"], "acct.ledger_lines", since=today() - timedelta(days=400), until=today())
    rent = [r for r in d["rows"] if r["account"] == "expense:thue_van_phong"]
    assert rent and all(r["mapped"] == "6422" for r in rent) and all(r["party"] is None for r in rent)
    assert d["accounts"]["expense:thue_van_phong"]["mapped"] == "6422"
    assert all(isinstance(r["mapped"], str) and r["mapped"] != "????" for r in d["rows"])


async def test_balances_equal_postings_through_the_day(co: dict[str, Any]) -> None:
    tid = co["tid"]
    d = await _sys(tid, "acct.ledger_balances", on=today())
    got = {(r["entity"], r["account"]): r["balance_vnd"] for r in d["rows"]}
    assert all(v for v in got.values())                      # chỉ dòng khác 0
    assert got == await _books(tid, date(2000, 1, 1), today())
    assert sum(got.values()) == 0                            # cả sổ cân
    before = await _sys(tid, "acct.ledger_balances", on=today() - timedelta(days=4000))
    assert before["rows"] == []
    ent = await owner_val("SELECT code FROM core.legal_entities WHERE tenant_id = %s AND is_default", (tid,))
    one = await _sys(tid, "acct.ledger_balances", on=today(), entity_code=ent)
    assert one["entity"] == ent and {r["entity"] for r in one["rows"]} <= {ent}


async def test_bad_input_and_contract_shape(co: dict[str, Any]) -> None:
    with pytest.raises(Invalid):
        await _sys(co["tid"], "acct.ledger_lines", since=today(), until=today() - timedelta(days=1))
    with pytest.raises(Invalid):
        await _sys(co["tid"], "acct.ledger_lines", since=today(), until=today(), after="khong-dung-dang")
    for name in ("acct.ledger_lines", "acct.ledger_balances"):
        op = registry.OPS[name]
        assert op.internal and op.agent == "deny" and op.family == "ledger_read", name
    assert "ledger_read" in registry.OPEN_FAMILIES
