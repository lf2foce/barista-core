"""Nhiều pháp nhân trong một công ty (0099): pháp nhân là CHIỀU trên bút toán và chứng từ tiền — mỗi bút toán đúng một pháp nhân, sổ từng pháp
nhân tự cân, số gộp = tổng các pháp nhân; phí môi giới vào pháp nhân của dự án, hoa hồng vào pháp nhân chi hoa hồng (mặc định); đảo giữ pháp
nhân; tiền về pháp nhân này không trả được khoản của pháp nhân khác (không đề xuất, không ghi); xuất MISA tách theo pháp nhân."""


import uuid

import pytest

from barista.kernel import executor
from barista.kernel.clock import today
from barista.kernel.clock import today as clock_today  # xuất MISA mặc định = tháng đang kê khai (18/11/2026) ⇒ test nêu tháng này
from tests import money_source
from tests.conftest import Who, ok, owner_val, q


@pytest.fixture(autouse=True)
def _source(monkeypatch: pytest.MonkeyPatch) -> None:
    """Khoản phải thu của nguồn tiền giả của lõi (``tests/money_source.py``), ghi vào pháp nhân MG của công ty mẫu; bản dùng phí môi giới của một dự án
    và hoa hồng (pháp nhân theo dự án, đảo khi huỷ giao dịch) nằm ở test của gói."""
    money_source.use(monkeypatch)


async def test_every_journal_has_one_entity_and_each_entity_balances(who: Who) -> None:
    await money_source.revenue(who, entity_code="MG")
    assert await owner_val("SELECT count(*) FROM ledger.journals WHERE entity_id IS NULL") == 0
    unbalanced = await owner_val("""SELECT count(*) FROM (SELECT j.entity_id FROM ledger.journals j JOIN ledger.postings p ON p.journal_id = j.id
                                    GROUP BY j.tenant_id, j.entity_id HAVING sum(p.amount_vnd) <> 0) x""")
    assert unbalanced == 0                                                                  # sổ TỪNG pháp nhân tự cân


async def test_a_receivable_and_its_receipt_are_booked_in_the_entity_it_names(who: Who) -> None:
    ketoan = await who("ketoan")
    f = await money_source.revenue(who, entity_code="MG")
    assert f["entity"] == "MG"
    await ok(ketoan, "acct.record_payment", code=f["code"], amount_vnd=f["fee"], bank_ref=f"FT{uuid.uuid4().hex[:8]}")
    entities = await owner_val("""SELECT array_agg(DISTINCT e.code) FROM ledger.journals j JOIN core.legal_entities e ON e.id = j.entity_id
                                  WHERE j.source_ref = %s AND j.tenant_id = %s""", (f["code"], ketoan.tenant_id))
    assert entities == ["MG"]                                                                # ghi nhận + phiếu thu cùng pháp nhân


async def test_per_entity_balances_add_up_to_the_company(who: Who) -> None:
    ketoan = await who("ketoan")
    await money_source.revenue(who, entity_code="MG")
    await money_source.revenue(who)
    whole = {r["code"]: r["debit"] - r["credit"] for r in (await q(ketoan, "acct.balances"))["rows"]}
    parts: dict[str, int] = {}
    codes = await owner_val("SELECT array_agg(code) FROM core.legal_entities WHERE tenant_id = %s AND active", (ketoan.tenant_id,))   # MỌI pháp nhân — test khác chạy trước có thể đã thêm
    for code in codes:
        for r in (await q(ketoan, "acct.balances", entity_code=code))["rows"]:
            parts[r["code"]] = parts.get(r["code"], 0) + r["debit"] - r["credit"]
    assert parts == whole


async def test_money_into_one_entity_cannot_pay_another_entitys_receivable(who: Who) -> None:
    ketoan = await who("ketoan")
    f = await money_source.revenue(who, amount=61_000_000 + uuid.uuid4().int % 900_000, entity_code="MG")   # số tiền riêng: không khoản nào khác trùng số
    inv, fee = f["code"], f["fee"]
    ref = f"FT{uuid.uuid4().hex[:8]}"
    await ok(ketoan, "acct.add_statement_line", received_on=today().isoformat(), amount_vnd=fee, memo=f"TT {inv}", bank_ref=ref)   # → CHINH
    line = await owner_val("SELECT id FROM acct.statement_lines WHERE bank_ref = %s", (ref,))
    row = next(r for r in (await q(ketoan, "acct.reconcile"))["rows"] if r["bank_ref"] == ref)
    assert row["state"] == "unmatched" and not row["code"]                                  # không đề xuất ghép sang pháp nhân khác
    assert inv in row["hint"] and "pháp nhân MG" in row["hint"] and "chuyển nhầm" in row["hint"]   # …nhưng code nói rõ: mã của pháp nhân khác
    r = await executor.run(ketoan, "acct.apply_line", {"line_id": str(line), "code": inv})
    assert r.status == "invalid" and "pháp nhân" in (r.error or "")
    assert await owner_val("SELECT paid_vnd FROM acct.receivables WHERE code = %s", (inv,)) == 0
    await ok(ketoan, "acct.ignore_line", line_id=line, note="Thử pháp nhân — nạp nhầm tài khoản")


