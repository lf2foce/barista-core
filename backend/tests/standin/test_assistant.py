"""TRỢ LÝ với công cụ của một gói ngành — bản LÕI (ADR 0190) của ``barista-packs:commission/tests/test_assistant.py``: bộ công cụ theo quyền người hỏi (kể cả
thao tác của gói), truy vấn của gói thành thẻ dữ liệu, đề xuất được CHẠY THỬ (ghi không thể làm báo lỗi ngay cho trợ lý), câu gợi ý / câu xuyên module
gói góp cho vai (``Pack.hints``) lọc theo quyền, bước nói đã lọc gì. Model giả theo kịch bản (``tests/test_assistant.py``)."""

from typing import Any

from barista import seed
from barista.agent import chat
from barista.kernel import registry
from tests.conftest import Who, ok, owner_val
from tests.standin import helpers, pack
from tests.test_assistant import _turn, call


async def test_tools_follow_the_persons_permissions(who: Who) -> None:
    sale = {chat.tool_name(op.name) for op, _ in await chat.catalog(await who("sale1"))}
    acc = {chat.tool_name(op.name) for op, _ in await chat.catalog(await who("ketoan"))}
    assert {"crm__leads", "crm__create_lead", "orderdesk__list", "orderdesk__create", "orderdesk__sign"} <= sale
    # không: duyệt phiếu, quản trị, số cả công ty (min_scope all), thao tác nội bộ của gói
    assert not {"core__decide", "admin__invite_user", "acct__cashflow", "orderdesk__report_year", "orderdesk__health_report"} & sale
    assert {"acct__cashflow", "acct__record_payment", "orderdesk__list", "orderdesk__report_year", "orderdesk__draft_fee_request"} <= acc
    assert not {"crm__leads", "core__decide", "orderdesk__create", "orderdesk__sign", "orderdesk__cancel"} & acc   # kế toán chỉ ĐỌC đơn
    modes = {op.name: mode for op, mode in await chat.catalog(await who("sale1"))}
    assert modes["orderdesk.create"] == "propose" and modes["orderdesk.list"] != "deny"


async def test_query_becomes_card_and_thread_keeps_history(who: Who) -> None:
    sale1 = await who("sale1")
    await helpers.order(who, owner="sale1", line="LD")
    await helpers.order(who, owner="sale1", line="TV")
    out, events, model = await _turn(sale1, "Tôi còn đơn lắp đặt nào đang mở?", [
        [call("orderdesk.list", status="open", line="LD")],
        "Bạn còn đơn lắp đặt đang mở, em đã hiện bảng.",
    ])
    cards = [e["card"] for e in events if e["type"] == "card"]
    assert len(cards) == 1 and cards[0]["kind"] == "page" and cards[0]["page"]["kind"] == "table"
    assert cards[0]["page"]["rows"] and all(r["line"] == "LD" and r["status"] == "open" for r in cards[0]["page"]["rows"])
    assert {r["owner"] for r in cards[0]["page"]["rows"]} == {sale1.name}                   # thẻ chạy bằng quyền NGƯỜI HỎI: chỉ đơn của họ
    assert [e["status"] for e in events if e["type"] == "step"] == ["running", "ok"]
    assert model.returns and model.returns[0]["shown_to_user_as_card"] is True
    assert events[-1]["type"] == "done" and "lắp đặt" in events[-1]["text"]
    # lượt 2 cùng cuộc trò chuyện: model nhận lại lịch sử
    _, _, model2 = await _turn(sale1, "Còn đơn tư vấn?", ["Để em xem thêm."], thread_id=out["thread_id"])
    assert model2.history_len[0] >= 3
    thread = await chat.load_thread(sale1, out["thread_id"])
    assert thread is not None and len(thread["turns"]) == 4
    # cuộc trò chuyện riêng từng người — kể cả trưởng nhóm cũng không đọc được
    assert await chat.load_thread(await who("truonga"), out["thread_id"]) is None


async def test_proposal_is_dry_run_so_impossible_writes_surface_immediately(who: Who) -> None:
    """Đề xuất ghi thu một đơn khách ĐÃ TRẢ ĐỦ: chạy thử báo lỗi ngay cho agent — không có thẻ đề xuất "chết" nào tới tay người."""
    sale1 = await who("sale1")
    o = await helpers.order(who, owner="sale1")
    await ok(sale1, "orderdesk.settle", code=o["code"])
    paid = await owner_val("SELECT sum(paid_vnd) FROM orderdesk.stages WHERE order_id = %s", (o["order_id"],))
    _, events, model = await _turn(sale1, f"Ghi khách trả hết đơn {o['code']}", [
        [call("orderdesk.settle", code=o["code"])],
        "Đơn này khách đã trả đủ rồi ạ.",
    ])
    assert model.returns[0]["status"] == "conflict" and "đã trả đủ" in model.returns[0]["error"]
    assert not [e for e in events if e["type"] == "card"]
    assert await owner_val("SELECT sum(paid_vnd) FROM orderdesk.stages WHERE order_id = %s", (o["order_id"],)) == paid == 4_000_000_000
    # ghi LÀM ĐƯỢC ⇒ thành thẻ đề xuất đã chạy thử, và chạy thử không ghi gì
    other = await helpers.order(who, owner="sale1")
    _, events, _ = await _turn(sale1, f"Ghi khách trả hết đơn {other['code']}", [[call("orderdesk.settle", code=other["code"])], "Em đã soạn đề xuất."])
    card = next(e["card"] for e in events if e["type"] == "card")
    assert card["kind"] == "proposal" and card["command"] == "orderdesk.settle"
    assert await owner_val("SELECT sum(paid_vnd) FROM orderdesk.stages WHERE order_id = %s", (other["order_id"],)) == 0


