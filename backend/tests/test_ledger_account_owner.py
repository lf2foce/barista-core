"""0188: tài khoản sổ cái đã khai có CHỦ — gói tiêu thụ họ settlement / receivable chỉ ghi vào tài khoản do chính gói khai hoặc sơ đồ nền của Tài chính."""
import uuid

import pytest

from barista.db import db
from barista.kernel import ledger, registry
from barista.kernel.errors import Denied
from barista.kernel.executor import core_ctx
from tests import money_source
from tests.conftest import Who


def test_every_declared_account_has_an_owner():
    assert set(ledger.OWNERS) == set(ledger.ACCOUNTS)
    assert all(ledger.OWNERS.values())


def test_owner_is_the_declaring_code_root():
    assert ledger.code_root("barista.modules.acct.vat") == "barista.modules.acct"
    assert ledger.code_root("barista.modules.pay") == "barista.modules.pay"
    assert ledger.code_root("barista_pack_hosting.leases") == "barista_pack_hosting"
    assert ledger.OWNERS["expense:payroll"] == {"barista.modules.pay"}
    assert ledger.SHARED_ROOT in ledger.OWNERS["revenue:services"]


def test_a_module_may_use_its_own_accounts_and_the_finance_chart_only():
    assert ledger.usable_by("expense:payroll", "pay")
    assert ledger.usable_by("revenue:services", "pay")            # sơ đồ nền
    assert not ledger.usable_by("expense:payroll", "crm")         # của gói khác
    assert not ledger.usable_by("expense:payroll", "khong_co")    # module lạ
    assert not ledger.usable_by("expense:khong_khai", "pay")      # tài khoản chưa khai


def test_no_family_consumer_books_to_another_packs_account():
    """Mọi module đang khai dùng settlement / receivable: tài khoản chi phí / doanh thu / phải thu nó tự khai phải dùng được bởi chính nó."""
    consumers = registry.FAMILY_CALLERS["settlement"] | registry.FAMILY_CALLERS["receivable"]
    for mod in consumers:
        roots = {ledger.code_root(op.handler.__module__) for name, op in registry.OPS.items() if name.startswith(mod + ".")}
        for code, owners in ledger.OWNERS.items():
            if owners & roots:
                assert ledger.usable_by(code, mod), (mod, code)


async def test_family_consumer_is_refused_on_another_packs_account(who: Who, monkeypatch: pytest.MonkeyPatch):
    """Qua hợp đồng thật: một module nguồn (tiêu thụ settlement + receivable) ghi vào tài khoản của gói khác ⇒ từ chối; sơ đồ nền ⇒ qua cửa này."""
    money_source.use(monkeypatch)
    mod = money_source.MODULE
    gd = await who("giamdoc")
    pay = {"source": f"{mod}.payout", "source_ref": "x", "party_id": str(uuid.uuid4()), "amount_vnd": 1_000_000, "label": "Thử", "basis": "x"}
    fee = {"source": f"{mod}.fee", "source_ref": "x", "label": "Thử", "party": "Khách", "amount_vnd": 1_000_000, "nature": "revenue", "due_on": "2026-12-01"}
    async with db.tenant_tx(gd, rollback=True) as conn:
        ctx = core_ctx(conn, gd, module=mod)
        for op in ("acct.recognize_payable", "acct.recognize_recovery"):
            with pytest.raises(Denied, match="do gói khác khai"):
                await ctx.call_system(op, **pay, expense_account="expense:payroll")
        monkeypatch.setitem(ledger.OWNERS, "revenue:services", {"barista_pack_khac"})   # như thể tài khoản này do một gói khác khai
        with pytest.raises(Denied, match="do gói khác khai"):
            await ctx.call_system("acct.issue_receivable", **fee, revenue_account="revenue:services")
        monkeypatch.setitem(ledger.OWNERS, "revenue:services", {ledger.SHARED_ROOT})
        assert ledger.usable_by(money_source.EXPENSE, mod)   # tài khoản chi phí của nguồn test thuộc sơ đồ nền
        for op in ("acct.recognize_payable", "acct.recognize_recovery"):   # sơ đồ nền: lỗi (nếu có) không phải lỗi chủ tài khoản
            try:
                await ctx.call_system(op, **pay, expense_account=money_source.EXPENSE)
            except Exception as e:
                assert "do gói khác khai" not in str(e)


async def test_a_pack_marks_its_own_mechanism_account_and_finance_leaves_it_out_of_categories(who: Who, monkeypatch: pytest.MonkeyPatch):
    """0189: tài khoản do một quy tắc của gói tự ghi (không phải mục người chọn) được CHÍNH GÓI đánh dấu lúc khai — danh mục khoản thu / chi mặc định
    bỏ nó và ``acct.set_category`` không cho trỏ vào, mà Tài chính không nêu mã của gói nào."""
    from barista.kernel import executor
    from barista.modules.acct import corrections

    monkeypatch.setattr(ledger, "ACCOUNTS", dict(ledger.ACCOUNTS))
    monkeypatch.setattr(ledger, "OWNERS", {k: set(v) for k, v in ledger.OWNERS.items()})
    monkeypatch.setattr(ledger, "MECHANISM", set(ledger.MECHANISM))
    assert {"revenue:opening_adjustment", "revenue:advance_charge"} <= ledger.MECHANISM <= set(ledger.ACCOUNTS)
    ledger.declare({"revenue:thu_tu_ghi": ("Phí tự ghi theo chính sách (thử)", "revenue"), "revenue:thu_ban_hang": ("Doanh thu bán hàng (thử)", "revenue")},
                   mechanism=("revenue:thu_tu_ghi",))
    with pytest.raises(ValueError, match="chính lần khai"):
        ledger.declare({"revenue:thu_khac_thu": ("Thu khác (thử)", "revenue")}, mechanism=("revenue:services",))   # không đánh dấu tài khoản của bên khác
    assert "revenue:services" not in ledger.MECHANISM
    cats = corrections.default_categories("in")
    assert "thu_ban_hang" in cats and not {"thu_tu_ghi", "opening_adjustment", "advance_charge"} & set(cats)
    ketoan = await who("ketoan")
    r = await executor.run(ketoan, "acct.set_category", {"code": "thu_tu_ghi", "direction": "in", "label": "Thử", "account": "revenue:thu_tu_ghi"})
    assert r.status == "invalid" and "revenue:thu_ban_hang" in (r.error or "") and "revenue:thu_tu_ghi," not in (r.error or "")
