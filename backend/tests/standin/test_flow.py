"""HỒ SƠ BÁM ĐỐI TƯỢNG CỦA GÓI (0043) qua gói thế vai: bản LÕI (ADR 0190) của ``barista-packs:real_estate/tests/test_flow.py``, phần hồ sơ của
``test_journey.py``, ``test_notify.py`` và ``barista-packs:commission/tests/test_stage_deadline.py`` — mẫu kiểm theo bộ kiểm gói đăng ký, hồ sơ tự mở theo đối
tượng và giao việc của chặng, chặng chuyển do CODE (bộ kiểm), trả lại lùi chặng + việc cho người phụ trách, đối tượng huỷ ⇒ hồ sơ huỷ, số tiền công ty
ẩn theo quyền mẫu khai ở mục, thông báo đúng người, việc trợ lý chờ hiện ở "Cần xử lý"."""

from typing import Any
from uuid import UUID

from barista.db import db
from barista.kernel import events, executor
from barista.modules import work
from tests.conftest import Who, edge, ok, owner_val, q
from tests.standin import helpers
from tests.standin.pack import FLOW_KEY, ITEM_FEE, ITEM_PAPERS, ITEM_SLIP, ORDER, STAGE_1, STAGE_2, STAGE_3
from tests.standin.test_money import _wallet
from tests.test_flow import FREE, _board, _key
from tests.test_journey import _blocks
from tests.test_notify import _inbox
from tests.test_stage_deadline import _list


async def _stage(case: Any) -> str | None:
    return await owner_val("SELECT stage_key FROM flow.cases WHERE id = %s", (case,))


async def _open(who: Who) -> tuple[dict[str, Any], Any]:
    """Đơn mới của sale3 + hồ sơ tự mở theo quy trình mặc định của gói."""
    o = await helpers.order(who)
    case = await helpers.case_id(o["order_id"])
    assert case is not None
    return o, case


async def test_template_text_is_checked_against_registered_checkers_and_teams(who: Who) -> None:
    admin = await who("admin")
    for src, msg in [("- Mục trước chặng", "cần một chặng"), ("# A\n- X = orderdesk.khong_co", "không có bộ kiểm"),
                     ("# A\n- X = orderdesk.stage_paid seq=1", f"dành cho {ORDER}"),   # mẫu tự do không dùng được bộ kiểm của đối tượng gói
                     ("# A\n- X\n- X", "trùng mục"), ("# A\n- X\n> Việc @KHONGCO | 1 ngày", "Không có đội"), ("# A", "chưa có mục"),
                     ("ghi linh tinh", "phải bắt đầu bằng")]:
        r = await executor.run(admin, "flow.set_template", {"key": _key(), "name": "Thử", "source": src})
        assert r.status == "invalid" and msg in r.error, (src, r.error)
    r = await executor.run(admin, "flow.set_template", {"key": _key(), "name": "Thử", "source": "# A\n- X", "auto_open": True})
    assert r.status == "invalid"                                                    # tự mở cần đối tượng bám theo
    r = await executor.run(admin, "flow.set_template", {"key": _key(), "name": "Khác", "subject_kind": ORDER, "auto_open": True, "source": "# A\n- X"})
    assert r.status == "conflict"                                                   # một loại đối tượng chỉ một quy trình tự mở
    out = await ok(admin, "flow.set_template", key=_key(), name="Bám đơn, mở tay", subject_kind=ORDER,
                   source="# A\n- Cọc = orderdesk.stage_paid seq=1\n- Ký = orderdesk.signed")
    assert out["version"] == 1                                                      # bộ kiểm đúng loại đối tượng ⇒ nhận
    sale = await who("sale1")
    assert (await executor.run(sale, "flow.set_template", {"key": _key(), "name": "X", "source": FREE})).status == "denied"
    assert (await executor.run(await who("truonga"), "flow.open_case", {"key": FLOW_KEY, "title": "X"})).status == "invalid"   # mẫu bám đối tượng: tự mở


