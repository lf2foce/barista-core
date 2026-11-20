"""0170 — chi phí vận hành: phiếu chi phí / hoá đơn mua vào (đã trả ngay, ghi nợ nhà cung cấp rồi chi qua đường đề nghị chi sẵn có), huỷ = bút toán đảo qua
phiếu duyệt, khoá kỳ, quyền (sale không, kế toán có, trợ lý chỉ đề xuất), nhập lặp không ghi lần hai, thuế GTGT đầu vào chỉ khi công tắc bật + quy tắc đã
xác minh, báo cáo kết quả kinh doanh bằng đúng tổng sổ cái, tháng theo ngày của công ty. Dữ liệu giả, Postgres thật, công ty thử riêng (danh mục khoản chi
của công ty mẫu không bị đổi)."""

import json
import shutil
import uuid
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
import pytest
import pytest_asyncio

from barista import seed
from barista.db import db
from barista.kernel import executor, imports, regulations
from barista.kernel.clock import today
from barista.kernel.clock import today as clock_today  # xuất MISA mặc định = tháng đang kê khai (18/11/2026) ⇒ test nêu tháng này
from barista.kernel.errors import Invalid
from barista.kernel.principal import Principal
from barista.modules.acct.expenses import CASH, credit_decision, line_vat, pnl_from
from tests.conftest import approved, ok, owner_val, q

DOMAIN = "chiphi.demo"
SHIPPED = regulations.ROOT


@pytest_asyncio.fixture(scope="module")
async def co(demo: dict[str, Any]) -> dict[str, Any]:
    tid = await seed.create_tenant("Công ty thử chi phí", DOMAIN,
                                   people=[("giamdoc", "Giám đốc thử", "director", "HQ", True), ("admin", "Quản trị thử", "admin", "QT", True),
                                           ("ketoan", "Kế toán thử", "accountant", "KT", True), ("sale1", "Sale thử", "sale", "KD-A", False)],
                                   packs=seed.ALL_PACKS)   # chỉ gói của lõi: [[offering]] trên phiếu là chữ tự do (danh mục của gói ngành: test của gói)
    kt = await seed.who(tid, "ketoan", DOMAIN)
    for code, label, account in (("van_phong", "Thuê văn phòng", "expense:thue_van_phong"), ("quang_cao", "Quảng cáo", "expense:marketing"),
                                 ("dien_nuoc", "Điện nước", "expense:dien_nuoc")):
        await ok(kt, "acct.set_category", code=code, direction="out", label=label, account=account)
    await ok(kt, "acct.set_cost_object", code="CT-01", label="Công trình thử một", kind="Công trình")
    return {"tid": tid}


async def who(co: dict[str, Any], local: str) -> Principal:
    return await seed.who(co["tid"], local, DOMAIN)


async def _bal(tid: Any, account: str, *, journal_like: str = "") -> int:
    return int(await owner_val("""SELECT coalesce(sum(p.amount_vnd), 0) FROM ledger.postings p JOIN ledger.accounts a ON a.id = p.account_id
                                  JOIN ledger.journals j ON j.id = p.journal_id
                                  WHERE a.tenant_id = %s AND a.code = %s AND j.idempotency_key LIKE %s""", (tid, account, journal_like or "%")))


async def _journal_sums(bill_id: Any) -> list[int]:
    return [int(x) for x in await owner_val("""SELECT coalesce(array_agg(s ORDER BY k), '{}') FROM (SELECT j.idempotency_key k, sum(p.amount_vnd) s
                                               FROM ledger.journals j JOIN ledger.postings p ON p.journal_id = j.id
                                               WHERE j.idempotency_key IN (%s, %s) GROUP BY 1) x""", (f"acct.bill:{bill_id}", f"acct.bill_cancel:{bill_id}"))]


def _ref() -> str:
    return "R" + uuid.uuid4().hex[:10].upper()


# ── Hàm thuần ──

