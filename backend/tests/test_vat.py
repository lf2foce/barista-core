"""0165 — thuế GTGT đầu ra lát A: mức theo bộ quy định vn-vat tại ngày lập (khoảng giảm 2%), làm tròn, loại thuế là dữ liệu công ty, ghi sổ cân, huỷ =
bút toán đảo, điều chỉnh giảm qua đường giấy báo có, tổng hợp kỳ theo pháp nhân, quyền (sale không ghi, kế toán ghi, trợ lý chỉ đề xuất), công tắc mặc
định tắt, quy tắc chưa xác minh ⇒ chỉ xem trước. Dữ liệu giả, Postgres thật."""

import json
import shutil
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import pytest
import pytest_asyncio

from barista import seed
from barista.db import db
from barista.kernel import events, executor, regulations
from barista.kernel.clock import today
from barista.kernel.errors import Invalid
from barista.kernel.principal import Principal
from barista.modules.acct import _AR_DRIFT
from barista.modules.acct.vat import ROUNDING, period_bounds, rate_on, split
from tests import money_source
from tests.conftest import Who, approved, edge, ok, owner_val, q

SHIPPED = regulations.ROOT


# ── Hàm thuần: mức theo ngày, làm tròn ──

def test_rate_follows_invoice_date_and_reduction_window() -> None:
    assert rate_on("standard", True, date(2026, 12, 31)).rate_bps == 800              # trong khoảng giảm 2% (NQ 204/2025)
    assert rate_on("standard", True, date(2027, 1, 1)).rate_bps == 1000               # hết khoảng ⇒ về 10% không cần file mới
    assert rate_on("standard", True, date(2025, 7, 1)).reduced is True
    assert rate_on("standard", False, date(2026, 6, 1)).rate_bps == 1000              # loại công ty khai KHÔNG được giảm (vd kinh doanh BĐS)
    assert rate_on("five", True, date(2026, 6, 1)).rate_bps == 500
    assert rate_on("zero", False, date(2026, 6, 1)).rate_bps == 0
    exempt = rate_on("exempt", False, date(2026, 6, 1))
    assert exempt.rate_bps is None and exempt.line == "26" and not any(r.endswith(":ROUNDING") for r in exempt.refs)
    reduced = rate_on("standard", True, date(2026, 6, 1))
    assert reduced.refs == ("vn-vat@2025-07-01:RATE_STANDARD", "vn-vat@2025-07-01:REDUCTION", "vn-vat@2025-07-01:ROUNDING")
    assert reduced.label == "Thuế suất 8% (giảm 2%)"
    with pytest.raises(Invalid, match="Chưa có quy định"):
        rate_on("standard", False, date(2025, 6, 30))                                 # trước phiên bản đầu ⇒ không đoán luật cũ


def test_split_rounds_half_up_and_keeps_gross() -> None:
    assert split(1_234_567, 1000, "exclusive") == (1_234_567, 123_457, 1_358_024)     # 123.456,7 ⇒ 123.457
    assert split(5, 1000, "exclusive") == (5, 1, 6)                                  # 0,5 ⇒ lên 1
    assert split(4, 1000, "exclusive") == (4, 0, 4)
    base, tax, gross = split(1_000_001, 800, "inclusive")                            # 1.000.001 / 1,08 = 925.926,85 ⇒ 925.927
    assert (base, tax, gross) == (925_927, 74_074, 1_000_001)
    assert split(110_000_000, 1000, "inclusive") == (100_000_000, 10_000_000, 110_000_000)
    assert split(7_000_000, None, "exclusive") == (7_000_000, 0, 7_000_000)           # không chịu thuế
    assert split(7_000_000, 0, "inclusive") == (7_000_000, 0, 7_000_000)
    with pytest.raises(Invalid, match="làm tròn"):
        split(100, 1000, "exclusive", {"unit_vnd": 1000, "mode": "half_up", "per": "line"})
    assert ROUNDING == {"unit_vnd": 1, "mode": "half_up", "per": "line"}