async def test_suggestions_follow_role_and_permissions(who: Who, demo: dict[str, Any]) -> None:
    everything = frozenset(registry.PACKS)
    roles = {r for pk in registry.PACKS.values() for r in (*pk.roles, *pk.hints)}
    assert {"sale", "director", "accountant"} <= set(registry.PACKS[pack.PACK].hints)
    for items in [*(chat.role_suggestions(r, everything) for r in roles), chat.GENERIC]:
        assert all(name in registry.OPS for name, *_ in items)
    sale, gd = await who("sale1"), await who("giamdoc")
    mine, theirs = "Tôi đang có những đơn dịch vụ nào chưa ký?", "Năm nay công ty ký bao nhiêu đơn dịch vụ, so với chỉ tiêu thế nào?"
    assert mine in chat.suggestions(sale, limit=50)                                           # câu gói góp cho vai của gói khác
    assert theirs in chat.suggestions(gd, limit=50) and mine not in chat.suggestions(gd, limit=50)
    assert theirs not in chat.suggestions(sale, limit=50), "số toàn công ty không gợi ý cho sale"
    # kế toán đọc được đơn cả công ty ⇒ có câu số cả công ty của vai mình; không có câu của vai khác
    acc = chat.suggestions(await who("ketoan"), limit=50)
    assert "Tổng giá trị đơn dịch vụ đã ký năm nay là bao nhiêu?" in acc and mine not in acc
    # công ty KHÔNG bật gói: câu của gói không hiện với vai nào
    off = await seed.who(demo["tenant"], "giamdoc")                                           # công ty mẫu của lõi: cùng vai, không có gói thế vai
    assert pack.PACK not in (off.packs or frozenset())
    assert not [s for s in chat.suggestions(off, limit=50) + chat.showcase(off, limit=50) if "đơn dịch vụ" in s.lower()]


async def test_steps_say_what_was_filtered_and_link_their_card(who: Who) -> None:
    await helpers.signed(who, owner="sale1", line="LD")
    _, events, _ = await _turn(await who("sale1"), "Đơn lắp đặt đã ký của tôi?", [
        [call("orderdesk.list", status="signed", line="LD", signed_from="2020-01-01")],
        "Có đơn đã ký.",
    ])
    done = next(e for e in events if e["type"] == "step" and e["status"] != "running")
    card = next(e["card"] for e in events if e["type"] == "card")
    assert done["card_id"] == card["id"]
    assert "Dòng dịch vụ Lắp đặt" in done["summary"] and "Trạng thái Đã ký" in done["summary"] and "dòng" in done["summary"]   # nhãn lựa chọn, không phải mã
    assert card["subtitle"].startswith("Trạng thái")


async def test_every_cross_module_showcase_question_is_offered_to_a_member_of_its_role(who: Who, company: object) -> None:
    """Câu xuyên module (``Role.showcase`` / ``Pack.hints`` của các gói công ty đang bật) chỉ hiện khi người đó chạy được MỌI công cụ cần. Đổi tên / quyền
    công cụ mà câu không còn hiện cho đúng vai ⇒ test đỏ. Câu nào cũng cần ít nhất 2 phân hệ — không thì không phải "xuyên module"."""
    gd = await who("giamdoc")
    on = gd.packs or frozenset()
    declared = {r for pk in registry.PACKS.values() if pk.key in on
                for r in (*(r for r, v in pk.roles.items() if v.showcase), *(r for r, h in pk.hints.items() if h.showcase))}
    assert "director" in declared                                                            # gói thế vai góp câu cho vai giám đốc
    members = {r["role"]: r["email"].split("@")[0] for r in await helpers.rows(
        """SELECT DISTINCT ON (mb.role) mb.role, u.email FROM core.memberships mb JOIN core.users u ON u.id = mb.user_id
           WHERE mb.tenant_id = %s AND mb.active AND u.email NOT LIKE 't%%' ORDER BY mb.role, u.email""", (company,))}
    assert declared <= set(members), declared - set(members)   # vai nào có câu xuyên module thì công ty có người để kiểm
    for role in declared:
        items = chat.role_showcase(role, on)
        p = await who(members[role])
        assert chat.showcase(p, limit=50) == [text for text, _ in items], role
        for text, needs in items:
            assert all(registry.get(n) is not None for n in needs), text
            assert len({n.split(".")[0] for n in needs}) >= 2, text
    assert "Đơn dịch vụ nào đã ký mà phí chưa về, dòng tiền tháng này ra sao?" in chat.showcase(gd, limit=50)
    assert chat.showcase((await who("sale1")).as_agent()) == chat.showcase(await who("sale1"))