def test_line_vat_uses_invoice_tax_or_splits_from_gross() -> None:
    assert line_vat(11_000_000, "10", None) == (1000, 1_000_000)
    assert line_vat(10_800_000, "8", None) == (800, 800_000)
    assert line_vat(5_000_000, "10", 454_545) == (1000, 454_545)          # số trên hoá đơn bên bán là căn cứ
    assert line_vat(3_000_000, "", None) == (None, 0)
    with pytest.raises(Invalid):
        line_vat(1_000, "", 1_000)


class _V:
    def __init__(self, params: dict[str, Any]) -> None:
        self.params, self.ref = params, "vn-vat@2025-07-01"


def test_input_credit_is_closed_unless_switch_on_and_rule_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    rule = {"INPUT_CREDIT": {"value": {"invoice_required": True, "noncash_from_vnd": 5_000_000}}}
    assert credit_decision(enabled=True, version=_V(rule), doc_no="X", vat=0, total=1, pay_account="cash:bank")[0] == "none"
    assert credit_decision(enabled=False, version=_V(rule), doc_no="X", vat=10, total=110, pay_account="cash:bank")[0] == "in_cost"
    assert credit_decision(enabled=True, version=_V({}), doc_no="X", vat=10, total=110, pay_account="cash:bank")[0] == "in_cost"
    # bộ phát hành: quy tắc GIẢ ĐỊNH ⇒ nằm trong chi phí, ghi mã quy tắc đã xét
    t, note, refs = credit_decision(enabled=True, version=_V(rule), doc_no="X", vat=10, total=110, pay_account="cash:bank")
    assert t == "in_cost" and "chưa được kế toán xác minh" in note and refs == ["vn-vat@2025-07-01:INPUT_CREDIT"]
    from barista.modules.acct import expenses as mod

    monkeypatch.setattr(mod.regulations, "require_verified", lambda refs: [])
    assert credit_decision(enabled=True, version=_V(rule), doc_no="X", vat=10, total=110, pay_account="cash:bank")[0] == "input"
    assert credit_decision(enabled=True, version=_V(rule), doc_no="", vat=10, total=110, pay_account="cash:bank")[0] == "in_cost"
    assert credit_decision(enabled=True, version=_V(rule), doc_no="X", vat=500_000, total=5_500_000, pay_account=CASH)[0] == "in_cost"
    assert credit_decision(enabled=True, version=_V(rule), doc_no="X", vat=10, total=4_999_999, pay_account=CASH)[0] == "input"


def test_pnl_from_signs_and_totals() -> None:
    e = uuid.uuid4()
    rows = [{"entity_id": e, "month": "2026-01", "code": "revenue:services", "name": "DT", "kind": "revenue", "amount_vnd": -100},
            {"entity_id": e, "month": "2026-02", "code": "revenue:services", "name": "DT", "kind": "revenue", "amount_vnd": -50},
            {"entity_id": e, "month": "2026-01", "code": "expense:x", "name": "CP", "kind": "expense", "amount_vnd": 30}]
    d = pnl_from(rows, ["2026-01", "2026-02"], {str(e): {"code": "A", "name": "Pháp nhân A"}})
    assert d["revenue"]["total"] == 150 and d["expenses"]["total"] == 30
    assert d["operating_profit"] == {"by_month": {"2026-01": 70, "2026-02": 50}, "total": 120}
    assert d["by_entity"][0] == {"entity": "A", "name": "Pháp nhân A", "revenue": 150, "expenses": 30, "operating_profit": 120,
                                 "by_month": {"2026-01": 70, "2026-02": 50}}


# ── Ghi thật ──