async def test_misa_export_is_split_by_entity(who: Who) -> None:
    ketoan = await who("ketoan")
    inv = (await money_source.revenue(who, entity_code="MG"))["code"]
    page = await q(ketoan, "acct.misa_export", month=clock_today().strftime("%m/%Y"))
    assert any(c["label"] == "Pháp nhân" for c in page["columns"])                           # nhiều pháp nhân ⇒ có cột pháp nhân
    mg = await q(ketoan, "acct.misa_export", month=clock_today().strftime("%m/%Y"), entity_code="MG")
    assert mg["rows"] and all(r["entity"] == "MG" for r in mg["rows"]) and any(r["ref"] == inv for r in mg["rows"])
    assert not any(r["ref"] == inv for r in (await q(ketoan, "acct.misa_export", month=clock_today().strftime("%m/%Y"), entity_code="CHINH"))["rows"])


async def test_only_admins_manage_entities_and_one_default_always_remains(who: Who) -> None:
    admin, ketoan = await who("admin"), await who("ketoan")
    assert (await executor.run(ketoan, "admin.upsert_entity", {"code": "X1", "name": "Thử"})).status == "denied"
    await ok(admin, "admin.upsert_entity", code="TMP", name="Công ty thử pháp nhân", is_default=True)
    try:
        assert await owner_val("SELECT count(*) FROM core.legal_entities WHERE is_default AND tenant_id = %s", (admin.tenant_id,)) == 1
        r = await executor.run(admin, "admin.upsert_entity", {"code": "TMP", "name": "Công ty thử pháp nhân", "is_default": False})
        assert r.status == "invalid"                                                         # bỏ mặc định mà không ai thay ⇒ từ chối
    finally:
        await ok(admin, "admin.upsert_entity", code="CHINH", name=await owner_val(
            "SELECT name FROM core.legal_entities WHERE code = 'CHINH' AND tenant_id = %s", (admin.tenant_id,)), is_default=True)
        await ok(admin, "admin.upsert_entity", code="TMP", name="Công ty thử pháp nhân", active=False)


# ── Bản LÕI (ADR 0190) của hai test còn lại trong ``barista-packs:commission/tests/test_entities.py`` — nguồn tiền giả của lõi thay phí môi giới / hoa hồng ──

async def test_a_sources_receivable_goes_to_the_entity_it_names_and_its_payables_to_the_default(who: Who) -> None:
    """Doanh thu của một nguồn ghi vào pháp nhân NGUỒN NÊU (vd pháp nhân của đối tượng bán); nghĩa vụ phải trả nguồn đó ghi mà không nêu pháp nhân ⇒ pháp
    nhân mặc định của công ty — hai phía của cùng một nguồn không tự kéo nhau sang một pháp nhân."""
    ketoan = await who("ketoan")
    f = await money_source.revenue(who, entity_code="MG")
    assert f["entity"] == "MG"
    rec = await owner_val("""SELECT e.code FROM ledger.journals j JOIN core.legal_entities e ON e.id = j.entity_id
                             WHERE j.source = 'acct.receivable' AND j.source_ref = %s AND j.tenant_id = %s""", (f["code"], ketoan.tenant_id))
    assert rec == "MG" and not await owner_val("SELECT is_default FROM core.legal_entities WHERE tenant_id = %s AND code = 'MG'", (ketoan.tenant_id,))
    _, _, data = await money_source.payable(who)
    row = await owner_val("""SELECT jsonb_build_object('id', v.id, 'default', e.is_default)
                             FROM acct.payables v JOIN core.legal_entities e ON e.id = v.entity_id
                             WHERE v.source = %s AND v.source_ref = %s AND v.tenant_id = %s""", (data["source"], data["source_ref"], ketoan.tenant_id))
    assert row["default"] is True
    assert await owner_val("""SELECT bool_and(e.is_default) FROM ledger.journals j JOIN core.legal_entities e ON e.id = j.entity_id
                              WHERE j.source = 'acct.payable' AND j.source_ref = %s AND j.tenant_id = %s""", (row["id"], ketoan.tenant_id)) is True


async def test_a_reversal_stays_in_the_entity_of_the_original(who: Who) -> None:
    ketoan = await who("ketoan")
    f = await money_source.revenue(who, entity_code="MG")
    ref = f["code"].removeprefix("DT-")
    out = await money_source.internal(ketoan, "acct.void_receivables", source=f"{money_source.MODULE}.fee", ref_prefix=ref, due_after="0001-01-01")
    assert out == {"voided": [f["code"]], "kept": []}
    rows = await owner_val("""SELECT jsonb_agg(e.code) FROM ledger.journals j JOIN core.legal_entities e ON e.id = j.entity_id
                              WHERE j.source_ref = %s AND j.tenant_id = %s""", (f["code"], ketoan.tenant_id))
    assert len(rows) >= 2 and set(rows) == {"MG"}                                            # ghi nhận + đảo cùng pháp nhân
    assert await owner_val("""SELECT coalesce(sum(p.amount_vnd), 0) FROM ledger.postings p JOIN ledger.journals j ON j.id = p.journal_id
                              JOIN ledger.accounts a ON a.id = p.account_id
                              WHERE j.source_ref = %s AND j.tenant_id = %s AND a.code = %s""", (f["code"], ketoan.tenant_id, money_source.REVENUE)) == 0
