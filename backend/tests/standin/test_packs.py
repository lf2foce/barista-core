"""BẬT / TẮT MỘT GÓI NGÀNH (0045 / 0056 / 0059) qua gói thế vai: bản LÕI (ADR 0190) của ``barista-packs:commission/tests/test_packs.py`` và
``test_packs_industry.py`` — màn / nút / công cụ trợ lý của gói tắt biến mất, bên nghe của gói tắt không nhận việc (quyết định lúc sự kiện xảy ra), bật
/ tắt giữ phụ thuộc và chỉ do người, điều kiện chặn tắt của gói + phản ứng còn nợ (kể cả hết lượt thử) chặn tắt, cửa hàng gói xếp đúng nhóm."""

import uuid
from typing import Any

import pytest

from barista import seed
from barista.agent import chat
from barista.kernel import events, executor, registry
from barista.workspaces import manifest
from tests.conftest import Who, edge, ok, owner_val, q
from tests.standin import pack
from tests.test_packs_industry import PEOPLE


async def _tenant(packs: tuple[str, ...]) -> tuple[Any, str]:
    """Công ty dùng một lần chỉ có các gói nêu ra (+ gói chúng bắt buộc) — người tối thiểu: quản trị, kế toán, giám đốc, một nhân viên."""
    domain = f"t{uuid.uuid4().hex[:6]}.demo"
    return await seed.create_tenant(f"Công ty thử {domain}", domain, people=PEOPLE, packs=packs, about="công ty dịch vụ (thử)"), domain


async def _signed(tid: Any, domain: str, *, drain: bool = True) -> dict[str, Any]:
    admin, sale = await seed.who(tid, "admin", domain), await seed.who(tid, "sale1", domain)
    o = await ok(admin, "orderdesk.create", title="Lắp đặt", customer=f"Khách {uuid.uuid4().hex[:4]}", owner_email=sale.email, amount_vnd=3_000_000_000)
    await ok(admin, "orderdesk.sign", code=o["code"])
    if drain:
        await events.drain()
    return {"code": o["code"], "inv": f"PD-{o['code']}", "order_id": str(o["order_id"])}


async def _cancel(tid: Any, domain: str, code: str) -> None:
    pending = await ok(await seed.who(tid, "admin", domain), "orderdesk.cancel", code=code, reason="Khách đổi ý")
    await ok(edge(await seed.who(tid, "giamdoc", domain)), "core.decide", approval_id=pending["approval_id"], approve=True, note="")
    await events.drain()