async def test_paid_now_posts_balanced_entry_with_dimensions_and_reversal_cancels_through_approval(co: dict[str, Any]) -> None:
    kt = await who(co, "ketoan")
    out = await ok(kt, "acct.record_expense", category="Thuê văn phòng", amount_vnd="22tr", vat_rate="10", supplier="Công ty Cho thuê Mẫu",
                   doc_no="0000123", memo="Thuê văn phòng tháng này", team_code="kd-a", cost_object="ct-01", ref=_ref())
    bid = out["bill_id"]
    # công tắc GTGT tắt ⇒ thuế nằm trong chi phí, phiếu ghi rõ
    assert (out["vat_vnd"], out["input_vnd"], out["vat_treatment"]) == (2_000_000, 0, "in_cost") and "chưa bật" in out["vat_note"]
    assert await _journal_sums(bid) == [0]
    acc = await owner_val("SELECT name FROM ledger.accounts WHERE tenant_id = %s AND code = 'expense:thue_van_phong'", (co["tid"],))
    assert acc == "Thuê văn phòng"                                                      # tài khoản chi phí riêng của công ty, tạo ở lần ghi đầu
    assert await _bal(co["tid"], "expense:thue_van_phong", journal_like=f"acct.bill:{bid}") == 22_000_000
    assert await _bal(co["tid"], "cash:bank", journal_like=f"acct.bill:{bid}") == -22_000_000
    line = await owner_val("""SELECT json_build_object('offering', offering_code, 'cost', cost_object, 'team', t.code) FROM acct.bill_lines l
                              LEFT JOIN core.teams t ON t.id = l.team_id WHERE l.bill_id = %s""", (bid,))
    assert line == {"offering": "", "cost": "CT-01", "team": "KD-A"}
    # công ty chưa bật gói nào khai danh mục [[offering]] ⇒ ô đó không nhận chữ (không ghi chiều phân tích không ai kiểm được)
    r = await executor.run(kt, "acct.record_expense", {"category": "dien_nuoc", "amount_vnd": "1tr", "offering_code": "DAT"})
    assert r.status == "invalid" and "chưa bật gói nào" in str(r.error)
    # chiều chi phí phải có trong danh mục
    for bad in ({"cost_object": "KHONGCO"}, {"team_code": "KHONGCO"}, {"category": "khong_co"}):
        r = await executor.run(kt, "acct.record_expense", {"category": "dien_nuoc", "amount_vnd": "1tr", **bad})
        assert r.status in ("invalid", "not_found"), (bad, r.status)
    page = await q(kt, "acct.expenses", q="Cho thuê Mẫu")
    assert any(r["id"] == bid and r["state"] == "active" for r in page["rows"])
    # huỷ = phiếu duyệt (người xin không tự duyệt) rồi bút toán ĐẢO
    r = await executor.run(kt, "acct.cancel_expense", {"bill_id": str(bid), "reason": "Ghi trùng"})
    assert r.status == "pending_approval"
    done = await approved(kt, "acct.cancel_expense", bill_id=str(bid), reason="Ghi trùng")
    assert done["created"] is True
    assert await _journal_sums(bid) == [0, 0]
    assert await _bal(co["tid"], "expense:thue_van_phong", journal_like=f"acct.bill%{bid}") == 0
    assert await owner_val("SELECT cancelled_on FROM acct.bills WHERE id = %s", (bid,)) == today()
    # rào cuối ở DB: phiếu / dòng chỉ thêm
    with pytest.raises(psycopg.errors.RaiseException, match="chỉ thêm"):
        async with db.owner_tx() as conn:
            await conn.execute("UPDATE acct.bills SET total_vnd = 1 WHERE id = %s", (bid,))
    with pytest.raises(psycopg.errors.RaiseException, match="chỉ thêm"):
        async with db.owner_tx() as conn:
            await conn.execute("DELETE FROM acct.bill_lines WHERE bill_id = %s", (bid,))


