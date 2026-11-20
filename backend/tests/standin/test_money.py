"""TIỀN qua một gói ngành — bản LÕI (ADR 0190) của ``barista-packs:commission/tests/test_money.py`` và bốn kịch bản sự kiện của ``test_money_stress.py``: gói thế
vai ký đơn ⇒ phí thành khoản phải thu (họ ``receivable``), phí về ⇒ thưởng về ví (họ ``settlement``), huỷ qua duyệt ⇒ gỡ. Điều được kiểm là của lõi: giao
sự kiện ít nhất một lần + hợp đồng tiền chống lặp, trạng thái hiện tại thắng thứ tự sự kiện, bên nghe lỗi được thử lại chứ không mất, bên nghe đổi tên,
luật duyệt là dữ liệu, và bất biến tiền sau mỗi kịch bản."""

import dataclasses
from typing import Any

import pytest

from barista.db import db
from barista.kernel import events, executor, registry
from barista.kernel.errors import Invalid
from barista.kernel.executor import core_ctx
from barista.kernel.principal import Principal, system_principal
from tests.conftest import Who, edge, ok, owner_val, q
from tests.standin import helpers, pack
from tests.test_money_stress import _money_ok

_EXPENSE = """SELECT coalesce(sum(p.amount_vnd), 0) FROM ledger.postings p JOIN ledger.accounts a ON a.id = p.account_id
              WHERE a.code = 'expense:order_bonus' AND p.journal_id IN (
                  SELECT v.journal_id FROM acct.payables v WHERE v.source = 'orderdesk.bonus' AND v.source_ref LIKE %(o)s
                  UNION SELECT x.journal_id FROM acct.payable_adjustments x JOIN acct.payables v ON v.id = x.payable_id
                        WHERE v.source = 'orderdesk.bonus' AND v.source_ref LIKE %(o)s)"""


async def _expense(o: dict[str, Any]) -> int:
    """Chi phí thưởng ĐÃ GHI SỔ CÁI của một đơn (nghĩa vụ + mọi điều chỉnh)."""
    return int(await owner_val(_EXPENSE, {"o": o["order_id"] + ":%"}))


async def _earned(o: dict[str, Any]) -> int:
    return int(await owner_val("SELECT earned_vnd FROM orderdesk.orders WHERE id = %s", (o["order_id"],)))


async def _wallet(p: Principal) -> int:
    """Ví của một người theo Tài chính (``acct.position`` — họ settlement, chỉ nguồn của module gọi)."""
    async with db.tenant_tx(p) as conn:
        return int((await core_ctx(conn, p, module=pack.MODULE).call_system("acct.position", user_id=p.user_id))["owed"])


async def _snapshot(o: dict[str, Any]) -> dict[str, Any]:
    return {
        "receivables": await owner_val("SELECT count(*) FROM acct.receivables WHERE code = %s", (o["inv"],)),
        "payables": await owner_val("SELECT jsonb_agg(amount_vnd ORDER BY source_ref) FROM acct.payables WHERE source = 'orderdesk.bonus' AND source_ref LIKE %s",
                                    (o["order_id"] + ":%",)),
        "earned": await _earned(o),
        "journals": await owner_val("SELECT count(*) FROM ledger.journals WHERE tenant_id = (SELECT tenant_id FROM orderdesk.orders WHERE id = %s)",
                                    (o["order_id"],)),
        "work": await owner_val("SELECT count(*) FROM work.items WHERE subject_ref = %s", (o["inv"],)),
        "cases": await owner_val("SELECT count(*) FROM flow.cases WHERE subject_id = %s", (o["order_id"],)),
    }