def test_period_bounds_month_and_quarter() -> None:
    assert period_bounds("10/2026", date(2026, 11, 11))[:2] == (date(2026, 10, 1), date(2026, 10, 31))
    assert period_bounds("Q4/2026", date(2026, 11, 11))[:2] == (date(2026, 10, 1), date(2026, 12, 31))
    assert period_bounds("q1/2027", date(2026, 11, 11))[:2] == (date(2027, 1, 1), date(2027, 3, 31))
    # trống = tháng đang kê khai (rà vai kế toán 18/11/2026): tới hạn tờ khai GTGT tháng trước (bộ vn-tax: ngày 20 tháng sau) vẫn là tháng trước
    assert period_bounds("", date(2026, 2, 11))[:2] == (date(2026, 1, 1), date(2026, 1, 31))
    assert period_bounds("", date(2026, 2, 20))[:2] == (date(2026, 1, 1), date(2026, 1, 31))
    assert period_bounds("", date(2026, 2, 21))[:2] == (date(2026, 2, 1), date(2026, 2, 28))
    with pytest.raises(Invalid):
        period_bounds("13/2026", date(2026, 11, 11))


# ── Ghi thật trên dữ liệu mẫu ──

@pytest.fixture
def verified_law(tmp_path: Path) -> Iterator[None]:
    """Bản sao bộ quy định đang phát hành, riêng vn-vat ghi "đã xác minh" (như sau khi kế toán soát — runbook 13) ⇒ lần ghi thật chạy được."""
    root = tmp_path / "regulations"
    shutil.copytree(SHIPPED, root)
    f = root / "vn-vat" / "2025-07-01.json"
    raw = json.loads(f.read_text(encoding="utf-8"))
    raw["status"], raw["verified_by"] = "verified", "Kế toán thử — 11/11/2026"
    for p in raw["params"].values():
        p["status"], p["verified_by"] = "verified", "Kế toán thử — 11/11/2026"
    f.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    regulations.reload(root)
    yield
    regulations.reload(SHIPPED)


@pytest_asyncio.fixture
async def vat_on(who: Who) -> AsyncIterator[None]:
    admin = await who("admin")
    await ok(admin, "admin.set_setting", key="acct.vat_enabled", enabled=True)
    yield
    await ok(admin, "admin.set_setting", key="acct.vat_enabled", enabled=False)


async def _fee(who: Who) -> dict[str, Any]:
    """Một khoản phải thu doanh thu mới của nguồn tiền đang dùng (``tests/money_source.py``): lõi — nguồn giả, doanh thu revenue:services; test của
    gói chạy lại file này với khoản phí THẬT của gói (ký một giao dịch)."""
    return await money_source.revenue(who)


@pytest.fixture(autouse=True)
def _source(monkeypatch: pytest.MonkeyPatch) -> None:
    money_source.use(monkeypatch)


async def _bal(code: str, account: str) -> int:
    """Số dư một tài khoản theo sổ cái, chỉ các bút toán mang mã khoản này (dương = nợ)."""
    return int(await owner_val("""SELECT coalesce(sum(p.amount_vnd), 0) FROM ledger.postings p JOIN ledger.journals j ON j.id = p.journal_id
                                  JOIN ledger.accounts a ON a.id = p.account_id WHERE j.source_ref = %s AND a.code = %s""", (code, account)))


async def _ar(code: str) -> dict[str, Any]:
    return await owner_val("""SELECT json_build_object('amount', amount_vnd, 'fee', fee_vnd, 'paid', paid_vnd, 'credited', credited_vnd, 'status', status,
                                                       'invoice_no', invoice_no) FROM acct.receivables WHERE code = %s""", (code,))


async def _no_drift(code: str) -> None:
    assert await owner_val("SELECT count(*) FROM (" + _AR_DRIFT.format(cols="r.code") + ") d WHERE d.code = %s", (code,)) == 0