async def test_payable_then_settled_by_the_existing_payment_path(co: dict[str, Any]) -> None:
    kt = await who(co, "ketoan")
    supplier = f"Công ty Dịch vụ Mẫu {uuid.uuid4().hex[:4]}"
    out = await ok(kt, "acct.record_expense", category="quang_cao", amount_vnd="15tr", mode="payable", supplier=supplier, supplier_tax_code="0109999999",
                   doc_no="QC-01", due_on=(today() + timedelta(days=30)).isoformat())
    bid, pid = out["bill_id"], out["payable_id"]
    party = await owner_val("SELECT party_id FROM acct.payables WHERE id = %s", (pid,))
    assert await _bal(co["tid"], f"payable:{party}") == -15_000_000
    # cùng bên bán (theo tên) dùng lại đúng định danh — không tạo trùng
    again = await ok(kt, "acct.record_expense", category="quang_cao", amount_vnd="1tr", mode="payable", supplier=supplier, doc_no="QC-02")
    assert await owner_val("SELECT party_id FROM acct.payables WHERE id = %s", (again["payable_id"],)) == party
    assert await owner_val("SELECT count(*) FROM parties.parties WHERE tenant_id = %s AND display_name = %s", (co["tid"], supplier)) == 1
    # chi qua đường sẵn có: đề nghị chi → giám đốc duyệt → lệnh chi → xác nhận chuyển khoản
    res = await approved(kt, "acct.request_payment", payable_id=str(pid), amount_vnd="15tr", recipient_instruction="0101000999 — Ngân hàng Mẫu",
                         reason="Trả tiền quảng cáo")
    await ok(kt, "acct.confirm_payout", payout_id=str(res["payout_id"]), bank_ref=f"CK{uuid.uuid4().hex[:8]}")
    assert await _bal(co["tid"], f"payable:{party}") == -1_000_000                       # còn đúng phiếu thứ hai
    left = await owner_val("SELECT amount_vnd - coalesce((SELECT sum(amount_vnd) FROM acct.payout_allocations WHERE payable_id = p.id), 0) FROM acct.payables p"
                           " WHERE id = %s", (pid,))
    assert left == 0
    # đã có lệnh chi ⇒ không huỷ được phiếu ở đây
    r = await executor.run(kt, "acct.cancel_expense", {"bill_id": str(bid), "reason": "thử"})
    assert r.status == "pending_approval"
    gd = await who(co, "giamdoc")
    from tests.conftest import edge
    d = await executor.run(edge(gd), "core.decide", {"approval_id": str(r.approval_id), "approve": True})
    assert d.status == "ok" and d.output["status"] != "executed" and "lệnh chi" in str(d.output.get("error"))
    # phiếu ghi nợ chưa chi ⇒ huỷ được, nghĩa vụ về 0
    await approved(kt, "acct.cancel_expense", bill_id=str(again["bill_id"]), reason="Bên bán huỷ dịch vụ")
    assert await _bal(co["tid"], f"payable:{party}") == 0
    page = await q(kt, "acct.payables")
    assert next(x for x in page["rows"] if x["id"] == again["payable_id"])["left_vnd"] == 0


async def test_permissions_sale_cannot_accountant_can_agent_only_proposes(co: dict[str, Any]) -> None:
    kt, sale, gd = await who(co, "ketoan"), await who(co, "sale1"), await who(co, "giamdoc")
    inp = {"category": "dien_nuoc", "amount_vnd": "2tr", "ref": _ref()}
    assert (await executor.run(sale, "acct.record_expense", inp)).status == "denied"
    assert (await executor.run(gd, "acct.record_expense", inp)).status == "denied"         # giám đốc chỉ xem Kế toán
    assert (await executor.query(sale, "acct.expenses", {})).status == "denied"
    assert (await executor.query(sale, "acct.pnl_data", {})).status == "denied"
    assert (await executor.query(gd, "acct.pnl_data", {})).status == "ok"
    r = await executor.run(kt.as_agent(), "acct.record_expense", inp)
    assert r.status == "proposed" and r.output["command"] == "acct.record_expense"
    assert await owner_val("SELECT count(*) FROM acct.bills WHERE tenant_id = %s AND ref = %s", (co["tid"], inp["ref"])) == 0
    await ok(kt, "acct.record_expense", **r.output["input"])
    assert await owner_val("SELECT count(*) FROM acct.bills WHERE tenant_id = %s AND ref = %s", (co["tid"], inp["ref"])) == 1
    # RLS (lưới cuối): sale và công ty khác không thấy dòng nào
    for p, n in ((kt, 1), (sale, 0), (await seed.who(co["tid"], "giamdoc", DOMAIN), 1)):
        async with db.tenant_tx(p, read_only=True) as conn:
            row = await (await conn.execute("SELECT count(*) AS n FROM acct.bills WHERE ref = %s", (inp["ref"],))).fetchone()
            assert row is not None and row["n"] == n, p.email
    async with db.tenant_tx(await seed.who(co["tid"], "admin", DOMAIN), read_only=True) as conn:
        row = await (await conn.execute("SELECT count(*) AS n FROM acct.bill_lines")).fetchone()
        assert row is not None and row["n"] == 0                                         # quản trị không có quyền Kế toán