async def _payment_listeners(tid: Any, domain: str, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Một khoản phải thu của NGUỒN KHÁC (nguồn tiền giả của lõi) được thu ⇒ sự kiện ``acct.payment_received``; trả tên các bên nghe được giao việc."""
    from barista.kernel.clock import today
    from barista.kernel.principal import elevate

    monkeypatch.setattr(registry, "FAMILY_CALLERS", {**registry.FAMILY_CALLERS, "receivable": {*registry.FAMILY_CALLERS["receivable"], "testpay"}})
    kt = await seed.who(tid, "ketoan", domain)
    code = f"DT-{uuid.uuid4().hex[:8].upper()}"
    r = await executor.run(elevate(kt), "acct.issue_receivable", {
        "source": "testpay.fee", "source_ref": code, "label": "Phí thử", "party": "Khách thử", "nature": "revenue", "amount_vnd": 5_000_000,
        "revenue_account": "revenue:services", "receivable_account": "receivable:customers", "due_on": today().isoformat(), "code": code})
    assert r.status == "ok", r.error
    await ok(kt, "acct.record_payment", code=code, amount_vnd=5_000_000, bank_ref=f"B-{code}")
    subs = await owner_val("""SELECT array_agg(d.subscriber) FROM core.deliveries d JOIN core.events e ON e.id = d.event_id
                              WHERE e.tenant_id = %s AND e.type = 'acct.payment_received' AND e.payload->>'code' = %s""", (tid, code))
    await events.drain()
    return list(subs or [])


def test_the_stand_in_is_a_one_file_industry_pack_the_core_does_not_name() -> None:
    assert registry.pack_kind(pack.PACK) == "industry" and registry.pack_closure({pack.PACK}) >= {pack.PACK, "finance", "directory"}
    assert registry.FAMILY_CALLERS["settlement"] >= {pack.MODULE} and registry.FAMILY_CALLERS["receivable"] >= {pack.MODULE}
    assert all(f"{pack.MODULE}.{c}" in registry.CHECKERS for c in ("stage_paid", "signed", "fee_paid")) and pack.ORDER in registry.SUBJECTS
    assert {"order_fee", "order_stage"} <= set(registry.WORK_KINDS) and "orderdesk.metric_signed" in registry.METRICS


async def test_screens_buttons_and_tools_of_the_pack_follow_the_company_switch(who: Who) -> None:
    """Hai công ty cùng bản cài: bật gói ⇒ màn của gói góp vào phân hệ Bán hàng, lệnh chạy, trợ lý có công cụ; không bật ⇒ như không có gói — kể cả
    với người có quyền."""
    on_admin = await who("admin")
    tid, domain = await _tenant(("crm", "finance"))
    off_admin = await seed.who(tid, "admin", domain)

    def screens(p: Any) -> set[str]:
        return {s["key"] for w in manifest(p, "x")["workspaces"] for s in w["screens"]}

    assert "orders" in screens(on_admin) and "orders" not in screens(off_admin)
    r = await executor.query(off_admin, "orderdesk.list", {})
    assert r.status == "not_found" and "Đơn dịch vụ" in r.error                      # lời báo nêu tên gói, không lộ gì khác
    r = await executor.run(off_admin, "orderdesk.create", {"title": "X", "customer": "Y", "amount_vnd": 1_000_000})
    assert r.status == "not_found"
    assert any(op.name == "orderdesk.create" for op, _ in await chat.catalog(on_admin))
    assert not any(op.name.startswith("orderdesk.") for op, _ in await chat.catalog(off_admin))
    assert (await executor.query(await seed.who(tid, "ketoan", domain), "acct.receivables", {})).status == "ok"   # gói chung vẫn chạy


async def test_pack_switches_respect_dependencies_and_are_for_people_only() -> None:
    tid, domain = await _tenant(("crm",))
    admin = await seed.who(tid, "admin", domain)
    assert (await executor.run(admin.as_agent(), "admin.set_pack", {"pack": pack.PACK, "enabled": True})).status == "denied"   # trợ lý không bật gói
    await ok(admin, "admin.set_pack", pack=pack.PACK, enabled=True)                   # bật gói ⇒ kéo theo gói bắt buộc (Tài chính, Danh bạ)
    on = {r["id"]: r["status"] for r in (await q(admin, "admin.packs"))["rows"]}
    assert on[pack.PACK] == on["finance"] == on["directory"] == "on"
    r = await executor.run(admin, "admin.set_pack", {"pack": "finance", "enabled": False})
    assert r.status == "invalid" and "Đơn dịch vụ" in r.error                         # gói đang dùng cần nó
    # cấu hình mặc định của gói được cài khi bật (0059): quyền cho vai gói khác, chuỗi + luật duyệt, mẫu quy trình tự mở — nhân viên dùng được ngay
    admin, sale = await seed.who(tid, "admin", domain), await seed.who(tid, "sale1", domain)   # nạp lại quyền ⇒ thấy ngay
    o = await ok(sale, "orderdesk.create", title="Tư vấn", customer="Khách bật gói", amount_vnd=1_000_000_000)
    await events.drain()
    assert await owner_val("SELECT count(*) FROM flow.cases WHERE tenant_id = %s AND subject_id = %s", (tid, o["order_id"])) == 1
    assert (await executor.run(admin, "orderdesk.cancel", {"code": o["code"], "reason": "thử"})).status == "pending_approval"
    await ok(admin, "admin.set_pack", pack=pack.PACK, enabled=False)                  # không còn gì chặn (đơn chưa ký)
    await ok(await seed.who(tid, "admin", domain), "admin.set_pack", pack="finance", enabled=False)


async def test_turning_the_pack_off_stops_its_reactions_and_its_blockers_must_clear_first(monkeypatch: pytest.MonkeyPatch) -> None:
    tid, domain = await _tenant((pack.PACK, "crm"))
    admin = await seed.who(tid, "admin", domain)
    assert "orderdesk.earn_on_fee" in await _payment_listeners(tid, domain, monkeypatch)   # đang bật: bên nghe của gói được giao việc
    on = await _signed(tid, domain)
    assert await owner_val("SELECT count(*) FROM acct.receivables WHERE code = %s", (on["inv"],)) == 1   # đang bật: lập phí như thường
    # điều kiện chặn tắt DO GÓI khai (``pack_blockers``): còn đơn đã ký chưa thu đủ phí ⇒ KHÔNG tắt được (như khoá ngoại RESTRICT)
    r = await executor.run(admin, "admin.set_pack", {"pack": pack.PACK, "enabled": False})
    assert r.status == "invalid" and "đơn đã ký chưa thu đủ phí" in r.error
    await _cancel(tid, domain, on["code"])
    await ok(await seed.who(tid, "admin", domain), "admin.set_pack", pack=pack.PACK, enabled=False)
    # tắt rồi: sự kiện của Tài chính vẫn xảy ra, bên nghe của gói KHÔNG nhận việc (quyết định lúc sự kiện xảy ra, cùng giao dịch)
    assert "orderdesk.earn_on_fee" not in await _payment_listeners(tid, domain, monkeypatch)
    ketoan = await seed.who(tid, "ketoan", domain)
    assert (await executor.query(ketoan, "acct.cashflow", {})).status == "ok"                     # Tài chính dùng chung vẫn chạy
    assert (await executor.query(await seed.who(tid, "admin", domain), "orderdesk.list", {})).status == "not_found"


async def test_a_reaction_already_owed_blocks_turning_the_pack_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ký khi gói đang bật ⇒ sự kiện "đã ký" chờ worker lập phí. Còn phản ứng chờ ⇒ không tắt được; xử lý xong ⇒ có khoản phí. Sự kiện xảy ra khi gói
    ĐÃ tắt ⇒ không tạo việc cho bên nghe của gói."""
    tid, domain = await _tenant((pack.PACK, "crm"))
    admin = await seed.who(tid, "admin", domain)
    o = await _signed(tid, domain, drain=False)                                        # KHÔNG drain: phản ứng còn chờ
    r = await executor.run(admin, "admin.set_pack", {"pack": pack.PACK, "enabled": False})
    assert r.status == "invalid" and "phản ứng của gói" in r.error, r.error
    await events.drain()
    assert await owner_val("SELECT count(*) FROM acct.receivables WHERE code = %s", (o["inv"],)) == 1
    await _cancel(tid, domain, o["code"])
    await ok(await seed.who(tid, "admin", domain), "admin.set_pack", pack=pack.PACK, enabled=False)
    assert not [x for x in await _payment_listeners(tid, domain, monkeypatch) if x.split(".")[0] == pack.MODULE]


async def test_an_exhausted_reaction_still_blocks_until_retried_or_dismissed_with_a_reason() -> None:
    """Phản ứng HẾT LƯỢT THỬ (dead) là nghiệp vụ chưa xử lý ⇒ chặn tắt gói; chỉ hết chặn khi quản trị bỏ qua CÓ LÝ DO (ghi người + lý do)."""
    tid, domain = await _tenant((pack.PACK, "crm"))
    admin = await seed.who(tid, "admin", domain)
    o = await ok(admin, "orderdesk.create", title="Lắp đặt", customer="Khách hết lượt", amount_vnd=3_000_000_000)
    await events.drain()
    await ok(admin, "orderdesk.pay_stage", code=o["code"], seq=1)                      # sự kiện của gói, bên nghe của gói (đánh thức việc theo dõi)
    dead = await owner_val("""UPDATE core.deliveries SET status = 'dead', attempts = 8, last_error = 'giả lập hết lượt' WHERE tenant_id = %s
                              AND subscriber = 'orderdesk.wake_on_stage' AND status = 'pending' RETURNING id""", (tid,))
    assert dead is not None
    await events.drain()
    r = await executor.run(admin, "admin.set_pack", {"pack": pack.PACK, "enabled": False})
    assert r.status == "invalid" and "hết lượt thử" in r.error, r.error
    assert (await executor.run(admin, "admin.dismiss_delivery", {"delivery_id": dead, "reason": "  "})).status == "invalid"
    await ok(admin, "admin.dismiss_delivery", delivery_id=dead, reason="Đã nhắc tay")
    assert await owner_val("SELECT status || '|' || dismiss_reason FROM core.deliveries WHERE id = %s", (dead,)) == "dismissed|Đã nhắc tay"
    await ok(await seed.who(tid, "admin", domain), "admin.set_pack", pack=pack.PACK, enabled=False)


async def test_the_store_lists_the_pack_under_industry_and_previews_what_turning_it_on_adds(who: Who) -> None:
    tid, domain = await _tenant(("crm",))
    admin = await seed.who(tid, "admin", domain)
    store = await q(admin, "admin.pack_store")
    groups = {b["title"]: {i["title"]: i for i in b["items"]} for b in store["blocks"] if b["type"] == "tiles"}
    title = registry.pack_title(pack.PACK)
    assert title in groups["Nghiệp vụ theo ngành"] and title not in groups["Năng lực dùng chung — ngành nào cũng dùng"]
    assert "Tài chính" in groups["Năng lực dùng chung — ngành nào cũng dùng"]
    info = await q(admin, "admin.pack_info", pack=pack.PACK)
    lists = {b["title"]: b["items"] for b in info["blocks"] if b["type"] == "list"}
    assert [i["title"] for i in lists["Màn hình của gói"]] == ["Kinh doanh › Đơn"]     # chưa bật: nhãn trung tính của khái niệm
    on_info = await q(await who("admin"), "admin.pack_info", pack=pack.PACK)
    assert ["Kinh doanh › Đơn dịch vụ"] == [i["title"] for b in on_info["blocks"] if b["type"] == "list" and b["title"] == "Màn hình của gói"
                                            for i in b["items"]]                       # đã bật: từ vựng mặc định của gói (Pack.vocab)
    adds = {i["title"] for i in next(v for k, v in lists.items() if k.startswith("Khi bật sẽ thêm"))}
    assert {"Huỷ đơn dịch vụ", "Hồ sơ đơn dịch vụ (giả định)"} <= adds                # chuỗi duyệt + mẫu quy trình mặc định của gói
    on_store = await q(await who("admin"), "admin.pack_store")
    on = {i["title"]: i for b in on_store["blocks"] if b["type"] == "tiles" for i in b["items"]}
    assert on[title]["badge"] != groups["Nghiệp vụ theo ngành"][title]["badge"]         # công ty đã bật thấy trạng thái khác