async def test_fee_flow_sign_then_fee_received_reaches_the_wallet_once(who: Who) -> None:
    """Ký ⇒ khoản phải thu phí (doanh thu vào tài khoản của gói); phí về đủ ⇒ thưởng về ví người phụ trách đúng bằng số ghi sổ cái; nhập lại cùng
    giao dịch ngân hàng ⇒ chặn trùng."""
    ketoan, sale = await who("ketoan"), await helpers.fresh_member(who)
    before = await _wallet(sale)
    o = await helpers.signed(who, owner=sale, amount=6_000_000_000)
    assert await owner_val("SELECT amount_vnd FROM acct.receivables WHERE code = %s", (o["inv"],)) == o["fee"] == 180_000_000
    assert await owner_val("""SELECT sum(-p.amount_vnd) FROM ledger.postings p JOIN ledger.journals j ON j.id = p.journal_id
                              JOIN ledger.accounts a ON a.id = p.account_id WHERE j.source_ref = %s AND a.code = %s""", (o["inv"], pack.REVENUE)) == o["fee"]
    assert await _earned(o) == 0 and await _wallet(sale) == before                    # chưa thu ⇒ chưa về ví
    await ok(ketoan, "acct.record_payment", code=o["inv"], amount_vnd=o["fee"], bank_ref=f"T-{o['code']}")
    await events.drain()
    assert await _earned(o) == o["bonus"] == await _expense(o)
    assert await _wallet(sale) - before == o["bonus"]
    r = await executor.run(ketoan, "acct.record_payment", {"code": o["inv"], "amount_vnd": 1, "bank_ref": f"T-{o['code']}"})
    assert r.status in ("invalid", "conflict")
    await _money_ok()