def _sym() -> str:
    return "1C26T" + uuid.uuid4().hex[:2].upper().replace("0", "A").replace("1", "B")


def _num() -> str:
    return str(uuid.uuid4().int % 10**7).zfill(7)


async def _refused(p: Principal, name: str, inp: dict[str, Any]) -> str:
    """Lệnh qua chuỗi duyệt bị từ chối lúc chạy sau duyệt (hoặc ngay lúc xin) ⇒ trả lỗi."""
    r = await executor.run(p, name, inp)
    if r.status == "pending_approval":
        gd = await seed.who(p.tenant_id, "giamdoc")
        d = await executor.run(edge(gd), "core.decide", {"approval_id": str(r.approval_id), "approve": True})
        assert d.status == "ok" and d.output["status"] != "executed", d.output
        return str(d.output.get("error") or d.output)
    assert r.status in ("invalid", "denied", "conflict", "not_found"), (r.status, r.error)
    return str(r.error)


async def _category(who: Who, code: str, **kw: Any) -> None:
    await ok(await who("ketoan"), "acct.set_tax_category", code=code, label=f"Loại thử {code}", **kw)


async def test_switch_off_by_default_and_unverified_rules_only_preview(who: Who) -> None:
    ketoan = await who("ketoan")
    f = await _fee(who)
    await _category(who, "thu_tat", treatment="standard", price_mode="exclusive")
    # công tắc mặc định TẮT: không ghi được, khoản phải thu không có cột / nút hoá đơn GTGT (bản demo không đổi)
    r = await executor.run(ketoan, "acct.issue_vat_invoice", {"code": f["code"], "category": "thu_tat", "symbol": _sym(), "number": _num()})
    assert r.status == "invalid" and "chưa bật" in str(r.error)
    page = await q(ketoan, "acct.receivables", status="", q=f["code"])
    assert page["rows"][0]["vat"] == "" and "vat" not in {c["key"] for c in page["columns"]}
    admin = await who("admin")
    await ok(admin, "admin.set_setting", key="acct.vat_enabled", enabled=True)
    try:
        # bộ quy định phát hành đang GIẢ ĐỊNH ⇒ xem trước được (ghi rõ), ghi thật bị chặn — không đổi sổ
        card = await q(ketoan, "acct.vat_preview", code=f["code"], category="thu_tat")
        assert card["blocks"][0]["items"][1]["value"] == f["fee"] // 10
        assert any(b.get("tone") == "warn" for b in card["blocks"])
        r = await executor.run(ketoan, "acct.issue_vat_invoice", {"code": f["code"], "category": "thu_tat", "symbol": _sym(), "number": _num()})
        assert r.status == "invalid" and "chưa được kế toán xác minh" in str(r.error)
        assert await _bal(f["code"], "tax:vat_output") == 0 and (await _ar(f["code"]))["amount"] == f["fee"]
        assert (await q(ketoan, "acct.receivables", status="", q=f["code"]))["rows"][0]["vat"] == "ready"
    finally:
        await ok(admin, "admin.set_setting", key="acct.vat_enabled", enabled=False)