async def test_order_case_opens_by_itself_and_hands_out_stage_tasks(who: Who) -> None:
    sale3 = await who("sale3")
    o, case = await _open(who)
    assert await _stage(case) == STAGE_1
    tasks = {t["title"]: t for t in await helpers.rows("SELECT t.title, t.assignee_id, tm.code FROM flow.tasks t LEFT JOIN core.teams tm ON tm.id = t.team_id"
                                                       " WHERE t.case_id = %s", (case,))}
    assert tasks["Gửi phiếu đặt hàng bản gốc về kế toán"]["assignee_id"] == sale3.user_id        # không ghi đội ⇒ người phụ trách đối tượng
    assert tasks["Đối chiếu phiếu đặt hàng với bản gốc"]["assignee_id"] is None and tasks["Đối chiếu phiếu đặt hàng với bản gốc"]["code"] == "KT"
    board = _board(await q(await who("ketoan"), "flow.board", key=FLOW_KEY))
    assert any(o["code"] in t for t in board[STAGE_1])                              # bảng hồ sơ: thẻ ở cột đúng chặng
    await events.drain()
    assert await owner_val("SELECT count(*) FROM flow.cases WHERE subject_id = %s", (o["order_id"],)) == 1


async def test_case_file_hides_company_money_from_sale_and_advances_by_code(who: Who) -> None:
    admin, sale3, ketoan = await who("admin"), await who("sale3"), await who("ketoan")
    o, case = await _open(who)
    page = await q(sale3, "orderdesk.case_file", code=o["code"])
    stages = {s["key"]: s for s in _blocks(page, "stages")[0]["stages"]}
    assert stages[STAGE_1]["state"] == "current"
    assert "Tiếp nhận & cọc" in _blocks(page, "callout")[0]["title"] and "Kế toán" in _blocks(page, "callout")[0]["title"]

    # mục chưa có nguồn dữ liệu: người xác nhận; mục có bộ kiểm: dữ kiện thật (đợt 1 đã trả)
    await ok(ketoan, "flow.mark_item", case_id=case, item_key=ITEM_SLIP, status="verified", note="Đã đối chiếu bản gốc")   # mẫu: đội KT xác nhận
    assert await _stage(case) == STAGE_1                                             # còn mục có bộ kiểm chưa đạt
    await ok(admin, "orderdesk.pay_stage", code=o["code"], seq=1)
    await events.drain()
    stages = {s["key"]: s for s in _blocks(await q(sale3, "orderdesk.case_file", code=o["code"]), "stages")[0]["stages"]}
    assert stages[STAGE_1]["state"] == "done" and stages[STAGE_2]["state"] == "current"  # chặng chuyển do code

    await ok(admin, "flow.mark_item", case_id=case, item_key=ITEM_PAPERS, status="verified", note="Giấy tờ khớp tên")
    await ok(admin, "orderdesk.sign", code=o["code"])
    await events.drain()
    fee = lambda p: next(i for st in _blocks(p, "stages")[0]["stages"] for i in st["items"] if i["key"] == ITEM_FEE)  # noqa: E731
    page_sale, page_acc = await q(sale3, "orderdesk.case_file", code=o["code"]), await q(ketoan, "orderdesk.case_file", code=o["code"])
    assert fee(page_sale)["hidden"] is True and fee(page_sale)["value"] is None  # sale thấy trạng thái, KHÔNG thấy số tiền công ty
    assert fee(page_acc)["hidden"] is False and "đ" in fee(page_acc)["value"]
    assert await _stage(case) == STAGE_3                                             # chặng được GHI lại, không chỉ tính khi xem
    # tiền về đủ + đợt 2 ⇒ mọi mục có bộ kiểm đạt ⇒ hồ sơ tự đóng
    await ok(admin, "orderdesk.pay_stage", code=o["code"], seq=2)
    await ok(ketoan, "acct.record_payment", code=f"PD-{o['code']}", amount_vnd=o["fee"], bank_ref=f"CF-{o['code']}")
    await events.drain()
    assert await owner_val("SELECT status FROM flow.cases WHERE id = %s", (case,)) == "done"