async def test_import_is_idempotent_by_ref_and_by_supplier_document(co: dict[str, Any]) -> None:
    kt = await who(co, "ketoan")
    refs = [_ref(), _ref()]
    rows = [{"Ngày": "02/11/2026" if today() >= date(2026, 11, 2) else today().strftime("%d/%m/%Y"), "Số tiền": "1.500.000", "Khoản mục": "Điện nước",
             "Bộ phận": "KT", "Đối tượng chi phí": "CT-01", "Mã dòng": refs[0]},
            {"Ngày": today().strftime("%d/%m/%Y"), "Số tiền": "800.000", "Khoản mục": "dien_nuoc", "Mã dòng": refs[1], "Diễn giải": "Nước"}]
    first = await imports.run_import(kt, "acct.record_expense", rows, dry_run=False)
    assert first.output["ok"] == 2 and not first.output["unknown_columns"], first.output
    second = await imports.run_import(kt, "acct.record_expense", rows, dry_run=False)
    assert second.output["ok"] == 2
    assert await owner_val("SELECT count(*) FROM acct.bills WHERE tenant_id = %s AND ref = ANY(%s)", (co["tid"], refs)) == 2
    # gõ lại qua lệnh (không qua đường nhập): cùng mã dòng cùng nội dung ⇒ không ghi lần hai; khác nội dung ⇒ xung đột
    same = await ok(kt, "acct.record_expense", category="dien_nuoc", amount_vnd="800.000", ref=refs[1], memo="Nước")
    assert same["created"] is False
    assert (await executor.run(kt, "acct.record_expense", {"category": "dien_nuoc", "amount_vnd": "900.000", "ref": refs[1]})).status == "conflict"
    # khoá tự nhiên khi không có mã dòng: bên bán + số chứng từ + pháp nhân
    doc = f"HD{uuid.uuid4().hex[:6].upper()}"
    a = await ok(kt, "acct.record_expense", category="dien_nuoc", amount_vnd="3tr", supplier="Công ty Điện Mẫu", doc_no=doc)
    b = await ok(kt, "acct.record_expense", category="dien_nuoc", amount_vnd="3tr", supplier="Công ty Điện Mẫu", doc_no=doc.lower())
    assert a["bill_id"] == b["bill_id"] and b["created"] is False


async def test_period_lock_blocks_recording_and_cancelling(co: dict[str, Any]) -> None:
    kt, admin = await who(co, "ketoan"), await who(co, "admin")
    code = "EXP" + uuid.uuid4().hex[:3].upper()
    await ok(admin, "admin.upsert_entity", code=code, name=f"Pháp nhân thử {code}")
    d = today() - timedelta(days=5)
    early = await ok(kt, "acct.record_expense", category="dien_nuoc", amount_vnd="1tr", entity_code=code, bill_on=(d - timedelta(days=1)).isoformat())
    await ok(kt, "acct.lock_period", entity_code=code, locked_through=d.isoformat())
    r = await executor.run(kt, "acct.record_expense", {"category": "dien_nuoc", "amount_vnd": "1tr", "entity_code": code, "bill_on": d.isoformat()})
    assert r.status == "invalid" and "khoá" in str(r.error)
    await ok(kt, "acct.record_expense", category="dien_nuoc", amount_vnd="1tr", entity_code=code, bill_on=(d + timedelta(days=1)).isoformat())
    # huỷ phiếu của kỳ đã khoá: bút toán đảo nằm ở kỳ còn mở (hôm nay) được; chọn ngày trong kỳ khoá ⇒ từ chối
    r = await executor.run(kt, "acct.cancel_expense", {"bill_id": str(early["bill_id"]), "reason": "thử khoá", "cancelled_on": d.isoformat()})
    if r.status == "pending_approval":
        gd = await who(co, "giamdoc")
        from tests.conftest import edge
        dd = await executor.run(edge(gd), "core.decide", {"approval_id": str(r.approval_id), "approve": True})
        assert dd.output["status"] != "executed" and "khoá" in str(dd.output.get("error"))
    else:
        assert r.status == "invalid"
    await approved(kt, "acct.cancel_expense", bill_id=str(early["bill_id"]), reason="thử khoá")
    assert await _journal_sums(early["bill_id"]) == [0, 0]


