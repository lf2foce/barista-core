"""ĐỐI SOÁT SAO KÊ với khoản phải thu của một gói ngành (0055) — bản LÕI (ADR 0190) của ``barista-packs:commission/tests/test_reconcile.py``: dòng tiền vào nạp
trước (chưa đụng sổ cái), CODE khớp với khoản phí của gói theo mã trong nội dung chuyển khoản + số tiền, người bấm ghi qua đúng ``acct.record_payment``
⇒ phản ứng của gói (thưởng về ví, việc theo dõi thức dậy) tự chạy theo sự kiện sẵn có. Chênh lệch không tự xoá nợ: ghi phần đã nhận + việc cho kế toán."""

import uuid

from barista.db import db
from barista.kernel import events, executor, imports
from barista.kernel.clock import today
from barista.kernel.executor import core_ctx
from barista.kernel.principal import Principal
from tests.conftest import Who, ok, owner_val, q
from tests.standin import helpers, pack


async def _wallet(p: Principal) -> int:
    """Ví của một người theo Tài chính (``acct.position`` — họ settlement, chỉ nguồn của module gọi)."""
    async with db.tenant_tx(p) as conn:
        return int((await core_ctx(conn, p, module=pack.MODULE).call_system("acct.position", user_id=p.user_id))["owed"])


async def test_bank_export_is_imported_matched_and_applied(who: Who) -> None:
    ketoan, sale = await who("ketoan"), await helpers.fresh_member(who, "sale", "KD-B")
    a, b = await helpers.signed(who, owner=sale), await helpers.signed(who, owner=sale)
    before = await _wallet(sale)
    tag = uuid.uuid4().hex[:6].upper()
    day = f"{today():%d/%m/%Y}"
    # tiêu đề cột đúng kiểu sao kê ngân hàng (không phải mẫu của hệ thống) + một dòng ghi nợ (tiền ra) phải bị bỏ qua
    rows = [{"Ngày giao dịch": day, "Số tiền ghi có": a["fee"], "Diễn giải": f"KHACH TT PHI DICH VU {a['inv'].replace('-', ' ')}",
             "Số tham chiếu": f"FT{tag}1", "Tên đối ứng": "CONG TY KHACH THU"},
            {"Ngày giao dịch": day, "Số tiền ghi có": b["fee"] - b["fee"] // 10, "Diễn giải": f"TT phi {b['inv']}", "Số tham chiếu": f"FT{tag}2"},
            {"Ngày giao dịch": day, "Số tiền ghi có": None, "Diễn giải": "Phi SMS", "Số tham chiếu": f"FT{tag}3"}]
    dry = await imports.run_import(ketoan, "acct.add_statement_line", rows, dry_run=True)
    assert dry.ok, dry.error
    assert (await imports.run_import(ketoan, "acct.add_statement_line", rows, dry_run=False)).ok
    assert (await imports.run_import(ketoan, "acct.add_statement_line", rows, dry_run=False)).ok        # nạp lại cùng sao kê
    assert await owner_val("SELECT count(*) FROM acct.statement_lines WHERE bank_ref LIKE %s", (f"FT{tag}%",)) == 2

    page = await q(ketoan, "acct.reconcile")
    mine = {r["bank_ref"]: r for r in page["rows"] if r["bank_ref"].startswith(f"FT{tag}")}
    assert mine[f"FT{tag}1"]["state"] == "full" and mine[f"FT{tag}2"]["state"] == "short" and "10%" in mine[f"FT{tag}2"]["hint"]
    assert (await executor.query(sale, "acct.reconcile", {})).status == "denied"                      # số cả công ty: sale không xem

    proposed = await executor.run(ketoan.as_agent(), "acct.apply_statement", {})
    assert proposed.status == "proposed" and proposed.output["preview"]["applied"] >= 1               # trợ lý chỉ soạn, người bấm
    assert await owner_val("SELECT status FROM acct.receivables WHERE code = %s", (a["inv"],)) == "open"   # chạy thử không ghi gì

    done = await ok(ketoan, "acct.apply_statement")
    assert a["inv"] in done["codes"] and b["inv"] not in done["codes"]                           # dòng lệch không ghi hàng loạt
    await events.drain()
    assert await owner_val("SELECT status FROM acct.receivables WHERE code = %s", (a["inv"],)) == "paid"
    assert await owner_val("SELECT status FROM acct.statement_lines WHERE bank_ref = %s", (f"FT{tag}1",)) == "applied"
    assert await _wallet(sale) - before == a["bonus"]                                            # thưởng về ví theo sự kiện sẵn có của Tài chính
    assert await owner_val("SELECT count(*) FROM work.events WHERE item_id = %s AND kind = 'woke'", (a["work"],)) == 1

    line_b = await owner_val("SELECT id FROM acct.statement_lines WHERE bank_ref = %s", (f"FT{tag}2",))
    res = await ok(ketoan, "acct.apply_line", line_id=line_b)
    assert res["diff"] == b["fee"] // 10 and res["task_id"]
    assert await owner_val("SELECT status FROM acct.receivables WHERE code = %s", (b["inv"],)) == "partial"   # ghi phần đã nhận, KHÔNG xoá nợ
    task = await owner_val("SELECT jsonb_build_object('t', title, 'n', notes, 'a', assignee_id) FROM flow.tasks WHERE id = %s", (res["task_id"],))
    assert task["t"].startswith("Chênh") and "10%" in task["n"] and task["a"] == str(ketoan.user_id)
    await events.drain()
    assert await _wallet(sale) - before == a["bonus"] + b["bonus"] * 9 // 10                     # phần đã nhận ⇒ thưởng theo tỷ lệ đã thu


async def test_unmatched_line_needs_a_chosen_receivable_and_can_be_ignored(who: Who) -> None:
    ketoan = await who("ketoan")
    o = await helpers.signed(who)
    ref = f"FT{uuid.uuid4().hex[:8]}"
    await ok(ketoan, "acct.add_statement_line", received_on=today().isoformat(), amount_vnd=o["fee"] + 7, memo="chuyen tien", bank_ref=ref)
    line = await owner_val("SELECT id FROM acct.statement_lines WHERE bank_ref = %s", (ref,))
    r = await executor.run(ketoan, "acct.apply_line", {"line_id": str(line)})
    assert r.status == "invalid" and "chọn khoản phải thu" in r.error
    res = await ok(ketoan, "acct.apply_line", line_id=line, code=o["inv"])                        # người chọn ⇒ ghi đủ, phần thừa thành việc
    assert res["amount_vnd"] == o["fee"] and res["diff"] == -7 and res["task_id"]
    ref2 = f"FT{uuid.uuid4().hex[:8]}"
    await ok(ketoan, "acct.add_statement_line", received_on=today().isoformat(), amount_vnd=5_000_000, memo="khach dat coc", bank_ref=ref2)
    line2 = await owner_val("SELECT id FROM acct.statement_lines WHERE bank_ref = %s", (ref2,))
    await ok(ketoan, "acct.ignore_line", line_id=line2, note="Tiền cọc của khách, không phải phí đơn")
    assert await owner_val("SELECT status FROM acct.statement_lines WHERE id = %s", (line2,)) == "ignored"
    await events.drain()