async def test_exclusive_invoice_posts_balanced_entries_then_adjustment_reduces_through_credit_path(who: Who, verified_law: None, vat_on: None) -> None:
    ketoan = await who("ketoan")
    f = await _fee(who)
    code, fee = f["code"], f["fee"]
    # kinh doanh BĐS không được giảm 2% (danh mục loại trừ) ⇒ công ty khai loại 10%, áp mặc định cho doanh thu phí môi giới
    await _category(who, "moi_gioi_t", treatment="standard", reducible=False, price_mode="exclusive", applies_to=money_source.REVENUE,
                    note="Thử — kế toán xác nhận ngành BĐS không được giảm")
    sym, num = _sym(), _num()
    out = await ok(ketoan, "acct.issue_vat_invoice", code=code, symbol=sym, number=num)   # không chọn loại ⇒ loại áp mặc định cho tài khoản doanh thu của khoản
    tax = fee // 10
    assert (out["base_vnd"], out["tax_vnd"], out["gross_vnd"], out["rate_bps"]) == (fee, tax, fee + tax, 1000)
    # sổ cái: Nợ phải thu (gồm thuế) / Có doanh thu (chưa thuế) / Có thuế đầu ra — mỗi bút toán tổng 0 (DB kiểm), số dư theo khoản
    assert await _bal(code, money_source.RECEIVABLE) == fee + tax
    assert await _bal(code, money_source.REVENUE) == -fee
    assert await _bal(code, "tax:vat_output") == -tax
    assert await owner_val("""SELECT count(*) FROM (SELECT j.id FROM ledger.journals j JOIN ledger.postings p ON p.journal_id = j.id
                              WHERE j.source_ref = %s GROUP BY j.id HAVING sum(p.amount_vnd) <> 0) x""", (code,)) == 0
    ar = await _ar(code)
    assert (ar["amount"], ar["fee"], ar["invoice_no"]) == (fee + tax, fee + tax, f"{sym}-{num}")
    again = await ok(ketoan, "acct.issue_vat_invoice", code=code, symbol=sym, number=num)
    assert again["created"] is False and await _bal(code, "tax:vat_output") == -tax       # ghi lại cùng số ⇒ không ghi lần hai
    assert (await executor.run(ketoan, "acct.issue_vat_invoice", {"code": code, "symbol": sym, "number": _num()})).status == "conflict"
    # đường cũ không đi vòng qua thuế: giấy báo có / ghi số hoá đơn khác trên khoản đã có hoá đơn GTGT bị chặn
    assert "Điều chỉnh giảm" in await _refused(ketoan, "acct.credit_receivable", {"code": code, "amount_vnd": "1tr", "reason": "thử đường cũ"})
    assert (await executor.run(ketoan, "acct.record_invoice", {"code": code, "invoice_no": "1C26TZZ-99"})).status == "invalid"
    # bên trả chuyển đủ (gồm thuế) ⇒ đã thu đủ
    await ok(ketoan, "acct.record_payment", code=code, amount_vnd=fee + tax, bank_ref=f"VAT{uuid.uuid4().hex[:8]}")
    await events.drain()
    assert (await _ar(code))["status"] == "paid"

    # điều chỉnh giảm 11 triệu (đã gồm thuế) — qua phiếu duyệt; thuế tách theo mức của hoá đơn gốc: 10 triệu + 1 triệu
    adj = await approved(ketoan, "acct.adjust_vat_invoice", code=code, reduce_vnd="11tr", reason="CĐT giảm phí theo biên bản thử", symbol=sym,
                         number=_num())
    assert (adj["base_vnd"], adj["tax_vnd"], adj["released_vnd"]) == (10_000_000, 1_000_000, 11_000_000)
    ar = await _ar(code)
    assert (ar["amount"], ar["paid"], ar["credited"], ar["status"]) == (fee + tax - 11_000_000, fee + tax - 11_000_000, 11_000_000, "paid")
    assert await _bal(code, "tax:vat_output") == -(tax - 1_000_000)
    assert await _bal(code, money_source.REVENUE) == -(fee - 10_000_000)
    await _no_drift(code)
    # đã có điều chỉnh ⇒ không huỷ được (xử lý tiếp bằng điều chỉnh)
    assert "điều chỉnh" in await _refused(ketoan, "acct.cancel_vat_invoice", {"code": code, "reason": "thử huỷ sau điều chỉnh"})
    # tổng hợp kỳ của pháp nhân: phần bán ra 10% = hoá đơn − điều chỉnh, sổ cái khớp
    page = await q(ketoan, "acct.vat_summary", period=f"{today():%m/%Y}", entity_code=f["entity"])
    row = next(x for x in page["rows"] if x["id"] == "standard:1000")
    assert row["line"] == "32/33" and row["base_vnd"] >= fee - 10_000_000 and row["tax_vnd"] >= tax - 1_000_000
    inp = next(x for x in page["rows"] if x["id"] == "input")                             # đầu vào: từ phiếu chi phí đã khấu trừ (0170)
    assert inp["line"] == "25" and inp["tax_vnd"] >= 0 and "phiếu chi phí" in inp["note"]
    books = next(k for k in page["kpis"] if k["label"] == "Sổ cái thuế đầu ra")
    assert books["tone"] == "green"