@pytest.fixture
def verified_law(tmp_path: Path) -> Iterator[None]:
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


async def test_input_vat_posts_only_with_switch_and_verified_rule_and_shows_in_vat_summary(co: dict[str, Any], verified_law: None) -> None:
    kt, admin = await who(co, "ketoan"), await who(co, "admin")
    await ok(admin, "admin.set_setting", key="acct.vat_enabled", enabled=True)
    try:
        out = await ok(kt, "acct.record_expense", category="quang_cao", amount_vnd="11tr", vat_rate="10", doc_no=f"V{uuid.uuid4().hex[:6]}",
                       supplier="Công ty Quảng cáo Mẫu")
        bid = out["bill_id"]
        assert (out["vat_treatment"], out["input_vnd"]) == ("input", 1_000_000)
        assert await _bal(co["tid"], "tax:vat_input", journal_like=f"acct.bill:{bid}") == 1_000_000
        assert await _bal(co["tid"], "expense:marketing", journal_like=f"acct.bill:{bid}") == 10_000_000
        refs = await owner_val("SELECT rule_refs FROM acct.bills WHERE id = %s", (bid,))
        assert refs == ["vn-vat@2025-07-01:INPUT_CREDIT"]
        # không số hoá đơn ⇒ không khấu trừ; tiền mặt từ ngưỡng ⇒ không khấu trừ
        no_doc = await ok(kt, "acct.record_expense", category="quang_cao", amount_vnd="1,1tr", vat_rate="10")
        assert no_doc["vat_treatment"] == "in_cost" and "số hoá đơn" in no_doc["vat_note"]
        cash = await ok(kt, "acct.record_expense", category="quang_cao", amount_vnd="5,5tr", vat_rate="10", doc_no=f"V{uuid.uuid4().hex[:6]}",
                        paid_from="cash")
        assert cash["vat_treatment"] == "in_cost" and "tiền mặt" in cash["vat_note"].lower()
        page = await q(kt, "acct.vat_summary", period=f"{today():%m/%Y}")
        row = next(x for x in page["rows"] if x["id"] == "input")
        assert row["tax_vnd"] >= 1_000_000 and "phiếu chi phí" in row["note"]   # không lộ số ADR cho người dùng
        k = next(x for x in page["kpis"] if x["label"] == "Thuế GTGT đầu vào (phiếu chi phí)")
        assert k["tone"] == "green"
    finally:
        await ok(admin, "admin.set_setting", key="acct.vat_enabled", enabled=False)


async def _ledger_by_month(tid: Any, a: date, b: date) -> dict[tuple[str, str], int]:
    got = await owner_val("""SELECT coalesce(jsonb_object_agg(k, v), '{}') FROM (
                               SELECT a.kind || '|' || to_char(j.posted_on, 'YYYY-MM') AS k, sum(p.amount_vnd)::bigint AS v
                               FROM ledger.postings p JOIN ledger.journals j ON j.id = p.journal_id JOIN ledger.accounts a ON a.id = p.account_id
                               WHERE j.tenant_id = %s AND a.kind IN ('revenue', 'expense') AND j.posted_on BETWEEN %s AND %s GROUP BY 1) x""", (tid, a, b))
    return {tuple(k.split("|")): int(v) for k, v in got.items()}  # type: ignore[misc]


