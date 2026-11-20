"""NGUỒN TIỀN của test lõi (ADR 0187): các đường tiền của Tài chính mở cho gói theo họ (``settlement`` — nghĩa vụ phải trả, lệnh chi; ``receivable`` — khoản phải thu; 0186) cần một
MODULE NGUỒN gọi bằng quyền hệ thống. Lõi không có gói ngành nào ⇒ test của lõi dùng một module giả (``testpay`` — chỉ là tên trong ``FAMILY_CALLERS``,
nguồn ``testpay.*``); test của gói chạy LẠI đúng các test đó với nguồn thật của gói (``use(monkeypatch, "<module gói>", "<tài khoản chi phí của gói>")``).
Hàm dựng đọc ``MODULE`` / ``EXPENSE`` lúc gọi — file test đặt bằng một fixture autouse gọi ``use``."""

import sys
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

import pytest

from barista.db import db
from barista.kernel import executor, ledger, registry
from barista.kernel.clock import today
from barista.kernel.executor import core_ctx
from barista.kernel.principal import Principal, elevate
from tests.conftest import Who, fresh_sale, ok, owner_val

MODULE = "testpay"
EXPENSE = "expense:marketing"
REVENUE, RECEIVABLE = "revenue:services", "receivable:customers"   # tài khoản doanh thu / phải thu của khoản phải thu doanh thu mẫu (``revenue``)
MAKER: Callable[[Who], Awaitable[dict[str, Any]]] | None = None     # gói: hàm dựng khoản phải thu doanh thu THẬT của gói (vd ký một giao dịch)
FAMILIES = ("settlement", "identity", "receivable", "ledger_ref", "follow_up")


def use(monkeypatch: pytest.MonkeyPatch, module: str = "testpay", expense: str = "expense:marketing", *, revenue: str = "revenue:services",
        receivable: str = "receivable:customers", maker: Callable[[Who], Awaitable[dict[str, Any]]] | None = None) -> None:
    """Đặt module nguồn cho các hàm dựng trong test này. Module không có thật ⇒ khai nó tiêu thụ các họ tiền (như ``m.consumes`` của một gói)."""
    me = sys.modules[__name__]
    for name, value in (("MODULE", module), ("EXPENSE", expense), ("REVENUE", revenue), ("RECEIVABLE", receivable), ("MAKER", maker)):
        monkeypatch.setattr(me, name, value)
    if module not in registry.MODULES:
        monkeypatch.setattr(registry, "FAMILY_CALLERS", {**registry.FAMILY_CALLERS,
                                                         **{f: {*registry.FAMILY_CALLERS.get(f, ()), module} for f in FAMILIES}})


async def revenue(who: Who, amount: int = 60_000_000, entity_code: str = "") -> dict[str, Any]:
    """Một khoản phải thu DOANH THU mới của nguồn đang dùng: ``{code, fee, sale, entity}`` (``sale`` = một sale mới tinh không có quyền kế toán;
    ``entity`` = mã pháp nhân ghi doanh thu — ``entity_code`` chọn pháp nhân, trống = mặc định). Nguồn giả của lõi: lập qua đúng hợp đồng ``acct.issue_receivable`` bằng quyền hệ thống."""
    if MAKER is not None:
        return await MAKER(who)
    kt = await who("ketoan")
    eid = await owner_val("SELECT id FROM core.legal_entities WHERE tenant_id = %s AND code = %s", (kt.tenant_id, entity_code)) if entity_code else None
    ref = uuid4().hex[:10].upper()
    code = f"DT-{ref}"
    r = await executor.run(elevate(kt), "acct.issue_receivable", {
        "source": f"{MODULE}.fee", "source_ref": ref, "label": "Phí dịch vụ thử", "party": f"Khách thử {ref[:4]}", "nature": "revenue",
        "amount_vnd": amount, "revenue_account": REVENUE, "receivable_account": RECEIVABLE, "due_on": today().isoformat(), "code": code, **({"entity_id": str(eid)} if eid else {})})
    assert r.status == "ok", r.error
    entity = await owner_val("SELECT e.code FROM acct.receivables r JOIN core.legal_entities e ON e.id = r.entity_id WHERE r.code = %s AND r.tenant_id = %s",
                             (code, kt.tenant_id))
    return {"code": code, "sale": await fresh_sale(who), "fee": amount, "entity": entity}


async def internal(p: Principal, name: str, **data: Any) -> Any:
    async with db.tenant_tx(p) as conn:
        return await core_ctx(conn, p, module=MODULE).call_system(name, **data)


async def party(who: Who) -> Any:
    admin = await who("admin")
    return (await ok(admin, "parties.create", display_name=f"Bên thử {uuid4().hex[:6]}", kind="organization"))["party_id"]


async def payable(who: Who, pid: Any = None, amount: int = 10_000_000, tax: int = 0, ref: str | None = None) -> tuple[Any, Any, dict[str, Any]]:
    admin = await who("admin")
    pid = pid or await party(who)
    ref = ref or uuid4().hex
    data: dict[str, Any] = dict(source=f"{MODULE}.payee", source_ref=ref, party_id=pid, amount_vnd=amount, tax_vnd=tax,
                                tax_basis={"fixture": True} if tax else {}, expense_account=EXPENSE, label="Quyền fixture", basis=ref)
    result = await internal(admin, "acct.recognize_payable", **data)
    return pid, result, data


async def approved(who: Who) -> Principal:
    admin, gd = await who("admin"), await who("giamdoc")
    assert gd.user_id is not None
    return elevate(admin, approved_by=(gd.user_id,))


async def prepared(who: Who, pid: Any, amount: int, ref: str | None = None, **kw: Any) -> Any:
    return await internal(await approved(who), "acct.prepare_payout", source=f"{MODULE}.payment", source_ref=ref or uuid4().hex, party_id=pid,
                          amount_vnd=amount, label="Chi fixture", **kw)


async def account(who: Who, code: str) -> int:
    p = await who("ketoan")
    async with db.tenant_tx(p) as conn:
        return await ledger.balance(core_ctx(conn, p), code)