async def test_rejecting_an_earlier_item_moves_the_case_back_and_gives_the_owner_a_task(who: Who) -> None:
    admin, sale3, ketoan = await who("admin"), await who("sale3"), await who("ketoan")
    o, case = await _open(who)
    await ok(ketoan, "flow.mark_item", case_id=case, item_key=ITEM_SLIP, status="verified", note="Đã đối chiếu")
    await ok(admin, "orderdesk.pay_stage", code=o["code"], seq=1)
    await events.drain()
    assert await _stage(case) == STAGE_2
    await ok(ketoan, "flow.mark_item", case_id=case, item_key=ITEM_SLIP, status="rejected", note="Thiếu chữ ký khách")
    assert await _stage(case) == STAGE_1                                             # lùi đúng chặng của mục
    back = await helpers.rows("SELECT assignee_id, notes FROM flow.tasks WHERE case_id = %s AND origin = 'returned'", (case,))
    assert len(back) == 1 and back[0]["assignee_id"] == sale3.user_id and "Thiếu chữ ký khách" in back[0]["notes"]
    assert "Bổ sung: Phiếu đặt hàng đã ký" in _board(await q(sale3, "flow.tasks"))["todo"]   # người phụ trách thấy ngay trong "Việc của tôi"
    n = next(x for x in await _inbox(sale3) if x["kind"] == "returned" and o["code"] in x["body"])
    assert "Thiếu chữ ký khách" in n["body"] and n["open"]["query"] == "flow.case"       # và được báo, kèm đường mở hồ sơ
    r = await executor.run(admin, "flow.mark_item", {"case_id": str(case), "item_key": "tien_coc_da_nhan", "status": "verified"})
    assert r.status == "invalid" and "dữ kiện thật" in r.error                       # mục có bộ kiểm: không ai khai tay được
    await ok(admin, "flow.mark_item", case_id=case, item_key=ITEM_PAPERS, status="rejected", note="Ảnh mờ")   # mục ở chặng sau: vẫn có việc
    assert await owner_val("SELECT count(*) FROM flow.tasks WHERE case_id = %s AND origin = 'returned'", (case,)) == 2
    assert "returned" in [r["kind"] for r in await helpers.rows("SELECT kind FROM flow.case_events WHERE case_id = %s ORDER BY at, id", (case,))]


async def test_rejecting_an_item_needs_a_reason_and_only_the_named_team_confirms(who: Who) -> None:
    admin, sale3 = await who("admin"), await who("sale3")
    _, case = await _open(who)
    r = await executor.run(admin, "flow.mark_item", {"case_id": str(case), "item_key": ITEM_PAPERS, "status": "rejected"})
    assert r.status == "invalid"
    # trợ lý chỉ soạn đề xuất, không tự xác nhận hồ sơ
    p = await executor.run(admin.as_agent(), "flow.mark_item", {"case_id": str(case), "item_key": ITEM_PAPERS, "status": "verified", "note": "ok"})
    assert p.status == "proposed"
    r = await executor.run(sale3, "flow.mark_item", {"case_id": str(case), "item_key": ITEM_PAPERS, "status": "verified", "note": "ok"})
    assert r.status == "denied"                                                     # mục của đội QT: người phụ trách không tự khai


async def test_cancelling_the_order_cancels_the_case_and_its_open_tasks(who: Who) -> None:
    admin, giamdoc = await who("admin"), await who("giamdoc")
    o, case = await _open(who)
    r = await executor.run(admin, "orderdesk.cancel", {"code": o["code"], "reason": "Khách đổi ý"})
    assert r.status == "pending_approval"                                           # luật duyệt mặc định của gói
    assert any(n["kind"] == "approval" and f"Huỷ đơn {o['code']}" in n["title"] and not n["read"] for n in await _inbox(giamdoc))   # thông báo nói đúng việc + hồ sơ (mã đơn), không còn "<người>: <tên lệnh>"
    before = len([n for n in await _inbox(admin) if n["kind"] == "approval_done"])
    await ok(edge(giamdoc), "core.decide", approval_id=r.approval_id, approve=True)
    done = [n for n in await _inbox(admin) if n["kind"] == "approval_done"]
    assert len(done) == before + 1 and done[0]["title"].startswith("Phiếu đã được duyệt")   # người duyệt được báo trước, người xin được báo sau
    await events.drain()
    assert await owner_val("SELECT status FROM flow.cases WHERE id = %s", (case,)) == "cancelled"
    assert await owner_val("SELECT count(*) FROM flow.tasks WHERE case_id = %s AND status <> 'cancelled'", (case,)) == 0


async def test_cancelling_an_order_also_cancels_a_completed_case(who: Who) -> None:
    admin, giamdoc = await who("admin"), await who("giamdoc")
    o, case = await _open(who)
    async with db.owner_tx() as conn:   # hồ sơ đã đủ (đặt thẳng cho gọn — đường đi tới "đủ" đã có test riêng)
        await conn.execute("UPDATE flow.cases SET status = 'done', stage_key = NULL, closed_at = now() WHERE id = %s", (case,))
    r = await executor.run(admin, "orderdesk.cancel", {"code": o["code"], "reason": "Huỷ sau khi đủ hồ sơ"})
    await ok(edge(giamdoc), "core.decide", approval_id=r.approval_id, approve=True)
    await events.drain()
    assert await owner_val("SELECT status FROM flow.cases WHERE id = %s", (case,)) == "cancelled"