async def test_pnl_numbers_equal_ledger_sums(co: dict[str, Any], demo: dict[str, Any]) -> None:
    for tid, domain in ((co["tid"], DOMAIN), (demo["tenant"], seed.DOMAIN)):   # công ty mẫu: có doanh thu thật từ phí đã ghi
        kt = await seed.who(tid, "ketoan", domain)
        a = date(today().year, 1, 1)
        d = await q(kt, "acct.pnl_data")
        books = await _ledger_by_month(tid, a, today())
        for mth in d["months"]:
            assert d["revenue"]["by_month"][mth] == -books.get(("revenue", mth), 0), mth
            assert d["expenses"]["by_month"][mth] == books.get(("expense", mth), 0), mth
        assert d["operating_profit"]["total"] == d["revenue"]["total"] - d["expenses"]["total"]
        assert sum(x["operating_profit"] for x in d["by_entity"]) == d["operating_profit"]["total"]
        assert d["basis"] == "ledger" and d["from"] == a.isoformat()
        page = await q(kt, "acct.pnl")
        assert next(r for r in page["rows"] if r["id"] == "profit")["total"] == d["operating_profit"]["total"]
    # tập dữ liệu chi phí theo khoản mục × tháng (0100) — phiếu đã huỷ không tính
    kt = await who(co, "ketoan")
    ds = await q(kt, "acct.ds_expenses", by=["category", "month"], measures=["expense"])
    assert ds["rows"]


async def test_month_follows_the_company_zone(co: dict[str, Any]) -> None:
    """Ngày chứng từ trống = HÔM NAY CỦA CÔNG TY; tháng của báo cáo theo ngày đó (không theo giờ máy / giờ Việt Nam)."""
    admin, kt = await who(co, "admin"), await who(co, "ketoan")
    vn = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date()
    zone = next(z for z in ("Pacific/Kiritimati", "Pacific/Pago_Pago") if datetime.now(ZoneInfo(z)).date() != vn)
    await ok(admin, "admin.set_timezone", timezone=zone)
    try:
        kt = await who(co, "ketoan")
        out = await ok(kt, "acct.record_expense", category="dien_nuoc", amount_vnd="700.000", ref=_ref())
        local = datetime.now(ZoneInfo(zone)).date()
        bill_on = await owner_val("SELECT bill_on FROM acct.bills WHERE id = %s", (out["bill_id"],))
        assert bill_on == local != vn
        d = await q(kt, "acct.pnl_data", date_from=local.replace(day=1).isoformat(), date_to=local.isoformat())
        assert d["months"][-1] == f"{local:%Y-%m}" and d["expenses"]["by_month"][f"{local:%Y-%m}"] >= 700_000
    finally:
        await owner_val("UPDATE core.tenants SET timezone = 'Asia/Ho_Chi_Minh' WHERE id = %s RETURNING 1", (co["tid"],))


async def test_misa_export_maps_the_new_accounts_and_paid_bills_count_as_payments_in_the_system(co: dict[str, Any]) -> None:
    kt = await who(co, "ketoan")
    await seed.ensure_account_map(co["tid"])   # ánh xạ mẫu (dữ liệu công ty): tiền mặt, thuế đầu vào, chi phí riêng chưa ánh xạ ⇒ 642
    await ok(kt, "acct.record_expense", category="van_phong", amount_vnd="4tr", paid_from="cash", memo="Chi tiền mặt thử MISA", ref=_ref())
    page = await q(kt, "acct.misa_export", month=clock_today().strftime("%m/%Y"))
    mine = [r for r in page["rows"] if r["memo"].startswith("Chi phí: Chi tiền mặt thử MISA")]
    assert mine and all((r["debit"], r["credit"]) == ("642", "1111") for r in mine)
    assert (await q(kt, "acct.out_lines", state=""))["kind"] == "table"           # đối chiếu tiền ra đọc cả phiếu đã trả bằng chuyển khoản
    assert (await q(kt, "acct.payout_checks"))["kind"]