async def test_inclusive_reduced_rate_cancel_is_reversal_and_replacement_gets_new_number(who: Who, verified_law: None, vat_on: None) -> None:
    ketoan = await who("ketoan")
    f = await _fee(who)
    code, fee = f["code"], f["fee"]
    await _category(who, "dv_giam_t", treatment="standard", reducible=True, price_mode="inclusive")
    sym, num = _sym(), _num()
    out = await ok(ketoan, "acct.issue_vat_invoice", code=code, category="dv_giam_t", symbol=sym, number=num, issued_on=today().isoformat())
    base, tax, _ = split(fee, 800, "inclusive")
    assert (out["rate_bps"], out["base_vnd"], out["tax_vnd"]) == (800, base, tax)          # hôm nay trong khoảng giảm (tới 31/12/2026)
    assert (await _ar(code))["amount"] == fee                                                # giá đã gồm thuế ⇒ phải thu không đổi
    assert await _bal(code, money_source.REVENUE) == -base and await _bal(code, "tax:vat_output") == -tax
    refs = await owner_val("SELECT rule_refs FROM acct.vat_invoices WHERE id = %s", (out["invoice_id"],))
    assert "vn-vat@2025-07-01:REDUCTION" in refs                                             # mức + quy tắc chụp lên hoá đơn

    # huỷ = phiếu duyệt rồi bút toán ĐẢO đúng bút toán thuế; bản ghi giữ nguyên (chỉ điền cột huỷ)
    r = await executor.run(ketoan, "acct.cancel_vat_invoice", {"code": code, "reason": "Sai tên bên mua"})
    assert r.status == "pending_approval"
    gone = await approved(ketoan, "acct.cancel_vat_invoice", code=code, reason="Sai tên bên mua")
    assert gone["reversed_tax_vnd"] == tax
    assert await _bal(code, "tax:vat_output") == 0 and await _bal(code, money_source.REVENUE) == -fee
    assert await owner_val("""SELECT count(*) FROM ledger.journals r JOIN ledger.journals o ON o.id = r.reverses
                              WHERE o.idempotency_key = %s""", (f"acct.vat:{out['invoice_id']}",)) == 1
    assert (await _ar(code))["invoice_no"] == ""
    # số đã cấp không cấp lại; hoá đơn thay thế mang số mới
    assert (await executor.run(ketoan, "acct.issue_vat_invoice", {"code": code, "category": "dv_giam_t", "symbol": sym, "number": num})).status == "conflict"
    rep = await ok(ketoan, "acct.issue_vat_invoice", code=code, category="dv_giam_t", symbol=sym, number=_num())
    assert rep["tax_vnd"] == tax and await _bal(code, "tax:vat_output") == -tax
    # rào cuối ở DB: hoá đơn chỉ thêm
    with pytest.raises(psycopg.errors.RaiseException, match="chỉ thêm"):
        async with db.owner_tx() as conn:
            await conn.execute("UPDATE acct.vat_invoices SET tax_vnd = 0 WHERE id = %s", (rep["invoice_id"],))
    with pytest.raises(psycopg.errors.RaiseException, match="không xoá"):
        async with db.owner_tx() as conn:
            await conn.execute("DELETE FROM acct.vat_invoices WHERE id = %s", (out["invoice_id"],))


