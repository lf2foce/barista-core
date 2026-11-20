"""0219 — hợp đồng chỉ đọc cho gói so với sổ cái: ``acct.ledger_month_totals`` (họ company_figures) cộng mọi đối tượng chi phí = đúng phát sinh sổ cái
theo (pháp nhân, tháng ngày hạch toán, tài khoản); phần chi phí của phiếu chia theo dòng phiếu, huỷ trừ ở tháng huỷ; ``acct.pnl_account`` /
``acct.cost_object`` (họ ledger_ref) chỉ trả danh mục. Dữ liệu giả, Postgres thật, công ty thử riêng."""

import uuid
from collections import defaultdict
from datetime import date
from typing import Any

import pytest_asyncio

from barista import seed
from barista.db import db
from barista.kernel import executor, registry
from barista.kernel.clock import today
from barista.kernel.errors import NotFound
from barista.kernel.principal import system_principal
from barista.modules.acct.expenses import attribute
from tests.conftest import approved, ok, owner_val

DOMAIN = "sosanh.demo"


@pytest_asyncio.fixture(scope="module")
async def co() -> dict[str, Any]:
    tid = await seed.create_tenant("Công ty thử so sổ cái", DOMAIN, people=[("giamdoc", "Giám đốc thử", "director", "HQ", True),
                                                                         ("ketoan", "Kế toán thử", "accountant", "KT", True)], packs=seed.ALL_PACKS)
    kt = await seed.who(tid, "ketoan", DOMAIN)
    for code, label, account in (("van_phong", "Thuê văn phòng", "expense:thue_van_phong"), ("quang_cao", "Quảng cáo", "expense:marketing")):
        await ok(kt, "acct.set_category", code=code, direction="out", label=label, account=account)
    await ok(kt, "acct.set_cost_object", code="CT-01", label="Công trình thử một", kind="Công trình")
    return {"tid": tid, "kt": kt}


async def _sys(tid: Any, name: str, **inp: Any) -> Any:
    """Gọi đúng như gói gọi: ``ctx.call_system`` từ một module khai tiêu thụ họ (ở đây mượn mgmt, chỉ trong lần gọi — gói thật khai ``m.consumes``)."""
    fam = registry.OPS[name].family
    assert fam
    had = "mgmt" in registry.FAMILY_CALLERS[fam]
    registry.FAMILY_CALLERS[fam].add("mgmt")
    try:
        async with db.tenant_tx(system_principal(tid)) as conn:
            cx = executor.core_ctx(conn, system_principal(tid), module="mgmt")
            return await cx.call_system(name, **inp)
    finally:
        if not had:
            registry.FAMILY_CALLERS[fam].discard("mgmt")


async def _ledger(tid: Any, a: date, b: date) -> dict[tuple[str, str, str], int]:
    rows = await owner_val(
        """SELECT coalesce(json_agg(x), '[]') FROM (SELECT e.code AS entity, to_char(j.posted_on, 'YYYY-MM') AS month, a.code AS account, a.kind,
                  sum(p.amount_vnd)::bigint AS v FROM ledger.journals j JOIN ledger.postings p ON p.journal_id = j.id JOIN ledger.accounts a ON a.id = p.account_id
                  JOIN core.legal_entities e ON e.id = j.entity_id
                  WHERE j.tenant_id = %s AND j.posted_on BETWEEN %s AND %s AND a.kind IN ('revenue', 'expense') GROUP BY 1, 2, 3, 4) x""", (tid, a, b))
    return {(r["entity"], r["month"], r["account"]): (-r["v"] if r["kind"] == "revenue" else r["v"]) for r in rows}


def test_attribution_always_sums_to_the_ledger() -> None:
    totals = [{"entity": "A", "month": "2026-01", "account": "expense:x", "kind": "expense", "amount_vnd": 100},
              {"entity": "A", "month": "2026-01", "account": "revenue:y", "kind": "revenue", "amount_vnd": 50}]
    parts = [{"entity": "A", "month": "2026-01", "account": "expense:x", "cost_object": "CT", "amount_vnd": 70},
             {"entity": "A", "month": "2026-01", "account": "expense:z", "cost_object": "CT", "amount_vnd": 9}]   # tài khoản không có trong kỳ / loại ⇒ bỏ
    rows = attribute(totals, parts)
    assert {(r["account"], r["cost_object"]): r["amount_vnd"] for r in rows} == {("expense:x", ""): 30, ("expense:x", "CT"): 70, ("revenue:y", ""): 50}