async def test_cases_of_a_pack_subject_are_isolated_between_companies(who: Who) -> None:
    other_admin, admin = await who("admin", other=True), await who("admin")
    _, case = await _open(who)
    mine = {str(r["id"]) for r in (await q(admin, "flow.templates"))["rows"]}
    theirs = {str(r["id"]) for r in (await q(other_admin, "flow.templates"))["rows"]}
    assert mine and not mine & theirs                                               # mỗi công ty mẫu của mình (cài mặc định riêng), không lọt
    assert (await executor.query(other_admin, "flow.case", {"case_id": str(case)})).status == "not_found"
    assert (await executor.query(admin, "flow.case", {"case_id": str(case)})).status == "ok"


async def test_assistant_shows_a_case_card_and_the_model_gets_a_readable_summary(who: Who) -> None:
    from tests.test_assistant import _turn, call

    sale3, admin, ketoan = await who("sale3"), await who("admin"), await who("ketoan")
    o, case = await _open(who)
    await ok(ketoan, "flow.mark_item", case_id=case, item_key=ITEM_SLIP, status="verified", note="ok")
    await ok(admin, "orderdesk.pay_stage", code=o["code"], seq=1)
    await ok(admin, "flow.mark_item", case_id=case, item_key=ITEM_PAPERS, status="verified", note="ok")
    await ok(admin, "orderdesk.sign", code=o["code"])
    await events.drain()
    _, events_, model = await _turn(sale3, f"Hồ sơ {o['code']} tới đâu rồi?", [
        [call("orderdesk.case_file", code=o["code"])],
        "Hồ sơ đang ở chặng Thanh toán & phí, Kế toán giữ.",
    ])
    cards = [e["card"] for e in events_ if e["type"] == "card"]
    assert len(cards) == 1 and cards[0]["page"]["kind"] == "card"  # trợ lý mở được thẻ khối của gói, không chỉ bảng / chi tiết
    returned = model.returns[0]["data"]
    stages = next(b["chặng"] for b in returned["blocks"] if "chặng" in b)
    assert stages[0]["tên"] == "Tiếp nhận & cọc" and any("(ẩn số)" in m for st in stages for m in st["mục"])  # mục tiền công ty: model cũng không thấy số


async def test_overdue_follow_up_shows_under_needs_action_and_money_follows_receipts(who: Who, company: UUID) -> None:
    """Kịch bản xuyên module: ký ⇒ việc thu phí; khách quá hạn ⇒ trợ lý của kế toán soạn dự thảo, việc hiện ở "Cần xử lý" của người thấy nó; khách trả
    một nửa ⇒ thưởng về ví đúng một nửa; trả đủ ⇒ việc tự đóng, rời "Cần xử lý". Số nào cũng do code tính, model không cần."""
    from datetime import timedelta

    from barista.kernel.clock import today

    ketoan, giamdoc = await who("ketoan"), await who("giamdoc")
    owner = await helpers.fresh_member(who, team="KD-B")
    o = await helpers.signed(who, owner=owner, signed_on=today() - timedelta(days=40))
    before = await _wallet(owner)
    assert (await work.step(company, o["work"], use_model=False))["status"] == "waiting"
    for p in (ketoan, giamdoc):
        waiting = _list(await q(p, "flow.needs_action"), "Trợ lý đang chờ")
        assert any(o["code"] in w["title"] and "dự thảo" in w["subtitle"] for w in waiting), p.email
    assert not _list(await q(owner, "flow.needs_action"), "Trợ lý đang chờ")          # việc của kế toán: người phụ trách đơn không thấy

    await ok(ketoan, "acct.record_payment", code=o["inv"], amount_vnd=o["fee"] // 2, bank_ref=f"D1-{o['code']}")
    await events.drain()
    assert await _wallet(owner) - before == o["bonus"] // 2
    await ok(ketoan, "acct.record_payment", code=o["inv"], amount_vnd=o["fee"] - o["fee"] // 2, bank_ref=f"D2-{o['code']}")
    await events.drain()
    assert (await work.step(company, o["work"], use_model=False))["status"] == "done"
    assert await _wallet(owner) - before == o["bonus"]
    assert not any(o["code"] in w["title"] for w in _list(await q(ketoan, "flow.needs_action"), "Trợ lý đang chờ"))