async def test_exempt_invoice_has_no_tax_entry_and_reports_line_26(who: Who, verified_law: None, vat_on: None) -> None:
    ketoan = await who("ketoan")
    f = await _fee(who)
    await _category(who, "khong_chiu_t", treatment="exempt")
    out = await ok(ketoan, "acct.issue_vat_invoice", code=f["code"], category="khong_chiu_t", symbol=_sym(), number=_num())
    assert out["tax_vnd"] == 0 and out["rate_bps"] is None
    assert await owner_val("SELECT journal_id FROM acct.vat_invoices WHERE id = %s", (out["invoice_id"],)) is None
    page = await q(ketoan, "acct.vat_summary", period=f"Q{(today().month - 1) // 3 + 1}/{today().year}", entity_code=f["entity"])
    row = next(x for x in page["rows"] if x["id"] == "exempt:None")
    assert row["line"] == "26" and row["tax_vnd"] is None and row["base_vnd"] >= f["fee"]


async def test_permissions_sale_cannot_accountant_can_agent_only_proposes(who: Who, verified_law: None, vat_on: None) -> None:
    ketoan = await who("ketoan")
    f = await _fee(who)
    await _category(who, "quyen_t", treatment="standard", price_mode="exclusive")
    inp = {"code": f["code"], "category": "quyen_t", "symbol": _sym(), "number": _num()}
    assert (await executor.run(f["sale"], "acct.issue_vat_invoice", inp)).status == "denied"
    assert (await executor.run(await who("salesadmin"), "acct.issue_vat_invoice", inp)).status in ("denied", "not_found")
    assert (await executor.run(f["sale"], "acct.set_tax_category", {"code": "sale_t", "label": "x", "treatment": "exempt"})).status == "denied"
    assert (await executor.query(f["sale"], "acct.vat_summary", {})).status == "denied"
    # trợ lý của kế toán: chỉ thành thẻ đề xuất, sổ không đổi
    r = await executor.run(ketoan.as_agent(), "acct.issue_vat_invoice", inp)
    assert r.status == "proposed" and r.output["command"] == "acct.issue_vat_invoice"
    assert await owner_val("SELECT count(*) FROM acct.vat_invoices v JOIN acct.receivables r ON r.id = v.receivable_id WHERE r.code = %s", (f["code"],)) == 0
    assert (await executor.run(ketoan.as_agent(), "acct.set_tax_category", {"code": "agent_t", "label": "x", "treatment": "exempt"})).status == "proposed"
    # người bấm xác nhận = kế toán chạy đúng đầu vào đó
    await ok(ketoan, "acct.issue_vat_invoice", **r.output["input"])
    assert await _bal(f["code"], "tax:vat_output") == -(f["fee"] // 10)
    # huỷ do người xin không tự duyệt: kế toán gửi ⇒ phiếu chờ giám đốc
    assert (await executor.run(ketoan, "acct.cancel_vat_invoice", {"code": f["code"], "reason": "thử quyền"})).status == "pending_approval"


async def test_other_company_sees_no_invoices_or_categories(who: Who, verified_law: None, vat_on: None) -> None:
    ketoan = await who("ketoan")
    f = await _fee(who)
    await _category(who, "rls_t", treatment="standard", price_mode="exclusive")
    out = await ok(ketoan, "acct.issue_vat_invoice", code=f["code"], category="rls_t", symbol=_sym(), number=_num())
    # RLS (lưới cuối, không qua lệnh): kế toán thấy; công ty khác và sale (phạm vi own, khoản không phải của mình) không thấy dòng nào
    for p, n in ((ketoan, 1), (await who("admin", other=True), 0), (f["sale"], 0)):
        async with db.tenant_tx(p, read_only=True) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM acct.vat_invoices WHERE id = %s", (out["invoice_id"],))
            assert (row := await cur.fetchone()) is not None and row["n"] == n, p.email
            cur = await conn.execute("SELECT count(*) AS n FROM acct.tax_categories WHERE code = 'rls_t'")
            assert (row := await cur.fetchone()) is not None and row["n"] == (1 if p is ketoan else 0), p.email