async def test_ledger_month_totals_split_by_cost_object_equals_the_ledger(co: dict[str, Any]) -> None:
    tid, kt = co["tid"], co["kt"]
    a = date(today().year, 1, 1)
    ref = "R" + uuid.uuid4().hex[:10].upper()
    await ok(kt, "acct.record_expense", supplier="Bên bán mẫu", ref=ref, lines=[
        {"category": "van_phong", "amount_vnd": 10_000_000, "cost_object": "CT-01"},
        {"category": "van_phong", "amount_vnd": 4_000_000},
        {"category": "quang_cao", "amount_vnd": 3_000_000, "cost_object": "ct-01"}])
    gone = await ok(kt, "acct.record_expense", category="quang_cao", amount_vnd=2_000_000, cost_object="CT-01", ref=ref + "X")
    await approved(kt, "acct.cancel_expense", bill_id=str(gone["bill_id"]), reason="Ghi trùng")

    d = await _sys(tid, "acct.ledger_month_totals", since=a, until=today())
    assert d["basis"] == "ledger" and d["entity"] == ""
    got: dict[tuple[str, str, str], int] = defaultdict(int)
    for r in d["rows"]:
        got[(r["entity"], r["month"], r["account"])] += r["amount_vnd"]
    books = _ledger_nonzero(await _ledger(tid, a, today()))
    assert {k: v for k, v in got.items() if v} == books           # cộng mọi đối tượng = sổ cái, đúng từng (pháp nhân, tháng, tài khoản)
    mth = f"{today():%Y-%m}"
    by = {(r["account"], r["cost_object"]): r["amount_vnd"] for r in d["rows"] if r["month"] == mth}
    assert by[("expense:thue_van_phong", "CT-01")] == 10_000_000
    assert by[("expense:marketing", "CT-01")] == 3_000_000         # phiếu đã huỷ trong tháng: +2tr rồi −2tr
    assert d["cost_objects"]["CT-01"] == "Công trình thử một" and d["accounts"]["expense:marketing"]["kind"] == "expense"
    # lọc loại: chỉ doanh thu ⇒ không dòng chi phí nào; lọc pháp nhân
    only_rev = await _sys(tid, "acct.ledger_month_totals", since=a, until=today(), kinds=["revenue"])
    assert all(r["kind"] == "revenue" for r in only_rev["rows"])
    ent = await owner_val("SELECT code FROM core.legal_entities WHERE tenant_id = %s AND is_default", (tid,))
    one = await _sys(tid, "acct.ledger_month_totals", since=a, until=today(), entity_code=ent)
    assert one["entity"] == ent and {r["entity"] for r in one["rows"]} <= {ent}


def _ledger_nonzero(d: dict[tuple[str, str, str], int]) -> dict[tuple[str, str, str], int]:
    return {k: v for k, v in d.items() if v}


async def test_catalog_contracts_return_only_the_catalog(co: dict[str, Any]) -> None:
    tid = co["tid"]
    assert await _sys(tid, "acct.pnl_account", value="Thuê văn phòng") == {"account": "expense:thue_van_phong", "name": "Thuê văn phòng", "kind": "expense"}
    assert (await _sys(tid, "acct.pnl_account", value="expense:marketing"))["kind"] == "expense"
    try:
        await _sys(tid, "acct.pnl_account", value="expense:khong_co_dau")
        raise AssertionError("tài khoản lạ phải bị từ chối")
    except NotFound:
        pass
    assert (await _sys(tid, "acct.cost_object", code="ct-01"))["code"] == "CT-01"
    for name, fam in (("acct.ledger_month_totals", "company_figures"), ("acct.pnl_account", "ledger_ref"), ("acct.cost_object", "ledger_ref")):
        op = registry.OPS[name]
        assert op.internal and op.agent == "deny" and op.family == fam, name