async def test_fee_paid_in_two_tranches_reaches_the_wallet_pro_rata(who: Who) -> None:
    ketoan = await who("ketoan")
    o = await helpers.signed(who)
    await ok(ketoan, "acct.record_payment", code=o["inv"], amount_vnd=o["fee"] // 2, bank_ref=f"H1-{o['code']}")
    await events.drain()
    assert await _earned(o) == o["bonus"] // 2 == await _expense(o)
    await ok(ketoan, "acct.record_payment", code=o["inv"], amount_vnd=o["fee"] - o["fee"] // 2, bank_ref=f"H2-{o['code']}")
    await events.drain()
    assert await _earned(o) == o["bonus"] == await _expense(o)
    await _money_ok()


async def test_redelivering_every_money_event_changes_nothing(who: Who) -> None:
    """Máy chết sau khi bên nghe đã ghi nhưng trước khi đánh dấu xong ⇒ sự kiện giao lại. Giao lại MỌI sự kiện mở / ký / thu tiền của đơn: không thêm
    khoản phải thu, nghĩa vụ, bút toán, việc theo dõi, hồ sơ nào — bên nghe ký của gói KHÔNG tự chống lặp, lõi giữ (nguồn, tham chiếu)."""
    o = await helpers.signed(who)
    await ok(await who("ketoan"), "acct.record_payment", code=o["inv"], amount_vnd=o["fee"] // 2, bank_ref=f"R-{o['code']}")
    await events.drain()
    before = await _snapshot(o)
    assert before["receivables"] == 1 and before["work"] == 1 and before["earned"] == o["bonus"] // 2
    n = await owner_val("""WITH x AS (UPDATE core.deliveries d SET status = 'pending', next_at = now(), done_at = NULL FROM core.events e
                                      WHERE e.id = d.event_id AND d.status = 'done'
                                        AND (e.payload->>'code' IN (%s, %s) OR e.payload->>'id' = %s) RETURNING 1)
                           SELECT count(*) FROM x""", (o["code"], o["inv"], o["order_id"]))
    assert n >= 5   # 2 bên nghe ký của gói + bên nghe thu tiền của gói + bên nghe đối tượng của quy trình
    await events.drain()
    assert await _snapshot(o) == before
    await _money_ok()


async def test_cancel_handled_before_a_late_signed_handler_leaves_no_money_behind(who: Who) -> None:
    """Bên nghe "đã ký" chạy TRỄ hơn "đã huỷ" (lỗi tạm rồi thử lại sau): trạng thái HIỆN TẠI thắng thứ tự sự kiện — đơn đã huỷ không để lại khoản
    phải thu đang mở, doanh thu, hay việc thu phí."""
    admin, giamdoc = await who("admin"), await who("giamdoc")
    o = await helpers.order(who)
    await ok(admin, "orderdesk.sign", code=o["code"])
    late = """UPDATE core.deliveries d SET next_at = now() + interval '1 day' FROM core.events e
              WHERE e.id = d.event_id AND e.type = 'orderdesk.order_signed' AND e.payload->>'code' = %s AND d.status = 'pending' RETURNING d.id"""
    assert await owner_val(late, (o["code"],)) is not None   # bên nghe "đã ký" bị hoãn
    r = await executor.run(admin, "orderdesk.cancel", {"code": o["code"], "reason": "khách rút sớm"})
    await ok(edge(giamdoc), "core.decide", approval_id=r.approval_id, approve=True)
    await events.drain()                                          # bên nghe "đã huỷ" chạy trước
    await owner_val("""UPDATE core.deliveries d SET next_at = now() FROM core.events e WHERE e.id = d.event_id AND e.type = 'orderdesk.order_signed'
                       AND e.payload->>'code' = %s RETURNING 1""", (o["code"],))
    assert await events.drain() >= 2                              # rồi hai bên nghe "đã ký" mới chạy
    inv = f"PD-{o['code']}"
    assert await owner_val("SELECT count(*) FROM acct.receivables WHERE code = %s AND status <> 'void'", (inv,)) == 0
    assert await owner_val("SELECT count(*) FROM work.items WHERE subject_ref = %s AND status IN ('active', 'waiting')", (inv,)) == 0
    assert await owner_val("""SELECT coalesce(sum(p.amount_vnd), 0) FROM ledger.postings p JOIN ledger.journals j ON j.id = p.journal_id
                              JOIN ledger.accounts a ON a.id = p.account_id WHERE j.source_ref = %s AND a.code LIKE 'revenue:%%'""", (inv,)) == 0
    await _money_ok()


async def test_payments_handled_in_reverse_order_reach_the_wallet_exactly_once(who: Who) -> None:
    """Hai lần khách trả phí, sự kiện lần 1 tới SAU lần 2: thưởng về ví đúng một lần đủ số (mức đích theo căn cứ thu hiện tại, chỉ tăng; lõi khoá
    nghĩa vụ theo (nguồn, tham chiếu))."""
    o = await helpers.signed(who)
    ketoan = await who("ketoan")
    await ok(ketoan, "acct.record_payment", code=o["inv"], amount_vnd=o["fee"] // 2, bank_ref=f"P1-{o['code']}")
    first = await owner_val("""UPDATE core.deliveries d SET next_at = now() + interval '1 day' FROM core.events e WHERE e.id = d.event_id
                               AND e.type = 'acct.payment_received' AND e.payload->>'code' = %s AND d.status = 'pending'
                               AND d.subscriber = 'orderdesk.earn_on_fee' RETURNING d.id""", (o["inv"],))
    assert first is not None
    await ok(ketoan, "acct.record_payment", code=o["inv"], amount_vnd=o["fee"] - o["fee"] // 2, bank_ref=f"P2-{o['code']}")
    await events.drain()                                          # lần 2 (luỹ kế đủ) chạy trước
    assert await _earned(o) == o["bonus"]
    await owner_val("UPDATE core.deliveries SET next_at = now() WHERE id = %s RETURNING 1", (first,))
    assert await events.drain() >= 1                              # lần 1 tới muộn
    assert await owner_val("SELECT status FROM core.deliveries WHERE id = %s", (first,)) == "done"
    assert await _earned(o) == o["bonus"] == await _expense(o)
    assert await owner_val("SELECT count(*) FROM acct.payables WHERE source = 'orderdesk.bonus' AND source_ref LIKE %s", (o["order_id"] + ":%",)) == 1
    await _money_ok()


async def test_renamed_listener_delivery_retries_without_double_earning(who: Who) -> None:
    """``Module.on(replaces=)``: lượt còn mang TÊN CŨ của bên nghe chạy bằng bên nghe mới (không thành "không còn bên nghe"); chạy lại sau khi đã về ví
    không cộng tiền lần hai."""
    o = await helpers.signed(who)
    await ok(await who("ketoan"), "acct.record_payment", code=o["inv"], amount_vnd=o["fee"], bank_ref=f"RN-{o['code']}")
    await events.drain()
    assert await _earned(o) == o["bonus"]
    delivery = await owner_val("""UPDATE core.deliveries d SET subscriber = 'orderdesk.wake_on_fee', status = 'pending', next_at = now()
                                  FROM core.events e WHERE e.id = d.event_id AND d.subscriber = 'orderdesk.earn_on_fee'
                                  AND e.payload->>'code' = %s RETURNING d.id""", (o["inv"],))
    assert delivery is not None
    await events.drain()
    assert await owner_val("SELECT status FROM core.deliveries WHERE id = %s", (delivery,)) == "done"
    assert await _earned(o) == o["bonus"] == await _expense(o)
    await _money_ok()


async def test_failed_subscriber_is_retried_not_lost_and_others_still_run(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    """Một bên nghe lỗi (dữ liệu chưa đủ) ⇒ lượt của NÓ thử lại + hiện ở Quản trị › Sự kiện lỗi; bên nghe khác của cùng sự kiện vẫn chạy (độc lập);
    hết lỗi ⇒ lượt thử lại làm nốt, không mất phản ứng."""
    admin = await who("admin")
    sub = registry.SUBSCRIBERS["orderdesk.open_fee_work"]

    async def broken(ctx: Any, event: Any) -> None:
        raise Invalid("Chưa có người phụ trách công nợ (thử)")

    monkeypatch.setitem(registry.SUBSCRIBERS, sub.name, dataclasses.replace(sub, handler=broken))
    o = await helpers.signed(who)
    row = next(r for r in (await q(admin, "admin.deliveries"))["rows"] if r["subscriber"] == sub.name and "công nợ (thử)" in r["last_error"])
    assert row["attempts"] >= 1 and row["status"] == "pending"
    assert await owner_val("SELECT count(*) FROM acct.receivables WHERE code = %s", (o["inv"],)) == 1     # bên nghe lập phí không bị kéo theo
    assert o["work"] is None
    monkeypatch.setitem(registry.SUBSCRIBERS, sub.name, sub)
    await ok(admin, "admin.retry_delivery", delivery_id=row["id"])
    await events.drain()
    assert await owner_val("SELECT count(*) FROM work.items WHERE subject_ref = %s", (o["inv"],)) == 1
    assert await owner_val("SELECT status FROM core.deliveries WHERE id = %s", (row["id"],)) == "done"


async def test_cancel_requires_the_packs_approval_chain_and_unwinds(who: Who) -> None:
    """Luật duyệt mặc định của gói là DỮ LIỆU (``Pack.approval_rules`` cài qua ``admin.install_defaults``): huỷ đơn thành phiếu chờ giám đốc; duyệt ⇒
    lệnh chạy, khoản phí chưa thu bị huỷ bằng đúng một bút toán đảo, việc thu phí dừng."""
    admin, giamdoc, sale3 = await who("admin"), await who("giamdoc"), await who("sale3")
    o = await helpers.signed(who)
    r = await executor.run(admin, "orderdesk.cancel", {"code": o["code"], "reason": "khách đổi ý"})
    assert r.status == "pending_approval"
    assert await owner_val("SELECT status FROM orderdesk.orders WHERE id = %s", (o["order_id"],)) == "signed"
    assert (await executor.run(edge(sale3), "core.decide", {"approval_id": r.approval_id, "approve": True})).status != "ok"   # không phải người duyệt
    done = await ok(edge(giamdoc), "core.decide", approval_id=r.approval_id, approve=True)
    assert done["status"] == "executed", done
    await events.drain()
    assert await owner_val("SELECT status FROM orderdesk.orders WHERE id = %s", (o["order_id"],)) == "cancelled"
    assert await owner_val("SELECT status FROM acct.receivables WHERE code = %s", (o["inv"],)) == "void"
    assert await owner_val("SELECT count(*) FROM ledger.journals WHERE idempotency_key LIKE 'acct.ar_void:%%' AND source_ref = %s", (o["inv"],)) == 1
    tid = admin.tenant_id
    from barista.modules import work
    assert (await work.step(tid, o["work"], use_model=False))["status"] == "cancelled"
    await _money_ok()


async def test_cancel_after_partial_earn_claws_back_exactly(who: Who) -> None:
    """Huỷ sau khi đã thu một phần: phần thưởng đã về ví bị điều chỉnh về đúng 0 (họ settlement); khoản phí đã thu một phần KHÔNG tự huỷ — bên nghe
    dừng ở Sự kiện lỗi cho kế toán xử lý tay."""
    admin, ketoan, giamdoc = await who("admin"), await who("ketoan"), await who("giamdoc")
    o = await helpers.signed(who)
    await ok(ketoan, "acct.record_payment", code=o["inv"], amount_vnd=o["fee"] // 4, bank_ref=f"Q-{o['code']}")
    await events.drain()
    assert await _expense(o) == o["bonus"] // 4 > 0
    r = await executor.run(admin, "orderdesk.cancel", {"code": o["code"], "reason": "khách rút"})
    await ok(edge(giamdoc), "core.decide", approval_id=r.approval_id, approve=True)
    await events.drain()
    assert await _expense(o) == 0, "phần đã về ví bị thu hồi đúng bằng số đã ghi"
    assert await owner_val("SELECT status FROM acct.receivables WHERE code = %s", (o["inv"],)) == "partial"
    assert any(x["subscriber"] == "orderdesk.void_fee" and o["inv"] in x["last_error"] for x in (await q(admin, "admin.deliveries"))["rows"])
    await owner_val("""UPDATE core.deliveries d SET status = 'done' FROM core.events e WHERE e.id = d.event_id AND d.subscriber = 'orderdesk.void_fee'
                       AND e.payload->>'code' = %s RETURNING 1""", (o["code"],))   # dọn: không để lượt thử lại treo sang test khác
    await _money_ok()


async def test_money_drift_on_real_data_shows_in_health_and_alerts_once_a_day(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cùng bất biến chạy trên dữ liệu thật: khoản phải thu của một gói lệch phiếu thu ⇒ Sức khoẻ dữ liệu hiện mã; vòng quét báo như LỖI HỆ THỐNG một
    lần / ngày / công ty."""
    from barista.modules import acct

    o = await helpers.signed(who)
    gd = await who("giamdoc")
    monkeypatch.setattr(acct, "_drift_alerted", {})
    await owner_val("UPDATE acct.receivables SET paid_vnd = 1000, status = 'partial' WHERE code = %s RETURNING 1", (o["inv"],))   # giả lập lỗi
    try:
        issues = (await executor.query(system_principal(gd.tenant_id), "acct.health_report", {})).output["issues"]
        drift = next(i for i in issues if i["key"] == "money_drift")
        assert drift["n"] >= 1 and o["inv"] in drift["subtitle"]
        first = await executor.run(system_principal(gd.tenant_id), "acct.check_money", {})
        second = await executor.run(system_principal(gd.tenant_id), "acct.check_money", {})
        assert (first.status, second.status) == ("error", "ok")
        assert await owner_val("""SELECT count(*) FROM core.audit_log WHERE command = 'acct.check_money' AND status = 'error'
                                  AND error LIKE %s""", (f"%Lệch tiền%{o['inv']}%",)) == 1
    finally:
        await owner_val("UPDATE acct.receivables SET paid_vnd = 0, status = 'open' WHERE code = %s RETURNING 1", (o["inv"],))
    await events.drain()
    await _money_ok()
